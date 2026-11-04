#!/usr/bin/env python3
"""Tests for the social poster's same-day rules (scripts/social_post.py).

    python3 scripts/test_social_post.py

No network. Needs Pillow, because social_post imports social_card -- the
same requirement as the workflow that runs it.

Covers the two rules that decide WHAT gets posted, both fixed 2026-11-04
after the same three picks went out twice a day on Nov 2, 3 and 4:

  _entries_before() / already_posted_today()  -- a slot excludes every OTHER
      slot already posted today, whatever order GitHub actually ran them in
  _not_today_in_dallas()  -- a run that lands after the UTC date has rolled
      over (7 PM CDT / 6 PM CST) skips instead of posting tomorrow's events
"""

import argparse
import json
import pathlib
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import social_post as SP                                        # noqa: E402

FAILURES = []


def check(label, got, want):
    if got == want:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")
        FAILURES.append(label)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_out_of_order_slots():
    print("same-day exclusion does not assume slots run in order")
    # Nov 4 as it happened: afternoon (a late catch-up) logged first, then
    # morning built and re-picked the same three.
    log = {"2026-11-04": {"afternoon": {
        "picks": ["All Get Out", "Tea Around Town® Dallas", "Crochet Corner In-Person (Ages 13+)"],
        "venues": ["Club Dada"]}}}
    ex = SP.already_posted_today(log, "2026-11-04", "morning")
    check("morning sees the afternoon slot that already posted",
          ex, frozenset(SP.F._norm_name(n) for n in log["2026-11-04"]["afternoon"]["picks"]))
    check("and its venues count toward the repeat penalty",
          SP.recent_venues(log, "2026-11-04", "morning"), {"club dada"})

    check("a slot never excludes its own entry (retry after a partial post)",
          SP.already_posted_today(log, "2026-11-04", "afternoon"), frozenset())
    check("yesterday's picks are not a same-day exclusion",
          SP.already_posted_today(log, "2026-11-05", "morning"), frozenset())

    print("  against the real feed: the second slot of the day picks different events")
    events = json.loads((SP.ROOT / "live-events.json").read_text())
    import collections
    today = collections.Counter(e["date"] for e in events).most_common(1)[0][0]
    first = SP.select_picks(events, today, set())
    names = [p["event"]["name"] for p in first]
    check(f"the first slot on {today} picks {SP.PICKS}", len(names), SP.PICKS)
    log = {today: {"afternoon": {"picks": names, "venues": [p["venue"] for p in first if p["venue"]]}}}
    second = SP.select_picks(events, today, SP.recent_venues(log, today, "morning"),
                             exclude_names=SP.already_posted_today(log, today, "morning"))
    check(f"morning after afternoon on {today} shares no picks with it",
          set(names) & {p["event"]["name"] for p in second}, set())


def test_dallas_date_guard():
    print("a run that lands after the UTC date rolls over skips")
    check("Nov 4 00:57 UTC (6:57 PM Nov 3 CST) building 'Nov 4' -> skip",
          SP._not_today_in_dallas("2026-11-04", utc(2026, 11, 4, 0, 57)) is not None, True)
    check("Nov 4 16:43 UTC (10:43 AM CST) -> post",
          SP._not_today_in_dallas("2026-11-04", utc(2026, 11, 4, 16, 43)), None)
    check("Nov 4 05:59 UTC (11:59 PM Nov 3 CST) -> skip",
          SP._not_today_in_dallas("2026-11-04", utc(2026, 11, 4, 5, 59)) is not None, True)
    check("Nov 4 06:00 UTC (midnight CST) -> post",
          SP._not_today_in_dallas("2026-11-04", utc(2026, 11, 4, 6, 0)), None)
    check("CDT: Oct 15 23:30 UTC (6:30 PM CDT) -> post",
          SP._not_today_in_dallas("2026-10-15", utc(2026, 10, 15, 23, 30)), None)
    check("CDT: Oct 16 00:30 UTC (7:30 PM Oct 15 CDT) -> skip",
          SP._not_today_in_dallas("2026-10-16", utc(2026, 10, 16, 0, 30)) is not None, True)


def test_build_skips_when_late():
    print("cmd_build writes a skip plan and renders nothing when it is too late")
    tmp = pathlib.Path(tempfile.mkdtemp())
    real = (SP._creds, SP._not_today_in_dallas, SP.POSTED_FILE, SP.CARD_DIR)
    SP._creds = lambda allow_unconfigured=False: ("token", "page")
    SP._not_today_in_dallas = lambda today, now=None: "it is tomorrow in UTC"
    SP.POSTED_FILE, SP.CARD_DIR = tmp / "posted.json", tmp / "cards"
    try:
        plan = tmp / "plan.json"
        rc = SP.cmd_build(argparse.Namespace(slot="afternoon", plan=str(plan),
                                             font_cache=str(tmp / "fonts")))
        check("exit 0 (a skip, not a failure)", rc, 0)
        check("plan says skip", json.loads(plan.read_text()), {"skip": "too late for today"})
        check("no card rendered", (tmp / "cards").exists(), False)
    finally:
        SP._creds, SP._not_today_in_dallas, SP.POSTED_FILE, SP.CARD_DIR = real


def main():
    for t in (test_out_of_order_slots, test_dallas_date_guard, test_build_skips_when_late):
        t()
        print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
