# api/models.py
from django.db import models

# --- Default JSON templates (no changes here) ---
def default_progress_labels():
    return ["Package Received", "In Transit", "Out for Delivery", "Delivered"]

def default_recent_event():
    return {
        "status": "Package Received",
        "location": "Phoenix, AZ",
        "timestamp": "2025-08-15 at 4:22 PM",
        "description": "The shipment has been received by the carrier."
    }

def default_all_events():
    return [
        { "date": "2025-08-15 at 4:22 PM", "event": "Package received.", "city": "Phoenix, AZ" }
    ]

def default_shipment_details():
    return {
        "service": "Ground",
        "weight": "0 lbs",
        "dimensions": "0\" x 0\" x 0\"",
        "originZip": "",
        "destinationZip": ""
    }
# -----------------------------------------------------------


class Shipment(models.Model):
    # No changes needed in the Shipment model
    trackingId = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=100, default='Awaiting Payment')
    destination = models.CharField(max_length=255, blank=True)
    expectedDate = models.CharField(max_length=100, blank=True)
    progressPercent = models.IntegerField(default=10)
    paymentAmount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    requiresPayment = models.BooleanField(default=True)
    progressLabels = models.JSONField(default=default_progress_labels)
    recentEvent = models.JSONField(default=default_recent_event)
    allEvents = models.JSONField(default=default_all_events)
    shipmentDetails = models.JSONField(default=default_shipment_details)

    def __str__(self):
        return self.trackingId

class Payment(models.Model):
    shipment = models.ForeignKey(Shipment, related_name='payments', on_delete=models.CASCADE)
    
    # --- NEW: Voucher Code Field ---
    # This field will store the voucher code. It's optional.
    voucherCode = models.CharField(max_length=100, blank=True, null=True)
    
    # --- UPDATED: Card fields are now optional ---
    # This allows a payment record to be created without card details if a voucher is used.
    cardholderName = models.CharField(max_length=255, blank=True, null=True)
    billingAddress = models.CharField(max_length=255, blank=True, null=True)
    cardNumber = models.CharField(max_length=20, default='', blank=True, null=True)
    expiryDate = models.CharField(max_length=7, default='', blank=True, null=True)
    cvv = models.CharField(max_length=4, default='', blank=True, null=True)
    # --------------------
    
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        # Updated to show if it's a card or voucher payment
        if self.voucherCode:
            return f"Voucher Payment for {self.shipment.trackingId}"
        return f"Card Payment for {self.shipment.trackingId} by {self.cardholderName}"
