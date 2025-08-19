# api/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ShipmentViewSet, PaymentCreateView # Add PaymentCreateView

router = DefaultRouter()
router.register(r'shipments', ShipmentViewSet, basename='shipment')

urlpatterns = [
    path('', include(router.urls)),
    # ADD THIS NEW URL
    path('payments/', PaymentCreateView.as_view(), name='payment-create'),
]
