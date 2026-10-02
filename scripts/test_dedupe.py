#!/usr/bin/env python3
"""Tests for dedupe() in fetch_events.py -- the highest-risk code in the repo.

    python3 scripts/test_dedupe.py

Stdlib only and no network, like test_source_health.py. js/sources.js mirrors
this logic; these cases are the ones both layers must agree on (the browser
side is checked by running its _sameEvent() over the same rows in the preview,
see CLAUDE.md "Dedupe").
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fetch_events as F                                        # noqa: E402

FAILURES = []


def check(label, got, want):
    if got == want:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")
        FAILURES.append(label)


def ev(name, area, time="7:00 PM", date="2026-10-06"):
    return {"name": name, "area": area, "time": time, "date": date}


def kept(*rows):
    return [(r["name"], r["area"]) for r in F.dedupe(list(rows))]


def test_identical_title_pass():
    print("pass 1: identical title needs a compatible venue (2026-10-02)")
    check("same show, same venue -> one",
          len(kept(ev("Jazz Night", "Sandaga 813, Dallas"),
                   ev("Jazz Night", "Sandaga 813, Dallas"))), 1)
    check("venue given more fully by one source -> one",
          len(kept(ev("Mo Amer", "Majestic Theatre, Dallas"),
                   ev("Mo Amer", "Majestic Theatre - Dallas, Dallas"))), 1)

    rows = kept(ev("National Night Out", "Cedar Hill", "6:00 PM"),
                ev("National Night Out", "McKinney", "See details"))
    check("same title in two cities -> two (was merged before the fix)", len(rows), 2)
    check("and the split is recorded for the nightly log",
          [(a["area"], b["area"]) for a, b in F._DEDUPE_VENUE_SPLITS],
          [("Cedar Hill", "McKinney")])
    check("two library branches' Story Time -> two",
          len(kept(ev("Baby Bounce & Read", "South Garland Library, Garland", "10:30 AM"),
                   ev("Baby Bounce & Read", "West Garland Library, Garland", "10:30 AM"))), 2)
    check("an unknown venue never merges",
          len(kept(ev("Trivia", ""), ev("Trivia", ""))), 2)

    print("  the true duplicates pass 1 used to hide need an alias, not a looser rule")
    check("one venue under two spellings -> one, via venue-aliases.json",
          len(kept(ev("Shawn James", "Tannahill's Music Hall & Lounge, Fort Worth"),
                   ev("Shawn James", "Tannahills Tavern and Music Hall, Fort Worth"))), 1)
    check("Punch Line Dallas is Punch Line Irving",
          len(kept(ev("Comedy Showcase", "Punch Line Irving, Irving"),
                   ev("Comedy Showcase", "Punch Line Dallas - Irvine, Irving"))), 1)
    check("'Improv Comedy Club - Arlington' is Arlington Improv (found by the "
          "first nightly run: 10 duplicate listings)",
          len(kept(ev("Damon Williams", "Arlington Improv, Arlington"),
                   ev("Damon Williams", "Improv Comedy Club - Arlington, Arlington"))), 1)
    check("but that alias is keyed WITH its city, so the Addison club is not hijacked",
          F._venue_tokens("Improv Comedy Club - Addison, Addison"), {"improv", "comedy", "club"})
    check("and a variant without a suffix still matches as before",
          F._venue_tokens("Trees - Dallas, Dallas"), F._venue_tokens("Trees, Dallas"))
    check("but two rooms in one building stay apart",
          len(kept(ev("Late Show", "House of Blues Dallas, Dallas"),
                   ev("Late Show", "Cambridge Room at House of Blues, Dallas"))), 2)


def test_times_and_dates():
    print("times and dates still separate what they always did")
    check("2 PM matinee and 8 PM show at one venue -> two",
          len(kept(ev("Hamilton", "Music Hall at Fair Park, Dallas", "2:00 PM"),
                   ev("Hamilton", "Music Hall at Fair Park, Dallas", "8:00 PM"))), 2)
    check("same show on two dates -> two",
          len(kept(ev("Tea Around Town", "Downtown Dallas", date="2026-10-06"),
                   ev("Tea Around Town", "Downtown Dallas", date="2026-10-07"))), 2)


def test_fuzzy_pass_unchanged():
    print("pass 2 (_same_event) is unchanged")
    check("different titles, shared word, same venue and time -> one",
          len(kept(ev("Texas Rangers vs. Chicago White Sox", "Globe Life Field, Arlington"),
                   ev("White Sox at Rangers", "Globe Life Field, Arlington"))), 1)
    check("two comedians in one room at one time -> two",
          len(kept(ev("Jackie Fabulous", "Hyena's Comedy Club, Dallas"),
                   ev("Cipha Sounds", "Hyena's Comedy Club, Dallas"))), 2)


def main():
    for t in (test_identical_title_pass, test_times_and_dates, test_fuzzy_pass_unchanged):
        t()
        print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
