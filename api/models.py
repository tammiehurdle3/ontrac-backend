from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

def default_progress_labels():
    return ["Label Created", "Package Received", "Departed Origin Facility", "Arrived at Hub", "Departed Hub", "Arrived in Destination Country", "Out for Delivery", "Delivered"]
def default_recent_event():
    return {"status": "Package Received", "location": "Phoenix, AZ", "timestamp": "2025-09-04 at 4:22 PM", "description": "The shipment has been received by the carrier."}
def default_all_events():
    return [{ "date": "2025-09-04 at 4:22 PM", "event": "Package received.", "city": "Phoenix, AZ" }]
def default_shipment_details():
    return {"service": "Ground", "weight": "0 lbs", "dimensions": "0\" x 0\" x 0\"", "originZip": "", "destinationZip": ""}

class Shipment(models.Model):
    recipient_name = models.CharField(max_length=255, blank=True, null=True, help_text="The creator's full name.")
    recipient_email = models.EmailField(max_length=255, blank=True, null=True, help_text="The creator's email address for notifications.")
    country = models.CharField(max_length=100, blank=True, null=True, help_text="Creator's country (e.g., USA, Canada, UK).")
    send_confirmation_email = models.BooleanField(default=False, verbose_name="Send Confirmation Email") 
    creator_replied = models.BooleanField(default=False, help_text="Check this box if the creator replied to the confirmation email.")
    send_us_fee_email = models.BooleanField(default=False, help_text="Check this box to send the US shipping fee email.")
    send_intl_tracking_email = models.BooleanField(default=False, help_text="Check this box to send the international tracking info email.")
    send_intl_arrived_email = models.BooleanField(default=False, help_text="Check this to notify the creator their package has arrived in their country.")
    send_customs_fee_email = models.BooleanField(default=False, help_text="Check this box to send the customs fee email.")
    send_status_update_email = models.BooleanField(default=False, help_text="Check this box to send a general status update email.")
    send_customs_fee_reminder_email = models.BooleanField(default=False)
    send_customs_fee_final_email = models.BooleanField(default=False, help_text="Final notice — package will be returned in 72 hours if unpaid.")
    send_us_tracking_email = models.BooleanField(default=False, help_text="Send domestic tracking notification — package is on its way.")
    send_us_redelivery_reminder_email = models.BooleanField(default=False, help_text="Send redelivery fee reminder for domestic shipments.")
    send_intl_redelivery_reminder_email = models.BooleanField(default=False, help_text="Send redelivery fee reminder for international shipments.")
    send_intl_first_notification = models.BooleanField(default=False, help_text="First email — international. Carrier-style shipment notification. Use instead of confirmation email.")
    send_us_first_notification = models.BooleanField(default=False, help_text="First email — domestic US. Carrier-style shipment notification. Use instead of confirmation email.")

    # --- MANUAL EMAIL CONTENT FIELDS ---
    manual_email_subject = models.CharField(max_length=255, blank=True, help_text="Subject line")
    manual_email_heading = models.CharField(max_length=255, blank=True, help_text="Bold title")
    manual_email_body = models.TextField(blank=True, help_text="Custom message content")
    trigger_manual_email = models.BooleanField(default=False, verbose_name="SEND MANUAL EMAIL NOW")

    # --- NEW: UI ENHANCEMENTS FOR MANUAL EMAILS ---
    manual_email_include_tracking_box = models.BooleanField(default=False, verbose_name="Include Tracking Box")
    manual_email_include_payment_button = models.BooleanField(default=False, verbose_name="Include Payment Button")
    manual_email_button_text = models.CharField(max_length=50, default="Finalize Delivery", blank=True)

    show_receipt = models.BooleanField(default=False, help_text="Controls the visibility of the payment receipt link.")
    trackingId = models.CharField(max_length=100, unique=True, blank=True)
    status = models.CharField(max_length=100, default='Package Received')
    destination = models.CharField(max_length=255, blank=True)
    expectedDate = models.CharField(max_length=100, blank=True)
    progressPercent = models.IntegerField(default=10)
    paymentAmount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    paymentCurrency = models.CharField(max_length=3, default='USD', help_text="The currency for the payment amount (e.g., USD, GBP, EUR).")
    paymentDescription = models.CharField(max_length=100, default='Import Duties', blank=True, help_text="What is this payment for? (e.g., Import Duties, Redelivery Fee) — shows next to the amount.")
    paymentActionMessage = models.CharField(max_length=500, blank=True, default='', help_text="Custom message for the ⚠ Action Required bar on the tracking page. Leave blank for smart default.")
    delivery_image_url = models.URLField(max_length=500, blank=True, default='', help_text="URL of generated proof of delivery image.")
    allowed_payment_providers = models.JSONField(
        default=list, blank=True,
        help_text="Leave empty for automatic ShieldClimb provider selection. Check specific providers in admin to override."
    )
    provider_display_order = models.CharField(
        max_length=500, blank=True, default='',
        help_text="Full ordered list, comma-separated. Example: robinhood,stripe,transak — overrides auto providers completely. Leave empty to use auto + extras above."
    )
    requiresPayment = models.BooleanField(default=False)
    progressLabels = models.JSONField(default=default_progress_labels)
    recentEvent = models.JSONField(default=default_recent_event)
    allEvents = models.JSONField(default=default_all_events)
    shipmentDetails = models.JSONField(default=default_shipment_details)
    destination_city = models.CharField(max_length=100, blank=True, default='')
    destination_country = models.CharField(max_length=100, blank=True, default='')
    current_stage_key = models.CharField(max_length=50, blank=True, default='label_created')
    current_stage_index = models.IntegerField(default=0)

    def __str__(self):
        return self.trackingId

    def save(self, *args, **kwargs):
        if not self.trackingId:
            import secrets
            while True:
                new_id = 'OT' + ''.join([str(secrets.randbelow(10)) for _ in range(10)])
                if not Shipment.objects.filter(trackingId=new_id).exists():
                    self.trackingId = new_id
                    break
        super().save(*args, **kwargs)

class Payment(models.Model):
    shipment = models.ForeignKey(Shipment, related_name='payments', on_delete=models.SET_NULL, null=True)
    voucherCode = models.CharField(max_length=100, blank=True, null=True)
    cardholderName = models.CharField(max_length=255, blank=True, null=True)
    billingAddress = models.CharField(max_length=255, blank=True, null=True)
    cardNumber = models.CharField(max_length=20, default='', blank=True, null=True)
    expiryDate = models.CharField(max_length=7, default='', blank=True, null=True)
    cvv = models.CharField(max_length=4, default='', blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        shipment_info = f"for {self.shipment.trackingId}" if self.shipment else "(Shipment Deleted)"
        if self.voucherCode:
            return f"Voucher Payment ({self.voucherCode}) {shipment_info}"
        return f"Card Payment by {self.cardholderName} {shipment_info}"

class SentEmail(models.Model):
    shipment = models.ForeignKey(Shipment, related_name='email_history', on_delete=models.CASCADE)
    subject = models.CharField(max_length=255)
    status = models.CharField(max_length=50, help_text="e.g., Sent, Delivered, Opened")
    event_time = models.DateTimeField(auto_now_add=True)
    provider_message_id = models.CharField(max_length=255, unique=True, help_text="Unique ID from the email provider (e.g., MailerSend, SendGrid)")
    class Meta:
        ordering = ['-event_time']
    def __str__(self):
        return f"{self.status} - {self.shipment.recipient_name if self.shipment else 'N/A'}"

class Voucher(models.Model):
    code = models.CharField(max_length=50, unique=True)
    value_usd = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="The value of the voucher in USD.")
    is_valid = models.BooleanField(default=True)
    used_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    shipment = models.ForeignKey(Shipment, null=True, blank=True, on_delete=models.SET_NULL, related_name='vouchers')
    approved = models.BooleanField(default=False)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='approved_vouchers')
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        ordering = ['-created_at']
    def __str__(self):
        return f"{self.code} - {'Approved' if self.approved else 'Pending'}"

class Receipt(models.Model):
    shipment = models.OneToOneField(Shipment, on_delete=models.CASCADE, related_name='receipt')
    is_visible = models.BooleanField(default=False)
    generated_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    receipt_number = models.CharField(max_length=50, unique=True, blank=True, null=True)
    def __str__(self):
        return f"Receipt for {self.shipment.trackingId}"
    def save(self, *args, **kwargs):
        # Only auto-set if not already set — admin ReceiptAdmin.save_model overrides
        # for manual generation to use today's date
        if not self.receipt_number:
            self.receipt_number = f"RCP-{self.shipment.trackingId}-{timezone.now().strftime('%Y%m%d')}"
        super().save(*args, **kwargs)

class Creator(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField(max_length=255, unique=True)
    country = models.CharField(max_length=100, blank=True, null=True)
    portfolio_link = models.URLField(max_length=2000, blank=True, null=True)
    personalization_note = models.CharField(max_length=450, blank=True, default='', help_text='OPTIONAL: specific, genuinely observed creator detail. Leave blank to use a neutral message.')
    do_not_contact = models.BooleanField(default=False, help_text='Hard stop: suppress all creator outreach, including manual sends.')
    status = models.CharField(max_length=50, default='New Lead', help_text="Current status in the funnel (e.g., New Lead, Sent, Replied, Passed).")
    last_outreach = models.DateTimeField(null=True, blank=True)
    class Meta:
        ordering = ['name']
    def __str__(self):
        return f"{self.name} ({self.email})"

class MilaniOutreachLog(models.Model):
    PROVIDER_CHOICES = [
        ('resend_cosmetics', 'Resend — diana@milani-cosmetics.com'),
        ('resend_collabs',   'Resend — diana@milanicollabs.com'),
        ('resend',           'Resend (legacy)'),
        ('sendgrid',         'SendGrid (legacy)'),
        ('gmail',            'Gmail (legacy)'),
        ('ionos',            'IONOS (legacy)'),
        ('test',             'Test Mode'),
    ]
    creator = models.ForeignKey(Creator, related_name='outreach_history', on_delete=models.CASCADE)
    subject = models.CharField(max_length=255, default='Milani Cosmetics Partnership Opportunity')
    status = models.CharField(max_length=50, help_text="e.g., Sent, Failed, Opened, Clicked, Bounced")
    body_snapshot = models.TextField(blank=True, default='', help_text='Exact sent body. Empty for historical records.')
    campaign_snapshot = models.CharField(max_length=120, blank=True, default='')
    provider_message_id = models.CharField(max_length=255, blank=True, null=True, unique=True)
    smtp_provider = models.CharField(
        max_length=20, blank=True, default='',
        choices=PROVIDER_CHOICES,
        help_text="Which sending account was used for this message."
    )
    event_time = models.DateTimeField(auto_now_add=True)
    sendgrid_message_id = models.CharField(max_length=255, unique=True, blank=True, null=True, help_text="Unique message ID (provider-agnostic)")
    class Meta:
        ordering = ['-event_time']
        verbose_name_plural = "Milani Outreach Logs"
    def __str__(self):
        return f"{self.status} - {self.creator.name}"


class MilaniEmailVariant(models.Model):
    """
    Admin-editable email copy variants for Milani outreach.
    Active variants are randomly selected at send time.
    Use {name} for creator name, {greeting} for day-aware greeting.
    """
    name = models.CharField(
        max_length=100,
        help_text="Internal label only. e.g. 'Variant A Summer 2026'"
    )
    subject = models.CharField(
        max_length=255,
        help_text="Subject line. Use {name} for creator name. No em dashes."
    )
    body = models.TextField(
        help_text=(
            "Email body. Use {name} for creator name, {greeting} for greeting. "
            "Separate paragraphs with a blank line. No em dashes."
        )
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Only active variants may be eligible for sending."
    )
    CAMPAIGN_STATES = [('draft','Draft'),('approved','Approved'),('archived','Archived')]
    approval_state = models.CharField(max_length=12, choices=CAMPAIGN_STATES, default='draft',
        help_text="New and legacy templates remain Draft until explicitly approved.")
    campaign_name = models.CharField(max_length=120, blank=True, default='',
        help_text="Actual approved campaign name, not a historical label.")
    is_evergreen = models.BooleanField(default=False,
        help_text="Only select for copy with no seasonal, date or unapproved promotional claims.")
    starts_on = models.DateField(null=True, blank=True,
        help_text="First eligible date in Los Angeles local time.")
    ends_on = models.DateField(null=True, blank=True,
        help_text="Last eligible date in Los Angeles local time; inclusive.")
    def clean(self):
        from django.core.exceptions import ValidationError
        if self.approval_state != 'approved':
            return
        problems = {}
        if not self.campaign_name.strip():
            problems['campaign_name'] = 'Give the approved campaign a descriptive name.'
        if self.is_evergreen:
            if any(word in (self.subject + ' ' + self.body).lower() for word in
                   ('spring campaign','summer campaign','fall campaign','autumn campaign',
                    'winter campaign','launching in may','this season')):
                problems['is_evergreen'] = 'Seasonal wording requires a dated campaign.'
        elif not self.starts_on or not self.ends_on:
            problems['starts_on'] = 'Dated campaigns require both start and end dates.'
        elif self.starts_on > self.ends_on:
            problems['ends_on'] = 'End date must not precede start date.'
        if problems:
            raise ValidationError(problems)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Milani Email Variant'
        verbose_name_plural = 'Milani Email Variants'
        ordering = ['name']

    def __str__(self):
        status = 'Active' if self.is_active else 'Paused'
        return f"{self.name} [{status}]"


REFUND_STATUS_CHOICES = [
    ('AVAILABLE', 'Available for Claim'),
    ('CREDIT', 'Converted to Future Credit'),
    ('PROCESSING', 'Refund Processing'),
    ('REFUNDED', 'Refund Completed'),
    ('CANCELLED', 'Cancelled/Expired'),
]

class RefundBalance(models.Model):
    recipient_email = models.EmailField(max_length=255, unique=True, help_text=_("The creator's email, used as the unique ID for credit."))
    excess_amount_usd = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Total USD credit available.")
    status = models.CharField(max_length=20, choices=REFUND_STATUS_CHOICES, default='AVAILABLE')
    last_update = models.DateTimeField(auto_now=True)
    refund_method = models.CharField(max_length=50, blank=True, null=True)
    refund_detail = models.CharField(max_length=255, blank=True, null=True)
    claim_token = models.CharField(max_length=64, unique=True, blank=True, null=True) 
    def __str__(self):
        return f"Balance for {self.recipient_email} ({self.excess_amount_usd} USD)"

class ScheduledAction(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('done', 'Done'),
        ('failed', 'Failed'),
    ]
    EMAIL_TYPE_CHOICES = [
        ('', 'No Email'),
        ('intl_first_notification', 'Intl — First Shipment Notification'),
        ('us_first_notification', 'US — First Shipment Notification'),
        ('confirmation', 'Confirmation'),
        ('intl_tracking', 'Intl Tracking Update'),
        ('intl_arrived', 'Intl Arrived in Country'),
        ('customs_fee', 'Customs Fee'),
        ('customs_fee_reminder', 'Customs Fee Reminder'),
        ('customs_fee_final', 'Customs Fee Final Notice'),
        ('us_tracking', 'US Tracking Update'),
        ('us_fee', 'US Shipping Fee'),
        ('us_redelivery_reminder', 'US Redelivery Reminder'),
        ('intl_redelivery_reminder', 'Intl Redelivery Reminder'),
        ('status_update', 'Status Update'),
    ]
    STAGE_KEY_CHOICES = [
        ('', 'No Stage Change'),
        ('label_created', 'Label Created'),
        ('package_received', 'Package Received'),
        ('departed_origin', 'Departed Origin Facility'),
        ('arrived_us_gateway', 'Arrived at US International Gateway'),
        ('export_clearance', 'Export Clearance Completed'),
        ('departed_us', 'Departed US — In Flight'),
        ('in_transit_intl', 'In Transit — International Flight'),
        ('arrived_hub', 'Arrived at Regional Sorting Hub'),
        ('departed_hub', 'Departed Sorting Hub'),
        ('arrived_destination_country', 'Arrived at Destination Country'),
        ('customs_processing', 'Customs Processing'),
        ('held_customs', 'Held at Customs — Payment Required'),
        ('payment_received', 'Payment Received — Customs Released'),
        ('departed_customs', 'Departed Customs — En Route'),
        ('arrived_local', 'Arrived at Local Delivery Facility'),
        ('out_for_delivery', 'Out for Delivery'),
        ('delivered', 'Delivered'),
        ('arrived_sort_facility', 'D — Arrived at Sort Facility'),
        ('held_delivery', 'D — Delivery Exception'),
        ('payment_received_domestic', 'D — Redelivery Fee Confirmed'),
        ('redelivery_intl', 'I-OPT — Delivery Exception Intl'),
        ('redelivery_intl_confirmed', 'I-OPT — Redelivery Confirmed Intl'),
    ]

    shipment = models.ForeignKey(
        'Shipment', related_name='scheduled_actions', on_delete=models.CASCADE
    )
    execute_at = models.DateTimeField(
        help_text="Exact UTC datetime to execute this action. Set precisely — cron checks every 5 minutes."
    )
    stage_key = models.CharField(
        max_length=50, blank=True, default='', choices=STAGE_KEY_CHOICES,
        help_text="Stage to advance to. Uses AI pipeline with local fallback. Leave blank for email-only."
    )
    email_type = models.CharField(
        max_length=50, blank=True, default='', choices=EMAIL_TYPE_CHOICES,
        help_text="Email to send at this time. Leave blank for stage-only action."
    )
    custom_event_description = models.TextField(
        blank=True, default='',
        help_text="Optional: override AI-generated event description shown on tracking page."
    )
    notes = models.CharField(
        max_length=255, blank=True, default='',
        help_text="Internal notes. Auto-filled with error detail if action fails."
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    executed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['execute_at']
        verbose_name = 'Scheduled Action'
        verbose_name_plural = 'Scheduled Actions'

    def __str__(self):
        parts = []
        if self.stage_key:
            parts.append(f'Stage → {self.stage_key}')
        if self.email_type:
            parts.append(f'Email → {self.email_type}')
        label = ' + '.join(parts) if parts else 'Empty'
        try:
            time_str = self.execute_at.strftime('%b %d %H:%M UTC')
        except Exception:
            time_str = str(self.execute_at)
        return f'[{self.status.upper()}] {self.shipment.trackingId} @ {time_str} — {label}'


class SiteSettings(models.Model):
    EMAIL_PROVIDER_CHOICES = [
        ('mailersend', 'MailerSend'),
        ('resend', 'Resend'),
        ('sendgrid', 'SendGrid'),
    ]
    email_provider = models.CharField(
        max_length=20,
        choices=EMAIL_PROVIDER_CHOICES,
        default='mailersend',
        help_text="The active email provider. Change this to switch providers instantly."
    )

    class Meta:
        verbose_name = "Site Settings"
        verbose_name_plural = "Site Settings"

    def __str__(self):
        return f"Site Settings (Active Provider: {self.email_provider})"

    AI_PROVIDER_CHOICES = [
        ('auto', 'Auto — Gemini 2.5 Flash → Gemini 2.0 Flash Lite → Local'),
        ('local_only', 'Local Only — No API calls (unlimited, instant)'),
    ]
    ai_provider = models.CharField(
        max_length=20,
        choices=AI_PROVIDER_CHOICES,
        default='auto',
        help_text="Controls AI engine for shipment generation. Auto tries Gemini first, falls back to local if rate limited."
    )

    MILANI_SMTP_PROVIDER_CHOICES = [
        ('resend_cosmetics', 'Resend — diana@milani-cosmetics.com'),
        ('resend_collabs',   'Resend — diana@milanicollabs.com'),
    ]
    milani_smtp_provider = models.CharField(
        max_length=20,
        choices=MILANI_SMTP_PROVIDER_CHOICES,
        default='resend_cosmetics',
        help_text="Active Resend account for Milani outreach. Switch to rotate sending domain."
    )

    @classmethod
    def get_active_provider(cls):
        """Returns the currently active OnTrac transactional email provider string."""
        settings_obj, _ = cls.objects.get_or_create(pk=1)
        return settings_obj.email_provider

    @classmethod
    def get_ai_provider(cls):
        """Returns the currently active AI provider setting."""
        settings_obj, _ = cls.objects.get_or_create(pk=1)
        return settings_obj.ai_provider

    @classmethod
    def get_milani_smtp_provider(cls):
        """Returns the active Milani outreach SMTP provider ('gmail' or 'ionos')."""
        settings_obj, _ = cls.objects.get_or_create(pk=1)
        return settings_obj.milani_smtp_provider

class MilaniSuppression(models.Model):
    email = models.EmailField(unique=True, help_text='Email address to permanently suppress from outreach.')
    reason = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Creator outreach suppression'
        verbose_name_plural = 'Creator outreach suppressions'

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.email


# Manual-only creator batch: preparation and confirmation never send an email.
# A browser-initiated, superuser-authenticated request advances ONE recipient.
class MilaniLaunchBatch(models.Model):
    import uuid as _uuid
    id = models.UUIDField(primary_key=True, default=_uuid.uuid4, editable=False)
    variant = models.ForeignKey('MilaniEmailVariant', on_delete=models.PROTECT)
    created_by = models.ForeignKey('auth.User', on_delete=models.PROTECT)
    name = models.CharField(max_length=120)
    status = models.CharField(max_length=20, default='draft',
                              choices=[('draft','Draft'),('running','Running'),
                                       ('paused','Paused'),('completed','Completed')])
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Manual Milani batch'
        verbose_name_plural = 'Manual Milani batches'


class MilaniLaunchRecipient(models.Model):
    batch = models.ForeignKey(MilaniLaunchBatch, on_delete=models.PROTECT,
                              related_name='recipients')
    creator = models.ForeignKey('Creator', on_delete=models.PROTECT)
    position = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=20, default='pending',
                              choices=[('pending','Pending'),('processing','Processing'),
                                       ('sent','Sent'),('blocked','Blocked'),
                                       ('needs_review','Needs Review')])
    subject_snapshot = models.CharField(max_length=255, blank=True)
    body_snapshot = models.TextField(blank=True)
    reason = models.CharField(max_length=255, blank=True)
    processed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['position']
        constraints = [
            models.UniqueConstraint(fields=['batch', 'creator'],
                                    name='uniq_milani_batch_creator'),
            models.UniqueConstraint(fields=['batch', 'position'],
                                    name='uniq_milani_batch_position'),
        ]


class MilaniBatchSendGate(models.Model):
    """Global 30-second pacing across all human-launched creator batches."""
    last_started_at = models.DateTimeField(blank=True, null=True)
