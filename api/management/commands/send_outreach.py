# api/management/commands/send_outreach.py
from django.core.management.base import BaseCommand
from django.utils import timezone
from ...models import Creator # Note the relative import '...'
from ...milani_email_service import send_milani_outreach_email
import time

class Command(BaseCommand):
    help = 'Sends queued Milani outreach emails in a staggered fashion.'

    def handle(self, *args, **options):
        # We process creators that were set to 'Queued' by the Admin Action
        creators_to_send = Creator.objects.filter(status='Queued').order_by('last_outreach')[:100]
        
        if not creators_to_send.exists():
            self.stdout.write(self.style.NOTICE("No creators in the 'Queued' status to send to."))
            return
            
        self.stdout.write(self.style.SUCCESS(f"Starting outreach for {creators_to_send.count()} creators."))
        
        STAGGER_INTERVAL_SECONDS = 5 # 5 seconds delay between each email

        for i, creator in enumerate(creators_to_send):
            self.stdout.write(f"Processing ({i+1}/{creators_to_send.count()}): {creator.email}...")
            
            # Send the email
            success = send_milani_outreach_email(creator)
            
            if success:
                self.stdout.write(self.style.SUCCESS(f"    Sent successfully."))
            else:
                self.stdout.write(self.style.ERROR(f"    Failed to send. Reverting status to 'New Lead'."))
                creator.status = 'New Lead'
                creator.save()
            
            # Wait for the interval before the next send
            if i < creators_to_send.count() - 1:
                time.sleep(STAGGER_INTERVAL_SECONDS)
                
        self.stdout.write(self.style.SUCCESS("Milani outreach staggered sending complete."))