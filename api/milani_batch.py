"""Manual, tab-driven batch delivery. Nothing here runs on a scheduler.

Two distinct actions: prepare a frozen, reviewable draft, then a human confirms
and manually starts one browser-controlled request per recipient.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import (Creator, MilaniEmailVariant, MilaniLaunchBatch,
                     MilaniLaunchRecipient, MilaniBatchSendGate,
                     MilaniOutreachLog)
from .milani_outreach_v2 import (OutreachBlocked, prepare_approved_send,
                                 require_approved_variant)

MAX_RECIPIENTS = 20
SEND_GAP_SECONDS = 30
_active_scope = ContextVar('milani_explicit_batch_scope', default=None)


def readiness_error():
    if not (getattr(settings, 'MILANI_OUTREACH_V2_ENABLED', False)
            and getattr(settings, 'MILANI_OUTREACH_BULK_ENABLED', False)):
        return 'Manual batch feature is not enabled.'
    if getattr(settings, 'MILANI_OUTREACH_TEST_MODE', False):
        return 'Local self-addressed testing cannot launch bulk delivery.'
    from .milani_email_service import _get_provider_config
    provider = _get_provider_config()
    if (provider['from_email'].strip().casefold() != 'diana@milanicollabs.com'
            or provider['log_label'] != 'resend_collabs'):
        return 'Choose the verified Resend Collabs sender in Site Settings first.'
    if not getattr(settings, provider['api_key_setting'], ''):
        return 'Verified Resend sender credential is unavailable.'
    postal = getattr(settings, 'MILANI_SENDER_POSTAL_ADDRESS', '').strip()
    if not postal or 'TEST-ONLY' in postal or 'NOT VERIFIED' in postal:
        return 'The verified Milani sender postal address is not configured.'
    if not getattr(settings, 'MILANI_PUBLIC_BASE_URL', '').startswith('https://'):
        return 'A public HTTPS unsubscribe endpoint must be configured.'
    return None


def prepare_draft(*, creator_ids, variant, operator):
    if not operator.is_active or not operator.is_superuser:
        raise OutreachBlocked('Only a superuser can prepare a manual batch.')
    ids = [int(i) for i in creator_ids]
    if (not ids or len(ids) > MAX_RECIPIENTS or
            len(ids) != len(set(ids)) or any(i <= 0 for i in ids)):
        raise OutreachBlocked('Select 1 to 20 unique creators per batch.')
    require_approved_variant(variant)
    selected = Creator.objects.in_bulk(ids)
    if len(selected) != len(ids):
        raise OutreachBlocked('A selected creator no longer exists. Review selection again.')
    with transaction.atomic():
        batch = MilaniLaunchBatch.objects.create(
            variant=variant, created_by=operator, name=variant.campaign_name)
        items = []
        for position, pk in enumerate(ids, 1):
            creator = selected[pk]
            try:
                rendered = prepare_approved_send(creator, variant=variant)
                state, reason = 'pending', ''
                subject, body = rendered['subject'], rendered['body']
            except OutreachBlocked as error:
                state, reason = 'blocked', str(error)[:255]
                subject, body = '', ''
            items.append(MilaniLaunchRecipient(
                batch=batch, creator=creator, position=position,
                status=state, reason=reason, subject_snapshot=subject,
                body_snapshot=body))
        MilaniLaunchRecipient.objects.bulk_create(items)
    return batch

def confirm_batch(batch, operator, phrase):
    if not operator.is_superuser or batch.created_by_id != operator.pk:
        raise OutreachBlocked('This batch belongs to another operator.')
    with transaction.atomic():
        locked = MilaniLaunchBatch.objects.select_for_update().get(pk=batch.pk)
        if locked.status != 'draft':
            raise OutreachBlocked('Only a draft may be confirmed once.')
        eligible = locked.recipients.filter(status='pending').count()
        if not eligible:
            raise OutreachBlocked('No eligible recipients; nothing can be launched.')
        if phrase != f'LAUNCH {eligible}':
            raise OutreachBlocked(f'Type LAUNCH {eligible} exactly to confirm.')
        err = readiness_error()
        if err:
            raise OutreachBlocked(err)
        require_approved_variant(locked.variant)
        locked.status = 'running'
        locked.confirmed_at = timezone.now()
        locked.save(update_fields=['status', 'confirmed_at', 'updated_at'])
        return locked


def scope_authorized(creator, variant_id=None):
    """Called from BOTH the entry point and the last provider-call boundary."""
    pair = _active_scope.get()
    if not pair or not (settings.MILANI_OUTREACH_V2_ENABLED
                        and settings.MILANI_OUTREACH_BULK_ENABLED
                        and not settings.MILANI_OUTREACH_TEST_MODE):
        return False
    batch_id, item_id, creator_id, approved_variant = pair
    if creator_id != creator.pk or (variant_id is not None
                                    and approved_variant != variant_id):
        return False
    return MilaniLaunchRecipient.objects.filter(
        pk=item_id, batch_id=batch_id, creator_id=creator_id,
        batch__variant_id=approved_variant, batch__status='running',
        status='processing', batch__confirmed_at__isnull=False
    ).exists()


@contextmanager
def scoped_delivery(item):
    """Only execute_step may call this; never use request-local settings overrides."""
    token = _active_scope.set((item.batch_id, item.pk, item.creator_id,
                               item.batch.variant_id))
    try:
        yield
    finally:
        _active_scope.reset(token)

def step_once(batch_id, operator):
    """One authenticated request sends AT MOST one email; no background execution."""
    if not operator.is_active or not operator.is_superuser:
        raise OutreachBlocked('A superuser must initiate each batch delivery step.')
    err = readiness_error()
    if err:
        raise OutreachBlocked(err)
    with transaction.atomic():
        batch = (MilaniLaunchBatch.objects.select_for_update()
                 .select_related('variant').get(pk=batch_id))
        if batch.created_by_id != operator.pk or batch.status != 'running':
            raise OutreachBlocked('Batch is not authorized or no longer running.')
        require_approved_variant(batch.variant)
        if batch.recipients.filter(status__in=['processing', 'needs_review']).exists():
            raise OutreachBlocked('An earlier send needs manual reconciliation; no further automatic steps.')
        gate, _ = MilaniBatchSendGate.objects.get_or_create(pk=1)
        gate = MilaniBatchSendGate.objects.select_for_update().get(pk=gate.pk)
        now = timezone.now()
        if gate.last_started_at:
            wait = (gate.last_started_at + timedelta(seconds=SEND_GAP_SECONDS) - now)
            if wait.total_seconds() > 0:
                return {'state': 'wait', 'seconds': max(1, int(wait.total_seconds()) + 1)}
        item = (batch.recipients.select_for_update().filter(status='pending')
                .select_related('creator').order_by('position').first())
        if item is None:
            batch.status = 'completed'
            batch.save(update_fields=['status', 'updated_at'])
            return {'state': 'complete'}
        try:
            current = prepare_approved_send(item.creator, variant=batch.variant)
            if (current['subject'] != item.subject_snapshot or
                    current['body'] != item.body_snapshot):
                raise OutreachBlocked('Creator or campaign changed since review.')
        except OutreachBlocked as error:
            item.status = 'blocked'
            item.reason = str(error)[:255]
            item.processed_at = now
            item.save(update_fields=['status', 'reason', 'processed_at'])
            return {'state': 'blocked', 'position': item.position,
                    'reason': item.reason}
        item.status = 'processing'
        item.processed_at = now
        item.save(update_fields=['status', 'processed_at'])
        gate.last_started_at = now
        gate.save(update_fields=['last_started_at'])
    # Transaction COMMITTED; never hold any DB lock across a provider request.
    from .milani_email_service import send_specific_milani_variant
    with scoped_delivery(item):
        succeeded = send_specific_milani_variant(
            item.creator, '', '', variant_id=batch.variant_id)
    with transaction.atomic():
        final = MilaniLaunchRecipient.objects.select_for_update().get(pk=item.pk)
        if succeeded:
            final.status = 'sent'
        else:
            uncertain = MilaniOutreachLog.objects.filter(
                creator_id=item.creator_id,
                event_time__gte=item.processed_at - timedelta(seconds=2),
                status__in=['Sending', 'Needs Review']).exists()
            final.status = 'needs_review' if uncertain else 'blocked'
            final.reason = ('Check provider acceptance before retrying.'
                            if uncertain else 'Safety check or provider rejected this recipient.')
        final.processed_at = timezone.now()
        final.save(update_fields=['status', 'reason', 'processed_at'])
        return {'state': final.status, 'position': final.position}


def pause_batch(batch_id, operator):
    with transaction.atomic():
        batch = MilaniLaunchBatch.objects.select_for_update().get(pk=batch_id)
        if batch.created_by_id != operator.pk or not operator.is_superuser:
            raise OutreachBlocked('Not permitted to pause this batch.')
        if batch.status == 'running':
            batch.status = 'paused'
            batch.save(update_fields=['status', 'updated_at'])
        return batch


def resume_batch(batch_id, operator, phrase):
    """Paused batches need a fresh explicit confirmation, never automatic resume."""
    with transaction.atomic():
        batch = (MilaniLaunchBatch.objects.select_for_update()
                 .select_related('variant').get(pk=batch_id))
        if not operator.is_active or not operator.is_superuser or batch.created_by_id != operator.pk:
            raise OutreachBlocked('Only the original superuser may resume.')
        if batch.status != 'paused':
            raise OutreachBlocked('This batch is not paused.')
        if batch.recipients.filter(status__in=['processing', 'needs_review']).exists():
            raise OutreachBlocked('Reconcile uncertain delivery before preparing a new batch.')
        remaining = list(batch.recipients.filter(status='pending').select_related('creator'))
        if not remaining or phrase != f'RESUME {len(remaining)}':
            raise OutreachBlocked(f'Type RESUME {len(remaining)} exactly if there are pending recipients.')
        issue = readiness_error()
        if issue:
            raise OutreachBlocked(issue)
        require_approved_variant(batch.variant)
        for item in remaining:
            data = prepare_approved_send(item.creator, variant=batch.variant)
            if data['subject'] != item.subject_snapshot or data['body'] != item.body_snapshot:
                raise OutreachBlocked('Creator or copy changed. Create and review a fresh batch.')
        batch.status = 'running'
        batch.save(update_fields=['status', 'updated_at'])
        return batch
