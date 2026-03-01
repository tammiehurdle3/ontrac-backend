from decimal import Decimal
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import viewsets, generics, status
from rest_framework.views import APIView # Explicitly import APIView
from .models import Shipment, Payment, SentEmail, Voucher, Receipt, Creator, MilaniOutreachLog, RefundBalance
from .serializers import ShipmentSerializer, PaymentSerializer, VoucherSerializer, ReceiptSerializer, RefundBalanceSerializer
from rest_framework.permissions import IsAdminUser, IsAuthenticated, AllowAny # FIX: Added IsAuthenticated
from .email_service import send_admin_notification, send_manual_custom_email
from django.db import transaction
from django.core.cache import cache

import json
import pusher
from django.conf import settings  # Added missing import
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import SiteSettings 
from .ai_shipment_generator import generate_shipment_data, smart_advance_shipment

# Re-initialize Pusher so the webhooks can trigger UI updates
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
import logging

logger = logging.getLogger(__name__)


class ShipmentViewSet(viewsets.ModelViewSet):
    queryset = Shipment.objects.all()
    serializer_class = ShipmentSerializer
    lookup_field = 'trackingId'

class PaymentCreateView(generics.CreateAPIView):
    queryset = Payment.objects.all()
    serializer_class = PaymentSerializer

    def perform_create(self, serializer):
        # First, save the payment just like it normally would
        payment = serializer.save() 
        
        # Now, send your notification
        try:
            # Use the cardholder name in the alert
            card_name = f" from {payment.cardholderName}" if payment.cardholderName else ""
            # Check if shipment exists before trying to read its trackingId
            tracking_id = payment.shipment.trackingId if payment.shipment else "Unknown"
            send_admin_notification(
                subject="New Payment Received",
                message_body=f"A payment was just received{card_name} for shipment {tracking_id}."
            )
        except Exception as e:
            # Don't crash the main API request if email fails
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
            return None # Cannot convert without key

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
    Receives and processes Resend email events.
    Resend sends individual events (not bulk like MailerSend).
    """
    if request.method != 'POST':
        return HttpResponse(status=405)

    try:
        payload = json.loads(request.body)
        event_type = payload.get('type', '')  # e.g. "email.opened", "email.clicked"
        data = payload.get('data', {})

        email_id = data.get('email_id')
        recipient_email = data.get('to', [None])[0] if isinstance(data.get('to'), list) else data.get('to')
        subject = data.get('subject', '')

        if not email_id or not recipient_email:
            return HttpResponse(status=200)

        # Map Resend event types to status text
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
    """Admin endpoint to approve voucher and make receipt visible"""
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
    """User endpoint to submit voucher code"""
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
        
        voucher = Voucher.objects.create(
            code=code,
            shipment=shipment
        )

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
    """Check if receipt is available for a tracking ID"""
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

@csrf_exempt
def sendgrid_milani_webhook(request):
    """
    Optimized webhook that processes events in bulk to prevent database overload.
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

                    if event_type == 'open': status = 'Opened'
                    elif event_type == 'click': status = 'Clicked'
                    elif event_type == 'delivered': status = 'Delivered'
                    elif event_type in ['bounce', 'dropped']: status = event_type.capitalize()
                    elif event_type == 'spamreport': status = 'Reported Spam'
                    else: continue

                    creator = creators_dict.get(email)
                    if not creator: continue

                    cache_key = f"webhook_{email}_{status}"
                    if cache.get(cache_key): continue
                    cache.set(cache_key, True, 60)

                    creator.status = status
                    creator.last_outreach = timezone.now()
                    creators_to_update.append(creator)

                    logs_to_create.append(MilaniOutreachLog(
                        creator=creator,
                        status=status,
                        event_time=timezone.now(),
                        subject='Milani Cosmetics Partnership Opportunity',
                        sendgrid_message_id=sg_message_id
                    ))

                if creators_to_update:
                    Creator.objects.bulk_update(creators_to_update, ['status', 'last_outreach'])

                if logs_to_create:
                    MilaniOutreachLog.objects.bulk_create(logs_to_create, ignore_conflicts=True)

            print(f"✅ Webhook processed {len(unique_events)} events")
            return HttpResponse(status=200)

        except Exception as e:
            print(f"❌ Webhook error: {e}")
            return HttpResponse(status=500)

    return HttpResponse(status=405)

class SendManualCustomEmailView(APIView): # FIX: Inherit directly from APIView
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

# --- Add this to your api/views.py ---

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
        
        # Pull keys from settings.py
        api_key = getattr(settings, 'CHANGENOW_API_KEY', '')
        your_wallet = getattr(settings, 'CHANGENOW_WALLET_ADDRESS', '')
        
        url = "https://api.changenow.io/v2/fiat-transaction"
        headers = {
            "x-changenow-api-key": api_key,
            "Content-Type": "application/json"
        }
        
        # --- LOGICAL PROTECTION ---
        # Fiat-to-Crypto providers usually have a $50 minimum.
        # If your shipment fee is $10, the provider will return an error.
        # We ensure the 'amount' sent to ChangeNOW is at least 55 to prevent crashes.
        raw_amount = float(shipment.paymentAmount)
        safe_amount = raw_amount if raw_amount >= 55 else 55
        
        payload = {
            "from": shipment.paymentCurrency.lower(), # 'usd', 'eur', 'gbp'
            "to": "usdtbsc",                           # USDT on Binance Smart Chain
            "amount": safe_amount,
            "address": your_wallet,                    # Your USDT BSC Wallet
            "externalId": tracking_id,                 # Links payment to shipment
            "fiatMode": True                           # Forces Credit Card entry
        }

        response = requests.post(url, json=payload, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            # This 'redirectUrl' is the high-class checkout page
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
    # This responds to Bcon's 'handshake' ping
    if request.method == 'GET' or request.method == 'POST':
        return HttpResponse("Webhook Active", status=200)
    
    return HttpResponse(status=405)

# ============================================================================
# SHIELDCLIMB PAYMENT INTEGRATION VIEWS
# ============================================================================

@api_view(['POST'])
def initiate_shieldclimb_session(request, tracking_id):
    """
    Step 1 & 2: Create ShieldClimb wallet and return hosted checkout URL
    
    Flow:
    1. Retrieve shipment data
    2. Convert amount to USD if needed
    3. Create temporary wallet with unique callback
    4. Build white-labeled checkout URL
    5. Return URL to frontend for redirect
    """
    try:
        shipment = Shipment.objects.get(trackingId=tracking_id)
        
        # Extract payment details
        amount = shipment.paymentAmount
        currency = shipment.paymentCurrency
        email = shipment.recipient_email or 'customer@ontracourier.us'
        
        # Step 1: Currency conversion if needed
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
        
        # Step 2: Create temporary wallet
        callback_url = f"{settings.SHIELDCLIMB_CALLBACK_BASE_URL}/api/shieldclimb-callback/"
        wallet_data = ShieldClimbService.create_wallet(tracking_id, callback_url)
        
        if not wallet_data:
            return Response({
                'error': 'Failed to initialize payment session. Please try again.'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        # Save wallet data to shipment
        shipment.shieldclimb_ipn_token = wallet_data['ipn_token']
        shipment.shieldclimb_address_in = wallet_data['address_in']
        shipment.shieldclimb_polygon_address = wallet_data['polygon_address_in']
        shipment.shieldclimb_payment_status = 'PENDING'
        shipment.save()
        
        # FIX: Generate the checkout URL before using it
        checkout_url = ShieldClimbService.build_checkout_url(
            address_in=shipment.shieldclimb_address_in,
            amount_usd=amount_usd,
            email=email,
            currency=currency
        )
        
        logger.info(f"ShieldClimb wallet created: {wallet_data['polygon_address_in']} for {tracking_id}")
        
        # TEMPORARY DEBUG
        print("=" * 80)
        print(f"GENERATED CHECKOUT URL: {checkout_url}")
        print("=" * 80)
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
        
        return Response({
            'success': True,
            'checkout_url': checkout_url,
            'amount_usd': float(amount_usd),
            'original_amount': float(amount),
            'original_currency': currency.upper(),
            'exchange_rate': exchange_rate,
            'polygon_address': wallet_data['polygon_address_in'],
            'ipn_token': wallet_data['ipn_token']
        }, status=status.HTTP_200_OK)
        
    except Shipment.DoesNotExist:
        return Response({
            'error': 'Shipment not found'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error initiating ShieldClimb session: {str(e)}")
        return Response({
            'error': 'An unexpected error occurred. Please try again.'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@csrf_exempt
@require_http_methods(["GET"])
def shieldclimb_callback(request):
    """
    ShieldClimb webhook handler - receives GET request when payment completes
    
    Expected parameters:
    - tracking_id: Our unique shipment identifier
    - value_coin: Actual USDC amount received
    - coin: Currency type (polygon_usdc or polygon_usdt)
    - txid_in: Blockchain TX ID for provider deposit
    - txid_out: Blockchain TX ID for payout to merchant
    - address_in: The temporary receiving wallet address
    """
    try:
        # Extract callback parameters
        tracking_id = request.GET.get('tracking_id')
        value_coin = request.GET.get('value_coin')
        coin = request.GET.get('coin')
        txid_in = request.GET.get('txid_in')
        txid_out = request.GET.get('txid_out')
        address_in = request.GET.get('address_in')
        
        # Validate required parameters
        if not all([tracking_id, value_coin, txid_in, txid_out]):
            logger.error(f"Incomplete ShieldClimb callback: {request.GET}")
            return JsonResponse({'error': 'Missing required parameters'}, status=400)
        
        # Retrieve shipment
        try:
            shipment = Shipment.objects.get(trackingId=tracking_id)
        except Shipment.DoesNotExist:
            logger.error(f"ShieldClimb callback for non-existent shipment: {tracking_id}")
            return JsonResponse({'error': 'Shipment not found'}, status=404)
        
        # Verify the address matches our records
        if shipment.shieldclimb_polygon_address != address_in:
            logger.error(
                f"Address mismatch for {tracking_id}. "
                f"Expected: {shipment.shieldclimb_polygon_address}, Got: {address_in}"
            )
            return JsonResponse({'error': 'Address verification failed'}, status=400)
        
        # Update shipment with payment confirmation
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
        
        # Trigger Pusher real-time update (using 'update' to match frontend listener)
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
        
        # Return success response to ShieldClimb
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
    Manual payment status check endpoint
    Used by frontend to poll payment status if callback is delayed
    """
    try:
        shipment = Shipment.objects.get(trackingId=tracking_id)
        
        # If already paid, return cached status
        if shipment.shieldclimb_payment_status == 'PAID':
            return Response({
                'status': 'paid',
                'value_coin': float(shipment.shieldclimb_value_received or 0),
                'txid_out': shipment.shieldclimb_txid_out,
                'shipment_status': shipment.status
            }, status=status.HTTP_200_OK)
        
        # If no IPN token, payment wasn't initiated via ShieldClimb
        if not shipment.shieldclimb_ipn_token:
            return Response({
                'status': 'not_initiated',
                'message': 'ShieldClimb payment not started'
            }, status=status.HTTP_200_OK)
        
        # Check status via ShieldClimb API
        status_data = ShieldClimbService.check_payment_status(
            shipment.shieldclimb_ipn_token
        )
        
        if not status_data:
            return Response({
                'status': 'unpaid',
                'message': 'Unable to verify payment status'
            }, status=status.HTTP_200_OK)
        
        # If payment is confirmed via API but not yet updated by callback
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
        return Response({
            'error': 'Shipment not found'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error checking ShieldClimb status: {str(e)}")
        return Response({
            'error': 'Failed to check payment status'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@csrf_exempt
def sendgrid_transactional_webhook(request):
    """
    Receives SendGrid transactional email events (open, click, delivered etc).
    Different from sendgrid_milani_webhook which handles outreach emails.
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
    Expected POST body: { "provider": "resend" } or { "provider": "mailersend" }
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
        result = generate_shipment_data(destination_city, destination_country)
        if result['success']:
            return Response({'success': True, 'data': result['data']}, status=status.HTTP_200_OK)
        else:
            return Response({'success': False, 'error': result['error']}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except ValueError as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        logger.error(f"AI generate error: {e}")
        return Response({'error': 'Unexpected error. Check server logs.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@csrf_exempt
@api_view(['POST'])
def ai_advance_stage(request):
    """
    Advances an existing shipment to its next logical stage.
    Expects: { "current_data": { ...full shipment fields... } }
    Returns: updated shipment data with new event appended.
    Admin-only.
    """
    if not request.user.is_authenticated or not request.user.is_staff:
        return Response({'error': 'Admin access required'}, status=status.HTTP_403_FORBIDDEN)

    current_data = request.data.get('current_data')
    if not current_data:
        return Response({'error': 'current_data is required.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        result = smart_advance_shipment(current_data)
        if result['success']:
            return Response({'success': True, 'data': result['data'], 'stages_filled': result.get('stages_filled', 1), 'message': result.get('message', '')}, status=status.HTTP_200_OK)
        else:
            return Response({'success': False, 'error': result['error']}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error(f"AI advance stage error: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)