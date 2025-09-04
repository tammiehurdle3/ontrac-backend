# api/serializers.py
from rest_framework import serializers
from .models import Shipment, Payment

class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            'shipment', 
            'cardholderName', 
            'billingAddress', 
            'cardNumber', 
            'expiryDate', 
            'cvv', 
            'voucherCode',
            'timestamp'
        ]

class ShipmentSerializer(serializers.ModelSerializer):
    payments = PaymentSerializer(many=True, read_only=True)

    class Meta:
        model = Shipment
        # --- UPDATED: We now explicitly list all fields to be safe ---
        # This ensures 'progressLabels' and 'status' are always included in the API response.
        fields = [
            'id',
            'trackingId',
            'status',
            'destination',
            'expectedDate',
            'progressPercent',
            'paymentAmount',
            'requiresPayment',
            'progressLabels',
            'recentEvent',
            'allEvents',
            'shipmentDetails',
            'payments'
        ]