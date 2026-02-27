from django.contrib import admin
from .models import Shipment, Payment, SentEmail, Voucher, Receipt, Creator, MilaniOutreachLog, RefundBalance, SiteSettings
from django.conf import settings
import pusher
from decimal import Decimal

# Import the new email service and all template IDs
from .email_service import send_transactional_email, send_manual_custom_email

# NEW: Import Milani Service and a Management Command utility
from .milani_email_service import send_milani_outreach_email
from django.core.management import call_command
from django.utils import timezone
from .views import convert_to_usd
import uuid
from django.db.models import Case, When, Value  
from django.utils.html import format_html
from django import forms
from django.utils.safestring import mark_safe

class TrackingIdWidget(forms.TextInput):
    """Custom widget that renders the trackingId field with a sleek Generate button."""
    def render(self, name, value, attrs=None, renderer=None):
        html = super().render(name, value, attrs, renderer)
        input_id = (attrs or {}).get('id', f'id_{name}')
        button = f'''
        <button type="button"
            onclick="(function(){{
                var a = new Uint8Array(10);
                crypto.getRandomValues(a);
                var digits = Array.from(a).map(function(b){{ return b % 10; }}).join('');
                document.getElementById('{input_id}').value = 'OT' + digits;
            }})()"
            style="margin-left:10px; padding:6px 16px; background:#417690;
                   color:#fff; border:none; border-radius:4px; cursor:pointer;
                   font-size:12px; font-weight:600; vertical-align:middle;
                   letter-spacing:0.5px; transition:background .2s;"
            onmouseover="this.style.background='#2b5068'"
            onmouseout="this.style.background='#417690'">
            ⟳ Generate ID
        </button>
        '''
        return mark_safe(html + button)


class ShipmentAdminForm(forms.ModelForm):
    class Meta:
        model = Shipment
        fields = '__all__'
        widgets = {
            'trackingId': TrackingIdWidget(attrs={'style': 'width:200px; font-family:monospace; font-size:14px; font-weight:600;'}),
        }

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
    classes = ['collapse']
    ordering = ('-timestamp',)

class SentEmailInline(admin.TabularInline):
    model = SentEmail
    extra = 0
    readonly_fields = ('subject', 'status', 'event_time', 'provider_message_id')
    can_delete = False
    verbose_name_plural = "Sent Email History"
    classes = ['collapse']
    max_num = 10
    ordering = ('-event_time',)

    def has_add_permission(self, request, obj=None):
        return False

@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    form = ShipmentAdminForm
    list_display = ('trackingId', 'recipient_name', 'recipient_email', 'colored_status', 'creator_replied', 'country', 'requiresPayment')
    search_fields = ('trackingId', 'recipient_name', 'recipient_email')
    inlines = [PaymentInline, SentEmailInline]
    list_per_page = 25
    prefetch_related = ('payments', 'email_history')
    list_filter = ('status', 'country', 'requiresPayment', 'creator_replied')

    @admin.display(description='Status', ordering='status')
    def colored_status(self, obj):
        colors = {
            'Delivered': '#28a745',
            'Payment Confirmed': '#28a745',
            'In Transit': '#fd7e14',
            'Out for Delivery': '#fd7e14',
            'Pending Payment': '#dc3545',
            'Customs Hold': '#dc3545',
        }
        color = colors.get(obj.status, '#6c757d')
        return format_html('<b style="color: {};">{}</b>', color, obj.status)
    
    fieldsets = (
        (None, {'fields': ('trackingId', 'status', 'destination', 'expectedDate', 'progressPercent')}),
        ('Creator Information', {
            'classes': ('collapse',),
            'fields': ('recipient_name', 'recipient_email', 'country', 'creator_replied')
        }),
        ('Payment', {
            'classes': ('collapse',),
            'fields': ('requiresPayment', 'paymentAmount', 'paymentCurrency', 'paymentDescription')
        }),
        ('Custom Manual Email', {
            'classes': ('collapse',),
            'description': "Type a custom message here to send manually while maintaining OnTrac styling.",
            'fields': (
                'manual_email_subject', 
                'manual_email_heading', 
                'manual_email_body', 
                'manual_email_include_tracking_box',  # NEW
                'manual_email_include_payment_button', # NEW
                'manual_email_button_text',            #
                'trigger_manual_email'
            )
        }),
        ('Manual Email Triggers', {
            'classes': ('collapse',),
            # --- THIS SECTION IS NOW CORRECTED ---
            'fields': ('send_confirmation_email', 'send_us_fee_email', 'send_intl_tracking_email','send_intl_arrived_email', 'send_customs_fee_email','send_status_update_email','send_customs_fee_reminder_email'),
        }),
        ('Tracking Data (JSON)', {
            'classes': ('collapse',),
            'fields': ('progressLabels', 'recentEvent', 'allEvents', 'shipmentDetails')
        }),
    )

    def save_model(self, request, obj, form, change):
        # A dictionary to map the checkbox field name to the email_type string
        email_triggers = {
            'send_confirmation_email': 'confirmation',
            'send_us_fee_email': 'us_fee',
            'send_intl_tracking_email': 'intl_tracking',
            'send_intl_arrived_email': 'intl_arrived',
            'send_customs_fee_email': 'customs_fee',
            'send_status_update_email': 'status_update',
            'send_customs_fee_reminder_email': 'customs_fee_reminder',
        }

        if change:
            # Loop through the triggers to see which box was checked
            for field_name, email_type in email_triggers.items():
                if form.cleaned_data.get(field_name):
                    send_transactional_email(obj, email_type)
                    setattr(obj, field_name, False) # Reset the checkbox

            # 2. Handle the NEW Manual Custom Email Trigger
            if form.cleaned_data.get('trigger_manual_email'):
                subject = obj.manual_email_subject or f"Update regarding shipment {obj.trackingId}"
                heading = obj.manual_email_heading or "Shipment Notification"
                body = obj.manual_email_body
                inc_track = obj.manual_email_include_tracking_box
                inc_pay = obj.manual_email_include_payment_button
                btn_txt = obj.manual_email_button_text
                
                if body:
                    # We pass all the variables here
                    send_manual_custom_email(obj, subject, heading, body, inc_track, inc_pay, btn_txt)
                    
                    # Reset the trigger so it doesn't send again on next save
                    obj.trigger_manual_email = False
                    
        super().save_model(request, obj, form, change)

        if pusher_client:
            try:
                channel_name = f'shipment-{obj.trackingId}'
                pusher_client.trigger(channel_name, 'update', {'message': 'Shipment updated'})
            except Exception as e:
                print(f"CRITICAL: Error sending Pusher notification: {e}")

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('shipment', 'cardholderName', 'voucherCode', 'timestamp')
    list_per_page = 50
    list_select_related = ('shipment',)

@admin.register(SentEmail)
class SentEmailAdmin(admin.ModelAdmin):
    show_full_result_count = False
    list_display = ('shipment', 'subject', 'status', 'event_time', 'provider_message_id')
    list_filter = ('status', 'event_time')
    search_fields = ('shipment__recipient_name', 'subject', 'shipment__trackingId', 'shipment__recipient_email')
    list_per_page = 50
    list_select_related = ('shipment',)
    date_hierarchy = 'event_time'

@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    list_display = ('code', 'shipment', 'value_usd', 'approved', 'created_at')
    list_editable = ('value_usd',)
    list_filter = ('approved', 'created_at')
    search_fields = ('code', 'shipment__trackingId')
    actions = [approve_vouchers] 
    list_per_page = 50
    list_select_related = ('shipment', 'used_by', 'approved_by')

@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ('shipment', 'is_visible', 'receipt_number', 'generated_at')
    list_filter = ('is_visible', 'generated_at')
    search_fields = ('shipment__trackingId', 'receipt_number')
    list_per_page = 50
    list_select_related = ('shipment', 'approved_by')

@admin.register(RefundBalance)
class RefundBalanceAdmin(admin.ModelAdmin):
    list_display = ('recipient_email', 'excess_amount_usd', 'status', 'last_update')
    search_fields = ('recipient_email',)
    list_filter = ('status', 'last_update')
    readonly_fields = ('claim_token', 'last_update')
    list_per_page = 50

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
    list_display = ('name', 'email','colored_status', 'last_outreach', 'country', 'portfolio_link')
    list_filter = ('status', 'country')
    search_fields = ('name', 'email', 'country')
    actions = [send_individual_outreach, queue_bulk_outreach]
    list_per_page = 50

    # --- 4. COLOR-CODING METHOD (Your second idea) ---
    @admin.display(description='Status', ordering='status')
    def colored_status(self, obj):
        if obj.status == 'New Lead':
            color = 'green'
            text = 'NEW LEAD'
        elif obj.status in ['Sent', 'Queued']:
            color = 'orange'
            text = obj.status.upper()
        elif obj.status in ['Invalid Email', 'Dropped', 'Bounced', 'Failed']:
            color = 'red'
            text = obj.status.upper()
        else:
            color = 'inherit' # Default text color
            text = obj.status.upper()
            
        return format_html('<b style="color: {};">{}</b>', color, text)    
    
@admin.register(MilaniOutreachLog)
class MilaniOutreachLogAdmin(admin.ModelAdmin):
    show_full_result_count = False
    list_display = ('creator', 'subject', 'status', 'event_time', 'sendgrid_message_id')
    list_filter = ('status', 'event_time')
    search_fields = ('creator__name', 'creator__email', 'subject')
    date_hierarchy = 'event_time'
    readonly_fields = ('creator', 'subject', 'status', 'event_time', 'sendgrid_message_id')
    list_per_page = 100
    list_select_related = ('creator',)

@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'email_provider')
    
    def has_add_permission(self, request):
        # Only allow one settings object to exist
        return not SiteSettings.objects.exists()
    
    def has_delete_permission(self, request, obj=None):
        return False