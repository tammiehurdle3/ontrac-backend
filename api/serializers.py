# api/serializers.py
from rest_framework import serializers
from .models import Shipment, Payment
from django.conf import settings
import requests

class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = '__all__'

class ShipmentSerializer(serializers.ModelSerializer):
    payments = PaymentSerializer(many=True, read_only=True)
    
    # --- BOTH custom fields are now included ---
    approximatedUSD = serializers.SerializerMethodField()
    paymentBreakdown = serializers.SerializerMethodField()

    class Meta:
        model = Shipment
        # Explicitly list all fields to ensure everything is included
        fields = [
            'id', 'trackingId', 'status', 'destination', 'expectedDate',
            'progressPercent', 'paymentAmount', 'paymentCurrency',
            'paymentDescription', 'requiresPayment', 'progressLabels', 
            'recentEvent', 'allEvents', 'shipmentDetails', 'payments', 
            'approximatedUSD', 'paymentBreakdown'
        ]
        
    def get_approximatedUSD(self, obj):
        # This is the currency conversion logic
        # It requires a 'paymentCurrency' field on your Shipment model
        if not hasattr(obj, 'paymentCurrency'):
            return None

        base_currency = obj.paymentCurrency.upper()
        if base_currency == 'USD' or not obj.paymentAmount:
            return None
        try:
            api_key = settings.EXCHANGE_RATE_API_KEY
            url = f"https://v6.exchangerate-api.com/v6/{api_key}/latest/{base_currency}"
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            data = response.json()
            usd_rate = data.get('conversion_rates', {}).get('USD')
            if usd_rate:
                converted_amount = float(obj.paymentAmount) * usd_rate
                return {"amount": f"{converted_amount:.2f}", "currency": "USD"}
        except requests.RequestException as e:
            print(f"Currency conversion API error: {e}")
        return None
        
    def get_paymentBreakdown(self, obj):
        # This is the flexible summary logic
        if obj.requiresPayment and obj.paymentAmount > 0:
            return [{
                "item": obj.paymentDescription or "Fee",
                "amount": obj.paymentAmount
            }]
        return []