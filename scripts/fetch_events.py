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
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEEDS_FILE = Path(__file__).resolve().parent / "feeds.json"
OUT_FILE = ROOT / "live-events.json"

GEO = {"lat": 32.7767, "lng": -96.7970, "radius_miles": 50}  # 50mi covers the DFW suburbs
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
        for ev in events:
            dates = (ev.get("dates") or {}).get("start") or {}
            venue = ((ev.get("_embedded") or {}).get("venues") or [{}])[0]
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
        for ev in events:
            dt = ev.get("datetime_local") or ""
            venue = ev.get("venue") or {}
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

# Metro cities we consider "DFW". Seated gives a "City, ST" formatted-address,
# not coordinates, so the radius filter used for TM/SG can't apply here.
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
}


def _seated_is_dfw(formatted_address: str) -> bool:
    """formatted-address looks like 'Fort Worth, TX'. Match the city half only,
    and require Texas so a 'Dallas, GA' or 'Arlington, VA' can't sneak in."""
    parts = [p.strip().lower() for p in (formatted_address or "").split(",")]
    if len(parts) < 2 or not parts[-1].startswith("tx"):
        return False
    return parts[0] in DFW_CITIES


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
</style></head><body>
<p class="k">/ LETS DO IT DALLAS — {note}</p>
<h1>{heading}</h1>
<a class="cta" href="{app_link}">( OPEN THE LIVE RADAR ↗ )</a>
<ul>{rows}</ul>
<p><a href="/">← letsdoitdallas.com</a></p>
</body></html>"""


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


def dedupe(rows):
    """Collapse the same event arriving from multiple sources. Keeps the FIRST
    occurrence, so callers should order rows richest-source-first.

    Two keys, because sources disagree about titles:
      1. normalized title + date   — catches most matches
      2. venue + date + start time — catches the same show titled differently
         ("White Sox at Rangers" vs "Texas Rangers vs. Chicago White Sox").

    Key 2 additionally requires the two titles to share a meaningful word.
    Without that guard it over-collapses: a comedy club running Jackie Fabulous
    and Cipha Sounds at the same 7:00 PM slot, or a rodeo arena running
    Ultimate Bullfighters alongside the Stockyards Championship Rodeo, are
    genuinely different events and must both survive.
    Key 2 is skipped when the time is a placeholder, since many placeholder
    rows at one venue would otherwise collapse into each other."""
    VAGUE_TIMES = ("", "see details", "all day", "doors — see listing")
    STOP = {"the", "a", "an", "at", "vs", "v", "and", "of", "in", "on", "for",
            "with", "not", "featuring", "night", "show", "series"}
    seen_name, seen_slot, unique = {}, {}, []
    for r in rows:
        if not r.get("date") or not r.get("name"):
            continue
        norm = _norm_name(r["name"])
        name_key = (norm, r["date"])
        if name_key in seen_name:
            continue
        tokens = {t for t in norm.split() if t not in STOP and len(t) > 1}
        venue = (r.get("area") or "").split(",")[0].strip().lower()
        time = (r.get("time") or "").strip().lower()
        slot_key = (venue, r["date"], time) if venue and time not in VAGUE_TIMES else None
        # same venue + same start time AND a shared word => same event, retitled
        if slot_key and any(tokens & prev for prev in seen_slot.get(slot_key, [])):
            continue
        seen_name[name_key] = True
        if slot_key:
            seen_slot.setdefault(slot_key, []).append(tokens)
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
