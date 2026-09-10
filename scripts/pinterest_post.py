#!/usr/bin/env python3
"""Pins each hub and venue page to Pinterest, once, as an evergreen board.

    python scripts/pinterest_post.py check            token, board, what's left
    python scripts/pinterest_post.py run --limit 5    pin up to 5 new targets

WHY THIS IS NOT social_post.py WITH A THIRD PLATFORM
----------------------------------------------------
Pinterest is not a feed, it is a search index. A pin has a multi-year half
life, which makes the daily "DFW TODAY - THU SEP 10" card exactly the wrong
thing to put on a board: it is stale tomorrow and stale forever after, and a
board full of dead dates is worth less than no board.

So the unit here is a PLACE, not a day. One pin per district, city, "free
events" and venue page -- "Things to Do in Deep Ellum" stays true, and the
page it links to refreshes itself every night. That means this posts ONCE per
target and then never again, which is a different shape from three-a-day and
belongs in its own script and its own weekly workflow.

It also means the board grows with the site on its own: a venue that clears
VENUE_MIN_EVENTS next month becomes a new target the next time this runs.

NO BUILD/PUBLISH SPLIT
----------------------
Unlike Instagram, Pinterest's create-pin endpoint accepts the image bytes
directly (media_source.source_type = "image_base64"), so there is no
commit-to-Pages-then-publish dance and no _wait_for_pages(). Nothing this
script renders is committed -- the pins live on Pinterest, not in the repo,
which also keeps a public repo from growing an image a week forever.

ONE SECRET
----------
    PINTEREST_ACCESS_TOKEN    OAuth token for the business account.
The board id is NOT configured: it is resolved by name at runtime, the same
reasoning that keeps the Instagram user id out of the secrets in
social_post.py -- one less thing to rotate, and a preflight that can tell
"board missing" from "wrong id".

TOKEN EXPIRY IS THE THING THAT WILL BREAK THIS. Pinterest access tokens are
short-lived unless minted through the refresh flow; there is no equivalent of
Meta's never-expiring system-user token. `check` is the preflight to run after
any credential change.

VERIFIED vs DOCUMENTED. The three endpoints below were probed on 2026-09-10
and all exist (they answer 401 to a bad token, not 404). The request BODIES
are from Pinterest's v5 docs and have not been exercised against a real token
-- the same doc/reality gap that cost a wrong guess on Instagram's carousel
`children` parameter, so treat the first live run as the test and read the
error text rather than assuming.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_events as F                                     # noqa: E402
import social_card                                           # noqa: E402

ROOT = F.ROOT
SITE = F.SITE
API = "https://api.pinterest.com/v5"
PINNED_FILE = ROOT / "social" / "pinned.json"

# The board everything goes to. Created by hand in the Pinterest UI; `check`
# says so by name if it is missing, because a script cannot guess which of
# someone's boards was meant.
BOARD_NAME = "Things to Do in DFW"

_UNCONFIGURED = (
    "PINTEREST_ACCESS_TOKEN is not set — skipping. Standing up the Pinterest\n"
    "side is manual, UI-only work (business account, app, OAuth token, and a\n"
    f"board named {BOARD_NAME!r}); failing red every week through that window\n"
    "trains you to ignore the alarm. Set the secret to turn this on."
)


# The same label map /submit/ renders and social_post.py's cards use, taken
# from fetch_events.py rather than spelled out a third time.
_CATEGORY_LABEL = dict(F._SUBMIT_CATEGORIES)


class PinError(RuntimeError):
    pass


# --------------------------------------------------------------- API client
def _api(path: str, token: str, body: dict | None = None, method: str = "GET"):
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": F.UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            msg = json.loads(raw).get("message") or raw
        except ValueError:
            msg = raw
        # 401 here is almost always the token having expired rather than
        # anything about this request -- see TOKEN EXPIRY above.
        hint = " (token expired or revoked?)" if exc.code == 401 else ""
        raise PinError(f"{method} {path} -> HTTP {exc.code}{hint}: {msg[:300]}") from None


def _board_id(token: str) -> str:
    """Resolve BOARD_NAME to an id, or raise with the manual fix."""
    boards = _api("/boards?page_size=100", token).get("items", [])
    for b in boards:
        if (b.get("name") or "").strip().lower() == BOARD_NAME.lower():
            return b["id"]
    have = ", ".join(repr(b.get("name")) for b in boards) or "none"
    raise PinError(
        f"No board named {BOARD_NAME!r} on this account (found: {have}). "
        f"Create it in the Pinterest UI — a script should not guess which "
        f"existing board was meant, and creating one silently would scatter "
        f"pins across a board you use for something else.")


# ----------------------------------------------------------------- targets
def _tagline(events) -> str:
    """The categories this place actually books, most common first. Read from
    the feed rather than written by hand so it cannot drift from reality."""
    counts = {}
    for e in events:
        c = e.get("category")
        if c:
            counts[c] = counts.get(c, 0) + 1
    labels = [_CATEGORY_LABEL.get(c, c.title())
              for c, _n in sorted(counts.items(), key=lambda kv: -kv[1])[:3]]
    return " · ".join(labels)


def targets() -> list[dict]:
    """Every page worth a pin, best first.

    Districts and cities lead because they match how people actually search
    Pinterest ("things to do in Dallas"), venues follow. A hub with no
    upcoming events is skipped entirely: those pages carry noindex until they
    have something (see _hub_html in fetch_events.py), and pinning boilerplate
    is how a board stops being worth following.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = [e for e in json.loads((ROOT / "live-events.json").read_text())
            if e["date"] >= today]

    out = []

    def add(key, path, title, place, evs, kicker="THINGS TO DO IN"):
        if not evs or not (ROOT / path / "index.html").exists():
            return
        out.append({"key": key, "path": path, "title": title, "place": place,
                    "kicker": kicker, "n": len(evs), "tagline": _tagline(evs)})

    for slug, label, _m in F.DISTRICTS:
        add(f"district/{slug}", f"district/{slug}", label, "Dallas–Fort Worth",
            [e for e in rows if F._slugify_matches(e["area"]) == slug])

    by_city = {}
    for e in rows:
        city = e.get("city") or F._city_of(e.get("area", ""))
        if city:
            by_city.setdefault(F._city_slug(city), (city, []))[1].append(e)
    for slug, (city, evs) in by_city.items():
        add(f"city/{slug}", f"city/{slug}", city, f"{city}, TX", evs)

    add("free-events", "free-events", "Free Things to Do in DFW",
        "Dallas–Fort Worth", [e for e in rows if e.get("cost") == 0], kicker="")

    by_venue = {}
    for e in rows:
        venue, _s, city = F._split_area(e["area"])
        if venue:
            by_venue.setdefault(F._venue_slug(venue), (venue, city, []))[2].append(e)
    for slug, (venue, city, evs) in sorted(by_venue.items(), key=lambda kv: -len(kv[1][2])):
        add(f"venue/{slug}", f"venue/{slug}", venue,
            f"{city}, TX" if city else "Dallas–Fort Worth", evs, kicker="UPCOMING AT")

    return out


def _copy(t: dict) -> tuple[str, str, str]:
    """Pin title, description and alt text. Pinterest search reads the
    description, so it names the place and the city in plain words -- and
    claims nothing the linked page does not already show."""
    where = t["title"]
    kinds = t["tagline"].lower() or "events"
    title = (f"Things to Do in {where}" if t["kicker"] != "UPCOMING AT"
             else f"Upcoming Shows at {where}")
    desc = (f"{kinds.capitalize()} in {where}, Dallas–Fort Worth. "
            f"{t['n']} upcoming event{'s' if t['n'] != 1 else ''}, refreshed "
            f"every night — see what's on tonight and this weekend at "
            f"letsdoitdallas.com.")
    alt = (f"Lets Do It Dallas — {title}, on a black card with the "
           f"Reunion Tower mark.")
    return title, desc[:800], alt[:500]


# --------------------------------------------------------------------- log
def load_pinned() -> dict:
    if not PINNED_FILE.exists():
        return {}
    try:
        return json.loads(PINNED_FILE.read_text())
    except ValueError:
        print("  pinned.json unreadable; treating as empty", file=sys.stderr)
        return {}


def save_pinned(log: dict) -> None:
    PINNED_FILE.parent.mkdir(parents=True, exist_ok=True)
    PINNED_FILE.write_text(json.dumps(log, indent=1, sort_keys=True) + "\n")


# ------------------------------------------------------------------- modes
def _token(allow_unconfigured: bool = False) -> str | None:
    tok = (os.environ.get("PINTEREST_ACCESS_TOKEN") or "").strip()
    if not tok:
        if allow_unconfigured:
            return None
        sys.exit("PINTEREST_ACCESS_TOKEN is not set.")
    return tok


def cmd_check(_args) -> int:
    tok = _token()
    me = _api("/user_account", tok)
    print(f"account: @{me.get('username')} ({me.get('account_type')})")
    boards = _api("/boards?page_size=100", tok).get("items", [])
    print(f"boards : {len(boards)} — " + ", ".join(repr(b.get('name')) for b in boards))
    bid = _board_id(tok)
    print(f"target : {BOARD_NAME!r} -> {bid}")
    log, all_t = load_pinned(), targets()
    todo = [t for t in all_t if t["key"] not in log]
    print(f"targets: {len(all_t)} pinnable, {len(log)} already pinned, {len(todo)} left")
    for t in todo[:8]:
        print(f"   next: {t['key']:38s} {t['n']:>3} events  {t['tagline']}")
    return 0


def cmd_run(args) -> int:
    tok = _token(allow_unconfigured=True)
    if tok is None:
        print(_UNCONFIGURED)
        return 0

    log = load_pinned()
    todo = [t for t in targets() if t["key"] not in log][:args.limit]
    if not todo:
        print("nothing new to pin; the board is caught up")
        return 0

    board = _board_id(tok)
    failures = []
    with tempfile.TemporaryDirectory(prefix="pins-") as tmp:
        tmp = Path(tmp)
        for t in todo:
            title, desc, alt = _copy(t)
            img = tmp / f"{t['key'].replace('/', '-')}.jpg"
            social_card.render_pin(t["title"], t["tagline"], t["place"], img,
                                   Path(args.font_cache), kicker=t["kicker"])
            social_card.verify_pin(img)
            if args.dry_run:
                print(f"dry run: would pin {t['key']} — {title}")
                continue
            body = {
                "board_id": board,
                "title": title[:100],
                "description": desc,
                "alt_text": alt,
                "link": f"{SITE}/{t['path']}/",
                "media_source": {
                    "source_type": "image_base64",
                    "content_type": "image/jpeg",
                    "data": base64.b64encode(img.read_bytes()).decode(),
                },
            }
            try:
                pin = _api("/pins", tok, body, method="POST")
            except PinError as exc:
                failures.append(f"{t['key']}: {exc}")
                continue
            log[t["key"]] = {"pin": pin.get("id"),
                             "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            # Saved after EACH pin, not at the end: a run that dies halfway
            # must not re-pin what it already posted. Same reasoning as
            # social_post.py writing posted.json after each platform.
            save_pinned(log)
            print(f"pinned {t['key']} -> {pin.get('id')}")

    for line in failures:
        print(line, file=sys.stderr)
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="token, board and remaining targets")
    r = sub.add_parser("run")
    # Small on purpose. The whole catalogue in one burst looks like a bot to
    # Pinterest and to a human scrolling the board; a few a week fills it over
    # a couple of months and then quietly stops.
    r.add_argument("--limit", type=int, default=5)
    r.add_argument("--font-cache", default=".font-cache")
    r.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        return {"check": cmd_check, "run": cmd_run}[args.cmd](args)
    except PinError as exc:
        # A clean line, not a traceback: every PinError already carries the
        # endpoint, the status and Pinterest's own message, which is the whole
        # of what is useful when a token expires at 3am.
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
