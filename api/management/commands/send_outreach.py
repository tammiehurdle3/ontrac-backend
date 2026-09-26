"""
api/management/commands/send_outreach.py
-----------------------------------------
Processes all Creator records with status='Queued' and sends the Milani
outreach email via Resend API.

Staggered sends: a configurable delay between each email is enforced here
to avoid provider throttling. The v2 system has a lower configurable run limit.

Usage:
    python manage.py send_outreach
    python manage.py send_outreach --delay 45       # 45s between sends
    python manage.py send_outreach --limit 50       # cap at 50 sends per run
    python manage.py send_outreach --dry-run        # preview without sending

Railway cron: set a scheduled job to run this command.
The admin "Queue Bulk Outreach" action sets creators to 'Queued'.
This command picks them up and fires the sends.
"""

import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from api.models import Creator
from api.milani_email_service import send_milani_outreach_email

# Legacy default. V2 applies MILANI_OUTREACH_MAX_BATCH separately.
DEFAULT_LIMIT = 100
DEFAULT_DELAY_SECONDS = 30  # 30s between sends = ~120 emails/hour comfortable margin


class Command(BaseCommand):
    help = (
        'Sends Milani outreach emails to all Creators with status=Queued. '
        'Applies a configurable inter-send delay and v2 approval and run-limit gates.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--delay',
            type=int,
            default=DEFAULT_DELAY_SECONDS,
            help=f'Seconds to wait between each send (default: {DEFAULT_DELAY_SECONDS}).',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=DEFAULT_LIMIT,
            help=f'Maximum number of emails to send per run (default: {DEFAULT_LIMIT}).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Preview queued creators without sending any emails.',
        )

    def handle(self, *args, **options):
        delay: int = options['delay']
        limit: int = options['limit']
        dry_run: bool = options['dry_run']
        if limit < 1:
            raise CommandError('--limit must be at least 1.')
        if delay < 0:
            raise CommandError('--delay cannot be negative.')
        if getattr(settings, 'MILANI_OUTREACH_V2_ENABLED', False):
            if not dry_run and (getattr(settings, 'MILANI_OUTREACH_TEST_MODE', False) or
                                not getattr(settings, 'MILANI_OUTREACH_BULK_ENABLED', False)):
                self.stdout.write(self.style.WARNING('V2 bulk delivery is disabled. Nothing sent.'))
                return
            from api.milani_outreach_v2 import eligible_variants
            if not eligible_variants():
                self.stdout.write(self.style.WARNING(
                    'No eligible approved campaign for the Los Angeles date. Nothing sent.'))
                return
            if not dry_run and not getattr(settings, 'MILANI_OUTREACH_SEND_ENABLED', False):
                self.stdout.write(self.style.WARNING(
                    'Local preview: all external email delivery is disabled. Nothing sent.'))
                return
            cap = getattr(settings, 'MILANI_OUTREACH_MAX_BATCH', 20)
            if cap < 1:
                self.stdout.write(self.style.WARNING(
                    'Configured v2 batch limit is zero. Nothing sent.'))
                return
            if limit > cap:
                limit = cap
                self.stdout.write(self.style.WARNING(
                    f'Outreach v2 run limited to {limit} recipients for safety.'))
            if not dry_run and delay < 30:
                delay = 30
                self.stdout.write(self.style.WARNING(
                    'Outreach v2 enforces at least 30 seconds between sends.'))

        queued = (
            Creator.objects
            .filter(status='Queued')
            .order_by('last_outreach', 'id')[:limit]
        )

        total = queued.count()

        if total == 0:
            self.stdout.write(self.style.WARNING('No creators with status=Queued found. Nothing to do.'))
            return

        self.stdout.write(
            f'\n{"[DRY RUN] " if dry_run else ""}'
            f'Found {total} queued creator(s). '
            f'Delay: {delay}s between sends. Limit: {limit}.\n'
        )

        sent = 0
        failed = 0
        skipped = 0

        for index, creator in enumerate(queued, start=1):
            self.stdout.write(
                f'  [{index}/{total}] {creator.name} <{creator.email}>'
            )

            if dry_run:
                self.stdout.write(self.style.SUCCESS('    → [DRY RUN] Would send — skipping.'))
                skipped += 1
                continue

            success = send_milani_outreach_email(creator)

            if success:
                sent += 1
                self.stdout.write(self.style.SUCCESS(f'    → ✅ Sent'))
            else:
                failed += 1
                # Status is not reset — creator stays 'Queued' or 'Sent' as
                # milani_email_service sets it. A failed send leaves status unchanged
                # so the admin can retry by re-queuing.
                self.stdout.write(self.style.ERROR(f'    → ❌ Failed (see logs for detail)'))

            # Throttle — skip delay after the last send to avoid unnecessary wait.
            if index < total and delay > 0:
                self.stdout.write(f'    ⏱  Waiting {delay}s before next send...')
                time.sleep(delay)

        # ── Summary ───────────────────────────────────────────────────────
        self.stdout.write('\n' + '─' * 50)
        if dry_run:
            self.stdout.write(
                self.style.WARNING(f'[DRY RUN] {skipped} email(s) previewed. Nothing was sent.')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Done. Sent: {sent} | Failed: {failed} | Total processed: {total}'
                )
            )
        self.stdout.write('─' * 50 + '\n')