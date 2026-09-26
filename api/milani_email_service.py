"""
milani_email_service.py
-----------------------
Handles Milani Cosmetics cold outreach via Resend API.

Architecture:
- Two isolated Resend accounts, one per sending domain.
- Active provider toggled via SiteSettings.milani_smtp_provider in admin.
- Variants are DB-driven (MilaniEmailVariant model) — editable from admin.
  Falls back to OUTREACH_VARIANTS if no active DB variants exist.
- Zero SMTP. All sends are HTTPS API calls via the Resend SDK.
- Completely isolated from OnTrac transactional email (RESEND_API_KEY).
- Open tracking via self-hosted 1x1 pixel at /api/webhooks/milani-open/
"""

from __future__ import annotations

import logging
import random
import uuid
from typing import TYPE_CHECKING

import resend as resend_sdk

from django.conf import settings
from django.utils import timezone
from django.utils.html import escape
from zoneinfo import ZoneInfo

from .models import MilaniOutreachLog

if TYPE_CHECKING:
    from .models import Creator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PROVIDER REGISTRY
# Maps SiteSettings.milani_smtp_provider → (api_key_setting, from_email)
# ---------------------------------------------------------------------------
_PROVIDER_CONFIG = {
    'resend_cosmetics': {
        'api_key_setting': 'MILANI_COSMETICS_RESEND_API_KEY',
        'from_email':      'diana@milani-cosmetics.com',
        'from_name':       'Diana Higuera',
        'log_label':       'resend_cosmetics',
    },
    'resend_collabs': {
        'api_key_setting': 'RESEND_MILANI_API_KEY',
        'from_email':      'diana@milanicollabs.com',
        'from_name':       'Diana Higuera',
        'log_label':       'resend_collabs',
    },
}
_DEFAULT_PROVIDER = 'resend_cosmetics'


# ---------------------------------------------------------------------------
# HARDCODED FALLBACK VARIANTS
# Used only when no active MilaniEmailVariant rows exist in the database.
# Edit the live variants directly in Django admin under Milani Email Variants.
# No em dashes. Season updated to Summer.
# ---------------------------------------------------------------------------
OUTREACH_VARIANTS: list[dict[str, str]] = [
    # -- Variant A -----------------------------------------------------------
    {
        "subject": "Paid Collaboration Inquiry: Milani Cosmetics x {name}",
        "body": (
            "Hi {name},\n\n"
            "{greeting}\n\n"
            "I'm Diana, and I'm a Senior Marketing Manager handling Brand Partnerships "
            "here at Milani Cosmetics.\n\n"
            "We are currently casting for a paid Summer Campaign and we loved your "
            "authentic approach to skin-first beauty. We think your aesthetic would be "
            "a perfect fit for a new collection we are dropping.\n\n"
            "Are you open to discussing a collaboration?\n\n"
            "If so, please reply with your portfolio or media kit so I can share it with "
            "my Creative Director for sign-off. I will share the Creative Brief and full "
            "scope of the project once we clear that step.\n\n"
            "Best regards,\n"
            "Diana Higuera\n"
            "Senior Marketing and Communications Manager\n"
            "Milani Cosmetics | Los Angeles"
        ),
    },
    # -- Variant B -----------------------------------------------------------
    {
        "subject": "Summer Campaign: Milani Cosmetics x {name}",
        "body": (
            "Hi {name},\n\n"
            "{greeting}\n\n"
            "My name is Diana. I'm a Senior Marketing Manager at Milani Cosmetics and "
            "I head up our Brand Partnerships team.\n\n"
            "We are in the middle of casting for a paid Summer Campaign. Your content "
            "caught our attention, particularly your skin-first approach and the way you "
            "connect with your audience. We believe you would be a natural fit for a "
            "new collection we have dropping this season.\n\n"
            "Would you be open to a collaboration conversation?\n\n"
            "If yes, reply with your media kit or portfolio and I will loop in my "
            "Creative Director. Once we have sign-off, I can send over the full "
            "Creative Brief and project scope.\n\n"
            "Best,\n"
            "Diana Higuera\n"
            "Senior Marketing and Communications Manager\n"
            "Milani Cosmetics | Los Angeles"
        ),
    },
    # -- Variant C -----------------------------------------------------------
    {
        "subject": "Brand Partnership Opportunity: Milani Cosmetics",
        "body": (
            "Hi {name},\n\n"
            "{greeting}\n\n"
            "I'm Diana, Senior Marketing Manager at Milani Cosmetics, where I handle "
            "all creator partnerships.\n\n"
            "I'm reaching out because we are actively casting for a paid Summer Campaign "
            "and your profile stood out to us. Your authentic, skin-first content is "
            "exactly the aesthetic we are looking for in a collection we are launching "
            "this summer.\n\n"
            "Are you currently open to brand partnerships?\n\n"
            "If so, I would love to get the conversation started. Just reply with your "
            "media kit or portfolio. Once I have that, I can share it with our Creative "
            "Director and send you the full Creative Brief if it is a mutual fit.\n\n"
            "Warm regards,\n"
            "Diana Higuera\n"
            "Senior Marketing and Communications Manager\n"
            "Milani Cosmetics | Los Angeles"
        ),
    },
    # -- Variant D -----------------------------------------------------------
    {
        "subject": "Collaboration Inquiry: Milani Cosmetics Summer Campaign",
        "body": (
            "Hi {name},\n\n"
            "{greeting}\n\n"
            "My name is Diana and I lead Brand Partnerships at Milani Cosmetics. I came "
            "across your work and wanted to personally reach out, as I believe you could "
            "be a strong fit for an upcoming paid collaboration.\n\n"
            "We are curating a group of creators for our Summer Campaign. The focus is "
            "on elevated, skin-first beauty, and your content feels naturally aligned "
            "with this direction.\n\n"
            "If this resonates, please feel free to share your portfolio or media kit. "
            "I will review it alongside our Creative Director and, if aligned, share "
            "the Creative Brief and full project scope.\n\n"
            "Best regards,\n"
            "Diana Higuera\n"
            "Senior Marketing and Communications Manager\n"
            "Milani Cosmetics | Los Angeles"
        ),
    },
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_contextual_greeting() -> str:
    """Day-aware greeting sentence. Rotates randomly within each day group."""
    _GREETINGS = {
        'start_of_week': [
            "I hope your week is off to a strong start!",
            "Hope the week is treating you well so far!",
            "Wishing you a productive start to the week!",
        ],
        'midweek': [
            "Hope the week has been going smoothly for you!",
            "Hope you are having a great week so far!",
        ],
        'end_of_week': [
            "Hope you have had a good week.",
            "Hope you have had a smooth week so far.",
            "Hope your week has been treating you well.",
        ],
        'weekend': [
            "Hope you are having a wonderful weekend!",
            "Hope the weekend is treating you well!",
        ],
    }
    # Only the outreach greeting uses LA; global shipping/payment timezone remains UTC.
    weekday = timezone.now().astimezone(ZoneInfo('America/Los_Angeles')).weekday()
    if weekday in (0, 1):
        pool = _GREETINGS['start_of_week']
    elif weekday in (2, 3):
        pool = _GREETINGS['midweek']
    elif weekday == 4:
        pool = _GREETINGS['end_of_week']
    else:
        pool = _GREETINGS['weekend']
    return random.choice(pool)


def _get_random_variant() -> dict:
    """
    Returns a random active variant dict with 'subject' and 'body' keys.
    Checks MilaniEmailVariant DB table first (admin-editable).
    Falls back to hardcoded OUTREACH_VARIANTS if DB has no active variants.
    """
    try:
        from .models import MilaniEmailVariant
        db_variants = list(
            MilaniEmailVariant.objects.filter(is_active=True).values('subject', 'body')
        )
        if db_variants:
            return random.choice(db_variants)
        logger.info("[Milani] No active DB variants found — using hardcoded fallback.")
    except Exception as e:
        logger.warning(f"[Milani] DB variant lookup failed, using hardcoded: {e}")
    return random.choice(OUTREACH_VARIANTS)


def _get_provider_config() -> dict:
    """
    Reads active provider from SiteSettings.
    Falls back to _DEFAULT_PROVIDER for any unknown/legacy value.
    """
    from .models import SiteSettings
    provider_key = SiteSettings.get_milani_smtp_provider()
    return _PROVIDER_CONFIG.get(provider_key) or _PROVIDER_CONFIG[_DEFAULT_PROVIDER]


def _build_html_body(plain_body: str, message_id: str, from_email: str) -> str:
    """
    Converts plain text to minimal, deliverability-safe HTML.
    Embeds a 1x1 open-tracking pixel keyed on message_id.
    Dark mode compatible via CSS media query.
    """
    v2 = getattr(settings, 'MILANI_OUTREACH_V2_ENABLED', False)
    test_mode = v2 and getattr(settings, 'MILANI_OUTREACH_TEST_MODE', False)
    if test_mode:
        # A single self-addressed test cannot use a private localhost HTTP link.
        # Do not claim the public signed-unsubscribe endpoint has been deployed.
        from_email_safe = escape(from_email)
        footer_html = (
            '<p class="unsub-text"><strong>Single-recipient test email.</strong> '
            'No mailing list is enabled. To request no further messages, '
            f'<a href="mailto:{from_email_safe}?subject=Unsubscribe">reply to unsubscribe</a>.'
            '</p>'
        )
        base_url = ''
    elif v2:
        from .milani_unsubscribe import unsubscribe_url
        unsub_url = unsubscribe_url(message_id)
        postal = getattr(settings, 'MILANI_SENDER_POSTAL_ADDRESS', '').strip()
        if not postal or (getattr(settings, 'ENVIRONMENT', '') != 'local' and 'TEST-ONLY' in postal):
            raise ValueError('Verified sender postal address is required for live outreach.')
        from_email_safe = escape(from_email)
        footer_html = (
            '<p class="unsub-text">You may stop future creator outreach at any time. '
            f'<a href="{escape(unsub_url)}" class="unsub-link">Unsubscribe</a><br>'
            f'{escape(postal)}</p>'
        )
        base_url = getattr(settings, 'MILANI_PUBLIC_BASE_URL', '').rstrip('/')
    else:
        from_email_safe = escape(from_email)
        footer_html = (
            '<p class="unsub-text">If you are not interested in future partnerships, '
            f'<a href="mailto:{from_email_safe}?subject=Unsubscribe" '
            'class="unsub-link">unsubscribe here</a>.</p>'
        )
        base_url = getattr(settings, 'SHIELDCLIMB_CALLBACK_BASE_URL', '').rstrip('/')
    pixel_url = f"{base_url}/api/webhooks/milani-open/?mid={message_id}"

    paragraphs = plain_body.strip().split('\n\n')
    html_paragraphs = []
    for para in paragraphs:
        lines = escape(para.strip()).split('\n')
        if len(lines) == 1:
            html_paragraphs.append(f'<p style="margin:0 0 16px 0;">{lines[0]}</p>')
        else:
            inner = '<br>'.join(lines)
            html_paragraphs.append(f'<p style="margin:0 0 16px 0;">{inner}</p>')

    body_html = '\n    '.join(html_paragraphs)
    pixel_markup = '' if test_mode else (
        f'<img src="{pixel_url}" width="1" height="1" border="0" '
        'style="display:block;height:1px;width:1px;border:0;margin:0;padding:0;" alt="">'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="color-scheme" content="light dark">
  <meta name="supported-color-schemes" content="light dark">
  <style>
    :root {{ color-scheme: light dark; }}
    body {{
      font-family: 'Aptos', 'Segoe UI', Arial, sans-serif;
      font-size: 15px; line-height: 1.6; margin: 0; padding: 0;
    }}
    .email-container {{ max-width: 540px; margin: 36px auto; padding: 0 24px; }}
    .unsub-text {{ font-size: 11px; color: #999; margin-top: 24px;
                   border-top: 1px solid #e8e8e8; padding-top: 16px; }}
    .unsub-link {{ color: #999; text-decoration: underline; }}
    @media (prefers-color-scheme: dark) {{
      body, .email-container {{ background-color: #121212 !important; color: #e0e0e0 !important; }}
      .unsub-text {{ color: #777 !important; border-top-color: #333 !important; }}
      .unsub-link {{ color: #777 !important; }}
    }}
  </style>
</head>
<body>
  <div class="email-container">
    {body_html}
    {footer_html}
    {pixel_markup}
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _outreach_headers(message_id, from_email, *, v2, test=False):
    headers = {
        'List-Unsubscribe': f'<mailto:{from_email}?subject=Unsubscribe>',
        'X-Campaign-ID': message_id,
    }
    test_mode = v2 and getattr(settings, 'MILANI_OUTREACH_TEST_MODE', False)
    if v2 and not test_mode:
        from .milani_unsubscribe import unsubscribe_url
        url = unsubscribe_url(message_id)
        headers['List-Unsubscribe'] = f'<{url}>, <mailto:{from_email}?subject=Unsubscribe>'
        headers['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
    if test or test_mode:
        headers['X-Test-Send'] = 'true'
    return headers


def _dispatch_outreach(creator: 'Creator', payload: dict):
    """Final provider-call boundary; normal disable flag cannot be bypassed."""
    if not getattr(settings, 'MILANI_OUTREACH_SEND_ENABLED', True):
        from .milani_batch import scope_authorized
        if not scope_authorized(creator) or payload.get('to') != [creator.email] or any(
                payload.get(k) for k in ('cc', 'bcc')):
            raise ValueError('Provider call denied: no verified manual batch scope.')
    if getattr(settings, 'MILANI_OUTREACH_TEST_MODE', False):
        if (getattr(settings, 'ENVIRONMENT', '') != 'local' or
                not getattr(settings, 'MILANI_OUTREACH_V2_ENABLED', False) or
                not getattr(settings, 'MILANI_OUTREACH_SEND_ENABLED', False) or
                creator.email.strip().casefold() != 'smthpines@gmail.com' or
                payload.get('to') != [creator.email] or
                any(payload.get(k) for k in ('cc', 'bcc'))):
            raise ValueError('Local single-test allowlist blocked this provider call.')
    return resend_sdk.Emails.send(payload)


def send_milani_outreach_email(creator: 'Creator') -> bool:
    """
    Sends a Milani outreach email to the given Creator via Resend API.
    Returns True on success, False on failure. Never raises.
    """
    if not getattr(settings, 'MILANI_OUTREACH_SEND_ENABLED', True):
        from .milani_batch import scope_authorized
        if not scope_authorized(creator):
            logger.warning('[Milani] Outbound sending disabled in this environment.')
            return False
    if getattr(settings, 'MILANI_OUTREACH_TEST_MODE', False) and (
            getattr(settings, 'ENVIRONMENT', '') != 'local' or
            not getattr(settings, 'MILANI_OUTREACH_V2_ENABLED', False) or
            creator.email.strip().casefold() != 'smthpines@gmail.com'):
        logger.warning('[Milani] Test recipient blocked before any DB claim.')
        return False
    v2 = getattr(settings, 'MILANI_OUTREACH_V2_ENABLED', False)
    prepared = None
    if v2:
        from .milani_outreach_v2 import OutreachBlocked, prepare_approved_send
        try:
            prepared = prepare_approved_send(creator)
        except OutreachBlocked as error:
            logger.warning('[Milani] Safe send blocked: %s', error)
            return False
    config     = _get_provider_config()
    api_key    = getattr(settings, config['api_key_setting'], '')
    from_email = config['from_email']
    from_name  = config['from_name']
    provider   = config['log_label']

    if not api_key:
        logger.critical(
            f"[Milani/{provider}] API key '{config['api_key_setting']}' is not set."
        )
        return False

    message_id  = uuid.uuid4().hex
    if v2:
        subject = prepared['subject']
        plain_body = prepared['body']
        campaign = prepared['campaign']
    else:
        variant = _get_random_variant()
        greeting = _get_contextual_greeting()
        subject = variant['subject'].format(name=creator.name)
        plain_body = variant['body'].format(name=creator.name, greeting=greeting)
        campaign = ''
    try:
        html_body = _build_html_body(plain_body, message_id, from_email)
        headers = _outreach_headers(message_id, from_email, v2=v2)
    except ValueError as error:
        logger.error('[Milani] Safe send blocked by missing configuration: %s', error)
        return False

    claim = None
    if v2:
        from .milani_outreach_v2 import claim_approved_send, OutreachBlocked
        try:
            claim = claim_approved_send(creator, prepared, message_id, provider)
        except OutreachBlocked as error:
            logger.warning('[Milani] Concurrent or ineligible recipient blocked: %s', error)
            return False
    try:
        resend_sdk.api_key = api_key
        response = _dispatch_outreach(creator, {
            "from":    f"{from_name} <{from_email}>",
            "to":      [creator.email],
            "subject": subject,
            "html":    html_body,
            "text":    plain_body,
            "headers": headers,
        })

        resend_id = (
            response.id if hasattr(response, 'id')
            else response.get('id') if isinstance(response, dict)
            else None
        )
        if not resend_id:
            raise ValueError(f"Unexpected Resend response: {response}")

    except Exception as send_err:
        logger.error(f"[Milani/{provider}] Delivery outcome requires review for {creator.email}: {send_err}")
        if v2 and claim is not None:
            from .milani_outreach_v2 import mark_claim_uncertain
            try:
                mark_claim_uncertain(claim)
            except Exception:
                logger.exception('[Milani] Could not mark uncertain send; claim remains for investigation.')
        else:
            _write_log(creator, subject, 'Failed', message_id, provider)
            creator.status = 'Failed'
            creator.save(update_fields=['status'])
        return False

    if v2:
        from .milani_outreach_v2 import finish_claim
        try:
            finish_claim(claim, resend_id)
        except Exception:
            logger.exception('[Milani] Provider accepted message, but DB update failed. Do not retry automatically.')
            return False
    else:
        now = timezone.now()
        creator.status = 'Sent'
        creator.last_outreach = now
        creator.save(update_fields=['status', 'last_outreach'])
        _write_log(creator, subject, 'Sent', message_id, provider)
    logger.info(
        f"[Milani/{provider}] Sent to {creator.email} | "
        f"resend_id={resend_id} | subject='{subject}'"
    )
    return True


def send_specific_milani_variant(creator: 'Creator', subject: str, body: str, *, variant_id=None) -> bool:
    """
    Sends a specific subject + body to a creator via the active Resend provider.
    Used for test sends from the admin preview page.
    Returns True on success, False on failure. Never raises.
    """
    if not getattr(settings, 'MILANI_OUTREACH_SEND_ENABLED', True):
        from .milani_batch import scope_authorized
        if not scope_authorized(creator, variant_id=variant_id):
            logger.warning('[Milani] Test sending disabled in this environment.')
            return False
    if getattr(settings, 'MILANI_OUTREACH_TEST_MODE', False) and (
            getattr(settings, 'ENVIRONMENT', '') != 'local' or
            not getattr(settings, 'MILANI_OUTREACH_V2_ENABLED', False) or
            creator.email.strip().casefold() != 'smthpines@gmail.com'):
        logger.warning('[Milani] Test recipient blocked before any DB claim.')
        return False
    v2 = getattr(settings, 'MILANI_OUTREACH_V2_ENABLED', False)
    prepared = None
    if v2:
        from .milani_outreach_v2 import OutreachBlocked, prepare_approved_send
        from .models import MilaniEmailVariant
        try:
            if not variant_id:
                raise OutreachBlocked('Variant ID is required for approved test sends.')
            variant = MilaniEmailVariant.objects.get(pk=variant_id)
            prepared = prepare_approved_send(creator, variant=variant)
        except (OutreachBlocked, MilaniEmailVariant.DoesNotExist) as error:
            logger.warning('[Milani] Specific send blocked: %s', error)
            return False
    config     = _get_provider_config()
    api_key    = getattr(settings, config['api_key_setting'], '')
    from_email = config['from_email']
    from_name  = config['from_name']
    provider   = config['log_label']

    if not api_key:
        logger.critical(f"[Milani/{provider}] API key '{config['api_key_setting']}' not set.")
        return False

    message_id = uuid.uuid4().hex
    greeting   = _get_contextual_greeting()

    try:
        if v2:
            subject_rendered = prepared['subject']
            body_rendered = prepared['body']
        else:
            subject_rendered = subject.format(name=creator.name)
            body_rendered = body.format(name=creator.name, greeting=greeting)
    except KeyError as e:
        logger.error(f"[Milani test send] Template placeholder error: {e}")
        return False

    try:
        html_body = _build_html_body(body_rendered, message_id, from_email)
        headers = _outreach_headers(message_id, from_email, v2=v2, test=True)
    except ValueError as error:
        logger.error('[Milani] Specific send blocked by missing configuration: %s', error)
        return False

    claim = None
    if v2:
        from .milani_outreach_v2 import claim_approved_send, OutreachBlocked
        try:
            claim = claim_approved_send(creator, prepared, message_id, provider)
        except OutreachBlocked as error:
            logger.warning('[Milani] Specific send blocked: %s', error)
            return False
    try:
        resend_sdk.api_key = api_key
        response = _dispatch_outreach(creator, {
            "from":    f"{from_name} <{from_email}>",
            "to":      [creator.email],
            "subject": subject_rendered,
            "html":    html_body,
            "text":    body_rendered,
            "headers": headers,
        })
        resend_id = (
            response.id if hasattr(response, 'id')
            else response.get('id') if isinstance(response, dict)
            else None
        )
        if not resend_id:
            raise ValueError(f"Unexpected Resend response: {response}")
    except Exception as send_err:
        logger.error(f"[Milani test/{provider}] Outcome requires review for {creator.email}: {send_err}")
        if v2 and claim is not None:
            from .milani_outreach_v2 import mark_claim_uncertain
            try:
                mark_claim_uncertain(claim)
            except Exception:
                logger.exception('[Milani] Specific send claim requires manual reconciliation.')
        return False

    if v2:
        from .milani_outreach_v2 import finish_claim
        try:
            finish_claim(claim, resend_id)
        except Exception:
            logger.exception('[Milani] Specific send accepted but log failed; never auto-retry.')
            return False
    else:
        _write_log(creator, subject_rendered, 'Sent', message_id, provider)
    logger.info(f"[Milani test/{provider}] Sent to {creator.email} | subject='{subject_rendered}'")
    return True


def _write_log(
    creator: 'Creator',
    subject: str,
    send_status: str,
    message_id: str,
    provider: str = '',
    *,
    body_snapshot: str = '',
    campaign_snapshot: str = '',
    provider_message_id=None,
) -> None:
    try:
        MilaniOutreachLog.objects.get_or_create(
            sendgrid_message_id=message_id,
            defaults={
                'creator':       creator,
                'subject':       subject,
                'status':        send_status,
                'smtp_provider': provider,
                'body_snapshot': body_snapshot,
                'campaign_snapshot': campaign_snapshot,
                'provider_message_id': provider_message_id,
                'event_time':    timezone.now(),
            }
        )
    except Exception as log_err:
        logger.warning(f"[Milani] Log write failed: {log_err}")