"""Signed unsubscribe endpoints. GET never unsubscribes (safe from link prefetch).

POST accepts a signed, message-id-only token. It can be used by email
clients that support List-Unsubscribe-Post without requiring a login.
"""
from django.conf import settings
from django.core import signing
from django.http import HttpResponse
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt

from .models import MilaniOutreachLog, MilaniSuppression

SALT = 'milani-outreach-v2-unsubscribe'


def unsubscribe_url(message_id):
    from urllib.parse import quote
    base = getattr(settings, 'MILANI_PUBLIC_BASE_URL', '').strip().rstrip('/')
    if not base:
        raise ValueError('A verified MILANI_PUBLIC_BASE_URL is required for v2.')
    if not base.startswith('https://') and not (
        getattr(settings, 'ENVIRONMENT', '') == 'local' and base.startswith('http://localhost:')
    ):
        raise ValueError('The unsubscribe base URL must be HTTPS outside local preview.')
    token = signing.dumps({'mid': message_id}, salt=SALT)
    return base + '/api/milani/unsubscribe/?token=' + quote(token, safe='')


@csrf_exempt
def milani_unsubscribe(request):
    if request.method not in ('GET', 'POST'):
        return HttpResponse(status=405)
    token = (request.POST.get('token') or request.GET.get('token') or '').strip()
    if len(token) > 700:
        return HttpResponse('Invalid unsubscribe link.', status=400)
    try:
        obj = signing.loads(token, salt=SALT)
        mid = obj.get('mid', '')
        if not isinstance(mid, str) or len(mid) != 32:
            raise signing.BadSignature('Invalid message identifier.')
        log = MilaniOutreachLog.objects.select_related('creator').get(
            sendgrid_message_id=mid)
    except (signing.BadSignature, MilaniOutreachLog.DoesNotExist,
            ValueError, TypeError, AttributeError):
        return HttpResponse('This unsubscribe link is invalid.', status=400)
    if request.method == 'GET':
        # A crawler or email security gateway may GET this URL. Confirm explicitly.
        safe_token = escape(token)
        page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Unsubscribe</title></head><body style="font:16px system-ui;max-width:420px;
margin:12vh auto;padding:24px;line-height:1.5;color:#151515">
<h1>Unsubscribe from creator outreach</h1>
<p>Stop future creator partnership messages to this address?</p>
<form method="post"><input type="hidden" name="token" value="{safe_token}">
<button type="submit" style="background:#222;color:white;padding:12px 25px;
border:0;border-radius:8px;cursor:pointer">Confirm unsubscribe</button></form>
</body></html>"""
        response=HttpResponse(page)
    else:
        from django.db import transaction
        with transaction.atomic():
            creator=log.creator
            MilaniSuppression.objects.get_or_create(
                email=creator.email.strip().lower(),
                defaults={'reason':'Signed one-click unsubscribe request'})
            if not creator.do_not_contact:
                creator.do_not_contact=True
                creator.save(update_fields=['do_not_contact'])
        response=HttpResponse('Unsubscribed. You will receive no further creator outreach.')
    response['Cache-Control']='no-store'
    response['Referrer-Policy']='no-referrer'
    response['X-Content-Type-Options']='nosniff'
    return response
