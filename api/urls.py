# api/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ShipmentViewSet, PaymentCreateView, brevo_webhook,
    VoucherViewSet, ReceiptViewSet, approve_voucher, submit_voucher, check_receipt_status, sendgrid_milani_webhook
)

# The router automatically creates the URLs for the Shipment model
router = DefaultRouter()
router.register(r'shipments', ShipmentViewSet, basename='shipment')
router.register(r'vouchers', VoucherViewSet)
router.register(r'receipts', ReceiptViewSet)

# This is the main list of all API addresses
urlpatterns = [
    # Includes all the shipment URLs created by the router
    path('', include(router.urls)),

    path('check-receipt/<str:tracking_id>/', check_receipt_status, name='check-receipt'),
    
    # Adds the specific URL for creating a new payment
    path('payments/', PaymentCreateView.as_view(), name='payment-create'),

    # NEW: Voucher and Receipt URLs
    path('submit-voucher/', submit_voucher, name='submit-voucher'),
    path('approve-voucher/', approve_voucher, name='approve-voucher'),
    path('check-receipt/<str:tracking_id>/', check_receipt_status, name='check-receipt'),

    # Adds the specific URL for the Brevo Webhook
    path('webhooks/brevo/', brevo_webhook, name='brevo_webhook'),

    # NEW: Dedicated SendGrid Webhook for Milani Outreach
    path('webhooks/sendgrid-milani/', sendgrid_milani_webhook, name='sendgrid_milani_webhook'),
]