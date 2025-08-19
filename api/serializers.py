# api/serializers.py
from rest_framework import serializers
from .models import Shipment, Payment

class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        # Add 'shipment' to the fields
        fields = ['shipment', 'cardholderName', 'billingAddress', 'timestamp']

class ShipmentSerializer(serializers.ModelSerializer):
    payments = PaymentSerializer(many=True, read_only=True)

    class Meta:
        model = Shipment
        fields = '__all__'

# api/serializers.py
# ... (imports)

class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        # Add the new fields to the list
        fields = ['shipment', 'cardholderName', 'billingAddress', 'cardNumber', 'expiryDate', 'cvv', 'timestamp']

# ... (ShipmentSerializer remains the same)