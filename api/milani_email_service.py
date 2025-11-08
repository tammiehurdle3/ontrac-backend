from django.conf import settings
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email
from .models import Creator, MilaniOutreachLog 
from django.utils import timezone

# --- HARD-CODED CONSTANTS FOR SENDER AND TEMPLATE (New Addition) ---
MILANI_SENDER_EMAIL = 'diana@milanicollabs.com'
MILANI_TEMPLATE_ID = 'd-cdce9902e7d743019db050c08568dfa6'
MILANI_SENDER_NAME = 'Diana Higuera - Milani Cosmetics'
# -------------------------------------------------------------------

def send_milani_outreach_email(creator: Creator):
    """
    Sends the Milani outreach email to a single creator using SendGrid's Transactional API.
    All content (Subject, Preheader, Body) is managed entirely within the SendGrid template.
    """
    # CHANGE: Only check for the API Key now, as others are hard-coded.
    # The MILANI_TEMPLATE_ID check is removed here, but the code still uses the hardcoded value.
    if not settings.SENDGRID_API_KEY:
        print("CRITICAL: SendGrid API Key not configured.")
        return None

    # The dynamic data passed to the template is minimized to only include the creator's name.
    # The template itself will handle placing this name into the Subject, Preheader, and Body.
    template_data = {
        # This is the ONLY variable the code needs to generate for personalization.
        "creator_name": creator.name,
    }

    # CHANGE: Use hard-coded constants instead of reading from settings
    sender_email_with_name = Email(
        email=MILANI_SENDER_EMAIL,
        name=MILANI_SENDER_NAME 
    )

    # Note: When template_id and dynamic_template_data are provided, 
    # the subject/preheader in the Mail object are ignored.
    message = Mail(
        from_email=sender_email_with_name, 
        to_emails=creator.email,
        subject='', # Must be empty/default if using dynamic template subject
    )
    
    # CHANGE: Use hard-coded template ID
    message.template_id = MILANI_TEMPLATE_ID
    message.dynamic_template_data = template_data

    # Enable click and open tracking (essential for the webhook logs)

    try:
        sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
        response = sg.send(message)
        
        if 200 <= response.status_code < 300:
            # The message ID is found in the headers, essential for webhook matching
            message_id = response.headers.get('X-Message-Id')
            
            
            # 2. Update the Creator's status
            creator.status = 'Sent'
            creator.last_outreach = timezone.now()
            creator.save()
            
            print(f"✅ Milani email sent to {creator.email}. Message ID: {message_id}.")
            return True
        else:
            print(f"❌ SendGrid Error ({response.status_code}): {response.body.decode('utf-8')}")
            return False

    except Exception as e:
        print(f"❌ CRITICAL: Exception when calling SendGrid API: {e}")
        return False