"""Superuser-only manual batch console. Browser drives delivery; no cron."""
from django.contrib import admin, messages
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .milani_batch import (OutreachBlocked, confirm_batch, pause_batch, resume_batch,
                           prepare_draft, readiness_error, step_once, MAX_RECIPIENTS)
from .milani_outreach_v2 import eligible_variants
from .milani_batch_refresh import apply_refresh, preview_refresh
from .models import Creator, MilaniEmailVariant, MilaniLaunchBatch


def _allowed(request):
    return request.user.is_authenticated and request.user.is_active and request.user.is_superuser


def _home(request, *, error=''):
    ids = []
    for value in request.GET.get('ids', '').split(','):
        if value.isdecimal() and int(value) > 0:
            ids.append(int(value))
    selected = set(ids[:MAX_RECIPIENTS])
    creators = Creator.objects.all().order_by('-pk')[:250]
    return render(request, 'admin/api/creator/manual_batch.html', {
        'page': 'home', 'title': 'Manual creator batches', 'creators': creators,
        'selected': selected, 'variants': eligible_variants(),
        'error': error, 'max_batch': MAX_RECIPIENTS,
        'readiness': readiness_error(),
        'home_url': reverse('admin:milani_batch_home'),
    })


def home(request):
    if not _allowed(request):
        return JsonResponse({'error': 'Superuser access required'}, status=403)
    if request.method == 'GET':
        return _home(request)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['GET', 'POST'])
    try:
        variant = get_object_or_404(MilaniEmailVariant, pk=request.POST.get('variant_id'))
        ids = request.POST.getlist('creator_ids')
        if not all(x.isdecimal() for x in ids):
            raise OutreachBlocked('Select valid creator records only.')
        batch = prepare_draft(creator_ids=ids, variant=variant, operator=request.user)
        return redirect('admin:milani_batch_review', batch.pk)
    except (OutreachBlocked, ValueError, TypeError) as exc:
        return render(request, 'admin/api/creator/manual_batch.html', {
            'page': 'home', 'title': 'Manual creator batches',
            'creators': Creator.objects.all().order_by('-pk')[:250],
            'variants': eligible_variants(), 'selected': set(
                int(x) for x in request.POST.getlist('creator_ids') if x.isdecimal()),
            'error': str(exc), 'max_batch': MAX_RECIPIENTS,
            'readiness': readiness_error(),
            'home_url': reverse('admin:milani_batch_home'),
        }, status=400)


def _batch_for_user(request, batch_id):
    if not _allowed(request):
        return None
    batch = get_object_or_404(
        MilaniLaunchBatch.objects.select_related('variant','created_by'),
        pk=batch_id, created_by=request.user)
    return batch


def review(request, batch_id):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    batch = _batch_for_user(request, batch_id)
    if not batch:
        return JsonResponse({'error': 'Superuser access required'}, status=403)
    items = list(batch.recipients.select_related('creator').order_by('position'))
    ready = sum(x.status == 'pending' for x in items)
    freshness, freshness_issue = None, ''
    if (batch.status in ('draft', 'paused') and
            any(x.status in ('pending', 'blocked', 'processing', 'needs_review')
                for x in items)):
        try:
            freshness = preview_refresh(batch, request.user)
        except OutreachBlocked as exc:
            freshness_issue = str(exc)
    return render(request, 'admin/api/creator/manual_batch.html', {
        'page': 'review', 'title': 'Review and launch batch',
        'batch': batch, 'items': items, 'ready': ready,
        'freshness': freshness, 'freshness_issue': freshness_issue,
        'max_batch': MAX_RECIPIENTS, 'readiness': readiness_error(),
        'confirm_phrase': f'LAUNCH {ready}',
        'confirm_url': reverse('admin:milani_batch_confirm', args=[batch.pk]),
        'step_url': reverse('admin:milani_batch_step', args=[batch.pk]),
        'pause_url': reverse('admin:milani_batch_pause', args=[batch.pk]),
        'resume_url': reverse('admin:milani_batch_resume', args=[batch.pk]),
        'resume_phrase': f'RESUME {ready}',
        'refresh_url': reverse('admin:milani_batch_refresh', args=[batch.pk]),
        'home_url': reverse('admin:milani_batch_home'),
    })

def confirm(request, batch_id):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    batch = _batch_for_user(request, batch_id)
    if not batch:
        return JsonResponse({'error': 'Superuser access required'}, status=403)
    try:
        confirm_batch(batch, request.user, request.POST.get('confirmation', ''))
        messages.success(
            request, 'Batch confirmed but NOT SENT. Use Start Delivery to begin.')
    except OutreachBlocked as exc:
        messages.error(request, str(exc))
    return redirect('admin:milani_batch_review', batch.pk)


def step(request, batch_id):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    batch = _batch_for_user(request, batch_id)
    if not batch:
        return JsonResponse({'error': 'Superuser access required'}, status=403)
    try:
        result = step_once(batch.pk, request.user)
        return JsonResponse(result)
    except OutreachBlocked as exc:
        return JsonResponse({'state': 'stopped', 'reason': str(exc)}, status=409)


def pause(request, batch_id):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    batch = _batch_for_user(request, batch_id)
    if not batch:
        return JsonResponse({'error': 'Superuser access required'}, status=403)
    try:
        pause_batch(batch.pk, request.user)
        messages.info(request, 'This batch is paused. No additional emails will be sent.')
    except OutreachBlocked as exc:
        messages.error(request, str(exc))
    return redirect('admin:milani_batch_review', batch.pk)


@admin.action(description='Review selected creators in a new manual batch (no email sent)')
def creator_review_batch(modeladmin, request, queryset):
    if not _allowed(request):
        modeladmin.message_user(request, 'Only superusers may launch outreach batches.',
                                level=messages.ERROR)
        return None
    ids = list(queryset.values_list('pk', flat=True)[:MAX_RECIPIENTS + 1])
    if not ids or len(ids) > MAX_RECIPIENTS:
        modeladmin.message_user(request, 'Select between 1 and 20 creators for review.',
                                level=messages.ERROR)
        return None
    from urllib.parse import urlencode
    url = reverse('admin:milani_batch_home') + '?' + urlencode(
        {'ids': ','.join(map(str, ids))})
    return redirect(url)


def resume(request, batch_id):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    batch = _batch_for_user(request, batch_id)
    if not batch:
        return JsonResponse({'error': 'Superuser access required'}, status=403)
    try:
        resume_batch(batch.pk, request.user, request.POST.get('confirmation', ''))
        messages.success(request, 'Batch resumed but NOT SENDING. Start Delivery manually.')
    except OutreachBlocked as exc:
        messages.error(request, str(exc))
    return redirect('admin:milani_batch_review', batch.pk)


def refresh_preview(request, batch_id):
    """Always re-render from current creator/campaign data. No writes or sends."""
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    batch = _batch_for_user(request, batch_id)
    if not batch:
        return JsonResponse({'error': 'Superuser access required'}, status=403)
    try:
        plan = preview_refresh(batch, request.user)
    except OutreachBlocked as exc:
        messages.error(request, str(exc))
        return redirect('admin:milani_batch_review', batch.pk)
    return render(request, 'admin/api/creator/manual_batch.html', {
        'page': 'refresh', 'title': 'Refresh & Re-review prepared emails',
        'batch': batch, 'plan': plan,
        'apply_url': reverse('admin:milani_batch_refresh_apply', args=[batch.pk]),
        'review_url': reverse('admin:milani_batch_review', args=[batch.pk]),
        'refresh_phrase': f'REFRESH {len(plan["rows"])}',
    })


def refresh_apply(request, batch_id):
    """Explicit approval of EXACT prior preview. Still does not confirm/send."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    batch = _batch_for_user(request, batch_id)
    if not batch:
        return JsonResponse({'error': 'Superuser access required'}, status=403)
    try:
        count = batch.recipients.filter(status__in=['pending', 'blocked']).count()
        if request.POST.get('confirmation') != f'REFRESH {count}':
            raise OutreachBlocked(f'Type REFRESH {count} exactly to approve the updates.')
        result = apply_refresh(batch.pk, request.user, request.POST.get('refresh_token', ''))
        messages.success(request,
                         f'Re-reviewed {count} unsent recipients. '
                         f'{result["changed"]} changed. Nothing sent. '
                         'Confirm or resume separately when ready.')
        return redirect('admin:milani_batch_review', batch.pk)
    except OutreachBlocked as exc:
        messages.error(request, str(exc))
        return redirect('admin:milani_batch_refresh', batch.pk)
