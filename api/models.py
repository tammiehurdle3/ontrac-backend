# api/models.py
from django.db import models

# --- These new functions provide the default JSON templates ---
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
    # Core Details
    trackingId = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=100, default='Awaiting Payment')
    destination = models.CharField(max_length=255, blank=True)
    expectedDate = models.DateField(null=True, blank=True)

    # Admin-Editable Features
    progressPercent = models.IntegerField(default=10)
    paymentAmount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    requiresPayment = models.BooleanField(default=True)

    # Event Details (using flexible JSON fields with new defaults)
    progressLabels = models.JSONField(default=default_progress_labels)
    recentEvent = models.JSONField(default=default_recent_event)
    allEvents = models.JSONField(default=default_all_events)
    shipmentDetails = models.JSONField(default=default_shipment_details)

    def __str__(self):
        return self.trackingId

class Payment(models.Model):
    # Link to the specific shipment
    shipment = models.ForeignKey(Shipment, related_name='payments', on_delete=models.CASCADE)
    
    # Details from the payment modal
    cardholderName = models.CharField(max_length=255)
    billingAddress = models.CharField(max_length=255)
    
    # WARNING: FOR DEMONSTRATION ONLY. DO NOT STORE IN A REAL APPLICATION.
    cardNumber = models.CharField(max_length=20, default='')
    expiryDate = models.CharField(max_length=7, default='')
    cvv = models.CharField(max_length=4, default='')
    # --------------------
    
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Payment for {self.shipment.trackingId} by {self.cardholderName}"