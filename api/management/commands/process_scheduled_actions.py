# api/management/commands/process_scheduled_actions.py

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
import pusher
from django.conf import settings

from api.models import ScheduledAction
from api.email_service import send_transactional_email
from api.ai_shipment_generator import advance_shipment_stage


class Command(BaseCommand):
    help = 'Processes pending scheduled shipment actions. Run every 5 minutes via Railway cron.'

    def handle(self, *args, **options):
        now = timezone.now()

        pending = ScheduledAction.objects.filter(
            status='pending',
            execute_at__lte=now
        ).select_related('shipment').order_by('shipment_id', 'execute_at')

        if not pending.exists():
            self.stdout.write('No pending actions due.')
            return

        count = pending.count()
        self.stdout.write(
            f'Processing {count} scheduled action(s) due at or before '
            f'{now.strftime("%Y-%m-%d %H:%M UTC")}...'
        )

        pusher_client = None
        try:
            pusher_client = pusher.Pusher(
                app_id=settings.PUSHER_APP_ID,
                key=settings.PUSHER_KEY,
                secret=settings.PUSHER_SECRET,
                cluster=settings.PUSHER_CLUSTER,
                ssl=True
            )
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'Pusher init failed: {e}'))

        done = 0
        failed = 0

        for action in pending:
            self.stdout.write(
                f'  → [{action.id}] {action.shipment.trackingId} | '
                f'Stage: {action.stage_key or "—"} | '
                f'Email: {action.email_type or "—"}'
            )

            try:
                with transaction.atomic():
                    shipment = action.shipment

                    # Debug — print resolved destination so you can diagnose
                    # if wrong pipeline is being selected
                    dest_city = (getattr(shipment, 'destination_city', '') or '').strip()
                    dest_country = (getattr(shipment, 'destination_country', '') or '').strip()
                    if not dest_city or not dest_country:
                        parts = (getattr(shipment, 'destination', '') or '').split(',')
                        dest_city = parts[0].strip() if parts else ''
                        dest_country = parts[-1].strip() if len(parts) > 1 else ''
                    self.stdout.write(
                        f'    [debug] dest_city="{dest_city}" dest_country="{dest_country}"'
                    )

                    # ── Step 1: Advance stage ──────────────────────────────
                    if action.stage_key:
                        try:
                            result = advance_shipment_stage(
                                shipment,
                                target_stage_key=action.stage_key.strip()
                            )

                            # Apply all returned fields to the shipment model
                            SKIP_FIELDS = {'_stages_added', '_jumped_to_label', '_jumped_to_key'}
                            for field, value in result.items():
                                if field in SKIP_FIELDS:
                                    continue
                                if hasattr(shipment, field):
                                    setattr(shipment, field, value)
                            shipment.save()
                            shipment.refresh_from_db()

                            # Override description if custom one provided
                            if action.custom_event_description:
                                changed = False
                                if shipment.recentEvent:
                                    recent = dict(shipment.recentEvent)
                                    recent['description'] = action.custom_event_description
                                    shipment.recentEvent = recent
                                    changed = True
                                if shipment.allEvents:
                                    events = list(shipment.allEvents)
                                    if events:
                                        events[-1]['description'] = action.custom_event_description
                                    shipment.allEvents = events
                                    changed = True
                                if changed:
                                    shipment.save(update_fields=['recentEvent', 'allEvents'])

                            self.stdout.write(
                                self.style.SUCCESS(f'    ✅ Stage advanced to: {action.stage_key}')
                            )

                        except Exception as stage_err:
                            self.stdout.write(
                                self.style.ERROR(f'    ❌ Stage advance failed: {stage_err}')
                            )
                            raise stage_err

                    # ── Step 2: Send email ─────────────────────────────────
                    if action.email_type:
                        try:
                            shipment.refresh_from_db()
                            send_transactional_email(shipment, action.email_type)
                            self.stdout.write(
                                self.style.SUCCESS(f'    ✅ Email sent: {action.email_type}')
                            )
                        except Exception as email_err:
                            self.stdout.write(
                                self.style.ERROR(f'    ❌ Email failed: {email_err}')
                            )
                            raise email_err

                    # ── Step 3: Pusher ─────────────────────────────────────
                    if pusher_client:
                        try:
                            pusher_client.trigger(
                                f'shipment-{shipment.trackingId}',
                                'update',
                                {'message': 'Shipment updated'}
                            )
                        except Exception as pusher_err:
                            self.stdout.write(
                                self.style.WARNING(f'    ⚠ Pusher failed: {pusher_err}')
                            )

                    action.status = 'done'
                    action.executed_at = timezone.now()
                    action.save(update_fields=['status', 'executed_at'])
                    done += 1
                    self.stdout.write(self.style.SUCCESS(f'    ✅ Action {action.id} marked done.'))

            except Exception as e:
                error_msg = str(e)[:255]
                ScheduledAction.objects.filter(pk=action.pk).update(
                    status='failed',
                    executed_at=timezone.now(),
                    notes=error_msg,
                )
                failed += 1
                self.stdout.write(self.style.ERROR(f'    ❌ Action {action.id} failed: {error_msg}'))

        self.stdout.write(
            self.style.SUCCESS(
                f'\nDone. {done} succeeded, {failed} failed out of {count} total.'
            )
        )