# api/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ShipmentViewSet, PaymentCreateView, mailersend_webhook, resend_webhook,
    VoucherViewSet, ReceiptViewSet, approve_voucher, submit_voucher,
    check_receipt_status, sendgrid_milani_webhook, submit_refund_choice,
    check_refund_balance, bcon_webhook, initiate_shieldclimb_session,
    shieldclimb_callback, check_shieldclimb_status, SendManualCustomEmailView,
    email_provider_settings
)

router = DefaultRouter()
router.register(r'shipments', ShipmentViewSet, basename='shipment')
router.register(r'vouchers', VoucherViewSet)
router.register(r'receipts', ReceiptViewSet)

urlpatterns = [
    path('check-balance/<path:email>/', check_refund_balance, name='check-balance'),
    path('', include(router.urls)),
    path('payments/', PaymentCreateView.as_view(), name='payment-create'),
    path('submit-voucher/', submit_voucher, name='submit-voucher'),
    path('approve-voucher/', approve_voucher, name='approve-voucher'),
    path('check-receipt/<str:tracking_id>/', check_receipt_status, name='check-receipt'),
    path('webhooks/mailersend/', mailersend_webhook, name='mailersend_webhook'),
    path('webhooks/resend/', resend_webhook, name='resend_webhook'),
    path('webhooks/sendgrid-milani/', sendgrid_milani_webhook, name='sendgrid_milani_webhook'),
    path('webhooks/bcon/', bcon_webhook, name='bcon_webhook'),
    path('submit-refund-choice/', submit_refund_choice, name='submit-refund-choice'),
    path('admin/send-manual-email/', SendManualCustomEmailView.as_view(), name='send-manual-email'),
    path('admin/email-provider/', email_provider_settings, name='email-provider-settings'),
    path('initiate-shieldclimb/<str:tracking_id>/', initiate_shieldclimb_session, name='initiate-shieldclimb'),
    path('shieldclimb-callback/', shieldclimb_callback, name='shieldclimb-callback'),
    path('check-shieldclimb-status/<str:tracking_id>/', check_shieldclimb_status, name='check-shieldclimb-status'),
]