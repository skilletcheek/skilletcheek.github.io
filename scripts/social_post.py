#!/usr/bin/env python3
"""Posts three pick-lists a day -- "morning", "midday" and "afternoon" -- to
the Facebook Page and the linked Instagram Business account. Driven by
.github/workflows/social-post.yml.

Deliberately a SEPARATE script and a separate workflow from fetch_events.py:
a Meta outage, an expired token or a rate limit must never be able to block
the nightly event refresh, and a collapsed feed must never post garbage. This
one only ever READS live-events.json.

    python scripts/social_post.py check                        credentials + linkage
    python scripts/social_post.py build  --slot S --plan P     pick, render, write P
    python scripts/social_post.py publish --slot S --plan P    post it, log it

`--slot` is one of SLOT_ORDER ("morning", "midday", "afternoon") and is required on build
and publish -- see SLOTS below for what distinguishes them.

build and publish are split because Instagram cannot be handed image bytes.
Its Content Publishing API takes an `image_url` that Meta's servers fetch
themselves, so every image has to be committed and live on GitHub Pages
BEFORE the container call. The workflow therefore runs build -> git push ->
publish, and publish blocks on each URL actually going live (_wait_for_pages).

Facebook has no such constraint -- /{page-id}/photos accepts a multipart
upload -- so the Facebook post never depends on Pages having deployed. It is
posted first, for that reason.

THE TWO PLATFORMS GET DIFFERENT MEDIA. Facebook gets one dense card (all
three picks, render_card()) -- its caption already carries the full text and
there's no algorithmic reward there for a format change.

Instagram gets whatever IG_FORMAT names. Since 2026-09-10 that is a REEL: the
same cover-plus-one-slide-per-pick sequence the carousel used, drawn at 9:16
and muxed into a short .mp4 (render_reel()), posted via post_instagram_reel().
A carousel beats a single image, which is why the 2026-09-05 switch happened,
but both are FEED posts and reach mostly people who already follow the
account; a Reel is the only native format Instagram pushes to people who do
not. IG_FORMAT = "carousel" restores the previous behaviour exactly.

Both formats need the same commit-then-publish ordering, because Meta fetches
a video_url exactly the way it fetches an image_url. The reel commits ONE file
a day where the carousel committed four, which matters in a public repo whose
history keeps every card forever.

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
/{ig-id}/content_publishing_limit, which `check` prints. Three slots a day is
nowhere near that.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import tempfile
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

# Three posts a day, each with its own card headline. Order matters: it is how
# same-day de-duplication (already_posted_today(), recent_venues()) decides
# which slot(s) count as "already happened today" when a later slot builds --
# a slot only ever looks at slots earlier than itself in this tuple, never
# itself or one that hasn't run yet. The actual times live in
# social-post.yml's cron, not here, so there is exactly one place that can
# drift out of sync with reality instead of two.
SLOTS = {"morning": "DFW Today", "midday": "DFW This Afternoon",
         "afternoon": "Tonight in DFW"}
SLOT_ORDER = tuple(SLOTS)

# What Instagram gets. "reel" since 2026-09-10; "carousel" is the previous
# behaviour, kept whole and one word away.
#
# A carousel outperforms a single image, which is why the 2026-09-05 switch
# happened -- but both are FEED posts, shown mostly to people who already
# follow the account. A Reel is the only native format Instagram pushes to
# people who don't, which is the entire problem for an account this new.
# Facebook is untouched either way: it keeps render_card()'s single dense
# image, whose caption already carries all three picks as text.
#
# Never both in one slot. The reel and the carousel would be the same three
# picks posted twice within a minute of each other.
IG_FORMAT = "reel"


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


def select_picks(events: list[dict], today: str, recent_venues: set[str],
                  exclude_names: frozenset[str] = frozenset()) -> list[dict]:
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

    `exclude_names` is how the day's second post is guaranteed never to repeat
    the first's: it is a hard filter, applied before scoring, of
    F._norm_name() values already picked earlier the same day. Unlike
    recent_venues (a soft -6, so a repeat is merely disfavored across days)
    this one is absolute -- the same event cannot be featured twice on the
    same day even if nothing else on the feed comes close to its score.
    """
    runs = {}
    for ev in events:                       # how many distinct dates each name runs
        runs.setdefault(F._norm_name(ev["name"]), set()).add(ev["date"])

    scored = []
    for ev in [e for e in events if e["date"] == today
               and F._norm_name(e["name"]) not in exclude_names]:
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


# CTA phrasing differs by slot for the same reason the headline does: an 8 AM
# post saying "all of TONIGHT" reads oddly next to a pick that's an 8 AM tea
# tasting. Both still link to /tonight/ -- it's the site's own hub for
# "today's events" regardless of time of day, there's no separate /today/.
_CTA = {"morning": "See everything on today",
        "midday": "See what's on this afternoon",
        "afternoon": "All of tonight"}


def compose(picks: list[dict], today: str, slot: str) -> dict:
    """Facebook and Instagram captions for one of SLOTS.

    Same voice as the page -- slash kickers, caps, no adjectives (see
    index.html's hero and the hub headings in fetch_events.py). Instagram gets
    hashtags and a bare domain because captions there are not clickable;
    Facebook gets the real URLs.
    """
    stamp = datetime.strptime(today, "%Y-%m-%d")
    label = SLOTS[slot]
    head = f"{label.upper()} — {stamp.strftime('%a %b %d').upper()}"
    body = "\n".join(_line(p, i) for i, p in enumerate(picks, 1))

    top = picks[0]
    deep = None
    if top["venue_slug"] and _page_exists(f"venue/{top['venue_slug']}"):
        deep = f"{SITE}/venue/{top['venue_slug']}/"
    elif top["district"] and _page_exists(f"district/{top['district']}"):
        deep = f"{SITE}/district/{top['district']}/"

    fb = f"{head}\n\n{body}\n\n{_CTA[slot]} → {SITE}/tonight/"
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
            "headline": label,
            "datestamp": stamp.strftime("%a · %b %d · %Y")}


# --------------------------------------------------------------- posted log
def load_posted() -> dict:
    """Read social/posted.json, migrating the pre-2026-08-31 single-post-a-day
    shape (date -> {facebook, instagram, picks, venues}) to the slotted one
    (date -> slot -> {...}) on the fly.

    A day is detected as legacy-shaped when its value has no SLOT_ORDER key at
    the second level -- i.e. it looks like an entry, not a dict of entries.
    Folded into "midday", the new slot closest in time to the old single
    18:30 UTC run. This never touches the file on disk; a day only gets
    rewritten in the new shape once something posts for it again.
    """
    if not POSTED_FILE.exists():
        return {}
    try:
        raw = json.loads(POSTED_FILE.read_text())
    except ValueError:
        print("  posted.json unreadable; treating as empty", file=sys.stderr)
        return {}
    migrated = {}
    for date, entry in raw.items():
        if isinstance(entry, dict) and any(k in SLOT_ORDER for k in entry):
            migrated[date] = entry
        else:
            migrated[date] = {"midday": entry}
    return migrated


def save_posted(log: dict) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    trimmed = {k: v for k, v in log.items() if k >= cutoff}
    POSTED_FILE.parent.mkdir(parents=True, exist_ok=True)
    POSTED_FILE.write_text(json.dumps(trimmed, indent=1, sort_keys=True) + "\n")


def _entries_before(log: dict, today: str, slot: str):
    """Yield every logged entry that should count as "already happened" from
    the point of view of building `slot` on `today`: every entry on an
    earlier date, and -- for today itself -- only the slots that come before
    `slot` in SLOT_ORDER. A slot must never see itself (it hasn't run yet at
    build time) or a slot later in the day that also hasn't run.
    """
    idx = SLOT_ORDER.index(slot)
    for date, slots in log.items():
        for s, entry in slots.items():
            if date == today and SLOT_ORDER.index(s) >= idx:
                continue
            yield date, entry


def recent_venues(log: dict, today: str, slot: str, days: int = 7) -> set[str]:
    since = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    return {v.lower() for date, entry in _entries_before(log, today, slot)
            if since <= date <= today for v in entry.get("venues", []) if v}


def already_posted_today(log: dict, today: str, slot: str) -> frozenset[str]:
    """Normalized names of events already picked earlier TODAY (in a slot
    before this one), so this slot can never repeat one of them -- see
    select_picks()'s exclude_names. Checked against `picks`, which
    cmd_publish() writes unconditionally before either platform is attempted,
    so this still excludes correctly even if the earlier slot's post failed.
    """
    return frozenset(F._norm_name(n) for date, entry in _entries_before(log, today, slot)
                     if date == today for n in entry.get("picks", []))


def prune_cards(today: str) -> int:
    cutoff = (datetime.strptime(today, "%Y-%m-%d")
              - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    gone = 0
    # .mp4 as well as .jpg since the reel landed -- a card directory that
    # pruned only the images would grow a video a day forever.
    for card in sorted(CARD_DIR.glob("*.jpg")) + sorted(CARD_DIR.glob("*.mp4")):
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


def _poll_container(container_id: str, token: str, attempts: int = 30,
                    delay: int = 5) -> None:
    """Block until a media container reaches FINISHED, the status poll the
    docs require for every container -- child, parent, or single-image.

    media_publish on a container still IN_PROGRESS returns a generic error, so
    status_code is polled first. ERROR carries status, which is the only
    place the real reason (bad aspect, non-JPEG, unreachable URL) ever
    appears.
    """
    for _ in range(attempts):
        state = _graph(container_id, token, {"fields": "status_code,status"})
        code = state.get("status_code")
        if code == "FINISHED":
            return
        if code == "ERROR":
            raise GraphError(f"container {container_id} failed: {state.get('status')}")
        time.sleep(delay)
    raise GraphError(f"container {container_id} never reached FINISHED")


def post_instagram_carousel(ig_id: str, token: str, caption: str,
                           image_urls: list[str]) -> str:
    """Carousel publish: one child container per image, then a parent
    container that references them, then media_publish on the parent.

    Children take NO caption -- Instagram's own docs are explicit that
    captions on carousel children are unsupported; the caption lives on the
    parent only. `children` is a comma-separated STRING of container ids
    ("id1,id2,id3"), not the JSON array Meta's own reference page describes
    it as -- verified against multiple independent working examples on
    2026-09-02 rather than trusting the doc text, since the ambiguity there
    would have meant guessing wrong on a live post, the same kind of
    doc/reality mismatch that cost a wrong guess earlier this session on the
    Page-token failure message.

    Each container (every child, then the parent) is polled to FINISHED
    before the next step, the same caution the single-image flow already
    used -- an unfinished child referenced by the parent is exactly the kind
    of failure that would otherwise surface as an opaque media error with no
    field naming which slide was the problem.
    """
    child_ids = []
    for url in image_urls:
        cid = _graph(f"{ig_id}/media", token,
                     {"image_url": url, "is_carousel_item": "true"}, post=True)["id"]
        _poll_container(cid, token)
        child_ids.append(cid)

    parent = _graph(f"{ig_id}/media", token,
                    {"media_type": "CAROUSEL", "children": ",".join(child_ids),
                     "caption": caption}, post=True)["id"]
    _poll_container(parent, token)
    return str(_graph(f"{ig_id}/media_publish", token,
                      {"creation_id": parent}, post=True)["id"])


def post_instagram_reel(ig_id: str, token: str, caption: str,
                        video_url: str) -> str:
    """Reel publish: one container, polled to FINISHED, then media_publish.

    Structurally the single-image flow with media_type=REELS and a video_url
    -- Meta fetches the file itself here too, so the commit-to-Pages-first
    ordering the carousel needs is unchanged and _wait_for_pages() still
    guards it.

    Polled longer than an image container (10 min against the carousel's
    2.5): Meta transcodes the video server-side, so FINISHED arrives on
    Meta's schedule rather than ours, and a poll that gives up early would
    look exactly like a failed upload while the reel was still processing
    fine.

    share_to_feed puts it on the profile grid as well as in the Reels tab.
    The grid is what a venue or a first-time visitor actually lands on, and
    a grid with holes in it reads as an abandoned account.
    """
    cid = _graph(f"{ig_id}/media", token,
                 {"media_type": "REELS", "video_url": video_url,
                  "caption": caption, "share_to_feed": "true"}, post=True)["id"]
    _poll_container(cid, token, attempts=60, delay=10)
    return str(_graph(f"{ig_id}/media_publish", token,
                      {"creation_id": cid}, post=True)["id"])


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


def _slide_rows(picks: list[dict]) -> list[dict]:
    """The per-pick text a slide needs, in the order the picks were chosen.
    One derivation, used by both Instagram formats and the Facebook card."""
    return [{"name": p["event"]["name"],
             "meta": f"{p['event']['time']} · {F._display_area(p['event']['area'])}",
             "tag": CATEGORY_LABEL.get(p["event"]["category"], p["event"]["category"]),
             "cat": p["event"]["category"]}
            for p in picks]


def _render_instagram(text: dict, picks: list[dict], today: str, slot: str,
                      font_cache: Path) -> list[Path]:
    """Render Instagram's media and return what must be live on Pages.

    Both formats are the same sequence of slides -- a cover, then one per
    pick. The carousel commits each slide and posts them as children; the
    reel draws the same slides at 9:16 into a TEMP directory, muxes them
    into one .mp4 and commits only that. So the reel adds one file a day to
    the repo where the carousel added four, which matters because
    prune_cards() only deletes from the working tree: git keeps every card
    this site has ever posted, forever, in a public repo.
    """
    rows = _slide_rows(picks)
    n = len(rows)

    if IG_FORMAT == "carousel":
        out = [CARD_DIR / f"{today}-{slot}-ig0.jpg"]
        social_card.render_cover_slide(text["headline"], text["datestamp"],
                                       n, out[0], font_cache)
        social_card.verify_card(out[0])
        for i, r in enumerate(rows, 1):
            slide = CARD_DIR / f"{today}-{slot}-ig{i}.jpg"
            social_card.render_pick_slide(
                index=i, total=n, name=r["name"], meta=r["meta"], tag=r["tag"],
                cat_slug=r["cat"], is_last=(i == n),
                out_path=slide, cache_dir=font_cache)
            social_card.verify_card(slide)
            out.append(slide)
        return out

    if IG_FORMAT != "reel":
        raise ValueError(f"IG_FORMAT is {IG_FORMAT!r}; expected 'reel' or 'carousel'")

    size = (social_card.REEL_W, social_card.REEL_H)
    shared = dict(size=size, safe_bottom=social_card.REEL_SAFE_BOTTOM,
                  swipe=False, scale=social_card.REEL_SCALE)
    reel = CARD_DIR / f"{today}-{slot}.mp4"
    with tempfile.TemporaryDirectory(prefix="reel-frames-") as tmp:
        tmp = Path(tmp)
        frames = [tmp / "f0.jpg"]
        social_card.render_cover_slide(text["headline"], text["datestamp"],
                                       n, frames[0], font_cache, **shared)
        for i, r in enumerate(rows, 1):
            frame = tmp / f"f{i}.jpg"
            social_card.render_pick_slide(
                index=i, total=n, name=r["name"], meta=r["meta"], tag=r["tag"],
                cat_slug=r["cat"], is_last=(i == n),
                out_path=frame, cache_dir=font_cache,
                max_name_lines=social_card.REEL_NAME_LINES, **shared)
            frames.append(frame)
        social_card.render_reel(frames, reel)
    social_card.verify_reel(reel)
    return [reel]


def cmd_build(args) -> int:
    # Checked here and not only in publish so the setup window leaves the repo
    # completely untouched. Rendering first would commit a card a day that
    # nothing ever posts, and card commits are what the Instagram fetch reads
    # -- a pile of orphans is a confusing thing to come back to.
    if _creds(allow_unconfigured=True) is None:
        print(_UNCONFIGURED)
        Path(args.plan).write_text(json.dumps({"skip": "not configured"}) + "\n")
        return 0

    today, slot = _today(), args.slot
    log = load_posted()
    done = log.get(today, {}).get(slot, {})
    if done.get("facebook") and done.get("instagram"):
        print(f"already posted {slot} for {today} (fb={done['facebook']['id']} "
              f"ig={done['instagram']['id']}); nothing to do")
        Path(args.plan).write_text(json.dumps({"skip": "already posted"}) + "\n")
        return 0

    events = json.loads((ROOT / "live-events.json").read_text())
    exclude = already_posted_today(log, today, slot)
    picks = select_picks(events, today, recent_venues(log, today, slot),
                         exclude_names=exclude)
    if not picks:
        # Not an error either way. DFW has quiet Mondays (no events), and on
        # a thin day the earlier slot can plausibly claim everything worth
        # posting (exhausted) -- a run that posted an empty or repeat card
        # would be worse than a run that posted nothing.
        reason = "no distinct events left today" if exclude else "no events"
        print(f"{reason} for {today} [{slot}]; nothing to post")
        Path(args.plan).write_text(json.dumps({"skip": reason}) + "\n")
        return 0

    text = compose(picks, today, slot)
    font_cache = Path(args.font_cache)

    # Facebook keeps the single dense card exactly as before: its caption
    # already carries all three picks as text, and there is no algorithmic
    # reward on FB for a format change the way there is on Instagram.
    card = CARD_DIR / f"{today}-{slot}.jpg"
    social_card.render_card(text["headline"], text["datestamp"],
                            _slide_rows(picks), card, font_cache)
    social_card.verify_card(card)

    # Instagram gets whichever of the two native formats IG_FORMAT names,
    # built from the same cover-plus-one-slide-per-pick sequence either way.
    ig_assets = _render_instagram(text, picks, today, slot, font_cache)

    removed = prune_cards(today)

    plan = {"date": today, "slot": slot, "card": str(card.relative_to(ROOT)),
            "card_url": f"{SITE}/social/cards/{today}-{slot}.jpg",
            "card_bytes": card.stat().st_size,
            "ig_format": IG_FORMAT,
            "ig_assets": [{"url": f"{SITE}/social/cards/{a.name}",
                          "bytes": a.stat().st_size} for a in ig_assets],
            "facebook": text["facebook"], "instagram": text["instagram"],
            "picks": [p["event"]["name"] for p in picks],
            "venues": [p["venue"] for p in picks if p["venue"]],
            "scores": [p["score"] for p in picks]}
    Path(args.plan).write_text(json.dumps(plan, indent=1, ensure_ascii=False) + "\n")
    print(f"picked {len(picks)} for {today} [{slot}] (scores {plan['scores']}):")
    for name in plan["picks"]:
        print(f"  - {name}")
    print(f"rendered {IG_FORMAT} for Instagram: "
          + ", ".join(a.name for a in ig_assets))
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
        shape = ("a reel" if plan["ig_format"] == "reel"
                 else f"a {len(plan['ig_assets'])}-slide carousel")
        print(f"dry run: would post {plan['card_url']} to Facebook and "
              f"{shape} to Instagram")
        return 0
    today, slot, log = plan["date"], plan["slot"], load_posted()
    entry = log.setdefault(today, {}).setdefault(slot, {})
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
            # Every asset, whatever the format: Meta fetches the reel's .mp4
            # from Pages exactly as it fetches a carousel child's .jpg, so
            # the same deploy race applies to both.
            for asset in plan["ig_assets"]:
                _wait_for_pages(asset["url"], asset["bytes"])
            urls = [a["url"] for a in plan["ig_assets"]]
            if plan["ig_format"] == "reel":
                media_id = post_instagram_reel(
                    acct["ig_id"], acct["page_token"], plan["instagram"], urls[0])
                shape = "reel"
            else:
                media_id = post_instagram_carousel(
                    acct["ig_id"], acct["page_token"], plan["instagram"], urls)
                shape = f"{len(urls)}-slide carousel"
            entry["instagram"] = {"id": media_id, "at": now, "format": plan["ig_format"]}
            save_posted(log)
            print(f"instagram: posted {media_id} ({shape})")
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
            # publish takes no --slot: it reads "slot" back out of the plan
            # file build wrote, which is the one place that value should
            # live. A second copy on the CLI could disagree with the plan
            # and nothing would ever catch it.
            p.add_argument("--slot", required=True, choices=SLOT_ORDER)
            p.add_argument("--font-cache", default=".font-cache")
        else:
            p.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return {"check": cmd_check, "build": cmd_build, "publish": cmd_publish}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
