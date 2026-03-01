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
    paymentDescription = models.CharField(max_length=100, default='Shipping Fee', blank=True, help_text="What is this payment for? (e.g., Customs Fee)")
    requiresPayment = models.BooleanField(default=False)
    progressLabels = models.JSONField(default=default_progress_labels)
    recentEvent = models.JSONField(default=default_recent_event)
    allEvents = models.JSONField(default=default_all_events)
    shipmentDetails = models.JSONField(default=default_shipment_details)

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
        if not self.receipt_number:
            self.receipt_number = f"RCP-{self.shipment.trackingId}-{timezone.now().strftime('%Y%m%d')}"
        super().save(*args, **kwargs)

class Creator(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField(max_length=255, unique=True)
    country = models.CharField(max_length=100, blank=True, null=True)
    portfolio_link = models.URLField(max_length=2000, blank=True, null=True)
    status = models.CharField(max_length=50, default='New Lead', help_text="Current status in the funnel (e.g., New Lead, Sent, Replied, Passed).")
    last_outreach = models.DateTimeField(null=True, blank=True)
    class Meta:
        ordering = ['name']
    def __str__(self):
        return f"{self.name} ({self.email})"

class MilaniOutreachLog(models.Model):
    creator = models.ForeignKey(Creator, related_name='outreach_history', on_delete=models.CASCADE)
    subject = models.CharField(max_length=255, default='Milani Cosmetics Partnership Opportunity')
    status = models.CharField(max_length=50, help_text="e.g., Sent, Delivered, Opened, Clicked, Dropped, Bounced")
    event_time = models.DateTimeField(auto_now_add=True)
    sendgrid_message_id = models.CharField(max_length=255, unique=True, blank=True, null=True, help_text="Unique ID from SendGrid")
    class Meta:
        ordering = ['-event_time']
        verbose_name_plural = "Milani Outreach Logs"
    def __str__(self):
        return f"{self.status} - {self.creator.name}"

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

    @classmethod
    def get_active_provider(cls):
        """Returns the currently active email provider string."""
        settings_obj, _ = cls.objects.get_or_create(pk=1)
        return settings_obj.email_provider

    @classmethod
    def get_ai_provider(cls):
        """Returns the currently active AI provider setting."""
        settings_obj, _ = cls.objects.get_or_create(pk=1)
        return settings_obj.ai_provider