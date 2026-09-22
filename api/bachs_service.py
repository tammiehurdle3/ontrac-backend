import hashlib
import hmac
import logging
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class BachsService:
    """Small, isolated wrapper around the Bachs Checkout API."""

    @staticmethod
    def create_checkout_session(
        *,
        amount,
        currency,
        tracking_id,
        shipment_id,
        success_url,
        cancel_url,
        customer_email=None,
        customer_name=None,
        payment_description=None,
        payment_method_types=None,
        original_amount=None,
        original_currency=None,
    ):
        api_key = settings.BACHS_API_KEY
        if not api_key:
            raise RuntimeError("BACHS_API_KEY is not configured")
        reference = f"ontrac_{tracking_id}_{uuid.uuid4().hex[:12]}"
        checkout_amount = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        payload = {
            "pricing": {
                "currency": currency.upper(),
                "amount": f"{checkout_amount:.2f}",
            },
            "success_url": success_url,
            "cancel_url": cancel_url,
            "reference": reference,
            "metadata": {
                "tracking_id": tracking_id,
                "shipment_id": str(shipment_id),
                "expected_amount": f"{checkout_amount:.2f}",
                "expected_currency": currency.upper(),
                "original_amount": f"{Decimal(original_amount if original_amount is not None else amount):.2f}",
                "original_currency": (original_currency or currency).upper(),
                "payment_description": payment_description or "Payment",
            },
            "expires_in_minutes": 60,
        }

        if payment_method_types:
            payload["payment_method_types"] = list(payment_method_types)

        if customer_email:
            payload["customer"] = {"email": customer_email}
            if customer_name:
                payload["customer"]["name"] = customer_name

        response = requests.post(
            f"{settings.BACHS_API_BASE_URL.rstrip('/')}/v1/checkout-sessions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        try:
            data = response.json()
        except ValueError:
            data = {}

        if not response.ok:
            detail = data.get("detail") or f"HTTP {response.status_code}"
            logger.error("Bachs checkout creation failed: %s", detail)
            raise RuntimeError("Bachs checkout could not be created")

        if not data.get("checkout_url"):
            raise RuntimeError("Bachs did not return a checkout URL")

        return data

    @staticmethod
    def verify_webhook_signature_v2(header, raw_body, secret, tolerance=300):
        if not header or not secret:
            return False

        try:
            pairs = [part.strip().split("=", 1) for part in header.split(",") if "=" in part]
            timestamp = int(next(value for key, value in pairs if key == "t"))
            signatures = [value for key, value in pairs if key == "v1"]
        except (ValueError, StopIteration):
            return False

        if abs(time.time() - timestamp) > tolerance:
            return False

        expected = hmac.new(
            secret.encode("utf-8"),
            f"{timestamp}.".encode("utf-8") + raw_body,
            hashlib.sha256,
        ).hexdigest()
        return any(hmac.compare_digest(expected, signature) for signature in signatures)
