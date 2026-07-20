#!/usr/bin/env python3
"""Nightly event aggregator for RJ Does Dallas.

Runs in GitHub Actions (see .github/workflows/fetch-events.yml) and writes
live-events.json in the repo root, which the site loads alongside events.json.

Sources (all optional — missing keys/feeds are skipped gracefully):
  * Ticketmaster Discovery API   (env TICKETMASTER_KEY)
  * SeatGeek API                 (env SEATGEEK_CLIENT_ID)
  * Any iCal/ICS feeds listed in scripts/feeds.json

Output rows use the same schema as events.json:
  {name, category, area, date, time, cost, description, url}

Stdlib only — no pip installs needed.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEEDS_FILE = Path(__file__).resolve().parent / "feeds.json"
OUT_FILE = ROOT / "live-events.json"

GEO = {"lat": 32.7767, "lng": -96.7970, "radius_miles": 50}  # 50mi covers the DFW suburbs

# Cities we consider "DFW". The lat/long radius sent to Ticketmaster/SeatGeek is
# NOT trustworthy on its own — TM has returned El Paso (600 mi away) inside a
# 50-mile query because of bad venue geo — so every source that reports a real
# city is gated on this list as well. Hand-configured feeds (ICS, Prekindle) are
# exempt: their `area` is a curated neighborhood like "Deep Ellum", not a city.
DFW_CITIES = {
    "dallas", "fort worth", "arlington", "plano", "irving", "garland", "frisco",
    "mckinney", "grand prairie", "mesquite", "carrollton", "denton", "richardson",
    "lewisville", "allen", "flower mound", "north richland hills", "mansfield",
    "rowlett", "bedford", "euless", "grapevine", "cedar hill", "wylie", "keller",
    "coppell", "hurst", "duncanville", "the colony", "little elm", "prosper",
    "rockwall", "burleson", "haltom city", "southlake", "waxahachie", "cleburne",
    "weatherford", "desoto", "lancaster", "farmers branch", "addison", "sachse",
    "murphy", "highland village", "corinth", "saginaw", "watauga", "crowley",
    "benbrook", "azle", "granbury", "midlothian", "ennis", "greenville",
    "sanger", "roanoke", "argyle", "justin", "aubrey", "melissa", "anna",
    "forney", "terrell", "seagoville", "balch springs", "glenn heights",
    "red oak", "arlington heights", "colleyville", "trophy club", "westlake",
    "las colinas", "university park", "highland park", "farmersville",
}


def is_dfw_city(city: str, state: str | None = None) -> bool:
    """True when a source-reported city is in the DFW metro. A missing city is
    allowed through (the radius query already constrained it, and we'd rather
    keep a local event than drop it); a city we DO know and don't recognize is
    rejected. State, when reported, must be Texas — that alone kills the
    'Arlington, VA' / 'Dallas, GA' class of false positive."""
    if state and state.strip().upper() not in ("TX", "TEXAS"):
        return False
    c = (city or "").strip().lower()
    if not c:
        return True
    return c in DFW_CITIES
DAYS_AHEAD = 30
UA = "rj-does-dallas-fetcher/1.0 (+https://letsdoitdallas.com)"
SITE = "https://letsdoitdallas.com"
PRESS_FILE = ROOT / "press.json"

# Keep in sync with DISTRICTS in js/data.js (slug, label, match substrings)
DISTRICTS = [
    ("downtown-dallas", "Downtown Dallas", ["downtown dallas", "victory park"]),
    ("deep-ellum", "Deep Ellum", ["deep ellum"]),
    ("arts-district", "Arts District", ["arts district"]),
    ("uptown", "Uptown", ["uptown"]),
    ("bishop-arts", "Bishop Arts", ["oak cliff", "bishop arts"]),
    ("design-district", "Design District", ["design district"]),
    ("lower-greenville", "Lower Greenville", ["lower greenville", "east dallas"]),
    ("fort-worth", "Fort Worth", ["fort worth", "southside"]),
    ("stockyards", "The Stockyards", ["stockyards"]),
    ("arlington", "Arlington", ["arlington"]),
    ("grapevine", "Grapevine", ["grapevine"]),
    ("irving", "Irving", ["irving", "las colinas"]),
    ("frisco", "Frisco", ["frisco"]),
    ("plano", "Plano", ["plano", "coppell", "addison", "richardson", "northwest dallas"]),
    ("mckinney", "McKinney", ["mckinney", "allen"]),
]


def http_json(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def http_text(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read().decode("utf-8", errors="replace")


def pretty_time(hhmm: str) -> str:
    m = re.match(r"(\d{1,2}):(\d{2})", hhmm or "")
    if not m:
        return "See details"
    h, mi = int(m.group(1)), m.group(2)
    ap = "PM" if h >= 12 else "AM"
    h = h % 12 or 12
    return f"{h}:{mi} {ap}"


def row(name, category, area, date, time, cost, desc, url, image=None):
    return {
        "name": (name or "").strip()[:140],
        "category": category or "festival",
        "area": (area or "Dallas–Fort Worth").strip()[:80],
        "date": date,
        "time": time or "See details",
        "cost": cost,
        "description": (desc or "").strip()[:280],
        "url": url or "#",
        "image": image,
    }


# ---------------------------------------------------------------- Ticketmaster
TM_SEGMENT = {"Music": "music", "Sports": "sports", "Arts & Theatre": "arts",
              "Film": "arts", "Family": "family", "Miscellaneous": "festival"}


def fetch_ticketmaster(start, end):
    key = os.environ.get("TICKETMASTER_KEY", "").strip()
    if not key:
        return []
    out = []
    for page in range(3):  # up to 600 events
        params = urllib.parse.urlencode({
            "apikey": key,
            "latlong": f"{GEO['lat']},{GEO['lng']}",
            "radius": GEO["radius_miles"], "unit": "miles",
            "startDateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "size": 200, "page": page, "sort": "date,asc",
        })
        try:
            data = http_json(f"https://app.ticketmaster.com/discovery/v2/events.json?{params}")
        except Exception as e:  # noqa: BLE001
            print(f"ticketmaster: page {page} failed: {e}", file=sys.stderr)
            break
        events = (data.get("_embedded") or {}).get("events") or []
        skipped = 0
        for ev in events:
            dates = (ev.get("dates") or {}).get("start") or {}
            venue = ((ev.get("_embedded") or {}).get("venues") or [{}])[0]
            if not is_dfw_city((venue.get("city") or {}).get("name"),
                               (venue.get("state") or {}).get("stateCode")):
                skipped += 1
                continue
            cls = (ev.get("classifications") or [{}])[0]
            seg = ((cls.get("segment") or {}).get("name"))
            price = (ev.get("priceRanges") or [{}])[0].get("min")
            genre = ((cls.get("genre") or {}).get("name") or "").strip()
            desc = f"{genre} event via Ticketmaster." if genre and genre != "Undefined" else "Live event via Ticketmaster."
            imgs = ev.get("images") or []
            img = next((im.get("url") for im in imgs
                        if im.get("ratio") == "16_9" and 500 <= (im.get("width") or 0) <= 1200),
                       imgs[0].get("url") if imgs else None)
            out.append(row(
                ev.get("name"), TM_SEGMENT.get(seg, "festival"),
                ", ".join(x for x in [venue.get("name"), (venue.get("city") or {}).get("name")] if x),
                dates.get("localDate"), pretty_time(dates.get("localTime", "")),
                round(price) if isinstance(price, (int, float)) else None,
                desc, ev.get("url"), img,
            ))
        if skipped:
            print(f"ticketmaster: page {page} dropped {skipped} non-DFW events")
        if page >= int((data.get("page") or {}).get("totalPages", 1)) - 1:
            break
    print(f"ticketmaster: {len(out)} events")
    return out


# -------------------------------------------------------------------- SeatGeek
def sg_category(tax_name: str) -> str:
    t = (tax_name or "").lower()
    if re.search(r"sports|nba|nfl|mlb|nhl|mls|soccer|baseball|basketball|football|hockey|racing|rodeo", t):
        return "sports"
    if re.search(r"concert|music", t):
        return "music"
    if re.search(r"theater|broadway|classical|opera|ballet|dance|literary", t):
        return "arts"
    if "comedy" in t:
        return "nightlife"
    if "family" in t:
        return "family"
    return "festival"


def fetch_seatgeek(start, end):
    cid = os.environ.get("SEATGEEK_CLIENT_ID", "").strip()
    if not cid:
        return []
    out = []
    for page in range(1, 4):  # up to 300 events
        params = urllib.parse.urlencode({
            "client_id": cid,
            "lat": GEO["lat"], "lon": GEO["lng"], "range": f"{GEO['radius_miles']}mi",
            "datetime_local.gte": start.strftime("%Y-%m-%dT00:00:00"),
            "datetime_local.lte": end.strftime("%Y-%m-%dT23:59:59"),
            "per_page": 100, "page": page,
        })
        try:
            data = http_json(f"https://api.seatgeek.com/2/events?{params}")
        except Exception as e:  # noqa: BLE001
            print(f"seatgeek: page {page} failed: {e}", file=sys.stderr)
            break
        events = data.get("events") or []
        skipped = 0
        for ev in events:
            dt = ev.get("datetime_local") or ""
            venue = ev.get("venue") or {}
            if not is_dfw_city(venue.get("city"), venue.get("state")):
                skipped += 1
                continue
            price = (ev.get("stats") or {}).get("lowest_price")
            tax = ((ev.get("taxonomies") or [{}])[0]).get("name", "")
            out.append(row(
                ev.get("short_title") or ev.get("title"), sg_category(tax),
                ", ".join(x for x in [venue.get("name"), venue.get("city")] if x),
                dt[:10] or None, pretty_time(dt[11:16]),
                round(price) if isinstance(price, (int, float)) else None,
                f"{(ev.get('type') or 'live').replace('_', ' ')} event via SeatGeek.",
                ev.get("url"),
                ((ev.get("performers") or [{}])[0]).get("image"),
            ))
        if skipped:
            print(f"seatgeek: page {page} dropped {skipped} non-DFW events")
        if len(events) < 100:
            break
    print(f"seatgeek: {len(out)} events")
    return out


# ------------------------------------------------------------------- ICS feeds
def unfold_ics(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    for line in lines:
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def parse_ics_datetime(val: str):
    val = val.strip()
    m = re.match(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2}))?", val)
    if not m:
        return None, None
    date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    time = pretty_time(f"{m.group(4)}:{m.group(5)}") if m.group(4) else "All day"
    return date, time


def fetch_ics_feeds(start, end):
    if not FEEDS_FILE.exists():
        return []
    try:
        feeds = json.loads(FEEDS_FILE.read_text()).get("ics_feeds", [])
    except Exception as e:  # noqa: BLE001
        print(f"feeds.json unreadable: {e}", file=sys.stderr)
        return []
    lo, hi = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    out = []
    for feed in feeds:
        url = feed.get("url")
        if not url:
            continue
        try:
            lines = unfold_ics(http_text(url))
        except Exception as e:  # noqa: BLE001
            print(f"ics feed failed ({url}): {e}", file=sys.stderr)
            continue
        ev, count = None, 0
        for line in lines:
            if line.startswith("BEGIN:VEVENT"):
                ev = {}
            elif line.startswith("END:VEVENT") and ev is not None:
                date, time = parse_ics_datetime(ev.get("DTSTART", ""))
                if date and lo <= date <= hi and ev.get("SUMMARY"):
                    out.append(row(
                        ev.get("SUMMARY"), feed.get("category", "festival"),
                        ev.get("LOCATION") or feed.get("area"),
                        date, time, feed.get("cost"),
                        re.sub(r"\\n", " ", ev.get("DESCRIPTION", ""))[:280],
                        ev.get("URL") or feed.get("fallback_url"),
                    ))
                    count += 1
                ev = None
            elif ev is not None and ":" in line:
                key, _, val = line.partition(":")
                key = key.split(";", 1)[0].upper()
                if key in ("SUMMARY", "DTSTART", "LOCATION", "DESCRIPTION", "URL"):
                    ev[key] = val.replace("\\,", ",").replace("\\;", ";")
        print(f"ics ({url}): {count} events")
    return out


# -------------------------------------------------------------------- Prekindle
def fetch_prekindle(start, end):
    """Local venues that sell through Prekindle. Their public listing page
    (prekindle.com/events/<slug>) embeds a schema.org JSON-LD array of
    upcoming events — machine-readable without any API key."""
    if not FEEDS_FILE.exists():
        return []
    try:
        pages = json.loads(FEEDS_FILE.read_text()).get("prekindle_pages", [])
    except Exception:  # noqa: BLE001
        return []
    lo, hi = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    out = []
    for p in pages:
        venue, area = p.get("venue", "Venue"), p.get("area", "Dallas")
        url = p.get("url") or (p.get("slug") and f"https://www.prekindle.com/events/{p['slug']}")
        if not url:
            continue
        try:
            html = http_text(url)
        except Exception as e:  # noqa: BLE001
            print(f"prekindle failed ({venue}): {e}", file=sys.stderr)
            continue
        m = re.search(r'application/ld\+json[^>]*>(.*?)</script>', html, re.S)
        if not m:
            print(f"prekindle ({venue}): no JSON-LD block")
            continue
        try:
            events = json.loads(m.group(1))
        except Exception as e:  # noqa: BLE001
            print(f"prekindle ({venue}): bad JSON-LD: {e}", file=sys.stderr)
            continue
        count = 0
        for ev in events if isinstance(events, list) else [events]:
            date = str(ev.get("startDate") or "")[:10]
            if not (ev.get("name") and lo <= date <= hi):
                continue
            price = (ev.get("offers") or {}).get("price")
            try:
                cost = float(price) if price is not None else None
            except (TypeError, ValueError):
                cost = None
            desc = (ev.get("description") or "").strip()
            if not desc or desc == ev.get("name"):
                desc = f"Live at {venue}."
            out.append(row(ev["name"], p.get("category", "music"), area,
                           date, "Doors — see listing", cost, desc, ev.get("url"),
                           ev.get("image")))
            count += 1
        print(f"prekindle ({venue}): {count} events")
    return out


# ------------------------------------------------------------------- Eventbrite
# Eventbrite retired its public event-search API (/v3/events/search/ now 404s
# without a token, and tokens only reach events you own), so there is no
# supported way to query "events in Dallas" through the API.
#
# Their city discovery pages, however, embed a schema.org ItemList of Events —
# the same machine-readable pattern we already parse for Prekindle. As of
# 2026-07 robots.txt allows /d/ for generic user agents (it disallows their
# internal /api/v3/destination/* and /rss/ paths, which we don't touch).
# 20 events per page; we walk a few pages per city.
EVENTBRITE_PAGE = "https://www.eventbrite.com/d/{slug}/all-events/?page={page}"

# Eventbrite tags each event with its own taxonomy, which beats guessing from
# the title. Anything unmapped (or their catch-all "Other") falls through to the
# keyword pass below.
EB_CATEGORY_MAP = {
    "music": "music",
    "food & drink": "food",
    "arts": "arts", "film & media": "arts", "science & technology": "arts",
    "sports & fitness": "sports", "health & wellness": "sports",
    "travel & outdoor": "outdoors",
    "family & education": "family",
    "fashion & beauty": "market", "home & lifestyle": "market",
    "hobbies & special interest": "market",
    "seasonal & holiday": "festival", "community & culture": "festival",
    "charity & causes": "festival", "religion & spirituality": "festival",
    "business & professional": "festival", "government & politics": "festival",
    "auto, boat & air": "festival",
}
EB_KEYWORDS = [
    ("music",     r"concert|live music|\bband\b|orchestra|jazz|hip hop|open mic|karaoke"),
    ("food",      r"\bfood\b|wine|beer|brunch|tasting|dinner|cocktail|brewery|culinary|bbq"),
    ("arts",      r"\bart\b|gallery|museum|theat|painting|poetry|exhibit"),
    ("outdoors",  r"hike|hiking|\b5k\b|yoga|trail|cycling|kayak|garden"),
    ("sports",    r"tournament|rodeo|league|boxing|wrestl|\bgolf\b"),
    ("family",    r"\bkids\b|family|children|storytime|toddler"),
    ("market",    r"market|vendor|pop-?up|bazaar|craft fair|flea"),
    ("nightlife", r"party|comedy|nightclub|late night|happy hour|day party"),
    ("festival",  r"festival|\bfest\b|celebration|expo|convention"),
]


def eb_category(tags, text: str) -> str:
    for t in tags or []:
        if t.get("prefix") == "EventbriteCategory":
            mapped = EB_CATEGORY_MAP.get((t.get("display_name") or "").strip().lower())
            if mapped:
                return mapped
    low = (text or "").lower()
    for cat, pattern in EB_KEYWORDS:
        if re.search(pattern, low):
            return cat
    return "festival"


def _eb_from_server_data(html: str):
    """Preferred path: the page's own __SERVER_DATA__ blob. Richer than the
    JSON-LD — it carries start_time, the online-event flag and Eventbrite's
    category tags, none of which appear in the structured-data block."""
    i = html.find("window.__SERVER_DATA__")
    if i < 0:
        return []
    j = html.find("{", i)
    if j < 0:
        return []
    try:
        data, _ = json.JSONDecoder().raw_decode(html[j:])
        results = ((data.get("search_data") or {}).get("events") or {}).get("results") or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for e in results:
        if e.get("is_online_event") or e.get("is_cancelled"):
            continue           # a city guide wants things you can physically attend
        venue = e.get("primary_venue") or {}
        addr = venue.get("address") or {}
        out.append({
            "name": e.get("name"),
            "date": (e.get("start_date") or "")[:10],
            "time": pretty_time(e.get("start_time") or "") if e.get("start_time") else "See details",
            "venue": venue.get("name"),
            "city": addr.get("city"),
            "region": addr.get("region"),
            "desc": e.get("summary"),
            "url": (e.get("url") or "").split("?")[0],
            "image": e.get("image", {}).get("url") if isinstance(e.get("image"), dict) else e.get("image"),
            "cat": eb_category(e.get("tags"), f"{e.get('name','')} {e.get('summary','')}"),
        })
    return out


def _eb_from_jsonld(html: str):
    """Fallback if the blob above moves or is renamed: the schema.org ItemList
    is a public contract and less likely to change, but it has no start times."""
    out = []
    for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>',
                            html, re.S):
        try:
            data = json.loads(block)
        except Exception:  # noqa: BLE001
            continue
        if not (isinstance(data, dict) and data.get("@type") == "ItemList"):
            continue
        for item in data.get("itemListElement") or []:
            ev = item.get("item") or {}
            if ev.get("@type") != "Event":
                continue
            place = ev.get("location") or {}
            addr = place.get("address") or {}
            raw = str(ev.get("startDate") or "")
            out.append({
                "name": ev.get("name"), "date": raw[:10],
                "time": pretty_time(raw[11:16]) if len(raw) >= 16 else "See details",
                "venue": place.get("name"), "city": addr.get("addressLocality"),
                "region": addr.get("addressRegion"), "desc": ev.get("description"),
                "url": (ev.get("url") or "").split("?")[0], "image": ev.get("image"),
                "cat": eb_category(None, f"{ev.get('name','')} {ev.get('description','')}"),
            })
    return out


def _eb_get(url: str, label: str, attempts: int = 3):
    """Fetch a discovery page, backing off on 429. Eventbrite throttles bursts,
    and a throttled source must degrade to "no events" rather than fail the
    nightly build — every caller treats None as 'stop walking this city'."""
    for attempt in range(attempts):
        try:
            return http_text(url)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < attempts - 1:
                wait = 5 * (attempt + 1)
                print(f"eventbrite throttled ({label}), retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"eventbrite failed ({label}): {e}", file=sys.stderr)
            return None
        except Exception as e:  # noqa: BLE001
            print(f"eventbrite failed ({label}): {e}", file=sys.stderr)
            return None
    return None


def fetch_eventbrite(start, end):
    if not FEEDS_FILE.exists():
        return []
    try:
        locations = json.loads(FEEDS_FILE.read_text()).get("eventbrite_locations", [])
    except Exception:  # noqa: BLE001
        return []
    lo, hi = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    out = []
    for loc in locations:
        slug = loc.get("slug")
        if not slug:
            continue
        kept = 0
        for page in range(1, int(loc.get("pages", 3)) + 1):
            html = _eb_get(EVENTBRITE_PAGE.format(slug=slug, page=page), f"{slug} p{page}")
            if html is None:
                break
            events = _eb_from_server_data(html) or _eb_from_jsonld(html)
            if not events:
                break                  # out of results, or the markup moved on us
            for e in events:
                if not (e["name"] and lo <= e["date"] <= hi):
                    continue
                if not is_dfw_city(e["city"], e["region"]):
                    continue
                out.append(row(
                    e["name"], e["cat"],
                    ", ".join(x for x in [e["venue"], e["city"]] if x),
                    e["date"], e["time"],
                    None,              # the discovery page carries no price
                    e["desc"], e["url"], e["image"],
                ))
                kept += 1
            time.sleep(2.0)            # be a polite guest on someone else's HTML
        print(f"eventbrite ({slug}): {kept} events")
    return out


# ----------------------------------------------------------------------- Seated
# Seated (seated.com) is artist-direct ticketing: the artists who sell through it
# often bypass Ticketmaster entirely, which is exactly the gap it fills here.
# Its API is keyed by ARTIST, not by city — there is no "events in Dallas" query
# — so we walk a watchlist of artist UUIDs and keep only their DFW-area dates.
#
# To add an artist: open their tour page, view source, and copy the UUID out of
# `data-artist-id="..."` (that's the same id their Seated widget uses). Then add
# {artist, id} to "seated_artists" in feeds.json.
#
# Endpoint (public, no key):
#   https://cdn.seated.com/api/tour/<artist-uuid>?include=tour-events
# NOTE: tours.seated.com/api/... answers 200 with text/html (an SPA fallback) —
# it is NOT an API. Only cdn.seated.com returns application/vnd.api+json.
SEATED_API = "https://cdn.seated.com/api/tour"
SEATED_LINK = "https://link.seated.com"

def _seated_is_dfw(formatted_address: str) -> bool:
    """formatted-address looks like 'Fort Worth, TX'. Split it and hand both
    halves to the shared gate. Stricter than is_dfw_city in one way: Seated
    always reports a city, so a missing one here means malformed data, not an
    unknown-but-probably-local venue."""
    parts = [p.strip() for p in (formatted_address or "").split(",")]
    if len(parts) < 2 or not parts[0]:
        return False
    return is_dfw_city(parts[0], parts[-1])


def _seated_local_time(starts_at: str, known: bool) -> str:
    """starts-at is UTC; the widget shows it in venue-local time. Convert to
    Central. If the artist hasn't published a set time, say so rather than
    inventing midnight."""
    if not known or not starts_at:
        return "See details"
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        local = dt.astimezone(ZoneInfo("America/Chicago"))
        return pretty_time(local.strftime("%H:%M"))
    except Exception:  # noqa: BLE001  (no tzdata on the runner, bad timestamp…)
        return "See details"


def fetch_seated(start, end):
    if not FEEDS_FILE.exists():
        return []
    try:
        artists = json.loads(FEEDS_FILE.read_text()).get("seated_artists", [])
    except Exception:  # noqa: BLE001
        return []
    lo, hi = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    out = []
    for a in artists:
        aid, name = a.get("id"), a.get("artist", "Artist")
        if not aid:
            continue
        try:
            data = http_json(f"{SEATED_API}/{aid}?include=tour-events")
        except Exception as e:  # noqa: BLE001
            print(f"seated failed ({name}): {e}", file=sys.stderr)
            continue
        artist_name = ((data.get("data") or {}).get("attributes") or {}).get("name") or name
        artist_img = ((data.get("data") or {}).get("attributes") or {}).get("image-url")
        kept = 0
        for ev in data.get("included") or []:
            if ev.get("type") != "tour-events":
                continue
            at = ev.get("attributes") or {}
            date = at.get("starts-at-date-local") or ""
            if not (lo <= date <= hi) or not _seated_is_dfw(at.get("formatted-address")):
                continue
            venue = at.get("venue-name") or ""
            city = (at.get("formatted-address") or "").split(",")[0].strip()
            desc = f"{artist_name} live at {venue}." if venue else f"{artist_name} live in {city}."
            if at.get("is-sold-out"):
                desc += " Sold out."
            out.append(row(
                artist_name, a.get("category", "music"),
                ", ".join(x for x in [venue, city] if x),
                date, _seated_local_time(at.get("starts-at"), at.get("is-starts-at-known")),
                None,  # Seated's API carries no price
                desc,
                f"{SEATED_LINK}/{ev.get('id')}" if ev.get("id") else a.get("url", "#"),
                artist_img,
            ))
            kept += 1
        print(f"seated ({artist_name}): {kept} DFW events")
    return out


# ----------------------------------------------------------------- press wire
def _strip_cdata(s: str) -> str:
    import html as _html
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)
    return _html.unescape(s).strip()


def fetch_press():
    """Latest culture headlines from DFW publication RSS feeds -> press.json."""
    if not FEEDS_FILE.exists():
        return []
    try:
        feeds = json.loads(FEEDS_FILE.read_text()).get("rss_feeds", [])
    except Exception:  # noqa: BLE001
        return []
    items = []
    for feed in feeds:
        url, source = feed.get("url"), feed.get("source", "PRESS")
        if not url:
            continue
        try:
            xml = http_text(url)
        except Exception as e:  # noqa: BLE001
            print(f"rss failed ({source}): {e}", file=sys.stderr)
            continue
        got = 0
        for m in re.finditer(r"<item>(.*?)</item>", xml, flags=re.S):
            block = m.group(1)
            t = re.search(r"<title>(.*?)</title>", block, flags=re.S)
            l = re.search(r"<link>(.*?)</link>", block, flags=re.S)
            if not t or not l:
                continue
            items.append({"title": _strip_cdata(t.group(1))[:140],
                          "url": _strip_cdata(l.group(1)),
                          "source": source})
            got += 1
            if got >= 3:
                break
        print(f"rss ({source}): {got} headlines")
    return items[:12]


# ----------------------------------------------------------- hub page builder
def _slugify_matches(area: str):
    a = (area or "").lower()
    for slug, _label, match in DISTRICTS:
        if any(m in a for m in match):
            return slug
    return None


def _jsonld(events):
    out = []
    for i, e in enumerate(events[:30]):
        t = re.match(r"(\d{1,2}):(\d{2})\s*(AM|PM)", e.get("time") or "")
        start = e["date"]
        end = e["date"]
        if t:
            h = int(t.group(1)) % 12 + (12 if t.group(3) == "PM" else 0)
            mi = int(t.group(2))
            start = f"{e['date']}T{h:02d}:{mi:02d}:00-05:00"
            # default a 3-hour run, clamped to the same day
            end_min = min(h * 60 + mi + 180, 23 * 60 + 59)
            end = f"{e['date']}T{end_min // 60:02d}:{end_min % 60:02d}:00-05:00"
        url = e["url"] if e["url"] and e["url"] != "#" else SITE
        item = {"@type": "Event", "name": e["name"], "startDate": start, "endDate": end,
                "eventStatus": "https://schema.org/EventScheduled",
                "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
                "location": {"@type": "Place", "name": e["area"],
                             "address": {"@type": "PostalAddress", "addressRegion": "TX",
                                         "addressLocality": e["area"]}},
                "image": [e.get("image") or f"{SITE}/og-image.png"],
                "url": url,
                "organizer": {"@type": "Organization", "name": e["area"], "url": url},
                "performer": {"@type": "PerformingGroup", "name": e["name"]}}
        if e.get("description"):
            item["description"] = e["description"]
        offer = {"@type": "Offer", "url": url,
                 "availability": "https://schema.org/InStock",
                 "validFrom": f"{e['date']}T00:00:00-05:00"}
        if e.get("cost") is not None:
            offer["price"] = e["cost"]
            offer["priceCurrency"] = "USD"
        item["offers"] = offer
        out.append({"@type": "ListItem", "position": i + 1, "item": item})
    return json.dumps({"@context": "https://schema.org", "@type": "ItemList", "itemListElement": out})


def _analytics_snippet():
    """Mirror index.html's Cloudflare beacon onto the generated hub pages.

    The token is read out of index.html rather than duplicated here so there is
    exactly one place to paste it. Returns "" when it is unset (or index.html
    has been restructured), which leaves the hub pages making no beacon call.
    """
    try:
        html = (ROOT / "index.html").read_text()
    except OSError:
        return ""
    m = re.search(r'var\s+CF_BEACON_TOKEN\s*=\s*"([^"]*)"', html)
    token = m.group(1) if m else ""
    if not token:
        return ""
    cfg = json.dumps({"token": token})
    return ('<script type="module" src="https://static.cloudflareinsights.com/beacon.min.js" '
            f"data-cf-beacon='{cfg}'></script>")


def _hub_html(title, desc, canonical, events, app_link, heading, note):
    rows = "\n".join(
        f'<li><a href="{e["url"]}" rel="noopener"><strong>{e["name"]}</strong></a> '
        f'<span>/ {e["date"]} · {e["time"]} · {e["area"]}</span></li>'
        for e in events[:60]) or "<li>Fresh listings load nightly — check the live radar.</li>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<meta name="description" content="{desc}"/>
<link rel="canonical" href="{canonical}"/>
<meta property="og:title" content="{title}"/>
<meta property="og:description" content="{desc}"/>
<meta property="og:type" content="website"/>
<meta property="og:url" content="{canonical}"/>
<meta property="og:image" content="{SITE}/og-image.png"/>
<meta name="twitter:card" content="summary_large_image"/>
<meta name="twitter:image" content="{SITE}/og-image.png"/>
<script type="application/ld+json">{_jsonld(events)}</script>
<style>
body{{background:#08090B;color:#8A909E;font:15px/1.6 Inter,-apple-system,sans-serif;margin:0;padding:40px 6vw}}
h1{{color:#fff;font-size:2rem;letter-spacing:.01em}}a{{color:#00FF87;text-decoration:none}}
.k{{font-family:ui-monospace,monospace;font-size:11px;letter-spacing:.12em;color:#00FF87}}
ul{{list-style:none;padding:0}}li{{padding:12px 0;border-bottom:1px solid #191C22}}
li span{{display:block;font-family:ui-monospace,monospace;font-size:11px;color:#8A909E}}
.cta{{display:inline-block;margin:18px 0;border:1px solid #0E3A2F;padding:12px 18px}}
</style>{_analytics_snippet()}</head><body>
<p class="k">/ LETS DO IT DALLAS — {note}</p>
<h1>{heading}</h1>
<a class="cta" href="{app_link}">( OPEN THE LIVE RADAR ↗ )</a>
<ul>{rows}</ul>
<p><a href="/">← letsdoitdallas.com</a></p>
</body></html>"""


def _config_value(key):
    """Pull a string value out of the CONFIG block in js/data.js.

    Same single-source trick as _analytics_snippet(): data.js stays the one
    place a non-coder edits, and generated pages follow it instead of keeping
    their own copy that silently drifts.
    """
    try:
        js = (ROOT / "js" / "data.js").read_text()
    except OSError:
        return ""
    m = re.search(key + r'\s*:\s*"([^"]*)"', js)
    return m.group(1) if m else ""


# Kept out of the f-string below so the CSS braces don't need doubling.
_ADVERTISE_CSS = """
.wrap{max-width:1100px;margin:0 auto;padding:0 var(--pad)}
.a-nav{border-bottom:1px solid var(--line);padding:14px var(--pad);
  font-family:var(--mono);font-size:11px;letter-spacing:.12em}
.a-hero{padding:clamp(40px,8vw,90px) 0 clamp(30px,5vw,60px);border-bottom:1px solid var(--line)}
.a-kicker{font-family:var(--mono);font-size:11px;letter-spacing:.16em;color:var(--gold);margin-bottom:18px}
.a-hero h1{font-size:clamp(32px,6vw,60px);line-height:1.04;letter-spacing:-.01em;max-width:16ch}
.a-sub{font-size:clamp(15px,2vw,18px);max-width:56ch;margin-top:20px;color:#9AA1B0}
.a-sec{padding:clamp(36px,6vw,68px) 0;border-bottom:1px solid var(--line)}
.a-sec h2{font-size:clamp(20px,3vw,28px);margin-bottom:8px}
.a-lead{font-family:var(--mono);font-size:11px;letter-spacing:.14em;color:var(--em);margin-bottom:16px}
.a-cols{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);
  border:1px solid var(--line);margin-top:26px}
.a-cols>div{background:var(--bg);padding:24px 20px}
.a-cols h3{font-size:15px;margin-bottom:8px}
.a-cols p{margin:0;font-size:13.5px;color:#7A8090}
@media(max-width:760px){.a-cols{grid-template-columns:1fr}}
.a-stats{display:grid;grid-template-columns:repeat(5,1fr);gap:1px;background:var(--line);
  border:1px solid var(--line);margin-top:26px}
.a-stats>div{background:var(--bg);padding:22px 16px;text-align:center}
.a-stats .n{font-family:var(--display);font-size:clamp(22px,3vw,32px);color:var(--em);font-weight:800;line-height:1}
.a-stats .l{font-family:var(--mono);font-size:9.5px;letter-spacing:.12em;margin-top:9px;color:#7A8090}
/* 5 stats into 2 columns leaves an empty 6th cell that reads as a broken
   box against the 1px grid — let the last one span the row instead. */
@media(max-width:760px){.a-stats{grid-template-columns:repeat(2,1fr)}
  .a-stats>div:last-child{grid-column:1/-1}}
.a-note{border-left:2px solid var(--jade);padding:4px 0 4px 20px;margin-top:24px;
  max-width:68ch;color:#9AA1B0;font-size:14.5px}
.a-ask{margin:22px 0 0;padding:0;list-style:none;max-width:64ch}
.a-ask li{padding:12px 0;border-bottom:1px solid var(--line);font-size:14.5px}
.a-ask li::before{content:"/ ";color:var(--em);font-family:var(--mono)}
.a-cta{display:inline-block;margin-top:26px;border:1px solid var(--em-dim);color:var(--em);
  background:rgba(14,58,47,.18);padding:16px 26px;font-family:var(--mono);
  font-size:12px;letter-spacing:.14em;transition:all .18s}
.a-cta:hover{background:var(--jade);color:var(--white)}
.a-form{display:grid;gap:12px;max-width:520px;margin-top:24px}
.a-form input,.a-form textarea{background:var(--bg-2);border:1px solid var(--line);color:var(--white);
  padding:13px 14px;font-family:var(--body);font-size:14px;border-radius:0}
.a-form input:focus,.a-form textarea:focus{outline:none;border-color:var(--em-dim)}
.a-form button{background:rgba(14,58,47,.18);border:1px solid var(--em-dim);color:var(--em);
  padding:15px;font-size:12px;letter-spacing:.14em;cursor:pointer;transition:all .18s}
.a-form button:hover{background:var(--jade);color:var(--white)}
.a-foot{padding:34px 0;font-family:var(--mono);font-size:11px;letter-spacing:.1em}
.demo{border:1px solid var(--line);margin-top:24px}
.demo-label{font-family:var(--mono);font-size:9.5px;letter-spacing:.14em;color:#4A4F5C;
  padding:10px 14px;border-bottom:1px solid var(--line)}
"""


def write_advertise():
    """Generate /advertise/ — the sponsorship sales page.

    Generated rather than hand-written so the inventory numbers are recomputed
    from the real feed every night. A stale "648 events" on a page a venue
    owner is reading is exactly the kind of thing that costs a sale.

    Deliberately quotes INVENTORY, never audience. The site is new and has no
    meaningful traffic history, and inventing one for a sales page aimed at
    local business owners is both dishonest and trivially checkable.
    """
    events = []
    for fname in ("live-events.json", "eventbrite.json"):
        p = ROOT / fname
        if p.exists():
            try:
                events += json.loads(p.read_text())
            except (ValueError, OSError):
                pass

    venues = {e.get("area") for e in events if e.get("area")}
    dates = sorted({e.get("date") for e in events if e.get("date")})
    span = len(dates)
    # Count the generated SEO hubs only: skip the site root and this page
    # itself, which isn't a landing page and would inflate the number.
    hubs = sum(1 for p in ROOT.rglob("index.html")
               if p.parent != ROOT and p.parent.name != "advertise")

    email = _config_value("contactEmail") or "hello@letsdoitdallas.com"
    endpoint = _config_value("advertiseEndpoint")
    subject = urllib.parse.quote("Founding partner — Lets Do It Dallas")
    body = urllib.parse.quote(
        "Venue / business name:\n"
        "What you'd want to promote:\n"
        "Website or socials:\n"
        "Best contact:\n")
    mailto = f"mailto:{email}?subject={subject}&body={body}"

    if endpoint:
        form = f"""<form class="a-form" action="{endpoint}" method="POST">
<input name="venue" placeholder="Venue or business name" required/>
<input name="email" type="email" placeholder="Your email" required/>
<input name="link" placeholder="Website or Instagram"/>
<textarea name="about" rows="3" placeholder="What would you want to promote?"></textarea>
<button type="submit">CLAIM A FOUNDING SPOT →</button>
</form>"""
    else:
        form = f'<a class="a-cta" href="{mailto}">CLAIM A FOUNDING SPOT →</a>'

    title = "Advertise on Lets Do It Dallas — Founding Partners, Free for 90 Days"
    desc = ("Three DFW venues get the pinned top spot on Lets Do It Dallas free "
            "for 90 days. No card, no contract.")

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<meta name="description" content="{desc}"/>
<link rel="canonical" href="{SITE}/advertise/"/>
<meta property="og:title" content="{title}"/>
<meta property="og:description" content="{desc}"/>
<meta property="og:type" content="website"/>
<meta property="og:url" content="{SITE}/advertise/"/>
<meta property="og:image" content="{SITE}/og-image.png"/>
<meta name="twitter:card" content="summary_large_image"/>
<meta name="twitter:image" content="{SITE}/og-image.png"/>
<link rel="stylesheet" href="/css/styles.css"/>
<link rel="preload" as="style" href="https://fonts.googleapis.com/css2?family=Syne:wght@600;700;800&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap"/>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Syne:wght@600;700;800&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" media="print" onload="this.media='all';this.onload=null"/>
<noscript><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Syne:wght@600;700;800&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap"/></noscript>
<style>{_ADVERTISE_CSS}</style>{_analytics_snippet()}</head><body>

<div class="a-nav"><a href="/">← LETS DO IT DALLAS</a></div>

<header class="a-hero"><div class="wrap">
  <div class="a-kicker">/ FOUNDING PARTNERS — 3 SPOTS</div>
  <h1>Own the top of Dallas for 90 days. Free.</h1>
  <p class="a-sub">We're giving the pinned #1 slot to three DFW venues at no charge.
  No card, no contract, no auto-renew. We're new, and we'd rather prove this
  works than talk you into it.</p>
  {form}
</div></header>

<section class="a-sec"><div class="wrap">
  <div class="a-lead">/ WHAT YOU GET</div>
  <h2>The gold-badge slot, on the nights you pick.</h2>
  <div class="a-cols">
    <div><h3>Pinned to the top</h3><p>Your card sits above every other listing
    for that day, full-width, with an animated emerald edge and a gold
    SPONSORED badge. It's the first thing anyone browsing that date sees.</p></div>
    <div><h3>Only the days you want</h3><p>Run it every day, or only Fridays and
    Saturdays, or only the week of your event. You're not buying a banner that
    shows up whenever — you're buying the nights that matter to you.</p></div>
    <div><h3>Your link, your copy</h3><p>Headline, description, neighborhood,
    times and a direct link to your own ticketing. We don't put an aggregator
    in between you and your customer.</p></div>
  </div>

  <div class="demo">
    <div class="demo-label">ACTUAL RENDER — THIS IS THE REAL COMPONENT</div>
    <article class="card sponsored" style="cursor:default">
      <div class="card-toprow">
        <span class="idx">(01)</span>
        <span class="tag">/ LIVE MUSIC</span>
        <span class="spon">★ SPONSORED</span>
      </div>
      <div class="card-mid"><div class="card-txt">
        <h3>Your headline goes right here</h3>
        <div class="meta">/ FRI 07 AUG · 8:00 PM</div>
        <div class="meta">/ DEEP ELLUM, DALLAS</div>
      </div></div>
      <p class="desc">Two sentences about the night, in your words. Doors, the
      lineup, the thing that makes someone pick you over the other forty things
      happening in DFW that evening.</p>
      <div class="card-foot"><span class="badge">YOUR VENUE</span></div>
    </article>
  </div>
</div></section>

<section class="a-sec"><div class="wrap">
  <div class="a-lead">/ THE INVENTORY</div>
  <h2>What's already on the page.</h2>
  <p class="a-sub" style="margin-top:12px">These are listings, not audience
  numbers — see below. Recounted from the live feed every night.</p>
  <div class="a-stats">
    <div><div class="n">{len(events)}</div><div class="l">EVENTS LISTED</div></div>
    <div><div class="n">{len(venues)}</div><div class="l">DFW VENUES</div></div>
    <div><div class="n">{span}</div><div class="l">DAYS AHEAD</div></div>
    <div><div class="n">{hubs}</div><div class="l">SEO LANDING PAGES</div></div>
    <div><div class="n">24h</div><div class="l">REFRESH CYCLE</div></div>
  </div>
</div></section>

<section class="a-sec"><div class="wrap">
  <div class="a-lead">/ STRAIGHT TALK</div>
  <h2>Why it's free, honestly.</h2>
  <div class="a-note">
  <p style="margin-top:0">We're not going to quote you a traffic number. The site
  launched in July 2026 and we only turned on analytics a few days later, so we
  don't have meaningful audience data yet — and making one up for a page aimed
  at people who run real businesses isn't something we're willing to do.</p>
  <p>That's the entire reason the first three spots cost nothing. You'd be
  taking a chance on reach we can't prove, so you shouldn't be paying for it.
  If it sends you people, you'll know, and we can talk about what's fair after
  that. If it doesn't, you've lost an email.</p>
  <p style="margin-bottom:0">What we can tell you is exactly what's above:
  what's listed, how often it updates, and where it shows up in search.</p>
  </div>
</div></section>

<section class="a-sec"><div class="wrap">
  <div class="a-lead">/ WHAT WE ASK</div>
  <h2>Three things, none of them money.</h2>
  <ul class="a-ask">
    <li>Your name and logo on this page as a founding partner, so the venues
    after you can see someone went first.</li>
    <li>A sentence at the end of the 90 days about how it actually went —
    good or bad. We'll publish it either way.</li>
    <li>Tell us when something's wrong. Wrong showtime, dead link, a night we
    missed. You know your calendar better than any feed does.</li>
  </ul>
  <p class="a-sub">After 90 days there's no obligation and nothing auto-charges.
  If you want to keep the spot, founding partners keep founding rates.</p>
  {form}
</div></section>

<footer class="a-foot"><div class="wrap">
  <a href="/">← BACK TO THE RADAR</a> &nbsp;·&nbsp;
  <a href="{mailto}">{email}</a>
</div></footer>

</body></html>"""

    d = ROOT / "advertise"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(html)
    return f"{SITE}/advertise/"


def write_hubs(events):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pages = []

    def emit(path, title, desc, evs, app_link, heading, note):
        d = ROOT / path
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(_hub_html(title, desc, f"{SITE}/{path}/", evs, app_link, heading, note))
        pages.append(f"{SITE}/{path}/")

    tonight = [e for e in events if e["date"] == today]
    emit("tonight", "Things to Do in Dallas–Fort Worth Tonight | Lets Do It Dallas",
         "Tonight's live events, music, pop-ups, and nightlife across DFW. Real-time event radar on Lets Do It Dallas.",
         tonight, "/?view=tonight", "TONIGHT IN DALLAS–FORT WORTH", "TIME HUB")

    now = datetime.now(timezone.utc)
    sat = now + timedelta(days=(5 - now.weekday()) % 7)
    wknd = {sat.strftime("%Y-%m-%d"), (sat + timedelta(days=1)).strftime("%Y-%m-%d")}
    emit("this-weekend", "Things to Do in DFW This Weekend | Lets Do It Dallas",
         "This weekend's events, festivals, markets and shows across Dallas–Fort Worth.",
         [e for e in events if e["date"] in wknd], "/?view=weekend", "THIS WEEKEND IN DFW", "TIME HUB")

    emit("free-events", "Free Things to Do in Dallas–Fort Worth | Lets Do It Dallas",
         "Free events, free museums, free live music and markets across DFW.",
         [e for e in events if e.get("cost") == 0], "/?free=1", "FREE IN DFW", "COST HUB")

    for slug, label, _match in DISTRICTS:
        evs = [e for e in events if _slugify_matches(e["area"]) == slug]
        emit(f"district/{slug}", f"Things to Do in {label} | Lets Do It Dallas",
             f"Live events, music, and nightlife in {label} — part of the Lets Do It Dallas real-time event radar.",
             evs, f"/?district={slug}", label.upper(), "DISTRICT HUB")

    # After the hubs exist, so its "SEO landing pages" count is accurate.
    pages.append(write_advertise())

    sitemap = "\n".join(
        f"<url><loc>{u}</loc><lastmod>{today}</lastmod></url>"
        for u in [SITE + "/"] + pages)
    (ROOT / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{sitemap}\n</urlset>\n')
    (ROOT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap.xml\n")
    print(f"wrote {len(pages)} hub pages + sitemap.xml + robots.txt")


# ---------------------------------------------------------------------- dedupe
def _norm_name(name: str) -> str:
    """Normalized title used for de-duplication. Mirrored by _dedupeKey in
    js/sources.js so both layers collapse the same near-duplicates."""
    n = (name or "").lower()
    n = re.sub(r"\(.*?\)", " ", n)              # drop parentheticals like (18+)
    n = n.replace("&", " and ")
    # strip generic show words so "X Tour" and "X" collapse together
    n = re.sub(r"\b(tickets?|tour|live|concert|presents?|featuring|feat|"
               r"with special guests?)\b", " ", n)
    n = re.sub(r"[^a-z0-9]+", " ", n).strip()
    return re.sub(r"^the ", "", n)              # "The Randy Rogers Band" == "Randy Rogers Band"


_STOP = {"the", "a", "an", "at", "vs", "v", "and", "of", "in", "on", "for",
         "with", "not", "featuring", "night", "show", "series"}
_VAGUE_TIMES = ("", "see details", "all day", "doors — see listing")
# Sources disagree about whether a listing's time is doors or downbeat, so allow
# an hour and a half of slack. Wide enough to merge a 7:00/8:00 PM disagreement,
# narrow enough to keep a 2:00 PM matinee separate from the 8:00 PM performance
# (and an early comedy set separate from the late one).
_TIME_SLACK_MIN = 90


def _times_compatible(a, b) -> bool:
    """Could these two start times be the same performance? An unknown time
    can't prove separation, so it counts as compatible."""
    if a is None or b is None:
        return True
    return abs(a - b) <= _TIME_SLACK_MIN


ALIAS_FILE = ROOT / "venue-aliases.json"


def _load_venue_aliases() -> dict:
    """variant name -> canonical name, both reduced to a comparison key.
    Shared with js/sources.js so the browser collapses the same pairs."""
    try:
        raw = json.loads(ALIAS_FILE.read_text()).get("aliases", {})
    except (OSError, ValueError):
        return {}
    out = {}
    for canonical, variants in raw.items():
        for v in list(variants) + [canonical]:
            out[_venue_key(v)] = canonical
    return out


def _venue_key(name: str) -> str:
    """Punctuation/suffix-insensitive form used to look an alias up."""
    v = (name or "").split(",")[0].strip().lower()
    v = re.sub(r"\s+-\s+[^-]+$", "", v)        # trailing city: "… - Sanger"
    v = v.replace("&", " and ").replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", " ", v).strip()


_VENUE_ALIASES = _load_venue_aliases()


def _venue_tokens(area: str) -> set:
    """Comparable token set for the venue half of an `area` string. Sources
    punctuate and suffix venues differently — "Cooper's Bar & Grill - Arlington"
    and "Coopers Bar and Grill" must land on the same tokens.

    A renamed venue shares no tokens at all with its old name, which no amount
    of normalization fixes, so variants are first rewritten to a canonical name
    via venue-aliases.json (see that file for why this is data and not a looser
    comparison)."""
    key = _venue_key(area)
    canonical = _VENUE_ALIASES.get(key)
    v = _venue_key(canonical) if canonical else key
    return {t for t in v.split() if t not in _STOP and len(t) > 1}


def _time_minutes(t: str):
    if (t or "").strip().lower() in _VAGUE_TIMES:
        return None
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*(AM|PM)", t or "", re.I)
    if not m:
        return None
    h = int(m.group(1)) % 12 + (12 if m.group(3).upper() == "PM" else 0)
    return h * 60 + int(m.group(2))


def _same_event(tokens, venue, mins, p_tokens, p_venue, p_mins) -> bool:
    """Two rows on the same date describe one event when the titles share a
    meaningful word, the venues agree, and the times are close.

    Every clause matters. Titles alone miss "White Sox at Rangers" vs "Texas
    Rangers vs. Chicago White Sox". Venue+time alone wrongly merged Jackie
    Fabulous into Cipha Sounds at one comedy club, and Ultimate Bullfighters
    into the Stockyards Championship Rodeo."""
    if not (tokens & p_tokens):            # unrelated titles => different events
        return False
    if not venue or not p_venue:           # unknown venue => don't guess
        return False
    # one venue string may be a fuller form of the other ("Roanoke Live" vs
    # "Roanoke ChopShop Live"), so accept either being a subset
    if not (venue <= p_venue or p_venue <= venue):
        return False
    return _times_compatible(mins, p_mins)


def dedupe(rows):
    """Collapse the same event arriving from multiple sources. Keeps the FIRST
    occurrence, so callers should order rows richest-source-first.

    Two passes, because sources disagree about titles:
      1. exact normalized title + date — cheap, catches most matches
      2. fuzzy same-date comparison via _same_event() — catches the same show
         listed under different titles, venue spellings, or start times.
    See _same_event() for why each of its clauses is required."""
    by_date, unique = {}, []
    for r in rows:
        if not r.get("date") or not r.get("name"):
            continue
        norm = _norm_name(r["name"])
        tokens = {t for t in norm.split() if t not in _STOP and len(t) > 1}
        venue = _venue_tokens(r.get("area"))
        mins = _time_minutes(r.get("time"))
        dup = False
        for p_norm, p_tokens, p_venue, p_mins in by_date.get(r["date"], []):
            # identical title on the same day — a duplicate unless the times are
            # far enough apart to be genuinely separate performances (a 2:00 PM
            # matinee and an 8:00 PM show are two different tickets)
            if norm == p_norm and _times_compatible(mins, p_mins):
                dup = True
                break
            if _same_event(tokens, venue, mins, p_tokens, p_venue, p_mins):
                dup = True
                break
        if dup:
            continue
        by_date.setdefault(r["date"], []).append((norm, tokens, venue, mins))
        unique.append(r)
    unique.sort(key=lambda r: (r["date"], r["name"]))
    return unique


# ------------------------------------------------------------------------ main
def main():
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=DAYS_AHEAD)

    # Order matters: the dedupe below keeps the FIRST occurrence, so richer
    # sources go first. Seated is last — it carries no price, so it should only
    # contribute shows the ticketing APIs don't already have.
    # Eventbrite is deliberately absent: it answers 405 to datacenter IPs, so it
    # is refreshed by hand via scripts/fetch_eventbrite_local.py into
    # eventbrite.json, which the site loads as a separate source.
    rows = (fetch_ticketmaster(start, end) + fetch_seatgeek(start, end)
            + fetch_ics_feeds(start, end) + fetch_prekindle(start, end)
            + fetch_seated(start, end))

    unique = dedupe(rows)

    OUT_FILE.write_text(json.dumps(unique, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(unique)} events -> {OUT_FILE.name}")

    PRESS_FILE.write_text(json.dumps(fetch_press(), indent=1, ensure_ascii=False) + "\n")
    write_hubs(unique)


if __name__ == "__main__":
    main()
