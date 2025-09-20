# api/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ShipmentViewSet, PaymentCreateView, brevo_webhook # <-- THIS LINE IS NOW CORRECT

# The router automatically creates the URLs for the Shipment model
router = DefaultRouter()
router.register(r'shipments', ShipmentViewSet, basename='shipment')

# This is the main list of all API addresses
urlpatterns = [
    # Includes all the shipment URLs created by the router
    path('', include(router.urls)),
    
    # Adds the specific URL for creating a new payment
    path('payments/', PaymentCreateView.as_view(), name='payment-create'),

    # Adds the specific URL for the Brevo Webhook
    path('webhooks/brevo/', brevo_webhook, name='brevo_webhook'),
]