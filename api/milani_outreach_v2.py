"""Campaign-selection and truthful rendering for local-outreach v2.

No provider calls occur in this module. Production stays on legacy behavior
until MILANI_OUTREACH_V2_ENABLED is explicitly enabled after database backup.
"""
from __future__ import annotations

import random
import hashlib
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone
from .models import Creator, MilaniEmailVariant, MilaniOutreachLog, MilaniSuppression

LA = ZoneInfo('America/Los_Angeles')
RECENT_DAYS = 30
SENT_STATUSES = ('Sending', 'Needs Review', 'Sent', 'Delivered', 'Opened', 'Clicked', 'Replied')
PERMANENT_BLOCK = ('Bounced', 'Dropped', 'Reported Spam', 'Invalid Email')
LIVE = 'approved'


class OutreachBlocked(Exception):
    """A safety stop: no email may be sent."""


def now_in_la(now=None):
    moment = now or timezone.now()
    if timezone.is_naive(moment):
        raise ValueError('Naive timestamps are forbidden')
    return moment.astimezone(LA)


def weekday_greeting(now=None, *, key=None, choice=None):
    weekday = now_in_la(now).weekday()
    if weekday <= 1:
        pool = ('Hope your week is off to a good start!', 'Hope your week is going well!')
    elif weekday <= 3:
        pool = ('Hope you are having a good week!', 'Hope the week is treating you well!')
    elif weekday == 4:
        pool = ('Hope you have had a good week!', 'Hope Friday is treating you well!')
    else:
        pool = ('Hope you are having a good weekend!', 'Hope your weekend is going well!')
    if key:
        digest = hashlib.sha256(
            (key.strip().lower() + '|' + now_in_la(now).date().isoformat()).encode()
        ).digest()
        return pool[digest[0] % len(pool)]
    return (choice or random.choice)(pool)


def eligible_variants(now=None):
    today = now_in_la(now).date()
    candidates = MilaniEmailVariant.objects.filter(
        approval_state=LIVE, is_active=True,
    ).order_by('name')
    eligible = []
    for variant in candidates:
        try:
            require_approved_variant(variant, now)
        except OutreachBlocked:
            continue
        eligible.append(variant)
    return eligible


def require_approved_variant(variant, now=None):
    if variant.approval_state != LIVE or not variant.is_active:
        raise OutreachBlocked('This template is not approved and active.')
    from django.core.exceptions import ValidationError
    try:
        variant.full_clean()
    except ValidationError:
        raise OutreachBlocked('Invalid campaign configuration; review and reapprove in admin.')
    if not variant.is_evergreen:
        today = now_in_la(now).date()
        if not variant.starts_on or not variant.ends_on:
            raise OutreachBlocked('The campaign has no complete date window.')
        if not variant.starts_on <= today <= variant.ends_on:
            raise OutreachBlocked('The campaign is not eligible on the current Los Angeles date.')


def check_recipient(creator: Creator, *, now=None):
    if creator.do_not_contact:
        raise OutreachBlocked('Recipient is marked Do Not Contact.')
    if MilaniSuppression.objects.filter(email__iexact=creator.email.strip()).exists():
        raise OutreachBlocked('Recipient address is on the suppression list.')
    if not creator.email.strip():
        raise OutreachBlocked('Recipient has no email address.')
    if creator.status in PERMANENT_BLOCK or MilaniOutreachLog.objects.filter(
        creator=creator, status__in=PERMANENT_BLOCK,
    ).exists():
        raise OutreachBlocked('Recipient has bounced, complained, or is invalid.')
    if creator.status == 'Replied':
        raise OutreachBlocked('Recipient replied previously; use an individual conversation.')
    if creator.status == 'Needs Review' or MilaniOutreachLog.objects.filter(
        creator=creator, status__in=('Sending', 'Needs Review'),
    ).exists():
        raise OutreachBlocked('An unresolved delivery attempt needs manual reconciliation.')
    cutoff = (now or timezone.now()) - timedelta(days=RECENT_DAYS)
    if MilaniOutreachLog.objects.filter(
        creator=creator, status__in=SENT_STATUSES, event_time__gte=cutoff,
    ).exists():
        raise OutreachBlocked('Recipient has a recorded outreach in the last 30 days.')


def render_variant(variant, creator, *, now=None, greeting=None):
    """Exact preview and send use the same rendering path. No fake compliment."""
    value = (creator.personalization_note or '').strip()
    personal_line = ''
    if value:
        value = re.sub(r'\s+', ' ', value)
        # Staff writes a complete truthful sentence so no speculative wording is added.
        personal_line = value[:450]
    raw_body = variant.body
    if not personal_line:
        raw_body = re.sub(r'^.*\{personal_line\}.*(?:\n|$)', '', raw_body, flags=re.M)
    replacements = {
        'name': creator.name,
        'greeting': weekday_greeting(now, key=creator.email) if greeting is None else greeting,
        'personal_line': personal_line,
    }
    try:
        subject = variant.subject.format(**replacements).strip()
        body = raw_body.format(**replacements).strip()
    except (KeyError, ValueError) as exc:
        raise OutreachBlocked(f'Invalid template placeholders: {exc}') from exc
    body = re.sub(r'\n{3,}', '\n\n', body)
    if not subject or not body:
        raise OutreachBlocked('Subject and body must not be empty.')
    return {'subject': subject, 'body': body, 'variant': variant,
            'campaign': variant.campaign_name}


def prepare_approved_send(creator, *, now=None, variant=None):
    check_recipient(creator, now=now)
    if variant is None:
        choices = eligible_variants(now)
        if not choices:
            raise OutreachBlocked('There is no currently eligible APPROVED campaign. Nothing sent.')
        variant = random.choice(choices)
    require_approved_variant(variant, now)
    return render_variant(variant, creator, now=now)



def claim_approved_send(creator, prepared, message_id, provider):
    # Claim in a short DB transaction. Never hold locks during external HTTP.
    # On production PostgreSQL, select_for_update serializes this recipient.
    from django.db import transaction
    with transaction.atomic():
        locked = Creator.objects.select_for_update().get(pk=creator.pk)
        check_recipient(locked)
        variant = MilaniEmailVariant.objects.select_for_update().get(pk=prepared['variant'].pk)
        require_approved_variant(variant)
        current = render_variant(variant, locked)
        if current['subject'] != prepared['subject'] or current['body'] != prepared['body']:
            raise OutreachBlocked('Recipient or campaign changed; review before sending.')
        return MilaniOutreachLog.objects.create(
            creator=locked, subject=current['subject'], status='Sending',
            sendgrid_message_id=message_id, smtp_provider=provider,
            body_snapshot=current['body'], campaign_snapshot=current['campaign'])


def finish_claim(claim, provider_message_id):
    from django.db import transaction
    with transaction.atomic():
        log = MilaniOutreachLog.objects.select_for_update().get(pk=claim.pk)
        creator = Creator.objects.select_for_update().get(pk=log.creator_id)
        if log.status == 'Sending':
            log.status = 'Sent'
        log.provider_message_id = provider_message_id
        log.save(update_fields=['status', 'provider_message_id'])
        if creator.status not in ('Replied', *PERMANENT_BLOCK):
            creator.status = 'Sent'
        creator.last_outreach = timezone.now()
        creator.save(update_fields=['status', 'last_outreach'])


def mark_claim_uncertain(claim):
    # A request error does not prove the provider failed to accept the email.
    # Block automatic retries until a human reconciles this delivery.
    from django.db import transaction
    with transaction.atomic():
        log = MilaniOutreachLog.objects.select_for_update().get(pk=claim.pk)
        if log.status == 'Sending':
            log.status = 'Needs Review'
            log.save(update_fields=['status'])
        creator = Creator.objects.select_for_update().get(pk=log.creator_id)
        if creator.status not in ('Replied', *PERMANENT_BLOCK):
            creator.status = 'Needs Review'
            creator.last_outreach = timezone.now()
            creator.save(update_fields=['status', 'last_outreach'])
