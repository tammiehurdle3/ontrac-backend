from __future__ import print_function

from mailersend.client import MailerSendClient
from pydantic import BaseModel, Field
from typing import List
from django.conf import settings
from babel.numbers import format_currency

# --- MODELS FOR THE DIRECT HTML PAYLOAD ---
class From(BaseModel):
    email: str
    name: str

class To(BaseModel):
    email: str
    name: str

class CustomEmailParams(BaseModel):
    from_sender: From = Field(..., alias='from')
    to: List[To]
    subject: str
    html: str

# --- FINAL, POLISHED HTML TEMPLATES ---

# This is a base template. The content will be dynamically inserted.
BASE_HTML_TEMPLATE = """
<!DOCTYPE html><html><head><title>{subject}</title></head><body style="background-color: #f2f2f2; margin: 0; padding: 0; font-family: Arial, Helvetica, sans-serif;"><table border="0" cellpadding="0" cellspacing="0" width="100%"><tr><td align="center" style="padding: 20px 0;"><table border="0" cellpadding="0" cellspacing="0" width="600" style="border-collapse: collapse; background-color: #ffffff; border-radius: 8px; overflow: hidden;"><tr><td align="center" style="padding: 40px 0 20px 0;"><img src="https://img.mailinblue.com/9891055/images/content_library/original/68c4ae677a9b83494e12391a.png" alt="OnTrac Courier Logo" width="180" style="display: block;" /></td></tr><tr><td style="padding: 20px 40px; color: #3b3f44; font-size: 16px; line-height: 1.6;"><h2 style="color: #1f2d3d; font-size: 26px; font-weight: bold; margin: 0 0 20px 0; text-align: center;">{heading}</h2>{main_body}</td></tr><tr><td align="center" style="padding: 30px 40px; background-color: #eff2f7; border-top: 1px solid #e1e1e1;"><table border="0" cellpadding="0" cellspacing="0" style="margin-bottom: 20px;"><tr><td style="padding: 0 8px;"><a href="#" target="_blank"><img src="https://creative-assets.mailinblue.com/editor/social-icons/rounded_colored/facebook_32px.png" width="32" alt="Facebook"></a></td><td style="padding: 0 8px;"><a href="#" target="_blank"><img src="https://creative-assets.mailinblue.com/editor/social-icons/rounded_colored/instagram_32px.png" width="32" alt="Instagram"></a></td><td style="padding: 0 8px;"><a href="#" target="_blank"><img src="https://creative-assets.mailinblue.com/editor/social-icons/rounded_colored/linkedin_32px.png" width="32" alt="LinkedIn"></a></td><td style="padding: 0 8px;"><a href="#" target="_blank"><img src="https://creative-assets.mailinblue.com/editor/social-icons/rounded_colored/youtube_32px.png" width="32" alt="YouTube"></a></td></tr></table><p style="margin: 0; color: #555555; font-size: 12px; line-height: 1.5;"><strong>OnTrac Courier</strong> | 7400 W Buckeye Rd, Phoenix, AZ 85043</p><p style="margin: 5px 0; color: #555555; font-size: 12px; line-height: 1.5;">This is a transactional email regarding your creator partnership.</p><p style="margin: 10px 0 0 0;"><a href="{unsubscribe}" style="color: #0092ff; font-size: 12px;">Unsubscribe</a></p></td></tr></table></td></tr></table></body></html>
"""

STATUS_UPDATE_HTML = """
<!DOCTYPE html><html><head><title>{subject}</title></head><body style="background-color: #f2f2f2; margin: 0; padding: 0; font-family: Arial, Helvetica, sans-serif;"><table border="0" cellpadding="0" cellspacing="0" width="100%"><tr><td align="center" style="padding: 20px 0;"><table border="0" cellpadding="0" cellspacing="0" width="600" style="border-collapse: collapse; background-color: #ffffff; border-radius: 8px; overflow: hidden;"><tr><td align="center" style="padding: 40px 0 20px 0;"><img src="https://img.mailinblue.com/9891055/images/content_library/original/68c4ae677a9b83494e12391a.png" alt="OnTrac Courier Logo" width="180" style="display: block;" /></td></tr><tr><td style="padding: 20px 40px; color: #3b3f44; font-size: 16px; line-height: 1.6;"><h2 style="color: #1f2d3d; font-size: 26px; font-weight: bold; margin: 0 0 20px 0; text-align: center;">Shipment Status Update</h2><p style="margin: 0 0 20px 0;">Hello {creator_name},</p><p style="margin: 0 0 25px 0;">Here is the latest tracking update for your shipment with OnTrac.</p><table border="0" cellpadding="12" cellspacing="0" width="100%" style="border: 1px solid #e1e1e1; border-radius: 5px; margin-bottom: 25px;"><tr><td style="background-color: #f7f7f7; width: 150px;"><strong>Tracking ID:</strong></td><td>{tracking_id}</td></tr><tr><td style="background-color: #f7f7f7;"><strong>Current Status:</strong></td><td>{status}</td></tr><tr><td style="background-color: #f7f7f7;"><strong>Details:</strong></td><td>{description}</td></tr></table><div style="text-align: center; margin: 30px 0;"><a href="https://ontracourier.us" target="_blank" style="background-color: #d22730; color: #ffffff; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold;">View Full Tracking History</a></div><p>We look forward to a successful delivery.</p><br/><p>Thank you,<br><strong>The OnTrac Team</strong></p></td></tr><tr><td align="center" style="padding: 30px 40px; background-color: #eff2f7; border-top: 1px solid #e1e1e1;"><table border="0" cellpadding="0" cellspacing="0" style="margin-bottom: 20px;"><tr><td style="padding: 0 8px;"><a href="#" target="_blank"><img src="https://creative-assets.mailinblue.com/editor/social-icons/rounded_colored/facebook_32px.png" width="32" alt="Facebook"></a></td><td style="padding: 0 8px;"><a href="#" target="_blank"><img src="https://creative-assets.mailinblue.com/editor/social-icons/rounded_colored/instagram_32px.png" width="32" alt="Instagram"></a></td><td style="padding: 0 8px;"><a href="#" target="_blank"><img src="https://creative-assets.mailinblue.com/editor/social-icons/rounded_colored/linkedin_32px.png" width="32" alt="LinkedIn"></a></td><td style="padding: 0 8px;"><a href="#" target="_blank"><img src="https://creative-assets.mailinblue.com/editor/social-icons/rounded_colored/youtube_32px.png" width="32" alt="YouTube"></a></td></tr></table><p style="margin: 0; color: #555555; font-size: 12px; line-height: 1.5;"><strong>OnTrac Courier</strong> | 7400 W Buckeye Rd, Phoenix, AZ 85043</p><p style="margin: 10px 0 0 0;"><a href="{unsubscribe}" style="color: #0092ff; font-size: 12px;">Unsubscribe</a></p></td></tr></table></td></tr></table></body></html>
"""
# --------------------------------------------------------------------

MAILERSEND_SENDER_EMAIL = 'support@ontracourier.us'
MAILERSEND_SENDER_NAME = 'OnTrac Courier'

def send_transactional_email(shipment, email_type: str):
    if not shipment.recipient_email:
        print(f"ERROR: Shipment {shipment.trackingId} has no recipient_email.")
        return

    # --- 1. Define all dynamic content ---
    subject, heading, main_body, html_template = "", "", "", BASE_HTML_TEMPLATE
    replied_greeting = "<p>Thank you for confirming receipt of our initial notification.</p>"
    creator_name = getattr(shipment, 'recipient_name', 'Creator')

    # --- 2. BUILD THE COMPLETE EMAIL CONTENT BASED ON TYPE ---
    if email_type == 'confirmation':
        subject = f"Your Milani Shipment is Confirmed (Tracking #{shipment.trackingId})"
        heading = "Confirming Your Milani Cosmetics Shipment"
        main_body = f"""
            <p>Hello {creator_name},</p>
            <p>This is a notification from OnTrac. Your package from Milani Cosmetics has been processed in our system and is being prepared for shipment.</p>
            <p style="background-color: #f7f7f7; padding: 10px; border-radius: 4px; margin-top: 20px;"><strong>Tracking ID:</strong> {shipment.trackingId}</p>
            <p>To ensure our automated delivery alerts are reaching your inbox correctly for all future updates, would you mind sending a quick 'GOT IT' or 'OK' in reply?</p>
            <p>The direct tracking link will be sent in a separate email shortly.</p>
            <br>
            <p>Thank you,<br><strong>The OnTrac Team</strong></p>
        """
    
    elif email_type == 'intl_tracking':
        subject = f"Tracking Information for Your Shipment #{shipment.trackingId}"
        heading = "Shipment Tracking Details"
        greeting = replied_greeting if shipment.creator_replied else "<p>Following up on our previous message, here are the details and the direct link to our tracking portal. Your package is being prepared for dispatch.</p>"
        summary_table = f"""
            <table border="0" cellpadding="12" cellspacing="0" width="100%" style="border: 1px solid #e1e1e1; border-radius: 5px; margin: 25px 0;">
                <tr><td style="background-color: #f7f7f7; width: 150px;"><strong>Tracking ID:</strong></td><td>{shipment.trackingId}</td></tr>
                <tr><td style="background-color: #f7f7f7;"><strong>Status:</strong></td><td>{shipment.status}</td></tr>
                <tr><td style="background-color: #f7f7f7;"><strong>Destination:</strong></td><td>{shipment.destination}</td></tr>
                <tr><td style="background-color: #f7f7f7;"><strong>Expected Date:</strong></td><td>{shipment.expectedDate}</td></tr>
            </table>
        """
        main_body = f"""
            <p>Hello {creator_name},</p>
            {greeting}
            {summary_table}
            <p><strong>How to track your package:</strong></p>
            <ol style="padding-left: 20px; margin-top: 0;">
                <li>Click the button below to go to our tracking homepage.</li>
                <li>Copy and paste your Tracking ID into the search field.</li>
            </ol>
            <div style="text-align: center; margin: 30px 0;"><a href="https://ontracourier.us" target="_blank" style="background-color: #d22730; color: #ffffff; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold;">Track Your Package</a></div>
            <p>We look forward to a successful delivery.</p>
            <p>Thank you,<br><strong>The OnTrac Team</strong></p>
        """

    elif email_type == 'intl_arrived':
        subject = f"Your OnTrac Shipment has arrived in {shipment.country or 'your destination country'}"
        heading = f"Your Shipment Has Arrived in {shipment.country or 'your country'}"
        main_body = f"""
            <p>Hello {creator_name},</p>
            <p>We're pleased to let you know that your shipment, tracking ID <strong>{shipment.trackingId}</strong>, has successfully arrived in {shipment.country or 'your country'}.</p>
            <p>It will now be processed for final clearance before being scheduled for delivery. We will notify you immediately if any further action is required on your part.</p>
            <p>In the meantime, you can continue to track your package using the button below.</p>
            <div style="text-align: center; margin: 30px 0;"><a href="https://ontracourier.us" target="_blank" style="background-color: #d22730; color: #ffffff; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold;">Track Your Package</a></div>
            <p>Thank you,<br><strong>The OnTrac Team</strong></p>
        """

    elif email_type == 'us_fee':
        subject = f"Action Required: Finalize Your Milani Shipment #{shipment.trackingId}"
        heading = "Finalize Your Milani Cosmetics Shipment"
        amount_due = format_currency(shipment.paymentAmount or 0.00, shipment.paymentCurrency or 'USD', locale='en_US')
        greeting = replied_greeting if shipment.creator_replied else ""
        summary_table = f"""
            <table border="0" cellpadding="12" cellspacing="0" width="100%" style="border: 1px solid #e1e1e1; border-radius: 5px; margin: 25px 0;">
                <tr><td style="background-color: #f7f7f7; width: 150px;"><strong>Tracking ID:</strong></td><td>{shipment.trackingId}</td></tr>
                <tr><td style="background-color: #f7f7f7;"><strong>Status:</strong></td><td>{shipment.status}</td></tr>
                <tr><td style="background-color: #f7f7f7;"><strong>Amount Due:</strong></td><td style="font-weight: bold; color: #d22730;">{amount_due}</td></tr>
            </table>
        """
        main_body = f"""
            <p>Hello {creator_name},</p>
            {greeting}
            <p>Your package is now ready for the final step before being dispatched. A standard pre-payment for shipping and handling is required to release this shipment for delivery.</p>
            {summary_table}
            <p>To complete this payment securely, please follow these steps:</p>
            <ol style="padding-left: 20px; margin-top: 0;">
                <li>Click the button below to go to our tracking homepage.</li>
                <li>Copy and paste your Tracking ID into the search field.</li>
                <li>On your shipment details page, locate and click the "Pay Now" button to finalize the fee.</li>
            </ol>
            <div style="text-align: center; margin: 30px 0;"><a href="https://ontracourier.us" target="_blank" style="background-color: #d22730; color: #ffffff; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold;">Finalize Delivery</a></div>
            <p>Once payment is confirmed, your package will be immediately dispatched.</p>
            <p>Our support team is available to help with any questions or issues. For the quickest assistance, please contact us via the live chat feature on our website.</p>
            <br>
            <p>Thank you,<br><strong>The OnTrac Team</strong></p>
        """
    
    elif email_type == 'customs_fee':
        subject = f"Action Required: Custom Clearance for Shipment #{shipment.trackingId}"
        heading = "Finalize Customs Clearance for Your Shipment"
        amount_due = format_currency(shipment.paymentAmount or 0.00, shipment.paymentCurrency or 'USD', locale='en_US')
        summary_table = f"""
            <table border="0" cellpadding="12" cellspacing="0" width="100%" style="border: 1px solid #e1e1e1; border-radius: 5px; margin: 25px 0;">
                <tr><td style="background-color: #f7f7f7; width: 150px;"><strong>Tracking ID:</strong></td><td>{shipment.trackingId}</td></tr>
                <tr><td style="background-color: #f7f7f7;"><strong>Current Status:</strong></td><td>{shipment.status}</td></tr>
                <tr><td style="background-color: #f7f7f7;"><strong>Customs Fee Due:</strong></td><td style="font-weight: bold; color: #d22730;">{amount_due}</td></tr>
            </table>
        """
        main_body = f"""
            <p>Hello {creator_name},</p>
            <p>This is an important notification regarding your Milani Cosmetics shipment, which has arrived in {shipment.country or 'your country'}. It has been processed by customs and is now awaiting a standard import duty payment before it can be released for final delivery.</p>
            {summary_table}
            <p>To complete this payment securely, please follow these steps:</p>
            <ol style="padding-left: 20px; margin-top: 0;">
                <li>Click the button below to go to our tracking homepage.</li>
                <li>Copy and paste your Tracking ID into the search field.</li>
                <li>On your shipment details page, locate and click the "Pay Now" button to finalize the fee.</li>
            </ol>
            <div style="text-align: center; margin: 30px 0;"><a href="https://ontracourier.us" target="_blank" style="background-color: #d22730; color: #ffffff; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold;">Pay Customs Fee</a></div>
            <p>Once payment is confirmed, your package will be immediately dispatched.</p>
            <p>Our support team is available to help with any questions or issues. For the quickest assistance, please contact us via the live chat feature on our website.</p>
            <br>
            <p>Thank you,<br><strong>The OnTrac Team</strong></p>
        """

    elif email_type == 'customs_fee_reminder':
        subject = f"URGENT: Payment Reminder - Customs Hold on Shipment #{shipment.trackingId}"
        heading = "Finalize Customs Clearance for Your Shipment"
        amount_due = format_currency(shipment.paymentAmount or 0.00, shipment.paymentCurrency or 'USD', locale='en_US')
        summary_table = f"""
            <table border="0" cellpadding="12" cellspacing="0" width="100%" style="border: 1px solid #e1e1e1; border-radius: 5px; margin: 25px 0;">
                <tr><td style="background-color: #f7f7f7; width: 150px;"><strong>Tracking ID:</strong></td><td>{shipment.trackingId}</td></tr>
                <tr><td style="background-color: #f7f7f7;"><strong>Current Status:</strong></td><td>{shipment.status}</td></tr>
                <tr><td style="background-color: #f7f7f7;"><strong>Customs Fee Due:</strong></td><td style="font-weight: bold; color: #d22730;">{amount_due}</td></tr>
            </table>
        """
        main_body = f"""
            <p>Hello {creator_name},</p>
            <p>This is a friendly notification regarding your Milani Cosmetics shipment, which remains pending release by local Customs in {shipment.country or 'your country'} awaiting an import duty payment.</p>
            <p>Our records indicate this shipment is still on hold due to the outstanding import duty payment. Immediate action is required. Please note that failure to finalize this fee promptly may result in the package incurring storage fees (demurrage) or being returned back to the sender.</p>
            {summary_table}
            <p>You can finalize the payment by clicking the button below and entering your tracking ID on our website.</p>
            <div style="text-align: center; margin: 30px 0;"><a href="https://ontracourier.us" target="_blank" style="background-color: #d22730; color: #ffffff; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold;">Pay Customs Fee Now</a></div>
            <p>If you have already completed this payment, please disregard this notice. If you have any questions, please contact our support team via live chat.</p>
            <br>
            <p>Thank you,<br><strong>The OnTrac Team</strong></p>
        """

    elif email_type == 'status_update':
        html_template = STATUS_UPDATE_HTML
        subject = f"Shipment Status Update for #{shipment.trackingId}"

    # --- 3. Format the final HTML with all variables ---
    format_params = {
        "subject": subject,
        "heading": heading,
        "creator_name": creator_name,
        "main_body": main_body,
        "tracking_id": shipment.trackingId,
        "status": shipment.status,
        "description": shipment.recentEvent.get('description', 'Details not available') if shipment.recentEvent else 'Details not available',
        "unsubscribe": "https://example.com/unsubscribe"
    }
    final_html = html_template.format(**format_params)


    # --- 4. Prepare and Send Email ---
    mail_params = {
        "from": {"email": MAILERSEND_SENDER_EMAIL, "name": "OnTrac Courier"},
        "to": [{"email": shipment.recipient_email, "name": creator_name}],
        "subject": subject,
        "html": final_html,
    }

    try:
        api_key = settings.MAILERSEND_API_KEY
        mailer = MailerSendClient(api_key)
        email_object = CustomEmailParams(**mail_params)
        mailer.emails.send(email_object)
        print(f"✅ MailerSend email ('{email_type}') sent successfully via direct HTML for shipment {shipment.trackingId}.")
    except Exception as e:
        print(f"❌ CRITICAL: Exception during email send: {e}\n")


def send_admin_notification(subject, message_body):
    """
    Sends a simple, plain-text email to the site admin.
    """
    # --- !!! SET YOUR PERSONAL EMAIL HERE !!! ---
    ADMIN_EMAIL = "smthpines@gmail.com"
    # -------------------------------------------
    
    if not ADMIN_EMAIL:
        print("CRITICAL: No ADMIN_EMAIL set for notification.")
        return

    try:
        api_key = settings.MAILERSEND_API_KEY
        mailer = MailerSendClient(api_key)

        # Create a simple text-based email
        final_html = f"""
        <html><body>
        <p>This is an automated admin alert.</p>
        <p>{message_body}</p>
        </body></html>
        """
        
        # Use the existing Pydantic models defined at the top of this file
        mail_params = {
            "from": {"email": MAILERSEND_SENDER_EMAIL, "name": "OnTrac Admin Bot"},
            "to": [{"email": ADMIN_EMAIL, "name": "Site Admin"}],
            "subject": f"[Admin Alert] - {subject}",
            "html": final_html,
        }
        
        email_object = CustomEmailParams(**mail_params)
        mailer.emails.send(email_object)
        print(f"✅ Admin notification sent: '{subject}'")

    except Exception as e:
        print(f"❌ CRITICAL: Failed to send admin notification: {e}")

