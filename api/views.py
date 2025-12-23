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
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.contrib.auth.models import User
from django.conf import settings
import requests
import uuid

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
            send_admin_notification(
                subject="New Payment Received",
                message_body=f"A payment was just received{card_name} for shipment {payment.shipment.trackingId}."
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
        
        if cache.get(cache_key):
            return HttpResponse(status=200) 
        cache.set(cache_key, True, 60)

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