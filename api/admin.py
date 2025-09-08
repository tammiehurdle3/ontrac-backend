# api/admin.py
from django.contrib import admin
from .models import Shipment, Payment
# --- NEW: Import Pusher and Django settings ---
from django.conf import settings
import pusher

# --- NEW: Initialize the Pusher client safely ---
# This part sets up the connection to your Pusher account.
try:
    pusher_client = pusher.Pusher(
      app_id=settings.PUSHER_APP_ID,
      key=settings.PUSHER_KEY,
      secret=settings.PUSHER_SECRET,
      cluster=settings.PUSHER_CLUSTER,
      ssl=True
    )
except AttributeError:
    # This will prevent the app from crashing if the Pusher settings are missing.
    pusher_client = None
    print("Pusher settings not found. Real-time notifications are disabled.")


# --- Your existing PaymentInline class (UNCHANGED) ---
class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = ('cardholderName', 'billingAddress', 'voucherCode')


# --- Your ShipmentAdmin class with the SAFE ADDITION ---
@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    # --- All of your existing settings are preserved ---
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

    # --- NEW: This is the only addition to this class ---
    # This special function runs only when a model is saved in the admin.
    def save_model(self, request, obj, form, change):
        # First, we save the shipment to the database as normal.
        super().save_model(request, obj, form, change)

        # Then, we try to send the real-time notification.
        if pusher_client:
            try:
                channel_name = f'shipment-{obj.trackingId}'
                event_name = 'update'
                data = {'message': f'Shipment {obj.trackingId} has been updated'}
                pusher_client.trigger(channel_name, event_name, data)
                print(f"Pusher notification sent for {channel_name}") # For debugging
            except Exception as e:
                # This ensures that even if Pusher fails, your site will NOT crash.
                print(f"CRITICAL: Error sending Pusher notification: {e}")
        else:
            print("Pusher client not configured. Skipping real-time notification.")


# --- Your existing PaymentAdmin class (UNCHANGED) ---
@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('shipment', 'cardholderName', 'voucherCode', 'timestamp')
