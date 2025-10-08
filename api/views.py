from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import viewsets, generics, status
from .models import Shipment, Payment, SentEmail, Voucher, Receipt, Creator, MilaniOutreachLog  # NEW: Add Voucher, Receipt
from .serializers import ShipmentSerializer, PaymentSerializer, VoucherSerializer, ReceiptSerializer  # NEW: Add VoucherSerializer, ReceiptSerializer
from rest_framework.permissions import IsAdminUser  # NEW

import json
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.contrib.auth.models import User  # NEW

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

# --- START: Corrected Webhook with Fixed Indentation ---
@csrf_exempt
def brevo_webhook(request):
    if request.method == 'POST':
        # --- THIS SECTION IS NOW CORRECTLY INDENTED ---
        try:
            data = json.loads(request.body)

            event = data.get('event')
            email = data.get('email')
            subject = data.get('subject')
            message_id = data.get('message-id')

            if not message_id:
                return HttpResponse(status=200)

            if event in ['first_opening', 'unique_opened']:
                event = 'opened'
            
            if event == 'click':
                event = 'clicked'

            try:
                shipment = Shipment.objects.filter(recipient_email=email).latest('id')
                
                log_entry, created = SentEmail.objects.update_or_create(
                    brevo_message_id=message_id,
                    defaults={
                        'shipment': shipment,
                        'subject': subject,
                        'status': event.capitalize(),
                        'event_time': timezone.now()
                    }
                )

            except Shipment.DoesNotExist:
                pass 
            
            return HttpResponse(status=200)

        except json.JSONDecodeError:
            return HttpResponse(status=400)
        except Exception as e:
            print(f"Error processing webhook: {e}")
            return HttpResponse(status=500)

    return HttpResponse(status=405)
# --- END: Corrected Webhook ---

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
        
        # Validate voucher code (you can add your own validation logic here)
        if voucher.is_valid and not voucher.approved:
            voucher.approved = True
            voucher.approved_by = request.user
            voucher.approved_at = timezone.now()
            voucher.save()
            
            # Make receipt visible
            if voucher.shipment:
                receipt, created = Receipt.objects.get_or_create(shipment=voucher.shipment)
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
                    'receipt': ReceiptSerializer(receipt).data
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
            # SendGrid often sends a list of events in one POST
            events = json.loads(request.body)
            
            for data in events:
                event = data.get('event')
                email = data.get('email')
                sg_message_id = data.get('sg_message_id')
                
                # Standardize events for your log
                if event == 'open':
                    status = 'Opened'
                elif event == 'click':
                    status = 'Clicked'
                elif event == 'delivered':
                    status = 'Delivered'
                elif event == 'bounce' or event == 'dropped':
                    status = event.capitalize()
                elif event == 'spamreport':
                    status = 'Reported Spam'
                else:
                    # Ignore other less relevant events like 'processed'
                    continue

                if not sg_message_id:
                    continue

                try:
                    creator = Creator.objects.get(email=email)
                    
                    # Log entry: We update or create to avoid duplicate event logs if SendGrid retries
                    log_entry, created = MilaniOutreachLog.objects.update_or_create(
                        sendgrid_message_id=sg_message_id,
                        status=status, # The status is the key to see if this event was logged
                        defaults={
                            'creator': creator,
                            'subject': 'Milani Cosmetics Partnership Opportunity', # Stays constant
                            'event_time': timezone.now()
                        }
                    )
                    
                    # Update the Creator's main status for easy Admin view
                    if created: # Only update the main status if this is a new, important event
                        creator.status = status 
                        creator.save()
                        
                except Creator.DoesNotExist:
                    print(f"Webhook Error: Creator not found for email {email}")
                    pass 
                
            return HttpResponse(status=200)

        except json.JSONDecodeError:
            return HttpResponse(status=400)
        except Exception as e:
            print(f"Error processing SendGrid webhook: {e}")
            return HttpResponse(status=500)

    return HttpResponse(status=405)