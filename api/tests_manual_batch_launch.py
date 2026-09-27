"""Manual batches: no email until an explicit, authenticated Start Delivery step."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .milani_batch import OutreachBlocked, confirm_batch, prepare_draft, readiness_error
from .models import (Creator, MilaniBatchSendGate, MilaniEmailVariant,
                     MilaniLaunchBatch, MilaniLaunchRecipient, MilaniOutreachLog,
                     MilaniSuppression, SiteSettings)


@override_settings(
    MILANI_OUTREACH_V2_ENABLED=True,
    MILANI_OUTREACH_BULK_ENABLED=True,
    MILANI_OUTREACH_SEND_ENABLED=False,
    MILANI_OUTREACH_TEST_MODE=False,
    MILANI_PUBLIC_BASE_URL='https://api.ontracourier.us',
    MILANI_SENDER_POSTAL_ADDRESS=('New Milani Group LLC, 10000 W. Washington Blvd, '
                                   'Suite 210, Culver City, CA 90232, United States'),
    RESEND_MILANI_API_KEY='fake-provider-key-for-tests',
)
class ManualBatchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            'batch-admin', 'batch-admin@example.invalid', 'test-password')
        self.staff = User.objects.create_user(
            'staff-user','staff@example.invalid','test-password',is_staff=True)
        SiteSettings.objects.update_or_create(
            pk=1, defaults={'milani_smtp_provider':'resend_collabs'})
        self.people = [
            Creator.objects.create(name='Creator A', email='a@example.invalid',
                                   personalization_note='Your tutorial used natural light.'),
            Creator.objects.create(name='Creator B', email='b@example.invalid'),
            Creator.objects.create(name='Creator C', email='c@example.invalid'),
        ]
        self.variant = MilaniEmailVariant.objects.create(
            name='Reviewed Collaboration', campaign_name='September Outreach',
            subject='Creator collaboration for {name}',
            body='Hi {name},\n\n{personal_line}\n\nWould you like to learn more?',
            approval_state='approved', is_active=True, is_evergreen=True)
        self.home = reverse('admin:milani_batch_home')
        self.client.force_login(self.user)

    def prepare(self, people=None):
        people = self.people[:2] if people is None else people
        with patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            response = self.client.post(self.home, {
                'variant_id':self.variant.pk,
                'creator_ids':[x.pk for x in people]})
            provider.assert_not_called()
        self.assertEqual(response.status_code, 302)
        return MilaniLaunchBatch.objects.latest('created_at')

    def test_home_is_a_real_superuser_admin_page(self):
        r = self.client.get(self.home)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Prepare your next batch')
        self.assertContains(r, 'September Outreach')

    def test_non_superuser_cannot_review_confirm_or_send(self):
        batch = self.prepare()
        self.client.force_login(self.staff)
        for path in [self.home,
                     reverse('admin:milani_batch_review',args=[batch.pk])]:
            self.assertEqual(self.client.get(path).status_code, 403)
        for path in [reverse('admin:milani_batch_confirm',args=[batch.pk]),
                     reverse('admin:milani_batch_step',args=[batch.pk]),
                     reverse('admin:milani_batch_pause',args=[batch.pk])]:
            self.assertEqual(self.client.post(path).status_code, 403)

    def test_prepare_is_a_reviewable_snapshot_without_claims_or_sending(self):
        batch=self.prepare()
        self.assertEqual(batch.status,'draft')
        items=list(batch.recipients.all())
        self.assertEqual(len(items),2)
        self.assertEqual([r.status for r in items],['pending','pending'])
        self.assertIn('natural light',items[0].body_snapshot)
        self.assertNotIn('{personal_line}',items[1].body_snapshot)
        self.assertFalse(MilaniOutreachLog.objects.exists())
        r=self.client.get(reverse('admin:milani_batch_review',args=[batch.pk]))
        self.assertContains(r,items[0].subject_snapshot)
        self.assertContains(r,'Confirm without sending')

    def test_global_sender_disabled_prevents_legacy_admin_provider_calls(self):
        with patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            from .milani_email_service import send_milani_outreach_email
            self.assertFalse(send_milani_outreach_email(self.people[0]))
            provider.assert_not_called()

    def test_confirm_requires_exact_count_phrase_and_does_not_send(self):
        batch=self.prepare()
        url=reverse('admin:milani_batch_confirm',args=[batch.pk])
        with patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            self.assertEqual(self.client.post(url,{'confirmation':'LAUNCH 1'}).status_code,302)
            batch.refresh_from_db()
            self.assertEqual(batch.status,'draft')
            self.assertEqual(self.client.post(url,{'confirmation':'LAUNCH 2'}).status_code,302)
            provider.assert_not_called()
        batch.refresh_from_db()
        self.assertEqual(batch.status,'running')
        self.assertIsNotNone(batch.confirmed_at)
        self.assertEqual(MilaniOutreachLog.objects.count(),0)

    def test_one_browser_step_sends_only_one_then_enforces_pacing(self):
        batch=self.prepare()
        confirm_batch(batch,self.user,'LAUNCH 2')
        url=reverse('admin:milani_batch_step',args=[batch.pk])
        with patch('api.milani_email_service.resend_sdk.Emails.send',
                   side_effect=[{'id':'provider-one'},{'id':'provider-two'}]) as provider:
            response=self.client.post(url)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['state'],'sent')
            self.assertEqual(provider.call_count,1)
            self.assertEqual(provider.call_args.args[0]['to'],['a@example.invalid'])
            self.assertEqual(self.client.post(url).json()['state'],'wait')
            self.assertEqual(provider.call_count,1)
            MilaniBatchSendGate.objects.filter(pk=1).update(
                last_started_at=timezone.now()-timedelta(seconds=31))
            self.assertEqual(self.client.post(url).json()['state'],'sent')
            self.assertEqual(provider.call_count,2)
            self.assertEqual(provider.call_args.args[0]['to'],['b@example.invalid'])
            MilaniBatchSendGate.objects.filter(pk=1).update(
                last_started_at=timezone.now()-timedelta(seconds=31))
            self.assertEqual(self.client.post(url).json()['state'],'complete')
        batch.refresh_from_db()
        self.assertEqual(batch.status,'completed')
        self.assertEqual(list(batch.recipients.values_list('status',flat=True)),
                         ['sent','sent'])
        self.assertEqual(MilaniOutreachLog.objects.count(),2)

    def test_changed_creator_pauses_for_review_without_contact(self):
        batch=self.prepare([self.people[0]])
        confirm_batch(batch,self.user,'LAUNCH 1')
        self.people[0].personalization_note='This is changed since approval.'
        self.people[0].save(update_fields=['personalization_note'])
        with patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            result=self.client.post(reverse('admin:milani_batch_step',args=[batch.pk]))
            self.assertEqual(result.json()['state'],'refresh_required')
            provider.assert_not_called()
        batch.refresh_from_db()
        self.assertEqual(batch.status,'paused')
        self.assertEqual(batch.recipients.first().status,'pending')

    def test_suppressed_creator_is_excluded_at_preparation(self):
        MilaniSuppression.objects.create(email='b@example.invalid')
        batch=self.prepare()
        self.assertEqual(list(batch.recipients.values_list('status',flat=True)),
                         ['pending','blocked'])
        confirm_batch(batch,self.user,'LAUNCH 1')
        self.assertEqual(batch.recipients.filter(status='blocked').count(),1)

    def test_provider_uncertainty_stops_batch_and_never_auto_retries(self):
        batch=self.prepare()
        confirm_batch(batch,self.user,'LAUNCH 2')
        url=reverse('admin:milani_batch_step',args=[batch.pk])
        with patch('api.milani_email_service.resend_sdk.Emails.send',
                   side_effect=ConnectionError('provider outcome unknown')) as provider:
            self.assertEqual(self.client.post(url).json()['state'],'needs_review')
            self.assertEqual(provider.call_count,1)
            self.assertEqual(self.client.post(url).status_code,409)
            self.assertEqual(provider.call_count,1)
        self.assertEqual(batch.recipients.first().status,'needs_review')

    def test_no_email_after_pause(self):
        batch=self.prepare()
        confirm_batch(batch,self.user,'LAUNCH 2')
        self.client.post(reverse('admin:milani_batch_pause',args=[batch.pk]))
        with patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            self.assertEqual(self.client.post(
                reverse('admin:milani_batch_step',args=[batch.pk])).status_code,409)
            provider.assert_not_called()

    def test_no_campaign_or_no_provider_fails_closed(self):
        self.variant.approval_state='draft'
        self.variant.save(update_fields=['approval_state'])
        response=self.client.get(self.home)
        self.assertContains(response,'No currently eligible approved campaign')
        with override_settings(RESEND_MILANI_API_KEY=''):
            self.assertIn('credential',readiness_error())
        with override_settings(MILANI_OUTREACH_BULK_ENABLED=False):
            self.assertIsNotNone(readiness_error())

    def test_csrf_protects_launching_and_one_step(self):
        from django.test import Client
        batch=self.prepare()
        confirm_batch(batch,self.user,'LAUNCH 2')
        strict=Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        with patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            self.assertEqual(strict.post(
                reverse('admin:milani_batch_step',args=[batch.pk])).status_code,403)
            provider.assert_not_called()

    def test_provider_boundary_rejects_unscoped_call_even_with_bulk_enabled(self):
        from .milani_email_service import _dispatch_outreach
        with patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            with self.assertRaisesRegex(ValueError,'no verified manual batch scope'):
                _dispatch_outreach(self.people[0], {
                    'to':[self.people[0].email], 'subject':'Not approved'})
            provider.assert_not_called()

    def test_pacing_applies_across_two_different_manual_batches(self):
        first=self.prepare([self.people[0]])
        second=self.prepare([self.people[1]])
        confirm_batch(first,self.user,'LAUNCH 1')
        confirm_batch(second,self.user,'LAUNCH 1')
        with patch('api.milani_email_service.resend_sdk.Emails.send',
                   return_value={'id':'first-provider-confirmed'}) as provider:
            self.assertEqual(self.client.post(
                reverse('admin:milani_batch_step',args=[first.pk])).json()['state'],'sent')
            self.assertEqual(self.client.post(
                reverse('admin:milani_batch_step',args=[second.pk])).json()['state'],'wait')
            self.assertEqual(provider.call_count,1)
        self.assertFalse(second.recipients.filter(status='sent').exists())

    def test_step_requires_post_and_cannot_start_unconfirmed_draft(self):
        batch=self.prepare([self.people[0]])
        url=reverse('admin:milani_batch_step',args=[batch.pk])
        self.assertEqual(self.client.get(url).status_code,405)
        with patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            self.assertEqual(self.client.post(url).status_code,409)
            provider.assert_not_called()

    def test_verified_sender_required_before_confirmation(self):
        batch=self.prepare([self.people[0]])
        url=reverse('admin:milani_batch_confirm',args=[batch.pk])
        with override_settings(MILANI_SENDER_POSTAL_ADDRESS=''):
            self.assertEqual(self.client.post(url,{'confirmation':'LAUNCH 1'}).status_code,302)
        batch.refresh_from_db()
        self.assertEqual(batch.status,'draft')
        self.assertFalse(MilaniOutreachLog.objects.exists())

    def test_history_view_and_resume_require_new_confirmation_without_sending(self):
        batch=self.prepare([self.people[0]])
        confirm_batch(batch,self.user,'LAUNCH 1')
        self.client.post(reverse('admin:milani_batch_pause',args=[batch.pk]))
        batch.refresh_from_db()
        self.assertEqual(batch.status,'paused')
        history=self.client.get(reverse('admin:api_milanilaunchbatch_changelist'))
        self.assertEqual(history.status_code,200)
        self.assertContains(history,'September Outreach')
        resume=reverse('admin:milani_batch_resume',args=[batch.pk])
        with patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            self.client.post(resume,{'confirmation':'RESUME 2'})
            batch.refresh_from_db()
            self.assertEqual(batch.status,'paused')
            self.client.post(resume,{'confirmation':'RESUME 1'})
            batch.refresh_from_db()
            self.assertEqual(batch.status,'running')
            provider.assert_not_called()

    def test_uncertain_paused_batch_cannot_resume_without_reconciliation(self):
        batch=self.prepare([self.people[0]])
        confirm_batch(batch,self.user,'LAUNCH 1')
        MilaniLaunchRecipient.objects.filter(batch=batch).update(status='needs_review')
        self.client.post(reverse('admin:milani_batch_pause',args=[batch.pk]))
        with patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            self.client.post(reverse('admin:milani_batch_resume',args=[batch.pk]),
                             {'confirmation':'RESUME 0'})
            provider.assert_not_called()
        batch.refresh_from_db()
        self.assertEqual(batch.status,'paused')
