"""
api/ai_shipment_generator.py

COMPLETE REBUILD — Deterministic routing + AI descriptions only.

What changed vs old version:
  - Stage PIPELINE is now hardcoded per destination region (real-world routes)
  - AI (Gemini) ONLY writes the event description text — never decides routing
  - Label Created is always stage 0
  - Chicago will NEVER appear for a Spain shipment again
  - Madrid will NEVER be listed as a hub departure point
  - Admin can now jump to ANY specific stage directly, not just next

Two public functions:
  generate_shipment_data(destination_city, destination_country)
  advance_shipment_stage(shipment, target_stage_key=None)

One helper:
  get_stage_pipeline_for_admin(shipment)  — used by the stage selector UI
"""

import os
import json
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ─── Gemini setup ─────────────────────────────────────────────────────────────
try:
    import google.generativeai as genai
    GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
    if GEMINI_KEY:
        genai.configure(api_key=GEMINI_KEY)
    GEMINI_AVAILABLE = bool(GEMINI_KEY)
except ImportError:
    GEMINI_AVAILABLE = False


# ─── REAL-WORLD ROUTING TABLE ─────────────────────────────────────────────────
# Routes are based on actual major courier networks (DHL/FedEx/UPS)
# us_gateway  = US departure airport/city (depends on destination region)
# regional_hub = intermediate sorting hub before destination country
# hub_short   = short name used in event descriptions

REGION_ROUTES = {
    # UK & Ireland
    "UNITED KINGDOM": {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "London Heathrow (LHR), United Kingdom", "hub_short": "London Heathrow"},
    "UK":             {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "London Heathrow (LHR), United Kingdom", "hub_short": "London Heathrow"},
    "IRELAND":        {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Dublin Airport (DUB), Ireland", "hub_short": "Dublin Airport"},
    # Western Europe (route: LAX → Leipzig LEJ → destination)
    "SPAIN":          {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Leipzig/Halle Airport (LEJ), Germany", "hub_short": "Leipzig Sorting Hub"},
    "FRANCE":         {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Paris Charles de Gaulle (CDG), France", "hub_short": "Paris CDG Hub"},
    "GERMANY":        {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Frankfurt Airport (FRA), Germany", "hub_short": "Frankfurt Hub"},
    "ITALY":          {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Leipzig/Halle Airport (LEJ), Germany", "hub_short": "Leipzig Sorting Hub"},
    "NETHERLANDS":    {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Amsterdam Schiphol (AMS), Netherlands", "hub_short": "Amsterdam Schiphol"},
    "BELGIUM":        {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Brussels Airport (BRU), Belgium", "hub_short": "Brussels Hub"},
    "PORTUGAL":       {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Leipzig/Halle Airport (LEJ), Germany", "hub_short": "Leipzig Sorting Hub"},
    "SWEDEN":         {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Copenhagen Airport (CPH), Denmark", "hub_short": "Copenhagen Hub"},
    "NORWAY":         {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Copenhagen Airport (CPH), Denmark", "hub_short": "Copenhagen Hub"},
    "DENMARK":        {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Copenhagen Airport (CPH), Denmark", "hub_short": "Copenhagen Hub"},
    "FINLAND":        {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Copenhagen Airport (CPH), Denmark", "hub_short": "Copenhagen Hub"},
    "SWITZERLAND":    {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Zurich Airport (ZRH), Switzerland", "hub_short": "Zurich Hub"},
    "AUSTRIA":        {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Frankfurt Airport (FRA), Germany", "hub_short": "Frankfurt Hub"},
    "POLAND":         {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Leipzig/Halle Airport (LEJ), Germany", "hub_short": "Leipzig Sorting Hub"},
    "CZECH REPUBLIC": {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Frankfurt Airport (FRA), Germany", "hub_short": "Frankfurt Hub"},
    "HUNGARY":        {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Frankfurt Airport (FRA), Germany", "hub_short": "Frankfurt Hub"},
    "GREECE":         {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Leipzig/Halle Airport (LEJ), Germany", "hub_short": "Leipzig Sorting Hub"},
    "ROMANIA":        {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Leipzig/Halle Airport (LEJ), Germany", "hub_short": "Leipzig Sorting Hub"},
    "CROATIA":        {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic", "regional_hub": "Frankfurt Airport (FRA), Germany", "hub_short": "Frankfurt Hub"},
    # Middle East
    "UAE":            {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over Europe and the Arabian Peninsula", "regional_hub": "Dubai International (DXB), UAE", "hub_short": "Dubai DXB"},
    "SAUDI ARABIA":   {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over Europe and the Arabian Peninsula", "regional_hub": "Dubai International (DXB), UAE", "hub_short": "Dubai DXB Hub"},
    "TURKEY":         {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the North Atlantic and Europe", "regional_hub": "Istanbul Airport (IST), Turkey", "hub_short": "Istanbul Hub"},
    "ISRAEL":         {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over Europe and the Mediterranean", "regional_hub": "Frankfurt Airport (FRA), Germany", "hub_short": "Frankfurt Hub"},
    "QATAR":          {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over Europe and the Arabian Peninsula", "regional_hub": "Dubai International (DXB), UAE", "hub_short": "Dubai DXB Hub"},
    "KUWAIT":         {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over Europe and the Arabian Peninsula", "regional_hub": "Dubai International (DXB), UAE", "hub_short": "Dubai DXB Hub"},
    # Asia Pacific
    "AUSTRALIA":      {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the Pacific Ocean", "regional_hub": "Sydney Airport (SYD), Australia", "hub_short": "Sydney Hub"},
    "NEW ZEALAND":    {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the Pacific Ocean", "regional_hub": "Auckland Airport (AKL), New Zealand", "hub_short": "Auckland Hub"},
    "JAPAN":          {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the Pacific Ocean", "regional_hub": "Tokyo Narita (NRT), Japan", "hub_short": "Tokyo Narita"},
    "SOUTH KOREA":    {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the Pacific Ocean", "regional_hub": "Incheon International (ICN), South Korea", "hub_short": "Incheon Hub"},
    "CHINA":          {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the Pacific Ocean", "regional_hub": "Shanghai Pudong (PVG), China", "hub_short": "Shanghai Hub"},
    "HONG KONG":      {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the Pacific Ocean", "regional_hub": "Hong Kong International (HKG)", "hub_short": "Hong Kong Hub"},
    "SINGAPORE":      {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the Pacific Ocean", "regional_hub": "Singapore Changi (SIN)", "hub_short": "Singapore Changi"},
    "INDIA":          {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the Pacific Ocean and South Asia", "regional_hub": "Delhi Indira Gandhi (DEL), India", "hub_short": "Delhi Hub"},
    "THAILAND":       {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the Pacific Ocean", "regional_hub": "Singapore Changi (SIN)", "hub_short": "Singapore Hub"},
    "MALAYSIA":       {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the Pacific Ocean", "regional_hub": "Singapore Changi (SIN)", "hub_short": "Singapore Hub"},
    "INDONESIA":      {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the Pacific Ocean", "regional_hub": "Singapore Changi (SIN)", "hub_short": "Singapore Hub"},
    "PHILIPPINES":    {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the Pacific Ocean", "regional_hub": "Hong Kong International (HKG)", "hub_short": "Hong Kong Hub"},
    "VIETNAM":        {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over the Pacific Ocean", "regional_hub": "Singapore Changi (SIN)", "hub_short": "Singapore Hub"},
    # Latin America
    "BRAZIL":         {"us_gateway": "Miami, FL (MIA)", "transit_note": "over the Caribbean and South America", "regional_hub": "São Paulo Guarulhos (GRU), Brazil", "hub_short": "São Paulo Hub"},
    "MEXICO":         {"us_gateway": "Dallas/Fort Worth, TX (DFW)", "transit_note": "via the US-Mexico corridor", "regional_hub": "Mexico City International (MEX)", "hub_short": "Mexico City Hub"},
    "COLOMBIA":       {"us_gateway": "Miami, FL (MIA)", "transit_note": "over the Caribbean", "regional_hub": "Bogotá El Dorado (BOG), Colombia", "hub_short": "Bogotá Hub"},
    "ARGENTINA":      {"us_gateway": "Miami, FL (MIA)", "transit_note": "over South America", "regional_hub": "Buenos Aires Ezeiza (EZE), Argentina", "hub_short": "Buenos Aires Hub"},
    "CHILE":          {"us_gateway": "Miami, FL (MIA)", "transit_note": "over South America", "regional_hub": "Santiago Airport (SCL), Chile", "hub_short": "Santiago Hub"},
    "PERU":           {"us_gateway": "Miami, FL (MIA)", "transit_note": "over South America", "regional_hub": "Lima Jorge Chávez (LIM), Peru", "hub_short": "Lima Hub"},
    "VENEZUELA":      {"us_gateway": "Miami, FL (MIA)", "transit_note": "over the Caribbean", "regional_hub": "Bogotá El Dorado (BOG), Colombia", "hub_short": "Bogotá Hub"},
    "ECUADOR":        {"us_gateway": "Miami, FL (MIA)", "transit_note": "over South America", "regional_hub": "Lima Jorge Chávez (LIM), Peru", "hub_short": "Lima Hub"},
    # Canada
    "CANADA":         {"us_gateway": "Seattle, WA (SEA)", "transit_note": "via the US-Canada border corridor", "regional_hub": "Toronto Pearson (YYZ), Canada", "hub_short": "Toronto Pearson"},
    # Africa
    "SOUTH AFRICA":   {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over Europe and sub-Saharan Africa", "regional_hub": "Johannesburg O.R. Tambo (JNB), South Africa", "hub_short": "Johannesburg Hub"},
    "NIGERIA":        {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over Europe and West Africa", "regional_hub": "Lagos Murtala Muhammed (LOS), Nigeria", "hub_short": "Lagos Hub"},
    "KENYA":          {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over Europe and East Africa", "regional_hub": "Nairobi Jomo Kenyatta (NBO), Kenya", "hub_short": "Nairobi Hub"},
    "EGYPT":          {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over Europe and North Africa", "regional_hub": "Cairo International (CAI), Egypt", "hub_short": "Cairo Hub"},
    "GHANA":          {"us_gateway": "Los Angeles, CA (LAX)", "transit_note": "over Europe and West Africa", "regional_hub": "Lagos Murtala Muhammed (LOS), Nigeria", "hub_short": "Lagos Hub"},
}

DEFAULT_ROUTE = {
    "us_gateway": "Los Angeles, CA (LAX)",
    "transit_note": "internationally",
    "regional_hub": "Frankfurt Airport (FRA), Germany",
    "hub_short": "Frankfurt Hub",
}

def _get_route(country):
    key = country.strip().upper()
    if key in REGION_ROUTES:
        return REGION_ROUTES[key]
    for k, v in REGION_ROUTES.items():
        if k in key or key in k:
            return v
    return DEFAULT_ROUTE


# ─── STAGE PIPELINE BUILDER ───────────────────────────────────────────────────
def build_stage_pipeline(destination_city, destination_country):
    """
    Returns the full ordered list of stages for this destination.
    This is the single source of truth for routing logic.
    AI never modifies this — it only writes descriptions for each stage.
    """
    route = _get_route(destination_country)
    gw = route["us_gateway"]
    hub = route["regional_hub"]
    hub_short = route["hub_short"]
    transit = route["transit_note"]
    dest = f"{destination_city}, {destination_country}"

    return [
        {
            "key": "label_created",
            "label": "Label Created",
            "location": "Phoenix, AZ, USA",
            "requires_payment": False,
            "default_desc": "Shipping label created and registered. Package scheduled for pickup from sender.",
        },
        {
            "key": "package_received",
            "label": "Package Received",
            "location": "Phoenix, AZ, USA",
            "requires_payment": False,
            "default_desc": "Package received and scanned at OnTrac Phoenix facility. Processing initiated.",
        },
        {
            "key": "departed_origin",
            "label": "Departed Origin Facility",
            "location": "Phoenix, AZ, USA",
            "requires_payment": False,
            "default_desc": f"Package departed Phoenix origin facility. Transferring to US international gateway.",
        },
        {
            "key": "arrived_us_gateway",
            "label": "Arrived at US International Gateway",
            "location": gw,
            "requires_payment": False,
            "default_desc": f"Package arrived at US international gateway hub ({gw}). Outbound processing underway.",
        },
        {
            "key": "export_clearance",
            "label": "Export Clearance Completed",
            "location": gw,
            "requires_payment": False,
            "default_desc": "US export customs documentation verified. Package cleared for international departure.",
        },
        {
            "key": "departed_us",
            "label": "Departed US — In Flight",
            "location": gw,
            "requires_payment": False,
            "default_desc": f"Package loaded and departed {gw} on scheduled international cargo flight.",
        },
        {
            "key": "in_transit_intl",
            "label": "In Transit — International Flight",
            "location": gw,  # Show departure gateway, not "In Transit (over the Atlantic)"
            "requires_payment": False,
            "default_desc": f"Shipment in transit on international cargo flight from {gw}. En route to {hub_short}.",
        },
        {
            "key": "arrived_hub",
            "label": "Arrived at Regional Sorting Hub",
            "location": hub,
            "requires_payment": False,
            "default_desc": f"Package arrived at {hub_short}. Sorting and forwarding to destination country in progress.",
        },
        {
            "key": "departed_hub",
            "label": "Departed Sorting Hub",
            "location": hub,
            "requires_payment": False,
            "default_desc": f"Package departed {hub_short} on connecting flight toward {destination_country}.",
        },
        {
            "key": "arrived_destination_country",
            "label": "Arrived at Destination Country",
            "location": dest,
            "requires_payment": False,
            "default_desc": f"Package arrived at inbound clearance facility in {destination_city}. Import documentation lodged with {destination_country} customs authority.",
        },
        {
            "key": "customs_processing",
            "label": "Customs Processing",
            "location": dest,
            "requires_payment": False,
            "default_desc": f"Shipment is currently under review by {destination_country} customs authority. Import duties and applicable taxes are being assessed. Further updates will follow.",
        },
        {
            "key": "held_customs",
            "label": "Held at Customs — Payment Required",
            "location": dest,
            "requires_payment": True,
            "default_desc": f"This shipment is held by {destination_country} customs authorities pending payment of import duties and taxes. The package will be released upon receipt of payment.",
        },
        {
            "key": "payment_received",
            "label": "Payment Received — Customs Released",
            "location": dest,
            "requires_payment": False,
            "default_desc": f"Import duty payment confirmed and processed. Package officially released by {destination_country} customs.",
        },
        {
            "key": "departed_customs",
            "label": "Departed Customs — En Route",
            "location": dest,
            "requires_payment": False,
            "default_desc": f"Package cleared customs facility and transferred to local delivery depot in {destination_city}.",
        },
        {
            "key": "arrived_local",
            "label": "Arrived at Local Delivery Facility",
            "location": dest,
            "requires_payment": False,
            "default_desc": f"Package arrived at local OnTrac delivery depot in {destination_city}. Sorted for final delivery.",
        },
        {
            "key": "out_for_delivery",
            "label": "Out for Delivery",
            "location": dest,
            "requires_payment": False,
            "default_desc": f"Package with delivery driver. Estimated delivery today in {destination_city}.",
        },
        {
            "key": "delivered",
            "label": "Delivered",
            "location": dest,
            "requires_payment": False,
            "default_desc": f"Package successfully delivered to recipient in {destination_city}. Delivery confirmed.",
        },
    ]


# ─── REALISTIC HOUR GAPS BETWEEN STAGES ──────────────────────────────────────
STAGE_HOUR_GAPS = {
    "label_created":               (2, 6),
    "package_received":            (3, 8),
    "departed_origin":             (4, 10),
    "arrived_us_gateway":          (4, 9),
    "export_clearance":            (3, 7),
    "departed_us":                 (4, 10),
    "in_transit_intl":             (9, 15),   # long haul flight
    "arrived_hub":                 (3, 8),
    "departed_hub":                (6, 14),   # connecting flight
    "arrived_destination_country": (5, 10),
    "customs_processing":          (18, 36),  # customs takes 1-2 days
    "held_customs":                (12, 24),  # customer gets notified
    "payment_received":            (1, 4),
    "departed_customs":            (4, 8),
    "arrived_local":               (6, 12),
    "out_for_delivery":            (4, 8),
    "delivered":                   (2, 6),
}


# ─── TIMESTAMP HELPERS ────────────────────────────────────────────────────────

# Timezone for each stage key — the local timezone of the facility
STAGE_TIMEZONE = {
    "label_created":               "America/Phoenix",
    "package_received":            "America/Phoenix",
    "departed_origin":             "America/Phoenix",
    "arrived_us_gateway":          "America/Los_Angeles",  # LAX/SEA/JFK all in US, close enough
    "export_clearance":            "America/Los_Angeles",
    "departed_us":                 "America/Los_Angeles",
    "in_transit_intl":             "America/Los_Angeles",  # last known = departure airport
    "arrived_hub":                 "Europe/Berlin",        # Leipzig, Frankfurt, Amsterdam all CET
    "departed_hub":                "Europe/Berlin",
    "arrived_destination_country": "Europe/Madrid",        # destination country local time
    "customs_processing":          "Europe/Madrid",
    "held_customs":                "Europe/Madrid",
    "payment_received":            "Europe/Madrid",
    "departed_customs":            "Europe/Madrid",
    "arrived_local":               "Europe/Madrid",
    "out_for_delivery":            "Europe/Madrid",
    "delivered":                   "Europe/Madrid",
}

# Realistic LOCAL hour windows (start_hour, end_hour) for each stage
# Based on how real courier facilities actually operate
STAGE_HOUR_WINDOW = {
    "label_created":               (8, 19),   # business hours sender side
    "package_received":            (7, 21),   # facility intake hours
    "departed_origin":             (14, 23),  # afternoon/evening truck runs
    "arrived_us_gateway":          (5, 23),   # 24/7 but mostly daytime intake
    "export_clearance":            (6, 22),   # customs office hours (extended)
    "departed_us":                 (19, 23),  # evening cargo flights (red-eyes)
    "in_transit_intl":             (21, 4),   # overnight — wraps past midnight
    "arrived_hub":                 (3, 9),    # early morning hub arrivals
    "departed_hub":                (8, 16),   # morning sort and depart
    "arrived_destination_country": (6, 14),   # morning arrival at local facility
    "customs_processing":          (8, 17),   # customs office hours
    "held_customs":                (8, 17),   # business hours notification
    "payment_received":            (8, 20),   # any time payment clears
    "departed_customs":            (9, 18),   # after customs clears
    "arrived_local":               (8, 15),   # local depot intake
    "out_for_delivery":            (7, 10),   # drivers depart early
    "delivered":                   (9, 19),   # delivery window
}

def _snap_to_realistic_hours(dt_utc, stage_key):
    """
    Given a UTC datetime and a stage key, convert to the stage's local timezone,
    snap the hour to the realistic operating window, then convert back to UTC.
    Preserves the date — only adjusts the hour within that day.
    """
    tz_name = STAGE_TIMEZONE.get(stage_key, "UTC")
    window = STAGE_HOUR_WINDOW.get(stage_key, (0, 23))
    start_h, end_h = window

    try:
        local_tz = ZoneInfo(tz_name)
    except Exception:
        return dt_utc  # unknown tz, return as-is

    local_dt = dt_utc.astimezone(local_tz)
    local_hour = local_dt.hour

    # Handle windows that wrap past midnight (e.g. 21→4)
    if start_h > end_h:
        # Overnight window — valid hours are start_h..23 or 0..end_h
        in_window = local_hour >= start_h or local_hour <= end_h
    else:
        in_window = start_h <= local_hour <= end_h

    if not in_window:
        if start_h > end_h:
            choices = list(range(start_h, 24)) + list(range(0, end_h + 1))
        else:
            choices = list(range(start_h, end_h + 1))
        new_hour = random.choice(choices)
        new_minute = random.randint(3, 58)
        local_dt = local_dt.replace(hour=new_hour, minute=new_minute, second=0, microsecond=0)

    result_utc = local_dt.astimezone(ZoneInfo("UTC"))

    # KEY FIX: snap may pick a valid but future hour (e.g. 4 PM when it's 7 AM Phoenix).
    # Roll back one day so the timestamp is always in the past.
    now_utc = datetime.now(tz=ZoneInfo("UTC"))
    if result_utc > now_utc:
        local_dt = local_dt - timedelta(days=1)
        result_utc = local_dt.astimezone(ZoneInfo("UTC"))

    return result_utc


def _format_ts(dt, stage_key=None):
    """
    Format datetime to display string in the facility's LOCAL timezone.
    Label Created shows Phoenix time. Leipzig shows German time. Madrid shows Spanish time.
    Exactly how DHL/FedEx work.
    """
    if stage_key and stage_key in STAGE_TIMEZONE:
        try:
            local_tz = ZoneInfo(STAGE_TIMEZONE[stage_key])
            dt = dt.astimezone(local_tz)
        except Exception:
            pass
    return dt.strftime("%Y-%m-%d at %-I:%M %p")

def _parse_ts(ts_str):
    """Parse our standard timestamp string back to datetime."""
    try:
        dt = datetime.strptime(ts_str, "%Y-%m-%d at %I:%M %p")
        return dt.replace(tzinfo=ZoneInfo("UTC"))
    except Exception:
        return datetime.now(tz=ZoneInfo("UTC")) - timedelta(hours=12)

def _parse_ts_local(ts_str, tz_name="America/Phoenix"):
    """Parse timestamp string that was formatted in a specific local timezone."""
    try:
        dt = datetime.strptime(ts_str, "%Y-%m-%d at %I:%M %p")
        return dt.replace(tzinfo=ZoneInfo(tz_name))
    except Exception:
        return datetime.now(tz=ZoneInfo("UTC")) - timedelta(hours=12)


# ─── GEMINI DESCRIPTION WRITER ────────────────────────────────────────────────
def _ai_description(stage_key, stage_label, location, dest_city, dest_country):
    """
    Ask Gemini to write a realistic single-line tracking event description.
    Returns None on any failure — caller uses default_desc as fallback.
    """
    if not GEMINI_AVAILABLE:
        return None
    try:
        model = genai.GenerativeModel("gemini-2.0-flash-lite")
        prompt = (
            f"Write a single realistic tracking event description for an international courier parcel.\n"
            f"Stage: {stage_label}\n"
            f"Facility location: {location}\n"
            f"Final destination: {dest_city}, {dest_country}\n\n"
            f"Rules:\n"
            f"- Maximum 18 words\n"
            f"- Use specific airport codes or facility names where appropriate\n"
            f"- Sound exactly like DHL or FedEx tracking text\n"
            f"- Do NOT mention payment amounts\n"
            f"- Do NOT be vague\n"
            f"- Output ONLY the description, no quotes, no explanation"
        )
        response = model.generate_content(prompt)
        text = response.text.strip().strip('"\'')
        return text if len(text) > 8 else None
    except Exception as e:
        print(f"[AI] Gemini failed for stage {stage_key}: {e}")
        return None


def _generate_shipment_details(destination_country):
    """
    Generates realistic shipment detail fields.
    Dimensions are fixed at 12x10x4. Weight varies 4.2-4.6 lbs.
    Service is International Priority for international, Ground for domestic.
    """
    weight = round(random.uniform(4.2, 4.6), 1)
    country_upper = destination_country.strip().upper()
    is_domestic = country_upper in ("USA", "US", "UNITED STATES")
    service = "Ground" if is_domestic else "International Priority"
    return {
        "service": service,
        "weight": f"{weight} lbs",
        "dimensions": '12" x 10" x 4"',
        "originZip": "85043",
        "destinationZip": "",  # filled by address parser in admin JS
    }


# ─── PUBLIC: Generate New Shipment ───────────────────────────────────────────
def generate_shipment_data(destination_city, destination_country, expected_date_str=None, journey_days=None):
    """
    Create a brand-new shipment starting at 'Label Created'.

    Args:
        destination_city, destination_country : where it's going
        expected_date_str : optional "March 10, 2026" — sets journey end
        journey_days      : optional integer — if set, expected = today + journey_days

    Returns a dict of fields to apply to the Shipment model.
    """
    pipeline = build_stage_pipeline(destination_city, destination_country)
    route = _get_route(destination_country)

    now = datetime.now(tz=ZoneInfo("America/Phoenix"))

    # ── Determine expected delivery date ─────────────────────────────────────
    if journey_days is not None:
        expected_dt = now + timedelta(days=journey_days)
    elif expected_date_str:
        expected_dt = None
        for fmt in ("%B %d, %Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                expected_dt = datetime.strptime(expected_date_str, fmt).replace(
                    tzinfo=ZoneInfo("America/Phoenix"), hour=14, minute=0
                )
                break
            except ValueError:
                continue
        if not expected_dt:
            expected_dt = now + timedelta(days=10)
    else:
        # Default: realistic international delivery window
        expected_dt = now + timedelta(days=random.randint(8, 14))

    # ── Label created: beginning of the journey ───────────────────────────────
    # How long ago was the label created?
    # Journey = expected_dt - now = days remaining
    # Label was created proportionally earlier (stage 0 = 0% of journey)
    # Total journey from label_created to delivered. If expected is 10 days from now,
    # and we're at stage 0 right now, label was created ~now (or a couple hours ago).
    start_dt = now - timedelta(minutes=random.uniform(10, 45))

    first_stage = pipeline[0]
    ai_desc = _ai_description(
        first_stage["key"], first_stage["label"],
        first_stage["location"], destination_city, destination_country
    )
    desc = ai_desc or first_stage["default_desc"]

    event_history = [{
        "city": first_stage["location"],
        "date": _format_ts(start_dt, "label_created"),
        "timestamp": _format_ts(start_dt, "label_created"),
        "event": first_stage["label"],
        "description": desc,
    }]

    recent_event = {
        "event": first_stage["label"],
        "location": first_stage["location"],
        "description": desc,
        "timestamp": _format_ts(start_dt, "label_created"),
    }

    return {
        "status": STAGE_TO_VISUAL_LABEL.get(first_stage["key"], first_stage["label"]),
        "destination": f"{destination_city}, {destination_country}",
        "destination_city": destination_city,
        "destination_country": destination_country,
        "current_stage_key": first_stage["key"],
        "current_stage_index": 0,
        "requiresPayment": False,
        "progressPercent": 0,
        "progressLabels": VISUAL_PROGRESS_LABELS,
        "expectedDate": expected_dt.strftime("%B %d, %Y"),
        "recentEvent": recent_event,
        "allEvents": event_history,
        "shipmentDetails": _generate_shipment_details(destination_country),
        "_route_us_gateway": route["us_gateway"],
        "_route_regional_hub": route["regional_hub"],
    }


# ─── STATUS STRING → STAGE KEY FALLBACK ──────────────────────────────────────
# If current_stage_key is empty (pre-migration), map status label to key.
STATUS_TO_KEY = {
    "Label Created": "label_created",
    "Package Received": "package_received",
    "Departed Origin Facility": "departed_origin",
    "Arrived at US International Gateway": "arrived_us_gateway",
    "Export Clearance Completed": "export_clearance",
    "Departed US — In Flight": "departed_us",
    "In Transit — International Flight": "in_transit_intl",
    "Arrived at Regional Sorting Hub": "arrived_hub",
    "Departed Sorting Hub": "departed_hub",
    "Arrived at Destination Country": "arrived_destination_country",
    "Customs Processing": "customs_processing",
    "Held at Customs — Payment Required": "held_customs",
    "Payment Received — Customs Released": "payment_received",
    "Departed Customs — En Route": "departed_customs",
    "Arrived at Local Delivery Facility": "arrived_local",
    "Out for Delivery": "out_for_delivery",
    "Delivered": "delivered",
    # Legacy status strings from old system
    "Departed Hub": "departed_hub",
    "Arrived at Hub": "arrived_hub",
    "Arrived in Destination Country": "arrived_destination_country",
    "Payment Confirmed": "payment_received",
}


# ─── VISUAL PROGRESS LABELS (the 8 dots shown on the frontend bar) ───────────
# These are what the customer sees. Matches the existing progressLabels default.
VISUAL_PROGRESS_LABELS = [
    "Label Created",
    "Package Received",
    "Departed Origin Facility",
    "Arrived at Hub",
    "Departed Hub",
    "Arrived in Destination Country",
    "Out for Delivery",
    "Delivered",
]

# Maps every internal stage key → which of the 8 visual labels to show as status.
# This is what fixes the progress bar. Customer sees "Arrived at Hub" while the
# detailed event history still shows "Departed US — In Flight", "Export Clearance", etc.
STAGE_TO_VISUAL_LABEL = {
    "label_created":               "Label Created",
    "package_received":            "Package Received",
    "departed_origin":             "Departed Origin Facility",
    "arrived_us_gateway":          "Arrived at Hub",
    "export_clearance":            "Arrived at Hub",
    "departed_us":                 "Arrived at Hub",
    "in_transit_intl":             "Arrived at Hub",
    "arrived_hub":                 "Arrived at Hub",
    "departed_hub":                "Departed Hub",
    "arrived_destination_country": "Arrived in Destination Country",
    "customs_processing":          "Arrived in Destination Country",
    "held_customs":                "Arrived in Destination Country",
    "payment_received":            "Arrived in Destination Country",
    "departed_customs":            "Arrived in Destination Country",
    "arrived_local":               "Out for Delivery",
    "out_for_delivery":            "Out for Delivery",
    "delivered":                   "Delivered",
}
def advance_shipment_stage(shipment, target_stage_key=None):
    """
    Advance to the next stage OR jump to any specific stage.

    Args:
        shipment         : Shipment model instance
        target_stage_key : If given, jump directly to that stage.
                           If None, go to the next stage in sequence.

    Returns:
        dict of fields to save onto the shipment

    Raises:
        ValueError if already at last stage or invalid key given
    """
    # ── Resolve destination ──────────────────────────────────────────────────
    dest_city = (getattr(shipment, "destination_city", "") or "").strip()
    dest_country = (getattr(shipment, "destination_country", "") or "").strip()

    if not dest_city or not dest_country:
        parts = (getattr(shipment, "destination", "") or "").split(",")
        dest_city = parts[0].strip() if parts else "Unknown City"
        dest_country = parts[-1].strip() if len(parts) > 1 else "Unknown Country"

    pipeline = build_stage_pipeline(dest_city, dest_country)
    pipeline_keys = [s["key"] for s in pipeline]

    # ── Find current position ────────────────────────────────────────────────
    current_key = (getattr(shipment, "current_stage_key", None) or "").strip()

    # Fallback 1: if key not set, try STATUS_TO_KEY from status field
    if not current_key or current_key not in pipeline_keys:
        status_str = (getattr(shipment, "status", None) or "").strip()
        current_key = STATUS_TO_KEY.get(status_str, "")

    # Always scan allEvents to find the furthest stage in history.
    # This handles manually-entered journeys where current_stage_key may be
    # stale or stuck at the model default ("label_created") even though the
    # event history is much further along.
    raw_history_check = getattr(shipment, "allEvents", None) or []
    if isinstance(raw_history_check, str):
        try:
            raw_history_check = json.loads(raw_history_check)
        except Exception:
            raw_history_check = []
    event_labels_in_history = {e.get("event", "").strip() for e in raw_history_check}
    history_best_index = -1
    for pi, pstage in enumerate(pipeline):
        if pstage["label"] in event_labels_in_history:
            history_best_index = pi
        for ev_label in event_labels_in_history:
            mapped = STATUS_TO_KEY.get(ev_label, "")
            if mapped == pstage["key"] and pi > history_best_index:
                history_best_index = pi

    # Resolve current index from stored key
    if current_key and current_key in pipeline_keys:
        key_based_index = pipeline_keys.index(current_key)
    else:
        key_based_index = 0

    # Use whichever is further — stored key or what event history shows
    if history_best_index > key_based_index:
        current_index = history_best_index
        current_key = pipeline[history_best_index]["key"]
    else:
        current_index = key_based_index

    if current_index < 0:
        current_index = 0
        current_key = "label_created"

    # ── Determine target ─────────────────────────────────────────────────────
    if target_stage_key:
        if target_stage_key not in pipeline_keys:
            raise ValueError(f"Unknown stage key: '{target_stage_key}'")
        target_index = pipeline_keys.index(target_stage_key)
    else:
        target_index = current_index + 1
        if target_index >= len(pipeline):
            raise ValueError("Shipment is already at the final stage (Delivered).")

    if target_index <= current_index:
        raise ValueError(f"Cannot go backwards. Current: {current_key}")

    # ── Load existing event history ──────────────────────────────────────────
    raw_history = getattr(shipment, "allEvents", None) or []
    if isinstance(raw_history, str):
        try:
            raw_history = json.loads(raw_history)
        except Exception:
            raw_history = []

    # ── TIMELINE-CONTROLLED TIMESTAMP DISTRIBUTION ───────────────────────────
    #
    # Key rule: stages being marked as COMPLETED NOW must all land in the past.
    # We use two windows:
    #   - "completed window": journey_start → now  (for all stages being added now)
    #   - journey_end (expectedDate) is only used for the final "Delivered" stage
    #
    # This prevents the collapse bug where all timestamps clamp to the same minute.
    # Example: expected is March 11, today is March 1, you jump to Held at Customs.
    # All stages distribute proportionally between March 1 (start) and ~now,
    # so they spread realistically across the actual elapsed time.

    now_utc = datetime.now(tz=ZoneInfo("UTC"))
    stages_to_add = pipeline[current_index + 1 : target_index + 1]

    # ── Find journey start (first event date) ─────────────────────────────────
    if raw_history:
        # Extract date portion only — ignores timezone of stored string entirely.
        # Works for Phoenix-generated shipments AND manually entered New York journeys.
        # Hour precision doesn't matter — distribution is date-spread based.
        try:
            first_date_str = raw_history[0].get("date", "").split(" at ")[0]
            journey_start = datetime.strptime(first_date_str, "%Y-%m-%d").replace(
                hour=12, minute=0, tzinfo=ZoneInfo("UTC")
            )
        except Exception:
            journey_start = now_utc - timedelta(days=5)
    else:
        journey_start = now_utc - timedelta(days=5)

    # ── Completed stages window end = a realistic "few hours ago" ─────────────
    # Never in the future — the whole point is these stages have already happened.
    # End 1-3 hours ago so the most recent stage looks recent not stale.
    completed_window_end = now_utc - timedelta(hours=random.uniform(1.0, 3.0))

    if journey_start >= completed_window_end:
        journey_start = completed_window_end - timedelta(hours=12)

    completed_window_seconds = (completed_window_end - journey_start).total_seconds()
    if completed_window_seconds <= 0:
        completed_window_seconds = 3600 * 8

    # ── Find journey end for the full pipeline fraction calculation ────────────
    expected_date_str = (getattr(shipment, "expectedDate", None) or "").strip()
    journey_end = None
    if expected_date_str:
        for fmt in ("%B %d, %Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                journey_end = datetime.strptime(expected_date_str, fmt).replace(
                    tzinfo=ZoneInfo("UTC"), hour=14, minute=0
                )
                break
            except ValueError:
                continue
    if not journey_end:
        journey_end = now_utc + timedelta(days=2)

    total_journey_seconds = (journey_end - journey_start).total_seconds()
    if total_journey_seconds <= 0:
        total_journey_seconds = 86400 * 10

    total_stages = len(pipeline) - 1

    def stage_fraction(stage_index):
        """What fraction (0.0–1.0) of the journey is this stage at?"""
        return stage_index / total_stages

    # ── Build timestamps ───────────────────────────────────────────────────────
    new_events = []
    for i, stage in enumerate(stages_to_add):
        actual_index = current_index + 1 + i
        frac = stage_fraction(actual_index)

        # Map the stage's journey fraction onto the COMPLETED window (not future)
        # So 50% of journey → 50% of (journey_start → now-2h), not 50% of full 10-day span
        ts = journey_start + timedelta(seconds=frac * completed_window_seconds)

        # Small variation so timestamps don't look mechanical
        ts += timedelta(minutes=random.uniform(-8, 8))

        # Snap to realistic operating hours for this stage's local timezone
        ts = _snap_to_realistic_hours(ts, stage["key"])

        # Hard safety: never future, never before journey start
        ts = min(ts, now_utc - timedelta(minutes=5))
        if ts < journey_start:
            ts = journey_start + timedelta(minutes=random.randint(10, 30))

        # Never go backwards — compare raw UTC datetimes, never re-parse formatted strings
        if new_events:
            prev_dt = new_events[-1]["_raw_ts"]
            if ts <= prev_dt:
                ts = prev_dt + timedelta(minutes=random.randint(20, 60))
        elif raw_history:
            try:
                last_date_str = raw_history[-1].get("date", "").split(" at ")[0]
                last_dt = datetime.strptime(last_date_str, "%Y-%m-%d").replace(
                    hour=23, minute=59, tzinfo=ZoneInfo("UTC")
                )
            except Exception:
                last_dt = now_utc - timedelta(hours=6)
            if ts <= last_dt:
                ts = last_dt + timedelta(minutes=random.randint(20, 60))

        ai_desc = _ai_description(
            stage["key"], stage["label"],
            stage["location"], dest_city, dest_country
        )
        desc = ai_desc or stage["default_desc"]

        new_events.append({
            "city": stage["location"],
            "date": _format_ts(ts, stage["key"]),
            "event": stage["label"],
            "description": desc,
            "_raw_ts": ts,  # keep raw for monotonic check — stripped before save
        })

    if not new_events:
        raise ValueError("No new stages to add.")

    # ── Build return data ────────────────────────────────────────────────────
    # Strip internal _raw_ts before saving — not part of the schema
    for ev in new_events:
        ev.pop("_raw_ts", None)
    updated_history = raw_history + new_events
    latest = new_events[-1]
    target_stage = pipeline[target_index]
    stages_added = len(new_events)
    progress = round((target_index / (len(pipeline) - 1)) * 100)

    # Visual status = simplified label shown in header + matched by progress bar dots
    # Detailed label stays in allEvents event history
    visual_status = STAGE_TO_VISUAL_LABEL.get(target_stage["key"], target_stage["label"])

    return {
        "status": visual_status,
        "current_stage_key": target_stage["key"],
        "current_stage_index": target_index,
        "requiresPayment": target_stage["requires_payment"],
        "progressPercent": progress,
        "progressLabels": VISUAL_PROGRESS_LABELS,
        "recentEvent": {
            "event": latest["event"],
            "location": latest["city"],
            "description": latest["description"],
            "timestamp": latest["date"],
        },
        "allEvents": updated_history,
        # Admin feedback fields (not saved to model)
        "_stages_added": stages_added,
        "_jumped_to_label": target_stage["label"],
        "_jumped_to_key": target_stage["key"],
    }


# ─── PUBLIC: Pipeline for Admin Display ──────────────────────────────────────
def get_stage_pipeline_for_admin(shipment):
    """
    Returns the full pipeline list with each stage annotated:
      - is_current  : this is the active stage
      - is_completed: stage is in the past
      - is_future   : stage hasn't happened yet

    Used by the admin JS to render the visual stage selector.
    """
    dest_city = (getattr(shipment, "destination_city", "") or "").strip()
    dest_country = (getattr(shipment, "destination_country", "") or "").strip()
    if not dest_city or not dest_country:
        parts = (getattr(shipment, "destination", "") or "").split(",")
        dest_city = parts[0].strip() if parts else "Unknown"
        dest_country = parts[-1].strip() if len(parts) > 1 else "Unknown"

    pipeline = build_stage_pipeline(dest_city, dest_country)
    current_key = (getattr(shipment, "current_stage_key", None) or "").strip()

    if not current_key or current_key not in [s["key"] for s in pipeline]:
        status_str = (getattr(shipment, "status", None) or "").strip()
        current_key = STATUS_TO_KEY.get(status_str, "label_created")

    result = []
    current_idx = next(
        (i for i, s in enumerate(pipeline) if s["key"] == current_key), 0
    )

    for i, stage in enumerate(pipeline):
        result.append({
            "index": i,
            "key": stage["key"],
            "label": stage["label"],
            "location": stage["location"],
            "requires_payment": stage["requires_payment"],
            "is_completed": i < current_idx,
            "is_current": i == current_idx,
            "is_future": i > current_idx,
        })

    return result