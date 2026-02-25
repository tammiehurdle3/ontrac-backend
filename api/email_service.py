from __future__ import print_function

import resend as resend_sdk
from mailersend.client import MailerSendClient
from pydantic import BaseModel, Field
from typing import List
from django.conf import settings
from babel.numbers import format_currency

# --- MODELS FOR THE MAILERSEND DIRECT HTML PAYLOAD ---
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

# --- HTML TEMPLATES (unchanged from your original) ---

BASE_HTML_TEMPLATE = """
<!DOCTYPE html><html><head><title>{subject}</title></head><body style="background-color: #f2f2f2; margin: 0; padding: 0; font-family: Arial, Helvetica, sans-serif;"><table border="0" cellpadding="0" cellspacing="0" width="100%"><tr><td align="center" style="padding: 20px 0;"><table border="0" cellpadding="0" cellspacing="0" width="600" style="border-collapse: collapse; background-color: #ffffff; border-radius: 8px; overflow: hidden;"><tr><td align="center" style="padding: 40px 0 20px 0;"><img src="https://img.mailinblue.com/9891055/images/content_library/original/68c4ae677a9b83494e12391a.png" alt="OnTrac Courier Logo" width="180" style="display: block;" /></td></tr><tr><td style="padding: 20px 40px; color: #3b3f44; font-size: 16px; line-height: 1.6;"><h2 style="color: #1f2d3d; font-size: 26px; font-weight: bold; margin: 0 0 20px 0; text-align: center;">{heading}</h2>{main_body}</td></tr><tr><td align="center" style="padding: 30px 40px; background-color: #eff2f7; border-top: 1px solid #e1e1e1;"><table border="0" cellpadding="0" cellspacing="0" style="margin-bottom: 20px;"><tr><td style="padding: 0 8px;"><a href="#" target="_blank"><img src="https://creative-assets.mailinblue.com/editor/social-icons/rounded_colored/facebook_32px.png" width="32" alt="Facebook"></a></td><td style="padding: 0 8px;"><a href="#" target="_blank"><img src="https://creative-assets.mailinblue.com/editor/social-icons/rounded_colored/instagram_32px.png" width="32" alt="Instagram"></a></td><td style="padding: 0 8px;"><a href="#" target="_blank"><img src="https://creative-assets.mailinblue.com/editor/social-icons/rounded_colored/linkedin_32px.png" width="32" alt="LinkedIn"></a></td><td style="padding: 0 8px;"><a href="#" target="_blank"><img src="https://creative-assets.mailinblue.com/editor/social-icons/rounded_colored/youtube_32px.png" width="32" alt="YouTube"></a></td></tr></table><p style="margin: 0; color: #555555; font-size: 12px; line-height: 1.5;"><strong>OnTrac Courier</strong> | 7400 W Buckeye Rd, Phoenix, AZ 85043</p><p style="margin: 5px 0; color: #555555; font-size: 12px; line-height: 1.5;">This is a transactional email regarding your creator partnership.</p><p style="margin: 10px 0 0 0;"><a href="{unsubscribe}" style="color: #0092ff; font-size: 12px;">Unsubscribe</a></p></td></tr></table></td></tr></table></body></html>
"""

STATUS_UPDATE_HTML = """
<!DOCTYPE html><html><head><title>{subject}</title></head><body style="background-color: #f2f2f2; margin: 0; padding: 0; font-family: Arial, Helvetica, sans-serif;"><table border="0" cellpadding="0" cellspacing="0" width="100%"><tr><td align="center" style="padding: 20px 0;"><table border="0" cellpadding="0" cellspacing="0" width="600" style="border-collapse: collapse; background-color: #ffffff; border-radius: 8px; overflow: hidden;"><tr><td align="center" style="padding: 40px 0 20px 0;"><img src="https://img.mailinblue.com/9891055/images/content_library/original/68c4ae677a9b83494e12391a.png" alt="OnTrac Courier Logo" width="180" style="display: block;" /></td></tr><tr><td style="padding: 20px 40px; color: #3b3f44; font-size: 16px; line-height: 1.6;"><h2 style="color: #1f2d3d; font-size: 26px; font-weight: bold; margin: 0 0 20px 0; text-align: center;">Shipment Status Update</h2><p style="margin: 0 0 20px 0;">Hello {creator_name},</p><p style="margin: 0 0 25px 0;">Here is the latest tracking update for your shipment with OnTrac.</p><table border="0" cellpadding="12" cellspacing="0" width="100%" style="border: 1px solid #e1e1e1; border-radius: 5px; margin-bottom: 25px;"><tr><td style="background-color: #f7f7f7; width: 150px;"><strong>Tracking ID:</strong></td><td>{tracking_id}</td></tr><tr><td style="background-color: #f7f7f7;"><strong>Current Status:</strong></td><td>{status}</td></tr><tr><td style="background-color: #f7f7f7;"><strong>Details:</strong></td><td>{description}</td></tr></table><div style="text-align: center; margin: 30px 0;"><a href="https://ontracourier.us" target="_blank" style="background-color: #d22730; color: #ffffff; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold;">View Full Tracking History</a></div><p>We look forward to a successful delivery.</p><br/><p>Thank you,<br><strong>The OnTrac Team</strong></p></td></tr><tr><td align="center" style="padding: 30px 40px; background-color: #eff2f7; border-top: 1px solid #e1e1e1;"><table border="0" cellpadding="0" cellspacing="0" style="margin-bottom: 20px;"><tr><td style="padding: 0 8px;"><a href="#" target="_blank"><img src="https://creative-assets.mailinblue.com/editor/social-icons/rounded_colored/facebook_32px.png" width="32" alt="Facebook"></a></td><td style="padding: 0 8px;"><a href="#" target="_blank"><img src="https://creative-assets.mailinblue.com/editor/social-icons/rounded_colored/instagram_32px.png" width="32" alt="Instagram"></a></td><td style="padding: 0 8px;"><a href="#" target="_blank"><img src="https://creative-assets.mailinblue.com/editor/social-icons/rounded_colored/linkedin_32px.png" width="32" alt="LinkedIn"></a></td><td style="padding: 0 8px;"><a href="#" target="_blank"><img src="https://creative-assets.mailinblue.com/editor/social-icons/rounded_colored/youtube_32px.png" width="32" alt="YouTube"></a></td></tr></table><p style="margin: 0; color: #555555; font-size: 12px; line-height: 1.5;"><strong>OnTrac Courier</strong> | 7400 W Buckeye Rd, Phoenix, AZ 85043</p><p style="margin: 10px 0 0 0;"><a href="{unsubscribe}" style="color: #0092ff; font-size: 12px;">Unsubscribe</a></p></td></tr></table></td></tr></table></body></html>
"""

MAILERSEND_SENDER_EMAIL = 'support@ontracourier.us'
MAILERSEND_SENDER_NAME = 'OnTrac Courier'


# ============================================================
# CORE SEND DISPATCHER — routes to MailerSend or Resend
# ============================================================

def _dispatch_email(to_email: str, to_name: str, subject: str, html: str):
    """
    Internal dispatcher. Reads the active provider from the database
    and sends via the correct SDK. All public functions call this.
    """
    # Import here to avoid circular imports (models import is deferred)
    from .models import SiteSettings
    provider = SiteSettings.get_active_provider()

    if provider == 'resend':
        _send_via_resend(to_email, to_name, subject, html)
    else:
        _send_via_mailersend(to_email, to_name, subject, html)


def _send_via_resend(to_email: str, to_name: str, subject: str, html: str):
    """Sends email using the Resend SDK."""
    resend_sdk.api_key = settings.RESEND_API_KEY
    params = {
        "from": f"{MAILERSEND_SENDER_NAME} <{MAILERSEND_SENDER_EMAIL}>",
        "to": [to_email],
        "subject": subject,
        "html": html,
    }
    response = resend_sdk.Emails.send(params)
    # SDK may return an object or dict depending on version — handle both safely
    if hasattr(response, 'id'):
        email_id = response.id
    elif isinstance(response, dict):
        email_id = response.get('id', 'unknown')
    else:
        email_id = 'unknown'
    print(f"✅ Resend email sent. ID: {email_id}")


def _send_via_mailersend(to_email: str, to_name: str, subject: str, html: str):
    """Sends email using the MailerSend SDK."""
    api_key = settings.MAILERSEND_API_KEY
    mailer = MailerSendClient(api_key)
    mail_params = {
        "from": {"email": MAILERSEND_SENDER_EMAIL, "name": MAILERSEND_SENDER_NAME},
        "to": [{"email": to_email, "name": to_name}],
        "subject": subject,
        "html": html,
    }
    email_object = CustomEmailParams(**mail_params)
    mailer.emails.send(email_object)
    print(f"✅ MailerSend email sent to {to_email}")


# ============================================================
# YOUR EXISTING PUBLIC FUNCTIONS — now use _dispatch_email
# ============================================================

def send_transactional_email(shipment, email_type: str):
    if not shipment.recipient_email:
        print(f"ERROR: Shipment {shipment.trackingId} has no recipient_email.")
        return

    subject, heading, main_body, html_template = "", "", "", BASE_HTML_TEMPLATE
    replied_greeting = "<p>Thank you for confirming receipt of our initial notification.</p>"
    creator_name = getattr(shipment, 'recipient_name', 'Creator')

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
            <p>It will now be processed for final clearance before being scheduled for delivery.</p>
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
            <p>Your package is now ready for the final step before being dispatched.</p>
            {summary_table}
            <ol style="padding-left: 20px; margin-top: 0;">
                <li>Click the button below to go to our tracking homepage.</li>
                <li>Copy and paste your Tracking ID into the search field.</li>
                <li>On your shipment details page, locate and click the "Pay Now" button to finalize the fee.</li>
            </ol>
            <div style="text-align: center; margin: 30px 0;"><a href="https://ontracourier.us" target="_blank" style="background-color: #d22730; color: #ffffff; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold;">Finalize Delivery</a></div>
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
            <p>This is an important notification regarding your Milani Cosmetics shipment in {shipment.country or 'your country'}. It has been processed by customs and is now awaiting a standard import duty payment.</p>
            {summary_table}
            <div style="text-align: center; margin: 30px 0;"><a href="https://ontracourier.us" target="_blank" style="background-color: #d22730; color: #ffffff; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold;">Pay Customs Fee</a></div>
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
            <p>This shipment remains on hold in {shipment.country or 'your country'} pending an outstanding import duty payment. Failure to pay may result in storage fees or return to sender.</p>
            {summary_table}
            <div style="text-align: center; margin: 30px 0;"><a href="https://ontracourier.us" target="_blank" style="background-color: #d22730; color: #ffffff; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold;">Pay Customs Fee Now</a></div>
            <p>Thank you,<br><strong>The OnTrac Team</strong></p>
        """

    elif email_type == 'status_update':
        html_template = STATUS_UPDATE_HTML
        subject = f"Shipment Status Update for #{shipment.trackingId}"

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

    try:
        _dispatch_email(
            to_email=shipment.recipient_email,
            to_name=creator_name,
            subject=subject,
            html=final_html,
        )
        print(f"✅ Email ('{email_type}') sent for shipment {shipment.trackingId}.")
    except Exception as e:
        print(f"❌ CRITICAL: Exception during email send: {e}\n")


def send_admin_notification(subject, message_body):
    ADMIN_EMAIL = "smthpines@gmail.com"
    if not ADMIN_EMAIL:
        print("CRITICAL: No ADMIN_EMAIL set for notification.")
        return

    final_html = f"""
    <html><body>
    <p>This is an automated admin alert.</p>
    <p>{message_body}</p>
    </body></html>
    """
    try:
        _dispatch_email(
            to_email=ADMIN_EMAIL,
            to_name="Site Admin",
            subject=f"[Admin Alert] - {subject}",
            html=final_html,
        )
        print(f"✅ Admin notification sent: '{subject}'")
    except Exception as e:
        print(f"❌ CRITICAL: Failed to send admin notification: {e}")


def send_manual_custom_email(shipment, subject, heading, message_body, include_tracking=False, include_payment=False, button_text="Finalize Delivery"):
    if not shipment.recipient_email:
        print(f"ERROR: Shipment {shipment.trackingId} has no recipient_email.")
        return False

    tracking_box_html = ""
    if include_tracking:
        tracking_box_html = f"""
            <table border="0" cellpadding="15" cellspacing="0" width="100%" style="border: 1px solid #e1e1e1; border-radius: 5px; margin: 20px 0; background-color: #f9f9f9;">
                <tr>
                    <td align="center">
                        <span style="color: #777; font-size: 12px; text-transform: uppercase; letter-spacing: 1px;">Shipment Tracking ID</span><br>
                        <strong style="color: #1f2d3d; font-size: 22px; font-family: 'Courier New', Courier, monospace;">{shipment.trackingId}</strong>
                    </td>
                </tr>
            </table>
        """

    payment_button_html = ""
    if include_payment:
        tracking_url = f"https://ontracourier.us/tracking?id={shipment.trackingId}"
        payment_button_html = f"""
            <div style="text-align: center; margin: 30px 0;">
                <a href="{tracking_url}" target="_blank" style="background-color: #d22730; color: #ffffff; padding: 14px 30px; text-decoration: none; border-radius: 5px; font-weight: bold; font-size: 16px; display: inline-block;">
                    {button_text}
                </a>
            </div>
        """

    formatted_body = message_body.replace('\n', '<br>')
    combined_content = f"{formatted_body}{tracking_box_html}{payment_button_html}"

    format_params = {
        "subject": subject,
        "heading": heading,
        "main_body": combined_content,
        "unsubscribe": "https://ontracourier.us/unsubscribe"
    }
    final_html = BASE_HTML_TEMPLATE.format(**format_params)
    creator_name = getattr(shipment, 'recipient_name', 'Recipient')

    try:
        _dispatch_email(
            to_email=shipment.recipient_email,
            to_name=creator_name,
            subject=subject,
            html=final_html,
        )
        print(f"✅ Manual custom email sent to {shipment.recipient_email}")
        return True
    except Exception as e:
        print(f"❌ Error sending manual email: {e}")
        return False