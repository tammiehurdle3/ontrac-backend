"""Explicit re-review of prepared creator batches. NEVER calls an email provider.

The preview is an HMAC-signed proposal digest, not permission to send.
Approval recomputes and compares every row while locking the batch. Neither
action confirms/resumes a batch or creates delivery claims.
"""
import hashlib
import json
from datetime import timezone as datetime_timezone

from django.core import signing
from django.db import transaction
from django.utils import timezone

from .milani_outreach_v2 import (
    OutreachBlocked, now_in_la, prepare_approved_send, require_approved_variant,
)
from .models import Creator, MilaniEmailVariant, MilaniLaunchBatch, MilaniLaunchRecipient

TOKEN_SALT = 'milani.manual-batch-refresh.v1'
TOKEN_AGE_SECONDS = 15 * 60
REFRESHABLE = frozenset(('pending', 'blocked'))
UNCERTAIN = frozenset(('processing', 'needs_review'))


def _iso(moment):
    # In-memory LA timestamps and DB-loaded UTC timestamps represent the same
    # instant but stringify differently. Canonical UTC prevents false mismatches.
    return moment.astimezone(datetime_timezone.utc).isoformat() if moment else ''


def _check_access(batch, operator):
    if not (operator.is_authenticated and operator.is_active and
            operator.is_superuser and batch.created_by_id == operator.pk):
        raise OutreachBlocked('Only the original superuser may refresh this batch.')
    if batch.status not in ('draft', 'paused'):
        raise OutreachBlocked('Pause the batch before refreshing unsent messages.')
    if batch.recipients.filter(status__in=UNCERTAIN).exists():
        raise OutreachBlocked(
            'Resolve any in-flight or uncertain message before refreshing. '
            'Refreshing must never conceal an unknown provider outcome.'
        )


def _proposal(batch, *, now):
    """Pure projection: no snapshots, claim rows or statuses are modified."""
    require_approved_variant(batch.variant, now)
    rows = list(batch.recipients.select_related('creator').order_by('position'))
    selected = [r for r in rows if r.status in REFRESHABLE]
    if not selected:
        raise OutreachBlocked('No unsent recipients remain to refresh.')
    changes = []
    proposed = []
    for item in rows:
        if item.status not in REFRESHABLE:
            # Sent rows and unknown outcomes remain in the hash but are untouchable.
            proposed.append({
                'id': item.pk, 'status': item.status,
                'email': item.recipient_email_snapshot,
                'subject': item.subject_snapshot, 'body': item.body_snapshot,
                'reason': item.reason,
            })
            continue
        creator = item.creator
        try:
            prepared = prepare_approved_send(creator, now=now, variant=batch.variant)
            new_status, new_reason = 'pending', ''
            new_subject, new_body = prepared['subject'], prepared['body']
        except OutreachBlocked as exc:
            new_status, new_reason = 'blocked', str(exc)[:255]
            new_subject, new_body = '', ''
        revised = {
            'id': item.pk, 'status': new_status,
            'email': creator.email, 'subject': new_subject,
            'body': new_body, 'reason': new_reason,
        }
        previous = {
            'status': item.status, 'email': item.recipient_email_snapshot,
            'subject': item.subject_snapshot, 'body': item.body_snapshot,
            'reason': item.reason,
        }
        new_fields = {key: value for key, value in revised.items() if key != 'id'}
        is_changed = previous != new_fields
        changes.append({
            'item': item, 'previous': previous, 'revised': revised,
            'changed': is_changed,
            'email_changed': previous['email'].casefold() != creator.email.casefold(),
            'status_changed': previous['status'] != new_status,
            'copy_changed': (previous['subject'] != new_subject or
                             previous['body'] != new_body),
        })
        proposed.append(revised)
    return {
        'batch': batch, 'rows': changes,
        'campaign_changed': (
            batch.name != batch.variant.campaign_name or
            batch.variant_revision_at != batch.variant.updated_at),
        'new_campaign_name': batch.variant.campaign_name,
        'date': now_in_la(now).date().isoformat(),
        'digest_data': {
            'schema': 1, 'batch': str(batch.pk), 'status': batch.status,
            'confirmed_at': _iso(batch.confirmed_at),
            'batch_updated_at': _iso(batch.updated_at),
            'campaign_old': batch.name,
            'campaign_new': batch.variant.campaign_name,
            'variant_id': batch.variant_id,
            'variant_updated': _iso(batch.variant.updated_at),
            'variant_approved_at_review': _iso(batch.variant_revision_at),
            'day_la': now_in_la(now).date().isoformat(),
            'previous': [
                {
                    'id': i.pk, 'status': i.status,
                    'email': i.recipient_email_snapshot, 'subject': i.subject_snapshot,
                    'body': i.body_snapshot, 'reason': i.reason,
                    'processed_at': _iso(i.processed_at),
                } for i in rows
            ],
            'proposed': proposed,
        },
    }

def _hash_plan(proposal):
    raw = json.dumps(proposal['digest_data'], sort_keys=True,
                     separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def preview_refresh(batch, operator, *, now=None):
    # Use the DB's current version rather than a possibly cached admin/model instance.
    batch = MilaniLaunchBatch.objects.select_related('variant').get(pk=batch.pk)
    _check_access(batch, operator)
    current = now or timezone.now()
    proposal = _proposal(batch, now=current)
    token = signing.dumps({
        'batch': str(batch.pk), 'operator': operator.pk,
        'digest': _hash_plan(proposal), 'day_la': proposal['date'],
    }, salt=TOKEN_SALT, compress=True)
    proposal['token'] = token
    proposal['changed_count'] = sum(row['changed'] for row in proposal['rows'])
    return proposal


def apply_refresh(batch_id, operator, token):
    if not token:
        raise OutreachBlocked('Preview the exact changes before approving them.')
    try:
        signed = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_AGE_SECONDS)
    except (signing.BadSignature, signing.SignatureExpired):
        raise OutreachBlocked(
            'The refresh preview expired or was altered. Review the changes again.'
        )
    if (signed.get('batch') != str(batch_id) or
            signed.get('operator') != operator.pk):
        raise OutreachBlocked('This refresh approval belongs to a different batch or operator.')
    now = timezone.now()
    if signed.get('day_la') != now_in_la(now).date().isoformat():
        raise OutreachBlocked(
            'The Los Angeles date changed since the preview. Refresh and review again.'
        )
    with transaction.atomic():
        batch = (MilaniLaunchBatch.objects.select_for_update()
                 .select_related('variant').get(pk=batch_id))
        _check_access(batch, operator)
        batch.variant = MilaniEmailVariant.objects.select_for_update().get(
            pk=batch.variant_id)
        # Lock all recipients and creators used to compute the reviewed proposal.
        rows = list(batch.recipients.select_for_update().order_by('position'))
        creators = list(Creator.objects.select_for_update().filter(
            pk__in=[i.creator_id for i in rows]).order_by('pk'))
        if len(creators) != len(rows):
            raise OutreachBlocked('A creator disappeared. Prepare a new batch.')
        proposal = _proposal(batch, now=now)
        if _hash_plan(proposal) != signed.get('digest'):
            raise OutreachBlocked(
                'Recipient eligibility or wording changed after the preview. '
                'Review the new differences before approving anything.'
            )
        updated = 0
        for change in proposal['rows']:
            item, new = change['item'], change['revised']
            if item.status not in REFRESHABLE:
                raise OutreachBlocked('A recipient changed state during review.')
            item.status = new['status']
            item.recipient_email_snapshot = new['email']
            item.subject_snapshot = new['subject']
            item.body_snapshot = new['body']
            item.reason = new['reason']
            item.save(update_fields=[
                'status', 'recipient_email_snapshot', 'subject_snapshot',
                'body_snapshot', 'reason',
            ])
            updated += int(change['changed'])
        batch.name = proposal['new_campaign_name']
        batch.variant_revision_at = batch.variant.updated_at
        batch.save(update_fields=['name', 'variant_revision_at', 'updated_at'])
    return {'changed': updated, 'status': batch.status}
