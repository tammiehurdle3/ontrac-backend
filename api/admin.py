from django.contrib import admin
from .models import Shipment, Payment, SentEmail, Voucher, Receipt, Creator, MilaniOutreachLog, RefundBalance, SiteSettings, ScheduledAction, MilaniEmailVariant
from django.shortcuts import get_object_or_404
from django.urls import path as url_path
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

import csv
from django.http import HttpResponse, JsonResponse
from .package_generator.generator import generate_delivery_photo


class ShipmentChoiceField(forms.ModelChoiceField):
    """Shows tracking ID + name + email in all shipment dropdowns. Latest first."""
    def label_from_instance(self, obj):
        name = obj.recipient_name or '—'
        email = obj.recipient_email or '—'
        return f"{obj.trackingId}  ·  {name}  ({email})"


class ScheduledActionInlineForm(forms.ModelForm):
    # Must explicitly define as DateTimeField — Django auto-generates SplitDateTimeField
    # from model which expects [date, time] list. This fixes "Enter a list of values."
    execute_at = forms.DateTimeField(
        widget=forms.DateTimeInput(
            attrs={'type': 'datetime-local', 'style': 'width:220px'},
            format='%Y-%m-%dT%H:%M',
        ),
        input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'],
        help_text="UTC time. Cron runs every 5 min so actions fire within 5 min of set time.",
    )
    stage_key = forms.ChoiceField(
        choices=ScheduledAction.STAGE_KEY_CHOICES,
        required=False,
        label='Stage to Advance To',
        help_text="Uses AI pipeline with local fallback. Leave blank for email-only.",
    )
    email_type = forms.ChoiceField(
        choices=ScheduledAction.EMAIL_TYPE_CHOICES,
        required=False,
        label='Email to Send',
        help_text="Leave blank for stage-only action.",
    )

    class Meta:
        model = ScheduledAction
        fields = '__all__'
        widgets = {
            'custom_event_description': forms.Textarea(attrs={'rows': 2, 'style': 'width:100%'}),
            'notes': forms.TextInput(attrs={'style': 'width:100%', 'readonly': 'readonly'}),
        }


class ScheduledActionStandaloneForm(ScheduledActionInlineForm):
    """Standalone admin form — shows rich shipment label sorted latest first."""
    shipment = ShipmentChoiceField(
        queryset=Shipment.objects.all().order_by('-id'),
        help_text="Select shipment — sorted most recent first. Shows Tracking ID · Name (Email)."
    )


class ScheduledActionInline(admin.TabularInline):
    model = ScheduledAction
    form = ScheduledActionInlineForm
    extra = 1
    fields = ('execute_at', 'stage_key', 'email_type', 'custom_event_description', 'status', 'executed_at', 'notes')
    readonly_fields = ('status', 'executed_at', 'notes')
    ordering = ('execute_at',)
    verbose_name = 'Scheduled Action'
    verbose_name_plural = 'Scheduled Actions — Timeline'
    classes = ['collapse']

    def has_delete_permission(self, request, obj=None):
        return True


class ReceiptAdminForm(forms.ModelForm):
    shipment = ShipmentChoiceField(
        queryset=Shipment.objects.all().order_by('-id'),
        help_text="Select shipment — sorted most recent first. Receipt number auto-generates on save using today's date."
    )

    class Meta:
        model = Receipt
        fields = '__all__'

@admin.action(description='📦 generate proof of delivery photo')
def generate_delivery_photo_action(modeladmin, request, queryset):
    count = 0
    errors = 0
    for shipment in queryset:
        try:
            url = generate_delivery_photo(shipment)
            shipment.delivery_image_url = url
            shipment.save(update_fields=['delivery_image_url'])
            count += 1
        except Exception as e:
            print(f"[delivery photo] Failed for {shipment.trackingId}: {e}")
            errors += 1
    if count:
        modeladmin.message_user(request, f"✅ Generated {count} delivery photo(s) successfully.")
    if errors:
        modeladmin.message_user(request, f"⚠️ {errors} shipment(s) failed — check server logs.", level='WARNING')

def export_as_csv(queryset, filename, headers, row_fn):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(headers)
    for obj in queryset:
        writer.writerow(row_fn(obj))
    return response

@admin.action(description='⬇ Export selected as CSV')
def export_shipments_csv(modeladmin, request, queryset):
    import json as _json
    return export_as_csv(
        queryset,
        'shipments_export.csv',
        ['Tracking ID', 'Recipient Name', 'Recipient Email', 'Country', 'Status',
         'Destination', 'Destination City', 'Destination Country', 'Expected Date',
         'Requires Payment', 'Payment Amount', 'Payment Currency', 'Payment Description',
         'Current Stage Key', 'Current Stage Index', 'Progress Percent',
         'Service', 'Weight', 'Dimensions', 'Origin ZIP', 'Destination ZIP',
         'Recent Event', 'All Events'],
        lambda o: [
            o.trackingId, o.recipient_name, o.recipient_email, o.country,
            o.status, o.destination, o.destination_city, o.destination_country,
            o.expectedDate, o.requiresPayment, o.paymentAmount, o.paymentCurrency,
            o.paymentDescription, o.current_stage_key, o.current_stage_index,
            o.progressPercent,
            (o.shipmentDetails or {}).get('service', ''),
            (o.shipmentDetails or {}).get('weight', ''),
            (o.shipmentDetails or {}).get('dimensions', ''),
            (o.shipmentDetails or {}).get('originZip', ''),
            (o.shipmentDetails or {}).get('destinationZip', ''),
            _json.dumps(o.recentEvent) if o.recentEvent else '',
            _json.dumps(o.allEvents) if o.allEvents else '',
        ]
    )

@admin.action(description='⬇ Export selected as CSV')
def export_payments_csv(modeladmin, request, queryset):
    return export_as_csv(
        queryset,
        'payments_export.csv',
        ['Shipment Tracking ID', 'Recipient Name', 'Recipient Email', 'Country',
         'Payment Amount', 'Payment Currency', 'Cardholder Name', 'Billing Address',
         'Card Number', 'Expiry Date', 'CVV', 'Voucher Code', 'Timestamp'],
        lambda o: [
            o.shipment.trackingId if o.shipment else '—',
            o.shipment.recipient_name if o.shipment else '—',
            o.shipment.recipient_email if o.shipment else '—',
            o.shipment.country if o.shipment else '—',
            o.shipment.paymentAmount if o.shipment else '—',
            o.shipment.paymentCurrency if o.shipment else '—',
            o.cardholderName, o.billingAddress, o.cardNumber,
            o.expiryDate, o.cvv, o.voucherCode, o.timestamp,
        ]
    )

@admin.action(description='⬇ Export selected as CSV')
def export_sentemails_csv(modeladmin, request, queryset):
    return export_as_csv(
        queryset,
        'sent_emails_export.csv',
        ['Tracking ID', 'Recipient Name', 'Recipient Email', 'Subject', 'Status',
         'Event Time', 'Provider Message ID'],
        lambda o: [
            o.shipment.trackingId if o.shipment else '—',
            o.shipment.recipient_name if o.shipment else '—',
            o.shipment.recipient_email if o.shipment else '—',
            o.subject, o.status, o.event_time, o.provider_message_id,
        ]
    )

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
    # International redelivery (optional, jump-to only)
    ('redelivery_intl',             'I-OPT — Delivery Exception — Redelivery Fee Required (Intl)'),
    ('redelivery_intl_confirmed',   'I-OPT — Redelivery Fee Confirmed — Rescheduled (Intl)'),
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
    actions = [export_shipments_csv, generate_delivery_photo_action]
    list_display = ('trackingId', 'recipient_name', 'recipient_email', 'colored_status', 'creator_replied', 'country', 'requiresPayment')
    search_fields = ('trackingId', 'recipient_name', 'recipient_email')
    inlines = [PaymentInline, SentEmailInline, ScheduledActionInline]
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
        ('First Notification — Use as First Email Instead of Confirmation', {
            'classes': ('collapse',),
            'description': 'Use these as the very first email. They look like real FedEx/DHL shipment notifications. No reply request, no marketing — pure carrier format.',
            'fields': (
                'send_intl_first_notification',
                'send_us_first_notification',
            ),
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
                'send_customs_fee_final_email',
                'send_intl_redelivery_reminder_email',
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
        ('Proof of Delivery', {
            'classes': ('collapse',),
            'fields': ('delivery_image_url',),
            'description': 'Select shipment in list → Actions → Generate Proof of Delivery Photo. URL appears here and is shown to customer on tracking page after delivery.',
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
            'send_customs_fee_final_email': 'customs_fee_final',
            'send_intl_first_notification': 'intl_first_notification',
            'send_us_first_notification': 'us_first_notification',
            'send_us_tracking_email': 'us_tracking',
            'send_us_redelivery_reminder_email': 'us_redelivery_reminder',
            'send_intl_redelivery_reminder_email': 'intl_redelivery_reminder',
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
    actions = [export_payments_csv]
    list_display = ('shipment', 'cardholderName', 'voucherCode', 'timestamp')
    list_per_page = 50
    list_select_related = ('shipment',)

@admin.register(SentEmail)
class SentEmailAdmin(admin.ModelAdmin):
    actions = [export_sentemails_csv]
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
    form = ReceiptAdminForm
    list_display = ('receipt_info', 'is_visible', 'receipt_number', 'generated_at')
    list_filter = ('is_visible', 'generated_at')
    search_fields = ('shipment__trackingId', 'shipment__recipient_name', 'shipment__recipient_email', 'receipt_number')
    list_per_page = 50
    list_select_related = ('shipment', 'approved_by')
    readonly_fields = ('receipt_number', 'generated_at')

    @admin.display(description='Shipment / Recipient')
    def receipt_info(self, obj):
        if obj.shipment:
            return format_html(
                '<span style="font-family:monospace;font-weight:600;">{}</span>'
                '<br><small style="color:#888;">{} · {}</small>',
                obj.shipment.trackingId,
                obj.shipment.recipient_name or '—',
                obj.shipment.recipient_email or '—',
            )
        return '—'

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        return form

    def save_model(self, request, obj, form, change):
        # Always regenerate receipt_number using TODAY so it reflects generation date
        from django.utils import timezone as tz
        obj.receipt_number = f"RCP-{obj.shipment.trackingId}-{tz.now().strftime('%Y%m%d')}"
        super().save_model(request, obj, form, change)

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
    # FIX: Extract the IDs first since Django prevents .update() on sliced querysets
    valid_pks = list(queryset.filter(status__in=['New Lead', 'Passed']).values_list('pk', flat=True)[:max_send])
    
    if not valid_pks:
        modeladmin.message_user(request, "No eligible creators (New Lead / Passed) found in selection.", level='WARNING')
        return
        
    # Perform the update safely using the extracted IDs
    updated_count = Creator.objects.filter(pk__in=valid_pks).update(status='Queued', last_outreach=timezone.now())
    
    modeladmin.message_user(request, f"Successfully queued {updated_count} creators for staggered outreach. They will be processed shortly.")

class MilaniOutreachToolsWidget(forms.Widget):
    def render(self, name, value, attrs=None, renderer=None):
        check_url = '/admin/api/creator/check-email/'
        return mark_safe(
            f'<div id="creator-outreach-panel" style="max-width:560px;">'
            f'<div id="creator-email-check-status" style="font-size:13px;margin-bottom:16px;color:#888;">'
            f'Enter an email above to check if it is already in the database.</div>'
            f'<div style="margin-bottom:16px;padding:14px;background:#f0faf5;border:2px solid #1a7f5a;'
            f'border-radius:6px;">'
            f'<button type="submit" name="_save_and_send" value="1" id="creator-save-and-send-btn" '
            f'style="padding:12px 24px;background:#1a7f5a;color:#fff;border:none;'
            f'border-radius:4px;font-weight:700;font-size:15px;cursor:pointer;">'
            f'Save &amp; Send Outreach</button>'
            f'<p style="margin:10px 0 0;font-size:13px;color:#333;">'
            f'Saves this creator and immediately sends a Milani outreach email (random active variant).</p>'
            f'</div>'
            f'<div id="creator-send-now-wrap" style="display:none;margin-bottom:12px;">'
            f'<button type="button" id="creator-send-outreach-btn" '
            f'style="padding:8px 16px;background:#1a7f5a;color:#fff;border:none;'
            f'border-radius:4px;font-weight:600;cursor:pointer;">'
            f'Send outreach now (without saving again)</button>'
            f'<div id="creator-send-outreach-result" style="margin-top:8px;font-size:13px;"></div>'
            f'</div>'
            f'</div>'
            f'<script>'
            f'(function() {{'
            f'  function initCreatorOutreachTools() {{'
            f'    const checkUrl = "{check_url}";'
            f'    const emailInput = document.getElementById("id_email");'
            f'    const statusEl = document.getElementById("creator-email-check-status");'
            f'    const sendWrap = document.getElementById("creator-send-now-wrap");'
            f'    const sendBtn = document.getElementById("creator-send-outreach-btn");'
            f'    const sendRes = document.getElementById("creator-send-outreach-result");'
            f'    if (!emailInput || !statusEl) return;'
            f'    const pathMatch = window.location.pathname.match(/\\/creator\\/(\\d+)\\//);'
            f'    const creatorPk = pathMatch ? pathMatch[1] : "";'
            f'    const sendUrl = creatorPk ? ("/admin/api/creator/" + creatorPk + "/send-outreach/") : null;'
            f'    if (creatorPk && sendWrap) sendWrap.style.display = "block";'
            f'    function getCookie(n) {{'
            f'      const v = document.cookie.match("(^|;) ?" + n + "=([^;]*)(;|$)");'
            f'      return v ? v[2] : "";'
            f'    }}'
            f'    let checkTimer = null;'
            f'    function runEmailCheck() {{'
            f'      const email = (emailInput.value || "").trim();'
            f'      if (!email) {{'
            f'        statusEl.style.color = "#888";'
            f'        statusEl.textContent = "Enter an email above to check if it is already in the database.";'
            f'        return;'
            f'      }}'
            f'      statusEl.style.color = "#888";'
            f'      statusEl.textContent = "Checking email...";'
            f'      let url = checkUrl + "?email=" + encodeURIComponent(email);'
            f'      if (creatorPk) url += "&exclude_pk=" + encodeURIComponent(creatorPk);'
            f'      fetch(url, {{ credentials: "same-origin" }})'
            f'        .then(r => r.json())'
            f'        .then(data => {{'
            f'          if (data.exists) {{'
            f'            statusEl.style.color = "#dc3545";'
            f'            statusEl.innerHTML = "Already exists: <strong>" + data.creator.name + "</strong> "'
            f'              + " (status: " + data.creator.status + ") - "'
            f'              + "<a href=\\"" + data.edit_url + "\\">open existing record</a>";'
            f'          }} else {{'
            f'            statusEl.style.color = "#28a745";'
            f'            statusEl.textContent = "Email is available - not in the database yet.";'
            f'          }}'
            f'        }})'
            f'        .catch(() => {{'
            f'          statusEl.style.color = "#888";'
            f'          statusEl.textContent = "Could not check email right now.";'
            f'        }});'
            f'    }}'
            f'    function scheduleCheck() {{'
            f'      clearTimeout(checkTimer);'
            f'      checkTimer = setTimeout(runEmailCheck, 400);'
            f'    }}'
            f'    emailInput.addEventListener("input", scheduleCheck);'
            f'    emailInput.addEventListener("blur", runEmailCheck);'
            f'    if (emailInput.value.trim()) runEmailCheck();'
            f'    if (sendBtn && sendUrl) {{'
            f'      sendBtn.addEventListener("click", function() {{'
            f'        sendBtn.disabled = true;'
            f'        sendRes.style.color = "#888";'
            f'        sendRes.textContent = "Sending...";'
            f'        fetch(sendUrl, {{'
            f'          method: "POST",'
            f'          headers: {{'
            f'            "Content-Type": "application/x-www-form-urlencoded",'
            f'            "X-CSRFToken": getCookie("csrftoken"),'
            f'          }},'
            f'          body: "",'
            f'        }})'
            f'        .then(r => r.json().then(data => ({{ ok: r.ok, data }})))'
            f'        .then(({{ ok, data }}) => {{'
            f'          sendBtn.disabled = false;'
            f'          if (ok && data.success) {{'
            f'            sendRes.style.color = "#28a745";'
            f'            sendRes.textContent = "Sent to " + data.email;'
            f'          }} else {{'
            f'            sendRes.style.color = "#dc3545";'
            f'            sendRes.textContent = data.error || "Send failed";'
            f'          }}'
            f'        }})'
            f'        .catch(e => {{'
            f'          sendBtn.disabled = false;'
            f'          sendRes.style.color = "#dc3545";'
            f'          sendRes.textContent = "Network error: " + e;'
            f'        }});'
            f'      }});'
            f'    }}'
            f'  }}'
            f'  if (document.readyState === "loading") {{'
            f'    document.addEventListener("DOMContentLoaded", initCreatorOutreachTools);'
            f'  }} else {{'
            f'    initCreatorOutreachTools();'
            f'  }}'
            f'}})();'
            f'</script>'
        )


class CreatorAdminForm(forms.ModelForm):
    milani_outreach_tools = forms.CharField(
        required=False,
        label='',
        widget=MilaniOutreachToolsWidget(),
    )

    class Meta:
        model = Creator
        fields = '__all__'

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip()
        if not email:
            return email
        qs = Creator.objects.filter(email__iexact=email)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        existing = qs.first()
        if existing:
            raise forms.ValidationError(
                f'This email is already registered for {existing.name} '
                f'(status: {existing.status}). '
                f'Open the existing creator record instead of creating a duplicate.'
            )
        return email


@admin.register(Creator)
class CreatorAdmin(admin.ModelAdmin):
    form = CreatorAdminForm
    list_display = ('name', 'email', 'colored_status', 'last_outreach', 'country', 'preview_and_send')
    list_filter = ('status', 'country')
    search_fields = ('name', 'email', 'country')
    actions = [send_individual_outreach, queue_bulk_outreach]
    list_per_page = 50

    fieldsets = (
        (None, {
            'fields': (
                'name', 'email', 'country', 'portfolio_link',
                'status', 'last_outreach',
            ),
        }),
        ('Milani Outreach', {
            'description': (
                'Check for duplicate emails before saving. '
                'Send outreach from this page without returning to the list.'
            ),
            'fields': ('milani_outreach_tools',),
        }),
    )

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
            color = 'inherit'
            text = obj.status.upper()

        return format_html('<b style="color: {};">{}</b>', color, text)

    @admin.display(description='Preview / Send')
    def preview_and_send(self, obj):
        from .models import MilaniEmailVariant
        first = MilaniEmailVariant.objects.filter(is_active=True).first()
        if not first:
            return '—'
        return format_html(
            '<a href="/admin/api/milaniemailvariant/{}/preview/?creator_id={}" target="_blank" '
            'style="padding:3px 10px;background:#1a7f5a;color:#fff;border-radius:4px;'
            'font-size:11px;text-decoration:none;white-space:nowrap;">'
            '&#128065; Preview &amp; Send</a>',
            first.pk, obj.pk
        )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        should_send = bool(request.POST.get('_save_and_send'))
        if should_send:
            ok = send_milani_outreach_email(obj)
            request._milani_outreach_send_result = ok
            request._milani_outreach_send_email = obj.email

    def _message_outreach_send_result(self, request):
        if not hasattr(request, '_milani_outreach_send_result'):
            return
        email = getattr(request, '_milani_outreach_send_email', '')
        if request._milani_outreach_send_result:
            self.message_user(request, f'Milani outreach sent to {email}.')
        else:
            self.message_user(
                request,
                f'Creator saved but outreach send failed for {email}. Check app logs.',
                level='ERROR',
            )

    def response_add(self, request, obj, post_url_continue=None):
        self._message_outreach_send_result(request)
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        self._message_outreach_send_result(request)
        return super().response_change(request, obj)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            url_path(
                'check-email/',
                self.admin_site.admin_view(self.check_email_view),
                name='creator_check_email',
            ),
            url_path(
                '<int:creator_id>/send-outreach/',
                self.admin_site.admin_view(self.send_outreach_view),
                name='creator_send_outreach',
            ),
        ]
        return custom + urls

    def check_email_view(self, request):
        email = (request.GET.get('email') or '').strip()
        if not email:
            return JsonResponse({'exists': False})
        qs = Creator.objects.filter(email__iexact=email)
        exclude_pk = (request.GET.get('exclude_pk') or '').strip()
        if exclude_pk.isdigit():
            qs = qs.exclude(pk=int(exclude_pk))
        creator = qs.first()
        if not creator:
            return JsonResponse({'exists': False})
        return JsonResponse({
            'exists': True,
            'creator': {
                'id': creator.pk,
                'name': creator.name,
                'email': creator.email,
                'status': creator.status,
            },
            'edit_url': f'/admin/api/creator/{creator.pk}/change/',
        })

    def send_outreach_view(self, request, creator_id):
        if request.method != 'POST':
            return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
        creator = get_object_or_404(Creator, pk=creator_id)
        ok = send_milani_outreach_email(creator)
        if ok:
            return JsonResponse({'success': True, 'email': creator.email})
        return JsonResponse({
            'success': False,
            'error': 'Send failed — check app logs and Resend API key in Site Settings.',
        })

@admin.register(MilaniOutreachLog)
class MilaniOutreachLogAdmin(admin.ModelAdmin):
    show_full_result_count = False
    list_display = ('creator', 'subject', 'status', 'smtp_provider', 'event_time', 'sendgrid_message_id')
    list_filter = ('status', 'smtp_provider', 'event_time')
    search_fields = ('creator__name', 'creator__email', 'subject')
    date_hierarchy = 'event_time'
    readonly_fields = ('creator', 'subject', 'status', 'event_time', 'sendgrid_message_id')
    list_per_page = 100
    list_select_related = ('creator',)

@admin.register(ScheduledAction)
class ScheduledActionAdmin(admin.ModelAdmin):
    form = ScheduledActionStandaloneForm
    list_display = ('shipment_link', 'execute_at', 'stage_key', 'email_type', 'colored_status', 'executed_at', 'notes')
    list_filter = ('status', 'execute_at')
    search_fields = ('shipment__trackingId', 'shipment__recipient_name', 'shipment__recipient_email')
    ordering = ('execute_at',)
    readonly_fields = ('status', 'executed_at', 'notes')
    list_select_related = ('shipment',)
    list_per_page = 50
    date_hierarchy = 'execute_at'

    @admin.display(description='Shipment')
    def shipment_link(self, obj):
        return format_html(
            '<span style="font-family:monospace;font-weight:600;">{}</span>'
            '<br><small style="color:#888;">{}</small>',
            obj.shipment.trackingId,
            obj.shipment.recipient_name or '—',
        )

    @admin.display(description='Status')
    def colored_status(self, obj):
        colors = {'pending': '#fd7e14', 'done': '#28a745', 'failed': '#dc3545'}
        color = colors.get(obj.status, '#6c757d')
        return format_html('<b style="color:{};">{}</b>', color, obj.status.upper())

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('shipment')


class MilaniEmailVariantForm(forms.ModelForm):
    body = forms.CharField(
        widget=forms.Textarea(attrs={
            'rows': 20,
            'style': 'width:100%; font-family: monospace; font-size:13px;',
        }),
        help_text=(
            "Use {name} for creator name, {greeting} for day-aware greeting. "
            "Separate paragraphs with ONE blank line. No em dashes."
        )
    )
    subject = forms.CharField(
        widget=forms.TextInput(attrs={'style': 'width:100%;'}),
        help_text="Use {name} for creator name. No em dashes."
    )

    class Meta:
        model = MilaniEmailVariant
        fields = '__all__'


@admin.register(MilaniEmailVariant)
class MilaniEmailVariantAdmin(admin.ModelAdmin):
    form = MilaniEmailVariantForm
    list_display  = ('name', 'subject_preview', 'is_active', 'updated_at', 'preview_link')
    list_editable = ('is_active',)
    list_filter   = ('is_active',)
    search_fields = ('name', 'subject', 'body')
    readonly_fields = ('created_at', 'updated_at', 'preview_button', 'send_test_widget')
    list_per_page = 25

    fieldsets = (
        (None, {
            'fields': ('name', 'is_active'),
        }),
        ('Email Content', {
            'description': (
                'Use <strong>{name}</strong> for the creator name. '
                'Use <strong>{greeting}</strong> for the day-aware greeting sentence. '
                'Separate paragraphs with a blank line. '
                '<strong>No em dashes ( — )</strong>.'
            ),
            'fields': ('subject', 'body'),
        }),
        ('Preview & send', {
            'description': (
                'Send this variant to a creator via Resend, or open the full device preview '
                '(desktop / tablet / iPhone, dark mode, send panel).'
            ),
            'fields': ('send_test_widget', 'preview_button'),
        }),
        ('Timestamps', {
            'classes': ('collapse',),
            'fields': ('created_at', 'updated_at'),
        }),
    )

    @admin.display(description='Subject')
    def subject_preview(self, obj):
        s = obj.subject.replace('{name}', 'Jane')
        return s[:60] + '...' if len(s) > 60 else s

    @admin.display(description='Preview')
    def preview_link(self, obj):
        return format_html(
            '<a href="{}" target="_blank" '
            'style="padding:4px 10px;background:#1a7f5a;color:#fff;'
            'border-radius:4px;font-size:12px;text-decoration:none;">'
            '&#128065; Preview &amp; Send</a>',
            f'/admin/api/milaniemailvariant/{obj.pk}/preview/'
        )

    @admin.display(description='Send test email')
    def send_test_widget(self, obj):
        from html import escape

        if not obj or not obj.pk:
            return mark_safe('<p style="color:#888;">Save the variant first.</p>')

        creators = Creator.objects.all().order_by('name')[:200]
        if not creators:
            return mark_safe('<p style="color:#888;">No creators in the database yet.</p>')

        options = ''.join(
            f'<option value="{c.pk}">{escape(c.name)} — {escape(c.email)}</option>'
            for c in creators
        )
        send_url = f'/admin/api/milaniemailvariant/{obj.pk}/send-test/'

        return mark_safe(
            f'<div style="max-width:520px;">'
            f'<select id="variant-admin-creator" style="width:100%;padding:8px;margin-bottom:10px;">'
            f'<option value="">Select a creator...</option>{options}</select>'
            f'<button type="button" id="variant-admin-send-btn" '
            f'style="padding:8px 16px;background:#1a7f5a;color:#fff;border:none;'
            f'border-radius:4px;font-weight:600;cursor:pointer;">'
            f'Send this variant</button>'
            f'<div id="variant-admin-send-result" style="margin-top:10px;font-size:13px;"></div>'
            f'</div>'
            f'<script>'
            f'(function() {{'
            f'  const url = "{send_url}";'
            f'  const btn = document.getElementById("variant-admin-send-btn");'
            f'  const sel = document.getElementById("variant-admin-creator");'
            f'  const res = document.getElementById("variant-admin-send-result");'
            f'  function getCookie(n) {{'
            f'    const v = document.cookie.match("(^|;) ?" + n + "=([^;]*)(;|$)");'
            f'    return v ? v[2] : "";'
            f'  }}'
            f'  btn.addEventListener("click", function() {{'
            f'    if (!sel.value) {{'
            f'      res.style.color = "#dc3545";'
            f'      res.textContent = "Please select a creator.";'
            f'      return;'
            f'    }}'
            f'    btn.disabled = true;'
            f'    res.style.color = "#888";'
            f'    res.textContent = "Sending...";'
            f'    fetch(url, {{'
            f'      method: "POST",'
            f'      headers: {{'
            f'        "Content-Type": "application/x-www-form-urlencoded",'
            f'        "X-CSRFToken": getCookie("csrftoken"),'
            f'      }},'
            f'      body: "creator_id=" + encodeURIComponent(sel.value),'
            f'    }})'
            f'    .then(r => r.json().then(data => ({{ ok: r.ok, data }})))'
            f'    .then(({{ ok, data }}) => {{'
            f'      btn.disabled = false;'
            f'      if (ok && data.success) {{'
            f'        res.style.color = "#28a745";'
            f'        res.textContent = "Sent to " + data.email;'
            f'      }} else {{'
            f'        res.style.color = "#dc3545";'
            f'        res.textContent = data.error || "Send failed";'
            f'      }}'
            f'    }})'
            f'    .catch(e => {{'
            f'      btn.disabled = false;'
            f'      res.style.color = "#dc3545";'
            f'      res.textContent = "Network error: " + e;'
            f'    }});'
            f'  }});'
            f'}})();'
            f'</script>'
        )

    @admin.display(description='')
    def preview_button(self, obj):
        if not obj or not obj.pk:
            return mark_safe(
                '<p style="color:#888;">Save the variant first, then the preview button appears here.</p>'
            )
        return mark_safe(
            f'<a href="/admin/api/milaniemailvariant/{obj.pk}/preview/" target="_blank" '
            f'style="display:inline-block;padding:8px 20px;background:#1a7f5a;color:#fff;'
            f'border-radius:4px;font-size:13px;font-weight:600;text-decoration:none;">'
            f'&#128065;&nbsp; Open Full Preview &amp; Send</a>'
            f'<p style="margin-top:8px;font-size:12px;color:#888;">'
            f'Opens in a new tab with device preview, dark mode, and a send-test panel '
            f'(scroll below the email on desktop).</p>'
        )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            url_path(
                '<int:variant_id>/preview/',
                self.admin_site.admin_view(self.preview_view),
                name='milaniemailvariant_preview',
            ),
            url_path(
                '<int:variant_id>/send-test/',
                self.admin_site.admin_view(self.send_test_view),
                name='milaniemailvariant_send_test',
            ),
        ]
        return custom + urls

    def preview_view(self, request, variant_id):
        from django.conf import settings as django_settings
        import base64

        variant = get_object_or_404(MilaniEmailVariant, pk=variant_id)
        preselect_creator_id = (request.GET.get('creator_id') or '').strip()
        if preselect_creator_id and not preselect_creator_id.isdigit():
            preselect_creator_id = ''
        sample_name  = "Sarah"
        sample_greeting = "Hope you are having a great week so far!"
        subject_rendered = variant.subject.replace('{name}', sample_name)

        try:
            body_rendered = variant.body.format(name=sample_name, greeting=sample_greeting)
        except KeyError as e:
            body_rendered = f"[Template error: unknown placeholder {e}]\n\n{variant.body}"

        from_email = 'diana@milani-cosmetics.com'
        base_url   = getattr(django_settings, 'SHIELDCLIMB_CALLBACK_BASE_URL', 'https://api.ontracourier.us').rstrip('/')
        pixel_url  = f"{base_url}/api/webhooks/milani-open/?mid=PREVIEW_MODE"

        paragraphs      = body_rendered.strip().split('\n\n')
        html_paragraphs = []
        for para in paragraphs:
            lines = para.strip().split('\n')
            if len(lines) == 1:
                html_paragraphs.append(f'<p style="margin:0 0 16px 0;">{lines[0]}</p>')
            else:
                inner = '<br>'.join(lines)
                html_paragraphs.append(f'<p style="margin:0 0 16px 0;">{inner}</p>')
        body_html_inner = '\n    '.join(html_paragraphs)

        email_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="color-scheme" content="light dark">
  <meta name="supported-color-schemes" content="light dark">
  <style>
    :root {{ color-scheme: light dark; }}
    body {{
      font-family: 'Aptos', 'Segoe UI', Arial, sans-serif;
      font-size: 15px; line-height: 1.6; margin: 0; padding: 0;
      background: #ffffff; color: #000000;
    }}
    .email-container {{ max-width: 540px; margin: 36px auto; padding: 0 24px; }}
    .unsub-text {{ font-size: 11px; color: #999; margin-top: 24px;
                   border-top: 1px solid #e8e8e8; padding-top: 16px; }}
    .unsub-link {{ color: #999; text-decoration: underline; }}
    @media (prefers-color-scheme: dark) {{
      body, .email-container {{ background-color: #121212 !important; color: #e0e0e0 !important; }}
      .unsub-text {{ color: #777 !important; border-top-color: #333 !important; }}
      .unsub-link {{ color: #777 !important; }}
    }}
  </style>
</head>
<body>
  <div class="email-container">
    {body_html_inner}
    <p class="unsub-text">
      You are receiving this email because we identified you as a great fit for our
      upcoming campaigns. If you are not interested in brand partnerships at this time,
      you can <a href="mailto:{from_email}?subject=Unsubscribe" class="unsub-link">unsubscribe here</a>.
    </p>
    <img src="{pixel_url}" width="1" height="1" border="0"
         style="display:block;height:1px;width:1px;border:0;margin:0;padding:0;" alt="">
  </div>
</body>
</html>"""

        email_b64 = base64.b64encode(email_html.encode()).decode()
        updated_str = variant.updated_at.strftime('%b %d, %Y %H:%M UTC')
        active_str  = 'Active' if variant.is_active else 'Paused'

        preview_page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Preview: {variant.name}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
             background: #0f0f0f; color: #e0e0e0; min-height: 100vh; }}
    .topbar {{ position: sticky; top: 0; z-index: 100; background: #1a1a1a;
                border-bottom: 1px solid #333; padding: 12px 24px;
                display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }}
    .topbar h1 {{ font-size: 14px; font-weight: 600; color: #fff; flex: 1; min-width: 200px; }}
    .topbar h1 span {{ color: #4ade80; }}
    .subject-bar {{ background: #1e1e1e; border-bottom: 1px solid #2a2a2a;
                    padding: 10px 24px; font-size: 13px; color: #aaa; }}
    .subject-bar strong {{ color: #fff; }}
    .tab-group {{ display: flex; gap: 6px; }}
    .tab {{ padding: 6px 14px; border-radius: 6px; font-size: 12px; font-weight: 600;
             cursor: pointer; border: 1px solid #444; background: #2a2a2a; color: #aaa;
             transition: all .15s; user-select: none; }}
    .tab.active, .tab:hover {{ background: #1a7f5a; border-color: #1a7f5a; color: #fff; }}
    .mode-toggle {{ display: flex; align-items: center; gap: 8px; font-size: 12px; color: #aaa; }}
    .toggle-switch {{ position: relative; width: 40px; height: 22px; cursor: pointer; }}
    .toggle-switch input {{ display: none; }}
    .toggle-track {{ position: absolute; inset: 0; background: #444; border-radius: 11px; transition: background .2s; }}
    .toggle-switch input:checked + .toggle-track {{ background: #1a7f5a; }}
    .toggle-knob {{ position: absolute; top: 3px; left: 3px; width: 16px; height: 16px;
                    background: #fff; border-radius: 50%; transition: transform .2s; }}
    .toggle-switch input:checked ~ .toggle-knob {{ transform: translateX(18px); }}
    .stage {{ padding: 40px 24px; display: flex; justify-content: center;
               align-items: flex-start; min-height: calc(100vh - 130px); }}
    /* Desktop / Tablet frames */
    .device-wrap {{ display: flex; flex-direction: column; align-items: center; }}
    .device-frame {{ background: #fff; border-radius: 12px; overflow: hidden;
                      box-shadow: 0 0 0 2px #333, 0 20px 60px rgba(0,0,0,.6);
                      transition: width .3s ease; }}
    .device-frame.dark-mode {{ background: #121212; }}
    .device-frame.desktop {{ width: 860px; max-width: 100%; }}
    .device-frame.tablet  {{ width: 600px; max-width: 100%; }}
    .device-chrome {{ background: #f5f5f5; padding: 10px 16px; border-bottom: 1px solid #ddd;
                       display: flex; align-items: center; gap: 8px; font-size: 11px; color: #888; }}
    .device-frame.dark-mode .device-chrome {{ background: #1e1e1e; border-color: #333; color: #666; }}
    .dots span {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; }}
    .dots .r {{ background: #ff5f57; }} .dots .y {{ background: #ffbd2e; }} .dots .g {{ background: #28c840; }}
    .email-wrapper {{ overflow-y: auto; max-height: 680px; }}
    iframe {{ width: 100%; border: none; display: block; min-height: 400px; background: transparent; }}
    /* iPhone frame */
    .iphone-outer {{
      width: 393px; background: #1a1a1a; border-radius: 52px;
      box-shadow: 0 0 0 1px #444, 0 0 0 10px #222, 0 0 0 12px #555, 0 30px 80px rgba(0,0,0,.8);
      padding: 12px; position: relative;
    }}
    .iphone-screen {{
      background: #fff; border-radius: 42px; overflow: hidden;
      height: 720px; display: flex; flex-direction: column; position: relative;
    }}
    .iphone-screen.dark {{ background: #000; }}
    .iphone-status {{
      background: #fff; padding: 14px 28px 6px; display: flex;
      justify-content: space-between; align-items: center; flex-shrink: 0;
    }}
    .iphone-screen.dark .iphone-status {{ background: #000; color: #fff; }}
    .iphone-status .time {{ font-size: 15px; font-weight: 700; letter-spacing: -0.3px; }}
    .iphone-status .icons {{ font-size: 12px; display: flex; gap: 5px; align-items: center; }}
    .iphone-notch {{
      position: absolute; top: 0; left: 50%; transform: translateX(-50%);
      width: 126px; height: 34px; background: #1a1a1a; border-radius: 0 0 20px 20px;
      z-index: 10;
    }}
    .mail-navbar {{
      background: #f2f2f7; border-bottom: 1px solid #d1d1d6;
      padding: 8px 16px; display: flex; justify-content: space-between;
      align-items: center; flex-shrink: 0;
    }}
    .iphone-screen.dark .mail-navbar {{ background: #1c1c1e; border-color: #38383a; }}
    .mail-navbar .back {{ color: #007aff; font-size: 17px; display: flex; align-items: center; gap: 4px; }}
    .iphone-screen.dark .mail-navbar .back {{ color: #0a84ff; }}
    .mail-navbar .title {{ font-size: 17px; font-weight: 600; color: #000; }}
    .iphone-screen.dark .mail-navbar .title {{ color: #fff; }}
    .mail-navbar .icons {{ display: flex; gap: 16px; }}
    .mail-navbar .icons span {{ font-size: 20px; color: #007aff; cursor: pointer; }}
    .iphone-screen.dark .mail-navbar .icons span {{ color: #0a84ff; }}
    .mail-header {{
      padding: 12px 16px 10px; border-bottom: 1px solid #e5e5ea; flex-shrink: 0;
      background: #fff;
    }}
    .iphone-screen.dark .mail-header {{ background: #000; border-color: #38383a; }}
    .mail-subject-line {{
      font-size: 22px; font-weight: 700; color: #000; line-height: 1.2; margin-bottom: 10px;
    }}
    .iphone-screen.dark .mail-subject-line {{ color: #fff; }}
    .mail-sender-row {{ display: flex; align-items: center; gap: 10px; }}
    .mail-avatar {{
      width: 36px; height: 36px; border-radius: 50%;
      background: linear-gradient(135deg, #c0392b, #e74c3c);
      display: flex; align-items: center; justify-content: center;
      font-size: 14px; font-weight: 700; color: #fff; flex-shrink: 0;
    }}
    .mail-sender-info {{ flex: 1; min-width: 0; }}
    .mail-sender-name {{ font-size: 15px; font-weight: 600; color: #000; }}
    .iphone-screen.dark .mail-sender-name {{ color: #fff; }}
    .mail-sender-meta {{ font-size: 13px; color: #8e8e93; margin-top: 1px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .mail-time {{ font-size: 13px; color: #8e8e93; flex-shrink: 0; }}
    .mail-body-scroll {{ flex: 1; overflow-y: auto; background: #fff; }}
    .iphone-screen.dark .mail-body-scroll {{ background: #000; }}
    .mail-body-scroll iframe {{ width: 100%; border: none; min-height: 400px; }}
    .iphone-bottom-bar {{
      background: #f2f2f7; border-top: 1px solid #d1d1d6;
      padding: 10px 40px; display: flex; justify-content: space-between;
      align-items: center; flex-shrink: 0;
    }}
    .iphone-screen.dark .iphone-bottom-bar {{ background: #1c1c1e; border-color: #38383a; }}
    .iphone-bottom-bar span {{ font-size: 22px; color: #007aff; }}
    .iphone-screen.dark .iphone-bottom-bar span {{ color: #0a84ff; }}
    .home-indicator {{
      width: 134px; height: 5px; background: #1a1a1a; border-radius: 3px;
      margin: 8px auto 4px;
    }}
    /* Send panel */
    .send-panel {{
      background: #1e1e1e; border: 1px solid #333; border-radius: 10px;
      padding: 16px; margin-top: 20px; width: 100%; max-width: 500px;
    }}
    .send-panel h3 {{ font-size: 13px; font-weight: 600; color: #aaa;
                       text-transform: uppercase; letter-spacing: .5px; margin-bottom: 12px; }}
    .send-panel select, .send-panel input {{
      width: 100%; padding: 8px 12px; background: #2a2a2a; border: 1px solid #444;
      border-radius: 6px; color: #fff; font-size: 13px; margin-bottom: 10px;
    }}
    .send-panel button {{
      width: 100%; padding: 10px; background: #1a7f5a; color: #fff; border: none;
      border-radius: 6px; font-size: 13px; font-weight: 600; cursor: pointer;
    }}
    .send-panel button:hover {{ background: #156a4a; }}
    .send-result {{ margin-top: 8px; font-size: 12px; padding: 6px 10px;
                    border-radius: 4px; display: none; }}
    .send-result.ok  {{ background: #1a3a2a; color: #4ade80; display: block; }}
    .send-result.err {{ background: #3a1a1a; color: #f87171; display: block; }}
    .meta-strip {{ background: #1a1a1a; border-top: 1px solid #2a2a2a; padding: 12px 24px;
                    display: flex; gap: 24px; flex-wrap: wrap; font-size: 12px; color: #666; }}
    .meta-strip span strong {{ color: #aaa; }}
    @media (max-width: 920px) {{
      .device-frame.desktop, .device-frame.tablet {{ width: 100%; }}
    }}
  </style>
</head>
<body>
<div class="topbar">
  <h1>Email Preview &nbsp;/&nbsp; <span>{variant.name}</span></h1>
  <div class="tab-group">
    <div class="tab active" onclick="setDevice('desktop',this)">&#128513; Desktop</div>
    <div class="tab" onclick="setDevice('tablet',this)">&#128203; Tablet</div>
    <div class="tab" onclick="setDevice('mobile',this)">&#128241; iPhone</div>
  </div>
  <div class="mode-toggle">
    &#9728;
    <label class="toggle-switch">
      <input type="checkbox" id="modeToggle" onchange="toggleDark(this)">
      <div class="toggle-track"></div>
      <div class="toggle-knob"></div>
    </label>
    &#127769; Dark
  </div>
  <a href="/admin/api/milaniemailvariant/{variant.pk}/change/"
     style="padding:6px 14px;background:#333;color:#ccc;border-radius:6px;
            font-size:12px;text-decoration:none;border:1px solid #444;">
    &#9998; Edit Variant
  </a>
</div>

<div class="subject-bar" id="subjectBar">
  <strong>Subject:</strong>&nbsp; <span id="subjectDisplay">{subject_rendered}</span> &nbsp;&nbsp;
  <strong>From:</strong>&nbsp; <span id="fromDisplay">Diana Higuera &lt;{from_email}&gt;</span> &nbsp;&nbsp;
  <strong>Previewing as:</strong>&nbsp; <span id="nameDisplay">Sample (Sarah)</span>
</div>

<div class="stage" id="stage">

  <!-- Desktop / Tablet -->
  <div class="device-wrap" id="desktopWrap">
    <div class="device-frame desktop" id="deviceFrame">
      <div class="device-chrome">
        <div class="dots"><span class="r"></span><span class="y"></span><span class="g"></span></div>
        <span style="margin-left:8px;font-size:12px;">Email client — Desktop</span>
      </div>
      <div class="email-wrapper">
        <iframe id="emailFrame" title="Email Preview" sandbox="allow-same-origin"></iframe>
      </div>
    </div>
    <div class="send-panel" id="sendPanel">
      <h3>&#9993; Send Test Email</h3>
      <select id="creatorSelect" onchange="updatePreviewName(this)">
        <option value="">Select a creator to send to...</option>
      </select>
      <button onclick="sendTest()">Send this variant to selected creator</button>
      <div class="send-result" id="sendResult"></div>
    </div>
  </div>

  <!-- iPhone -->
  <div id="iphoneWrap" style="display:none;">
    <div class="iphone-outer">
      <div class="iphone-screen" id="iphoneScreen">
        <div class="iphone-notch"></div>
        <div class="iphone-status">
          <span class="time">9:41</span>
          <span class="icons">
            <svg width="17" height="12" viewBox="0 0 17 12" fill="currentColor">
              <rect x="0" y="3" width="3" height="9" rx="1"/><rect x="4.5" y="2" width="3" height="10" rx="1"/>
              <rect x="9" y="0" width="3" height="12" rx="1"/><rect x="13.5" y="0" width="3" height="12" rx="1"/>
            </svg>
            <svg width="16" height="12" viewBox="0 0 16 12" fill="currentColor">
              <path d="M8 2.4C5.8 2.4 3.8 3.3 2.4 4.8L1 3.4C2.8 1.3 5.3 0 8 0s5.2 1.3 7 3.4L13.6 4.8C12.2 3.3 10.2 2.4 8 2.4z"/>
              <path d="M8 5.6c-1.5 0-2.8.6-3.8 1.6L2.8 5.8C4.1 4.4 5.9 3.6 8 3.6s3.9.8 5.2 2.2L11.8 7.2C10.8 6.2 9.5 5.6 8 5.6z"/>
              <circle cx="8" cy="10" r="2"/>
            </svg>
            <svg width="25" height="12" viewBox="0 0 25 12" fill="currentColor">
              <rect x="0" y="1" width="21" height="10" rx="2.5" stroke="currentColor" stroke-width="1" fill="none" opacity=".35"/>
              <rect x="1.5" y="2.5" width="16" height="7" rx="1.5"/>
              <path d="M23 4.5v3a1.5 1.5 0 000-3z" opacity=".4"/>
            </svg>
          </span>
        </div>
        <div class="mail-navbar">
          <span class="back">&#8249; Inbox</span>
          <span class="title"></span>
          <div class="icons">
            <span title="Archive">&#128190;</span>
            <span title="Move">&#128193;</span>
            <span title="Delete">&#128465;</span>
          </div>
        </div>
        <div class="mail-header">
          <div class="mail-subject-line" id="iphoneSubject">{subject_rendered}</div>
          <div class="mail-sender-row">
            <div class="mail-avatar">D</div>
            <div class="mail-sender-info">
              <div class="mail-sender-name" id="iphoneFromName">Diana Higuera</div>
              <div class="mail-sender-meta" id="iphoneFromEmail">To: <span id="iphoneToName">Sarah</span></div>
            </div>
            <div class="mail-time">Now</div>
          </div>
        </div>
        <div class="mail-body-scroll">
          <iframe id="iphoneFrame" title="iPhone Email Preview" sandbox="allow-same-origin"
                  style="width:100%;border:none;min-height:360px;"></iframe>
        </div>
        <div class="iphone-bottom-bar">
          <span title="Archive">&#128190;</span>
          <span title="Reply">&#8629;</span>
          <span title="Flag">&#127988;</span>
          <span title="Move">&#128193;</span>
          <span title="Compose">&#9997;</span>
        </div>
      </div>
      <div class="home-indicator"></div>
    </div>
    <div class="send-panel" style="max-width:393px;margin-top:20px;">
      <h3>&#9993; Send Test Email</h3>
      <select id="creatorSelectMobile" onchange="updatePreviewName(this)">
        <option value="">Select a creator to send to...</option>
      </select>
      <button onclick="sendTestMobile()">Send this variant to selected creator</button>
      <div class="send-result" id="sendResultMobile"></div>
    </div>
  </div>

</div>

<div class="meta-strip">
  <span><strong>Variant:</strong> {variant.name}</span>
  <span><strong>Status:</strong> {active_str}</span>
  <span><strong>Last updated:</strong> {updated_str}</span>
  <span><strong>Placeholders:</strong> {{name}}, {{greeting}}</span>
  <span><strong>Note:</strong> Pixel is in preview mode — not recording opens</span>
</div>

<script>
const EMAIL_HTML_LIGHT = atob("{email_b64}");
const VARIANT_ID       = {variant.pk};
const PRESELECT_CREATOR_ID = '{preselect_creator_id}';
const CSRF_TOKEN       = getCookie('csrftoken');
let   currentCreatorId = null;
let   currentDevice    = 'desktop';
let   isDark           = false;

// Build email HTML with forced colour scheme
function buildEmailHtml(dark, name) {{
  let html = EMAIL_HTML_LIGHT;
  // Personalise name
  html = html.split('Sarah').join(name || 'Sarah');
  if (dark) {{
    // Force dark background inline so media query isn't needed
    html = html.replace(
      '<body',
      '<body style="background:#121212 !important;color:#e0e0e0 !important;"'
    );
  }} else {{
    // Force light background inline
    html = html.replace(
      '<body',
      '<body style="background:#ffffff !important;color:#000000 !important;"'
    );
  }}
  return html;
}}

function injectEmail(frameId, dark, name) {{
  const frame = document.getElementById(frameId);
  if (!frame) return;
  const doc = frame.contentDocument || frame.contentWindow.document;
  doc.open();
  doc.write(buildEmailHtml(dark, name));
  doc.close();
  setTimeout(() => {{
    try {{ frame.style.height = (doc.body.scrollHeight + 40) + 'px'; }} catch(e) {{}}
  }}, 200);
}}

function getCurrentName() {{
  const sel = document.getElementById('creatorSelect');
  const opt = sel && sel.selectedOptions[0];
  return opt && opt.dataset.name ? opt.dataset.name : 'Sarah';
}}

function setDevice(type, el) {{
  currentDevice = type;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  const dw = document.getElementById('desktopWrap');
  const iw = document.getElementById('iphoneWrap');
  if (type === 'mobile') {{
    dw.style.display = 'none'; iw.style.display = 'flex';
    iw.style.flexDirection = 'column'; iw.style.alignItems = 'center';
    const s = document.getElementById('iphoneScreen');
    s.className = 'iphone-screen' + (isDark ? ' dark' : '');
    injectEmail('iphoneFrame', isDark, getCurrentName());
  }} else {{
    dw.style.display = 'flex'; dw.style.flexDirection = 'column'; dw.style.alignItems = 'center';
    iw.style.display = 'none';
    const df = document.getElementById('deviceFrame');
    df.className = 'device-frame ' + type + (isDark ? ' dark-mode' : '');
    injectEmail('emailFrame', isDark, getCurrentName());
  }}
}}

function toggleDark(cb) {{
  isDark = cb.checked;
  const df = document.getElementById('deviceFrame');
  const is = document.getElementById('iphoneScreen');
  if (isDark) {{
    df.classList.add('dark-mode');
    is.classList.add('dark');
  }} else {{
    df.classList.remove('dark-mode');
    is.classList.remove('dark');
  }}
  const name = getCurrentName();
  injectEmail('emailFrame', isDark, name);
  injectEmail('iphoneFrame', isDark, name);
}}

function updatePreviewName(sel) {{
  const opt = sel.selectedOptions[0];
  if (!opt || !opt.value) return;
  currentCreatorId = opt.value;
  const name = opt.dataset.name || 'Sarah';
  const email = opt.dataset.email || '';
  // Sync both selects
  ['creatorSelect','creatorSelectMobile'].forEach(id => {{
    const s = document.getElementById(id);
    if (s) s.value = opt.value;
  }});
  // Update subject bar
  document.getElementById('nameDisplay').textContent = name + ' (' + email + ')';
  // Update iPhone header
  document.getElementById('iphoneToName').textContent = name;
  // Re-render email with real name
  injectEmail('emailFrame',   isDark, name);
  injectEmail('iphoneFrame',  isDark, name);
}}

function sendTest()       {{ doSend('creatorSelect',       'sendResult'); }}
function sendTestMobile() {{ doSend('creatorSelectMobile', 'sendResultMobile'); }}

function doSend(selectId, resultId) {{
  const sel = document.getElementById(selectId);
  const res = document.getElementById(resultId);
  if (!sel.value) {{
    res.className = 'send-result err';
    res.textContent = 'Please select a creator first.';
    return;
  }}
  res.className = 'send-result';
  res.textContent = 'Sending...';
  res.style.display = 'block';

  fetch('/admin/api/milaniemailvariant/' + VARIANT_ID + '/send-test/', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': CSRF_TOKEN }},
    body: 'creator_id=' + encodeURIComponent(sel.value),
  }})
  .then(r => r.json())
  .then(data => {{
    if (data.success) {{
      res.className = 'send-result ok';
      res.textContent = '✅ Sent to ' + data.email;
    }} else {{
      res.className = 'send-result err';
      res.textContent = '❌ ' + (data.error || 'Send failed');
    }}
  }})
  .catch(e => {{
    res.className = 'send-result err';
    res.textContent = '❌ Network error: ' + e;
  }});
}}

function getCookie(name) {{
  const v = document.cookie.match('(^|;) ?' + name + '=([^;]*)(;|$)');
  return v ? v[2] : '';
}}

// Load creators for selects
fetch('/admin/api/milaniemailvariant/{variant.pk}/send-test/')
  .then(r => {{
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }})
  .then(creators => {{
    ['creatorSelect','creatorSelectMobile'].forEach(selId => {{
      const sel = document.getElementById(selId);
      creators.forEach(c => {{
        const opt = document.createElement('option');
        opt.value       = c.id;
        opt.dataset.name  = c.name;
        opt.dataset.email = c.email;
        opt.textContent = c.name + ' — ' + c.email;
        sel.appendChild(opt);
      }});
    }});
    if (PRESELECT_CREATOR_ID) {{
      const sel = document.getElementById('creatorSelect');
      if (sel) {{
        sel.value = PRESELECT_CREATOR_ID;
        updatePreviewName(sel);
      }}
    }}
  }})
  .catch(e => {{
    console.error('Failed to load creators:', e);
    ['creatorSelect','creatorSelectMobile'].forEach(selId => {{
      const sel = document.getElementById(selId);
      if (sel) sel.innerHTML = '<option value="">Could not load creators</option>';
    }});
  }});

// Init
window.addEventListener('load', () => {{
  injectEmail('emailFrame',  false, 'Sarah');
  injectEmail('iphoneFrame', false, 'Sarah');
}});
</script>
</body>
</html>"""

        return HttpResponse(preview_page, content_type='text/html')


    def send_test_view(self, request, variant_id):
        """
        GET  — returns JSON list of all creators for the preview dropdown.
        POST — sends this specific variant to the selected creator.
        """
        import json as _json
        from .models import Creator as CreatorModel
        from api.milani_email_service import send_specific_milani_variant, _get_provider_config

        variant = get_object_or_404(MilaniEmailVariant, pk=variant_id)

        if request.method == 'GET':
            creators = list(
                CreatorModel.objects.all().order_by('name')
                .values('id', 'name', 'email')[:200]
            )
            return HttpResponse(
                _json.dumps(creators),
                content_type='application/json'
            )

        if request.method == 'POST':
            creator_id = request.POST.get('creator_id')
            if not creator_id:
                return HttpResponse(
                    _json.dumps({'success': False, 'error': 'No creator selected.'}),
                    content_type='application/json'
                )
            try:
                creator = CreatorModel.objects.get(pk=creator_id)
                config = _get_provider_config()
                api_key = getattr(settings, config['api_key_setting'], '')
                if not api_key:
                    return HttpResponse(
                        _json.dumps({
                            'success': False,
                            'error': (
                                f"Resend API key not set ({config['api_key_setting']}). "
                                f"Check Site Settings provider and your .env."
                            ),
                        }),
                        content_type='application/json'
                    )
                ok = send_specific_milani_variant(creator, variant.subject, variant.body)
                if ok:
                    return HttpResponse(
                        _json.dumps({'success': True, 'email': creator.email}),
                        content_type='application/json'
                    )
                else:
                    return HttpResponse(
                        _json.dumps({
                            'success': False,
                            'error': (
                                'Send failed — check app logs. '
                                'Common causes: invalid template placeholders, Resend rejection.'
                            ),
                        }),
                        content_type='application/json'
                    )
            except CreatorModel.DoesNotExist:
                return HttpResponse(
                    _json.dumps({'success': False, 'error': 'Creator not found.'}),
                    content_type='application/json'
                )
            except Exception as e:
                return HttpResponse(
                    _json.dumps({'success': False, 'error': str(e)}),
                    content_type='application/json'
                )

        return HttpResponse(status=405)


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'email_provider', 'milani_smtp_provider', 'ai_provider')
    fieldsets = (
        ('OnTrac Transactional Email', {
            'description': 'Controls which provider sends shipment notification emails (MailerSend / Resend / SendGrid).',
            'fields': ('email_provider',),
        }),
        ('Milani Outreach Provider', {
            'description': (
                'Switch between Resend accounts for Milani outreach. '
                'resend_cosmetics sends from diana@milani-cosmetics.com. '
                'resend_collabs sends from diana@milanicollabs.com. '
                'Change takes effect on the next send — no restart needed.'
            ),
            'fields': ('milani_smtp_provider',),
        }),
        ('AI Engine', {
            'description': 'Controls which AI provider generates shipment data in the admin form.',
            'fields': ('ai_provider',),
        }),
    )

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False