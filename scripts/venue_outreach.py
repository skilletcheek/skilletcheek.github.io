#!/usr/bin/env python3
"""Draft the "we built you a page" email for a venue that has a /venue/ page.

Run locally, by hand, one venue at a time. Prints a ready-to-send subject and
body; --open hands it to your mail client instead.

WHY THIS EXISTS
---------------
Search Console showed the whole site parked in "Discovered - currently not
indexed" on 2026-07-23, and the fix documented in CLAUDE.md ("Internal links
are the crawl budget") was all internal. Internal linking is what a new domain
can do for ITSELF; inbound links from local, topically-relevant domains are
the part it cannot. A venue's own site is exactly that domain -- and venues
have a real reason to link back, because the page is about them.

The second effect is the one that actually pays this month: a venue that likes
the page often posts it to their own audience, which is traffic and a link in
one go.

NO CONTACT LIST, EVER
---------------------
CLAUDE.md's first rule is that this repo is public and must never carry a
contact list. notify_submitter.py already refuses to store a submitter's
address for the same reason. So this script:

  * never writes an address anywhere -- --to is used once, for one mailto,
  * derives everything else from files already published on the site,
  * has no --all-send, no queue and no address book. It drafts ONE email.

That is a deliberate ceiling, not an unfinished feature. Bulk-mailing 48
venues from a script is how a new domain becomes a spam complaint; 48 personal
emails sent by a human over a couple of weeks is how it gets 48 links.

MIRROR WARNING
--------------
The venue-slug mapping below is _split_area() + _venue_slug() imported from
fetch_events.py, not a reimplementation -- get it wrong and this sends a venue
a link to somebody else's page. Same reasoning as notify_submitter.py's
event_uid() mirror; see CLAUDE.md.

USAGE
    python3 scripts/venue_outreach.py --list
    python3 scripts/venue_outreach.py --match club-dada
    python3 scripts/venue_outreach.py --match club-dada --to booking@x.com --open
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_events as F                                     # noqa: E402

ROOT = F.ROOT
SITE = F.SITE

# Rooms whose name says they are an arena, a stadium or a corporate shed. They
# still get a page and still belong on the site -- they are just the worst
# outreach targets in the list: a Ticketmaster-fed arena has a marketing
# department, no incentive to link out, and nobody who answers this email.
# A heuristic on the name, shown in --list so you can skip them, never a filter.
_MAJOR = re.compile(r"\b(stadium|arena|coliseum|pavilion|amphitheat|"
                    r"convention center|center|centre)\b", re.I)


def upcoming_by_venue() -> dict:
    """{slug: {"name":…, "city":…, "events":[…]}} for venues that HAVE a page.

    Ground truth for "has a page" is the directory on disk, because that is
    what the email links to. A venue below VENUE_MIN_EVENTS has no page and
    _prune_stale_venues() may have deleted the one it had.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = json.loads((ROOT / "live-events.json").read_text())
    out = {}
    for e in rows:
        if e["date"] < today:
            continue
        venue, _street, city = F._split_area(e["area"])
        if not venue:
            continue
        slug = F._venue_slug(venue)
        if not (ROOT / "venue" / slug / "index.html").exists():
            continue
        g = out.setdefault(slug, {"name": venue, "city": city or "", "events": []})
        g["events"].append(e)
    for g in out.values():
        g["events"].sort(key=lambda e: (e["date"], F._time_minutes(e.get("time")) or 0))
    return out


def compose(slug: str, g: dict, contact_name: str, signature: str) -> tuple:
    """Subject and body. Specific, checkable, and short.

    Every number and date in here is read from the live feed, so the venue can
    verify the whole email by clicking one link. That is the point: the ask
    lands only if the thing being offered is obviously already real.
    """
    site_name = F._config_value("siteName") or "Lets Do It Dallas"
    name = g["name"]
    page = f"{SITE}/venue/{slug}/"
    n = len(g["events"])
    # Only offer the calendar if the file is actually there. The per-venue
    # .ics files are written by the nightly build, so between shipping the
    # feature and the next run they do not exist yet -- and an email whose
    # second link 404s undoes the one thing this email is trying to prove.
    has_cal = (ROOT / "venue" / slug / "calendar.ics").exists()
    cal_block = (
        f"There's a calendar people can subscribe to as well, so your dates "
        f"land in their own calendar as you add them:\n\n"
        f"  webcal://letsdoitdallas.com/venue/{slug}/calendar.ics\n\n"
    ) if has_cal else ""

    nxt = "\n".join(
        f"  {F._fmt_day(e['date'])}"
        + (f", {e['time']}" if F._time_minutes(e.get('time')) is not None else "")
        + f" — {e['name']}"
        for e in g["events"][:3])

    subject = f"Built a page for {name} — {n} of your upcoming shows"
    greeting = f"Hi {contact_name}," if contact_name else "Hi,"

    # The correction offer goes FIRST and the link ask second, on purpose. The
    # correction is the part with value for them and the part that gets a
    # reply; leading with the ask makes the whole thing read as link-begging.
    body = (
        f"{greeting}\n\n"
        f"I run {site_name} (letsdoitdallas.com), a free events site for DFW. "
        f"We already list {name}, so it has its own page:\n\n"
        f"  {page}\n\n"
        f"It's live now, carries {n} upcoming show{'s' if n != 1 else ''}, and "
        f"refreshes every night. Right now that's:\n\n"
        f"{nxt}\n\n"
        f"{cal_block}"
        f"Two things, both small:\n\n"
        f"1. If anything's wrong — a date, the room name, a show we're "
        f"missing — reply and I'll fix it. Free, no catch, no account.\n"
        f"2. If it's useful, a link to that page from your site helps people "
        f"find your shows. A links or press page is plenty.\n\n"
        f"Either way the page stays up and stays free.\n\n"
        f"— {signature}\n"
        f"  letsdoitdallas.com\n\n"
        f"If you'd rather not hear from me again, say the word and I won't "
        f"write again.\n"
    )
    return subject, body


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true",
                    help="show venues with pages, best outreach targets first")
    ap.add_argument("--match", help="venue slug, or a substring of its name")
    ap.add_argument("--to", help="venue's email (used once, never stored)")
    ap.add_argument("--name", default="", help="contact's first name, for the greeting")
    ap.add_argument("--signature", default="", help="how to sign off (default: the site name)")
    ap.add_argument("--open", action="store_true",
                    help="hand the draft to your mail client instead of printing it")
    args = ap.parse_args()

    venues = upcoming_by_venue()
    if not venues:
        sys.exit("No venue pages found. Has the nightly build run?")

    if args.list or not args.match:
        ranked = sorted(venues.items(), key=lambda kv: -len(kv[1]["events"]))
        print(f"{len(ranked)} venue page(s), most upcoming shows first.")
        print("'major?' is a guess from the name — arenas and sheds rarely "
              "link out and rarely reply.\n")
        print(f"  {'slug':44s} {'shows':>5}  {'major?':6s} city")
        for slug, g in ranked:
            flag = "yes" if _MAJOR.search(g["name"]) else ""
            print(f"  {slug:44s} {len(g['events']):5d}  {flag:6s} {g['city']}")
        if not args.match:
            print("\nPass --match <slug> [--to <email>] [--name <first name>].")
        return

    needle = args.match.lower()
    hits = [(s, g) for s, g in venues.items()
            if needle == s or needle in s or needle in g["name"].lower()]
    if not hits:
        sys.exit(f"No venue page matches {needle!r}. Try --list.")
    if len(hits) > 1:
        sys.exit(f"{needle!r} matches {len(hits)}: "
                 f"{', '.join(sorted(s for s, _ in hits))}. Be more specific.")
    slug, g = hits[0]

    signature = args.signature.strip() or (F._config_value("siteName") or "Lets Do It Dallas")
    subject, body = compose(slug, g, args.name.strip(), signature)

    if args.open:
        if not args.to:
            sys.exit("--open needs --to.")
        url = (f"mailto:{urllib.parse.quote(args.to)}"
               f"?subject={urllib.parse.quote(subject)}"
               f"&body={urllib.parse.quote(body)}")
        subprocess.run(["open", url], check=False)
        print("Handed to your mail client. Full text below in case it truncated:\n")

    print(f"To:      {args.to or '<pass --to>'}")
    print(f"Subject: {subject}\n")
    print(body)


if __name__ == "__main__":
    main()
