"""Signed delivery webhooks, independent of real provider network."""
import base64
import hashlib
import hmac
import json
import time

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Creator, MilaniOutreachLog, MilaniSuppression

KEY = b'private-test-resend-signing-key'
SECRET = 'whsec_' + base64.b64encode(KEY).decode()


@override_settings(MILANI_RESEND_WEBHOOK_SECRET=SECRET,
                   MILANI_OUTREACH_V2_ENABLED=True)
class VerifiedResendWebhookTests(TestCase):
    def setUp(self):
        self.creator = Creator.objects.create(
            name='Test Creator', email='creator@example.invalid', status='Sent')
        self.log = MilaniOutreachLog.objects.create(
            creator=self.creator, status='Sent', subject='Test message',
            smtp_provider='resend_collabs', provider_message_id='provider-accepted-1',
            sendgrid_message_id='b' * 32)
        self.url = reverse('resend_milani_webhook')

    def event(self, event_type='email.delivered', *, provider_id='provider-accepted-1',
              recipient='creator@example.invalid'):
        return {'type': event_type, 'data': {
            'email_id': provider_id, 'to': [recipient], 'subject': 'Test message'}}

    def signed_post(self, payload, *, timestamp=None, signature=None):
        body = json.dumps(payload, separators=(',', ':')).encode()
        stamp = str(int(time.time()) if timestamp is None else timestamp)
        message = 'msg-local-signed-test'
        digest = hmac.new(KEY, message.encode() + b'.' + stamp.encode() +
                          b'.' + body, hashlib.sha256).digest()
        sign = signature if signature is not None else (
            'v1,' + base64.b64encode(digest).decode())
        return self.client.post(self.url, data=body, content_type='application/json',
                                HTTP_SVIX_ID=message, HTTP_SVIX_TIMESTAMP=stamp,
                                HTTP_SVIX_SIGNATURE=sign)

    def test_unauthenticated_post_and_missing_secret_fail_closed(self):
        payload = json.dumps(self.event())
        self.assertEqual(self.client.post(
            self.url, payload, content_type='application/json').status_code, 401)
        with override_settings(MILANI_RESEND_WEBHOOK_SECRET=''):
            self.assertEqual(self.signed_post(self.event()).status_code, 401)
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, 'Sent')

    def test_invalid_signature_and_stale_timestamp_rejected(self):
        self.assertEqual(self.signed_post(
            self.event(), signature='v1,bad-signature').status_code, 401)
        self.assertEqual(self.signed_post(
            self.event(), timestamp=int(time.time()) - 601).status_code, 401)
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, 'Sent')

    def test_valid_delivered_event_updates_existing_row_idempotently(self):
        self.assertEqual(self.signed_post(self.event()).status_code, 200)
        self.assertEqual(self.signed_post(self.event()).status_code, 200)
        self.log.refresh_from_db()
        self.creator.refresh_from_db()
        self.assertEqual(self.log.status, 'Delivered')
        self.assertEqual(self.creator.status, 'Delivered')
        self.assertEqual(MilaniOutreachLog.objects.count(), 1)
        self.assertEqual(MilaniSuppression.objects.count(), 0)

    def test_late_sent_event_does_not_downgrade_delivery(self):
        self.signed_post(self.event())
        self.assertEqual(self.signed_post(self.event('email.sent')).status_code, 200)
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, 'Delivered')

    def test_bounce_suppresses_and_cannot_be_undone_by_later_delivery(self):
        self.assertEqual(self.signed_post(self.event('email.bounced')).status_code, 200)
        self.assertEqual(self.signed_post(self.event()).status_code, 200)
        self.log.refresh_from_db()
        self.creator.refresh_from_db()
        self.assertEqual(self.log.status, 'Bounced')
        self.assertEqual(self.creator.status, 'Bounced')
        self.assertTrue(self.creator.do_not_contact)
        self.assertEqual(MilaniSuppression.objects.count(), 1)

    def test_verified_complaint_suppresses_recipient(self):
        self.assertEqual(self.signed_post(self.event('email.complained')).status_code, 200)
        self.creator.refresh_from_db()
        self.assertTrue(self.creator.do_not_contact)
        self.assertEqual(self.creator.status, 'Reported Spam')
    def test_unknown_message_and_wrong_recipient_do_not_mutate(self):
        for payload in [
            self.event(provider_id='different-provider-id'),
            self.event(recipient='different@example.invalid')
        ]:
            self.assertEqual(self.signed_post(payload).json()['status'], 'ignored')
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, 'Sent')
        self.assertEqual(MilaniOutreachLog.objects.count(), 1)

    def test_reply_status_is_preserved_for_non_failure_event(self):
        self.creator.status = 'Replied'
        self.creator.save(update_fields=['status'])
        self.assertEqual(self.signed_post(self.event()).status_code, 200)
        self.creator.refresh_from_db()
        self.assertEqual(self.creator.status, 'Replied')
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, 'Delivered')

    def test_signed_invalid_json_and_non_post_requests(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.signed_post(['not-an-object']).status_code, 400)
