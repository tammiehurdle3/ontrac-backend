from django.contrib import admin
from .models import Shipment, Payment, SentEmail, Voucher, Receipt, Creator, MilaniOutreachLog, RefundBalance
from django.conf import settings
import pusher
from decimal import Decimal

# Import the new email service and all template IDs
from .email_service import (
    send_transactional_email, CONFIRMATION_TEMPLATE_ID, 
    US_FEE_REPLIED_ID, US_FEE_NO_REPLY_ID,
    INTL_TRACKING_REPLIED_ID, INTL_TRACKING_NO_REPLY_ID,
    INTL_ARRIVED_TEMPLATE_ID,
    CUSTOMS_FEE_TEMPLATE_ID,
    STATUS_UPDATE_TEMPLATE_ID
)

# NEW: Import Milani Service and a Management Command utility
from .milani_email_service import send_milani_outreach_email
from django.core.management import call_command
from django.utils import timezone
from .views import convert_to_usd
import uuid

# ===================================================================
#  1. DEFINE THE ADMIN ACTION FUNCTION FIRST
# ===================================================================

@admin.action(description='Approve selected vouchers and calculate balance')
def approve_vouchers(modeladmin, request, queryset):
    """
    This is the custom action that will run our special logic.
    """
    approved_count = 0
    for voucher in queryset:
        if voucher.is_valid and not voucher.approved and voucher.value_usd > 0:
            voucher.approved = True
            voucher.approved_by = request.user
            voucher.approved_at = timezone.now()
            
            shipment = voucher.shipment
            if shipment:
                voucher_value_usd = voucher.value_usd
                
                required_fee = float(shipment.paymentAmount)
                required_fee_usd = convert_to_usd(required_fee, shipment.paymentCurrency)

                if required_fee_usd is not None and voucher_value_usd > required_fee_usd:
                    # All lines in this block now have the same, correct indentation
                    excess = voucher_value_usd - Decimal(required_fee_usd)
                    RefundBalance.objects.update_or_create(
                        recipient_email=shipment.recipient_email,
                        defaults={
                            'excess_amount_usd': excess,
                            'status': 'AVAILABLE',
                            'claim_token': uuid.uuid4().hex
                        }
                    )
                
                receipt, created = Receipt.objects.get_or_create(shipment=shipment)
                receipt.is_visible = True
                receipt.approved_by = request.user
                receipt.save()
                
                shipment.requiresPayment = False
                shipment.save()
            
            voucher.save()
            approved_count += 1

            if pusher_client and shipment:
                try:
                    channel_name = f'shipment-{shipment.trackingId}'
                    pusher_client.trigger(channel_name, 'update', {'message': 'approved'})
                except Exception as e:
                    print(f"CRITICAL: Error sending Pusher notification from admin action: {e}")
            
    modeladmin.message_user(request, f"Successfully approved {approved_count} vouchers.")

# ===================================================================
#  2. NOW, REGISTER ALL YOUR ADMIN CLASSES
# ===================================================================

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

class SentEmailInline(admin.TabularInline):
    model = SentEmail
    extra = 0
    readonly_fields = ('subject', 'status', 'event_time', 'brevo_message_id')
    can_delete = False
    verbose_name_plural = "Sent Email History"

    def has_add_permission(self, request, obj=None):
        return False

@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ('trackingId', 'status', 'creator_replied', 'country', 'requiresPayment')
    search_fields = ('trackingId',)
    inlines = [PaymentInline, SentEmailInline]
    
    fieldsets = (
        (None, {'fields': ('trackingId', 'status', 'destination', 'expectedDate', 'progressPercent')}),
        ('Creator Information', {
            'fields': ('recipient_name', 'recipient_email', 'country', 'creator_replied')
        }),
        ('Payment', {
            'fields': ('requiresPayment', 'paymentAmount', 'paymentCurrency', 'paymentDescription')
        }),
        ('Manual Email Triggers', {
            'classes': ('collapse',),
            'fields': ('send_confirmation_email', 'send_us_fee_email', 'send_intl_tracking_email','send_intl_arrived_email', 'send_customs_fee_email','send_status_update_email') 
        }),
        ('Tracking Data (JSON)', {
            'classes': ('collapse',),
            'fields': ('progressLabels', 'recentEvent', 'allEvents', 'shipmentDetails')
        }),
    )

    def save_model(self, request, obj, form, change):
        if change and 'send_confirmation_email' in form.changed_data and obj.send_confirmation_email:
            send_transactional_email(obj, CONFIRMATION_TEMPLATE_ID)
            obj.send_confirmation_email = False

        if change and 'send_us_fee_email' in form.changed_data and obj.send_us_fee_email:
            template_id = US_FEE_REPLIED_ID if obj.creator_replied else US_FEE_NO_REPLY_ID
            send_transactional_email(obj, template_id)
            obj.send_us_fee_email = False

        if change and 'send_intl_tracking_email' in form.changed_data and obj.send_intl_tracking_email:
            template_id = INTL_TRACKING_REPLIED_ID if obj.creator_replied else INTL_TRACKING_NO_REPLY_ID
            send_transactional_email(obj, template_id)
            obj.send_intl_tracking_email = False

        if change and 'send_intl_arrived_email' in form.changed_data and obj.send_intl_arrived_email:
            send_transactional_email(obj, INTL_ARRIVED_TEMPLATE_ID)
            obj.send_intl_arrived_email = False    
            
        if change and 'send_customs_fee_email' in form.changed_data and obj.send_customs_fee_email:
            send_transactional_email(obj, CUSTOMS_FEE_TEMPLATE_ID)
            obj.send_customs_fee_email = False

        if change and 'send_status_update_email' in form.changed_data and obj.send_status_update_email:
            send_transactional_email(obj, STATUS_UPDATE_TEMPLATE_ID)
            obj.send_status_update_email = False    
            
        super().save_model(request, obj, form, change)

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

@admin.register(SentEmail)
class SentEmailAdmin(admin.ModelAdmin):
    list_display = ('shipment', 'subject', 'status', 'event_time')
    list_filter = ('status', 'event_time')
    search_fields = ('shipment__recipient_name', 'subject', 'shipment__trackingId')

@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    list_display = ('code', 'shipment', 'value_usd', 'approved', 'created_at')
    list_editable = ('value_usd',)
    list_filter = ('approved', 'created_at')
    search_fields = ('code', 'shipment__trackingId')
    actions = [approve_vouchers] # This now correctly refers to the function defined above

@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ('shipment', 'is_visible', 'receipt_number', 'generated_at')
    list_filter = ('is_visible', 'generated_at')
    search_fields = ('shipment__trackingId', 'receipt_number')

@admin.register(RefundBalance)
class RefundBalanceAdmin(admin.ModelAdmin):
    list_display = ('recipient_email', 'excess_amount_usd', 'status', 'last_update')
    search_fields = ('recipient_email',)
    list_filter = ('status', 'last_update')
    readonly_fields = ('claim_token', 'last_update')
    
    fieldsets = (
        ('Balance Information', {
            'fields': ('recipient_email', 'excess_amount_usd', 'status', 'last_update')
        }),
        ('Manual Refund Details (If Applicable)', {
            'classes': ('collapse',),
            'fields': ('refund_method', 'refund_detail')
        }),
        ('Security', {
            'fields': ('claim_token',)
        }),
    )

@admin.action(description='Send Milani Outreach Email (Individual)')
def send_individual_outreach(modeladmin, request, queryset):
    count = 0
    for creator in queryset:
        send_milani_outreach_email(creator)
        count += 1
    modeladmin.message_user(request, f"Successfully triggered individual send for {count} creators.")
    
@admin.action(description='QUEUE Milani Outreach for Staggered Send (Bulk, Max 100)')
def queue_bulk_outreach(modeladmin, request, queryset):
    max_send = 100 
    creators_to_queue = queryset.filter(status__in=['New Lead', 'Passed'])[:max_send]
    creators_to_queue.update(status='Queued', last_outreach=timezone.now())
    modeladmin.message_user(request, f"Successfully queued {creators_to_queue.count()} creators for staggered outreach. They will be processed shortly.")

@admin.register(Creator)
class CreatorAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'status', 'last_outreach', 'country', 'portfolio_link')
    list_filter = ('status', 'country')
    search_fields = ('name', 'email', 'country')
    actions = [send_individual_outreach, queue_bulk_outreach]
    
@admin.register(MilaniOutreachLog)
class MilaniOutreachLogAdmin(admin.ModelAdmin):
    list_display = ('creator', 'subject', 'status', 'event_time', 'sendgrid_message_id')
    list_filter = ('status', 'event_time')
    search_fields = ('creator__name', 'creator__email', 'subject')
    date_hierarchy = 'event_time'
    readonly_fields = ('creator', 'subject', 'status', 'event_time', 'sendgrid_message_id')