"""Authenticated Resend events for creator outreach V2.

Never trust an unsigned status change or create a recipient from webhook data.
Only associate events with an existing, provider-acknowledged outreach record.
"""
import base64
import binascii
import hashlib
import hmac
import json
import time

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import MilaniOutreachLog, MilaniSuppression

EVENT_STATUS = {
    'email.sent': 'Sent',
    'email.delivered': 'Delivered',
    'email.opened': 'Opened',
    'email.clicked': 'Clicked',
    'email.bounced': 'Bounced',
    'email.complained': 'Reported Spam',
    'email.failed': 'Dropped',
    'email.suppressed': 'Dropped',
}
RANK = {'Sending': 0, 'Needs Review': 0, 'Sent': 1,
        'Delivered': 2, 'Opened': 3, 'Clicked': 4}
HARD_STOP = {'Bounced', 'Dropped', 'Reported Spam', 'Invalid Email'}


def _b64(value):
    return base64.b64decode(value + '=' * (-len(value) % 4), validate=True)


def _valid_signature(request):
    secret = getattr(settings, 'MILANI_RESEND_WEBHOOK_SECRET', '').strip()
    if not secret.startswith('whsec_'):
        return False
    message = request.headers.get('Svix-Id') or request.headers.get('Webhook-Id', '')
    stamp = request.headers.get('Svix-Timestamp') or request.headers.get('Webhook-Timestamp', '')
    signatures = (request.headers.get('Svix-Signature')
                  or request.headers.get('Webhook-Signature', ''))
    if not message or len(message) > 255 or len(signatures) > 1024:
        return False
    try:
        timestamp = int(stamp)
        if abs(time.time() - timestamp) > 300:
            return False
        key = _b64(secret[6:])
        if not key:
            return False
        signed = message.encode() + b'.' + stamp.encode() + b'.' + request.body
        expected = hmac.new(key, signed, hashlib.sha256).digest()
        for entry in signatures.split():
            if entry.startswith('v1,'):
                try:
                    if hmac.compare_digest(_b64(entry[3:]), expected):
                        return True
                except (ValueError, binascii.Error):
                    continue
    except (ValueError, UnicodeError, binascii.Error):
        return False
    return False


@csrf_exempt
def verified_resend_milani_webhook(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'method not allowed'}, status=405)
    if not _valid_signature(request):
        return JsonResponse({'status': 'unauthorized'}, status=401)
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeError):
        return JsonResponse({'status': 'invalid payload'}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({'status': 'invalid payload'}, status=400)
    new_status = EVENT_STATUS.get(payload.get('type'))
    data = payload.get('data')
    if not new_status or not isinstance(data, dict):
        return JsonResponse({'status': 'ignored'})
    provider_id = data.get('email_id')
    recipients = data.get('to')
    if not isinstance(provider_id, str) or not 1 <= len(provider_id) <= 255:
        return JsonResponse({'status': 'ignored'})
    if isinstance(recipients, str):
        recipients = [recipients]
    if not isinstance(recipients, list) or len(recipients) != 1:
        return JsonResponse({'status': 'ignored'})
    recipient = recipients[0]
    if not isinstance(recipient, str):
        return JsonResponse({'status': 'ignored'})
    with transaction.atomic():
        log = (MilaniOutreachLog.objects.select_for_update()
               .select_related('creator')
               .filter(provider_message_id=provider_id,
                       smtp_provider='resend_collabs').first())
        if not log or log.creator.email.strip().casefold() != recipient.strip().casefold():
            return JsonResponse({'status': 'ignored'})
        creator = log.creator
        if log.status not in HARD_STOP and (
                new_status in HARD_STOP or
                RANK.get(new_status, 0) > RANK.get(log.status, 0)):
            log.status = new_status
            log.event_time = timezone.now()
            log.save(update_fields=['status', 'event_time'])
        if new_status in HARD_STOP:
            MilaniSuppression.objects.get_or_create(
                email=creator.email.strip().lower(),
                defaults={'reason': 'Verified Resend failure or complaint'})
            if not creator.do_not_contact or creator.status not in HARD_STOP:
                creator.do_not_contact = True
                creator.status = new_status
                creator.save(update_fields=['do_not_contact', 'status'])
        elif (not creator.do_not_contact and creator.status != 'Replied'
              and creator.status not in HARD_STOP
              and RANK.get(new_status, 0) > RANK.get(creator.status, 0)):
            creator.status = new_status
            creator.save(update_fields=['status'])
    return JsonResponse({'status': 'accepted'})
