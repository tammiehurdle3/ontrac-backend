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
    """Tracking ID field with ⟳ Generate ID button (client-side crypto random)."""
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
                   letter-spacing:0.5px;"
            onmouseover="this.style.background='#2b5068'"
            onmouseout="this.style.background='#417690'">
            ⟳ Generate ID
        </button>
        '''
        return mark_safe(html + button)


class SortableProviderWidget(forms.Widget):
    """Click to add, drag or use arrows to reorder. Saves as comma-separated string."""
    def __init__(self, choices, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.choices = choices

    def render(self, name, value, attrs=None, renderer=None):
        current = [p.strip() for p in (value or '').split(',') if p.strip()]
        all_ids = [c[0] for c in self.choices]
        label_map = dict(self.choices)
        available = [c for c in all_ids if c not in current]

        selected_html = ''
        for p in current:
            label = label_map.get(p, p)
            selected_html += f'''
            <div class="sp-item" data-id="{p}" draggable="true"
                style="display:flex;align-items:center;justify-content:space-between;
                       padding:6px 10px;margin:3px 0;background:#1e3a2e;border:1px solid #4a9a6a;
                       border-radius:4px;cursor:grab;font-size:12px;">
                <span>☰ &nbsp;{label}</span>
                <div style="display:flex;gap:4px">
                    <button type="button" onclick="spUp(this)"
                        style="padding:1px 7px;font-size:11px;cursor:pointer;border:1px solid #999;border-radius:3px;background:#fff">▲</button>
                    <button type="button" onclick="spDown(this)"
                        style="padding:1px 7px;font-size:11px;cursor:pointer;border:1px solid #999;border-radius:3px;background:#fff">▼</button>
                    <button type="button" onclick="spRemove(this)"
                        style="padding:1px 7px;font-size:11px;cursor:pointer;border:1px solid #f44;border-radius:3px;background:#fff;color:#c00">✕</button>
                </div>
            </div>'''

        available_html = ''
        for pid in available:
            label = label_map.get(pid, pid)
            available_html += f'''
            <div class="sp-avail" data-id="{pid}"
                onclick="spAdd(this)"
                style="display:inline-block;padding:4px 10px;margin:3px;background:#2a2a2a;
                       border:1px solid #555;border-radius:12px;cursor:pointer;font-size:12px;color:#e0e0e0;"
                onmouseover="this.style.background='#1a3a5c'"
                onmouseout="this.style.background='#2a2a2a'">
                + {label}
            </div>'''

        uid = (attrs or {}).get('id', name)
        return mark_safe(f'''
        <input type="hidden" name="{name}" id="{uid}" value="{','.join(current)}">
        <div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:6px">
            <div style="flex:1;min-width:220px">
                <div style="font-size:11px;font-weight:700;color:#666;margin-bottom:4px;text-transform:uppercase;">
                    Selected Order (drag or use arrows)
                </div>
                <div id="sp-selected-{uid}" style="min-height:40px;padding:4px;background:#1a1a1a;
                     border:1px dashed #555;border-radius:4px;">
                    {selected_html}
                </div>
            </div>
            <div style="flex:2;min-width:280px">
                <div style="font-size:11px;font-weight:700;color:#666;margin-bottom:4px;text-transform:uppercase;">
                    Available — click to add
                </div>
                <div id="sp-avail-{uid}" style="padding:4px;background:#1a1a1a;
                     border:1px dashed #555;border-radius:4px;">
                    {available_html}
                </div>
            </div>
        </div>
        <script>
        (function(){{
            var selBox = document.getElementById('sp-selected-{uid}');
            var availBox = document.getElementById('sp-avail-{uid}');
            var hidden = document.getElementById('{uid}');
            var labelMap = {label_map};

            function sync() {{
                var ids = Array.from(selBox.querySelectorAll('.sp-item')).map(function(el){{ return el.dataset.id; }});
                hidden.value = ids.join(',');
            }}

            window.spAdd = window.spAdd || function(){{}};
            window.spRemove = window.spRemove || function(){{}};
            window.spUp = window.spUp || function(){{}};
            window.spDown = window.spDown || function(){{}};

            availBox.addEventListener('click', function(e){{
                var chip = e.target.closest('.sp-avail');
                if (!chip) return;
                var pid = chip.dataset.id;
                var label = labelMap[pid] || pid;
                var div = document.createElement('div');
                div.className = 'sp-item';
                div.dataset.id = pid;
                div.draggable = true;
                div.style.cssText = 'display:flex;align-items:center;justify-content:space-between;padding:6px 10px;margin:3px 0;background:#1e3a2e;border:1px solid #4a9a6a;border-radius:4px;cursor:grab;font-size:12px;color:#e0e0e0;';
                div.innerHTML = '<span>☰ &nbsp;' + label + '</span><div style="display:flex;gap:4px"><button type="button" onclick="javascript:void(0)" style="padding:1px 7px;font-size:11px;cursor:pointer;border:1px solid #999;border-radius:3px;background:#fff">▲</button><button type="button" style="padding:1px 7px;font-size:11px;cursor:pointer;border:1px solid #999;border-radius:3px;background:#fff">▼</button><button type="button" style="padding:1px 7px;font-size:11px;cursor:pointer;border:1px solid #f44;border-radius:3px;background:#fff;color:#c00">✕</button></div>';
                var btns = div.querySelectorAll('button');
                btns[0].onclick = function(){{ spUpEl(div); }};
                btns[1].onclick = function(){{ spDownEl(div); }};
                btns[2].onclick = function(){{ spRemoveEl(div, pid); }};
                addDrag(div);
                selBox.appendChild(div);
                chip.remove();
                sync();
            }});

            function spUpEl(el){{ if(el.previousElementSibling) {{ selBox.insertBefore(el, el.previousElementSibling); sync(); }} }}
            function spDownEl(el){{ if(el.nextElementSibling) {{ selBox.insertBefore(el.nextElementSibling, el); sync(); }} }}
            function spRemoveEl(el, pid){{
                var label = labelMap[pid] || pid;
                var chip = document.createElement('div');
                chip.className = 'sp-avail';
                chip.dataset.id = pid;
                chip.style.cssText = 'display:inline-block;padding:4px 10px;margin:3px;background:#2a2a2a;border:1px solid #555;border-radius:12px;cursor:pointer;font-size:12px;color:#e0e0e0;';
                chip.innerHTML = '+ ' + label;
                chip.onmouseover = function(){{ this.style.background='#1a3a5c'; }};
                chip.onmouseout = function(){{ this.style.background='#2a2a2a'; }};
                availBox.appendChild(chip);
                el.remove();
                sync();
            }}

            // Wire up existing items
            selBox.querySelectorAll('.sp-item').forEach(function(div){{
                var pid = div.dataset.id;
                var btns = div.querySelectorAll('button');
                btns[0].onclick = function(){{ spUpEl(div); }};
                btns[1].onclick = function(){{ spDownEl(div); }};
                btns[2].onclick = function(){{ spRemoveEl(div, pid); }};
                addDrag(div);
            }});

            // Drag and drop
            var dragSrc = null;
            function addDrag(el){{
                el.addEventListener('dragstart', function(){{ dragSrc = el; el.style.opacity='0.4'; }});
                el.addEventListener('dragend', function(){{ el.style.opacity='1'; sync(); }});
                el.addEventListener('dragover', function(e){{ e.preventDefault(); }});
                el.addEventListener('drop', function(e){{
                    e.preventDefault();
                    if(dragSrc !== el) {{ selBox.insertBefore(dragSrc, el); sync(); }}
                }});
            }}
        }})();
        </script>
        ''')

STAGE_KEY_CHOICES = [
    ('label_created',               '1 — Label Created'),
    ('package_received',            '2 — Package Received'),
    ('departed_origin',             '3 — Departed Origin Facility'),
    ('arrived_us_gateway',          '4 — Arrived at US International Gateway'),
    ('export_clearance',            '5 — Export Clearance Completed'),
    ('departed_us',                 '6 — Departed US — In Flight'),
    ('in_transit_intl',             '7 — In Transit — International Flight'),
    ('arrived_hub',                 '8 — Arrived at Regional Sorting Hub'),
    ('departed_hub',                '9 — Departed Sorting Hub'),
    ('arrived_destination_country', '10 — Arrived at Destination Country'),
    ('customs_processing',          '11 — Customs Processing'),
    ('held_customs',                '12 — Held at Customs — Payment Required'),
    ('payment_received',            '13 — Payment Received — Customs Released'),
    ('departed_customs',            '14 — Departed Customs — En Route'),
    ('arrived_local',               '15 — Arrived at Local Delivery Facility'),
    ('out_for_delivery',            '16 — Out for Delivery'),
    ('delivered',                   '17 — Delivered'),
    # Domestic US only
    ('arrived_sort_facility',       'D4 — Arrived at Regional Sort Facility'),
    ('held_delivery',               'D5 — Delivery Exception — Redelivery Fee Required'),
    ('payment_received_domestic',   'D6 — Redelivery Fee Confirmed — Rescheduled'),
]

PROVIDER_CHOICES = [
    ('moonpay',     'MoonPay'),
    ('rampnetwork', 'Ramp Network'),
    ('binance',     'Binance'),
    ('transak',     'Transak'),
    ('guardarian',  'Guardarian'),
    ('stripe',      'Stripe (USD only)'),
    ('simplex',     'Simplex'),
    ('banxa',       'Banxa'),
    ('topper',      'Topper'),
    ('unlimit',     'Unlimit'),
    ('revolut',     'Revolut'),
    ('kryptonim',   'Kryptonim'),
    ('bitnovo',     'Bitnovo (USD only)'),
    ('utorg',       'Utorg'),
    ('transfi',     'TransFi (USD only)'),
    ('sardine',     'Sardine'),
    ('cryptix',     'Cryptix'),
    ('robinhood',   'Robinhood (USD only)'),
    ('interac',     'Interac (CAD only)'),
    ('upi',         'UPI (INR only)'),
    ('wert',        'Credit Card (Wert)'),
]

class ShipmentAdminForm(forms.ModelForm):
    current_stage_key = forms.ChoiceField(
        choices=STAGE_KEY_CHOICES,
        required=False,
        help_text="Select the current stage. Advance Stage will continue from here."
    )

    status = forms.ChoiceField(
        choices=[
            # International
            ('Label Created',                  'Label Created'),
            ('Package Received',               'Package Received'),
            ('Departed Origin Facility',       'Departed Origin Facility'),
            ('Arrived at Hub',                 'Arrived at Hub'),
            ('Departed Hub',                   'Departed Hub'),
            ('Arrived in Destination Country', 'Arrived in Destination Country'),
            ('Out for Delivery',               'Out for Delivery'),
            ('Customs Hold',                   'Customs Hold'),
            ('Pending Payment',                'Pending Payment'),
            ('Payment Confirmed',              'Payment Confirmed'),
            ('Delivered',                      'Delivered'),
            # Domestic US
            ('Arrived at Sort Facility',       'Arrived at Sort Facility (Domestic)'),
            ('Delivery Exception',             'Delivery Exception (Domestic)'),
        ],
        required=False,
        help_text="Visual status shown on tracking page and admin list."
    )

    allowed_payment_providers = forms.MultipleChoiceField(
        choices=PROVIDER_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Leave all unchecked = automatic ShieldClimb selection. Check providers to append them after auto providers."
    )

    provider_display_order = forms.CharField(
        required=False,
        widget=SortableProviderWidget(choices=PROVIDER_CHOICES),
        help_text="Click providers to add. Drag or use ▲▼ to reorder. When anything is set here, auto providers are hidden and only your order shows."
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['allowed_payment_providers'].initial = self.instance.allowed_payment_providers or []
            self.fields['provider_display_order'].initial = self.instance.provider_display_order or ''


    class Meta:
        model = Shipment
        fields = '__all__'
        widgets = {
            'trackingId': TrackingIdWidget(attrs={
                'style': 'width:200px; font-family:monospace; font-size:14px; font-weight:600;'
            }),
        }

    class Media:
        js = ('admin/js/shipment_ai_generate.js',)

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
        (None, {'fields': ('trackingId', 'status', 'destination', 'expectedDate', 'progressPercent'),
                'description': mark_safe('<div id="ai-generate-bar" style="margin-bottom:12px;">'
                               '<div style="margin-bottom:10px;">'
                               '<input id="ai-full-address" placeholder="Paste full recipient address here → city, country, zip auto-fill" '
                               'style="padding:6px 10px;border:1px solid #ccc;border-radius:4px;width:620px;margin-right:8px;">'
                               '<span id="ai-addr-status" style="font-size:12px;color:#888;"></span>'
                               '<input type="hidden" id="ai-dest-zip" value="">'
                               '</div>'
                               '<input id="ai-dest-city" placeholder="Destination city (e.g. Madrid)" '
                               'style="padding:6px 10px;border:1px solid #ccc;border-radius:4px;width:200px;margin-right:8px;">'
                               '<input id="ai-dest-country" placeholder="Country (e.g. Spain)" '
                               'style="padding:6px 10px;border:1px solid #ccc;border-radius:4px;width:200px;margin-right:8px;">'
                               '<button type="button" id="ai-generate-btn" '
                               'style="padding:7px 18px;background:#1a7f5a;color:#fff;border:none;'
                               'border-radius:4px;cursor:pointer;font-weight:700;font-size:13px;">'
                               '✦ AI Generate Shipment Data</button>'
                               '<span id="ai-status" style="margin-left:12px;font-size:12px;color:#666;"></span>'
                               '<br><br>'
                               '<button type="button" id="ai-advance-btn" '
                               'style="padding:7px 18px;background:#7b3f00;color:#fff;border:none;'
                               'border-radius:4px;cursor:pointer;font-weight:700;font-size:13px;">'
                               '✦ Advance to Next Stage</button>'
                               '<span id="ai-advance-status" style="margin-left:12px;font-size:12px;color:#666;"></span>'
                               '</div>'),}),
        ('Creator Information', {
            'classes': ('collapse',),
            'fields': ('recipient_name', 'recipient_email', 'country', 'creator_replied')
        }),
        ('Payment', {
            'classes': ('collapse',),
            'fields': ('requiresPayment', 'paymentAmount', 'paymentCurrency', 'paymentDescription', 'paymentActionMessage', 'allowed_payment_providers', 'provider_display_order')
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
        ('Email Triggers — Domestic (USA)', {
            'classes': ('collapse',),
            'description': 'Use these for US-to-US shipments only.',
            'fields': (
                'send_us_tracking_email',
                'send_us_fee_email',
                'send_us_redelivery_reminder_email',
            ),
        }),
        ('Email Triggers — International', {
            'classes': ('collapse',),
            'description': 'Use these for shipments going outside the USA.',
            'fields': (
                'send_intl_tracking_email',
                'send_intl_arrived_email',
                'send_customs_fee_email',
                'send_customs_fee_reminder_email',
            ),
        }),
        ('Email Triggers — General', {
            'classes': ('collapse',),
            'description': 'Works for both domestic and international.',
            'fields': (
                'send_confirmation_email',
                'send_status_update_email',
            ),
        }),
        ('Tracking Data (JSON)', {
            'classes': ('collapse',),
            'fields': (
                'destination_city', 'destination_country',
                'current_stage_key', 'current_stage_index',
                'progressLabels', 'recentEvent', 'allEvents', 'shipmentDetails',
            )
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
            'send_us_tracking_email': 'us_tracking',
            'send_us_redelivery_reminder_email': 'us_redelivery_reminder',
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
    list_display = ('recipient_info', 'subject', 'status', 'event_time', 'provider_message_id')

    @admin.display(description='Shipment / Recipient')
    def recipient_info(self, obj):
        if obj.shipment:
            return format_html(
                '<span style="font-family:monospace;font-weight:600;">{}</span>'
                '<br><small style="color:#888;">{} · {}</small>',
                obj.shipment.trackingId,
                obj.shipment.recipient_name or '—',
                obj.shipment.recipient_email or '—',
            )
        return '—'
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