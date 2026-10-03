"""No orphan footer punctuation on narrow/mobile preview; no provider calls."""
import base64
import re
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .milani_email_service import _build_html_body, _outreach_headers
from .milani_unsubscribe import unsubscribe_url
from .models import Creator, MilaniEmailVariant, SiteSettings


@override_settings(
    MILANI_OUTREACH_V2_ENABLED=True,
    MILANI_OUTREACH_TEST_MODE=False,
    MILANI_PUBLIC_BASE_URL='https://api.ontracourier.us',
    MILANI_SENDER_POSTAL_ADDRESS=(
        'New Milani Group LLC, 10000 W. Washington Blvd, Suite 210, '
        'Culver City, CA 90232, United States'),
    ENVIRONMENT='production',
)
class MobileEmailFooterTests(TestCase):
    MESSAGE = 'f' * 32

    def test_live_html_link_has_own_line_without_stranded_period(self):
        rendered = _build_html_body(
            'Hi Tammie,\n\nA creator collaboration.',
            self.MESSAGE, 'diana@milanicollabs.com',
        )
        url = unsubscribe_url(self.MESSAGE)
        self.assertIn(f'href="{url}"', rendered)
        self.assertIn(
            'Unsubscribe</a><span class="postal-address" '
            'style="display:block;margin-top:12px;line-height:1.5;'
            'overflow-wrap:break-word;">Milani Cosmetics, Inc.', rendered,
        )
        self.assertIn(
            'Milani Cosmetics, Inc., 10000 W. Washington Blvd, Suite 210, '
            'Culver City, CA 90232, United States', rendered,
        )
        self.assertNotIn('New Milani Group LLC', rendered)
        self.assertNotIn('Unsubscribe</a>.', rendered)
        self.assertNotIn('unsubscribe here</a>.', rendered)
        self.assertRegex(
            rendered, r'<span style="display:block;line-height:1\.55;">'
                      r'You may stop future creator outreach at any time\.</span>'
        )
        self.assertIn('max-width: 540px', rendered)
        self.assertIn('padding: 0 24px', rendered)
        self.assertIn('prefers-color-scheme: dark', rendered)
        self.assertIn('overflow-wrap:break-word;', rendered)
        self.assertIn('/api/webhooks/milani-open/?mid=', rendered)
        self.assertIn('aria-hidden="true"', rendered)
        self.assertIn('max-height:0;overflow:hidden;line-height:0;font-size:0;', rendered)
        self.assertIn('opacity:0;mso-hide:all;', rendered)
        headers = _outreach_headers(
            self.MESSAGE, 'diana@milanicollabs.com', v2=True,
        )
        self.assertIn(url, headers['List-Unsubscribe'])
        self.assertEqual(headers['List-Unsubscribe-Post'], 'List-Unsubscribe=One-Click')

    @override_settings(MILANI_OUTREACH_TEST_MODE=True, ENVIRONMENT='local')
    def test_self_addressed_footer_has_no_orphan_period(self):
        html = _build_html_body(
            'Hi,\n\nThis is a test.', self.MESSAGE, 'diana@milanicollabs.com')
        self.assertIn('Single-recipient test email.', html)
        self.assertIn('Reply to unsubscribe</a>', html)
        self.assertNotIn('unsubscribe</a>.', html)
        self.assertNotIn('postal-address', html)

    @override_settings(MILANI_OUTREACH_V2_ENABLED=False)
    def test_legacy_footer_has_no_orphan_period(self):
        html = _build_html_body(
            'Hi,\n\nOlder message.', self.MESSAGE, 'diana@milanicollabs.com')
        self.assertIn('Unsubscribe here</a>', html)
        self.assertNotIn('Unsubscribe here</a>.', html)

    def test_actual_admin_mobile_and_desktop_share_fixed_live_html(self):
        owner = User.objects.create_superuser(
            'footer-test-admin', 'admin@example.invalid', 'test-not-a-login')
        creator = Creator.objects.create(
            name='Tammie', email='tammie@example.invalid')
        variant = MilaniEmailVariant.objects.create(
            name='Evergreen Test', campaign_name='Creator introductions',
            subject='A collaboration with {name}',
            body='Hi {name},\n\n{greeting}\n\nA creator collaboration.\n\nDiana',
            approval_state='approved', is_active=True, is_evergreen=True)
        SiteSettings.objects.update_or_create(
            pk=1, defaults={'milani_smtp_provider': 'resend_collabs'})
        self.client.force_login(owner)
        with patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            page = self.client.get(
                reverse('admin:milaniemailvariant_preview', args=[variant.pk]),
                {'creator_id': creator.pk},
            )
            provider.assert_not_called()
        self.assertEqual(page.status_code, 200)
        source = page.content.decode('utf-8')
        match = re.search(r'const EMAIL_HTML_LIGHT = atob\("([^"]+)"\)', source)
        self.assertIsNotNone(match)
        embedded_html = base64.b64decode(match.group(1)).decode()
        self.assertIn('Unsubscribe</a><span class="postal-address"', embedded_html)
        self.assertNotIn('Unsubscribe</a>.', embedded_html)
        self.assertNotIn('/api/webhooks/milani-open/', embedded_html)
        self.assertNotIn('<img src=', embedded_html)
        self.assertIn('id="emailFrame"', source)
        self.assertIn('id="iphoneFrame"', source)
        self.assertIn('doc.write(buildEmailHtml(dark, name));', source)
