#!/usr/bin/env python3
"""Posts one "tonight in DFW" pick-list a day to the Facebook Page and the
linked Instagram Business account. Driven by .github/workflows/social-post.yml.

Deliberately a SEPARATE script and a separate workflow from fetch_events.py:
a Meta outage, an expired token or a rate limit must never be able to block
the nightly event refresh, and a collapsed feed must never post garbage. This
one only ever READS live-events.json.

    python scripts/social_post.py check                 credentials + linkage
    python scripts/social_post.py build  --plan P       pick, render, write P
    python scripts/social_post.py publish --plan P      post it, log it

build and publish are split because Instagram cannot be handed image bytes.
Its Content Publishing API takes an `image_url` that Meta's servers fetch
themselves, so the card has to be committed and live on GitHub Pages BEFORE
the container call. The workflow therefore runs build -> git push -> publish,
and publish blocks on the URL actually going live (_wait_for_pages).

Facebook has no such constraint -- /{page-id}/photos accepts a multipart
upload -- so the Facebook post never depends on Pages having deployed. It is
posted first, for that reason.

ENVIRONMENT (both from repo secrets, never from a file in this repo):
    META_SYSTEM_USER_TOKEN   Business system-user token. Long-lived by
                             default, unlike the 60-day Page token you get
                             from the Graph Explorer.
    META_PAGE_ID             Numeric Facebook Page id.
The Instagram user id is NOT configured: it is read from the Page node at
runtime (`instagram_business_account`), which means one less secret to rotate
and a preflight that can tell "not linked" from "wrong id".

RATE LIMITS, checked against Meta's docs on 2026-08-28: Instagram allows 100
API-published posts per rolling 24 hours, queryable at
/{ig-id}/content_publishing_limit, which `check` prints. This posts once.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Reusing the aggregator rather than re-deriving its slug rules. CLAUDE.md's
# "two-layer mirrors" section is about logic that already exists twice and has
# drifted; a THIRD copy of district/venue resolution is exactly the failure it
# describes. fetch_events.py is import-safe -- its work is behind a
# __main__ guard -- and none of the names used here touch the network or the
# API keys this machine does not have.
import fetch_events as F                                     # noqa: E402
import social_card                                           # noqa: E402

ROOT = F.ROOT
SITE = F.SITE
GRAPH = "https://graph.facebook.com/v26.0"      # latest as of 2026-07-29

CARD_DIR = ROOT / "social" / "cards"
POSTED_FILE = ROOT / "social" / "posted.json"

# Cards are committed so Pages can serve them, so they accumulate in the
# working tree forever. Two months is well past any window in which a link
# still matters, and Instagram copies the image onto its own CDN at publish
# time -- pruning a card does not blank out a live post.
KEEP_DAYS = 60
PICKS = 3


# ------------------------------------------------------------ graph client
class GraphError(RuntimeError):
    """A Graph API error with Meta's own message, which urllib buries in the
    response body instead of the exception."""


def _graph(path: str, token: str, params: dict | None = None,
           post: bool = False, files: dict | None = None):
    params = dict(params or {})
    params["access_token"] = token
    url = f"{GRAPH}/{path.lstrip('/')}"
    data = headers = None
    if files:
        data, headers = _multipart(params, files)
    elif post:
        data = urllib.parse.urlencode(params).encode()
    else:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method="POST" if (post or files) else "GET")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:
            err = json.loads(body)["error"]
            detail = (f"{err.get('type')} code={err.get('code')}"
                      f"/{err.get('error_subcode')}: {err.get('message')}")
        except (ValueError, KeyError):
            detail = body[:500]
        raise GraphError(f"{exc.code} on {path} -- {detail}") from None


def _multipart(fields: dict, files: dict) -> tuple[bytes, dict]:
    """Hand-rolled multipart/form-data so Facebook can take the card as bytes.

    stdlib has no multipart encoder and this repo does not add a dependency
    for 20 lines. Uploading bytes is what lets the Facebook post go out even
    when GitHub Pages has not deployed the card yet.
    """
    boundary = uuid.uuid4().hex
    out = bytearray()
    for key, value in fields.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="{key}"\r\n\r\n{value}\r\n').encode()
    for key, path in files.items():
        path = Path(path)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        out += (f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="{key}"; filename="{path.name}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n").encode()
        out += path.read_bytes() + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), {"Content-Type": f"multipart/form-data; boundary={boundary}",
                        "Content-Length": str(len(out))}


def _node_kind(token: str, node_id: str) -> str | None:
    """What kind of Graph object is this id? `metadata=1` names the type.

    Used only to turn a confusing failure into an actionable one -- see
    _accounts(). Returns None rather than raising: this runs while already
    handling an error, and a second failure here must not replace the first.
    """
    try:
        meta = _graph(node_id, token, {"metadata": "1", "fields": "id"})
        return (meta.get("metadata") or {}).get("type")
    except GraphError:
        return None


_NO_TOKEN_FIELD = "nonexisting field (access_token)"

# Scopes needed to read a Page token off the Page node and then publish.
# pages_show_list is deliberately NOT here: the 2026-08-28 run listed
# /me/accounts and got a Page token back without it, so demanding it would send
# someone off to regenerate a token that was already fine.
_NEEDED_SCOPES = ("pages_manage_posts", "pages_read_engagement",
                  "instagram_basic", "instagram_content_publish")


def _diagnose(token: str, page_id: str, exc: Exception) -> str:
    """Report what the token can actually see, instead of guessing.

    Graph returns the same "(#100) nonexisting field (access_token)" for a
    wrong id, an unassigned Page and a token missing scopes, and guessing
    between them cost two round trips through the Meta UI. So this asks three
    questions whose answers separate every case, and prints all of them:
    what the id is, what Pages the token can list, and what scopes it carries.

    Note the ids below come back masked as *** in Actions logs when they equal
    the META_PAGE_ID secret -- which is itself the answer to "is the secret the
    same id as the Page the token can see?".
    """
    lines = [f"could not read a Page access token. Raw Graph error: {exc}", ""]

    kind = _node_kind(token, page_id)
    lines.append(f"  META_PAGE_ID resolves to  : {kind or 'UNREADABLE (the token '
                 'cannot see this object at all)'}")

    try:
        pages = _graph("me/accounts", token, {"fields": "id,name,access_token"})
        rows = pages.get("data") or []
        if rows:
            lines.append("  Pages this token can use  :")
            for pg in rows:
                lines.append(f"      {pg.get('id')}  {pg.get('name')}  "
                             f"token={'yes' if pg.get('access_token') else 'NO'}")
        else:
            lines.append("  Pages this token can use  : NONE")
    except GraphError as probe:
        lines.append(f"  Pages this token can use  : listing failed -- {probe}")

    try:
        debug = _graph("debug_token", token, {"input_token": token}).get("data", {})
        scopes = set(debug.get("scopes") or [])
        lines.append(f"  token type                : {debug.get('type')}")
        lines.append(f"  scopes granted            : {', '.join(sorted(scopes)) or 'NONE'}")
        missing = [s for s in _NEEDED_SCOPES if s not in scopes]
        if missing:
            lines.append(f"  MISSING SCOPES            : {', '.join(missing)}")
    except GraphError as probe:
        lines.append(f"  token debug               : failed -- {probe}")

    lines += ["", "  Read it like this:",
              "   * Pages listed as NONE, or the Page missing from the list -> the",
              "     system user does not have the Page. Business settings > Users >",
              "     System users > social-poster > Add assets > Pages.",
              "   * Page listed but token=NO, or scopes missing -> regenerate the",
              "     token with every scope above ticked. Scopes are fixed when the",
              "     token is made; assigning assets afterwards does not add them.",
              "   * META_PAGE_ID resolving to something other than 'page' -> the",
              "     secret holds the wrong id (the App ID is the usual mix-up)."]
    return "\n".join(lines)


def _accounts(token: str, page_id: str) -> dict:
    """Resolve the Page token and the linked Instagram account in one call.

    `instagram_business_account` is ABSENT, not null, when the Instagram
    account is a personal one or is not linked to this Page. That is the one
    failure no code can fix -- the conversion is a manual step in the Meta
    UI -- so it gets its own message rather than a KeyError.
    """
    try:
        node = _graph(page_id, token,
                      {"fields": "name,access_token,instagram_business_account{id,username}"})
    except GraphError as exc:
        # Graph answers "(#100) Tried accessing nonexisting field
        # (access_token)" for BOTH of the plausible setup mistakes, and the
        # message names neither. Ask what the id actually points at, which
        # separates them definitively.
        if _NO_TOKEN_FIELD not in str(exc):
            raise
        raise GraphError(_diagnose(token, page_id, exc)) from None

    if not node.get("access_token"):
        raise GraphError(
            f"Page {page_id} returned no access_token. The system user is "
            f"probably not assigned to this Page: Business Settings > Users > "
            f"System Users > Assign Assets > Pages, with the 'Manage Page' / "
            f"content task ticked.")
    ig = node.get("instagram_business_account")
    if not ig:
        raise GraphError(
            f"Page '{node.get('name')}' has no instagram_business_account. The "
            f"Instagram account is still a personal account, or is not linked "
            f"to this Page. Both are manual fixes in the Meta UI and cannot be "
            f"done over the API: convert the account to Business/Creator in "
            f"the Instagram app (Settings > Account type and tools), then link "
            f"it to the Page in Meta Business Suite > Settings > Accounts > "
            f"Instagram accounts.")
    return {"page_name": node.get("name"), "page_token": node["access_token"],
            "ig_id": ig["id"], "ig_username": ig.get("username")}


# --------------------------------------------------------------- selecting
_TICKETED_HINT = ("ticketmaster", "seatgeek", "ticketweb", "prekindle",
                  "axs.com", "etix", "eventbrite")

# The card's category chips read "Live Music", not "music". Taken from the
# submit form's own list rather than retyped, so the four places that name a
# category -- js/data.js CATEGORIES, /submit/, the modal, and this -- cannot
# drift into three different spellings of "Arts & Museums".
CATEGORY_LABEL = dict(F._SUBMIT_CATEGORIES)


def _today() -> str:
    """The same "today" write_hubs() uses -- UTC, not America/Chicago.

    They must agree or the post advertises picks that /tonight/ does not list.
    UTC and Dallas share a calendar date for every hour except 00:00-05:00
    UTC, so the workflow's schedule is pinned outside that band; see the
    comment on the cron in social-post.yml.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _page_exists(path: str) -> bool:
    """Does the site actually serve this page today?

    Read off disk instead of recomputing VENUE_MIN_EVENTS: the venue pages in
    the checkout ARE the published set, and _prune_stale_venues() deletes the
    ones that went quiet. Linking a post at a URL that 404s is worse than
    linking at /tonight/.
    """
    return (ROOT / path.strip("/") / "index.html").exists()


def _hour(ev: dict) -> int | None:
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*([AaPp])", ev.get("time") or "")
    if not m:
        return None
    hour = int(m.group(1)) % 12
    return hour + (12 if m.group(3).lower() == "p" else 0)


def select_picks(events: list[dict], today: str, recent_venues: set[str]) -> list[dict]:
    """Rank today's events and take up to PICKS, spread across categories.

    The scoring is all proxies, because live-events.json carries no
    attendance, no popularity and no capacity, and `cost` is null on 83% of
    rows -- so "most expensive tier as a proxy for notability" cannot be the
    spine of this. What the site already knows is used instead:

      +4  the venue cleared VENUE_MIN_EVENTS and has its own page. That is the
          site's own existing judgement that this is a real recurring room,
          computed nightly, and it costs nothing to reuse.
      +2  the row resolves to a district hub -- it is somewhere we can name.
      +2  a ticketing host in the url: a ticketed show is an event, where a
          museum's opening hours are not.
      +1  a non-null cost, for the same reason, weaker because it is so sparse.
      +2  starts 17:00-23:00. The post says TONIGHT; a 10 AM exhibit does not.
      -5  the same name appears on 5+ dates in the feed. These are standing
          exhibitions and distillery tours that run all summer -- technically
          on tonight, but nobody's plan for a Friday, and they would otherwise
          dominate every night's post identically.
      -6  this venue was posted in the last 7 days. Without it the two or
          three biggest rooms in DFW take every slot forever. Over the 31
          dates in live-events.json on 2026-08-28 this yielded 46 distinct
          venues across 93 slots, with no venue used more than 5 times.

    Category and venue spread are hard constraints applied after scoring, not
    weights, because a constraint is easier to reason about than a tuned
    number: three arena concerts is a worse post than a concert, a game and a
    festival, whatever the individual scores say.
    """
    runs = {}
    for ev in events:                       # how many distinct dates each name runs
        runs.setdefault(F._norm_name(ev["name"]), set()).add(ev["date"])

    scored = []
    for ev in [e for e in events if e["date"] == today]:
        venue, _street, _city = F._split_area(ev["area"])
        slug = F._venue_slug(venue or "")
        district = F._slugify_matches(ev["area"])
        score = 0
        if venue and F._is_real_venue(venue) and _page_exists(f"venue/{slug}"):
            score += 4
        if district and _page_exists(f"district/{district}"):
            score += 2
        if any(h in (ev.get("url") or "").lower() for h in _TICKETED_HINT):
            score += 2
        if ev.get("cost") is not None:
            score += 1
        hour = _hour(ev)
        if hour is not None and 17 <= hour <= 23:
            score += 2
        if len(runs.get(F._norm_name(ev["name"]), ())) >= 5:
            score -= 5
        if venue and venue.lower() in recent_venues:
            score -= 6
        scored.append((score, ev, venue, slug, district))

    # Sorted by score, then by start time, so an otherwise flat day still
    # produces a stable, sensible ordering instead of feed order.
    scored.sort(key=lambda t: (-t[0], _hour(t[1]) if _hour(t[1]) is not None else 99))

    picks, taken, used_cats, used_venues = [], set(), set(), set()

    def take(entry):
        score, ev, venue, slug, district = entry
        taken.add(id(ev))
        used_cats.add(ev["category"])
        used_venues.add((venue or "").lower())
        picks.append({"event": ev, "score": score, "venue": venue,
                      "venue_slug": slug, "district": district})

    # Three passes, each dropping one constraint. Distinct categories AND
    # distinct venues first; then venues only; then whatever is left.
    #
    # Both constraints have to be able to give way, because the feed is not
    # evenly spread: 'music' alone is 300 of 667 rows, and on a thin Monday a
    # strict rule with no backfill would quietly post one pick instead of
    # three. Venue distinctness outranks category distinctness -- run against
    # every date in live-events.json on 2026-08-28, the two-pass version put
    # Three Links Deep Ellum in slots 01 and 03 of the same card, which reads
    # like a paid placement for one room.
    for distinct_cat, distinct_venue in ((True, True), (False, True), (False, False)):
        for entry in scored:
            if len(picks) == PICKS:
                break
            _score, ev, venue, _slug, _district = entry
            if id(ev) in taken:
                continue
            if distinct_cat and ev["category"] in used_cats:
                continue
            if distinct_venue and (venue or "").lower() in used_venues:
                continue
            take(entry)
    return picks


# ---------------------------------------------------------------- captions
_CAT_TAG = {"music": "dallasmusic", "food": "dallasfood", "arts": "dallasarts",
            "outdoors": "dallasoutdoors", "sports": "dallassports",
            "family": "dallasfamily", "market": "dallasmarkets",
            "nightlife": "dallasnightlife", "festival": "dallasfestivals"}
_BASE_TAGS = ["dallas", "dfw", "fortworth", "dallastx",
              "thingstodoindallas", "dallasevents"]


def _line(pick: dict, index: int) -> str:
    ev = pick["event"]
    return (f"{index:02d} / {ev['name']}\n"
            f"     {ev['time']} · {F._display_area(ev['area'])}")


def compose(picks: list[dict], today: str) -> dict:
    """Facebook and Instagram captions.

    Same voice as the page -- slash kickers, caps, no adjectives (see
    index.html's hero and the hub headings in fetch_events.py). Instagram gets
    hashtags and a bare domain because captions there are not clickable;
    Facebook gets the real URLs.
    """
    stamp = datetime.strptime(today, "%Y-%m-%d")
    head = f"TONIGHT IN DFW — {stamp.strftime('%a %b %d').upper()}"
    body = "\n".join(_line(p, i) for i, p in enumerate(picks, 1))

    top = picks[0]
    deep = None
    if top["venue_slug"] and _page_exists(f"venue/{top['venue_slug']}"):
        deep = f"{SITE}/venue/{top['venue_slug']}/"
    elif top["district"] and _page_exists(f"district/{top['district']}"):
        deep = f"{SITE}/district/{top['district']}/"

    fb = f"{head}\n\n{body}\n\nAll of tonight → {SITE}/tonight/"
    if deep:
        fb += f"\nMore at {top['venue'] or top['district']} → {deep}"

    tags = _BASE_TAGS + [_CAT_TAG[p["event"]["category"]] for p in picks
                         if p["event"]["category"] in _CAT_TAG]
    seen, ordered = set(), []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            ordered.append("#" + tag)
    ig = (f"{head}\n\n{body}\n\nFull list at letsdoitdallas.com/tonight — "
          f"link in bio.\n\n" + " ".join(ordered))

    # 2,200 is Instagram's caption ceiling; three picks land near 500, but a
    # feed row with a pathological name should truncate rather than 400.
    if len(ig) > 2200:
        ig = ig[:2197].rstrip() + "..."
    return {"facebook": fb, "instagram": ig,
            "headline": "Tonight in DFW",
            "datestamp": stamp.strftime("%a · %b %d · %Y")}


# --------------------------------------------------------------- posted log
def load_posted() -> dict:
    if POSTED_FILE.exists():
        try:
            return json.loads(POSTED_FILE.read_text())
        except ValueError:
            print("  posted.json unreadable; treating as empty", file=sys.stderr)
    return {}


def save_posted(log: dict) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    trimmed = {k: v for k, v in log.items() if k >= cutoff}
    POSTED_FILE.parent.mkdir(parents=True, exist_ok=True)
    POSTED_FILE.write_text(json.dumps(trimmed, indent=1, sort_keys=True) + "\n")


def recent_venues(log: dict, today: str, days: int = 7) -> set[str]:
    since = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    return {v.lower() for date, entry in log.items() if since <= date < today
            for v in entry.get("venues", []) if v}


def prune_cards(today: str) -> int:
    cutoff = (datetime.strptime(today, "%Y-%m-%d")
              - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    gone = 0
    for card in CARD_DIR.glob("*.jpg"):
        if card.stem < cutoff:
            card.unlink()
            gone += 1
    return gone


# ------------------------------------------------------------- pages wait
def _wait_for_pages(url: str, expect_bytes: int, timeout: int = 900) -> None:
    """Block until GitHub Pages serves THIS card, not a cached older one.

    Instagram fetches image_url from its own servers, so a container created
    before Pages deploys fails with an opaque media error. Content-Length is
    compared as well as the status, because a re-run on the same date rewrites
    the same path and Pages' CDN will happily serve the previous bytes for a
    while -- a 200 alone would let us publish yesterday's card under today's
    caption.
    """
    deadline = time.time() + timeout
    delay, last = 5, "no attempt"
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, method="HEAD",
                                         headers={"User-Agent": F.UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                served = int(resp.headers.get("Content-Length") or 0)
                if resp.status == 200 and served == expect_bytes:
                    print(f"  pages serving {url} ({served} bytes)")
                    return
                last = f"{resp.status}, {served} bytes (want {expect_bytes})"
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
        except OSError as exc:
            last = str(exc)
        print(f"  waiting on GitHub Pages: {last}")
        time.sleep(delay)
        delay = min(delay * 1.6, 60)
    raise TimeoutError(
        f"{url} did not go live within {timeout}s (last: {last}). The card is "
        f"committed, so a re-run of this workflow will pick it up; check the "
        f"pages-build-deployment workflow first.")


# ------------------------------------------------------------- publishing
def post_facebook(page_id: str, page_token: str, message: str, card: Path) -> str:
    """Photo post with the bytes attached, so this never waits on Pages."""
    res = _graph(f"{page_id}/photos", page_token,
                 {"message": message, "published": "true"},
                 files={"source": card})
    return str(res.get("post_id") or res.get("id"))


def post_instagram(ig_id: str, token: str, caption: str, image_url: str) -> str:
    """Two-step container publish, with the status poll the docs require.

    media_publish on a container still IN_PROGRESS returns a generic error, so
    status_code is polled to FINISHED first. ERROR carries status, which is
    the only place the real reason (bad aspect, non-JPEG, unreachable URL)
    ever appears.
    """
    container = _graph(f"{ig_id}/media", token,
                       {"image_url": image_url, "caption": caption}, post=True)["id"]
    for _ in range(30):
        state = _graph(container, token, {"fields": "status_code,status"})
        code = state.get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
            raise GraphError(f"container {container} failed: {state.get('status')}")
        time.sleep(5)
    else:
        raise GraphError(f"container {container} never reached FINISHED")
    return str(_graph(f"{ig_id}/media_publish", token,
                      {"creation_id": container}, post=True)["id"])


# ------------------------------------------------------------------ modes
def _creds(allow_unconfigured: bool = False) -> tuple[str, str] | None:
    """Read the two secrets, distinguishing "not set up yet" from "broken".

    Both absent is the setup window: the Meta Page and Instagram account are
    manual, Meta-UI-only work that takes days, and until they exist there is
    nothing to post to. Failing red every night through that window trains you
    to ignore the one alarm that matters, so callers passing
    allow_unconfigured get None and skip quietly.

    Exactly ONE absent is never that -- it is a typo'd secret name or a
    half-finished setup, and it exits non-zero. So does a token that is
    present but rejected, which never reaches this function at all. The quiet
    path is narrow on purpose: deleting a secret later still fails loudly,
    because deleting one of two leaves the other behind.
    """
    token = os.environ.get("META_SYSTEM_USER_TOKEN", "").strip()
    page_id = os.environ.get("META_PAGE_ID", "").strip()
    if allow_unconfigured and not token and not page_id:
        return None
    missing = [n for n, v in (("META_SYSTEM_USER_TOKEN", token),
                              ("META_PAGE_ID", page_id)) if not v]
    if missing:
        raise SystemExit(f"missing environment: {', '.join(missing)} "
                         f"(GitHub repo Settings > Secrets and variables > Actions)")
    return token, page_id


_UNCONFIGURED = (
    "META_SYSTEM_USER_TOKEN and META_PAGE_ID are both unset, so the Meta "
    "accounts are not connected yet. Nothing rendered, nothing committed, "
    "nothing posted. See 'Daily social post' in CLAUDE.md for the setup "
    "order, then run: python scripts/social_post.py check")


def cmd_check(_args) -> int:
    token, page_id = _creds()
    acct = _accounts(token, page_id)
    print(f"  page      : {acct['page_name']} ({page_id})")
    print(f"  instagram : @{acct['ig_username']} ({acct['ig_id']})")
    quota = _graph(f"{acct['ig_id']}/content_publishing_limit", acct["page_token"],
                   {"fields": "config,quota_usage"})
    for entry in quota.get("data", []):
        print(f"  ig quota  : {entry.get('quota_usage')} of "
              f"{entry.get('config', {}).get('quota_total')} in the last 24h")
    debug = _graph("debug_token", token, {"input_token": token}).get("data", {})
    expires = debug.get("expires_at")
    print(f"  token     : type={debug.get('type')} app={debug.get('app_id')} "
          f"expires={'never' if not expires else datetime.fromtimestamp(expires, timezone.utc)}")
    if expires:
        print("  WARNING: this token expires. Regenerate it as a system-user "
              "token with 'Token expiration: Never'.", file=sys.stderr)
    missing = sorted({"pages_manage_posts", "pages_read_engagement",
                      "instagram_basic", "instagram_content_publish"}
                     - set(debug.get("scopes") or []))
    if missing:
        print(f"  WARNING: token is missing scopes: {', '.join(missing)}",
              file=sys.stderr)
    return 0


def cmd_build(args) -> int:
    # Checked here and not only in publish so the setup window leaves the repo
    # completely untouched. Rendering first would commit a card a day that
    # nothing ever posts, and card commits are what the Instagram fetch reads
    # -- a pile of orphans is a confusing thing to come back to.
    if _creds(allow_unconfigured=True) is None:
        print(_UNCONFIGURED)
        Path(args.plan).write_text(json.dumps({"skip": "not configured"}) + "\n")
        return 0

    today = _today()
    log = load_posted()
    done = log.get(today, {})
    if done.get("facebook") and done.get("instagram"):
        print(f"already posted for {today} (fb={done['facebook']['id']} "
              f"ig={done['instagram']['id']}); nothing to do")
        Path(args.plan).write_text(json.dumps({"skip": "already posted"}) + "\n")
        return 0

    events = json.loads((ROOT / "live-events.json").read_text())
    picks = select_picks(events, today, recent_venues(log, today))
    if not picks:
        # Not an error. DFW has quiet Mondays, and a run that posted an empty
        # card would be worse than a run that posted nothing.
        print(f"no events for {today}; nothing to post")
        Path(args.plan).write_text(json.dumps({"skip": "no events"}) + "\n")
        return 0

    text = compose(picks, today)
    card = CARD_DIR / f"{today}.jpg"
    rows = [{"name": p["event"]["name"],
             "meta": f"{p['event']['time']} · {F._display_area(p['event']['area'])}",
             "tag": CATEGORY_LABEL.get(p["event"]["category"], p["event"]["category"])}
            for p in picks]
    social_card.render_card(text["headline"], text["datestamp"], rows,
                            card, Path(args.font_cache))
    social_card.verify_card(card)
    removed = prune_cards(today)

    plan = {"date": today, "card": str(card.relative_to(ROOT)),
            "card_url": f"{SITE}/social/cards/{today}.jpg",
            "card_bytes": card.stat().st_size,
            "facebook": text["facebook"], "instagram": text["instagram"],
            "picks": [p["event"]["name"] for p in picks],
            "venues": [p["venue"] for p in picks if p["venue"]],
            "scores": [p["score"] for p in picks]}
    Path(args.plan).write_text(json.dumps(plan, indent=1, ensure_ascii=False) + "\n")
    print(f"picked {len(picks)} for {today} (scores {plan['scores']}):")
    for name in plan["picks"]:
        print(f"  - {name}")
    if removed:
        print(f"pruned {removed} card(s) older than {KEEP_DAYS} days")
    print(f"\n--- facebook ---\n{text['facebook']}\n\n--- instagram ---\n{text['instagram']}")
    return 0


def cmd_publish(args) -> int:
    plan = json.loads(Path(args.plan).read_text())
    if plan.get("skip"):
        print(f"nothing to publish: {plan['skip']}")
        return 0
    # build already skips when unconfigured, so this only fires if publish is
    # run on its own against an older plan file.
    if _creds(allow_unconfigured=True) is None:
        print(_UNCONFIGURED)
        return 0

    token, page_id = _creds()
    # Resolved BEFORE the dry-run exit on purpose. A dry run that returned
    # here would exercise none of the parts that actually break -- the token,
    # the asset assignment, the Instagram linkage -- and would pass just as
    # happily with no credentials at all, which makes it worse than useless as
    # the "test it safely" button. This way it is a real preflight that stops
    # one step short of posting.
    acct = _accounts(token, page_id)

    if args.dry_run:
        print(f"dry run: credentials resolve to "
              f"{acct['page_name']} / @{acct['ig_username']}")
        print(f"dry run: would post {plan['card_url']} to both platforms")
        return 0
    today, log = plan["date"], load_posted()
    entry = log.setdefault(today, {})
    entry["picks"], entry["venues"] = plan["picks"], plan["venues"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Written back after EACH platform, so a failure on the second one cannot
    # make a re-run repost the first. This is the whole idempotency story:
    # keyed on the date and the platform, not on a "last run" timestamp.
    failures = []
    if entry.get("facebook"):
        print(f"facebook: already posted ({entry['facebook']['id']})")
    else:
        try:
            post_id = post_facebook(page_id, acct["page_token"],
                                    plan["facebook"], ROOT / plan["card"])
            entry["facebook"] = {"id": post_id, "at": now}
            save_posted(log)
            print(f"facebook: posted {post_id}")
        except (GraphError, OSError) as exc:
            failures.append(f"facebook: {exc}")

    if entry.get("instagram"):
        print(f"instagram: already posted ({entry['instagram']['id']})")
    else:
        try:
            _wait_for_pages(plan["card_url"], plan["card_bytes"])
            media_id = post_instagram(acct["ig_id"], acct["page_token"],
                                      plan["instagram"], plan["card_url"])
            entry["instagram"] = {"id": media_id, "at": now}
            save_posted(log)
            print(f"instagram: posted {media_id}")
        except (GraphError, OSError, TimeoutError) as exc:
            failures.append(f"instagram: {exc}")

    save_posted(log)
    for line in failures:
        print(line, file=sys.stderr)
    # Partial success still exits non-zero: the platform that worked is
    # recorded, so a re-run only retries the one that did not, but a silent
    # half-post is exactly the failure that goes unnoticed for weeks.
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="verify token, Page and Instagram linkage")
    for name in ("build", "publish"):
        p = sub.add_parser(name)
        p.add_argument("--plan", default="social-plan.json")
        if name == "build":
            p.add_argument("--font-cache", default=".font-cache")
        else:
            p.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return {"check": cmd_check, "build": cmd_build, "publish": cmd_publish}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
