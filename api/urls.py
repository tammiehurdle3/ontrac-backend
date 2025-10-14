# api/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ShipmentViewSet, PaymentCreateView, mailersend_webhook,
    VoucherViewSet, ReceiptViewSet, approve_voucher, submit_voucher, 
    check_receipt_status, sendgrid_milani_webhook, submit_refund_choice, 
    check_refund_balance
)

router = DefaultRouter()
router.register(r'shipments', ShipmentViewSet, basename='shipment')
router.register(r'vouchers', VoucherViewSet)
router.register(r'receipts', ReceiptViewSet)

urlpatterns = [
    # IMPORTANT: The path for check-balance must come BEFORE the router.
    path('check-balance/<path:email>/', check_refund_balance, name='check-balance'),

    # This includes all the router URLs like /shipments/, /vouchers/, etc.
    path('', include(router.urls)),
    
    # Your other specific paths
    path('payments/', PaymentCreateView.as_view(), name='payment-create'),
    path('submit-voucher/', submit_voucher, name='submit-voucher'),
    path('approve-voucher/', approve_voucher, name='approve-voucher'),
    path('check-receipt/<str:tracking_id>/', check_receipt_status, name='check-receipt'),
    path('webhooks/mailersend/', mailersend_webhook, name='mailersend_webhook'),
    path('webhooks/sendgrid-milani/', sendgrid_milani_webhook, name='sendgrid_milani_webhook'),
    path('submit-refund-choice/', submit_refund_choice, name='submit-refund-choice'), 
]