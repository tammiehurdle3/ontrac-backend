# api/email_service.py

from __future__ import print_function
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
from django.conf import settings
from babel.numbers import format_currency

# --- FINALIZED: All 6 Template IDs are now included ---
CONFIRMATION_TEMPLATE_ID = 2      # For: Step 1 | All Creators Confirmation Email
US_FEE_REPLIED_ID = 3         # For: Step 2 | US Fee (Replied version)
US_FEE_NO_REPLY_ID = 6        # For: Step 2 | US Fee (No Reply version)
INTL_TRACKING_REPLIED_ID = 4  # For: Step 2 | Intl Tracking (Replied version)
INTL_TRACKING_NO_REPLY_ID = 7 # For: Step 2 | Intl Tracking (No Reply version)
CUSTOMS_FEE_TEMPLATE_ID = 5     # For: Step 3 | Customs Fee Email
STATUS_UPDATE_TEMPLATE_ID = 8
# ----------------------------------------------------

def send_transactional_email(shipment, template_id):
    """
    A flexible function to send any transactional email template.
    """
    if not shipment.recipient_email:
        print(f"ERROR: Shipment {shipment.trackingId} has no recipient_email.")
        return

    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key['api-key'] = settings.BREVO_API_KEY
    api_instance = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))

    # --- Dynamically build the parameters for the template ---
    params = {
        "creator_name": getattr(shipment, 'recipient_name', 'Valued Creator'),
        "tracking_id": shipment.trackingId,
        "country_name": shipment.country or "your destination country"
    }

    # --- UPDATED: This logic now includes all payment-related templates ---
    if template_id in [US_FEE_REPLIED_ID, US_FEE_NO_REPLY_ID, CUSTOMS_FEE_TEMPLATE_ID]:
        amount = shipment.paymentAmount or 0.00
        currency_code = shipment.paymentCurrency.upper() if shipment.paymentCurrency else 'USD'
        
        formatted_amount = format_currency(amount, currency_code, locale='en_US')
        params["amount_due"] = formatted_amount
    # --- End of dynamic parameters ---
    
    if template_id == STATUS_UPDATE_TEMPLATE_ID:
        recent_event = shipment.recentEvent
        params['status'] = recent_event.get('status', 'Status not available')
        params['description'] = recent_event.get('description', 'No details available.')
        params['location'] = recent_event.get('location', '')

    send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": shipment.recipient_email, "name": params["creator_name"]}],
        template_id=template_id,
        params=params
    )

    try:
        api_instance.send_transac_email(send_smtp_email)
        print(f"✅ Brevo email (Template ID: {template_id}) sent successfully for shipment {shipment.trackingId}.")
    except ApiException as e:
        print(f"❌ CRITICAL: Exception when calling Brevo API: {e}\n")