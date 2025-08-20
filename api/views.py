# api/views.py
from rest_framework.decorators import api_view
from rest_framework.response import Response

from rest_framework import viewsets, generics # Add generics
from .models import Shipment, Payment
from .serializers import ShipmentSerializer, PaymentSerializer # Add PaymentSerializer

class ShipmentViewSet(viewsets.ModelViewSet):
    queryset = Shipment.objects.all()
    serializer_class = ShipmentSerializer
    lookup_field = 'trackingId'

# ADD THIS NEW CLASS
class PaymentCreateView(generics.CreateAPIView):
    queryset = Payment.objects.all()
    serializer_class = PaymentSerializer

# ADD THIS NEW VIEW FUNCTION AT THE END OF THE FILE
@api_view(['GET'])
def api_root(request, format=None):
    return Response({
       'message': 'Welcome to the OnTrac API!',
       'shipments': '/api/shipments/',
       'payments': '/api/payments/'
    })