"""Request middleware for Bachs merchant-specific webhook hosts."""
from django.conf import settings


class BachsWebhookHostMiddleware:
    """Accept Bachs webhooks on hosts recorded on a merchant account.

    Django normally rejects an unknown ``Host`` with ``DisallowedHost`` before a
    view runs. Each merchant can store the hostname Bachs posts to (for example
    ``hooks.milani-cosmetics.com``), so adding a merchant stays an admin-only
    operation. Only the Bachs webhook path can use this, and only hosts that
    belong to an active merchant account are accepted; every other request still
    goes through Django's normal host validation.
    """

    WEBHOOK_PATH = '/api/webhooks/bachs/'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == self.WEBHOOK_PATH:
            raw_host = request.META.get('HTTP_HOST', '')
            hostname = self._hostname(raw_host)
            if hostname and not self._is_allowed(hostname) and self._is_merchant_host(hostname):
                request.META['HTTP_X_ORIGINAL_HOST'] = raw_host
                request.META['HTTP_HOST'] = self._fallback_host()
        return self.get_response(request)

    @staticmethod
    def _hostname(raw_host):
        return (raw_host or '').split(':', 1)[0].strip().lower()

    @staticmethod
    def _is_allowed(hostname):
        for pattern in settings.ALLOWED_HOSTS or []:
            pattern = str(pattern)
            if pattern == '*':
                return True
            if pattern.startswith('.'):
                if hostname == pattern[1:] or hostname.endswith(pattern):
                    return True
            elif hostname == pattern:
                return True
        return False

    @staticmethod
    def _is_merchant_host(hostname):
        from .models import BachsMerchantAccount

        return BachsMerchantAccount.objects.filter(
            is_active=True, webhook_host__iexact=hostname
        ).exists()

    @staticmethod
    def _fallback_host():
        for pattern in settings.ALLOWED_HOSTS or []:
            pattern = str(pattern)
            if pattern and pattern != '*' and not pattern.startswith('.'):
                return pattern
        return 'localhost'
