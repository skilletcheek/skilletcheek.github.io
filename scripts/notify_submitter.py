#!/usr/bin/env python3
"""Draft the "your event is live" email for someone who used /submit/.

Run locally, by hand, after their event has been added to events.json and
pushed. Prints a ready-to-send subject and body; --open hands it to your mail
client instead.

WHY THIS IS A LOCAL SCRIPT AND NOT A WORKFLOW
---------------------------------------------
The obvious version of this is a GitHub Action that watches events.json and
emails the submitter on publish. It cannot be built that way without either
(a) committing the submitter's address to events.json, or (b) paying for
Formspree's API to look it up. This repo is PUBLIC and events.json is served
at letsdoitdallas.com/events.json, so (a) would publish a contact list of
local venue owners. That is not a tradeoff worth making for a handful of
emails a month.

So the address is passed on the command line, used once, and never written
anywhere. Nothing this script touches is private.

WHY IT GROUPS BY URL
--------------------
One submission routinely becomes several rows: a multi-day festival gets a row
per day, and a speaker series gets a row per night with a different title each
time. The submitter sent one email and should get one reply listing every date.
Rows that share a `url` came from the same submission, which is the only
grouping signal that survives the retitling those rows go through.

MIRROR WARNING
--------------
event_uid() below mirrors uid() in js/app.js, which builds the ?e= deep link
from name + area + time. If that changes, this sends people links that open
the wrong drawer -- or no drawer at all. Same two-layer contract as dedupe()
and _split_area(); see CLAUDE.md.

USAGE
    python3 scripts/notify_submitter.py --list
    python3 scripts/notify_submitter.py --match armeniafest \
        --to someone@example.com --name Celine
    python3 scripts/notify_submitter.py --match escher --to ... --open
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys
import urllib.parse
from collections import OrderedDict

ROOT = pathlib.Path(__file__).resolve().parent.parent
EVENTS_FILE = ROOT / "events.json"
DATA_JS = ROOT / "js" / "data.js"
SITE = "https://letsdoitdallas.com"


def _config_value(key: str, fallback: str) -> str:
    """Pull a string off CONFIG in js/data.js so the site name and reply
    address have one definition rather than a second copy in here."""
    try:
        m = re.search(rf'{key}:\s*"([^"]*)"', DATA_JS.read_text())
    except OSError:
        return fallback
    return (m.group(1) if m else "") or fallback


def event_uid(row: dict) -> str:
    """Mirror of uid() in js/app.js -- see MIRROR WARNING above."""
    raw = f"{row.get('name','')}|{row.get('area','')}|{row.get('time','')}".lower()
    return re.sub(r"[^a-z0-9|]+", "-", raw)


def deep_link(row: dict) -> str:
    """A URL that opens this row's drawer, not just the day it falls on."""
    e = urllib.parse.quote(event_uid(row), safe="")
    return f"{SITE}/?date={row['date']}&e={e}"


def load_groups() -> "OrderedDict[str, list]":
    rows = json.loads(EVENTS_FILE.read_text())
    groups: "OrderedDict[str, list]" = OrderedDict()
    for r in sorted(rows, key=lambda x: x["date"]):
        groups.setdefault(r.get("url") or r["name"], []).append(r)
    return groups


def pick(groups, needle: str):
    """Match on any substring of the title or the link, case-insensitively."""
    needle = needle.lower()
    hits = [(k, v) for k, v in groups.items()
            if needle in k.lower() or any(needle in r["name"].lower() for r in v)]
    if not hits:
        sys.exit(f"No curated event matches {needle!r}. Try --list.")
    if len(hits) > 1:
        names = ", ".join(sorted({v[0]["name"] for _, v in hits}))
        sys.exit(f"{needle!r} matches more than one event ({names}). Be more specific.")
    return hits[0][1]


def fmt_date(iso: str) -> str:
    from datetime import date
    y, m, d = (int(x) for x in iso.split("-"))
    return date(y, m, d).strftime("%a %b %-d")


def compose(rows, contact_name: str) -> "tuple[str, str]":
    site_name = _config_value("siteName", "Lets Do It Dallas")
    title = rows[0]["name"].split(":")[0].split(" —")[0].strip()
    # Not "<title> is live on <site>" -- a title containing "Live"
    # ("Escher Quartet Live in Dallas") makes that read twice.
    subject = f"{title} — now on {site_name}"

    greeting = f"Hi {contact_name}," if contact_name else "Hi,"
    share = ("Those links go straight to the listings, so they're fine to share."
             if len(rows) > 1 else
             "That link goes straight to the listing, so it's fine to share.")
    if len(rows) == 1:
        r = rows[0]
        listing = deep_link(r)
        detail = (f"Listed as {fmt_date(r['date'])}, {r['time']}, "
                  f"{r['area'].split(',')[0].strip()}.")
    else:
        listing = "\n".join(f"  {fmt_date(r['date'])}: {deep_link(r)}" for r in rows)
        detail = (f"It runs across {len(rows)} dates, so each one is listed "
                  f"separately and shows up whichever day someone is browsing.")

    body = (
        f"{greeting}\n\n"
        f"Thanks for submitting {title} — it's live on the site now.\n\n"
        f"{listing}\n\n"
        f"{detail}\n\n"
        f"{share} If anything looks wrong, just reply and I'll fix it.\n\n"
        f"— {site_name}\n"
    )
    return subject, body


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="show curated events and exit")
    ap.add_argument("--match", help="substring of the event title or its link")
    ap.add_argument("--to", help="submitter's email (used once, never stored)")
    ap.add_argument("--name", default="", help="submitter's first name, for the greeting")
    ap.add_argument("--open", action="store_true",
                    help="hand the draft to your mail client instead of printing it")
    args = ap.parse_args()

    groups = load_groups()

    if args.list or not args.match:
        print(f"{len(groups)} curated event(s) in events.json:\n")
        for rows in groups.values():
            dates = ", ".join(r["date"] for rows_ in [rows] for r in rows_)
            print(f"  {rows[0]['name']}\n    {len(rows)} row(s): {dates}\n")
        if not args.match:
            print("Pass --match <substring> --to <email> [--name <first name>].")
        return

    rows = pick(groups, args.match)
    subject, body = compose(rows, args.name.strip())

    if args.open:
        if not args.to:
            sys.exit("--open needs --to.")
        # Long bodies get truncated by some clients; the printed copy below is
        # always the authoritative one.
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
