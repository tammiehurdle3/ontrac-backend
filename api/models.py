# Final version
# api/models.py
from django.db import models

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
    # Checkboxes for manual email triggers
    send_us_fee_email = models.BooleanField(default=False, help_text="Check this box to send the US shipping fee email.")
    send_intl_tracking_email = models.BooleanField(default=False, help_text="Check this box to send the international tracking info email.")
    send_customs_fee_email = models.BooleanField(default=False, help_text="Check this box to send the customs fee email.")

    trackingId = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=100, default='Awaiting Payment')
    destination = models.CharField(max_length=255, blank=True)
    expectedDate = models.CharField(max_length=100, blank=True)
    progressPercent = models.IntegerField(default=10)
    paymentAmount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    paymentCurrency = models.CharField(max_length=3, default='USD', help_text="The currency for the payment amount (e.g., USD, GBP, EUR).")
    paymentDescription = models.CharField(max_length=100, default='Shipping Fee', blank=True, help_text="What is this payment for? (e.g., Customs Fee)")
    requiresPayment = models.BooleanField(default=True)
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
