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


def row(name, category, area, date, time, cost, desc, url):
    return {
        "name": (name or "").strip()[:140],
        "category": category or "festival",
        "area": (area or "Dallas–Fort Worth").strip()[:80],
        "date": date,
        "time": time or "See details",
        "cost": cost,
        "description": (desc or "").strip()[:280],
        "url": url or "#",
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
            out.append(row(
                ev.get("name"), TM_SEGMENT.get(seg, "festival"),
                ", ".join(x for x in [venue.get("name"), (venue.get("city") or {}).get("name")] if x),
                dates.get("localDate"), pretty_time(dates.get("localTime", "")),
                round(price) if isinstance(price, (int, float)) else None,
                desc, ev.get("url"),
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
                           date, "Doors — see listing", cost, desc, ev.get("url")))
            count += 1
        print(f"prekindle ({venue}): {count} events")
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
        if t:
            h = int(t.group(1)) % 12 + (12 if t.group(3) == "PM" else 0)
            start = f"{e['date']}T{h:02d}:{t.group(2)}:00-05:00"
        item = {"@type": "Event", "name": e["name"], "startDate": start,
                "eventStatus": "https://schema.org/EventScheduled",
                "location": {"@type": "Place", "name": e["area"],
                             "address": {"@type": "PostalAddress", "addressRegion": "TX",
                                         "addressLocality": e["area"]}},
                "url": e["url"] if e["url"] != "#" else SITE}
        if e.get("description"):
            item["description"] = e["description"]
        if e.get("cost") is not None:
            item["offers"] = {"@type": "Offer", "price": e["cost"], "priceCurrency": "USD"}
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


# ------------------------------------------------------------------------ main
def main():
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=DAYS_AHEAD)

    rows = (fetch_ticketmaster(start, end) + fetch_seatgeek(start, end)
            + fetch_ics_feeds(start, end) + fetch_prekindle(start, end))

    seen, unique = set(), []
    for r in rows:
        norm = r["name"].lower()
        norm = re.sub(r"\(.*?\)", " ", norm)          # drop parentheticals like (18+)
        norm = norm.replace("&", " and ")
        norm = re.sub(r"[^a-z0-9]+", " ", norm).strip()
        key = (norm, r["date"])
        if not r["date"] or not r["name"] or key in seen:
            continue
        seen.add(key)
        unique.append(r)
    unique.sort(key=lambda r: (r["date"], r["name"]))

    OUT_FILE.write_text(json.dumps(unique, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(unique)} events -> {OUT_FILE.name}")

    PRESS_FILE.write_text(json.dumps(fetch_press(), indent=1, ensure_ascii=False) + "\n")
    write_hubs(unique)


if __name__ == "__main__":
    main()
