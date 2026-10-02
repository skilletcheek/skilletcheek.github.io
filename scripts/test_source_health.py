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
    F._SUB_YIELDS.clear()


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


# ------------------------------------------- the other scrapers (2026-10-02)
WINDOW = (datetime(2026, 10, 1), datetime(2026, 10, 31))


def _with_feeds(cfg, served, fn):
    """Run fetcher `fn` against a temp feeds.json and canned HTTP responses.
    A URL missing from `served` raises, which is what a failed fetch is."""
    real_feeds, real_sleep = F.FEEDS_FILE, F.time.sleep
    path = pathlib.Path(tempfile.mkdtemp()) / "feeds.json"
    path.write_text(json.dumps(cfg))

    def fake(url, *a, **k):
        if url not in served:
            raise OSError("HTTP Error 404: Not Found")
        return served[url]

    F.FEEDS_FILE, F.http_text, F.time.sleep = path, fake, lambda *_: None
    try:
        return fn(*WINDOW)
    finally:
        F.FEEDS_FILE, F.http_text, F.time.sleep = real_feeds, _real_http_text, real_sleep


CP_CITIES = ["Garland", "Cedar Hill", "Grapevine", "McKinney", "Frisco", "Lancaster"]


def _cp_feed(n, date="October 12, 2026", title="Story Time", moved=False):
    label = "When:" if moved else "Event date:"       # what a template change looks like
    item = ("<item><title>{t} {i}</title><link>https://x.test/{i}</link><description>"
            "<strong>{label}</strong> {d}<br><strong>Event Time:</strong> 10:00 AM"
            "<br><strong>Location:</strong> 100 Main St<br>Garland, TX 75040"
            "<strong>Description:</strong> fun</description></item>")
    return "<rss>" + "".join(item.format(t=title, i=i, label=label, d=date)
                             for i in range(n)) + "</rss>"


def _cp(feeds):
    cfg = {"civicplus_sites": [{"site": f"https://{c.replace(' ', '')}.test", "city": c}
                               for c in CP_CITIES],
           "civicplus_skip": ["council"]}
    served = {f"https://{c.replace(' ', '')}.test{F.CIVICPLUS_PATH}": body
              for c, body in zip(CP_CITIES, feeds) if body is not None}
    return _with_feeds(cfg, served, F.fetch_civicplus)


def test_civicplus_health():
    print("civicplus: per-city template faults, and the feeds as a whole")
    reset()
    rows = _cp([_cp_feed(8)] * 6)
    check("six healthy feeds -> rows, no fault", (len(rows), F._SOURCE_FAULTS), (48, []))
    reset()
    _cp([_cp_feed(8, date="September 1, 2026")] * 3 + [_cp_feed(8, title="City Council")] * 3)
    check("past and municipal items are filters", F._SOURCE_FAULTS, [])
    reset()
    rows = _cp([_cp_feed(8, moved=True)] + [_cp_feed(8)] * 5)
    check("one city's template moved -> 40 rows still look fine", len(rows), 40)
    check("but that city faults", [f.split(":")[0] for f in F._SOURCE_FAULTS],
          ["civicplus (Garland)"])
    print(f"        -> {F._SOURCE_FAULTS[0]}")
    reset()
    _cp([None] + [_cp_feed(8)] * 5)
    check("one city feed failing to fetch is a blip", F._SOURCE_FAULTS, [])
    reset()
    _cp([None] * 4 + [_cp_feed(8)] * 2)
    check("four of six failing faults", [f.split(":")[0] for f in F._SOURCE_FAULTS],
          ["civicplus"])


def _pk_page(n, date="2026-10-12T20:00:00", jsonld=True):
    if not jsonld:
        return "<html>redesigned</html>"
    evs = [{"name": f"Band {i}", "url": "https://p.test/e", **({"startDate": date} if date else {})}
           for i in range(n)]
    return '<script type="application/ld+json">' + json.dumps(evs) + "</script>"


def _pk(pages):
    cfg = {"prekindle_pages": [{"slug": f"v{i}", "venue": f"Venue {i}", "area": f"Venue {i}, Dallas"}
                               for i in range(len(pages))]}
    served = {f"https://www.prekindle.com/events/v{i}": p for i, p in enumerate(pages)
              if p is not None}
    return _with_feeds(cfg, served, F.fetch_prekindle)


def test_prekindle_health():
    print("prekindle: venue pages and the events listed on them")
    reset()
    rows = _pk([_pk_page(5)] * 6)
    check("healthy -> 30 rows, no fault", (len(rows), F._SOURCE_FAULTS), (30, []))
    reset()
    _pk([_pk_page(5, date="2026-09-01T20:00:00")] * 6)
    check("past events are a filter", F._SOURCE_FAULTS, [])
    reset()
    _pk([_pk_page(5, jsonld=False)] * 4 + [_pk_page(5)] * 2)
    check("pages without JSON-LD fault", F._SOURCE_FAULTS[0].startswith(
        "prekindle: 4 of 6 venue pages"), True)
    reset()
    rows = _pk([_pk_page(5, date=None)] * 6)
    check("events that lost startDate fault", (len(rows), len(F._SOURCE_FAULTS)), (0, 1))
    print(f"        -> {F._SOURCE_FAULTS[0]}")


def _singles_page(n, date="2026-10-04T16:00:00-06:00", city="Dallas"):
    org = '<script type="application/ld+json">{"@type": "Organization", "name": "x"}</script>'
    ev = lambda i: json.dumps({
        "@type": "Event", "name": f"Speed Dating {i}",
        **({"startDate": date} if date else {}),
        "location": {"name": "Bar", "address": {"addressLocality": city, "addressRegion": "TX"}}})
    return org + "".join(f'<script type="application/ld+json">{ev(i)}</script>' for i in range(n))


def _singles(page):
    return _with_feeds({"singles_pages": [{"url": "https://s.test/", "source": "S"}]},
                       {"https://s.test/": page}, F.fetch_singles_pages)


def test_singles_health():
    print("singles: the Event objects on one page")
    reset()
    rows = _singles(_singles_page(6))
    check("healthy, and the Organization block is not counted",
          (len(rows), F._SOURCE_FAULTS), (6, []))
    reset()
    rows = _singles(_singles_page(6, date="2026-10-4T16:00:00-06:00"))
    check("the 2026-10-02 unpadded date parses", [r["date"] for r in rows][:1], ["2026-10-04"])
    reset()
    _singles(_singles_page(6, city="Austin"))
    check("off-area is a filter", F._SOURCE_FAULTS, [])
    reset()
    _singles(_singles_page(6, date="soon"))
    check("unparseable startDate faults", len(F._SOURCE_FAULTS), 1)


def _ics(n, dtstart="DTSTART:20261012T190000", summary=True):
    ev = ("BEGIN:VEVENT\r\n" + (dtstart + "\r\n" if dtstart else "")
          + ("SUMMARY:Show\r\n" if summary else "") + "END:VEVENT\r\n")
    return "BEGIN:VCALENDAR\r\n" + ev * n + "END:VCALENDAR\r\n"


def _ics_run(body):
    return _with_feeds({"ics_feeds": [{"url": "https://c.test/x.ics", "area": "Dallas"}]},
                       {"https://c.test/x.ics": body}, F.fetch_ics_feeds)


def test_ics_health():
    print("ics_feeds: VEVENTs per feed")
    reset()
    check("healthy -> rows, no fault", (len(_ics_run(_ics(6))), F._SOURCE_FAULTS), (6, []))
    reset()
    _ics_run(_ics(6, dtstart="DTSTART:20250101T190000"))
    check("out of window is a filter", F._SOURCE_FAULTS, [])
    reset()
    _ics_run(_ics(6, dtstart=None))
    check("no DTSTART faults", [f.split(":")[0] for f in F._SOURCE_FAULTS], ["ics_feeds (c.test)"])
    reset()
    _ics_run(_ics(6, summary=False))
    check("no SUMMARY faults", len(F._SOURCE_FAULTS), 1)


def test_ics_per_feed_yield():
    """The blind spot two feeds created: UNT kept the ics_feeds TOTAL up, so
    What's Up Fort Worth could go dark without the total ever reaching zero."""
    print("ics_feeds: each feed's yield has its own baseline")
    cfg = {"ics_feeds": [{"url": "https://a.test/x.ics", "area": "Fort Worth"},
                         {"url": "https://b.test/y.ics", "area": "Denton"}]}

    def night(served):
        F._SUB_YIELDS.clear()
        F._SOURCE_FAULTS.clear()
        rows = _with_feeds(cfg, served, F.fetch_ics_feeds)
        counts = {"ics_feeds": len(rows)}
        counts.update(F._SUB_YIELDS)            # exactly what main() does
        return counts, F.check_source_health(counts)

    reset()
    both = {"https://a.test/x.ics": _ics(25), "https://b.test/y.ics": _ics(30)}
    for _ in range(3):
        counts, alarms = night(both)
    check("healthy nights record the total and each feed",
          counts, {"ics_feeds": 55, "ics_feeds (a.test)": 25, "ics_feeds (b.test)": 30})
    check("and raise nothing", alarms, [])

    counts, alarms = night({"https://b.test/y.ics": _ics(30)})
    check("a feed that never answers records 0, not a missing key",
          counts.get("ics_feeds (a.test)"), 0)
    check("the dead feed alarms", [a.split(":")[0] for a in alarms], ["ics_feeds (a.test)"])
    print(f"        -> {alarms[0]}")
    check("while the total (55 -> 30) does not", any(a.startswith("ics_feeds:") for a in alarms),
          False)

    counts, alarms = night({"https://b.test/y.ics": _ics(30)})
    check("and it stays red the next night", [a.split(":")[0] for a in alarms],
          ["ics_feeds (a.test)"])


def main():
    F.SOURCE_COUNTS_FILE = pathlib.Path(tempfile.mkdtemp()) / "source-counts.json"
    for t in (test_sunday_false_alarm, test_zero_rule, test_drop_rule,
              test_bookkeeping, test_parse_health, test_civicplus_health,
              test_prekindle_health, test_singles_health, test_ics_health,
              test_ics_per_feed_yield):
        t()
        print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
