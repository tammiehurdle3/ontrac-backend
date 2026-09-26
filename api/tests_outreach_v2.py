"""Outreach v2: isolated DB and mocked provider tests. No network emails."""
from datetime import date, datetime, timedelta, timezone as tz
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from .milani_outreach_v2 import (
    OutreachBlocked, check_recipient, eligible_variants, now_in_la,
    prepare_approved_send, render_variant, weekday_greeting,
)
from .milani_email_service import send_milani_outreach_email
from .models import Creator, MilaniEmailVariant, MilaniOutreachLog, MilaniSuppression


@override_settings(
    MILANI_OUTREACH_V2_ENABLED=True, MILANI_OUTREACH_SEND_ENABLED=False,
    MILANI_PUBLIC_BASE_URL='http://localhost:8012',
    MILANI_SENDER_POSTAL_ADDRESS='LOCAL PREVIEW ONLY - NOT VERIFIED - DO NOT SEND',
)
class OutreachSafetyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.creator = Creator.objects.create(name='Jordan', email='jordan@example.invalid')
        cls.variant = MilaniEmailVariant.objects.create(
            name='Preview A', campaign_name='Test Only',
            subject='Opportunity for {name}',
            body='Hi {name},\n\n{greeting}\n\n{personal_line}\n\nWould you like details?',
            approval_state='approved', is_evergreen=True, is_active=True,
        )

    def test_los_angeles_weekday_does_not_use_utc_date(self):
        utc_saturday = datetime(2026, 9, 26, 0, 30, tzinfo=tz.utc)
        self.assertEqual(now_in_la(utc_saturday).weekday(), 4)
        self.assertEqual(now_in_la(utc_saturday).date().isoformat(), '2026-09-25')
        self.assertIn(weekday_greeting(utc_saturday, key='jordan@example.invalid'),
                      ('Hope you have had a good week!', 'Hope Friday is treating you well!'))

    def test_dst_is_automatic_and_global_timezone_not_modified(self):
        self.assertEqual(now_in_la(datetime(2026, 7, 1, 12, tzinfo=tz.utc)).utcoffset(),
                         timedelta(hours=-7))
        self.assertEqual(now_in_la(datetime(2027, 1, 1, 12, tzinfo=tz.utc)).utcoffset(),
                         timedelta(hours=-8))
        self.assertEqual(timezone.get_default_timezone_name(), 'UTC')

    def test_same_person_same_day_sees_same_greeting(self):
        fixed = datetime(2026, 9, 26, 15, tzinfo=tz.utc)
        a = render_variant(self.variant, self.creator, now=fixed)
        b = render_variant(self.variant, self.creator, now=fixed)
        self.assertEqual(a['body'], b['body'])

    def test_optional_note_omits_whole_paragraph(self):
        rendered = render_variant(self.variant, self.creator)
        self.assertNotIn('{personal_line}', rendered['body'])
        self.assertNotIn('\n\n\n', rendered['body'])
        self.assertIn('Would you like details?', rendered['body'])

    def test_real_observation_included_when_supplied(self):
        self.creator.personalization_note = 'Your natural-light makeup tutorial was particularly clear.'
        body = render_variant(self.variant, self.creator)['body']
        self.assertIn('Your natural-light makeup tutorial', body)

    def test_legacy_active_draft_never_selected(self):
        MilaniEmailVariant.objects.create(
            name='Legacy Summer', subject='Summer campaign', body='Old summer email',
            is_active=True, approval_state='draft')
        self.assertEqual([v.pk for v in eligible_variants()], [self.variant.pk])

    def test_unapproved_templates_fail_closed(self):
        self.variant.approval_state = 'draft'
        self.variant.save()
        with self.assertRaisesRegex(OutreachBlocked, 'no currently eligible'):
            prepare_approved_send(self.creator)

    def test_seasonal_dates_are_checked_in_los_angeles(self):
        self.variant.is_evergreen = False
        self.variant.starts_on = date(2026, 9, 27)
        self.variant.ends_on = date(2026, 9, 30)
        self.variant.save()
        before = datetime(2026, 9, 27, 5, tzinfo=tz.utc)  # Sep 26 LA
        during = datetime(2026, 9, 27, 19, tzinfo=tz.utc)
        self.assertEqual(eligible_variants(before), [])
        self.assertEqual([v.pk for v in eligible_variants(during)], [self.variant.pk])

    def test_approved_seasonal_campaign_requires_dates(self):
        self.variant.is_evergreen = False
        with self.assertRaises(ValidationError):
            self.variant.full_clean()

    def test_seasonal_wording_cannot_be_approved_evergreen(self):
        self.variant.body += '\n\nPaid Summer Campaign launching.'
        with self.assertRaises(ValidationError):
            self.variant.full_clean()

    def test_do_not_contact_blocks_send(self):
        self.creator.do_not_contact = True
        self.creator.save()
        with self.assertRaisesRegex(OutreachBlocked, 'Do Not Contact'):
            prepare_approved_send(self.creator)

    def test_suppressed_address_case_insensitive(self):
        MilaniSuppression.objects.create(email='JORDAN@EXAMPLE.INVALID', reason='Requested removal')
        with self.assertRaisesRegex(OutreachBlocked, 'suppression'):
            prepare_approved_send(self.creator)

    def test_recent_outreach_blocks_accidental_duplicate(self):
        MilaniOutreachLog.objects.create(
            creator=self.creator, subject='Prior', status='Sent',
            sendgrid_message_id='prior-test-uuid',
        )
        with self.assertRaisesRegex(OutreachBlocked, 'last 30 days'):
            prepare_approved_send(self.creator)

    def test_local_send_flag_prevents_any_provider_call(self):
        with patch('api.milani_email_service.resend_sdk.Emails.send') as send:
            self.assertFalse(send_milani_outreach_email(self.creator))
            send.assert_not_called()

    @override_settings(MILANI_OUTREACH_SEND_ENABLED=True,
                       MILANI_COSMETICS_RESEND_API_KEY='fake-test-key')
    def test_provider_not_called_without_approved_campaign(self):
        self.variant.approval_state='draft'
        self.variant.save()
        with patch('api.milani_email_service.resend_sdk.Emails.send') as send:
            self.assertFalse(send_milani_outreach_email(self.creator))
            send.assert_not_called()

    @override_settings(MILANI_OUTREACH_SEND_ENABLED=True,
                       MILANI_COSMETICS_RESEND_API_KEY='fake-test-key')
    def test_mocked_send_records_exact_copy_and_blocks_repeat(self):
        self.creator.personalization_note='I especially liked your recent makeup demonstration.'
        self.creator.save()
        with patch('api.milani_email_service.resend_sdk.Emails.send',
                   return_value={'id':'TEST-NEVER-SENT'}) as provider:
            self.assertTrue(send_milani_outreach_email(self.creator))
            self.assertFalse(send_milani_outreach_email(self.creator))
            self.assertEqual(provider.call_count, 1)
        log=MilaniOutreachLog.objects.get(creator=self.creator)
        self.assertEqual(log.provider_message_id,'TEST-NEVER-SENT')
        self.assertIn('I especially liked',log.body_snapshot)
        self.assertEqual(log.campaign_snapshot,'Test Only')

    def test_admin_preview_contains_real_personal_note_without_sending(self):
        self.creator.personalization_note='I liked the clear lighting in your product videos.'
        self.creator.save()
        staff = User.objects.create_superuser('sandbox-admin','local-admin@example.invalid','test-only-password')
        self.client.force_login(staff)
        with patch('api.milani_email_service.resend_sdk.Emails.send') as send:
            response=self.client.get(f'/admin/api/milaniemailvariant/{self.variant.pk}/preview/',
                                     {'creator_id':self.creator.pk})
        self.assertEqual(response.status_code,200)
        self.assertIn('Preview:', response.content.decode('utf-8'))
        # Preview is base64-embedded to support device toggles.
        import base64,re
        page=response.content.decode('utf-8')
        match=re.search(r'const EMAIL_HTML_LIGHT = atob\("([^"]+)"\)',page)
        self.assertIsNotNone(match)
        html=base64.b64decode(match.group(1)).decode()
        self.assertIn('I liked the clear lighting',html)
        self.assertIn('Hi Jordan',html)
        send.assert_not_called()



    @override_settings(MILANI_OUTREACH_SEND_ENABLED=True,
                       MILANI_COSMETICS_RESEND_API_KEY='fake-test-key')
    def test_unresolved_timeout_never_autoretries(self):
        with patch('api.milani_email_service.resend_sdk.Emails.send',
                   side_effect=TimeoutError('provider outcome unknown')) as provider:
            self.assertFalse(send_milani_outreach_email(self.creator))
            self.assertFalse(send_milani_outreach_email(self.creator))
            self.assertEqual(provider.call_count, 1)
        log = MilaniOutreachLog.objects.get(creator=self.creator)
        self.assertEqual(log.status, 'Needs Review')
        self.creator.refresh_from_db()
        self.assertEqual(self.creator.status, 'Needs Review')
        with self.assertRaises(OutreachBlocked):
            prepare_approved_send(self.creator)

    @override_settings(MILANI_OUTREACH_SEND_ENABLED=True,
                       MILANI_COSMETICS_RESEND_API_KEY='fake-test-key')
    def test_overlapping_individual_send_claims_only_one_email(self):
        from .models import Creator
        def attempted_overlap(_request):
            # Simulates another process arriving while the first is in-flight.
            other = Creator.objects.get(pk=self.creator.pk)
            self.assertFalse(send_milani_outreach_email(other))
            return {'id': 'MOCK-ONE-DELIVERY'}
        with patch('api.milani_email_service.resend_sdk.Emails.send',
                   side_effect=attempted_overlap) as provider:
            self.assertTrue(send_milani_outreach_email(self.creator))
            self.assertEqual(provider.call_count, 1)
        self.assertEqual(MilaniOutreachLog.objects.filter(creator=self.creator).count(), 1)
        self.assertEqual(MilaniOutreachLog.objects.get(creator=self.creator).status, 'Sent')

    def test_past_bounce_blocks_recontact_without_age_limit(self):
        log = MilaniOutreachLog.objects.create(
            creator=self.creator, subject='Previously bounced',
            status='Bounced', sendgrid_message_id='old-bounce-message')
        MilaniOutreachLog.objects.filter(pk=log.pk).update(
            event_time=timezone.now() - timedelta(days=100))
        with self.assertRaisesRegex(OutreachBlocked, 'bounced'):
            prepare_approved_send(self.creator)

    def test_invalid_evergreen_direct_database_edit_still_fails_closed(self):
        # A raw database change bypasses ModelForm validation, not the send gate.
        self.variant.body += '\n\nSummer Campaign'
        self.variant.save()
        with self.assertRaisesRegex(OutreachBlocked, 'no currently eligible'):
            prepare_approved_send(self.creator)

    def test_admin_draft_remains_previewable_but_never_eligible(self):
        self.variant.approval_state = 'draft'
        self.variant.save()
        staff = User.objects.create_superuser(
            'draft-admin','draft-admin@example.invalid','test-only-password')
        self.client.force_login(staff)
        preview = self.client.get(f'/admin/api/milaniemailvariant/{self.variant.pk}/preview/')
        self.assertEqual(preview.status_code, 200)
        changelist = self.client.get('/admin/api/creator/')
        self.assertContains(changelist, 'Review campaign drafts')
        self.assertFalse(eligible_variants())


    @override_settings(MILANI_OUTREACH_BULK_ENABLED=True)
    def test_bulk_sender_aborts_without_approved_campaign(self):
        from io import StringIO
        from django.core.management import call_command
        self.variant.approval_state='draft'
        self.variant.save()
        self.creator.status='Queued'
        self.creator.save()
        result=StringIO()
        with patch('api.management.commands.send_outreach.send_milani_outreach_email') as send:
            call_command('send_outreach', limit=100, delay=0, stdout=result)
            send.assert_not_called()
        self.assertIn('No eligible approved campaign', result.getvalue())

    @override_settings(MILANI_OUTREACH_BULK_ENABLED=True)
    def test_bulk_sender_never_runs_in_local_preview_mode(self):
        from io import StringIO
        from django.core.management import call_command
        self.creator.status='Queued'
        self.creator.save()
        result=StringIO()
        with patch('api.management.commands.send_outreach.send_milani_outreach_email') as send:
            call_command('send_outreach', limit=100, delay=0, stdout=result)
            send.assert_not_called()
        self.assertIn('delivery is disabled', result.getvalue())

    @override_settings(MILANI_OUTREACH_SEND_ENABLED=True, MILANI_OUTREACH_MAX_BATCH=20,
                       MILANI_OUTREACH_BULK_ENABLED=True)
    def test_bulk_run_respects_cap_and_send_delay(self):
        from io import StringIO
        from django.core.management import call_command
        self.creator.status='Queued'
        self.creator.save()
        Creator.objects.bulk_create([
            Creator(name='Dummy '+str(i), email='mock-batch-'+str(i)+'@example.invalid',
                    status='Queued') for i in range(24)
        ])
        result=StringIO()
        with patch('api.management.commands.send_outreach.send_milani_outreach_email',
                   return_value=True) as send, \
             patch('api.management.commands.send_outreach.time.sleep') as sleep:
            call_command('send_outreach', limit=100, delay=0, stdout=result)
        self.assertEqual(send.call_count,20)
        self.assertEqual(sleep.call_count,19)
        self.assertTrue(all(c.args==(30,) for c in sleep.call_args_list))
        self.assertIn('run limited to 20',result.getvalue())

    def test_bulk_dry_run_never_sends(self):
        from io import StringIO
        from django.core.management import call_command
        self.creator.status='Queued'
        self.creator.save()
        result=StringIO()
        with patch('api.management.commands.send_outreach.send_milani_outreach_email') as send:
            call_command('send_outreach',limit=20,delay=0,dry_run=True,stdout=result)
            send.assert_not_called()
        self.assertIn('DRY RUN',result.getvalue())

    def test_local_preview_buttons_are_disabled_and_post_is_blocked(self):
        staff = User.objects.create_superuser(
            'no-send-admin','no-send@example.invalid','test-only-password')
        self.client.force_login(staff)
        preview = self.client.get(
            f'/admin/api/milaniemailvariant/{self.variant.pk}/preview/',
            {'creator_id': self.creator.pk})
        self.assertEqual(preview.status_code, 200)
        html = preview.content.decode('utf-8')
        self.assertEqual(html.count('Sending disabled — preview only'), 2)
        self.assertEqual(html.count('disabled aria-disabled="true"'), 2)
        self.assertNotIn('<button onclick="sendTest()">', html)
        with patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            result = self.client.post(
                f'/admin/api/milaniemailvariant/{self.variant.pk}/send-test/',
                {'creator_id': self.creator.pk})
            self.assertEqual(result.status_code, 200)
            self.assertFalse(result.json()['success'])
            provider.assert_not_called()

    @override_settings(MILANI_OUTREACH_TEST_MODE=True, MILANI_OUTREACH_SEND_ENABLED=True)
    def test_test_mode_blocks_every_other_address_at_provider_boundary(self):
        from .milani_email_service import _dispatch_outreach
        with patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            with self.assertRaisesRegex(ValueError, 'allowlist'):
                _dispatch_outreach(self.creator, {'to': ['jordan@example.invalid']})
            provider.assert_not_called()

    @override_settings(MILANI_OUTREACH_SEND_ENABLED=True,
                       MILANI_OUTREACH_BULK_ENABLED=False)
    def test_bulk_disabled_even_with_individual_send_enabled(self):
        from io import StringIO
        from django.core.management import call_command
        output = StringIO()
        with patch('api.management.commands.send_outreach.send_milani_outreach_email') as provider:
            call_command('send_outreach', stdout=output)
            provider.assert_not_called()
        self.assertIn('bulk delivery is disabled', output.getvalue())

    @override_settings(MILANI_OUTREACH_SEND_ENABLED=True,
                       MILANI_OUTREACH_BULK_ENABLED=True, MILANI_OUTREACH_TEST_MODE=True)
    def test_test_mode_disables_bulk_even_when_bulk_opted_in(self):
        from io import StringIO
        from django.core.management import call_command
        output = StringIO()
        with patch('api.management.commands.send_outreach.send_milani_outreach_email') as provider:
            call_command('send_outreach', stdout=output)
            provider.assert_not_called()
        self.assertIn('bulk delivery is disabled', output.getvalue())

    @override_settings(MILANI_OUTREACH_TEST_MODE=True,
                       MILANI_OUTREACH_SEND_ENABLED=True,
                       MILANI_COSMETICS_RESEND_API_KEY='fake-test-key')
    def test_one_recipient_specific_route_exact_render_and_no_localhost_footer(self):
        from .milani_email_service import send_specific_milani_variant
        me = Creator.objects.create(name='Smith', email='smthpines@gmail.com')
        expected = render_variant(self.variant, me)
        with patch('api.milani_email_service.resend_sdk.Emails.send',
                   return_value={'id': 'MOCK-SELF-ONLY'}) as provider:
            self.assertTrue(send_specific_milani_variant(me, 'ignored', 'ignored',
                                                         variant_id=self.variant.pk))
            self.assertFalse(send_specific_milani_variant(me, 'ignored', 'ignored',
                                                          variant_id=self.variant.pk))
            self.assertEqual(provider.call_count, 1)
            actual = provider.call_args.args[0]
        self.assertEqual(actual['to'], ['smthpines@gmail.com'])
        self.assertEqual(actual['subject'], expected['subject'])
        self.assertEqual(actual['text'], expected['body'])
        self.assertIn('mailto:', actual['html'])
        self.assertNotIn('localhost', actual['html'])
        self.assertNotIn('milani-open', actual['html'])
        self.assertNotIn('List-Unsubscribe-Post', actual['headers'])
        self.assertEqual(actual['headers']['X-Test-Send'], 'true')
        self.assertEqual(MilaniOutreachLog.objects.filter(creator=me).count(), 1)

    @override_settings(MILANI_OUTREACH_SEND_ENABLED=True,
                       MILANI_OUTREACH_BULK_ENABLED=False)
    def test_individual_admin_action_cannot_send_multiple_when_bulk_is_disabled(self):
        from unittest.mock import Mock
        from .admin import send_individual_outreach
        second = Creator.objects.create(name='Second', email='second@example.invalid')
        admin = Mock()
        selected = Creator.objects.filter(pk__in=[self.creator.pk, second.pk])
        with patch('api.admin.send_milani_outreach_email') as provider:
            send_individual_outreach(admin, object(), selected)
            provider.assert_not_called()
        self.assertIn('Select exactly one', admin.message_user.call_args.args[1])

    @override_settings(MILANI_OUTREACH_SEND_ENABLED=True,
                       MILANI_OUTREACH_BULK_ENABLED=True, MILANI_OUTREACH_TEST_MODE=True)
    def test_individual_admin_action_rejects_non_test_recipient_in_test_mode(self):
        from unittest.mock import Mock
        from .admin import send_individual_outreach
        admin = Mock()
        with patch('api.admin.send_milani_outreach_email') as provider:
            send_individual_outreach(admin, object(),
                                     Creator.objects.filter(pk=self.creator.pk))
            provider.assert_not_called()
        self.assertIn('restricted', admin.message_user.call_args.args[1])

    @override_settings(MILANI_OUTREACH_TEST_MODE=True,
                       MILANI_OUTREACH_SEND_ENABLED=True,
                       MILANI_COSMETICS_RESEND_API_KEY='fake-test-key')
    def test_non_test_address_never_claimed_by_both_send_paths(self):
        from .milani_email_service import send_specific_milani_variant
        with patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            self.assertFalse(send_milani_outreach_email(self.creator))
            self.assertFalse(send_specific_milani_variant(
                self.creator, 'ignored', 'ignored', variant_id=self.variant.pk))
            provider.assert_not_called()
        self.assertFalse(MilaniOutreachLog.objects.filter(creator=self.creator).exists())
        self.creator.refresh_from_db()
        self.assertNotEqual(self.creator.status, 'Needs Review')
