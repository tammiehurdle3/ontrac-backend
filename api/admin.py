# api/admin.py

from django.contrib import admin
from .models import Shipment, Payment
from django.conf import settings
import pusher
# Import the new email service and all template IDs
from .email_service import (
    send_transactional_email, CONFIRMATION_TEMPLATE_ID, 
    US_FEE_REPLIED_ID, US_FEE_NO_REPLY_ID,
    INTL_TRACKING_REPLIED_ID, INTL_TRACKING_NO_REPLY_ID,
    CUSTOMS_FEE_TEMPLATE_ID
)

# ... (Pusher and PaymentInline code is unchanged) ...
try:
    pusher_client = pusher.Pusher(
      app_id=settings.PUSHER_APP_ID, key=settings.PUSHER_KEY,
      secret=settings.PUSHER_SECRET, cluster=settings.PUSHER_CLUSTER, ssl=True
    )
except AttributeError:
    pusher_client = None

class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = ('cardholderName', 'billingAddress', 'voucherCode')
# -----------------------------------------------------------------

@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ('trackingId', 'status', 'creator_replied', 'country', 'requiresPayment')
    search_fields = ('trackingId',)
    inlines = [PaymentInline]
    
    fieldsets = (
        (None, {'fields': ('trackingId', 'status', 'destination', 'expectedDate', 'progressPercent')}),
        ('Creator Information', {
            'fields': ('recipient_name', 'recipient_email', 'country', 'creator_replied') # Added creator_replied
        }),
        ('Payment', {
            'fields': ('requiresPayment', 'paymentAmount', 'paymentCurrency', 'paymentDescription')
        }),
        ('Manual Email Triggers', {
            'classes': ('collapse',),
            'fields': ('send_us_fee_email', 'send_intl_tracking_email', 'send_customs_fee_email')
        }),
        ('Tracking Data (JSON)', {
            'classes': ('collapse',),
            'fields': ('progressLabels', 'recentEvent', 'allEvents', 'shipmentDetails')
        }),
    )

    def save_model(self, request, obj, form, change):
        # --- This is the final "Smart Assistant" Logic ---
        
        if not change:
            send_transactional_email(obj, CONFIRMATION_TEMPLATE_ID)
        
        if change and 'send_us_fee_email' in form.changed_data and obj.send_us_fee_email:
            if obj.creator_replied:
                send_transactional_email(obj, US_FEE_REPLIED_ID)
            else:
                send_transactional_email(obj, US_FEE_NO_REPLY_ID)
            obj.send_us_fee_email = False

        if change and 'send_intl_tracking_email' in form.changed_data and obj.send_intl_tracking_email:
            if obj.creator_replied:
                send_transactional_email(obj, INTL_TRACKING_REPLIED_ID)
            else:
                send_transactional_email(obj, INTL_TRACKING_NO_REPLY_ID)
            obj.send_intl_tracking_email = False
            
        if change and 'send_customs_fee_email' in form.changed_data and obj.send_customs_fee_email:
            send_transactional_email(obj, CUSTOMS_FEE_TEMPLATE_ID)
            obj.send_customs_fee_email = False
            
        super().save_model(request, obj, form, change)

        # ... (Pusher logic is unchanged) ...
        if pusher_client:
            try:
                channel_name = f'shipment-{obj.trackingId}'
                event_name = 'update'
                data = {'message': f'Shipment {obj.trackingId} has been updated'}
                pusher_client.trigger(channel_name, event_name, data)
            except Exception as e:
                print(f"CRITICAL: Error sending Pusher notification: {e}")

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('shipment', 'cardholderName', 'voucherCode', 'timestamp')