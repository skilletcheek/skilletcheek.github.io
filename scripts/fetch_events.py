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

GEO = {"lat": 32.7767, "lng": -96.7970, "radius_miles": 40}
DAYS_AHEAD = 30
UA = "rj-does-dallas-fetcher/1.0 (+https://letsdoitdallas.com)"


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


# ------------------------------------------------------------------------ main
def main():
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=DAYS_AHEAD)

    rows = fetch_ticketmaster(start, end) + fetch_seatgeek(start, end) + fetch_ics_feeds(start, end)

    seen, unique = set(), []
    for r in rows:
        key = (re.sub(r"[^a-z0-9]+", " ", r["name"].lower()).strip(), r["date"])
        if not r["date"] or not r["name"] or key in seen:
            continue
        seen.add(key)
        unique.append(r)
    unique.sort(key=lambda r: (r["date"], r["name"]))

    OUT_FILE.write_text(json.dumps(unique, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(unique)} events -> {OUT_FILE.name}")


if __name__ == "__main__":
    main()
