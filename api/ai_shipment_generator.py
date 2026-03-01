"""
api/ai_shipment_generator.py

Three public functions:
  generate_shipment_data()   — new shipment, 2 initial events (Label Created + Package Received)
  smart_advance_shipment()   — MAIN: catches up ALL missed stages OR advances one with Gemini
  advance_shipment_stage()   — single Gemini advance (called internally by smart_advance)

Timestamps use LOCAL TIME of each hub city.
Out for Delivery and Delivered are anchored to the shipment ExpectedDate.
"""

import requests
import json
import secrets
import random
from datetime import datetime, timedelta
from django.conf import settings


# ─────────────────────────────────────────────────────────────────────────────
# HUB TIMEZONE OFFSETS
# ─────────────────────────────────────────────────────────────────────────────
HUB_TIMEZONES = {
    "Phoenix, AZ":                        -7,
    "Chicago, IL":                        -6,
    "Los Angeles, CA":                    -8,
    "Miami, FL":                          -5,
    "New York, NY":                       -5,
    "Dallas, TX":                         -6,
    "Leipzig, Germany":                   +1,
    "London, UK":                          0,
    "Paris, France":                      +1,
    "Amsterdam, Netherlands":             +1,
    "Milan, Italy":                       +1,
    "Madrid, Spain":                      +1,
    "Vienna, Austria":                    +1,
    "Zurich, Switzerland":                +1,
    "Stockholm, Sweden":                  +1,
    "Oslo, Norway":                       +1,
    "Copenhagen, Denmark":                +1,
    "Warsaw, Poland":                     +1,
    "Lisbon, Portugal":                    0,
    "Athens, Greece":                     +2,
    "Istanbul, Turkey":                   +3,
    "Tel Aviv, Israel":                   +2,
    "Dubai, UAE":                         +4,
    "Riyadh, Saudi Arabia":               +3,
    "Kuwait City, Kuwait":                +3,
    "Doha, Qatar":                        +3,
    "Tokyo, Japan":                       +9,
    "Seoul, South Korea":                 +9,
    "Hong Kong":                          +8,
    "Shanghai, China":                    +8,
    "Taipei, Taiwan":                     +8,
    "Singapore":                          +8,
    "Kuala Lumpur, Malaysia":             +8,
    "Bangkok, Thailand":                  +7,
    "Manila, Philippines":                +8,
    "Jakarta, Indonesia":                 +7,
    "Ho Chi Minh City, Vietnam":          +7,
    "Mumbai, India":                      +5,
    "Delhi, India":                       +5,
    "Karachi, Pakistan":                  +5,
    "Dhaka, Bangladesh":                  +6,
    "Sydney, Australia":                  +11,
    "Auckland, New Zealand":              +13,
    "Lagos, Nigeria":                     +1,
    "Accra, Ghana":                        0,
    "Johannesburg, South Africa":         +2,
    "Nairobi, Kenya":                     +3,
    "Addis Ababa, Ethiopia":              +3,
    "Cairo, Egypt":                       +2,
    "Casablanca, Morocco":                +1,
    "Dar es Salaam, Tanzania":            +3,
    "Douala, Cameroon":                   +1,
    "Sao Paulo, Brazil":                  -3,
    "Buenos Aires, Argentina":            -3,
    "Santiago, Chile":                    -3,
    "Lima, Peru":                         -5,
    "Bogota, Colombia":                   -5,
    "Caracas, Venezuela":                 -4,
    "Mexico City, Mexico":                -6,
    "San Jose, Costa Rica":               -6,
    "Santo Domingo, Dominican Republic":  -4,
    "Kingston, Jamaica":                  -5,
    "Toronto, Canada":                    -5,
    "Vancouver, Canada":                  -8,
}


# ─────────────────────────────────────────────────────────────────────────────
# ROUTING TABLE
# ─────────────────────────────────────────────────────────────────────────────
ROUTING_TABLE = {
    "united kingdom": ["Chicago, IL", "London, UK"],
    "uk":             ["Chicago, IL", "London, UK"],
    "germany":        ["Chicago, IL", "Leipzig, Germany"],
    "france":         ["Chicago, IL", "Leipzig, Germany", "Paris, France"],
    "italy":          ["Chicago, IL", "Leipzig, Germany", "Milan, Italy"],
    "spain":          ["Chicago, IL", "Leipzig, Germany", "Madrid, Spain"],
    "netherlands":    ["Chicago, IL", "Leipzig, Germany", "Amsterdam, Netherlands"],
    "belgium":        ["Chicago, IL", "Leipzig, Germany"],
    "austria":        ["Chicago, IL", "Leipzig, Germany", "Vienna, Austria"],
    "switzerland":    ["Chicago, IL", "Leipzig, Germany", "Zurich, Switzerland"],
    "sweden":         ["Chicago, IL", "Leipzig, Germany", "Stockholm, Sweden"],
    "norway":         ["Chicago, IL", "Leipzig, Germany", "Oslo, Norway"],
    "denmark":        ["Chicago, IL", "Leipzig, Germany", "Copenhagen, Denmark"],
    "poland":         ["Chicago, IL", "Leipzig, Germany", "Warsaw, Poland"],
    "portugal":       ["Chicago, IL", "Leipzig, Germany", "Lisbon, Portugal"],
    "greece":         ["Chicago, IL", "Leipzig, Germany", "Athens, Greece"],
    "turkey":         ["Chicago, IL", "Leipzig, Germany", "Istanbul, Turkey"],
    "israel":         ["Chicago, IL", "Leipzig, Germany", "Tel Aviv, Israel"],
    "japan":          ["Los Angeles, CA", "Tokyo, Japan"],
    "south korea":    ["Los Angeles, CA", "Seoul, South Korea"],
    "china":          ["Los Angeles, CA", "Hong Kong", "Shanghai, China"],
    "hong kong":      ["Los Angeles, CA", "Hong Kong"],
    "taiwan":         ["Los Angeles, CA", "Taipei, Taiwan"],
    "singapore":      ["Los Angeles, CA", "Hong Kong", "Singapore"],
    "malaysia":       ["Los Angeles, CA", "Hong Kong", "Kuala Lumpur, Malaysia"],
    "thailand":       ["Los Angeles, CA", "Hong Kong", "Bangkok, Thailand"],
    "philippines":    ["Los Angeles, CA", "Hong Kong", "Manila, Philippines"],
    "indonesia":      ["Los Angeles, CA", "Hong Kong", "Jakarta, Indonesia"],
    "vietnam":        ["Los Angeles, CA", "Hong Kong", "Ho Chi Minh City, Vietnam"],
    "india":          ["Los Angeles, CA", "Mumbai, India"],
    "pakistan":       ["Los Angeles, CA", "Dubai, UAE", "Karachi, Pakistan"],
    "bangladesh":     ["Los Angeles, CA", "Hong Kong", "Dhaka, Bangladesh"],
    "australia":      ["Los Angeles, CA", "Sydney, Australia"],
    "new zealand":    ["Los Angeles, CA", "Sydney, Australia", "Auckland, New Zealand"],
    "united arab emirates": ["Chicago, IL", "Dubai, UAE"],
    "uae":            ["Chicago, IL", "Dubai, UAE"],
    "saudi arabia":   ["Chicago, IL", "Dubai, UAE", "Riyadh, Saudi Arabia"],
    "kuwait":         ["Chicago, IL", "Dubai, UAE", "Kuwait City, Kuwait"],
    "qatar":          ["Chicago, IL", "Dubai, UAE", "Doha, Qatar"],
    "nigeria":        ["New York, NY", "Lagos, Nigeria"],
    "ghana":          ["New York, NY", "Accra, Ghana"],
    "south africa":   ["New York, NY", "Amsterdam, Netherlands", "Johannesburg, South Africa"],
    "kenya":          ["New York, NY", "Amsterdam, Netherlands", "Nairobi, Kenya"],
    "ethiopia":       ["New York, NY", "Dubai, UAE", "Addis Ababa, Ethiopia"],
    "egypt":          ["New York, NY", "Leipzig, Germany", "Cairo, Egypt"],
    "morocco":        ["New York, NY", "Leipzig, Germany", "Casablanca, Morocco"],
    "tanzania":       ["New York, NY", "Amsterdam, Netherlands", "Dar es Salaam, Tanzania"],
    "cameroon":       ["New York, NY", "Paris, France", "Douala, Cameroon"],
    "brazil":         ["Miami, FL", "Sao Paulo, Brazil"],
    "mexico":         ["Dallas, TX", "Mexico City, Mexico"],
    "colombia":       ["Miami, FL", "Bogota, Colombia"],
    "argentina":      ["Miami, FL", "Sao Paulo, Brazil", "Buenos Aires, Argentina"],
    "chile":          ["Miami, FL", "Sao Paulo, Brazil", "Santiago, Chile"],
    "peru":           ["Miami, FL", "Lima, Peru"],
    "venezuela":      ["Miami, FL", "Caracas, Venezuela"],
    "ecuador":        ["Miami, FL", "Bogota, Colombia"],
    "costa rica":     ["Miami, FL", "San Jose, Costa Rica"],
    "dominican republic": ["Miami, FL", "Santo Domingo, Dominican Republic"],
    "jamaica":        ["Miami, FL", "Kingston, Jamaica"],
    "canada":         ["Chicago, IL", "Toronto, Canada"],
}


# ─────────────────────────────────────────────────────────────────────────────
# TRANSIT HOURS
# ─────────────────────────────────────────────────────────────────────────────
TRANSIT_HOURS = {
    ("Phoenix, AZ",        "Chicago, IL"):                  5,
    ("Phoenix, AZ",        "Los Angeles, CA"):              2,
    ("Phoenix, AZ",        "Miami, FL"):                    6,
    ("Phoenix, AZ",        "New York, NY"):                 7,
    ("Phoenix, AZ",        "Dallas, TX"):                   4,
    ("Chicago, IL",        "London, UK"):                  10,
    ("Chicago, IL",        "Leipzig, Germany"):            10,
    ("Chicago, IL",        "Dubai, UAE"):                  14,
    ("Chicago, IL",        "Toronto, Canada"):              3,
    ("Los Angeles, CA",    "Tokyo, Japan"):                12,
    ("Los Angeles, CA",    "Seoul, South Korea"):          12,
    ("Los Angeles, CA",    "Hong Kong"):                   14,
    ("Los Angeles, CA",    "Sydney, Australia"):           16,
    ("Los Angeles, CA",    "Mumbai, India"):               18,
    ("Los Angeles, CA",    "Dubai, UAE"):                  17,
    ("Miami, FL",          "Sao Paulo, Brazil"):           10,
    ("Miami, FL",          "Bogota, Colombia"):             4,
    ("Miami, FL",          "Lima, Peru"):                   6,
    ("Miami, FL",          "Caracas, Venezuela"):           4,
    ("Miami, FL",          "San Jose, Costa Rica"):         3,
    ("Miami, FL",          "Santo Domingo, Dominican Republic"): 3,
    ("Miami, FL",          "Kingston, Jamaica"):            2,
    ("New York, NY",       "Lagos, Nigeria"):              12,
    ("New York, NY",       "Accra, Ghana"):                12,
    ("New York, NY",       "Amsterdam, Netherlands"):       8,
    ("New York, NY",       "Leipzig, Germany"):             9,
    ("New York, NY",       "Dubai, UAE"):                  13,
    ("Dallas, TX",         "Mexico City, Mexico"):          3,
    ("Leipzig, Germany",   "Paris, France"):                3,
    ("Leipzig, Germany",   "Milan, Italy"):                 3,
    ("Leipzig, Germany",   "Madrid, Spain"):                3,
    ("Leipzig, Germany",   "Amsterdam, Netherlands"):       2,
    ("Leipzig, Germany",   "Vienna, Austria"):              3,
    ("Leipzig, Germany",   "Zurich, Switzerland"):          3,
    ("Leipzig, Germany",   "Stockholm, Sweden"):            3,
    ("Leipzig, Germany",   "Oslo, Norway"):                 3,
    ("Leipzig, Germany",   "Copenhagen, Denmark"):          3,
    ("Leipzig, Germany",   "Warsaw, Poland"):               3,
    ("Leipzig, Germany",   "Lisbon, Portugal"):             4,
    ("Leipzig, Germany",   "Athens, Greece"):               4,
    ("Leipzig, Germany",   "Istanbul, Turkey"):             4,
    ("Leipzig, Germany",   "Tel Aviv, Israel"):             5,
    ("Leipzig, Germany",   "Cairo, Egypt"):                 5,
    ("Leipzig, Germany",   "Casablanca, Morocco"):          4,
    ("Dubai, UAE",         "Riyadh, Saudi Arabia"):         3,
    ("Dubai, UAE",         "Kuwait City, Kuwait"):          3,
    ("Dubai, UAE",         "Doha, Qatar"):                  2,
    ("Dubai, UAE",         "Karachi, Pakistan"):            3,
    ("Dubai, UAE",         "Addis Ababa, Ethiopia"):        5,
    ("Hong Kong",          "Shanghai, China"):              3,
    ("Hong Kong",          "Singapore"):                    4,
    ("Hong Kong",          "Kuala Lumpur, Malaysia"):       4,
    ("Hong Kong",          "Bangkok, Thailand"):            4,
    ("Hong Kong",          "Manila, Philippines"):          3,
    ("Hong Kong",          "Jakarta, Indonesia"):           5,
    ("Hong Kong",          "Ho Chi Minh City, Vietnam"):   3,
    ("Hong Kong",          "Taipei, Taiwan"):               2,
    ("Hong Kong",          "Dhaka, Bangladesh"):            5,
    ("Sydney, Australia",  "Auckland, New Zealand"):        4,
    ("Amsterdam, Netherlands", "Johannesburg, South Africa"): 11,
    ("Amsterdam, Netherlands", "Nairobi, Kenya"):           9,
    ("Amsterdam, Netherlands", "Dar es Salaam, Tanzania"):  9,
    ("Paris, France",      "Douala, Cameroon"):             7,
    ("Sao Paulo, Brazil",  "Buenos Aires, Argentina"):      4,
    ("Sao Paulo, Brazil",  "Santiago, Chile"):              4,
}

TRANSIT_DAYS_TOTAL = {
    "canada": (3,5), "mexico": (4,7),
    "united kingdom": (5,8), "uk": (5,8), "germany": (5,8),
    "france": (5,8), "netherlands": (5,8), "italy": (6,9),
    "spain": (6,9), "sweden": (6,9), "norway": (7,10),
    "denmark": (6,9), "poland": (7,10), "switzerland": (5,8),
    "portugal": (6,9), "austria": (6,9), "belgium": (5,8),
    "greece": (7,10), "turkey": (7,10), "israel": (7,10),
    "united arab emirates": (6,9), "uae": (6,9),
    "saudi arabia": (7,10), "kuwait": (7,10), "qatar": (7,10),
    "japan": (7,10), "south korea": (7,10), "china": (8,12),
    "hong kong": (7,10), "singapore": (9,13), "india": (9,13),
    "thailand": (9,13), "malaysia": (9,13), "indonesia": (10,14),
    "vietnam": (10,14), "philippines": (10,14), "taiwan": (8,12),
    "pakistan": (10,14), "bangladesh": (11,15),
    "australia": (10,14), "new zealand": (12,16),
    "brazil": (8,12), "colombia": (7,11), "argentina": (10,14),
    "chile": (10,14), "peru": (9,13),
    "costa rica": (7,11), "dominican republic": (6,10),
    "jamaica": (6,10), "venezuela": (8,12), "ecuador": (8,12),
    "nigeria": (12,18), "ghana": (12,18), "south africa": (14,20),
    "kenya": (14,20), "ethiopia": (14,20), "egypt": (10,14),
    "morocco": (10,14), "tanzania": (14,20), "cameroon": (14,20),
}

STATUS_PROGRESSION = {
    "Label Created":                  "Package Received",
    "Package Received":               "Departed Origin Facility",
    "Departed Origin Facility":       "Arrived at Hub",
    "Arrived at Hub":                 "Departed Hub",
    "Departed Hub":                   "Arrived in Destination Country",
    "Arrived in Destination Country": "Out for Delivery",
    "Out for Delivery":               "Delivered",
    "Delivered":                      "Delivered",
}

PROGRESS_PERCENT = {
    "Label Created": 5, "Package Received": 15,
    "Departed Origin Facility": 25, "Arrived at Hub": 40,
    "Departed Hub": 55, "Arrived in Destination Country": 70,
    "Out for Delivery": 85, "Delivered": 100,
}

# Local descriptions used during catch-up (no Gemini needed, no rate limits)
# Hub → real airport/facility name mapping for realistic descriptions
HUB_FACILITIES = {
    "Phoenix, AZ":                       "Phoenix Sky Harbor International (PHX)",
    "Chicago, IL":                       "Chicago O'Hare International (ORD)",
    "Los Angeles, CA":                   "Los Angeles International (LAX)",
    "Miami, FL":                         "Miami International Airport (MIA)",
    "New York, NY":                      "John F. Kennedy International (JFK)",
    "Dallas, TX":                        "Dallas/Fort Worth International (DFW)",
    "Leipzig, Germany":                  "Leipzig/Halle DHL Hub (LEJ)",
    "London, UK":                        "London Heathrow (LHR)",
    "Paris, France":                     "Paris Charles de Gaulle (CDG)",
    "Amsterdam, Netherlands":            "Amsterdam Schiphol (AMS)",
    "Milan, Italy":                      "Milan Malpensa (MXP)",
    "Madrid, Spain":                     "Madrid Barajas (MAD)",
    "Vienna, Austria":                   "Vienna International (VIE)",
    "Zurich, Switzerland":               "Zurich Airport (ZRH)",
    "Stockholm, Sweden":                 "Stockholm Arlanda (ARN)",
    "Oslo, Norway":                      "Oslo Gardermoen (OSL)",
    "Copenhagen, Denmark":               "Copenhagen Airport (CPH)",
    "Warsaw, Poland":                    "Warsaw Chopin Airport (WAW)",
    "Lisbon, Portugal":                  "Lisbon Humberto Delgado (LIS)",
    "Athens, Greece":                    "Athens International (ATH)",
    "Istanbul, Turkey":                  "Istanbul Airport (IST)",
    "Tel Aviv, Israel":                  "Ben Gurion International (TLV)",
    "Dubai, UAE":                        "Dubai International (DXB)",
    "Riyadh, Saudi Arabia":              "King Khalid International (RUH)",
    "Kuwait City, Kuwait":               "Kuwait International Airport (KWI)",
    "Doha, Qatar":                       "Hamad International Airport (DOH)",
    "Tokyo, Japan":                      "Tokyo Narita International (NRT)",
    "Seoul, South Korea":                "Incheon International Airport (ICN)",
    "Hong Kong":                         "Hong Kong International (HKG)",
    "Shanghai, China":                   "Shanghai Pudong International (PVG)",
    "Taipei, Taiwan":                    "Taiwan Taoyuan International (TPE)",
    "Singapore":                         "Singapore Changi Airport (SIN)",
    "Kuala Lumpur, Malaysia":            "Kuala Lumpur International (KUL)",
    "Bangkok, Thailand":                 "Suvarnabhumi Airport Bangkok (BKK)",
    "Manila, Philippines":               "Ninoy Aquino International (MNL)",
    "Jakarta, Indonesia":                "Soekarno-Hatta International (CGK)",
    "Ho Chi Minh City, Vietnam":         "Tan Son Nhat International (SGN)",
    "Mumbai, India":                     "Chhatrapati Shivaji Maharaj (BOM)",
    "Delhi, India":                      "Indira Gandhi International (DEL)",
    "Karachi, Pakistan":                 "Jinnah International Airport (KHI)",
    "Dhaka, Bangladesh":                 "Hazrat Shahjalal International (DAC)",
    "Sydney, Australia":                 "Sydney Kingsford Smith (SYD)",
    "Auckland, New Zealand":             "Auckland International Airport (AKL)",
    "Lagos, Nigeria":                    "Murtala Muhammed International (LOS)",
    "Accra, Ghana":                      "Kotoka International Airport (ACC)",
    "Johannesburg, South Africa":        "O.R. Tambo International (JNB)",
    "Nairobi, Kenya":                    "Jomo Kenyatta International (NBO)",
    "Addis Ababa, Ethiopia":             "Addis Ababa Bole International (ADD)",
    "Cairo, Egypt":                      "Cairo International Airport (CAI)",
    "Casablanca, Morocco":               "Mohammed V International (CMN)",
    "Dar es Salaam, Tanzania":           "Julius Nyerere International (DAR)",
    "Douala, Cameroon":                  "Douala International Airport (DLA)",
    "Sao Paulo, Brazil":                 "São Paulo/Guarulhos International (GRU)",
    "Buenos Aires, Argentina":           "Ministro Pistarini International (EZE)",
    "Santiago, Chile":                   "Arturo Merino Benítez International (SCL)",
    "Lima, Peru":                        "Jorge Chávez International (LIM)",
    "Bogota, Colombia":                  "El Dorado International (BOG)",
    "Caracas, Venezuela":                "Simón Bolívar International (CCS)",
    "Mexico City, Mexico":               "Mexico City International (MEX)",
    "San Jose, Costa Rica":              "Juan Santamaría International (SJO)",
    "Santo Domingo, Dominican Republic": "Las Américas International (SDQ)",
    "Kingston, Jamaica":                 "Norman Manley International (KIN)",
    "Toronto, Canada":                   "Toronto Pearson International (YYZ)",
    "Vancouver, Canada":                 "Vancouver International Airport (YVR)",
}

def _get_catchup_description(status: str, location: str) -> str:
    """Generate a short realistic scan description for catch-up events."""
    facility = HUB_FACILITIES.get(location, location)
    templates = {
        "Departed Origin Facility": [
            f"Shipment departed {facility}. En route to connecting hub.",
            f"Package dispatched from origin. Departed {facility} on scheduled service.",
        ],
        "Arrived at Hub": [
            f"Arrived at {facility}. Package processing for onward connection.",
            f"Shipment received at {facility}. Staged for next departure.",
        ],
        "Departed Hub": [
            f"Departed {facility}. Loaded on international cargo service.",
            f"Package dispatched from {facility}. In transit to destination country.",
        ],
        "Arrived in Destination Country": [
            f"Arrived at {facility}. Package presented to customs for clearance.",
            f"Shipment cleared inbound at {facility}. Customs processing underway.",
        ],
        "Out for Delivery": [
            f"Package collected from {facility}. Out for final delivery.",
            f"Loaded on delivery vehicle at {facility}. Final delivery in progress.",
        ],
        "Delivered": [
            "Package successfully delivered to recipient. Shipment complete.",
            "Delivered to consignee. Proof of delivery obtained.",
        ],
    }
    import random as _r
    options = templates.get(status, [f"{status} at {location}."])
    return _r.choice(options)

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _local_time_str(utc_dt: datetime, hub_location: str) -> str:
    offset_hours = HUB_TIMEZONES.get(hub_location, 0)
    local_dt = utc_dt + timedelta(hours=offset_hours)
    hour = local_dt.hour
    minute = local_dt.minute
    ampm = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    return f"{local_dt.strftime('%Y-%m-%d')} at {hour12}:{minute:02d} {ampm}"


def _parse_timestamp(ts_str: str, location: str) -> datetime:
    try:
        clean = ts_str.replace(' at ', ' ')
        local_dt = datetime.strptime(clean, '%Y-%m-%d %I:%M %p')
        offset_hours = HUB_TIMEZONES.get(location, 0)
        return local_dt - timedelta(hours=offset_hours)
    except Exception:
        return datetime.utcnow()


def _parse_expected_date(expected_date_str: str):
    try:
        return datetime.strptime(expected_date_str.strip(), '%B %d, %Y')
    except Exception:
        return None


def _build_full_route(country: str, destination_city: str) -> list:
    key = country.lower().strip()
    hubs = ROUTING_TABLE.get(key, ["Chicago, IL", "Leipzig, Germany"])
    return ["Phoenix, AZ"] + hubs + [destination_city]


def _get_total_days(country: str) -> tuple:
    key = country.lower().strip()
    return TRANSIT_DAYS_TOTAL.get(key, (10, 15))


def _next_location(route: list, current_location: str) -> str:
    try:
        idx = route.index(current_location)
        return route[idx + 1] if idx + 1 < len(route) else route[-1]
    except ValueError:
        return route[1] if len(route) > 1 else route[0]


def _calc_next_utc(next_status, next_location, current_location, current_utc, expected_dt):
    """Calculate the UTC time the next stage should happen."""
    if next_status == 'Out for Delivery' and expected_dt:
        local_9am = (expected_dt - timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        offset = HUB_TIMEZONES.get(next_location, 0)
        return local_9am - timedelta(hours=offset)

    if next_status == 'Delivered' and expected_dt:
        local_2pm = expected_dt.replace(hour=14, minute=0, second=0, microsecond=0)
        offset = HUB_TIMEZONES.get(next_location, 0)
        return local_2pm - timedelta(hours=offset)

    transit_h = TRANSIT_HOURS.get((current_location, next_location), 8)
    return current_utc + timedelta(hours=transit_h)


# ─────────────────────────────────────────────────────────────────────────────
# LOCAL FALLBACK: Generate new shipment with zero API calls
# ─────────────────────────────────────────────────────────────────────────────
def _generate_local(destination_city: str, destination_country: str) -> dict:
    """Generates a complete new shipment using only local templates. Zero API calls."""
    import random as _r
    now_utc       = datetime.utcnow()
    tracking_id   = 'OT' + ''.join([str(secrets.randbelow(10)) for _ in range(10)])
    route         = _build_full_route(destination_country, destination_city)
    _, max_days   = _get_total_days(destination_country)
    expected_date = (now_utc + timedelta(days=max_days)).strftime('%B %d, %Y')
    weight_val    = round(_r.uniform(4.2, 4.6), 1)
    label_time_str   = _local_time_str(now_utc - timedelta(hours=2), "Phoenix, AZ")
    phoenix_time_str = _local_time_str(now_utc, "Phoenix, AZ")
    data = {
        "trackingId":      tracking_id,
        "status":          "Package Received",
        "expectedDate":    expected_date,
        "progressPercent": 15,
        "progressLabels":  [
            "Label Created", "Package Received", "Departed Origin Facility",
            "Arrived at Hub", "Departed Hub", "Arrived in Destination Country",
            "Out for Delivery", "Delivered"
        ],
        "recentEvent": {
            "status":      "Package Received",
            "location":    "Phoenix, AZ",
            "timestamp":   phoenix_time_str,
            "description": "Package received and accepted at Phoenix Sky Harbor (PHX). Shipment prepared for dispatch.",
        },
        "allEvents": [
            {"date": label_time_str,   "event": "Label Created",    "city": "Phoenix, AZ"},
            {"date": phoenix_time_str, "event": "Package Received", "city": "Phoenix, AZ"},
        ],
        "shipmentDetails": {
            "service":        "International Express",
            "weight":         f"{weight_val} lbs",
            "dimensions":     '12" x 10" x 4"',
            "originZip":      "85043",
            "destinationZip": "",
        },
    }
    return {"success": True, "data": data}


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC FUNCTION 1: Generate new shipment (Label Created + Package Received)
# ─────────────────────────────────────────────────────────────────────────────
def generate_shipment_data(destination_city: str, destination_country: str) -> dict:
    from .models import SiteSettings
    ai_mode = SiteSettings.get_ai_provider()

    if ai_mode == 'local_only':
        # Build shipment data entirely from local logic, no Gemini
        return _generate_local(destination_city, destination_country)

    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set in environment variables.")

    now_utc      = datetime.utcnow()
    tracking_id  = 'OT' + ''.join([str(secrets.randbelow(10)) for _ in range(10)])
    route        = _build_full_route(destination_country, destination_city)
    _, max_days  = _get_total_days(destination_country)
    expected_date = (now_utc + timedelta(days=max_days)).strftime('%B %d, %Y')

    label_time_str   = _local_time_str(now_utc - timedelta(hours=2), "Phoenix, AZ")
    phoenix_time_str  = _local_time_str(now_utc, "Phoenix, AZ")
    route_display     = " → ".join(route)
    import random as _r
    weight_val = round(_r.uniform(4.2, 4.6), 1)
    prompt = f"""You are a logistics data generator for OnTrac Courier.

Generate initial shipment data for a Milani Cosmetics package just received at Phoenix facility.
Tracking ID: {tracking_id}
Destination: {destination_city}, {destination_country}
Label created time (Phoenix local): {label_time_str}
Package received time (Phoenix local): {phoenix_time_str}
Expected delivery: {expected_date}
Full route this shipment will take: {route_display}

RULES:
- Two events in allEvents: Label Created first, then Package Received
- recentEvent is the Package Received event
- Descriptions sound like real carrier system scan messages

Return ONLY valid JSON, no markdown fences:
{{
  "trackingId": "{tracking_id}",
  "status": "Package Received",
  "expectedDate": "{expected_date}",
  "progressPercent": 15,
  "progressLabels": ["Label Created","Package Received","Departed Origin Facility","Arrived at Hub","Departed Hub","Arrived in Destination Country","Out for Delivery","Delivered"],
  "recentEvent": {{
    "status": "Package Received",
    "location": "Phoenix, AZ",
    "timestamp": "{phoenix_time_str}",
    "description": "Package received and accepted at Phoenix origin facility. Shipment in preparation for dispatch."
  }},
  "allEvents": [
    {{
      "date": "{label_time_str}",
      "event": "Label Created",
      "city": "Phoenix, AZ"
    }},
    {{
      "date": "{phoenix_time_str}",
      "event": "Package Received",
      "city": "Phoenix, AZ"
    }}
  ],
  "shipmentDetails": {{
    "service": "International Express",
    "weight": "{weight_val} lbs",
    "dimensions": "12\\" x 10\\" x 4\\"",
    "originZip": "85043",
    "destinationZip": ""
  }}
}}"""

    result = _call_gemini(api_key, prompt, force_tracking_id=tracking_id)
    if not result['success']:
        print(f"[AI] All Gemini models failed for generate, using local fallback. Reason: {result.get('error')}")
        return _generate_local(destination_city, destination_country)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC FUNCTION 2: Smart advance (catch-up + single Gemini)
# ─────────────────────────────────────────────────────────────────────────────
def smart_advance_shipment(current_data: dict) -> dict:
    """
    Called by the Advance to Next Stage button.

    Checks how many stages should have happened since the last logged event:
    - 2 or more missed → fills ALL of them instantly using local descriptions
    - 0 or 1 missed   → advances one step using Gemini for a realistic description

    Out for Delivery = day before ExpectedDate at 9AM local destination time
    Delivered        = ExpectedDate at 2PM local destination time
    """
    now_utc = datetime.utcnow()

    if current_data.get('status') == 'Delivered':
        return {"success": False, "error": "Shipment is already delivered."}

    missed = _get_missed_stages(current_data, now_utc)

    if len(missed) >= 2:
        result = _apply_caught_up_stages(current_data, missed)
        result['message'] = f"Caught up {len(missed)} missed stage(s) automatically."
        return result
    else:
        return advance_shipment_stage(current_data)


def _get_missed_stages(current_data: dict, now_utc: datetime) -> list:
    """Returns list of (status, location, event_utc, timestamp_str) for all missed stages."""
    current_status = current_data.get('status', 'Package Received')
    if current_status == 'Delivered':
        return []

    recent_event     = current_data.get('recentEvent', {})
    current_location = recent_event.get('location', 'Phoenix, AZ')
    last_utc         = _parse_timestamp(recent_event.get('timestamp', ''), current_location)

    destination  = current_data.get('destination', '')
    parts        = destination.rsplit(',', 1)
    dest_city    = parts[0].strip() if len(parts) == 2 else destination
    dest_country = parts[1].strip() if len(parts) == 2 else ''
    route        = _build_full_route(dest_country, dest_city)
    expected_dt  = _parse_expected_date(current_data.get('expectedDate', ''))

    missed      = []
    status      = current_status
    location    = current_location
    current_utc = last_utc

    while status != 'Delivered':
        next_status   = STATUS_PROGRESSION.get(status, 'Delivered')
        next_location = _next_location(route, location)
        next_utc      = _calc_next_utc(next_status, next_location, location, current_utc, expected_dt)

        if next_utc <= now_utc:
            ts_str = _local_time_str(next_utc, next_location)
            missed.append((next_status, next_location, next_utc, ts_str))
            status      = next_status
            location    = next_location
            current_utc = next_utc
        else:
            break

    return missed


def _apply_caught_up_stages(current_data: dict, stages: list) -> dict:
    updated    = dict(current_data)
    all_events = list(current_data.get('allEvents', []))

    for next_status, next_location, _, ts_str in stages:
        description = _get_catchup_description(next_status, next_location)

        new_recent = {
            'status':      next_status,
            'location':    next_location,
            'timestamp':   ts_str,
            'description': description,
        }
        all_events.append({'date': ts_str, 'event': next_status, 'city': next_location})
        updated['status']          = next_status
        updated['recentEvent']     = new_recent
        updated['progressPercent'] = PROGRESS_PERCENT.get(next_status, 50)

    updated['allEvents'] = all_events
    return {'success': True, 'data': updated, 'stages_filled': len(stages)}


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC FUNCTION 3: Single Gemini advance (called internally)
# ─────────────────────────────────────────────────────────────────────────────
def advance_shipment_stage(current_data: dict) -> dict:
    from .models import SiteSettings
    ai_mode = SiteSettings.get_ai_provider()

    api_key = getattr(settings, 'GEMINI_API_KEY', '') if ai_mode != 'local_only' else ''

    tracking_id    = current_data.get('trackingId', '')
    current_status = current_data.get('status', 'Package Received')
    destination    = current_data.get('destination', '')
    recent_event   = current_data.get('recentEvent', {})
    all_events     = current_data.get('allEvents', [])

    if current_status == 'Delivered':
        return {"success": False, "error": "Shipment is already delivered."}

    parts        = destination.rsplit(',', 1)
    dest_city    = parts[0].strip() if len(parts) == 2 else destination
    dest_country = parts[1].strip() if len(parts) == 2 else ''
    route        = _build_full_route(dest_country, dest_city)

    next_status      = STATUS_PROGRESSION.get(current_status, "In Transit")
    current_location = recent_event.get('location', 'Phoenix, AZ')
    next_location    = _next_location(route, current_location)
    last_utc         = _parse_timestamp(recent_event.get('timestamp', ''), current_location)
    expected_dt      = _parse_expected_date(current_data.get('expectedDate', ''))

    next_event_utc     = _calc_next_utc(next_status, next_location, current_location, last_utc, expected_dt)
    next_timestamp_str = _local_time_str(next_event_utc, next_location)
    next_progress      = PROGRESS_PERCENT.get(next_status, 50)

    facility_name = HUB_FACILITIES.get(next_location, next_location)
    prompt = f"""You are a logistics data generator for OnTrac Courier.

Generate the next scan event for this in-transit shipment.
Tracking ID: {tracking_id}
Next status: {next_status}
Facility: {facility_name}
Local time: {next_timestamp_str}

Write ONE short realistic carrier scan message. Include the facility name naturally.
Max 15 words. Sound like real DHL or FedEx. No preamble.

Return ONLY valid JSON, no markdown fences:
{{
  "status": "{next_status}",
  "location": "{next_location}",
  "timestamp": "{next_timestamp_str}",
  "description": "YOUR REALISTIC SCAN MESSAGE HERE"
}}"""

    result = _call_gemini(api_key, prompt)
    if not result['success']:
        # All Gemini models exhausted — fall back to local templates silently
        print(f"[AI] All models failed, using local fallback. Reason: {result.get('error')}")
        fallback_description = _get_catchup_description(next_status, next_location)
        new_event = {
            'status':      next_status,
            'location':    next_location,
            'timestamp':   next_timestamp_str,
            'description': fallback_description,
        }
    else:
        new_event = result['data']
    new_event_entry = {
        "date":  new_event.get('timestamp', next_timestamp_str),
        "event": next_status,
        "city":  next_location,
    }
    updated = dict(current_data)
    updated['status']          = next_status
    updated['recentEvent']     = new_event
    updated['allEvents']       = list(all_events) + [new_event_entry]
    updated['progressPercent'] = next_progress
    return {"success": True, "data": updated}


# ─────────────────────────────────────────────────────────────────────────────
# SHARED GEMINI CALLER
# ─────────────────────────────────────────────────────────────────────────────
# Model cascade — tried in order, falls back on 429
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
]

def _call_gemini(api_key: str, prompt: str, force_tracking_id: str = None) -> dict:
    """
    Tries Gemini models in cascade order.
    2.5 Flash (20/day) → 2.0 Flash Lite (1500/day) → caller handles local fallback.
    """
    last_error = "All Gemini models exhausted."

    for model in GEMINI_MODELS:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{model}:generateContent?key={api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2000},
        }
        try:
            resp = requests.post(url, json=payload, timeout=20)

            # 429 = rate limited on this model, try next
            if resp.status_code == 429:
                print(f"[AI] {model} rate limited, trying next model...")
                last_error = f"Rate limited on {model}"
                continue

            resp.raise_for_status()
            text = resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()

            if text.startswith('```'):
                text = text.split('```')[1]
                if text.startswith('json'):
                    text = text[4:]
            text = text.strip()

            data = json.loads(text)

            if force_tracking_id:
                data['trackingId']     = force_tracking_id
                data['progressLabels'] = [
                    "Label Created", "Package Received", "Departed Origin Facility",
                    "Arrived at Hub", "Departed Hub", "Arrived in Destination Country",
                    "Out for Delivery", "Delivered"
                ]
                if 'shipmentDetails' not in data:
                    data['shipmentDetails'] = {}
                data['shipmentDetails']['service']   = "International Express"
                data['shipmentDetails']['originZip'] = "85043"
                if not data['shipmentDetails'].get('weight') or data['shipmentDetails']['weight'] == '0 lbs':
                    import random as _r
                    data['shipmentDetails']['weight'] = f"{round(_r.uniform(4.2, 4.6), 1)} lbs"
                data['shipmentDetails']['dimensions'] = '12" x 10" x 4"'

            print(f"[AI] Success with {model}")
            return {"success": True, "data": data, "model_used": model}

        except requests.RequestException as e:
            last_error = f"Gemini API error ({model}): {str(e)}"
            continue
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            last_error = f"Failed to parse Gemini response ({model}): {str(e)}"
            continue
        except Exception as e:
            last_error = str(e)
            continue

    return {"success": False, "error": last_error}