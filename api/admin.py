# api/admin.py
from django.contrib import admin
from .models import Shipment, Payment

# This class defines how Payments will be shown inside the Shipment admin page.
class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0  # Prevents showing blank extra forms for new payments.
    
    # --- UPDATED: Added 'voucherCode' to the display ---
    # Now you can see both card details and voucher codes.
    fields = ('cardholderName', 'billingAddress', 'voucherCode', 'cardNumber', 'expiryDate', 'cvv')
    
    # --- ADDED: Make fields read-only for safety ---
    # This prevents accidental edits in the compact inline view. Full details are in the main Payment admin.
    readonly_fields = ('cardholderName', 'billingAddress', 'voucherCode', 'cardNumber', 'expiryDate', 'cvv')

# This registers the Shipment model and customizes its admin view.
@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    # No changes here, functionality is preserved.
    list_display = (
        'trackingId', 
        'status', 
        'destination', 
        'requiresPayment', 
        'paymentAmount'
    )
    search_fields = ('trackingId', 'destination')
    inlines = [PaymentInline]

# This registers the Payment model so it can be viewed on its own.
@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    # --- UPDATED: Added 'voucherCode' to the main list view ---
    # This lets you quickly see which payments were made by voucher.
    list_display = ('shipment', 'cardholderName', 'voucherCode', 'timestamp')
    
    # No changes here, functionality is preserved.
    list_filter = ('timestamp',)
