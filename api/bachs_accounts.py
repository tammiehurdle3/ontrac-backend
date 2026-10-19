"""Resolution and encryption helpers for Bachs merchant accounts.

Admin-managed accounts live in the database with their API key and webhook
secret encrypted by ``BACHS_CREDENTIALS_KEY``. Environment accounts continue to
work and are used as a fallback, so deployments without the key keep running.
"""
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


class CredentialsKeyMissing(ImproperlyConfigured):
    """Raised when merchant credentials exist but no encryption key is configured."""


def credentials_key_configured():
    return bool(getattr(settings, 'BACHS_CREDENTIALS_KEY', ''))


def _fernet():
    key = getattr(settings, 'BACHS_CREDENTIALS_KEY', '')
    if not key:
        raise CredentialsKeyMissing(
            'BACHS_CREDENTIALS_KEY is not configured; merchant credentials cannot be read or stored.'
        )
    try:
        return Fernet(key if isinstance(key, bytes) else key.encode('utf-8'))
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured('BACHS_CREDENTIALS_KEY is not a valid Fernet key.') from exc


def encrypt_secret(value):
    value = (value or '').strip()
    if not value:
        return ''
    return _fernet().encrypt(value.encode('utf-8')).decode('ascii')


def decrypt_secret(token):
    token = (token or '').strip()
    if not token:
        return ''
    try:
        return _fernet().decrypt(token.encode('ascii')).decode('utf-8')
    except InvalidToken as exc:
        raise ImproperlyConfigured(
            'A stored Bachs credential could not be decrypted; check BACHS_CREDENTIALS_KEY.'
        ) from exc


def environment_accounts():
    accounts = getattr(settings, 'BACHS_ACCOUNTS', {}) or {}
    if not isinstance(accounts, dict):
        return {}
    return {
        str(key): dict(value)
        for key, value in accounts.items()
        if isinstance(value, dict)
    }


def account_choices():
    """(key, label) pairs for the Site Settings selector: env accounts plus admin accounts."""
    choices = [
        (key, str(value.get('label') or key))
        for key, value in environment_accounts().items()
    ]
    known = {key for key, _label in choices}

    from .models import BachsMerchantAccount

    for merchant in BachsMerchantAccount.objects.filter(is_active=True).order_by('label'):
        if merchant.key in known:
            choices = [(key, label) for key, label in choices if key != merchant.key]
        choices.append((merchant.key, merchant.label))
        known.add(merchant.key)
    return choices


def get_account_keys():
    return [key for key, _label in account_choices()]


def get_account_config(key):
    """Resolve one account, preferring an admin-managed row over the env fallback."""
    if not key:
        return None

    from .models import BachsMerchantAccount

    merchant = BachsMerchantAccount.objects.filter(key=key, is_active=True).first()
    if merchant is not None:
        return merchant.as_config()
    return environment_accounts().get(str(key))
