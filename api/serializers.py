from rest_framework import serializers
from .models import Shipment, Payment, Voucher, Receipt, RefundBalance  # NEW: Added Voucher, Receipt
from django.conf import settings
import requests

class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = '__all__'

# NEW: Add these two serializers
class VoucherSerializer(serializers.ModelSerializer):
    class Meta:
        model = Voucher
        fields = ['id', 'code', 'is_valid', 'approved', 'shipment', 'created_at', 'approved_at']
        read_only_fields = ['id', 'created_at', 'approved_at']

class ReceiptSerializer(serializers.ModelSerializer):
    shipment_tracking = serializers.CharField(source='shipment.trackingId', read_only=True)
    recipient_name = serializers.CharField(source='shipment.recipient_name', read_only=True)
    payment_amount = serializers.CharField(source='shipment.paymentAmount', read_only=True)
    payment_currency = serializers.CharField(source='shipment.paymentCurrency', read_only=True)
    
    class Meta:
        model = Receipt
        fields = [
            'id', 'shipment', 'shipment_tracking', 'recipient_name', 
            'payment_amount', 'payment_currency', 'is_visible', 
            'generated_at', 'approved_by', 'receipt_number'
        ]
        read_only_fields = ['id', 'generated_at', 'receipt_number']

class RefundBalanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = RefundBalance
        fields = ['excess_amount_usd', 'status', 'claim_token']

class ShipmentSerializer(serializers.ModelSerializer):
    payments = PaymentSerializer(many=True, read_only=True)
    vouchers = VoucherSerializer(many=True, read_only=True)  # NEW
    receipt = ReceiptSerializer(read_only=True)  # NEW
    show_receipt = serializers.SerializerMethodField()  # NEW
    
    # --- BOTH custom fields are now included ---
    approximatedUSD = serializers.SerializerMethodField()
    paymentBreakdown = serializers.SerializerMethodField()

    refund_balance = serializers.SerializerMethodField()
    def get_refund_balance(self, obj):
        """Returns the available balance linked to the recipient's email."""
        if obj.recipient_email:
            try:
                # Look up the RefundBalance by recipient_email
                balance = RefundBalance.objects.get(recipient_email=obj.recipient_email)
                # Only return the balance if it's available to claim
                if balance.status == 'AVAILABLE':
                    return RefundBalanceSerializer(balance).data
            except RefundBalance.DoesNotExist:
                return None
        return None


    class Meta:
        model = Shipment
        # Explicitly list all fields to ensure everything is included
        fields = [
            'id', 'trackingId', 'status', 'destination', 'expectedDate',
            'progressPercent', 'paymentAmount', 'paymentCurrency',
            'paymentDescription', 'requiresPayment', 'show_receipt', 'progressLabels', 
            'recentEvent', 'allEvents', 'shipmentDetails', 'payments', 
            'vouchers', 'receipt', 'approximatedUSD', 'paymentBreakdown',
            'recipient_name', 'recipient_email', 'refund_balance'
        ]
        
    # NEW: Add this method for show_receipt
    def get_show_receipt(self, obj):
        """Check if receipt should be visible to user"""
        receipt = getattr(obj, 'receipt', None)
        return receipt.is_visible if receipt else False
        
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