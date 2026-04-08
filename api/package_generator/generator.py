import os
import uuid

from .label_builder import build_shipping_label, build_customs_form
from .box_compositor import composite_delivery_photo
from .r2_uploader import upload_to_r2


def generate_delivery_photo(shipment) -> str:
    tracking_id      = shipment.trackingId or ''
    recipient_name   = shipment.recipient_name or 'Recipient'
    destination_city = (shipment.destination_city or '').strip()
    dest_country     = (shipment.destination_country or '').strip()

    details  = shipment.shipmentDetails or {}
    weight   = details.get('weight', '4.3 lbs')
    dest_zip = details.get('destinationZip', '')

    if dest_zip and destination_city:
        street_view_address = f"{destination_city} {dest_zip}, {dest_country}"
    elif destination_city:
        street_view_address = f"{destination_city}, {dest_country}"
    else:
        street_view_address = shipment.destination or dest_country or 'Phoenix, AZ'

    frontend_url = os.environ.get('FRONTEND_URL', 'https://ontracourier.us')
    tracking_url = f"{frontend_url}/tracking?id={tracking_id}"
    api_key      = os.environ.get('GOOGLE_STREET_VIEW_API_KEY', '')

    label_img = build_shipping_label(
        tracking_id=tracking_id,
        recipient_name=recipient_name,
        recipient_address=destination_city,
        recipient_city=destination_city,
        recipient_country=dest_country,
        weight=weight,
        tracking_url=tracking_url,
    )

    customs_img = build_customs_form(
        tracking_id=tracking_id,
        destination_country=dest_country,
    )

    image_bytes = composite_delivery_photo(
        label_img=label_img,
        customs_img=customs_img,
        destination_country=dest_country,
        recipient_address=street_view_address,
        api_key=api_key,
    )

    filename   = f"delivery_{tracking_id}_{uuid.uuid4().hex[:8]}.jpg"
    public_url = upload_to_r2(image_bytes, filename)
    return public_url
