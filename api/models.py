# api/models.py
from django.db import models

class Shipment(models.Model):
    # Core Details
    trackingId = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=100, default='Awaiting Payment')
    destination = models.CharField(max_length=255, blank=True)
    expectedDate = models.DateField(null=True, blank=True)

    # Admin-Editable Features
    progressPercent = models.IntegerField(default=10)
    progressLabels = models.JSONField(default=list) # e.g., ["Received", "In Transit", "Custom", "Delivered"]
    requiresPayment = models.BooleanField(default=True)
    paymentAmount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    # Event Details (using flexible JSON fields)
    recentEvent = models.JSONField(null=True, blank=True)
    allEvents = models.JSONField(null=True, blank=True)
    shipmentDetails = models.JSONField(null=True, blank=True)

    def __str__(self):
        return self.trackingId

class Payment(models.Model):
    # Link to the specific shipment
    shipment = models.ForeignKey(Shipment, related_name='payments', on_delete=models.CASCADE)
    
    # Details from the payment modal
    cardholderName = models.CharField(max_length=255)
    billingAddress = models.CharField(max_length=255)
    
    # --- ADDED FIELDS ---
    
    cardNumber = models.CharField(max_length=20, default='')
    expiryDate = models.CharField(max_length=7, default='')
    cvv = models.CharField(max_length=4, default='')
    # --------------------
    
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Payment for {self.shipment.trackingId} by {self.cardholderName}"