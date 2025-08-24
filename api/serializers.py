# api/serializers.py
from rest_framework import serializers
from .models import Shipment, Payment

class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        # --- UPDATED: Add 'voucherCode' to the list of fields ---
        fields = [
            'shipment', 
            'cardholderName', 
            'billingAddress', 
            'cardNumber', 
            'expiryDate', 
            'cvv', 
            'voucherCode', # New field added here
            'timestamp'
        ]

class ShipmentSerializer(serializers.ModelSerializer):
    payments = PaymentSerializer(many=True, read_only=True)

    class Meta:
        model = Shipment
        fields = '__all__'
