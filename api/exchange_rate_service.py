import logging
import threading
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


class ExchangeRateService:
    """
    Authoritative fiat -> USD conversion for OnTrac's general payment/display flows.

    The existing ExchangeRate-API account remains the rate source. A successful rate is
    reused briefly to keep customer-visible approximations and Bachs checkout pricing
    consistent. During a transient provider outage, only a recently verified rate may be
    used. Invalid/unsupported currencies and provider configuration errors fail closed.
    """

    FRESH_TTL_SECONDS = 15 * 60
    FALLBACK_TTL_SECONDS = 60 * 60
    REQUEST_ATTEMPTS = 2
    REQUEST_TIMEOUT = (2.5, 4.0)
    USD_QUANTUM = Decimal("0.01")

    _memory_cache = {}
    _memory_lock = threading.Lock()

    @staticmethod
    def _normalize_currency(currency):
        if not isinstance(currency, str):
            return None
        code = currency.strip().upper()
        if len(code) != 3 or not code.isalpha() or not code.isascii():
            return None
        return code

    @classmethod
    def _cache_key(cls, currency):
        return f"ontrac:fx:{currency}:USD"

    @classmethod
    def _read_entry(cls, currency):
        key = cls._cache_key(currency)

        try:
            entry = cache.get(key)
            if isinstance(entry, dict):
                return entry
        except Exception as exc:
            logger.debug("Django FX cache unavailable: %s", exc)

        with cls._memory_lock:
            entry = cls._memory_cache.get(key)
            return dict(entry) if isinstance(entry, dict) else None

    @classmethod
    def _write_entry(cls, currency, rate, provider_updated_at=None):
        entry = {
            "rate": str(rate),
            "fetched_at": time.time(),
            "provider_updated_at": provider_updated_at,
        }
        key = cls._cache_key(currency)

        with cls._memory_lock:
            cls._memory_cache[key] = entry

        try:
            cache.set(key, entry, timeout=cls.FALLBACK_TTL_SECONDS)
        except Exception as exc:
            logger.debug("Django FX cache unavailable: %s", exc)

    @classmethod
    def _write_unsupported(cls, currency):
        entry = {
            "unsupported": True,
            "fetched_at": time.time(),
        }
        key = cls._cache_key(currency)

        with cls._memory_lock:
            cls._memory_cache[key] = entry

        try:
            cache.set(key, entry, timeout=cls.FRESH_TTL_SECONDS)
        except Exception as exc:
            logger.debug("Django FX cache unavailable: %s", exc)

    @classmethod
    def _entry_is_recent_unsupported(cls, entry):
        if not entry or not entry.get("unsupported"):
            return False
        try:
            age = time.time() - float(entry["fetched_at"])
            return 0 <= age <= cls.FRESH_TTL_SECONDS
        except (KeyError, TypeError, ValueError):
            return False

    @classmethod
    def _entry_rate_if_recent(cls, entry, max_age):
        if not entry:
            return None
        try:
            fetched_at = float(entry["fetched_at"])
            rate = Decimal(str(entry["rate"]))
            age = time.time() - fetched_at
            if rate <= 0 or age < 0 or age > max_age:
                return None
            return rate
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return None

    @classmethod
    def quantize_usd(cls, amount):
        try:
            value = Decimal(str(amount))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if not value.is_finite():
            return None
        return value.quantize(cls.USD_QUANTUM, rounding=ROUND_HALF_UP)

    @classmethod
    def _parse_provider_response(cls, response, currency):
        data = response.json()
        if data.get("result") == "error":
            return None, data.get("error-type") or "provider-error"

        raw_rate = data.get("conversion_rates", {}).get("USD")
        try:
            rate = Decimal(str(raw_rate))
        except (InvalidOperation, TypeError, ValueError):
            return None, "invalid-rate"

        if not rate.is_finite() or rate <= 0:
            return None, "invalid-rate"

        return (
            {
                "rate": rate,
                "provider_updated_at": data.get("time_last_update_unix"),
            },
            None,
        )

    @classmethod
    def get_usd_rate(cls, currency):
        currency = cls._normalize_currency(currency)
        if currency is None:
            return None
        if currency == "USD":
            return Decimal("1")

        cached_entry = cls._read_entry(currency)
        if cls._entry_is_recent_unsupported(cached_entry):
            return None

        fresh_rate = cls._entry_rate_if_recent(cached_entry, cls.FRESH_TTL_SECONDS)
        if fresh_rate is not None:
            return fresh_rate

        api_key = getattr(settings, "EXCHANGE_RATE_API_KEY", "")
        if not api_key:
            logger.error("EXCHANGE_RATE_API_KEY is missing; USD conversion unavailable")
            return None

        url = f"https://v6.exchangerate-api.com/v6/{api_key}/latest/{currency}"
        last_error = None
        transient_failure = False

        for attempt in range(cls.REQUEST_ATTEMPTS):
            try:
                response = requests.get(url, timeout=cls.REQUEST_TIMEOUT)

                # Retry network/server/rate-limit failures. Other HTTP errors are configuration
                # or request problems and should fail closed rather than use a stale price.
                if response.status_code == 429 or response.status_code >= 500:
                    transient_failure = True
                    response.raise_for_status()

                response.raise_for_status()
                parsed, provider_error = cls._parse_provider_response(response, currency)
                if parsed is not None:
                    cls._write_entry(
                        currency,
                        parsed["rate"],
                        provider_updated_at=parsed["provider_updated_at"],
                    )
                    return parsed["rate"]

                last_error = provider_error
                if provider_error in {"unsupported-code", "unknown-code"}:
                    cls._write_unsupported(currency)
                    logger.error("FX conversion rejected for %s: %s", currency, provider_error)
                    return None

                if provider_error in {
                    "malformed-request",
                    "invalid-key",
                    "inactive-account",
                }:
                    logger.error("FX conversion rejected for %s: %s", currency, provider_error)
                    return None

                # Quota/provider response problems are not a trustworthy reason to keep
                # retrying the same request, but a recent verified rate may bridge the outage.
                transient_failure = provider_error in {"quota-reached", "provider-error", "invalid-rate"}
                break

            except (requests.Timeout, requests.ConnectionError) as exc:
                transient_failure = True
                last_error = exc
            except requests.HTTPError as exc:
                last_error = exc
                transient_failure = response.status_code == 429 or response.status_code >= 500
                if not transient_failure:
                    logger.error("FX request rejected for %s: %s", currency, exc)
                    return None
            except (requests.RequestException, ValueError, TypeError) as exc:
                transient_failure = True
                last_error = exc

            if transient_failure and attempt + 1 < cls.REQUEST_ATTEMPTS:
                time.sleep(0.25)

        if transient_failure:
            fallback_rate = cls._entry_rate_if_recent(cached_entry, cls.FALLBACK_TTL_SECONDS)
            if fallback_rate is not None:
                logger.warning(
                    "FX provider temporarily unavailable for %s; using recent verified USD rate: %s",
                    currency,
                    last_error,
                )
                return fallback_rate

        logger.error("FX conversion unavailable for %s: %s", currency, last_error)
        return None

    @classmethod
    def convert_to_usd(cls, amount, currency):
        currency = cls._normalize_currency(currency)
        if currency is None:
            return None

        try:
            decimal_amount = Decimal(str(amount))
        except (InvalidOperation, TypeError, ValueError):
            return None

        if not decimal_amount.is_finite():
            return None
        if currency == "USD":
            return decimal_amount

        rate = cls.get_usd_rate(currency)
        if rate is None:
            return None

        return decimal_amount * rate

    @classmethod
    def convert_to_usd_cents(cls, amount, currency):
        converted = cls.convert_to_usd(amount, currency)
        if converted is None:
            return None
        return cls.quantize_usd(converted)
