# api/views.py
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import viewsets, generics
from .models import Shipment, Payment, SentEmail
from .serializers import ShipmentSerializer, PaymentSerializer

import json
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone

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