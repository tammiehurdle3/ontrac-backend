"""
milani_email_service.py
-----------------------
Handles Milani Cosmetics cold outreach via Google Workspace SMTP.

Architecture notes:
- Uses django.core.mail.get_connection() with explicit SMTP params.
  The global Django EMAIL_BACKEND is NEVER modified — OnTrac's transactional
  email pipeline (MailerSend / Resend / SendGrid via requests) is unaffected.
- Open tracking: a 1x1 pixel is embedded in the HTML. When the recipient
  opens the email and images load, /api/webhooks/milani-open/?mid={uuid}
  fires and milani_open_pixel (views.py) upgrades the log status to 'Opened'.
- Bounce/failure: caught at SMTP send time, logged as 'Failed'.
- Clicks: no HTTP links in the email body, so not applicable.
- MilaniOutreachLog.sendgrid_message_id column is reused as a generic
  unique message identifier (UUID) regardless of provider.
"""

from __future__ import annotations

import logging
import random
import uuid
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils import timezone

from .models import MilaniOutreachLog

if TYPE_CHECKING:
    from .models import Creator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sender identity — matches settings.py values.
# ---------------------------------------------------------------------------
MILANI_SENDER_NAME: str = getattr(settings, 'MILANI_SENDER_NAME', 'Diana Higuera')
MILANI_SENDER_EMAIL: str = getattr(settings, 'MILANI_SENDER_EMAIL', 'diana@milanicollabs.com')

# ---------------------------------------------------------------------------
# EDITABLE COPY VARIANTS
# ---------------------------------------------------------------------------
# Each entry is a dict with "subject" and "body" keys.
# Use {name} for creator name and {day} for the dynamic day of the week.
# The mailer picks one at random per send — add more variants to increase
# copy diversity and reduce Google's pattern-matching.
# DO NOT reference "Face Set. Mind Set." in this campaign.
# ---------------------------------------------------------------------------
OUTREACH_VARIANTS: list[dict[str, str]] = [
    # -- Variant A (base copy) -----------------------------------------------
    {
        "subject": "Paid Collaboration Inquiry: Milani Cosmetics x {name}",
        "body": (
            "Hi {name},\n\n"
            "{greeting}\n\n"
            "I'm Diana, and I'm a Senior Marketing Manager handling Brand Partnerships "
            "here at Milani Cosmetics.\n\n"
            "We are currently casting for a paid Spring Campaign launching in April, and we "
            "loved your authentic approach to skin-first beauty. We think your aesthetic would "
            "be a perfect fit for a new collection we are dropping.\n\n"
            "Are you open to discussing a collaboration?\n\n"
            "If so, please reply with your portfolio or media kit so I can share it with my "
            "Creative Director for sign-off. I will share the Creative Brief and full scope "
            "of the project once we clear that step.\n\n"
            "Best regards,\n"
            "Diana Higuera\n"
            "Senior Marketing & Communications Manager\n"
            "Milani Cosmetics | Los Angeles"
        ),
    },
    # -- Variant B -----------------------------------------------------------
    {
        "subject": "Spring Campaign — Milani Cosmetics x {name}",
        "body": (
            "Hi {name},\n\n"
            "{greeting}\n\n"
            "My name is Diana — I'm a Senior Marketing Manager at Milani Cosmetics "
            "and I head up our Brand Partnerships team.\n\n"
            "We are in the middle of casting for a paid Spring Campaign that launches in "
            "April. Your content caught our attention — specifically your skin-first "
            "approach and the way you connect with your audience. We believe you would "
            "be a natural fit for a new collection we have dropping this season.\n\n"
            "Would you be open to a collaboration conversation?\n\n"
            "If yes, reply with your media kit or portfolio and I will loop in my Creative "
            "Director. Once we have sign-off, I can send over the full Creative Brief and "
            "project scope.\n\n"
            "Best,\n"
            "Diana Higuera\n"
            "Senior Marketing & Communications Manager\n"
            "Milani Cosmetics | Los Angeles"
        ),
    },
    # -- Variant C -----------------------------------------------------------
    {
        "subject": "Brand Partnership Opportunity — Milani Cosmetics",
        "body": (
            "Hi {name},\n\n"
            "{greeting}\n\n"
            "I'm Diana, Senior Marketing Manager at Milani Cosmetics, where I handle all "
            "creator partnerships.\n\n"
            "I'm reaching out because we are actively casting for a paid Spring Campaign "
            "and your profile stood out to us. Your authentic, skin-first content is exactly "
            "the aesthetic we are looking for in a collection we are dropping this April.\n\n"
            "Are you currently open to brand partnerships?\n\n"
            "If so, I'd love to get the conversation started — just reply with your media "
            "kit or portfolio. Once I have that, I can share it with our Creative Director "
            "and send you the full Creative Brief if it is a mutual fit.\n\n"
            "Warm regards,\n"
            "Diana Higuera\n"
            "Senior Marketing & Communications Manager\n"
            "Milani Cosmetics | Los Angeles"
        ),
    },
    # -- Variant D -----------------------------------------------------------
    {
        "subject": "Collaboration Inquiry: Milani Cosmetics Spring Campaign",
        "body": (
            "Hi {name},\n\n"
            "{greeting}\n\n"
            "My name is Diana and I lead Brand Partnerships at Milani Cosmetics. I came across your work and wanted to personally reach out, as I believe you could be a strong fit for an upcoming paid collaboration.\n\n"
            "We’re curating a group of creators for our Spring Campaign launching in May. The focus is on elevated, skin-first beauty, and your content feels naturally aligned with this direction.\n\n"
            "If this resonates, please feel free to share your portfolio or media kit. I’ll review it alongside our Creative Director and, if aligned, share the Creative Brief and full project scope.\n\n"
            "Best Regards,\n\n"
            "Diana Higuera\n"
            "Senior Marketing & Communications Manager\n"
            "Milani Cosmetics | Los Angeles"
        ),
    },
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_contextual_greeting() -> str:
    """
    Returns a randomised, day-appropriate greeting sentence.
    Each day group has multiple variants that rotate randomly to avoid
    pattern-matching by spam filters.
    """
    _GREETINGS: dict[str, list[str]] = {
        'start_of_week': [
            "I hope your week is off to a strong start!",
            "Hope the week is treating you well so far!",
            "Wishing you a productive start to the week!",
        ],
        'midweek': [
            "Hope the week has been going smoothly for you!",
            "Hope you're having a great week so far!",
        ],
        'end_of_week': [
            "Hope you've had a good week.",
            "Hope you've had a smooth week so far.",
            "Hope your week's been treating you well.",
        ],
        'weekend': [
            "Hope you're having a wonderful weekend!",
            "Hope the weekend is treating you well!",
        ],
    }
    weekday = timezone.localtime(timezone.now()).weekday()  # 0=Mon … 6=Sun
    if weekday in (0, 1):
        pool = _GREETINGS['start_of_week']
    elif weekday in (2, 3):
        pool = _GREETINGS['midweek']
    elif weekday == 4:
        pool = _GREETINGS['end_of_week']
    else:
        pool = _GREETINGS['weekend']
    return random.choice(pool)


def _build_smtp_connection() -> tuple:
    """
    Reads the active Milani SMTP provider from SiteSettings and returns
    (connection, provider_key, sender_email) for the active account.

    provider_key is 'gmail' or 'ionos' — stored on MilaniOutreachLog
    so you can see in admin which account sent each email.

    Never touches the global Django EMAIL_BACKEND.
    Raises ValueError if the active provider's credentials are missing.
    """
    from .models import SiteSettings  # local import avoids circular at module load

    provider = SiteSettings.get_milani_smtp_provider()

    if provider == 'ionos':
        host = getattr(settings, 'MILANI_IONOS_SMTP_HOST', 'smtp.ionos.com')
        port = getattr(settings, 'MILANI_IONOS_SMTP_PORT', 587)
        use_tls = getattr(settings, 'MILANI_IONOS_SMTP_USE_TLS', True)
        username = getattr(settings, 'MILANI_IONOS_SMTP_USER', '')
        password = getattr(settings, 'MILANI_IONOS_SMTP_PASSWORD', '')
        sender_email = username
        if not username or not password:
            raise ValueError(
                "MILANI_IONOS_SMTP_USER and MILANI_IONOS_SMTP_PASSWORD must be set. "
                "Use your IONOS account password directly — no app password needed."
            )
    else:
        # Default: gmail
        provider = 'gmail'
        host = getattr(settings, 'MILANI_SMTP_HOST', 'smtp.gmail.com')
        port = getattr(settings, 'MILANI_SMTP_PORT', 587)
        use_tls = getattr(settings, 'MILANI_SMTP_USE_TLS', True)
        username = getattr(settings, 'MILANI_SMTP_USER', '')
        password = getattr(settings, 'MILANI_SMTP_PASSWORD', '')
        sender_email = username
        if not username or not password:
            raise ValueError(
                "MILANI_SMTP_USER and MILANI_SMTP_PASSWORD must be set. "
                "Generate a Google App Password at myaccount.google.com/apppasswords."
            )

    connection = get_connection(
        backend='django.core.mail.backends.smtp.EmailBackend',
        host=host,
        port=port,
        username=username,
        password=password,
        use_tls=use_tls,
        fail_silently=False,
    )
    return connection, provider, sender_email


def _build_html_body(plain_body: str, message_id: str) -> str:
    """
    Converts plain text body to minimal, deliverability-safe HTML.

    Embeds a 1x1 tracking pixel keyed on message_id. When the recipient
    opens the email and their client loads images, GET /api/webhooks/milani-open/
    fires and milani_open_pixel (views.py) upgrades the log status to 'Opened'.

    Paragraphs split on double newlines. Signature lines split on single newline.
    Dark mode compatible via CSS media query.
    """
    base_url = getattr(settings, 'SHIELDCLIMB_CALLBACK_BASE_URL', '').rstrip('/')
    pixel_url = f"{base_url}/api/webhooks/milani-open/?mid={message_id}"

    paragraphs = plain_body.strip().split('\n\n')
    html_paragraphs = []

    for para in paragraphs:
        lines = para.strip().split('\n')
        if len(lines) == 1:
            html_paragraphs.append(
                f'<p style="margin:0 0 16px 0;">{lines[0]}</p>'
            )
        else:
            # Multi-line blocks (signature) rendered as line-broken spans
            inner = '<br>'.join(line for line in lines)
            html_paragraphs.append(
                f'<p style="margin:0 0 16px 0;">{inner}</p>'
            )

    body_html = '\n    '.join(html_paragraphs)

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
      font-size: 15px;
      line-height: 1.6;
      margin: 0;
      padding: 0;
    }}
    .email-container {{ max-width: 540px; margin: 36px auto; padding: 0 24px; }}
    .unsub-text {{ font-size: 11px; color: #999999; margin-top: 24px; border-top: 1px solid #e8e8e8; padding-top: 16px; }}
    .unsub-link {{ color: #999999; text-decoration: underline; }}
    @media (prefers-color-scheme: dark) {{
      body, .email-container {{ background-color: #121212 !important; color: #e0e0e0 !important; }}
      .unsub-text {{ color: #777777 !important; border-top-color: #333 !important; }}
      .unsub-link {{ color: #777777 !important; }}
    }}
  </style>
</head>
<body>
  <div class="email-container">
    {body_html}
    <p class="unsub-text">
      You are receiving this email because we identified you as a great fit for our
      upcoming campaigns. If you are not interested in brand partnerships at this time,
      you can <a href="mailto:{MILANI_SENDER_EMAIL}?subject=Unsubscribe" class="unsub-link">unsubscribe here</a>.
    </p>
    <!-- open tracking pixel -->
    <img src="{pixel_url}" width="1" height="1" border="0"
         style="display:block;height:1px;width:1px;border:0;margin:0;padding:0;" alt="">
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Public API — called by admin actions and send_outreach management command
# ---------------------------------------------------------------------------

def send_milani_outreach_email(creator: 'Creator') -> bool:
    """
    Sends a Milani outreach email to the given Creator via Google Workspace SMTP.

    - Randomly selects one copy variant from OUTREACH_VARIANTS.
    - Injects creator name and current day of week into subject and body.
    - Embeds open tracking pixel in HTML version.
    - Hard bounces are caught as SMTP exceptions and logged as 'Failed'.
    - Writes MilaniOutreachLog on both success and failure.
    - Updates Creator.status on success.

    Returns True on success, False on failure. Never raises.
    """
    # -- 1. Generate unique message ID first (needed for pixel URL in HTML) --
    internal_message_id = uuid.uuid4().hex

    # -- 2. Build personalised copy ------------------------------------------
    variant = random.choice(OUTREACH_VARIANTS)
    greeting = _get_contextual_greeting()

    subject = variant['subject'].format(name=creator.name)
    plain_body = variant['body'].format(name=creator.name, greeting=greeting)
    html_body = _build_html_body(plain_body, internal_message_id)

    from_header = f"{MILANI_SENDER_NAME} <{MILANI_SENDER_EMAIL}>"

    # -- 3. Build SMTP connection ---------------------------------------------
    try:
        connection, smtp_provider, active_sender_email = _build_smtp_connection()
    except ValueError as config_err:
        logger.critical(f"[Milani SMTP] Configuration error: {config_err}")
        return False

    # -- 4. Compose and send --------------------------------------------------
    # from_header uses the active account's address, not the hardcoded module constant
    from_header = f"Diana Higuera <{active_sender_email}>"

    try:
        message = EmailMultiAlternatives(
            subject=subject,
            body=plain_body,
            from_email=from_header,
            to=[creator.email],
            headers={
                'List-Unsubscribe': f'<mailto:{active_sender_email}?subject=Unsubscribe>',
                'X-Mailer': 'Milani-Outreach/2.0',
                'X-Campaign-ID': internal_message_id,
            },
            connection=connection,
        )
        message.attach_alternative(html_body, 'text/html')
        message.send()

    except Exception as smtp_err:
        logger.error(f"[Milani SMTP/{smtp_provider}] Failed to send to {creator.email}: {smtp_err}")
        _write_log(creator, subject, 'Failed', internal_message_id, smtp_provider)
        creator.status = 'Failed'
        creator.save(update_fields=['status'])
        return False

    # -- 5. Persist success state ---------------------------------------------
    now = timezone.now()
    creator.status = 'Sent'
    creator.last_outreach = now
    creator.save(update_fields=['status', 'last_outreach'])

    _write_log(creator, subject, 'Sent', internal_message_id, smtp_provider)
    logger.info(
        f"[Milani SMTP/{smtp_provider}] Sent to {creator.email} | "
        f"subject='{subject}' | greeting='{greeting}' | mid={internal_message_id}"
    )
    return True



def _write_log(
    creator: 'Creator',
    subject: str,
    send_status: str,
    message_id: str,
    smtp_provider: str = '',
) -> None:
    """
    Writes a MilaniOutreachLog row.
    smtp_provider ('gmail' or 'ionos') is stored so the admin log shows
    exactly which sending account was used for each message.
    Silently swallows DB errors so a logging failure never kills the send loop.
    """
    try:
        MilaniOutreachLog.objects.get_or_create(
            sendgrid_message_id=message_id,
            defaults={
                'creator': creator,
                'subject': subject,
                'status': send_status,
                'smtp_provider': smtp_provider,
                'event_time': timezone.now(),
            }
        )
    except Exception as log_err:
        logger.warning(f"[Milani SMTP] MilaniOutreachLog write failed: {log_err}")