# api/views.py
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