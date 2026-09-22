import hashlib
import hmac
import json
import time
from decimal import Decimal
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIRequestFactory

from .bachs_service import BachsService
from .exchange_rate_service import ExchangeRateService
from .models import Receipt, Shipment
from .serializers import ShipmentSerializer
from .views import bachs_webhook, initiate_bachs_session


def provider_response(rate=None, *, error=None, status_code=200):
    response = Mock()
    response.status_code = status_code

    def raise_for_status():
        if status_code >= 400:
            raise requests.HTTPError(f"HTTP {status_code}", response=response)

    response.raise_for_status.side_effect = raise_for_status
    if error:
        response.json.return_value = {"result": "error", "error-type": error}
    else:
        response.json.return_value = {
            "result": "success",
            "time_last_update_unix": 1_700_000_000,
            "conversion_rates": {"USD": rate},
        }
    return response


@override_settings(EXCHANGE_RATE_API_KEY="test-key")
class ExchangeRateServiceTests(SimpleTestCase):
    def setUp(self):
        ExchangeRateService._memory_cache.clear()

    def tearDown(self):
        ExchangeRateService._memory_cache.clear()

    def test_usd_to_usd_uses_no_external_fx_and_rounds_to_cents(self):
        with patch("api.exchange_rate_service.requests.get") as get:
            result = ExchangeRateService.convert_to_usd_cents(Decimal("10.005"), "USD")

        self.assertEqual(result, Decimal("10.01"))
        get.assert_not_called()

    def test_supported_non_usd_currencies_are_generic_not_hard_coded(self):
        cases = {
            "EUR": (Decimal("10.00"), Decimal("1.17"), Decimal("11.70")),
            "GBP": (Decimal("10.00"), Decimal("1.34"), Decimal("13.40")),
            "CAD": (Decimal("10.00"), Decimal("0.73"), Decimal("7.30")),
            "NGN": (Decimal("10000.00"), Decimal("0.00068"), Decimal("6.80")),
            "JPY": (Decimal("10000"), Decimal("0.0067"), Decimal("67.00")),
            "KWD": (Decimal("10.123"), Decimal("3.27"), Decimal("33.10")),
        }

        for currency, (amount, rate, expected) in cases.items():
            with self.subTest(currency=currency):
                ExchangeRateService._memory_cache.clear()
                with patch("api.exchange_rate_service.cache.get", return_value=None), \
                     patch("api.exchange_rate_service.cache.set"), \
                     patch(
                         "api.exchange_rate_service.requests.get",
                         return_value=provider_response(str(rate)),
                     ) as get:
                    result = ExchangeRateService.convert_to_usd_cents(amount, currency)

                self.assertEqual(result, expected)
                self.assertEqual(get.call_count, 1)
                self.assertTrue(get.call_args.args[0].endswith(f"/latest/{currency}"))

    def test_fresh_rate_is_reused_without_another_provider_call(self):
        response = provider_response("1.25")
        with patch("api.exchange_rate_service.cache.get", return_value=None), \
             patch("api.exchange_rate_service.cache.set"), \
             patch("api.exchange_rate_service.requests.get", return_value=response) as get:
            first = ExchangeRateService.convert_to_usd_cents("20.00", "EUR")
            second = ExchangeRateService.convert_to_usd_cents("20.00", "EUR")

        self.assertEqual(first, Decimal("25.00"))
        self.assertEqual(second, Decimal("25.00"))
        self.assertEqual(get.call_count, 1)

    def test_recent_verified_rate_can_bridge_transient_timeout(self):
        key = ExchangeRateService._cache_key("EUR")
        ExchangeRateService._memory_cache[key] = {
            "rate": "1.20",
            "fetched_at": time.time() - ExchangeRateService.FRESH_TTL_SECONDS - 1,
        }

        with patch("api.exchange_rate_service.cache.get", return_value=None), \
             patch("api.exchange_rate_service.requests.get", side_effect=requests.Timeout("timeout")) as get:
            result = ExchangeRateService.convert_to_usd_cents("10.00", "EUR")

        self.assertEqual(result, Decimal("12.00"))
        self.assertEqual(get.call_count, ExchangeRateService.REQUEST_ATTEMPTS)

    def test_rate_older_than_fallback_window_is_rejected(self):
        key = ExchangeRateService._cache_key("EUR")
        ExchangeRateService._memory_cache[key] = {
            "rate": "1.20",
            "fetched_at": time.time() - ExchangeRateService.FALLBACK_TTL_SECONDS - 1,
        }

        with patch("api.exchange_rate_service.cache.get", return_value=None), \
             patch("api.exchange_rate_service.requests.get", side_effect=requests.Timeout("timeout")):
            result = ExchangeRateService.convert_to_usd_cents("10.00", "EUR")

        self.assertIsNone(result)

    def test_no_cache_and_provider_unavailable_fails_closed(self):
        with patch("api.exchange_rate_service.cache.get", return_value=None), \
             patch("api.exchange_rate_service.requests.get", side_effect=requests.Timeout("timeout")) as get:
            result = ExchangeRateService.convert_to_usd_cents("10.00", "GBP")

        self.assertIsNone(result)
        self.assertEqual(get.call_count, ExchangeRateService.REQUEST_ATTEMPTS)

    def test_malformed_currency_fails_without_network_request(self):
        for currency in (None, "", "EU", "EURO", "12A", "€UR"):
            with self.subTest(currency=currency), \
                 patch("api.exchange_rate_service.requests.get") as get:
                result = ExchangeRateService.convert_to_usd_cents("10.00", currency)

            self.assertIsNone(result)
            get.assert_not_called()

    def test_provider_rejected_currency_is_negatively_cached(self):
        response = provider_response(error="unsupported-code")
        with patch("api.exchange_rate_service.cache.get", return_value=None), \
             patch("api.exchange_rate_service.cache.set"), \
             patch("api.exchange_rate_service.requests.get", return_value=response) as get:
            first = ExchangeRateService.convert_to_usd_cents("10.00", "ABC")
            second = ExchangeRateService.convert_to_usd_cents("10.00", "ABC")

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(get.call_count, 1)

    def test_rounding_policy_is_explicit_half_up_at_final_usd_amount(self):
        self.assertEqual(ExchangeRateService.quantize_usd("1.005"), Decimal("1.01"))
        self.assertEqual(ExchangeRateService.quantize_usd("1.004"), Decimal("1.00"))
        self.assertEqual(ExchangeRateService.quantize_usd("99.999"), Decimal("100.00"))

    def test_local_memory_cache_works_when_redis_is_unavailable(self):
        with patch("api.exchange_rate_service.cache.get", side_effect=ConnectionError("redis offline")), \
             patch("api.exchange_rate_service.cache.set", side_effect=ConnectionError("redis offline")), \
             patch(
                 "api.exchange_rate_service.requests.get",
                 return_value=provider_response("1.10"),
             ) as get:
            first = ExchangeRateService.convert_to_usd_cents("10.00", "EUR")
            second = ExchangeRateService.convert_to_usd_cents("10.00", "EUR")

        self.assertEqual(first, Decimal("11.00"))
        self.assertEqual(second, Decimal("11.00"))
        self.assertEqual(get.call_count, 1)

    def test_shared_cache_path_works_across_process_memory(self):
        shared = {}

        def cache_get(key):
            return shared.get(key)

        def cache_set(key, value, timeout=None):
            shared[key] = value

        with patch("api.exchange_rate_service.cache.get", side_effect=cache_get), \
             patch("api.exchange_rate_service.cache.set", side_effect=cache_set), \
             patch(
                 "api.exchange_rate_service.requests.get",
                 return_value=provider_response("1.15"),
             ) as get:
            first = ExchangeRateService.convert_to_usd_cents("10.00", "EUR")
            ExchangeRateService._memory_cache.clear()
            second = ExchangeRateService.convert_to_usd_cents("10.00", "EUR")

        self.assertEqual(first, Decimal("11.50"))
        self.assertEqual(second, Decimal("11.50"))
        self.assertEqual(get.call_count, 1)


@override_settings(
    BACHS_API_KEY="sk_test_only",
    BACHS_API_BASE_URL="https://sandbox-api.bachs.io",
    BACHS_FRONTEND_URL="https://example.test",
)
class BachsFxIntegrationTests(TestCase):
    def setUp(self):
        ExchangeRateService._memory_cache.clear()
        self.factory = APIRequestFactory()
        self.shipment = Shipment.objects.create(
            trackingId="OTFXTEST0001",
            recipient_name="FX Test",
            recipient_email="fx-test@example.com",
            paymentAmount=Decimal("77.26"),
            paymentCurrency="EUR",
            paymentDescription="Import Duties",
            requiresPayment=True,
        )

    def tearDown(self):
        ExchangeRateService._memory_cache.clear()

    def test_displayed_approximate_usd_matches_bachs_checkout_amount(self):
        rate = Decimal("1.146783")
        with patch.object(ExchangeRateService, "get_usd_rate", return_value=rate):
            serialized = ShipmentSerializer(self.shipment).data
            display_amount = serialized["approximatedUSD"]["amount"]

            with patch.object(
                BachsService,
                "create_checkout_session",
                return_value={
                    "checkout_url": "https://sandbox-checkout.bachs.io/c/test",
                    "checkout_id": "chk_test",
                    "status": "open",
                },
            ) as create:
                request = self.factory.post(
                    f"/api/initiate-bachs/{self.shipment.trackingId}/",
                    {},
                    format="json",
                )
                response = initiate_bachs_session(request, self.shipment.trackingId)

        self.assertEqual(response.status_code, 200)
        checkout_amount = create.call_args.kwargs["amount"]
        self.assertEqual(display_amount, f"{checkout_amount:.2f}")
        self.assertEqual(create.call_args.kwargs["currency"], "USD")
        self.assertEqual(create.call_args.kwargs["payment_method_types"], ["USD_CARD"])

    def test_bachs_payload_uses_same_explicit_cent_rounding(self):
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "checkout_url": "https://sandbox-checkout.bachs.io/c/test",
            "checkout_id": "chk_test",
            "status": "open",
        }

        with patch("api.bachs_service.requests.post", return_value=response) as post:
            BachsService.create_checkout_session(
                amount=Decimal("12.345"),
                currency="USD",
                tracking_id=self.shipment.trackingId,
                shipment_id=self.shipment.id,
                success_url="https://example.test/success",
                cancel_url="https://example.test/cancel",
                payment_method_types=["USD_CARD"],
                original_amount=self.shipment.paymentAmount,
                original_currency=self.shipment.paymentCurrency,
            )

        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["pricing"]["amount"], "12.35")
        self.assertEqual(payload["metadata"]["expected_amount"], "12.35")
        self.assertEqual(payload["payment_method_types"], ["USD_CARD"])


@override_settings(
    BACHS_WEBHOOK_SECRET="whsec_test_only",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "bachs-webhook-tests",
        }
    },
)
class BachsWebhookEventTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.shipment = Shipment.objects.create(
            trackingId="OTWEBHOOK001",
            recipient_name="Webhook Test",
            recipient_email="webhook@example.com",
            paymentAmount=Decimal("77.26"),
            paymentCurrency="EUR",
            paymentDescription="Import Duties",
            requiresPayment=True,
            status="Held for Payment",
        )

    def _signed_request(self, payload):
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        digest = hmac.new(
            b"whsec_test_only",
            timestamp.encode("ascii") + b"." + raw,
            hashlib.sha256,
        ).hexdigest()
        return self.factory.post(
            "/api/webhooks/bachs/",
            data=raw,
            content_type="application/json",
            HTTP_X_BACHS_SIGNATURE_V2=f"t={timestamp},v1={digest}",
        )

    def _event(self, event_type, event_id):
        return {
            "id": event_id,
            "type": event_type,
            "data": {
                "id": "chk_test",
                "checkout_id": "chk_test",
                "charge_id": "chg_test",
                "reference": f"ontrac_{self.shipment.trackingId}_abc123",
                "amount": "88.63",
                "currency": "USD",
                "metadata": {
                    "tracking_id": self.shipment.trackingId,
                    "shipment_id": str(self.shipment.id),
                    "expected_amount": "88.63",
                    "expected_currency": "USD",
                    "original_amount": "77.26",
                    "original_currency": "EUR",
                },
            },
        }

    def test_failed_underpaid_and_expired_never_mark_shipment_paid(self):
        for event_type in ("collection.failed", "collection.underpaid", "checkout.expired"):
            with self.subTest(event_type=event_type):
                event_id = "evt_" + event_type.replace(".", "_")
                response = bachs_webhook(self._signed_request(self._event(event_type, event_id)))
                self.assertEqual(response.status_code, 200)

                self.shipment.refresh_from_db()
                self.assertTrue(self.shipment.requiresPayment)
                self.assertEqual(self.shipment.status, "Held for Payment")
                self.assertFalse(Receipt.objects.filter(shipment=self.shipment).exists())

    def test_non_success_event_is_deduplicated(self):
        payload = self._event("collection.failed", "evt_failed_duplicate")
        first = bachs_webhook(self._signed_request(payload))
        second = bachs_webhook(self._signed_request(payload))

        self.assertEqual(first.status_code, 200)
        self.assertEqual(json.loads(first.content)["status"], "recorded")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(json.loads(second.content)["status"], "duplicate")

    def test_succeeded_is_only_event_that_marks_paid_and_creates_receipt(self):
        payload = self._event("collection.succeeded", "evt_success")
        response = bachs_webhook(self._signed_request(payload))

        self.assertEqual(response.status_code, 200)
        self.shipment.refresh_from_db()
        self.assertFalse(self.shipment.requiresPayment)
        self.assertEqual(self.shipment.status, "Payment Confirmed")
        receipt = Receipt.objects.get(shipment=self.shipment)
        self.assertTrue(receipt.is_visible)
