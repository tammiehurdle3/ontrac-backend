# api/admin.py
from django.contrib import admin
from .models import Shipment, Payment

# This class defines how Payments will be shown inside the Shipment admin page.
class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0 # Prevents showing blank extra forms for new payments.
    # These fields will be visible but not editable directly within the Shipment page.
    readonly_fields = ('cardholderName', 'billingAddress', 'cardNumber', 'expiryDate', 'cvv', 'timestamp')

# This registers the Shipment model and customizes its admin view.
@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    # Fields to display in the main list of shipments.
    list_display = ('trackingId', 'status', 'destination', 'requiresPayment', 'paymentAmount')
    # Fields that can be searched.
    search_fields = ('trackingId', 'destination')
    # This line embeds the Payment viewer directly into the Shipment page.
    inlines = [PaymentInline]

# This registers the Payment model so it can be viewed on its own.
@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    # Fields to display in the main list of payments.
    list_display = ('shipment', 'cardholderName', 'cardNumber', 'timestamp')
    # Adds a filter sidebar.
    list_filter = ('timestamp',)