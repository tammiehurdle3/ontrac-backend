from urllib.parse import urlparse
from unittest.mock import patch
from django.test import TestCase, override_settings
from .milani_email_service import _build_html_body, _outreach_headers
from .milani_unsubscribe import unsubscribe_url
from .milani_outreach_v2 import OutreachBlocked, check_recipient
from .models import Creator, MilaniOutreachLog, MilaniSuppression


@override_settings(
    MILANI_OUTREACH_V2_ENABLED=True,
    MILANI_OUTREACH_SEND_ENABLED=False,
    MILANI_PUBLIC_BASE_URL='http://localhost:8012',
    MILANI_SENDER_POSTAL_ADDRESS='LOCAL PREVIEW ONLY - NOT VERIFIED - DO NOT SEND',
)
class SignedUnsubscribeTests(TestCase):
    def setUp(self):
        self.creator=Creator.objects.create(name='Test Creator',email='unsubscribe@example.invalid')
        self.message_id='a'*32
        self.log=MilaniOutreachLog.objects.create(
            creator=self.creator,status='Sent',subject='Test Only',
            sendgrid_message_id=self.message_id)

    def url(self):
        return unsubscribe_url(self.message_id).removeprefix('http://localhost:8012')

    def test_signed_link_has_no_recipient_email_in_url(self):
        self.assertNotIn(self.creator.email, self.url())
        self.assertIn('/api/milani/unsubscribe/?token=', self.url())

    def test_get_is_safe_against_email_security_prefetch(self):
        response=self.client.get(self.url())
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Confirm unsubscribe')
        self.creator.refresh_from_db()
        self.assertFalse(self.creator.do_not_contact)
        self.assertEqual(MilaniSuppression.objects.count(),0)

    def test_signed_post_stops_future_outreach(self):
        response=self.client.post(self.url())
        self.assertEqual(response.status_code,200)
        self.creator.refresh_from_db()
        self.assertTrue(self.creator.do_not_contact)
        self.assertTrue(MilaniSuppression.objects.filter(
            email=self.creator.email).exists())
        with self.assertRaises(OutreachBlocked):
            check_recipient(self.creator)

    def test_unsubscribe_is_idempotent(self):
        self.client.post(self.url())
        self.assertEqual(self.client.post(self.url()).status_code,200)
        self.assertEqual(MilaniSuppression.objects.count(),1)

    def test_tampered_token_is_rejected(self):
        url=self.url()
        self.assertEqual(self.client.post(url[:-1]+'X').status_code,400)
        self.assertFalse(MilaniSuppression.objects.exists())

    def test_get_may_not_store_personal_data(self):
        resp=self.client.get(self.url())
        self.assertNotIn(self.creator.email,resp.content.decode())
        self.assertEqual(resp['Cache-Control'],'no-store')
        self.assertEqual(resp['Referrer-Policy'],'no-referrer')

    def test_markup_and_headers_link_to_same_signed_endpoint(self):
        html=_build_html_body('Hi <Test>',self.message_id,'preview@example.invalid')
        headers=_outreach_headers(self.message_id,'preview@example.invalid',v2=True)
        url=unsubscribe_url(self.message_id)
        self.assertIn(url,html)
        self.assertIn(url,headers['List-Unsubscribe'])
        self.assertEqual(headers['List-Unsubscribe-Post'],'List-Unsubscribe=One-Click')
        self.assertIn('Hi &lt;Test&gt;',html)
        self.assertIn('LOCAL PREVIEW ONLY',html)

    @override_settings(ENVIRONMENT='production')
    def test_production_requires_https_unsubscribe_url(self):
        from .milani_unsubscribe import unsubscribe_url
        with self.assertRaises(ValueError):
            unsubscribe_url(self.message_id)

    @override_settings(MILANI_PUBLIC_BASE_URL='')
    def test_missing_unsubscribe_host_fails_closed(self):
        from .milani_unsubscribe import unsubscribe_url
        with self.assertRaises(ValueError):
            unsubscribe_url(self.message_id)

    @override_settings(MILANI_OUTREACH_SEND_ENABLED=True)
    def test_no_live_send_used_by_unsubscribe(self):
        with patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            self.client.post(self.url())
            provider.assert_not_called()
