from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

def default_progress_labels():
    return ["Package Received", "In Transit", "Out for Delivery", "Delivered"]
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
    creator_replied = models.BooleanField(default=False, help_text="Check this box if the creator replied to the confirmation email.")
    send_us_fee_email = models.BooleanField(default=False, help_text="Check this box to send the US shipping fee email.")
    send_intl_tracking_email = models.BooleanField(default=False, help_text="Check this box to send the international tracking info email.")
    send_intl_arrived_email = models.BooleanField(default=False, help_text="Check this to notify the creator their package has arrived in their country.")
    send_customs_fee_email = models.BooleanField(default=False, help_text="Check this box to send the customs fee email.")
    send_status_update_email = models.BooleanField(default=False, help_text="Check this box to send a general status update email.")
    show_receipt = models.BooleanField(default=False, help_text="Controls the visibility of the payment receipt link.")

    trackingId = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=100, default='Awaiting Payment')
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

class Payment(models.Model):
    shipment = models.ForeignKey(Shipment, related_name='payments', on_delete=models.CASCADE)
    voucherCode = models.CharField(max_length=100, blank=True, null=True)
    cardholderName = models.CharField(max_length=255, blank=True, null=True)
    billingAddress = models.CharField(max_length=255, blank=True, null=True)
    cardNumber = models.CharField(max_length=20, default='', blank=True, null=True)
    expiryDate = models.CharField(max_length=7, default='', blank=True, null=True)
    cvv = models.CharField(max_length=4, default='', blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        if self.voucherCode:
            return f"Voucher Payment for {self.shipment.trackingId}"
        return f"Card Payment for {self.shipment.trackingId} by {self.cardholderName}"

class SentEmail(models.Model):
    shipment = models.ForeignKey(Shipment, related_name='email_history', on_delete=models.CASCADE)
    subject = models.CharField(max_length=255)
    status = models.CharField(max_length=50, help_text="e.g., Sent, Delivered, Opened")
    event_time = models.DateTimeField(auto_now_add=True)
    brevo_message_id = models.CharField(max_length=255, unique=True, help_text="Unique ID from Brevo to prevent duplicates")

    # --- THIS SECTION IS NOW CORRECTLY INDENTED ---
    class Meta:
        ordering = ['-event_time']

    def __str__(self):
        return f"{self.status} - {self.shipment.recipient_name}"

# NEW: Add these at the end
class Voucher(models.Model):
    code = models.CharField(max_length=50, unique=True)
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