"""Production-ready creator workflow: duplicate protection, accessible sending, newest first."""
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .admin import CreatorAdminForm, send_individual_outreach
from .models import Creator, MilaniEmailVariant, MilaniOutreachLog, SiteSettings


@override_settings(
    MILANI_OUTREACH_V2_ENABLED=True,
    MILANI_OUTREACH_SEND_ENABLED=True,
    MILANI_OUTREACH_BULK_ENABLED=True,
    MILANI_OUTREACH_TEST_MODE=False,
    MILANI_PUBLIC_BASE_URL='https://api.ontracourier.us',
    MILANI_SENDER_POSTAL_ADDRESS=(
        'New Milani Group LLC, 10000 W. Washington Blvd, Suite 210, '
        'Culver City, CA 90232, United States'),
    RESEND_MILANI_API_KEY='fake-only-for-fully-mocked-tests',
)
class CreatorAdminExperienceTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            'creator-admin', 'super@example.invalid', 'test-only-password')
        self.client.force_login(self.superuser)
        SiteSettings.objects.update_or_create(
            pk=1, defaults={'milani_smtp_provider': 'resend_collabs'})
        self.older = Creator.objects.create(
            name='Older Creator', email='older@example.invalid')

    def approve_sample(self):
        return MilaniEmailVariant.objects.create(
            name='Approved evergreen, tests only',
            campaign_name='Reviewed collaboration',
            subject='Milani collaboration for {name}',
            body='Hi {name},\n\n{greeting}\n\nInterested in a collaboration?\n\nBest,\nDiana',
            approval_state='approved', is_active=True, is_evergreen=True,
        )

    def test_admin_newest_first_after_adding_and_during_search(self):
        recent = Creator.objects.create(
            name='Recent Creator', email='recent@example.invalid')
        url = reverse('admin:api_creator_changelist')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        ids = list(response.context['cl'].result_list.values_list('pk',flat=True))
        self.assertEqual(ids[:2], [recent.pk, self.older.pk])
        self.assertContains(response, 'Newest creators appear first')
        filtered = self.client.get(url, {'q': 'example.invalid'})
        self.assertEqual(filtered.status_code, 200)
        search_ids = list(filtered.context['cl'].result_list.values_list('pk',flat=True))
        self.assertEqual(search_ids[:2], [recent.pk, self.older.pk])

    def test_single_home_links_and_campaign_readiness_are_prominent(self):
        response = self.client.get(reverse('admin:api_creator_changelist'))
        self.assertContains(response, 'Your outreach workspace')
        self.assertContains(response, 'Review / Launch Batch')
        self.assertContains(response, 'Email campaigns')
        self.assertContains(response, 'Delivery is enabled.')
        self.assertContains(response, 'Approve an active campaign first')
        self.assertNotContains(response, 'Local preview')

    def test_save_only_available_without_a_campaign(self):
        response = self.client.get(reverse('admin:api_creator_add'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Save creator only')
        self.assertContains(response, 'Review campaign approvals')
        self.assertNotContains(response, 'name="_save_and_send"')

    def test_form_duplicate_catches_case_and_surrounding_spaces(self):
        form = CreatorAdminForm(data={
            'name': 'Duplicate', 'email': ' OLDER@EXAMPLE.INVALID ',
            'status': 'New Lead',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('already registered', str(form.errors['email']))
        self.assertEqual(Creator.objects.filter(email__iexact='older@example.invalid').count(),1)

    def test_live_duplicate_endpoint_includes_a_direct_edit_link(self):
        response=self.client.get(reverse('admin:creator_check_email'),{
            'email':'OLDER@EXAMPLE.INVALID'})
        self.assertEqual(response.status_code,200)
        data=response.json()
        self.assertTrue(data['exists'])
        self.assertEqual(data['creator']['id'],self.older.pk)
        self.assertEqual(data['edit_url'], reverse(
            'admin:api_creator_change',args=[self.older.pk]))
        self.assertFalse(self.client.get(reverse('admin:creator_check_email'),{
            'email':'older@example.invalid',
            'exclude_pk':self.older.pk,
        }).json()['exists'])

    def test_admin_save_only_never_contacts_any_provider(self):
        url=reverse('admin:api_creator_add')
        with patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            response=self.client.post(url,{
                'name':'Just Saved','email':'  NewCase@Example.invalid  ',
                'status':'New Lead','_save':'Save creator only'})
            provider.assert_not_called()
        self.assertEqual(response.status_code,302)
        creator=Creator.objects.get(name='Just Saved')
        self.assertEqual(creator.email,'newcase@example.invalid')
        self.assertEqual(Creator.objects.order_by('-pk').first(),creator)
        self.assertFalse(MilaniOutreachLog.objects.filter(creator=creator).exists())

    def test_approved_campaign_exposes_single_preview_in_front_of_the_row(self):
        variant=self.approve_sample()
        response=self.client.get(reverse('admin:api_creator_changelist'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Preview &amp; Send')
        self.assertContains(response,
            reverse('admin:milaniemailvariant_preview',args=[variant.pk])+
            '?creator_id='+str(self.older.pk))
        page=self.client.get(reverse('admin:api_creator_change',args=[self.older.pk]))
        self.assertContains(page,'Save creator only')
        self.assertContains(page,'name="_save_and_send"')
        self.assertContains(page,'Send this creator now (confirm first)')

    def test_admin_immediate_action_never_sends_more_than_one_even_with_bulk_enabled(self):
        second=Creator.objects.create(name='Second',email='second@example.invalid')
        a=Mock()
        with patch('api.admin.send_milani_outreach_email') as send:
            send_individual_outreach(a,object(),
                Creator.objects.filter(pk__in=[self.older.pk,second.pk]))
            send.assert_not_called()
        self.assertIn('Select exactly one',a.message_user.call_args.args[1])

    def test_single_send_endpoint_requires_approved_campaign(self):
        url=reverse('admin:creator_send_outreach',args=[self.older.pk])
        with patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            response=self.client.post(url)
            provider.assert_not_called()
        self.assertEqual(response.status_code,409)
        self.assertIn('Approve an active campaign',response.json()['error'])

    def test_single_send_with_approved_campaign_mocks_exactly_one_provider_call(self):
        self.approve_sample()
        url=reverse('admin:creator_send_outreach',args=[self.older.pk])
        with patch('api.milani_email_service.resend_sdk.Emails.send',
                   return_value={'id':'MOCK-ONE-ONLY'}) as provider:
            response=self.client.post(url)
            self.assertEqual(response.status_code,200,response.content)
            self.assertTrue(response.json()['success'])
            provider.assert_called_once()
            self.assertEqual(provider.call_args.args[0]['to'],['older@example.invalid'])
        self.assertEqual(
            MilaniOutreachLog.objects.filter(creator=self.older,status='Sent').count(),1)
        # Double-submit must not send twice.
        with patch('api.milani_email_service.resend_sdk.Emails.send') as provider:
            again=self.client.post(url)
            self.assertFalse(again.json()['success'])
            provider.assert_not_called()

    def test_preview_displays_real_production_sender_and_enablement(self):
        variant=self.approve_sample()
        response=self.client.get(reverse(
            'admin:milaniemailvariant_preview',args=[variant.pk]),
            {'creator_id':self.older.pk})
        self.assertEqual(response.status_code,200)
        html=response.content.decode()
        self.assertIn('diana@milanicollabs.com',html)
        self.assertNotIn('LOCAL PREVIEW:',html)
        self.assertNotIn('disabled aria-disabled="true"',html)
