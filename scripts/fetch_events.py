#!/usr/bin/env python3
"""Nightly event aggregator for RJ Does Dallas.

Runs in GitHub Actions (see .github/workflows/fetch-events.yml) and writes
live-events.json in the repo root, which the site loads alongside events.json.

Sources (all optional — missing keys/feeds are skipped gracefully):
  * Ticketmaster Discovery API   (env TICKETMASTER_KEY)
  * SeatGeek API                 (env SEATGEEK_CLIENT_ID)
  * Any iCal/ICS feeds listed in scripts/feeds.json
  * Prekindle venue pages, Seated artist tours (scripts/feeds.json)
  * Dallasites101 — /calendar/ listing page + per-event JSON-LD (see
    fetch_dallasites101 docstring below). Small yield (~8 events), no key.
  * Do214 — parser written, DISABLED: it 403s all non-browser UAs, so running
    it would mean spoofing one. See `_disabled_because` in feeds.json.

Sources evaluated and rejected (2026-07-21, so nobody re-litigates them):
  * Bandsintown — 403 "explicit deny"; partner/affiliate app_id required.
  * PredictHQ — paid commercial product; loader exists in js/sources.js but is
    unused, and there is no free tier worth wiring.
  * fortworthtexas.gov — edge-blocked (403 to every UA, browser included).
  * fortworth.com (Simpleview CVB), dfwi.org — HTML only, no ICS/RSS/JSON-LD.
  * fortworth.culturemap.com (CultureMap Fort Worth) — not WordPress, no RSS
    at any common path, and /events/ is a client-rendered app shell with no
    server-side event data and no per-event JSON-LD (the only JSON-LD on that
    page is a contentless CollectionPage stub). Same failure shape as the
    other Simpleview/CVB-style sites above. Re-probe only if the site visibly
    changes platforms.
  * TPWD — has an RSS feed but its items carry NO dates, so it cannot drive a
    date-picking site. Per-park RSS paths 404.
  * Patch DFW — no RSS endpoint (404).

Output rows use the same schema as events.json:
  {name, category, area, date, time, cost, description, url}

Stdlib only — no pip installs needed.
"""

import html as _html
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


# Many calendars (anything running The Events Calendar plugin) ship a
# CATEGORIES line per event. Without this every ICS row landed in the catch-all
# "festival" bucket, which made the vibe filters useless for those venues.
# Matched as substrings, longest first, so "Live Music" wins before "Music".
ICS_CATEGORY = [
    ("live music", "music"), ("concert", "music"), ("music", "music"),
    ("nightlife", "nightlife"), ("bar", "nightlife"),
    ("comedy", "arts"), ("theatre", "arts"), ("theater", "arts"),
    ("performing arts", "arts"), ("galler", "arts"), ("museum", "arts"),
    ("art", "arts"),
    ("food", "food"), ("dining", "food"), ("restaurant", "food"),
    ("market", "market"), ("farmers", "market"),
    ("sports", "sports"), ("rodeo", "sports"), ("equestrian", "sports"),
    ("outdoor", "outdoors"), ("hike", "outdoors"), ("nature", "outdoors"),
    ("park", "outdoors"),
    ("family", "family"), ("kids", "family"), ("children", "family"),
    ("festival", "festival"),
]


def ics_category(raw: str, default: str) -> str:
    """Map an ICS CATEGORIES value onto our vocabulary. An event can carry
    several ('Live Music,Special Event'); first recognised one wins. Falls back
    to the feed's configured category so a hand-tuned feed keeps its label."""
    text = (raw or "").lower()
    if not text:
        return default
    for needle, cat in ICS_CATEGORY:
        if needle in text:
            return cat
    return default


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
                        ev.get("SUMMARY"),
                        ics_category(ev.get("CATEGORIES"),
                                     feed.get("category", "festival")),
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
                if key in ("SUMMARY", "DTSTART", "LOCATION", "DESCRIPTION",
                           "URL", "CATEGORIES"):
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


# --------------------------------------------------------------------- Do214
# Do214 (DoStuff Media) publishes the DFW city calendar as JSON at the same
# paths as the HTML site, with `.json` appended:
#   https://do214.com/events/<YYYY>/<M>/<D>.json?page=N
# It is versioned (`api_version` in the payload) and key-free. robots.txt
# disallows /assets, /locales, /features, /search, /latest and *view=map — the
# event paths we walk are NOT disallowed.
#
# DISABLED BY DEFAULT — see `_disabled_because` in feeds.json. The endpoint
# 403s every non-browser User-Agent and only answers 'Mozilla/5.0', so running
# it means spoofing a browser to evade a bot block. This parser is finished and
# tested against their live payload; turn it on if Do214 grants access. Do not
# turn it on by faking the UA.
#
# Two deliberate calls here:
#  * We link to the Do214 event page, NOT the payload's `buy_url`. Those are
#    Do214's own affiliate links (etix.prf.hn/click/camref:.../pubref:dostuff),
#    so forwarding them would route our visitors through their commission —
#    and linking back is the fair trade for using their feed.
#  * Results are sorted by popularity, so `max_pages` is a quality knob, not
#    just a rate limit: page 1 is the best of the night, page 8 is the dregs.
DO214_BASE = "https://do214.com/events"
DO214_SITE = "https://do214.com"

DO214_CATEGORY = {
    "music/nightlife": "music",
    "arts/culture": "arts",
    "comedy": "arts",
    "food/drinks": "food",
    "sports/recreation": "sports",
    "sports": "sports",
    "arts & family": "family",
    "family": "family",
    "community": "festival",
}


def do214_category(cat: str) -> str:
    """Map Do214's category to ours. Some rows arrive tagged
    'Arts/Culture (Hidden)' — an internal visibility marker, not a category —
    so strip any trailing parenthetical before matching."""
    c = re.sub(r"\s*\(.*?\)\s*$", "", (cat or "")).strip().lower()
    return DO214_CATEGORY.get(c, "festival")


def _do214_price(ev: dict):
    """ticket_info is free text: '$20-$24, All Ages', '$245', 'All Ages'.
    Pull the money out and ignore the age rating."""
    if ev.get("is_free"):
        return "Free"
    m = re.search(r"\$\d[\d,]*(?:\.\d{2})?(?:\s*[-–]\s*\$?\d[\d,]*(?:\.\d{2})?)?",
                  ev.get("ticket_info") or "")
    return m.group(0).replace(" ", "") if m else None


def _do214_image(ev: dict, photos_base: str):
    img = ev.get("imagery") or {}
    aws = img.get("aws") or {}
    for key in ("poster_w_800", "poster_w_400", "cover_image_w_1200_h_450"):
        if aws.get(key):
            return aws[key]
    photo = img.get("photo") or img.get("poster")
    return f"{photos_base}{photo}" if photo and photos_base else None


def fetch_do214(start, end):
    """Walk Do214's per-day JSON. Keyed by DATE (unlike Seated, which is keyed
    by artist), so we request each day in the window rather than paging a
    single firehose."""
    if not FEEDS_FILE.exists():
        return []
    try:
        cfg = json.loads(FEEDS_FILE.read_text()).get("do214") or {}
    except Exception:  # noqa: BLE001
        return []
    if not cfg.get("enabled"):
        return []

    days = min(int(cfg.get("days", 14)), DAYS_AHEAD)
    max_pages = max(1, int(cfg.get("max_pages", 3)))
    delay = float(cfg.get("delay_ms", 400)) / 1000.0

    out, seen_ids = [], set()
    for offset in range(days):
        day = start + timedelta(days=offset)
        path = f"{DO214_BASE}/{day.year}/{day.month}/{day.day}.json"
        pages, page, kept = max_pages, 1, 0
        while page <= pages:
            try:
                data = http_json(f"{path}?page={page}" if page > 1 else path)
            except Exception as e:  # noqa: BLE001
                print(f"do214 failed ({day:%Y-%m-%d} p{page}): {e}", file=sys.stderr)
                break
            # Page 1 tells us how deep the day actually goes; never exceed
            # max_pages even on a busy Saturday (8+ pages).
            total = ((data.get("paging") or {}).get("total_pages")) or 1
            pages = min(int(total), max_pages)
            photos_base = data.get("photos_base") or ""

            for ev in data.get("events") or []:
                eid = ev.get("id")
                if eid in seen_ids:      # an event can span days and repeat
                    continue
                date = ev.get("begin_date") or ""
                if not date or ev.get("past"):
                    continue
                venue = ev.get("venue") or {}
                if not is_dfw_city(venue.get("city"), venue.get("state")):
                    continue
                seen_ids.add(eid)
                begin = ev.get("tz_adjusted_begin_date") or ev.get("begin_time") or ""
                m = re.search(r"T(\d{2}):(\d{2})", begin)
                out.append(row(
                    ev.get("title"), do214_category(ev.get("category")),
                    ", ".join(x for x in [venue.get("title"), venue.get("city")] if x),
                    date,
                    pretty_time(f"{m.group(1)}:{m.group(2)}") if m else "See details",
                    _do214_price(ev),
                    ev.get("excerpt") or ev.get("description"),
                    f"{DO214_SITE}{ev.get('permalink')}" if ev.get("permalink") else DO214_SITE,
                    _do214_image(ev, photos_base),
                ))
                kept += 1
            page += 1
            if delay:
                time.sleep(delay)
        if kept:
            print(f"do214 ({day:%Y-%m-%d}): {kept} events")
    print(f"do214: {len(out)} events total")
    return out


# ---------------------------------------------------------------- Dallasites101
# Dallasites101 is a Dallas lifestyle blog that also runs its own ticketed
# social meetups (pool parties, silent discos, "serve & social" volunteer
# nights). Its /calendar/ page links to individual /event/<slug>/<id>/ pages,
# each carrying a real schema.org Event JSON-LD block (name/date/venue/address)
# — same pattern as Prekindle. Two things the JSON-LD does NOT carry, both
# recovered from the surrounding page source instead:
#   * time-of-day — a `var time = "8:00 PM to 11:00 PM"` string
#   * a real outbound ticket link + price — some of these events are actually
#     sold through Eventbrite under Dallasites101's own affiliate link, buried
#     in an embedded `{"name":"Tickets URL","value":"...","admission":"$25..."}`
#     blob. Falls back to the event's own page when absent.
# Small yield (checked 2026-07-21: 8 events on /calendar/, a strict superset of
# its /calendar/101media-events/ sub-page) but genuine, and not carried by any
# other source we pull. robots.txt allows / with `Crawl-delay: 2`, honored
# below with a sleep between each per-event fetch (there is no bulk/API route —
# the listing page itself carries no JSON-LD, only links to follow).
DALLASITES101_BASE = "https://www.dallasites101.com"
DALLASITES101_CALENDAR = f"{DALLASITES101_BASE}/calendar/"


def fetch_dallasites101(start, end):
    lo, hi = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    try:
        listing = http_text(DALLASITES101_CALENDAR)
    except Exception as e:  # noqa: BLE001
        print(f"dallasites101: calendar page failed: {e}", file=sys.stderr)
        return []

    links = sorted(set(re.findall(r'href="(/event/[^"]+/\d+/)"', listing)))
    out = []
    for href in links:
        url = DALLASITES101_BASE + href
        try:
            html = http_text(url)
        except Exception as e:  # noqa: BLE001
            print(f"dallasites101 ({href}): fetch failed: {e}", file=sys.stderr)
            continue
        finally:
            time.sleep(2.0)     # robots.txt: Crawl-delay: 2

        m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
        if not m:
            continue
        try:
            ev = json.loads(m.group(1))
        except (ValueError, TypeError):
            continue
        if ev.get("@type") != "Event" or not ev.get("name"):
            continue

        date = str(ev.get("startDate") or "")[:10]
        if not (lo <= date <= hi):
            continue

        loc = ev.get("location") or {}
        addr = loc.get("address") or {}
        city, region = addr.get("addressLocality"), addr.get("addressRegion")
        if not is_dfw_city(city, region):
            continue
        area = ", ".join(x for x in [loc.get("name"), city] if x) or "Dallas"

        tm = re.search(r'var\s+time\s*=\s*"([^"]*)"', html)
        # the site writes ranges as "8:00 PM to 11:00 PM"; the site's own
        # timeRange() parser (js/app.js) splits on an en/em dash, not "to"
        time_str = tm.group(1).replace(" to ", "–") if tm else "See details"

        ticket_m = re.search(r'"name":"Tickets URL","value":"([^"]*)"', html)
        ticket_url = ticket_m.group(1) if ticket_m else (ev.get("url") or url)

        cost = None
        adm_m = re.search(r'"admission":"([^"]*)"', html)
        if adm_m:
            price_m = re.search(r"\$(\d+(?:\.\d+)?)", adm_m.group(1))
            if price_m:
                cost = float(price_m.group(1))          # lowest listed tier
            elif re.search(r"\bfree\b", adm_m.group(1), re.I):
                cost = 0

        # reuses Eventbrite's generic keyword pass (no Eventbrite-specific tags
        # involved when `tags` is None) rather than duplicating EB_KEYWORDS
        out.append(row(
            ev["name"], eb_category(None, ev["name"] + " " + (ev.get("description") or "")),
            area, date, time_str, cost, ev.get("description"), ticket_url, ev.get("image"),
        ))
    print(f"dallasites101: {len(out)} events")
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


_ADDR_NOISE = re.compile(r"^(united states|usa|us|tx|texas)$", re.I)
_POSTAL = re.compile(r"^\d{5}(-\d{4})?$")


def _split_area(area: str):
    """Split a free-text `area` into (venue, street, city) for schema.org.

    `area` is one field carrying several shapes: a bare district
    ("Lower Greenville"), Ticketmaster's "Venue, City", or an ICS feed's full
    postal address ("Tulips FTW, 112 Saint Louis Avenue, Fort Worth, 76104,
    United States"). Feeding the whole string to `addressLocality` — which the
    JSON-LD did — puts a street address in a field Google reads as a city name
    and risks Event rich-result eligibility.

    Any component may be None; callers must omit rather than guess. In
    particular a single-part area has no discoverable city: "Lower Greenville"
    is a Dallas neighborhood, but inferring "Dallas" from that is exactly the
    guess that produces wrong data for the Fort Worth entries.

    Note `row()` truncates `area` to 80 chars, so the tail of a long address
    can arrive already mangled ("..., Fort Worth, 76107, "). Dropping empty and
    noise parts absorbs that.
    """
    def noise(p):
        # row()'s 80-char cap can sever the trailing country mid-word, leaving
        # "Un" — short enough to survive as a locality. Any prefix of "united
        # states" is that artifact; no DFW city collides ("Union" diverges at
        # the fourth character).
        return (_ADDR_NOISE.match(p) or _POSTAL.match(p)
                or (len(p) >= 2 and "united states".startswith(p.lower())))

    parts = [p.strip() for p in (area or "").split(",")]
    parts = [p for p in parts if p and not noise(p)]
    if not parts:
        return None, None, None
    if len(parts) == 1:
        return parts[0], None, None
    venue, rest = parts[0], parts[1:]
    # A street line starts with a house number; everything else is locality.
    street = [p for p in rest if re.match(r"^\d", p)]
    city = [p for p in rest if p not in street]
    return venue, (", ".join(street) or None), (city[-1] if city else None)


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
        venue, street, city = _split_area(e["area"])
        addr = {"@type": "PostalAddress", "addressRegion": "TX"}
        if street:
            addr["streetAddress"] = street
        if city:
            addr["addressLocality"] = city
        item = {"@type": "Event", "name": e["name"], "startDate": start, "endDate": end,
                "eventStatus": "https://schema.org/EventScheduled",
                "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
                "location": {"@type": "Place", "name": venue or e["area"], "address": addr},
                "image": [e.get("image") or f"{SITE}/og-image.png"],
                "url": url,
                "performer": {"@type": "PerformingGroup", "name": e["name"]}}
        if venue:
            # No url: `url` is the ticketing listing, not the venue's own site.
            item["organizer"] = {"@type": "Organization", "name": venue}
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


def _display_area(area: str) -> str:
    """Human-readable venue line: "Tulips FTW · Fort Worth".

    Printing the raw `area` leaked full postal addresses into the listing, and
    `row()`'s 80-char cap chopped them mid-field ("..., Fort Worth, 76107, ").
    Reuses the JSON-LD split so the visible text and the structured data name
    the same place; the street number is dropped as noise for a reader.
    """
    venue, _street, city = _split_area(area)
    if venue and city and city.lower() not in venue.lower():
        return f"{venue} · {city}"
    return venue or (area or "")


# Venues carrying at least this many upcoming events get their own page. Below
# it the page is too thin to rank and just dilutes the crawl budget; at 3 the
# current feed yields ~40 pages.
VENUE_MIN_EVENTS = 3

# Touring productions that report themselves as a venue. They travel, so a
# venue page would outlive the run and strand the URL. Substring, lowercased.
_NOT_VENUES = ("universoul circus",)

# slug -> display name, populated by write_venues() and read by _hub_row() so
# listings can link to a venue page. write_venues() must therefore run before
# the hubs are written; write_hubs() enforces that ordering.
_VENUE_PAGES = {}


def _venue_slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s[:60].strip("-")


def _is_real_venue(name: str) -> bool:
    """Reject district labels and touring shows masquerading as venues.

    `area` is one field doing several jobs, so a Ticketmaster row can report
    "Lower Greenville" — a neighbourhood that already has a district hub. Giving
    it a venue page too would put two of our own URLs on one query. The district
    check reads DISTRICTS rather than a hand-list so the two stay consistent.
    """
    n = (name or "").strip().lower()
    if not n or any(bad in n for bad in _NOT_VENUES):
        return False
    return not any(n == m for _slug, _label, match in DISTRICTS for m in match)


def _hub_row(e, omit_venue=None, count=1, last=None):
    """One listing line. Everything from a feed is escaped.

    107 of the current 541 names carry a bare "&" ("County Line Records & WMG
    Vinyl Take-Back Event"), which went into the markup raw; a name with an
    angle bracket would break the page outright. This is the Python-side
    counterpart of the esc()/safeUrl() rule that js/sources.js already follows.

    `omit_venue` drops the venue from the line on that venue's own page, where
    repeating it on all 60 rows is noise and reads as boilerplate to a crawler.
    """
    url = e["url"] if str(e.get("url") or "").startswith(("http://", "https://")) else "#"
    venue, _street, _city = _split_area(e["area"])
    slug = _venue_slug(venue) if venue else None
    if count > 1 and last and last != e["date"]:
        bits = [f'{count} dates', f'{_fmt_day(e["date"])} – {_fmt_day(last)}']
    else:
        bits = [e["date"], _html.escape(e["time"])]
    if slug != omit_venue:
        where = _html.escape(_display_area(e["area"]))
        bits.append(f'<a href="/venue/{slug}/">{where}</a>' if slug in _VENUE_PAGES else where)
    return (f'<li><a href="{_html.escape(url, quote=True)}" rel="noopener">'
            f'<strong>{_html.escape(e["name"])}</strong></a> '
            f'<span>/ {" · ".join(bits)}</span></li>')


def _fmt_day(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%b %-d")
    except ValueError:
        return iso


def _group_repeats(events):
    """Collapse a repeated title into one entry carrying its date range.

    Residencies and tours report one row per performance, so Deep Ellum Art Co
    listed "Drunk Shakespeare" 33 times and was 100% one title; 7 of 38 venue
    pages had a single title over half their rows. That reads as doorway
    content to a crawler and tells a reader nothing 33 times over.

    Yields (event, count, last_date) keeping first-seen order, so the caller
    still has the earliest occurrence to link to. The JSON-LD is deliberately
    left ungrouped: each performance is a real dated Event and Google wants
    them individually.
    """
    order, seen = [], {}
    for e in events:
        key = e["name"].strip().lower()
        if key in seen:
            seen[key][1] += 1
            seen[key][2] = max(seen[key][2], e["date"])
        else:
            seen[key] = [e, 1, e["date"]]
            order.append(key)
    return [tuple(seen[k]) for k in order]


def _hub_html(title, desc, canonical, events, app_link, heading, note):
    rows = "\n".join(_hub_row(e, count=n, last=last)
                     for e, n, last in _group_repeats(events)[:60]) \
        or "<li>Fresh listings load nightly — check the live radar.</li>"
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


def _venue_html(name, city, street, canonical, events):
    """Venue page: the long-tail surface the site otherwise has none of.

    Every listing links straight out to Ticketmaster, so nothing on this domain
    could rank for "<act> <venue>". This page also doubles as outreach — it is
    something to point a venue at that already exists.

    Carries a Place node alongside the event ItemList so the venue reads as an
    entity rather than just a list.
    """
    place = {"@context": "https://schema.org", "@type": "Place", "name": name,
             "url": canonical,
             "address": {"@type": "PostalAddress", "addressRegion": "TX",
                         **({"streetAddress": street} if street else {}),
                         **({"addressLocality": city} if city else {})}}
    esc_name = _html.escape(name)
    # The <h1> already carries the name; this line adds only what it doesn't.
    where = " · ".join(([_html.escape(city)] if city else []) + [f"{len(events)} UPCOMING"])
    title = f"{esc_name} Events — Upcoming Shows{f' in {_html.escape(city)}' if city else ''} | Lets Do It Dallas"
    desc = (f"Upcoming events at {esc_name}"
            f"{f' in {_html.escape(city)}' if city else ''} — dates, times and tickets on the "
            "Lets Do It Dallas event radar.")
    rows = "\n".join(_hub_row(e, omit_venue=_venue_slug(name), count=n, last=last)
                     for e, n, last in _group_repeats(events)[:60])
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
<script type="application/ld+json">{json.dumps(place)}</script>
<style>
body{{background:#08090B;color:#8A909E;font:15px/1.6 Inter,-apple-system,sans-serif;margin:0;padding:40px 6vw}}
h1{{color:#fff;font-size:2rem;letter-spacing:.01em}}a{{color:#00FF87;text-decoration:none}}
.k{{font-family:ui-monospace,monospace;font-size:11px;letter-spacing:.12em;color:#00FF87}}
ul{{list-style:none;padding:0}}li{{padding:12px 0;border-bottom:1px solid #191C22}}
li span{{display:block;font-family:ui-monospace,monospace;font-size:11px;color:#8A909E}}
.cta{{display:inline-block;margin:18px 0;border:1px solid #0E3A2F;padding:12px 18px}}
.foot{{margin-top:28px;font-size:13px}}
</style>{_analytics_snippet()}</head><body>
<p class="k">/ LETS DO IT DALLAS — VENUE</p>
<h1>{esc_name}</h1>
<p class="k">{where}</p>
<a class="cta" href="/?q={urllib.parse.quote(name)}">( OPEN THE LIVE RADAR ↗ )</a>
<ul>{rows}</ul>
<p class="foot">Run {esc_name}? <a href="/submit/">List your shows free</a> — or
<a href="/advertise/">see partner options</a>.</p>
<p><a href="/">← letsdoitdallas.com</a></p>
</body></html>"""


def write_venues(events):
    """Emit /venue/<slug>/ for every venue clearing VENUE_MIN_EVENTS.

    Returns the canonical URLs for the sitemap, and fills _VENUE_PAGES so the
    hub listings can link here.
    """
    by_venue = {}
    for e in events:
        venue, street, city = _split_area(e.get("area", ""))
        if not venue or not _is_real_venue(venue):
            continue
        slug = _venue_slug(venue)
        if not slug:
            continue
        # First spelling seen wins the display name; a later row may be the
        # truncated one. Keep the longest street/city seen for the same reason.
        g = by_venue.setdefault(slug, {"name": venue, "street": None, "city": None, "events": []})
        g["events"].append(e)
        if street and not g["street"]:
            g["street"] = street
        if city and not g["city"]:
            g["city"] = city
        if len(venue) > len(g["name"]):
            g["name"] = venue

    urls = []
    _VENUE_PAGES.clear()
    for slug, g in sorted(by_venue.items()):
        if len(g["events"]) < VENUE_MIN_EVENTS:
            continue
        evs = sorted(g["events"], key=lambda e: (e["date"], e.get("time") or ""))
        canonical = f"{SITE}/venue/{slug}/"
        d = ROOT / "venue" / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(
            _venue_html(g["name"], g["city"], g["street"], canonical, evs))
        _VENUE_PAGES[slug] = g["name"]
        urls.append(canonical)
    print(f"wrote {len(urls)} venue pages (of {len(by_venue)} venues seen)")
    return urls


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
.p-wall{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);
  border:1px solid var(--line);margin-top:26px}
.p-cell{background:var(--bg);min-height:132px;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:8px;padding:24px 16px;text-align:center}
.p-cell.filled .p-word{font-family:var(--display);font-weight:800;font-size:19px;
  color:var(--white);letter-spacing:-.01em;line-height:1.2}
.p-cell.filled img{max-width:160px;max-height:52px;object-fit:contain}
.p-cell.filled a{display:block}
.p-area{font-family:var(--mono);font-size:9.5px;letter-spacing:.12em;color:#7A8090;margin-top:8px}
/* Open seats are the offer, not a gap — dashed rule reads as reserved. */
.p-cell.open{border:1px dashed rgba(255,207,92,.22);margin:-1px;background:var(--bg)}
.p-num{font-family:var(--mono);font-size:10px;letter-spacing:.14em;color:#3A3F4B}
.p-open{font-family:var(--mono);font-size:11px;letter-spacing:.16em;color:var(--gold)}
@media(max-width:760px){.p-wall{grid-template-columns:1fr}}
.p-quote{margin:26px 0 0;padding:20px 0 0;border-top:1px solid var(--line);max-width:64ch}
.p-quote blockquote{margin:0;font-size:16px;color:#C7CCD6;line-height:1.6}
.p-quote figcaption{font-family:var(--mono);font-size:10.5px;letter-spacing:.12em;
  color:#7A8090;margin-top:12px}
.demo{border:1px solid var(--line);margin-top:24px}
.demo-label{font-family:var(--mono);font-size:9.5px;letter-spacing:.14em;color:#767D8C;
  padding:10px 14px;border-bottom:1px solid var(--line)}
"""


def _load_partners():
    """Read partners.json -> (seats, [partner, ...]). Missing/broken file is
    treated as "no partners yet" rather than an error: a malformed edit should
    degrade the section to its empty state, not take the sales page down."""
    try:
        d = json.loads((ROOT / "partners.json").read_text())
    except (ValueError, OSError):
        return 3, [], "", "a year"
    partners = [p for p in d.get("partners", []) if p.get("name")]
    return (int(d.get("seats", 3) or 3), partners,
            (d.get("founding_rate") or "").strip(),
            (d.get("founding_rate_lock") or "a year").strip())


def _partners_section(seats, partners):
    """The founding-partner wall.

    /advertise/ promises each partner a spot on this page, so the section has to
    exist before the first venue says yes — otherwise the offer reads hollow at
    exactly the moment it needs to land. With nobody in it, it renders as three
    deliberately open slots rather than a blank gap: an empty seat is the offer,
    not a missing feature.
    """
    cells = []
    for p in partners:
        name = _html.escape(p.get("name", ""))
        area = _html.escape(p.get("area", ""))
        url = _html.escape(safe_http(p.get("url", "")))
        logo = p.get("logo", "")
        if logo:
            mark = '<img src="%s" alt="%s" loading="lazy"/>' % (_html.escape(logo), name)
        else:
            mark = '<span class="p-word">%s</span>' % name
        inner = mark + ('<div class="p-area">%s</div>' % area if area else "")
        if url:
            inner = '<a href="%s" target="_blank" rel="noopener">%s</a>' % (url, inner)
        cells.append('<div class="p-cell filled">%s</div>' % inner)

    for i in range(max(0, seats - len(partners))):
        cells.append(
            '<div class="p-cell open"><span class="p-num">%s</span>'
            '<span class="p-open">OPEN</span></div>'
            % str(len(partners) + i + 1).zfill(2))

    quotes = "".join(
        f'<figure class="p-quote"><blockquote>{_html.escape(p["quote"])}</blockquote>'
        f'<figcaption>— {_html.escape(p.get("quote_by") or p["name"])}</figcaption></figure>'
        for p in partners if p.get("quote"))

    if partners:
        lead = ("These venues went first, before there were any numbers to show them.")
    else:
        lead = ("Nobody has taken one yet — the site is new and we're not going to "
                "pretend otherwise. All three seats are open.")

    return f"""
<section class="a-sec"><div class="wrap">
  <div class="a-lead">/ FOUNDING PARTNERS</div>
  <h2>{len(partners)} of {seats} claimed.</h2>
  <p class="a-sub" style="margin-top:12px">{lead}</p>
  <div class="p-wall">{"".join(cells)}</div>
  {quotes}
</div></section>"""


def safe_http(url):
    """Only http(s) URLs reach the page — a partner entry is hand-edited, but a
    javascript: or data: URL in a link is not something to ship either way."""
    u = (url or "").strip()
    return u if u.startswith(("http://", "https://")) else ""


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

    seats, partners, rate, rate_lock = _load_partners()
    remaining = max(0, seats - len(partners))
    _words = {0: "no", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
    word = _words.get(remaining, str(remaining))

    # Hero copy is derived, not hand-written, so the page can never advertise
    # seats that are already gone — the wall and the headline can't disagree.
    if remaining == 0:
        kicker = "/ FOUNDING PARTNERS — ALL CLAIMED"
        hero_h1 = "All three founding spots are taken."
        hero_sub = ("The founding round is closed. If you want the pinned slot when "
                    "one opens up, tell us and you'll get first refusal — and "
                    "founding rates when we do start charging.")
        cta_label = "GET ON THE LIST →"
    else:
        seat_word = "spot" if remaining == 1 else "spots"
        kicker = "/ FOUNDING PARTNERS — %d %s LEFT" % (remaining, seat_word.upper())
        hero_h1 = "Own the top of Dallas for 90 days. Free."
        hero_sub = ("We're giving the pinned #1 slot to %s DFW %s at no charge. "
                    "No card, no contract, no auto-renew. We're new, and we'd rather "
                    "prove this works than talk you into it." % (word, "venue" if remaining == 1 else "venues"))
        cta_label = "CLAIM A FOUNDING SPOT →"

    # Naming the post-trial price beats "founding rates" as a vague promise —
    # unspecified future pricing reads as a setup for a bait-and-switch to
    # anyone who has been sold to before, and it invites the question anyway.
    if rate:
        after_copy = (
            "After 90 days there's no obligation and nothing auto-charges. If you "
            "want to keep the spot it's <strong>%s a month, locked for %s</strong> "
            "— that's the founding rate, and it doesn't move on you later."
            % (_html.escape(rate), _html.escape(rate_lock)))
    else:
        after_copy = ("After 90 days there's no obligation and nothing auto-charges. "
                      "If you want to keep the spot, founding partners keep founding rates.")

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
<button type="submit">{cta_label}</button>
</form>"""
    else:
        form = f'<a class="a-cta" href="{mailto}">{cta_label}</a>'

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
  <div class="a-kicker">{kicker}</div>
  <h1>{hero_h1}</h1>
  <p class="a-sub">{hero_sub}</p>
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
  <p class="a-sub">{after_copy}</p>
  {form}
</div></section>
{_partners_section(seats, partners)}

<footer class="a-foot"><div class="wrap">
  <a href="/">← BACK TO THE RADAR</a> &nbsp;·&nbsp;
  <a href="{mailto}">{email}</a>
</div></footer>

</body></html>"""

    d = ROOT / "advertise"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(html)
    return f"{SITE}/advertise/"


# ------------------------------------------------------------------ /submit/
# THE single definition of the submission form. Both the standalone /submit/
# page (generated here) and the on-site modal in index.html render these exact
# fields, and _check_modal_drift() fails loudly if index.html falls behind —
# two hand-maintained copies of one form is how a venue ends up filling in a
# field we never read.
#
# (name, label, kind, required, placeholder-or-options, half_width)
_SUBMIT_CATEGORIES = [
    ("music", "Live Music"), ("food", "Food & Drink"), ("arts", "Arts & Museums"),
    ("outdoors", "Outdoors & Parks"), ("sports", "Sports"), ("family", "Family & Kids"),
    ("market", "Markets"), ("nightlife", "Nightlife"), ("festival", "Festivals"),
]
_SUBMIT_RECUR = [
    ("once", "One-off"), ("weekly", "Every week"), ("monthly", "Every month"),
]
SUBMIT_FIELDS = [
    ("name", "EVENT NAME", "text", True, "Thursday Night Residency", False),
    ("venue", "VENUE", "text", True, "The Free Man", True),
    ("area", "NEIGHBORHOOD / CITY", "text", True, "Deep Ellum, Dallas", True),
    ("date", "DATE", "date", True, "", True),
    ("time", "START TIME", "text", False, "7:00 PM", True),
    ("recurring", "REPEATS", "select", False, _SUBMIT_RECUR, True),
    ("cost", "COST (USD, 0 = FREE)", "number", False, "0", True),
    ("category", "CATEGORY", "select", False, _SUBMIT_CATEGORIES, False),
    ("url", "LINK (TICKETS OR INFO)", "url", False, "https://", False),
    ("description", "SHORT DESCRIPTION", "textarea", False,
     "One or two lines — what makes it worth leaving the house for?", False),
    ("contact_name", "YOUR NAME", "text", True, "", True),
    ("contact_email", "YOUR EMAIL", "email", True, "you@venue.com", True),
]
# Bots fill every input they find, including ones humans can't see. A submission
# with this field set is dropped. Named innocuously — "honeypot" in the markup
# is a giveaway to anything that reads the DOM.
SUBMIT_HONEYPOT = "company_website"


def _submit_field_html(field, cls=""):
    name, label, kind, required, extra, _half = field
    req = " required" if required else ""
    lab = f'<label for="f_{name}">{label}{"" if required else " <i>(optional)</i>"}</label>'
    if kind == "select":
        opts = "".join(f'<option value="{v}">{_html.escape(t)}</option>' for v, t in extra)
        return f'{lab}<select id="f_{name}" name="{name}"{req}>{opts}</select>'
    if kind == "textarea":
        return (f'{lab}<textarea id="f_{name}" name="{name}" rows="3" '
                f'placeholder="{_html.escape(extra)}"{req}></textarea>')
    ph = f' placeholder="{_html.escape(extra)}"' if extra else ""
    mn = ' min="0"' if kind == "number" else ""
    cl = f' class="{cls}"' if cls else ""
    return f'{lab}<input id="f_{name}" name="{name}" type="{kind}"{ph}{mn}{req}{cl}/>'


def _check_modal_drift():
    """The modal in index.html is hand-written; this spec is not. Warn when they
    disagree so a field added here doesn't silently exist on only one of the two
    forms. A warning, not an exception — a drifted modal must never be able to
    break the nightly event fetch."""
    try:
        page = (ROOT / "index.html").read_text()
    except OSError:
        return
    modal = page.partition('id="submitModal"')[2].partition("</div>")[0] or page
    missing = [f[0] for f in SUBMIT_FIELDS if f'name="{f[0]}"' not in page]
    if missing:
        print(f"WARNING: index.html submit modal is missing fields {missing} "
              f"— update it to match SUBMIT_FIELDS in fetch_events.py",
              file=sys.stderr)
    if SUBMIT_HONEYPOT not in modal and SUBMIT_HONEYPOT not in page:
        print("WARNING: index.html submit modal has no honeypot field",
              file=sys.stderr)


_SUBMIT_CSS = """
.wrap{max-width:1100px;margin:0 auto;padding:0 var(--pad)}
.a-nav{border-bottom:1px solid var(--line);padding:14px var(--pad);
  font-family:var(--mono);font-size:11px;letter-spacing:.12em}
.a-hero{padding:clamp(40px,8vw,90px) 0 clamp(30px,5vw,60px);border-bottom:1px solid var(--line)}
.a-kicker{font-family:var(--mono);font-size:11px;letter-spacing:.16em;color:var(--em);margin-bottom:18px}
.a-hero h1{font-size:clamp(32px,6vw,60px);line-height:1.04;letter-spacing:-.01em;max-width:15ch}
.a-sub{font-size:clamp(15px,2vw,18px);max-width:58ch;margin-top:20px;color:#9AA1B0}
.a-sec{padding:clamp(36px,6vw,68px) 0;border-bottom:1px solid var(--line)}
.a-sec h2{font-size:clamp(20px,3vw,28px);margin-bottom:8px}
.a-lead{font-family:var(--mono);font-size:11px;letter-spacing:.14em;color:var(--em);margin-bottom:16px}
.a-cols{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);
  border:1px solid var(--line);margin-top:26px}
.a-cols>div{background:var(--bg);padding:24px 20px}
.a-cols h3{font-size:15px;margin-bottom:8px}
.a-cols p{margin:0;font-size:13.5px;color:#7A8090}
@media(max-width:760px){.a-cols{grid-template-columns:1fr}}
.a-note{border-left:2px solid var(--jade);padding:4px 0 4px 20px;margin-top:24px;
  max-width:68ch;color:#9AA1B0;font-size:14.5px}
.s-form{max-width:760px;margin-top:26px}
.s-form label{display:block;font-family:var(--mono);font-size:10.5px;letter-spacing:.14em;
  color:var(--silver);margin:20px 0 7px}
.s-form label i{color:var(--dim);font-style:normal;text-transform:none;letter-spacing:.04em}
.s-form input,.s-form select,.s-form textarea{width:100%;background:#0C0E12;color:var(--ink);
  border:1px solid var(--line);padding:12px 13px;font-family:var(--body);font-size:14.5px;
  border-radius:0;-webkit-appearance:none;appearance:none}
.s-form select{background-image:linear-gradient(45deg,transparent 50%,var(--silver) 50%),
  linear-gradient(135deg,var(--silver) 50%,transparent 50%);
  background-position:calc(100% - 19px) 50%,calc(100% - 13px) 50%;
  background-size:6px 6px,6px 6px;background-repeat:no-repeat;padding-right:38px}
.s-form input:focus,.s-form select:focus,.s-form textarea:focus{outline:none;
  border-color:var(--em);box-shadow:0 0 0 1px var(--em)}
.s-form textarea{resize:vertical}
.s-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 22px}
@media(max-width:640px){.s-grid{grid-template-columns:1fr}}
.s-hp{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}
.s-btn{margin-top:28px;background:var(--em);color:#04120C;border:none;padding:15px 26px;
  font-family:var(--mono);font-size:12px;letter-spacing:.14em;cursor:pointer;font-weight:500}
.s-btn:hover{filter:brightness(1.1)}
.s-cta{display:inline-block;margin-top:24px;border:1px solid var(--em);color:var(--em);
  padding:14px 22px;font-family:var(--mono);font-size:12px;letter-spacing:.14em}
.s-msg{margin-top:18px;font-family:var(--mono);font-size:12px;letter-spacing:.1em;color:var(--em)}
.s-steps{margin:22px 0 0;padding:0;list-style:none;max-width:64ch;counter-reset:s}
.s-steps li{padding:13px 0;border-bottom:1px solid var(--line);font-size:14.5px;counter-increment:s}
.s-steps li::before{content:"0" counter(s) " / ";color:var(--em);font-family:var(--mono);font-size:11px}
"""


def write_submit(events):
    """Standalone, linkable event-submission page.

    Exists because the on-site modal has no URL: venue outreach needs something
    to paste into an email, and "add your shows free" is a far easier first
    conversation with a venue than "buy a sponsorship" — it warms the same list
    /advertise/ sells to. Same intake model the big DFW aggregators run on.
    """
    email = _config_value("contactEmail") or "hello@letsdoitdallas.com"
    endpoint = _config_value("submitEventEndpoint")
    _check_modal_drift()

    rows = []
    pending = []
    for f in SUBMIT_FIELDS:
        (pending if f[5] else rows).append(f)
        if f[5] and len(pending) == 2:
            rows.append(tuple(pending)); pending.clear()
        elif not f[5] and pending:                      # a full-width field
            rows.insert(-1, tuple(pending)); pending.clear()
    if pending:
        rows.append(tuple(pending))

    body = []
    for r in rows:
        if isinstance(r, tuple) and r and isinstance(r[0], tuple):
            cells = "".join(f"<div>{_submit_field_html(f)}</div>" for f in r)
            body.append(f'<div class="s-grid">{cells}</div>')
        else:
            body.append(_submit_field_html(r))
    fields_html = "\n".join(body)

    if endpoint:
        form = f"""<form class="s-form" action="{endpoint}" method="POST" id="submitPageForm">
{fields_html}
<div class="s-hp" aria-hidden="true"><label for="f_{SUBMIT_HONEYPOT}">Leave this empty</label>
<input id="f_{SUBMIT_HONEYPOT}" name="{SUBMIT_HONEYPOT}" tabindex="-1" autocomplete="off"/></div>
<input type="hidden" name="_subject" value="Event submission — letsdoitdallas.com"/>
<button class="s-btn" type="submit">SUBMIT EVENT →</button>
<p class="s-msg">Free. No account needed.</p>
</form>"""
    else:
        # No endpoint configured yet — a mailto keeps the page honest rather
        # than rendering a form that silently posts nowhere.
        subject = urllib.parse.quote("Event submission — letsdoitdallas.com")
        mail_body = urllib.parse.quote(
            "Event name:\nVenue:\nNeighborhood / city:\nDate:\nStart time:\n"
            "Repeats (one-off / weekly / monthly):\nCost (0 = free):\n"
            "Category:\nLink:\nShort description:\n\nYour name:\nYour email:\n")
        form = (f'<a class="s-cta" href="mailto:{email}?subject={subject}'
                f'&body={mail_body}">EMAIL US YOUR EVENT →</a>'
                f'<p class="s-msg">Free. We reply to every one.</p>')

    title = "Submit an Event — Lets Do It Dallas"
    desc = ("List your DFW event free on Lets Do It Dallas. Music, food, markets, "
            "family and outdoors — send it over and we'll get it on the radar.")

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<meta name="description" content="{desc}"/>
<link rel="canonical" href="{SITE}/submit/"/>
<meta property="og:title" content="{title}"/>
<meta property="og:description" content="{desc}"/>
<meta property="og:type" content="website"/>
<meta property="og:url" content="{SITE}/submit/"/>
<meta property="og:image" content="{SITE}/og-image.png"/>
<meta name="twitter:card" content="summary_large_image"/>
<meta name="twitter:image" content="{SITE}/og-image.png"/>
<link rel="stylesheet" href="/css/styles.css"/>
<link rel="preload" as="style" href="https://fonts.googleapis.com/css2?family=Syne:wght@600;700;800&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap"/>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Syne:wght@600;700;800&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" media="print" onload="this.media='all';this.onload=null"/>
<noscript><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Syne:wght@600;700;800&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap"/></noscript>
<style>{_SUBMIT_CSS}</style>{_analytics_snippet()}</head><body>

<div class="a-nav"><a href="/">← LETS DO IT DALLAS</a></div>

<header class="a-hero"><div class="wrap">
  <p class="a-kicker">/ SUBMIT AN EVENT</p>
  <h1>Put your event on the radar.</h1>
  <p class="a-sub">Free, and it always will be. We pull {len(events)} events a night
  from the ticketing feeds — but the good stuff those feeds never see is the
  residency, the pop-up, the trivia night, the market. That part only gets here
  if you tell us.</p>
</div></header>

<section class="a-sec"><div class="wrap">
  <p class="a-lead">/ WHY BOTHER</p>
  <h2>What you get out of it</h2>
  <div class="a-cols">
    <div><h3>A real listing</h3><p>Your event shows up on the day it happens,
      in the right category, with your ticket link — not buried in a feed.</p></div>
    <div><h3>Search pages</h3><p>Listings feed our nightly-built pages for tonight,
      this weekend, free events, and each district. Those are indexed by Google.</p></div>
    <div><h3>Recurring is fine</h3><p>Tell us it's every Thursday and we'll set it up
      once. You don't have to submit it 52 times.</p></div>
  </div>
</div></section>

<section class="a-sec"><div class="wrap">
  <p class="a-lead">/ THE FORM</p>
  <h2>Tell us what's happening</h2>
  <p class="a-sub">Takes about a minute. Only the marked fields are required —
  send what you have and we'll chase the rest if we need it.</p>
  {form}
</div></section>

<section class="a-sec"><div class="wrap">
  <p class="a-lead">/ WHAT HAPPENS NEXT</p>
  <h2>How it gets on the site</h2>
  <ol class="s-steps">
    <li>A human reads it. Every submission, no auto-publish.</li>
    <li>We check it's real, in DFW, and not already on the site from a ticketing feed.</li>
    <li>It goes live, usually within a couple of days.</li>
    <li>If it clashes with something we already list, we merge rather than double it up.</li>
  </ol>
  <p class="a-note"><strong>Straight talk:</strong> we're a young site and we're not
  going to quote you a traffic number we can't back up. Listing is free precisely
  because we'd rather earn the relationship than sell you on numbers.
  If you want the pinned top spot instead, that's <a href="/advertise/">on the advertise page</a>.</p>
</div></section>

<section class="a-sec"><div class="wrap">
  <p class="a-lead">/ HOUSE RULES</p>
  <h2>What we'll list</h2>
  <p class="a-sub">Anything open to the public and actually happening in the
  DFW metro — music, food and drink, markets, arts, outdoors, sports, family,
  nightlife, festivals. We skip online-only events, anything outside the metro,
  affiliate-link farms, and events with no fixed date. We'll trim marketing
  copy down to a line or two so it reads like the rest of the site.</p>
</div></section>

<footer style="padding:34px 0 60px"><div class="wrap">
  <p style="font-family:var(--mono);font-size:11px;letter-spacing:.12em;color:var(--dim)">
  / QUESTIONS? <a href="mailto:{email}">{email}</a> &nbsp;·&nbsp;
  <a href="/">← BACK TO THE RADAR</a></p>
</div></footer>

</body></html>
"""
    out = ROOT / "submit"
    out.mkdir(exist_ok=True)
    (out / "index.html").write_text(html)
    return f"{SITE}/submit/"


def write_hubs(events):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # First: it fills _VENUE_PAGES, which _hub_row() reads to link listings to
    # venue pages. Emitting the hubs before this leaves those links off.
    pages = write_venues(events)

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
    pages.append(write_submit(events))

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
# If a source silently breaks (bad key, schema change, vendor outage), the
# fetchers above already degrade gracefully — they catch the error and return
# [], same as an intentionally-unconfigured source. main() couldn't tell "TM
# had zero events tonight" apart from "TM's key just died", and would happily
# overwrite live-events.json with whatever survived, commit it, push it, and
# exit 0. The site would quietly go from ~500 events to ~30 with no failed
# build to flag it. A run is only allowed to shrink the catalog by this much;
# past that it almost certainly means a source broke, not that DFW went quiet.
COLLAPSE_GUARD_RATIO = 0.5


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
    # Do214 sits after the primaries and before Seated: it is an aggregator, so
    # when it carries the same show as Ticketmaster we want TM's row (direct
    # ticket link, firmer price) to win dedupe. Its value is the small-venue
    # and food/arts inventory the ticketing APIs never list at all.
    # Dallasites101 sits alongside Do214: it's a small-yield lifestyle-blog
    # source (~8 events at a time) that sometimes carries a real ticket link
    # and price, so it deserves the same "before Seated" priority.
    rows = (fetch_ticketmaster(start, end) + fetch_seatgeek(start, end)
            + fetch_ics_feeds(start, end) + fetch_prekindle(start, end)
            + fetch_do214(start, end) + fetch_dallasites101(start, end)
            + fetch_seated(start, end))

    unique = dedupe(rows)

    previous_count = None
    if OUT_FILE.exists():
        try:
            previous_count = len(json.loads(OUT_FILE.read_text()))
        except (OSError, ValueError):
            previous_count = None   # unreadable old file can't gate anything

    if previous_count and len(unique) < previous_count * COLLAPSE_GUARD_RATIO:
        print(
            f"REFUSING TO WRITE: {len(unique)} events is less than "
            f"{COLLAPSE_GUARD_RATIO:.0%} of the previous {previous_count}. "
            f"This almost always means a source broke (dead API key, schema "
            f"change, vendor outage), not that DFW genuinely went quiet. "
            f"live-events.json, press.json and the hub pages are left "
            f"untouched — investigate the per-source counts printed above.",
            file=sys.stderr,
        )
        sys.exit(1)

    OUT_FILE.write_text(json.dumps(unique, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(unique)} events -> {OUT_FILE.name}")

    PRESS_FILE.write_text(json.dumps(fetch_press(), indent=1, ensure_ascii=False) + "\n")
    write_hubs(unique)


if __name__ == "__main__":
    main()
