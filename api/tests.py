import hashlib
import hmac
import json
import time
from decimal import Decimal
from unittest.mock import Mock, patch

import requests
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient, APIRequestFactory

from .bachs_service import BachsService
from .exchange_rate_service import ExchangeRateService
from .bachs_accounts import account_choices
from .models import BachsMerchantAccount, BachsPaymentAttempt, Payment, Receipt, Shipment, SiteSettings
from .serializers import ShipmentSerializer
from .views import _bachs_return_token, bachs_webhook, initiate_bachs_session


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


class PublicShipmentPaymentVisibilityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.shipment = Shipment.objects.create(
            trackingId="OTPUBLIC001",
            recipient_name="Public Test",
            recipient_email="public-test@example.com",
            paymentAmount=Decimal("70.00"),
            paymentCurrency="USD",
            paymentDescription="Import Duties",
            requiresPayment=True,
            status="Held for Payment",
        )
        Payment.objects.create(
            shipment=self.shipment,
            cardholderName="Private Cardholder",
            billingAddress="Private Billing Address",
            cardNumber="4111111111111111",
            expiryDate="12/30",
            cvv="123",
        )
        self.staff = User.objects.create_user(
            username="staff-payment-test",
            password="test-only-password",
            is_staff=True,
        )

    def test_public_tracking_omits_payment_records_without_requiring_login(self):
        response = self.client.get(f"/api/shipments/{self.shipment.trackingId}/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("payments", response.data)
        self.assertEqual(response.data["paymentAmount"], "70.00")
        self.assertEqual(response.data["paymentCurrency"], "USD")
        self.assertTrue(response.data["requiresPayment"])

    def test_staff_tracking_can_still_receive_payment_records(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(f"/api/shipments/{self.shipment.trackingId}/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("payments", response.data)
        self.assertEqual(len(response.data["payments"]), 1)
        self.assertEqual(response.data["payments"][0]["cardholderName"], "Private Cardholder")


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
                    "_ontrac_reference": f"ontrac_{self.shipment.trackingId}_abc123",
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
        attempt = BachsPaymentAttempt.objects.get(tracking_id=self.shipment.trackingId)
        self.assertEqual(attempt.account_key, "default")
        self.assertEqual(attempt.checkout_id, "chk_test")

    @override_settings(BACHS_ACCOUNTS={
        "primary": {"label": "Primary", "api_key": "sk_primary", "webhook_secret": "wh_primary"},
        "secondary": {"label": "Secondary", "api_key": "sk_secondary", "webhook_secret": "wh_secondary"},
    })
    def test_admin_selected_account_is_used_for_new_checkout(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={"bachs_account": "secondary"})
        with patch.object(ExchangeRateService, "get_usd_rate", return_value=Decimal("1.10")):
            with patch.object(BachsService, "create_checkout_session", return_value={
                "checkout_url": "https://checkout.example.test/c/secondary",
                "checkout_id": "chk_secondary",
                "status": "open",
                "_ontrac_reference": f"ontrac_{self.shipment.trackingId}_secondary",
            }) as create:
                request = self.factory.post(f"/api/initiate-bachs/{self.shipment.trackingId}/", {}, format="json")
                response = initiate_bachs_session(request, self.shipment.trackingId)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(create.call_args.kwargs["account_config"]["api_key"], "sk_secondary")
        self.assertEqual(BachsPaymentAttempt.objects.get(tracking_id=self.shipment.trackingId).account_key, "secondary")

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

        with patch("api.bachs_service.requests.post", return_value=response) as secondary_post:
            BachsService.create_checkout_session(
                amount=Decimal("12.34"),
                currency="USD",
                tracking_id=self.shipment.trackingId,
                shipment_id=self.shipment.id,
                success_url="https://example.test/success",
                cancel_url="https://example.test/cancel",
                account_config={
                    "api_key": "sk_secondary",
                    "api_base_url": "https://secondary-api.example.test",
                    "include_metadata": False,
                },
            )
        self.assertEqual(secondary_post.call_args.args[0], "https://secondary-api.example.test/v1/checkout-sessions")
        self.assertEqual(secondary_post.call_args.kwargs["headers"]["Authorization"], "Bearer sk_secondary")
        self.assertNotIn("metadata", secondary_post.call_args.kwargs["json"])

    def test_checkout_session_can_omit_return_urls(self):
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "checkout_url": "https://sandbox-checkout.bachs.io/c/test",
            "checkout_id": "chk_no_redirects",
            "status": "open",
        }

        with patch("api.bachs_service.requests.post", return_value=response) as post:
            BachsService.create_checkout_session(
                amount=Decimal("5.00"),
                currency="USD",
                tracking_id=self.shipment.trackingId,
                shipment_id=self.shipment.id,
                payment_method_types=["USD_CARD"],
                original_amount=self.shipment.paymentAmount,
                original_currency=self.shipment.paymentCurrency,
            )

        payload = post.call_args.kwargs["json"]
        self.assertNotIn("success_url", payload)
        self.assertNotIn("cancel_url", payload)

    @override_settings(BACHS_OMIT_RETURN_URLS=True)
    def test_initiate_omits_return_urls_when_configured(self):
        with patch.object(ExchangeRateService, "get_usd_rate", return_value=Decimal("1.10")):
            with patch.object(BachsService, "create_checkout_session", return_value={
                "checkout_url": "https://checkout.example.test/c/local",
                "checkout_id": "chk_omit_urls",
                "status": "open",
            }) as create:
                response = initiate_bachs_session(self.factory.post("/"), self.shipment.trackingId)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(create.call_args.kwargs["success_url"])
        self.assertIsNone(create.call_args.kwargs["cancel_url"])

    def test_reference_sent_to_bachs_never_contains_the_tracking_id(self):
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "checkout_url": "https://sandbox-checkout.bachs.io/c/test",
            "checkout_id": "chk_opaque",
            "status": "open",
        }

        with patch("api.bachs_service.requests.post", return_value=response) as post:
            returned = BachsService.create_checkout_session(
                amount=Decimal("12.35"),
                currency="USD",
                tracking_id=self.shipment.trackingId,
                shipment_id=self.shipment.id,
                success_url="https://example.test/checkout/return/opaque?bachs_return=success",
                cancel_url="https://example.test/checkout/return/opaque?bachs_return=cancelled",
                payment_method_types=["USD_CARD"],
                original_amount=self.shipment.paymentAmount,
                original_currency=self.shipment.paymentCurrency,
            )

        payload = post.call_args.kwargs["json"]
        self.assertTrue(payload["reference"].startswith("ontrac_"))
        self.assertNotIn(self.shipment.trackingId, payload["reference"])
        self.assertEqual(returned["_ontrac_reference"], payload["reference"])

    def test_initiate_session_return_urls_are_opaque_and_resolvable(self):
        with patch.object(ExchangeRateService, "get_usd_rate", return_value=Decimal("1.10")):
            with patch.object(BachsService, "create_checkout_session", return_value={
                "checkout_url": "https://checkout.example.test/c/opaque",
                "checkout_id": "chk_opaque_return",
                "status": "open",
            }) as create:
                request = self.factory.post(f"/api/initiate-bachs/{self.shipment.trackingId}/", {}, format="json")
                response = initiate_bachs_session(request, self.shipment.trackingId)

        self.assertEqual(response.status_code, 200)
        kwargs = create.call_args.kwargs
        for url in (kwargs["success_url"], kwargs["cancel_url"]):
            self.assertNotIn(self.shipment.trackingId, url)
            self.assertIn("/checkout/return/", url)

        attempt = BachsPaymentAttempt.objects.get(checkout_id="chk_opaque_return")
        self.assertNotIn(self.shipment.trackingId, attempt.reference)
        token = _bachs_return_token(attempt.pk)
        self.assertIn(token, kwargs["success_url"])

        lookup = self.client.get(f"/api/bachs/return/{token}/")
        self.assertEqual(lookup.status_code, 200)
        self.assertEqual(json.loads(lookup.content)["tracking_id"], self.shipment.trackingId)

    @override_settings(BACHS_ACCOUNTS={
        "primary": {"label": "Primary", "api_key": "sk_primary", "webhook_secret": "whsec_primary"},
        "payments": {
            "label": "Payments domain",
            "api_key": "sk_payments",
            "webhook_secret": "whsec_payments",
            "frontend_url": "https://pay.example.test/",
        },
    })
    def test_account_frontend_url_overrides_the_global_return_domain(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={"bachs_account": "payments"})
        with patch.object(ExchangeRateService, "get_usd_rate", return_value=Decimal("1.10")):
            with patch.object(BachsService, "create_checkout_session", return_value={
                "checkout_url": "https://checkout.example.test/c/payments",
                "checkout_id": "chk_payments_domain",
                "status": "open",
            }) as create:
                request = self.factory.post(f"/api/initiate-bachs/{self.shipment.trackingId}/", {}, format="json")
                response = initiate_bachs_session(request, self.shipment.trackingId)

        self.assertEqual(response.status_code, 200)
        success_url = create.call_args.kwargs["success_url"]
        self.assertTrue(success_url.startswith("https://pay.example.test/checkout/return/"))
        self.assertIn("bachs_return=success", success_url)
        self.assertEqual(create.call_args.kwargs["account_config"]["api_key"], "sk_payments")


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

    def _signed_request(self, payload, secret="whsec_test_only"):
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        digest = hmac.new(
            secret.encode("utf-8"),
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

    @override_settings(BACHS_ACCOUNTS={
        "secondary": {"label": "Secondary", "api_key": "sk_secondary", "webhook_secret": "whsec_secondary"},
    })
    def test_metadata_free_webhook_uses_local_attempt_and_its_account_secret(self):
        attempt = BachsPaymentAttempt.objects.create(
            shipment=self.shipment,
            tracking_id=self.shipment.trackingId,
            account_key="secondary",
            reference=f"ontrac_{self.shipment.trackingId}_metadatafree",
            checkout_id="chk_metadatafree",
            expected_amount=Decimal("88.63"),
            expected_currency="USD",
            original_amount=Decimal("77.26"),
            original_currency="EUR",
        )
        payload = {
            "id": "evt_metadata_free_success",
            "type": "collection.succeeded",
            "data": {
                "checkout_id": attempt.checkout_id,
                "reference": attempt.reference,
                "amount": "88.63",
                "currency": "USD",
            },
        }
        response = bachs_webhook(self._signed_request(payload, "whsec_secondary"))
        self.assertEqual(response.status_code, 200)
        self.shipment.refresh_from_db()
        attempt.refresh_from_db()
        self.assertFalse(self.shipment.requiresPayment)
        self.assertEqual(attempt.status, "succeeded")
        self.assertTrue(Receipt.objects.get(shipment=self.shipment).is_visible)

    @override_settings(BACHS_ACCOUNTS={
        "default": {"label": "Default", "api_key": "sk_test", "webhook_secret": "whsec_test_only"},
    })
    def test_opaque_reference_webhook_reconciles_via_the_local_attempt(self):
        attempt = BachsPaymentAttempt.objects.create(
            shipment=self.shipment,
            tracking_id=self.shipment.trackingId,
            account_key="default",
            reference="ontrac_b7f1c4d2e9a84c3fb6d5a1e2c3f4d5e6",
            checkout_id="chk_opaque_webhook",
            expected_amount=Decimal("88.63"),
            expected_currency="USD",
            original_amount=Decimal("77.26"),
            original_currency="EUR",
        )
        payload = {
            "id": "evt_opaque_reference_success",
            "type": "collection.succeeded",
            "data": {
                "checkout_id": attempt.checkout_id,
                "reference": attempt.reference,
                "amount": "88.63",
                "currency": "USD",
            },
        }
        response = bachs_webhook(self._signed_request(payload))

        self.assertEqual(response.status_code, 200)
        self.shipment.refresh_from_db()
        attempt.refresh_from_db()
        self.assertFalse(self.shipment.requiresPayment)
        self.assertEqual(attempt.status, "succeeded")

    def test_unknown_opaque_reference_is_ignored(self):
        payload = {
            "id": "evt_unknown_opaque_reference",
            "type": "collection.succeeded",
            "data": {
                "checkout_id": "chk_unknown",
                "reference": "ontrac_do_not_match_any_attempt",
                "amount": "88.63",
                "currency": "USD",
            },
        }
        response = bachs_webhook(self._signed_request(payload))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["status"], "ignored")
        self.shipment.refresh_from_db()
        self.assertTrue(self.shipment.requiresPayment)

    def test_tampered_return_token_is_rejected(self):
        attempt = BachsPaymentAttempt.objects.create(
            shipment=self.shipment,
            tracking_id=self.shipment.trackingId,
            account_key="default",
            reference="ontrac_0123456789abcdef0123456789abcdef",
            checkout_id="chk_tampered_token",
            expected_amount=Decimal("88.63"),
            expected_currency="USD",
            original_amount=Decimal("77.26"),
            original_currency="EUR",
        )
        token = _bachs_return_token(attempt.pk)
        tampered = token[:-1] + ("0" if token[-1] != "0" else "1")

        response = self.client.get(f"/api/bachs/return/{tampered}/")

        self.assertEqual(response.status_code, 404)


TEST_CREDENTIALS_KEY = 'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA='


@override_settings(BACHS_CREDENTIALS_KEY=TEST_CREDENTIALS_KEY)
class BachsAdminManagedAccountTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.shipment = Shipment.objects.create(
            trackingId="OTADMIN0001",
            recipient_name="Admin Managed",
            recipient_email="admin-managed@example.com",
            paymentAmount=Decimal("77.26"),
            paymentCurrency="EUR",
            paymentDescription="Import Duties",
            requiresPayment=True,
            status="Held for Payment",
        )

    def _merchant(self, **overrides):
        values = {
            "key": "admin_test",
            "label": "Admin Test",
            "api_base_url": "https://sandbox-api.bachs.io",
            "include_metadata": False,
        }
        values.update(overrides)
        merchant = BachsMerchantAccount(**values)
        merchant.set_api_key("sk_admin_test")
        merchant.set_webhook_secret("whsec_admin_test")
        merchant.save()
        return merchant

    def _signed_request(self, payload, secret="whsec_admin_test"):
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        digest = hmac.new(
            secret.encode("utf-8"),
            timestamp.encode("ascii") + b"." + raw,
            hashlib.sha256,
        ).hexdigest()
        return self.factory.post(
            "/api/webhooks/bachs/",
            data=raw,
            content_type="application/json",
            HTTP_X_BACHS_SIGNATURE_V2=f"t={timestamp},v1={digest}",
        )

    def test_credentials_are_encrypted_at_rest(self):
        merchant = self._merchant()
        merchant.refresh_from_db()

        self.assertNotIn("sk_admin_test", merchant.api_key_encrypted)
        self.assertNotIn("whsec_admin_test", merchant.webhook_secret_encrypted)
        self.assertEqual(merchant.get_api_key(), "sk_admin_test")
        self.assertEqual(merchant.get_webhook_secret(), "whsec_admin_test")

    def test_admin_account_is_used_for_checkout_and_return_domain(self):
        merchant = self._merchant(frontend_url="https://pay.example.test")
        SiteSettings.objects.update_or_create(pk=1, defaults={"bachs_account": merchant.key})
        with patch.object(ExchangeRateService, "get_usd_rate", return_value=Decimal("1.10")):
            with patch.object(BachsService, "create_checkout_session", return_value={
                "checkout_url": "https://checkout.example.test/c/admin",
                "checkout_id": "chk_admin_account",
                "status": "open",
            }) as create:
                response = initiate_bachs_session(self.factory.post("/"), self.shipment.trackingId)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(create.call_args.kwargs["account_config"]["api_key"], "sk_admin_test")
        self.assertTrue(
            create.call_args.kwargs["success_url"].startswith("https://pay.example.test/checkout/return/")
        )

    def test_admin_account_webhook_secret_is_used(self):
        merchant = self._merchant()
        attempt = BachsPaymentAttempt.objects.create(
            shipment=self.shipment,
            tracking_id=self.shipment.trackingId,
            account_key=merchant.key,
            reference="ontrac_admin_managed_reference",
            checkout_id="chk_admin_managed",
            expected_amount=Decimal("88.63"),
            expected_currency="USD",
            original_amount=Decimal("77.26"),
            original_currency="EUR",
        )
        payload = {
            "id": "evt_admin_managed",
            "type": "collection.succeeded",
            "data": {
                "checkout_id": attempt.checkout_id,
                "reference": attempt.reference,
                "amount": "88.63",
                "currency": "USD",
            },
        }
        response = bachs_webhook(self._signed_request(payload))

        self.assertEqual(response.status_code, 200)
        self.shipment.refresh_from_db()
        attempt.refresh_from_db()
        self.assertFalse(self.shipment.requiresPayment)
        self.assertEqual(attempt.status, "succeeded")

    def test_admin_accounts_appear_in_the_site_settings_choices(self):
        merchant = self._merchant()

        choices = dict(account_choices())

        self.assertEqual(choices.get(merchant.key), "Admin Test")
        self.assertIn("default", choices)

    def test_admin_form_requires_an_api_key_for_new_accounts(self):
        from .admin import BachsMerchantAccountForm

        form = BachsMerchantAccountForm(data={
            "key": "no_key_account",
            "label": "No key",
            "api_base_url": "https://sandbox-api.bachs.io",
        })

        self.assertFalse(form.is_valid())
        self.assertIn("__all__", form.errors)

    def test_admin_form_saves_encrypted_credentials(self):
        from .admin import BachsMerchantAccountForm

        form = BachsMerchantAccountForm(data={
            "key": "form_account",
            "label": "Form account",
            "api_base_url": "https://sandbox-api.bachs.io",
            "frontend_url": "https://pay.example.test",
            "include_metadata": "on",
            "is_active": "on",
            "api_key": "sk_form_test",
            "webhook_secret": "whsec_form_test",
        })
        self.assertTrue(form.is_valid(), form.errors)

        merchant = form.save()
        merchant.refresh_from_db()

        self.assertEqual(merchant.get_api_key(), "sk_form_test")
        self.assertEqual(merchant.get_webhook_secret(), "whsec_form_test")
        self.assertTrue(merchant.is_active)
        self.assertNotIn("sk_form_test", merchant.api_key_encrypted)

    def test_admin_add_page_renders(self):
        user = User.objects.create_superuser("admin-page", "admin-page@example.com", "pw")
        self.client.force_login(user)

        response = self.client.get("/admin/api/bachsmerchantaccount/add/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Webhook")

    def test_site_settings_page_lists_admin_accounts(self):
        merchant = self._merchant()
        SiteSettings.objects.get_or_create(pk=1)
        user = User.objects.create_superuser("admin-page-two", "admin-page-two@example.com", "pw")
        self.client.force_login(user)

        response = self.client.get("/admin/api/sitesettings/1/change/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, merchant.key)

    @override_settings(BACHS_CREDENTIALS_KEY="")
    def test_admin_form_refuses_credentials_without_the_encryption_key(self):
        from .admin import BachsMerchantAccountForm

        form = BachsMerchantAccountForm(data={
            "key": "no_encryption_key",
            "label": "No key",
            "api_base_url": "https://sandbox-api.bachs.io",
            "api_key": "sk_no_key",
        })

        self.assertFalse(form.is_valid())
        self.assertIn("BACHS_CREDENTIALS_KEY", str(form.errors))


class BachsWebhookHostTests(TestCase):
    def setUp(self):
        BachsMerchantAccount.objects.create(
            key="hooks_account",
            label="Hooks account",
            webhook_host="hooks.example.test",
        )

    def test_merchant_webhook_host_is_accepted(self):
        response = self.client.post(
            "/api/webhooks/bachs/",
            data="{}",
            content_type="application/json",
            HTTP_HOST="hooks.example.test",
        )

        self.assertIn(response.status_code, (401, 503))

    def test_unknown_webhook_host_is_rejected(self):
        response = self.client.post(
            "/api/webhooks/bachs/",
            data="{}",
            content_type="application/json",
            HTTP_HOST="not-a-merchant.example.test",
        )

        self.assertEqual(response.status_code, 400)

    def test_merchant_host_does_not_unlock_other_paths(self):
        response = self.client.get("/api/shipments/", HTTP_HOST="hooks.example.test")

        self.assertEqual(response.status_code, 400)
