#!/usr/bin/env python3
"""Eventbrite top-up — run this from a home/office machine, not CI.

Eventbrite serves its city discovery pages fine to a residential IP but answers
HTTP 405 to GitHub Actions (its runners are datacenter IPs). So Eventbrite is
deliberately left out of the nightly workflow and refreshed by hand instead.

    python3 scripts/fetch_eventbrite_local.py
    git add eventbrite.json && git commit -m "chore: refresh eventbrite.json" && git push

Writes eventbrite.json in the repo root, which the site loads alongside
live-events.json. Only events that are NOT already in live-events.json are
written, using the same dedupe the nightly job uses, so the browser never has
to reconcile two copies of the same show.

The discovery-page scrape needs no API keys. If EVENTBRITE_TOKEN is set (in
a local .env, gitignored, never committed), this also pulls events from the
token owner's own Eventbrite organizations via fetch_eventbrite_api() — use
that route to self-publish local events by creating them on Eventbrite.

Re-run whenever you want; weekly is plenty, since it covers a 30-day window
and the site hides events once their date has passed.

    python3 scripts/fetch_eventbrite_local.py --publish

is the unattended form, run weekly by a scheduled task on this Mac (added
2026-11-13). It pulls first (the dedupe below reads live-events.json, so it
must be tonight's), refreshes, and writes, commits and pushes eventbrite.json
ONLY if every check in validate() passes. On any failure it writes nothing,
pushes nothing, and exits non-zero naming the reason -- this repo is public
and the file is served at letsdoitdallas.com/eventbrite.json.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_events as fe  # noqa: E402

OUT = fe.ROOT / "eventbrite.json"
ENV_FILE = fe.ROOT / ".env"


def _load_env():
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


# Below this, a refresh is far more likely to be a throttled or broken scrape
# than DFW going quiet: hand refreshes have produced 104-179 rows.
MIN_PUBLISH_ROWS = 50
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def validate(rows: list, today: str) -> list:
    """Reasons not to publish `rows`; empty means publishable."""
    bad = []
    if len(rows) < MIN_PUBLISH_ROWS:
        bad.append(f"only {len(rows)} rows (< {MIN_PUBLISH_ROWS}); looks like a broken scrape")
    for r in rows:
        name = (r.get("name") or "")[:50]
        d = str(r.get("date"))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            bad.append(f"malformed date {d!r}: {name}")
        elif d < today:
            bad.append(f"past date {d}: {name}")
        if not r.get("name"):
            bad.append("row with no name")
        if not (r.get("city") and fe.is_dfw_city(r["city"], "TX")):
            bad.append(f"no DFW city ({r.get('city')!r}): {name}")
        if not str(r.get("url") or "").startswith(("https://", "http://")):
            bad.append(f"no http(s) link: {name}")
    if _EMAIL.search(json.dumps(rows)):
        bad.append("an email address appears in the rows (public repo)")
    return bad


def _git(*args) -> None:
    subprocess.run(["git", "-C", str(fe.ROOT), *args], check=True)


def main():
    publish = "--publish" in sys.argv[1:]
    if publish:
        # live-events.json must be current: dedupe below drops what it carries.
        _git("pull", "-q", "--rebase", "--autostash")
    _load_env()
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=fe.DAYS_AHEAD)

    rows = fe.fetch_eventbrite(start, end) + fe.fetch_eventbrite_api(start, end)
    if not rows:
        print("\nNo Eventbrite events fetched — leaving eventbrite.json untouched.")
        print("If every city logged 405/429, the host is throttling or blocking this")
        print("network; wait a while and try again rather than retrying in a loop.")
        return 1

    # Drop anything the nightly feed already carries. dedupe() keeps the first
    # occurrence, so listing the existing rows first makes them win.
    try:
        existing = json.loads((fe.ROOT / "live-events.json").read_text())
    except Exception:  # noqa: BLE001
        existing = []
    existing_ids = {id(r) for r in existing}
    merged = fe.dedupe(existing + rows)
    fresh = [r for r in merged if id(r) not in existing_ids]

    if publish:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        problems = validate(fresh, today)
        if problems:
            print(f"\nNOT PUBLISHED -- {len(problems)} check(s) failed; eventbrite.json untouched:")
            for p in problems[:20]:
                print(f"  - {p}")
            return 2

    OUT.write_text(json.dumps(fresh, indent=1, ensure_ascii=False) + "\n")
    print(f"\nfetched {len(rows)} Eventbrite rows")
    print(f"{len(rows) - len(fresh)} already covered by live-events.json")
    print(f"wrote {len(fresh)} new events -> {OUT.name}")
    if publish:
        if not subprocess.run(["git", "-C", str(fe.ROOT), "diff", "--quiet", "--", OUT.name]).returncode:
            print("eventbrite.json unchanged; nothing to publish")
            return 0
        dates = sorted(r["date"] for r in fresh)
        _git("commit", "-q", "-m",
             f"chore: refresh eventbrite.json (weekly)\n\n{len(fresh)} events, {dates[0]}..{dates[-1]}; "
             "all checks in fetch_eventbrite_local.validate() passed.", "--", OUT.name)
        _git("pull", "-q", "--rebase", "--autostash")
        _git("push", "-q", "origin", "main")
        print(f"PUBLISHED {len(fresh)} events ({dates[0]}..{dates[-1]})")
        return 0
    print("\nCommit it to publish:")
    print('  git add eventbrite.json && git commit -m "chore: refresh eventbrite.json" && git push')
    return 0


if __name__ == "__main__":
    sys.exit(main())
