"""Refresh/re-review never sends, preserves settled history and requires fresh approval."""
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.signing import SignatureExpired
from django.test import TestCase, override_settings
from django.urls import reverse

from .milani_batch import (
    OutreachBlocked, confirm_batch, prepare_draft, resume_batch, step_once,
)
from .milani_batch_refresh import apply_refresh, preview_refresh
from .models import (
    Creator, MilaniEmailVariant, MilaniLaunchBatch,
    MilaniLaunchRecipient, MilaniOutreachLog, MilaniSuppression, SiteSettings,
)

LA = ZoneInfo('America/Los_Angeles')
FRIDAY = datetime(2026, 9, 25, 12, tzinfo=LA)
MONDAY = datetime(2026, 9, 28, 12, tzinfo=LA)
TUESDAY = datetime(2026, 9, 29, 12, tzinfo=LA)


@override_settings(
    MILANI_OUTREACH_V2_ENABLED=True,
    MILANI_OUTREACH_BULK_ENABLED=True,
    MILANI_OUTREACH_SEND_ENABLED=False,
    MILANI_OUTREACH_TEST_MODE=False,
    MILANI_PUBLIC_BASE_URL='https://api.ontracourier.us',
    MILANI_SENDER_POSTAL_ADDRESS='New Milani Group LLC, Culver City, CA 90232',
    RESEND_MILANI_API_KEY='fake-review-test-key',
)
class ReReviewTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            'owner','owner@example.invalid','test-password')
        self.other = User.objects.create_superuser(
            'other','other@example.invalid','test-password')
        self.staff = User.objects.create_user(
            'staff','staff@example.invalid','test-password',is_staff=True)
        SiteSettings.objects.update_or_create(
            pk=1,defaults={'milani_smtp_provider':'resend_collabs'})
        self.one = Creator.objects.create(
            name='Creator One',email='one@example.invalid',
            personalization_note='Your lighting tutorial was precise.')
        self.two = Creator.objects.create(
            name='Creator Two',email='two@example.invalid')
        with patch('django.utils.timezone.now',return_value=FRIDAY):
            self.variant = MilaniEmailVariant.objects.create(
                name='Reviewed evergreen collaboration',
                campaign_name='Creator introductions',
                subject='A collaboration, {name}',
                body='Hi {name},\n\n{greeting}\n\n{personal_line}\n\nLet us talk.\n\nDiana',
                approval_state='approved',is_active=True,is_evergreen=True)
        self.client.force_login(self.owner)

    def batch(self, *, people=None, created_at=FRIDAY):
        with patch('django.utils.timezone.now',return_value=created_at), \
             patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            batch=prepare_draft(
                creator_ids=[p.pk for p in (people or [self.one,self.two])],
                variant=self.variant,operator=self.owner)
            provider.assert_not_called()
        return batch

    def fresh_preview(self,batch,at=MONDAY):
        with patch('django.utils.timezone.now',return_value=at), \
             patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            result=preview_refresh(batch,self.owner)
            provider.assert_not_called()
        return result

    def approve(self,batch,token,at=MONDAY):
        with patch('django.utils.timezone.now',return_value=at), \
             patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            result=apply_refresh(batch.pk,self.owner,token)
            provider.assert_not_called()
        return result

    def test_friday_to_monday_exact_preview_and_manual_apply(self):
        batch=self.batch()
        old=list(batch.recipients.values_list('body_snapshot',flat=True))
        self.assertTrue(all('Friday' in b or 'good week' in b for b in old))
        preview=self.fresh_preview(batch)
        self.assertEqual(preview['changed_count'],2)
        self.assertTrue(all(r['status_changed'] is False for r in preview['rows']))
        self.assertTrue(all(r['copy_changed'] for r in preview['rows']))
        self.assertEqual(list(batch.recipients.values_list('body_snapshot',flat=True)),old)
        self.assertFalse(MilaniOutreachLog.objects.exists())
        updated=self.approve(batch,preview['token'])
        self.assertEqual(updated['changed'],2)
        batch.refresh_from_db()
        self.assertEqual(batch.status,'draft')
        text=list(batch.recipients.values_list('body_snapshot',flat=True))
        self.assertTrue(all('week' in b.lower() for b in text))
        self.assertNotEqual(old,text)
        self.assertFalse(MilaniOutreachLog.objects.exists())
        with patch('django.utils.timezone.now',return_value=MONDAY), \
             patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            confirm_batch(batch,self.owner,'LAUNCH 2')
            provider.assert_not_called()
        batch.refresh_from_db()
        self.assertEqual(batch.status,'running')

    def test_get_preview_renders_actual_diff_without_persisting(self):
        batch=self.batch()
        url=reverse('admin:milani_batch_refresh',args=[batch.pk])
        before=list(batch.recipients.values_list('body_snapshot',flat=True))
        with patch('django.utils.timezone.now',return_value=MONDAY), \
             patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            response=self.client.get(url)
            self.assertEqual(response.status_code,200)
            self.assertContains(response,'Previously approved snapshot')
            self.assertContains(response,'Newly checked version')
            self.assertContains(response,'REFRESH 2')
            provider.assert_not_called()
        self.assertEqual(
            list(batch.recipients.values_list('body_snapshot',flat=True)),before)

    def test_apply_requires_exact_confirmation_and_never_sends(self):
        batch=self.batch()
        p=self.fresh_preview(batch)
        url=reverse('admin:milani_batch_refresh_apply',args=[batch.pk])
        original=list(batch.recipients.values_list('body_snapshot',flat=True))
        with patch('django.utils.timezone.now',return_value=MONDAY), \
             patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            response=self.client.post(url,{'confirmation':'REFRESH 1',
                                            'refresh_token':p['token']})
            self.assertEqual(response.status_code,302)
            provider.assert_not_called()
        self.assertEqual(list(batch.recipients.values_list('body_snapshot',flat=True)),
                         original)
        self.approve(batch,p['token'])
        self.assertNotEqual(list(batch.recipients.values_list('body_snapshot',flat=True)),
                            original)

    def test_signed_preview_rejects_edits_after_review(self):
        batch=self.batch()
        token=self.fresh_preview(batch)['token']
        self.one.personalization_note='A different creator observation.'
        self.one.save(update_fields=['personalization_note'])
        with self.assertRaisesRegex(OutreachBlocked,'changed after the preview'):
            self.approve(batch,token)
        self.assertIn('precise',batch.recipients.first().body_snapshot)

    def test_expired_or_tampered_preview_never_changes_a_snapshot(self):
        batch=self.batch()
        token=self.fresh_preview(batch)['token']
        with self.assertRaisesRegex(OutreachBlocked,'expired or was altered'):
            self.approve(batch,token+'tampered')
        with patch('api.milani_batch_refresh.signing.loads',
                   side_effect=SignatureExpired('expired')):
            with self.assertRaisesRegex(OutreachBlocked,'expired or was altered'):
                self.approve(batch,token)
        self.assertEqual(batch.status,'draft')
        self.assertFalse(MilaniOutreachLog.objects.exists())

    def test_los_angeles_date_change_rejects_previously_signed_preview(self):
        batch=self.batch()
        token=self.fresh_preview(batch)['token']
        with self.assertRaisesRegex(OutreachBlocked,'Los Angeles date changed'):
            self.approve(batch,token,TUESDAY)
        self.assertIn('Friday',batch.recipients.first().body_snapshot)

    def test_token_belongs_to_exact_owner_and_batch(self):
        batch=self.batch()
        other_batch=self.batch(people=[self.one])
        token=self.fresh_preview(batch)['token']
        with self.assertRaisesRegex(OutreachBlocked,'different batch or operator'):
            with patch('django.utils.timezone.now',return_value=MONDAY):
                apply_refresh(other_batch.pk,self.owner,token)
        with self.assertRaisesRegex(OutreachBlocked,'different batch or operator'):
            with patch('django.utils.timezone.now',return_value=MONDAY):
                apply_refresh(batch.pk,self.other,token)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(
            reverse('admin:milani_batch_refresh',args=[batch.pk])).status_code,403)

    def test_suppression_between_preview_and_apply_causes_re_review(self):
        batch=self.batch()
        first=self.fresh_preview(batch)
        MilaniSuppression.objects.create(email=self.one.email)
        with self.assertRaisesRegex(OutreachBlocked,'changed after the preview'):
            self.approve(batch,first['token'])
        new=self.fresh_preview(batch)
        self.assertTrue(new['rows'][0]['status_changed'])
        self.assertEqual(new['rows'][0]['revised']['status'],'blocked')
        result=self.approve(batch,new['token'])
        self.assertGreater(result['changed'],0)
        one=batch.recipients.get(creator=self.one)
        self.assertEqual(one.status,'blocked')
        self.assertFalse(one.subject_snapshot)
        self.assertFalse(MilaniOutreachLog.objects.exists())

    def test_changed_recipient_address_requires_explicit_review(self):
        batch=self.batch(people=[self.one])
        token=self.fresh_preview(batch)['token']
        self.one.email='new-one@example.invalid'
        self.one.save(update_fields=['email'])
        with self.assertRaisesRegex(OutreachBlocked,'changed after the preview'):
            self.approve(batch,token)
        current=self.fresh_preview(batch)
        self.assertTrue(current['rows'][0]['email_changed'])
        self.approve(batch,current['token'])
        self.assertEqual(batch.recipients.first().recipient_email_snapshot,
                         'new-one@example.invalid')

    def test_updated_campaign_revision_is_visible_and_never_silent(self):
        batch=self.batch()
        self.variant.campaign_name='Revised campaign approved by operator'
        with patch('django.utils.timezone.now',return_value=MONDAY):
            self.variant.save()
        preview=self.fresh_preview(batch)
        self.assertTrue(preview['campaign_changed'])
        self.assertEqual(preview['new_campaign_name'],self.variant.campaign_name)
        self.approve(batch,preview['token'])
        batch.refresh_from_db()
        self.assertEqual(batch.name,self.variant.campaign_name)
        self.assertEqual(batch.variant_revision_at,self.variant.updated_at)

    def test_expired_dated_campaign_cannot_be_refreshed(self):
        batch=self.batch()
        self.variant.is_evergreen=False
        self.variant.starts_on=FRIDAY.date()
        self.variant.ends_on=FRIDAY.date()
        self.variant.save()
        with self.assertRaisesRegex(OutreachBlocked,'not eligible'):
            self.fresh_preview(batch)
        self.assertIn('Friday',batch.recipients.first().body_snapshot)

    def test_paused_partial_batch_never_mutates_a_sent_recipient(self):
        batch=self.batch()
        with patch('django.utils.timezone.now',return_value=FRIDAY):
            confirm_batch(batch,self.owner,'LAUNCH 2')
        first=batch.recipients.order_by('position').first()
        first.status='sent'
        first.processed_at=FRIDAY
        first.save(update_fields=['status','processed_at'])
        batch.status='paused'
        batch.save(update_fields=['status','updated_at'])
        old=(first.status,first.subject_snapshot,first.body_snapshot,
             first.recipient_email_snapshot,first.processed_at)
        preview=self.fresh_preview(batch)
        self.assertEqual(len(preview['rows']),1)
        self.approve(batch,preview['token'])
        first.refresh_from_db()
        self.assertEqual((first.status,first.subject_snapshot,first.body_snapshot,
                          first.recipient_email_snapshot,first.processed_at),old)
        batch.refresh_from_db()
        self.assertEqual(batch.status,'paused')
        with patch('django.utils.timezone.now',return_value=MONDAY), \
             patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            resume_batch(batch.pk,self.owner,'RESUME 1')
            provider.assert_not_called()

    def test_unknown_provider_outcome_cannot_be_refreshed(self):
        batch=self.batch()
        batch.status='paused'
        batch.save(update_fields=['status','updated_at'])
        batch.recipients.filter(creator=self.one).update(status='needs_review')
        with self.assertRaisesRegex(OutreachBlocked,'unknown provider outcome'):
            self.fresh_preview(batch)
        self.assertEqual(batch.recipients.get(creator=self.one).status,'needs_review')

    def test_monday_auto_pauses_stale_delivery_until_refreshed_and_resumed(self):
        batch=self.batch(people=[self.one])
        with patch('django.utils.timezone.now',return_value=FRIDAY):
            confirm_batch(batch,self.owner,'LAUNCH 1')
        with patch('django.utils.timezone.now',return_value=MONDAY), \
             patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            result=step_once(batch.pk,self.owner)
            provider.assert_not_called()
        self.assertEqual(result['state'],'refresh_required')
        batch.refresh_from_db()
        self.assertEqual(batch.status,'paused')
        self.assertEqual(batch.recipients.first().status,'pending')
        self.approve(batch,self.fresh_preview(batch)['token'])
        with patch('django.utils.timezone.now',return_value=MONDAY), \
             patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            resume_batch(batch.pk,self.owner,'RESUME 1')
            provider.assert_not_called()

    def test_blocked_recipient_can_become_pending_only_after_re_review(self):
        MilaniSuppression.objects.create(email=self.one.email)
        batch=self.batch(people=[self.one])
        self.assertEqual(batch.recipients.first().status,'blocked')
        MilaniSuppression.objects.filter(email=self.one.email).delete()
        preview=self.fresh_preview(batch)
        self.assertEqual(preview['rows'][0]['revised']['status'],'pending')
        self.approve(batch,preview['token'])
        self.assertEqual(batch.recipients.first().status,'pending')
        self.assertFalse(MilaniOutreachLog.objects.exists())

    def test_confirmation_the_next_week_requires_refresh_first(self):
        batch=self.batch(people=[self.one])
        with patch('django.utils.timezone.now',return_value=MONDAY), \
             patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            with self.assertRaisesRegex(OutreachBlocked,'Refresh & Re-review'):
                confirm_batch(batch,self.owner,'LAUNCH 1')
            provider.assert_not_called()
        batch.refresh_from_db()
        self.assertEqual(batch.status,'draft')
        self.assertIsNone(batch.confirmed_at)

    def test_campaign_edited_after_preparation_requires_review_before_confirmation(self):
        batch=self.batch(people=[self.one])
        self.variant.campaign_name='Newly approved campaign title'
        with patch('django.utils.timezone.now',return_value=FRIDAY+timedelta(hours=1)):
            self.variant.save()
        with patch('django.utils.timezone.now',return_value=FRIDAY+timedelta(hours=1)):
            with self.assertRaisesRegex(OutreachBlocked,'Campaign revision changed'):
                confirm_batch(batch,self.owner,'LAUNCH 1')
        self.assertFalse(MilaniOutreachLog.objects.exists())

    def test_refresh_token_is_single_snapshot_not_an_unlimited_reapproval(self):
        batch=self.batch()
        token=self.fresh_preview(batch)['token']
        self.approve(batch,token)
        with self.assertRaisesRegex(OutreachBlocked,'changed after the preview'):
            self.approve(batch,token)
        self.assertFalse(MilaniOutreachLog.objects.exists())

    def test_refresh_requires_get_for_preview_and_post_for_apply(self):
        batch=self.batch()
        preview=reverse('admin:milani_batch_refresh',args=[batch.pk])
        apply=reverse('admin:milani_batch_refresh_apply',args=[batch.pk])
        self.assertEqual(self.client.post(preview).status_code,405)
        self.assertEqual(self.client.get(apply).status_code,405)

    def test_refresh_apply_is_csrf_protected_and_not_an_email_action(self):
        from django.test import Client
        batch=self.batch()
        signed=self.fresh_preview(batch)['token']
        strict=Client(enforce_csrf_checks=True)
        strict.force_login(self.owner)
        with patch('django.utils.timezone.now',return_value=MONDAY), \
             patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            response=strict.post(
                reverse('admin:milani_batch_refresh_apply',args=[batch.pk]),
                {'confirmation':'REFRESH 2','refresh_token':signed})
            self.assertEqual(response.status_code,403)
            provider.assert_not_called()
        self.assertIn('Friday',batch.recipients.first().body_snapshot)

    def test_review_page_warns_proactively_without_mutating_or_sending(self):
        batch=self.batch()
        original=list(batch.recipients.values_list('body_snapshot',flat=True))
        with patch('django.utils.timezone.now',return_value=MONDAY), \
             patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            response=self.client.get(
                reverse('admin:milani_batch_review',args=[batch.pk]))
            self.assertEqual(response.status_code,200)
            self.assertContains(response,'Saved messages need attention')
            self.assertContains(response,'2 unsent recipients changed')
            self.assertContains(response,'Refresh &amp; Re-review')
            provider.assert_not_called()
        self.assertEqual(list(batch.recipients.values_list('body_snapshot',flat=True)),
                         original)

    def test_review_page_shows_current_when_review_is_fresh(self):
        batch=self.batch(people=[self.one])
        with patch('django.utils.timezone.now',return_value=FRIDAY):
            response=self.client.get(
                reverse('admin:milani_batch_review',args=[batch.pk]))
            self.assertContains(response,'match the current approved campaign')
            self.assertNotContains(response,'Saved messages need attention')
