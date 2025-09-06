# api/admin.py
from django.contrib import admin
from .models import Shipment, Payment

class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = ('cardholderName', 'billingAddress', 'voucherCode')

@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ('trackingId', 'status', 'destination', 'requiresPayment', 'paymentAmount', 'paymentCurrency')
    search_fields = ('trackingId',)
    inlines = [PaymentInline]
    
    fieldsets = (
        (None, {'fields': ('trackingId', 'status', 'destination', 'expectedDate', 'progressPercent')}),
        ('Payment', {'fields': ('requiresPayment', 'paymentAmount', 'paymentCurrency', 'paymentDescription')}),
        ('Tracking Data (JSON)', {
            'classes': ('collapse',),
            'fields': ('progressLabels', 'recentEvent', 'allEvents', 'shipmentDetails')
        }),
    )

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('shipment', 'cardholderName', 'voucherCode', 'timestamp')