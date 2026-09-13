#!/usr/bin/env python3
"""Tests for the source-health checks in fetch_events.py.

    python3 scripts/test_source_health.py

Stdlib only and no network, like everything else the nightly run depends on.
Exits non-zero on the first failing assertion set, so CI could run it, but it
is hand-run today.

Covers the two checks that decide whether the nightly job goes red:

  check_source_health()  -- did a source's YIELD collapse against its rolling
                            baseline (see _source_baseline for why a median
                            and not the previous run)
  report_parse_health()  -- did a source's PAGES stop parsing, which a yield
                            cannot see

Both exist because an aggregate number is blind to a component failing, and
both have already been fooled once: COLLAPSE_GUARD_RATIO watched the total
while all six Prekindle pages 404'd, and the previous-run comparison turned a
Sunday morning into a red build.
"""

import json
import pathlib
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fetch_events as F                                        # noqa: E402

FAILURES = []
_real_http_text = F.http_text


def check(label, got, want):
    if got == want:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")
        FAILURES.append(label)


def reset():
    """Fresh counts file and no carried-over parse faults."""
    if F.SOURCE_COUNTS_FILE.exists():
        F.SOURCE_COUNTS_FILE.unlink()
    F._SOURCE_FAULTS.clear()


def run(**counts):
    return F.check_source_health(dict(counts))


# --------------------------------------------------------------- yield checks
def test_sunday_false_alarm():
    """The 2026-09-13 red build: Saturday's peak is not a baseline.

    dallasites101 publishes a fixed ~30-item rolling window, so seven of its
    eleven rows on Saturday 2026-09-12 were that same Saturday's markets.
    Sunday dropped all seven as past-dated and yielded 4.
    """
    print("the 2026-09-13 Sunday false alarm")
    reset()
    for n in [8, 6, 5, 11, 11, 11]:            # a real week, Saturday peaks
        run(dallasites101=n, ticketmaster=500)
    check("Sunday's 11 -> 4 does not alarm", run(dallasites101=4, ticketmaster=538), [])
    check("the old previous-run rule provably would have",
          11 >= 10 and 4 < 11 * 0.5, True)

    print("worst case: history is nothing but Saturday peaks")
    reset()
    for _ in range(F.SOURCE_HISTORY_RUNS):
        run(dallasites101=11)
    check("11 -> 4 still quiet, on the raised floor alone",
          run(dallasites101=4), [])
    check("but 11 -> 0 still alarms", len(run(dallasites101=0)), 1)


def test_zero_rule():
    print("a source going dark")
    reset()
    for n in [11, 8, 11, 9]:
        run(dallasites101=n)
    check("zero alarms", len(run(dallasites101=0)), 1)
    check("and KEEPS alarming on run 2 (the self-silencing bug)",
          len(run(dallasites101=0)), 1)
    check("and on run 3", len(run(dallasites101=0)), 1)
    check("the old rule went silent on run 2", 0 >= F.SOURCE_ZERO_FLOOR, False)

    print("the founding 2026-09-11 Prekindle incident")
    reset()
    for n in [68, 63, 70, 65, 63]:
        run(prekindle=n, ticketmaster=538)
    # All six Prekindle pages 404'd; the TOTAL stayed at 97% of the previous
    # night, so COLLAPSE_GUARD_RATIO saw nothing.
    check("prekindle -> 0 alarms", len(run(prekindle=0, ticketmaster=538)), 1)


def test_drop_rule():
    print("partial drops")
    reset()
    for n in [538, 520, 545, 530]:
        run(ticketmaster=n)
    check("a big source halving alarms", len(run(ticketmaster=120)), 1)

    reset()
    for n in [1, 0, 1, 1]:
        run(seated=n)
    check("seated at 0-1 never alarms (below the zero floor)", run(seated=0), [])


def test_bookkeeping():
    print("history bookkeeping")
    reset()
    F.SOURCE_COUNTS_FILE.write_text(json.dumps({
        "date": "2026-09-13",
        "counts": {"dallasites101": 4, "ticketmaster": 538},
        "alarms": ["dallasites101: 11 -> 4 (below 50% of the previous run)"],
    }, indent=1) + "\n")
    hist = F._load_source_history()
    check("the pre-history file shape migrates to one entry", len(hist), 1)
    check("its counts survive", hist[0]["counts"]["dallasites101"], 4)
    check("first run after migrating is clean",
          run(dallasites101=8, ticketmaster=540), [])
    check("history now holds 2 runs",
          len(json.loads(F.SOURCE_COUNTS_FILE.read_text())["history"]), 2)

    reset()
    for _ in range(F.SOURCE_HISTORY_RUNS * 2):
        run(ticketmaster=500)
    check(f"history caps at SOURCE_HISTORY_RUNS ({F.SOURCE_HISTORY_RUNS})",
          len(json.loads(F.SOURCE_COUNTS_FILE.read_text())["history"]),
          F.SOURCE_HISTORY_RUNS)
    check("a new source never alarms on its first run",
          run(ticketmaster=500, brandnew=0), [])

    reset()
    F.SOURCE_COUNTS_FILE.write_text("{ this is not json")
    check("a corrupt file degrades to an empty history", F._load_source_history(), [])
    check("and the run still completes", run(ticketmaster=500), [])


# ---------------------------------------------------------- parse-rate checks
def _rss(n):
    items = "".join(f"<item><link>https://www.dallasites101.com/event/e{i}/</link></item>"
                    for i in range(n))
    return f"<rss><channel>{items}</channel></rss>"


def _page(name, date, city="Dallas", region="TX", jsonld=True, good_json=True,
          typ="Event"):
    if not jsonld:                      # what a redesign looks like
        return "<html><body>no structured data here</body></html>"
    body = json.dumps({
        "@type": typ, "name": name, "startDate": f"{date}T19:00:00",
        "location": {"name": "Test Venue",
                     "address": {"addressLocality": city, "addressRegion": region}},
        "url": "https://www.dallasites101.com/event/x/",
    })
    if not good_json:
        body = body[:-5]                # truncated, json.loads raises
    return ('<html><script type="application/ld+json">' + body + "</script>"
            '<script>var time = "7:00 PM to 10:00 PM";</script></html>')


def _fetch_with(pages):
    """Run fetch_dallasites101 against a fixture, with no network and no sleep."""
    rss = _rss(len(pages))
    urls = [f"https://www.dallasites101.com/event/e{i}/" for i in range(len(pages))]
    served = dict(zip(urls, pages))

    def fake(url, *a, **k):
        if url == F.DALLASITES101_RSS:
            return rss
        return served[url]

    real_sleep = F.time.sleep
    F.http_text, F.time.sleep = fake, lambda *_: None
    try:
        start = datetime(2026, 9, 13)
        return F.fetch_dallasites101(start, datetime(2026, 10, 13))
    finally:
        F.http_text, F.time.sleep = _real_http_text, real_sleep


def test_parse_health():
    print("parse-failure rate (what a yield cannot see)")

    reset()
    rows = _fetch_with([_page(f"Show {i}", "2026-09-20") for i in range(10)])
    check("ten healthy pages -> ten rows", len(rows), 10)
    check("and no fault", F._SOURCE_FAULTS, [])

    print("  the real Sunday shape: pages parse, events are simply past")
    reset()
    rows = _fetch_with([_page(f"Sat {i}", "2026-09-12") for i in range(26)]
                       + [_page(f"Up {i}", "2026-09-20") for i in range(4)])
    check("only the 4 upcoming are kept", len(rows), 4)
    check("a past-dated event is a FILTER, not a fault", F._SOURCE_FAULTS, [])

    print("  the break a yield hides: template changed, nothing parses")
    reset()
    rows = _fetch_with([_page(f"Show {i}", "2026-09-20", jsonld=False)
                        for i in range(30)])
    check("no rows", len(rows), 0)
    check("and a fault IS raised", len(F._SOURCE_FAULTS), 1)
    print(f"        -> {F._SOURCE_FAULTS[0]}")
    check("it reaches the alarms that turn the job red",
          len(F.check_source_health({"dallasites101": 0})) >= 1, True)

    print("  a partial break still faults, while the yield looks plausible")
    reset()
    rows = _fetch_with([_page(f"Show {i}", "2026-09-20", jsonld=False)
                        for i in range(22)]
                       + [_page(f"Ok {i}", "2026-09-20") for i in range(8)])
    check("yield of 8 looks like an ordinary night", len(rows), 8)
    check("but the parse rate gives it away", len(F._SOURCE_FAULTS), 1)

    print("  malformed JSON and non-Event pages count as faults")
    reset()
    _fetch_with([_page(f"Show {i}", "2026-09-20", good_json=False) for i in range(6)])
    check("bad JSON faults", len(F._SOURCE_FAULTS), 1)
    reset()
    _fetch_with([_page(f"Show {i}", "2026-09-20", typ="Article") for i in range(6)])
    check("wrong @type faults", len(F._SOURCE_FAULTS), 1)

    print("  filters and small samples never fault")
    reset()
    rows = _fetch_with([_page(f"Show {i}", "2026-09-20", city="Austin")
                        for i in range(20)])
    check("off-area is a filter, not a fault", (len(rows), F._SOURCE_FAULTS), (0, []))
    reset()
    _fetch_with([_page(f"Show {i}", "2026-09-20", jsonld=False) for i in range(3)])
    check(f"under SOURCE_UNPARSED_MIN ({F.SOURCE_UNPARSED_MIN}) never faults",
          F._SOURCE_FAULTS, [])


def main():
    F.SOURCE_COUNTS_FILE = pathlib.Path(tempfile.mkdtemp()) / "source-counts.json"
    for t in (test_sunday_false_alarm, test_zero_rule, test_drop_rule,
              test_bookkeeping, test_parse_health):
        t()
        print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
