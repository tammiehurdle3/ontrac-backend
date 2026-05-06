"""
api/management/commands/send_outreach.py
-----------------------------------------
Processes all Creator records with status='Queued' and sends the Milani
outreach email via Google Workspace SMTP.

Staggered sends: a configurable delay between each email is enforced here
to avoid Google rate limits (Gmail SMTP allows ~500/day on Workspace).

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

from django.core.management.base import BaseCommand, CommandError

from api.models import Creator
from api.milani_email_service import send_milani_outreach_email

# Google Workspace SMTP safe ceiling per run.
# Adjust downward if you see "Daily sending quota exceeded" errors.
DEFAULT_LIMIT = 100
DEFAULT_DELAY_SECONDS = 30  # 30s between sends = ~120 emails/hour comfortable margin


class Command(BaseCommand):
    help = (
        'Sends Milani outreach emails to all Creators with status=Queued. '
        'Applies a configurable inter-send delay to respect Google SMTP rate limits.'
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