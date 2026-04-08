import os
import io
import random
from PIL import Image, ImageDraw, ImageFont
import barcode
from barcode.writer import ImageWriter
import qrcode

ASSETS_DIR = os.path.join(os.path.dirname(__file__), 'assets')
FONT_REGULAR = os.path.join(ASSETS_DIR, 'fonts', 'LiberationSans-Regular.ttf')
FONT_BOLD    = os.path.join(ASSETS_DIR, 'fonts', 'LiberationSans-Bold.ttf')

LABEL_W = 800
LABEL_H = 1200


def build_shipping_label(tracking_id, recipient_name, recipient_address,
                          recipient_city, recipient_country,
                          weight="4.3 lbs", tracking_url=None):
    if not tracking_url:
        tracking_url = f"https://ontracourier.us/tracking?id={tracking_id}"

    img  = Image.new('RGBA', (LABEL_W, LABEL_H), color=(255, 255, 255, 255))
    draw = ImageDraw.Draw(img)

    try:
        font_carrier  = ImageFont.truetype(FONT_BOLD,    52)
        font_header   = ImageFont.truetype(FONT_BOLD,    28)
        font_addr     = ImageFont.truetype(FONT_REGULAR, 30)
        font_tracking = ImageFont.truetype(FONT_BOLD,    44)
        font_small    = ImageFont.truetype(FONT_REGULAR, 24)
        font_weight   = ImageFont.truetype(FONT_REGULAR, 28)
    except Exception:
        font_carrier = font_header = font_addr = font_tracking = font_small = font_weight = ImageFont.load_default()

    draw.rectangle([4, 4, LABEL_W - 4, LABEL_H - 4], outline='black', width=3)

    # ── Carrier stripe (red band at top like real OnTrac) ─────────────────────
    draw.rectangle([4, 4, LABEL_W - 4, 105], fill='#d22730')
    draw.text((50, 25), "OnTrac", font=font_carrier, fill='white')
    draw.line([(4, 112), (LABEL_W - 4, 112)], fill='black', width=2)

    draw.text((50, 126), "FROM:", font=font_header, fill='black')
    y = 163
    for line in ["Milani Cosmetics", "7400 W Buckeye Rd", "Phoenix, AZ 85043", "United States"]:
        draw.text((50, y), line, font=font_addr, fill='black')
        y += 38
    draw.line([(4, y + 14), (LABEL_W - 4, y + 14)], fill='black', width=2)

    y += 28
    draw.text((50, y), "TO:", font=font_header, fill='black')
    y += 36
    for line in [recipient_name or "Recipient", recipient_address or "", recipient_city or "", recipient_country or ""]:
        if line.strip():
            draw.text((50, y), line, font=font_addr, fill='black')
            y += 42

    divider_y = max(y + 20, 570)
    draw.line([(4, divider_y), (LABEL_W - 4, divider_y)], fill='black', width=2)

    barcode_y      = divider_y + 20
    barcode_bottom = barcode_y + 160
    try:
        Code128 = barcode.get_barcode_class('code128')
        bc      = Code128(tracking_id, writer=ImageWriter())
        bc_buf  = io.BytesIO()
        bc.write(bc_buf, options={
            'module_width': 0.8, 'module_height': 12.0,
            'font_size': 0, 'text_distance': 1.0,
            'quiet_zone': 2.0, 'write_text': False,
        })
        bc_buf.seek(0)
        bc_img    = Image.open(bc_buf).convert('RGB')
        target_w  = LABEL_W - 100
        target_h  = min(int(bc_img.height * target_w / bc_img.width), 160)
        bc_img    = bc_img.resize((target_w, target_h), Image.LANCZOS)
        img.paste(bc_img, ((LABEL_W - target_w) // 2, barcode_y))
        barcode_bottom = barcode_y + target_h
    except Exception as e:
        print(f"[label] barcode error: {e}")

    tracking_text_y = barcode_bottom + 10
    try:
        bbox = draw.textbbox((0, 0), tracking_id, font=font_tracking)
        tw   = bbox[2] - bbox[0]
        draw.text(((LABEL_W - tw) // 2, tracking_text_y), tracking_id, font=font_tracking, fill='black')
    except Exception:
        draw.text((50, tracking_text_y), tracking_id, font=font_tracking, fill='black')

    second_divider_y = tracking_text_y + 65
    draw.line([(4, second_divider_y), (LABEL_W - 4, second_divider_y)], fill='black', width=2)

    qr_y = second_divider_y + 20
    try:
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=4, border=2)
        qr.add_data(tracking_url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color='black', back_color='white').convert('RGBA')
        qr_img = qr_img.resize((180, 180), Image.LANCZOS)
        img.paste(qr_img, (50, qr_y), qr_img)
    except Exception as e:
        print(f"[label] QR error: {e}")

    draw.text((560, qr_y + 20),  "Weight:",       font=font_weight, fill='black')
    draw.text((560, qr_y + 55),  weight,          font=font_weight, fill='black')
    draw.text((560, qr_y + 100), "Intl Priority", font=font_small,  fill='#555555')

    angle = random.uniform(-1.2, 1.2)
    img   = img.rotate(angle, expand=False, fillcolor=(255, 255, 255, 0))
    return img


def build_customs_form(tracking_id, destination_country, declared_value="$150.00"):
    W, H = 700, 420
    img  = Image.new('RGBA', (W, H), (232, 245, 233, 255))
    draw = ImageDraw.Draw(img)

    try:
        font_hdr  = ImageFont.truetype(FONT_BOLD,    32)
        font_body = ImageFont.truetype(FONT_REGULAR, 22)
    except Exception:
        font_hdr = font_body = ImageFont.load_default()

    draw.rectangle([3, 3, W - 3, H - 3], outline='#2E7D32', width=4)
    draw.text((40, 25), "CUSTOMS DECLARATION", font=font_hdr,  fill='black')
    draw.text((40, 66), "CN 23",               font=font_hdr,  fill='black')
    draw.line([(3, 108), (W - 3, 108)], fill='#2E7D32', width=2)

    y = 122
    for line in [
        "Contents: Cosmetic samples - promotional use",
        f"Declared Value: {declared_value}",
        f"Tracking: {tracking_id}",
        "Origin: United States",
        "Non-commercial shipment",
    ]:
        draw.text((40, y), line, font=font_body, fill='black')
        y += 48

    x = 45
    draw.rectangle([x, H - 75, W - 40, H - 20], fill='white')
    x2 = x + 5
    for _ in range(60):
        bw = random.choice([1, 2, 3])
        bh = random.randint(30, 45)
        draw.rectangle([x2, H - 68, x2 + bw, H - 68 + bh], fill='black')
        x2 += bw + random.randint(1, 4)
        if x2 > W - 50:
            break

    angle = random.uniform(-2.5, 2.5)
    img   = img.rotate(angle, expand=True, fillcolor=(232, 245, 233, 0))
    return img
