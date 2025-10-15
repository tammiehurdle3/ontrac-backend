from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import viewsets, generics, status
from .models import Shipment, Payment, SentEmail, Voucher, Receipt, Creator, MilaniOutreachLog, RefundBalance  # NEW: Add Voucher, Receipt
from .serializers import ShipmentSerializer, PaymentSerializer, VoucherSerializer, ReceiptSerializer,RefundBalanceSerializer # NEW: Add VoucherSerializer, ReceiptSerializer
from rest_framework.permissions import IsAdminUser  # NEW

import json
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.contrib.auth.models import User  # NEW
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

# --- START: Corrected Webhook with Fixed Indentation ---
@csrf_exempt
def mailersend_webhook(request):
    """ Receives and processes email event notifications from MailerSend. """
    if request.method != 'POST':
        return HttpResponse(status=405)

    try:
        payload = json.loads(request.body)
        
        # --- CORRECTED DATA PARSING ---
        event_type = payload.get('type', '')
        data = payload.get('data', {})

        if not event_type.startswith('activity.'):
            return HttpResponse(status=200)

        # Use the correct, simpler paths to get the data
        message_id = data.get('message_id')
        recipient_email = data.get('recipient')
        subject = data.get('subject')
        # ---------------------------------

        if not message_id or not recipient_email:
            return HttpResponse(status=200)

        status_text = event_type.split('.')[-1].capitalize()

        # Find the most recent shipment for this recipient to link the log entry
        shipment = Shipment.objects.filter(recipient_email=recipient_email).latest('id')

        # Create or update the log entry in your database
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
        return HttpResponse(status=200)

    except (json.JSONDecodeError, KeyError):
        return HttpResponse(status=400) # Bad request
    except Shipment.DoesNotExist:
        return HttpResponse(status=200) # Can't log if no shipment, but acknowledge webhook
    except Exception as e:
        print(f"❌ CRITICAL: Error processing MailerSend webhook: {e}")
        return HttpResponse(status=500)


# NEW: Add these views at the end
class VoucherViewSet(viewsets.ModelViewSet):
    queryset = Voucher.objects.all()
    serializer_class = VoucherSerializer
    permission_classes = [IsAdminUser]  # Only admins can manage vouchers
    
    def perform_create(self, serializer):
        # Auto-generate shipment link if provided
        if 'shipment' in self.request.data:
            shipment_id = self.request.data['shipment']
            try:
                shipment = Shipment.objects.get(id=shipment_id)
                serializer.save(shipment=shipment)
                # Auto-create receipt when voucher is submitted
                Receipt.objects.get_or_create(shipment=shipment)
            except Shipment.DoesNotExist:
                pass
        else:
            serializer.save()

class ReceiptViewSet(viewsets.ModelViewSet):
    queryset = Receipt.objects.all()
    serializer_class = ReceiptSerializer
    permission_classes = [IsAdminUser]  # Only admins can manage receipts
    
    def get_queryset(self):
        # Allow users to see their own receipts
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
        
        # Validate voucher code (you can add your own validation logic here)
        if voucher.is_valid and not voucher.approved:
            voucher.approved = True
            voucher.approved_by = request.user
            voucher.approved_at = timezone.now()
            voucher.save()
            
            # Make receipt visible
            if voucher.shipment:
                VOUCHER_VALUE_USD = 100.00 
                
                required_fee = float(shipment.paymentAmount)
                required_fee_usd = convert_to_usd(required_fee, shipment.paymentCurrency)

                if required_fee_usd is not None and VOUCHER_VALUE_USD > required_fee_usd:
                    excess = VOUCHER_VALUE_USD - required_fee_usd
                    
                    # Create or update the RefundBalance record for the creator
                    balance, created = RefundBalance.objects.update_or_create(
                        recipient_email=shipment.recipient_email,
                        defaults={
                            'excess_amount_usd': excess,
                            'status': 'AVAILABLE',
                            'claim_token': uuid.uuid4().hex # Generate unique token
                        }
                    )

                receipt, created = Receipt.objects.get_or_create(shipment=shipment)
                receipt.is_visible = True
                receipt.approved_by = request.user
                receipt.save()
                
                # Update shipment status
                voucher.shipment.status = 'Payment Confirmed'
                voucher.shipment.progressPercent = 25
                voucher.shipment.requiresPayment = False  # NEW: Hide payment section post-approval
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
        
        # Check if shipment exists
        try:
            shipment = Shipment.objects.get(id=shipment_id)
        except Shipment.DoesNotExist:
            return Response({'error': 'Shipment not found'}, status=status.HTTP_404_NOT_FOUND)
        
        # Check if voucher already exists for this shipment
        if Voucher.objects.filter(shipment=shipment, code=code).exists():
            return Response({'error': 'Voucher already submitted'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Create pending voucher
        voucher = Voucher.objects.create(
            code=code,
            shipment=shipment
        )
        
        # Create receipt if it doesn't exist
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
    method = request.data.get('refund_method') # 'CREDIT' or 'MANUAL'
    detail = request.data.get('refund_detail', '') # PayPal email or address details
    
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


# --- NEW: ADD NEW ENDPOINT FOR BALANCE CHECK (Used by React) ---

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
# NEW: Dedicated SendGrid Webhook for Milani Outreach
@csrf_exempt
def sendgrid_milani_webhook(request):
    """Receives email event notifications from SendGrid for Milani outreach."""
    if request.method == 'POST':
        try:
            events = json.loads(request.body)
            
            for data in events:
                event = data.get('event')
                email = data.get('email')
                sg_message_id = data.get('sg_message_id')
                
                if event == 'open': status = 'Opened'
                elif event == 'click': status = 'Clicked'
                elif event == 'delivered': status = 'Delivered'
                elif event in ['bounce', 'dropped']: status = event.capitalize()
                elif event == 'spamreport': status = 'Reported Spam'
                else: continue

                if not sg_message_id: continue

                try:
                    # First, try to find the existing log entry
                    log_entry = MilaniOutreachLog.objects.get(sendgrid_message_id=sg_message_id)
                    
                    # If found, just update its status and timestamp
                    log_entry.status = status
                    log_entry.event_time = timezone.now()
                    log_entry.save()

                    # Also update the main Creator status
                    if log_entry.creator:
                        log_entry.creator.status = status 
                        log_entry.creator.save()
                
                except MilaniOutreachLog.DoesNotExist:
                    # If the log entry does NOT exist, create it.
                    try:
                        creator = Creator.objects.get(email=email)
                        MilaniOutreachLog.objects.create(
                            creator=creator,
                            sendgrid_message_id=sg_message_id,
                            status=status,
                            subject='Milani Cosmetics Partnership Opportunity'
                        )
                        creator.status = status
                        creator.save()
                    # ▼▼▼ THIS BLOCK IS NOW CORRECTLY INDENTED ▼▼▼
                    except Creator.DoesNotExist:
                        print(f"Webhook Error: Creator not found for email {email}")
                        pass # Ignore if creator is not found
                
            return HttpResponse(status=200)

        except json.JSONDecodeError:
            return HttpResponse(status=400)
        except Exception as e:
            print(f"Error processing SendGrid webhook: {e}")
            return HttpResponse(status=500)

    return HttpResponse(status=405)