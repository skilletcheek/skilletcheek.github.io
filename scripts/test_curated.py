#!/usr/bin/env python3
"""Tests for load_curated() in fetch_events.py.

    python3 scripts/test_curated.py

Stdlib only, no network, never touches the real events.json. load_curated()
is what puts /submit/ events onto the generated pages (hubs, venue and city
pages, feed.xml, calendar.ics); before it existed none of them carried a
single curated row, while the homepage did.
"""

import json
import pathlib
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fetch_events as F                                        # noqa: E402

FAILURES = []
START, END = datetime(2026, 10, 10), datetime(2026, 11, 9)
REAL = F.CURATED_FILE


def check(label, got, want):
    if got == want:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")
        FAILURES.append(label)


def load(rows_or_text):
    """Run load_curated() over a temp file holding `rows_or_text`."""
    F._SOURCE_FAULTS.clear()
    p = pathlib.Path(tempfile.mkdtemp()) / "events.json"
    p.write_text(rows_or_text if isinstance(rows_or_text, str)
                 else json.dumps(rows_or_text))
    F.CURATED_FILE = p
    try:
        return F.load_curated(START, END)
    finally:
        F.CURATED_FILE = REAL


def ev(**kw):
    base = {"name": "Show", "category": "music", "area": "Trees, Dallas",
            "date": "2026-10-20", "time": "8:00 PM", "cost": 20,
            "description": "d", "url": "https://example.com/x"}
    base.update(kw)
    return base


def test_window():
    print("build window")
    got = load([ev(name="Past", date="2026-10-09"), ev(name="First", date="2026-10-10"),
                ev(name="Last", date="2026-11-09"), ev(name="Beyond", date="2026-11-10")])
    check("keeps both ends of the window, drops either side",
          [r["name"] for r in got], ["First", "Last"])


def test_shape():
    print("row shape matches fetched rows")
    r = load([ev(area="Inclusion Coffee, Arlington", category="FOOD")])[0]
    check("city is derived, as row() does for feeds", r["city"], "Arlington")
    check("category lowercased, as _fromRows() does", r["category"], "food")
    check("same keys as a fetched row",
          set(r) >= {"name", "category", "area", "city", "date", "time",
                     "cost", "description", "url", "image"}, True)

    print("field aliases mirror js/sources.js _fromRows()")
    r = load([{"name": "A", "cat": "arts", "dateISO": "2026-10-21",
               "desc": "via alias", "area": "DMA, Dallas"}])[0]
    check("cat / dateISO / desc accepted",
          (r["category"], r["date"], r["description"]), ("arts", "2026-10-21", "via alias"))
    check("missing category falls back to festival, as the browser does",
          load([ev(category=None)])[0]["category"], "festival")


def test_cost():
    print("cost mirrors _fromRows(): '' and null are unknown, else a number")
    for given, want in [(0, 0), (50, 50), (50.0, 50), ("50", 50), (12.5, 12.5),
                        ("", None), (None, None), ("call us", None)]:
        check(f"cost {given!r} -> {want!r}", load([ev(cost=given)])[0]["cost"], want)


def test_bad_rows():
    print("a typo in one row costs that row only")
    got = load([ev(name="Good"), ev(name="BadDate", date="10/20/2026"),
                ev(name="", date="2026-10-20"), "not a dict", ev(name="AlsoGood")])
    check("malformed date, missing name and non-dict dropped; rest kept",
          [r["name"] for r in got], ["Good", "AlsoGood"])
    check("a bad ROW is not a source fault", F._SOURCE_FAULTS, [])


def test_bad_file():
    print("an unparseable FILE is a source fault (the homepage lost them too)")
    check("syntax error -> no rows", load('[{"name": "x",]'), [])
    check("...and one fault for sourcecheck", len(F._SOURCE_FAULTS), 1)
    check("top level not a list -> fault", (load('{"name": "x"}'), len(F._SOURCE_FAULTS)), ([], 1))

    F._SOURCE_FAULTS.clear()
    F.CURATED_FILE = pathlib.Path(tempfile.mkdtemp()) / "absent.json"
    try:
        check("a missing file is not a fault", (F.load_curated(START, END), F._SOURCE_FAULTS), ([], []))
    finally:
        F.CURATED_FILE = REAL


def test_dedupe_order():
    print("curated wins a duplicate, as it does in the browser")
    feed = F.row("Pin-Ups on Tour: Salute to Comedy", "arts",
                 "American Legion Post 453 Dallas Love Field, Dallas",
                 "2026-10-24", "6:00 PM", 25, "feed copy", "https://tm.example/x")
    cur = load([ev(name="Pin-Ups on Tour: Salute to Comedy", category="nightlife",
                   area="American Legion Post 453 Dallas Love Field, Dallas",
                   date="2026-10-24", time="6:00 PM", cost=20)])
    merged = F.dedupe(cur + [feed])
    check("one row survives", len(merged), 1)
    check("and it is the curated one (its price, its link)",
          (merged[0]["cost"], merged[0]["url"]), (20, "https://example.com/x"))


def test_real_file():
    print("the real events.json")
    F._SOURCE_FAULTS.clear()
    raw = json.loads(REAL.read_text())
    got = F.load_curated(datetime(2000, 1, 1), datetime(2100, 1, 1))
    check("every row loads, none dropped", len(got), len(raw))
    check("no fault", F._SOURCE_FAULTS, [])
    j = [r for r in got if "unfaulted" in r["url"]]
    check("The Journey resolves to Arlington", j and j[0]["city"], "Arlington")


def main():
    for t in (test_window, test_shape, test_cost, test_bad_rows, test_bad_file,
              test_dedupe_order, test_real_file):
        t()
        print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
