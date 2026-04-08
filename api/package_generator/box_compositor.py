import os
import io
import random
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance, ImageChops

ASSETS_DIR = os.path.join(os.path.dirname(__file__), 'assets')
BOX_PATH   = os.path.join(ASSETS_DIR, 'boxes', 'box_base.png')

ROUTING_CODES = {
    'SPAIN': 'ESP-MAD-001', 'FRANCE': 'FRA-CDG-002', 'GERMANY': 'DEU-FRA-003',
    'UNITED KINGDOM': 'GBR-LHR-004', 'UK': 'GBR-LHR-004',
    'ITALY': 'ITA-FCO-005', 'NETHERLANDS': 'NLD-AMS-006',
    'CANADA': 'CAN-YYZ-007', 'AUSTRALIA': 'AUS-SYD-008',
    'JAPAN': 'JPN-NRT-009', 'UAE': 'UAE-DXB-010',
    'BRAZIL': 'BRA-GRU-011', 'DEFAULT': 'INT-HUB-099',
}

def _get_routing_code(country):
    return ROUTING_CODES.get((country or '').upper().strip(), ROUTING_CODES['DEFAULT'])


def _fetch_street_view(address, api_key):
    """Fetch doorstep-level Street View — tight FOV, looking down."""
    if not api_key:
        return None
    try:
        params = {
            'size': '1280x960',
            'location': address,
            'fov': 60,        # tight — feels like phone zoom
            'pitch': -25,     # looking DOWN at the doorstep
            'key': api_key,
        }
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/streetview",
            params=params, timeout=15
        )
        if resp.status_code != 200:
            return None
        if not resp.headers.get('content-type', '').startswith('image'):
            return None
        img    = Image.open(io.BytesIO(resp.content)).convert('RGBA')
        sample = list(img.convert('RGB').getdata())[:200]
        r_vals = [p[0] for p in sample]
        if max(r_vals) - min(r_vals) < 12:
            return None
        return img
    except Exception as e:
        print(f"[compositor] Street View error: {e}")
        return None


def _create_fallback_background():
    """Concrete porch / doorstep fallback."""
    W, H = 1280, 960
    img  = Image.new('RGB', (W, H))
    draw = ImageDraw.Draw(img)
    # sky
    draw.rectangle([0, 0, W, int(H * 0.35)], fill=(160, 175, 185))
    # door / wall area
    draw.rectangle([int(W*0.25), 0, int(W*0.75), int(H*0.65)], fill=(90, 70, 55))
    # door detail
    draw.rectangle([int(W*0.3), int(H*0.05), int(W*0.7), int(H*0.62)],
                   fill=(75, 55, 40), outline=(50, 35, 25), width=4)
    # concrete porch
    for y in range(int(H*0.62), H, 30):
        shade = 110 + random.randint(-8, 8)
        draw.rectangle([0, y, W, y + 29], fill=(shade, shade - 5, shade - 10))
    # grout lines
    for y in range(int(H*0.62), H, 30):
        draw.line([(0, y), (W, y)], fill=(90, 88, 85), width=1)
    for x in range(0, W, 80):
        draw.line([(x, int(H*0.62)), (x, H)], fill=(90, 88, 85), width=1)
    return img.convert('RGBA')


def _make_routing_sticker(routing_code):
    from PIL import ImageFont
    W, H = 200, 90
    img  = Image.new('RGBA', (W, H), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([2, 2, W-2, H-2], outline='black', width=2)
    try:
        fp   = os.path.join(ASSETS_DIR, 'fonts', 'LiberationSans-Bold.ttf')
        f_sm = ImageFont.truetype(fp, 11)
        f_lg = ImageFont.truetype(fp, 16)
    except Exception:
        f_sm = f_lg = ImageFont.load_default()
    draw.text((8, 6),  "ROUTING",    font=f_sm, fill='#666')
    draw.text((8, 22), routing_code, font=f_lg, fill='black')
    x = 8
    for _ in range(40):
        bw = random.choice([1, 2, 2, 3])
        bh = random.randint(18, 32)
        draw.rectangle([x, 52, x+bw, 52+bh], fill='black')
        x += bw + random.randint(1, 3)
        if x > W - 10:
            break
    return img


def _add_wear_marks(box_img):
    overlay = Image.new('RGBA', box_img.size, (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)
    w, h    = box_img.size
    for _ in range(random.randint(5, 10)):
        x  = random.randint(5, w - 30)
        y  = random.randint(5, h - 30)
        sz = random.randint(4, 18)
        op = random.randint(15, 50)
        draw.ellipse([x, y, x+sz, y+sz], fill=(60, 40, 20, op))
    for cx, cy in [(0, 0), (w-40, 0), (0, h-40), (w-40, h-40)]:
        draw.ellipse([cx, cy, cx+40, cy+40], fill=(50, 30, 10, random.randint(20, 50)))
    blurred = overlay.filter(ImageFilter.GaussianBlur(radius=3))
    result  = box_img.copy()
    result.paste(blurred, (0, 0), blurred)
    return result


def _perspective_squish(img):
    """Mild keystone — makes box look like it's flat on the ground."""
    w, h = img.size
    # squish top slightly — simulates looking down
    new_top_w = int(w * 0.88)
    offset    = (w - new_top_w) // 2
    result    = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    for y in range(h):
        frac      = y / h
        row_w     = int(new_top_w + (w - new_top_w) * frac)
        row_off   = int(offset * (1 - frac))
        row       = img.crop((0, y, w, y + 1)).resize((row_w, 1), Image.LANCZOS)
        result.paste(row, (row_off, y), row if row.mode == 'RGBA' else None)
    return result


def _match_brightness(box_img, bg_img):
    """Adjust box brightness to roughly match background."""
    bg_rgb   = bg_img.convert('RGB')
    bg_data  = list(bg_rgb.getdata())
    bg_avg   = sum(p[0]+p[1]+p[2] for p in bg_data) / (len(bg_data) * 3)
    box_rgb  = box_img.convert('RGB')
    box_data = list(box_rgb.getdata())
    box_avg  = sum(p[0]+p[1]+p[2] for p in box_data) / (len(box_data) * 3)
    if box_avg > 0:
        factor = (bg_avg / box_avg) * random.uniform(0.85, 1.05)
        factor = max(0.5, min(1.6, factor))
        enhanced = ImageEnhance.Brightness(box_img.convert('RGB')).enhance(factor)
        r, g, b  = enhanced.split()
        return Image.merge('RGBA', (r, g, b, box_img.split()[3]))
    return box_img


def _apply_phone_camera_effect(img):
    """Vignette + warmth + slight blur to simulate phone snap."""
    w, h = img.size

    # Vignette
    vig = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    vd  = ImageDraw.Draw(vig)
    for i in range(12):
        alpha = int(22 * (1 - i / 12))
        off   = i * 20
        vd.ellipse([off, off, w-off, h-off],
                   fill=(0,0,0,0), outline=(0,0,0,alpha), width=24)
    vig    = vig.filter(ImageFilter.GaussianBlur(radius=30))
    result = img.convert('RGBA')
    result.paste(vig, (0, 0), vig)

    # Slight compression blur (phone JPEG artefact feel)
    rgb   = result.convert('RGB')
    rgb   = rgb.filter(ImageFilter.GaussianBlur(radius=0.4))

    # Warmth
    r, g, b = rgb.split()
    r = r.point(lambda x: min(255, x + 7))
    b = b.point(lambda x: max(0, x - 5))
    warm = Image.merge('RGB', (r, g, b))

    # Slight desaturate — phones compress colour
    grey  = ImageEnhance.Color(warm).enhance(0.88)
    sharp = ImageEnhance.Sharpness(grey).enhance(1.15)
    return sharp.convert('RGBA')


def composite_delivery_photo(label_img, customs_img, destination_country,
                              recipient_address, api_key):
    # ── 1. Load & prep box ────────────────────────────────────────────────────
    try:
        box = Image.open(BOX_PATH).convert('RGBA')
    except Exception as e:
        raise RuntimeError(f"Cannot load box image at {BOX_PATH}: {e}")

    # Scale box — smaller feels more realistic (not huge 3D render)
    BOX_H = 420
    ratio = BOX_H / box.height
    BOX_W = int(box.width * ratio)
    box   = box.resize((BOX_W, BOX_H), Image.LANCZOS)

    # ── 2. Paste label ────────────────────────────────────────────────────────
    label_target_w = int(BOX_W * 0.48)
    label_target_h = int(label_img.height * label_target_w / label_img.width)
    label_resized  = label_img.resize(
        (label_target_w, label_target_h), Image.LANCZOS
    ).convert('RGBA')
    # Slight random rotation to label (hand-applied look)
    angle = random.uniform(-2.5, 2.5)
    label_rotated = label_resized.rotate(angle, expand=True, fillcolor=(0,0,0,0))
    lx = int(BOX_W * 0.06)
    ly = int(BOX_H * 0.10)
    box.paste(label_rotated, (lx, ly), label_rotated)

    # ── 3. Paste customs form ─────────────────────────────────────────────────
    try:
        cw = int(BOX_W * 0.33)
        ch = int(customs_img.height * cw / customs_img.width)
        cr = customs_img.resize((cw, ch), Image.LANCZOS).convert('RGBA')
        cr = cr.rotate(random.uniform(-3, 3), expand=True, fillcolor=(0,0,0,0))
        box.paste(cr, (int(BOX_W * 0.56), int(BOX_H * 0.50)), cr)
    except Exception as e:
        print(f"[compositor] customs paste: {e}")

    # ── 4. Routing sticker ────────────────────────────────────────────────────
    try:
        sticker = _make_routing_sticker(_get_routing_code(destination_country))
        sticker = sticker.resize((110, 50), Image.LANCZOS).convert('RGBA')
        sticker = sticker.rotate(random.uniform(-5, 5), expand=True, fillcolor=(0,0,0,0))
        box.paste(sticker, (BOX_W - 130, int(BOX_H * 0.05)), sticker)
    except Exception as e:
        print(f"[compositor] sticker: {e}")

    # ── 5. Wear & perspective ─────────────────────────────────────────────────
    box = _add_wear_marks(box)
    box = _perspective_squish(box)

    # ── 6. Background ─────────────────────────────────────────────────────────
    BG_W, BG_H = 1280, 960
    bg = _fetch_street_view(recipient_address, api_key)
    if bg is None:
        print("[compositor] Using fallback background")
        bg = _create_fallback_background()
    bg = bg.resize((BG_W, BG_H), Image.LANCZOS).convert('RGBA')

    # ── 7. Match box lighting to background ───────────────────────────────────
    box = _match_brightness(box, bg)

    # ── 8. Position ───────────────────────────────────────────────────────────�)
    BOX_BG_X = int(BG_W * 0.18) + random.randint(-20, 20)
    # Y: bottom third so box sits on "ground"
    BOX_BG_Y = int(BG_H * 0.55)

    # ── 9. Ground contact shadow ──────────────────────────────────────────────
    shadow = Image.new('RGBA', (BG_W, BG_H), (0, 0, 0, 0))
    sd     = ImageDraw.Draw(shadow)
    # Wide flat ellipse — box sitting on concrete
    sx1 = BOX_BG_X + int(BOX_W * 0.05)
    sx2 = BOX_BG_X + int(BOX_W * 0.95)
    sy1 = BOX_BG_Y + BOX_H - 10
    sy2 = sy1 + 40
    sd.ellipse([sx1, sy1, sx2, sy2], fill=(0, 0, 0, 100))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=14))

    # ── 10. Composite ─────────────────────────────────────────────────────────
    final = bg.copy()
    final.paste(shadow, (0, 0), shadow)
    final.paste(box, (BOX_BG_X, BOX_BG_Y), box)

    # ── 11. Phone camera effect ───────────────────────────────────────────────
    final = _apply_phone_camera_effect(final)

    # ── 12. Crop to 4:3 phone aspect ─────────────────────────────────────────
    out = io.BytesIO()
    final.convert('RGB').save(out, format='JPEG', quality=78, optimize=True)
    out.seek(0)
    return out.read()
