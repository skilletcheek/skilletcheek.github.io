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

Needs no API keys. Re-run whenever you want; weekly is plenty, since it covers
a 30-day window and the site hides events once their date has passed.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_events as fe  # noqa: E402

OUT = fe.ROOT / "eventbrite.json"


def main():
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=fe.DAYS_AHEAD)

    rows = fe.fetch_eventbrite(start, end)
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

    OUT.write_text(json.dumps(fresh, indent=1, ensure_ascii=False) + "\n")
    print(f"\nfetched {len(rows)} Eventbrite rows")
    print(f"{len(rows) - len(fresh)} already covered by live-events.json")
    print(f"wrote {len(fresh)} new events -> {OUT.name}")
    print("\nCommit it to publish:")
    print('  git add eventbrite.json && git commit -m "chore: refresh eventbrite.json" && git push')
    return 0


if __name__ == "__main__":
    sys.exit(main())
