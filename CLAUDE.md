# Lets Do It Dallas — letsdoitdallas.com

A DFW events site: pick a day, see what's happening. Dependency-free static
site (HTML/CSS/vanilla JS, no build step) on GitHub Pages, fed by a nightly
Python aggregator.

**This repo is public.** Never commit API keys, contact lists, pricing
strategy, or anything else you wouldn't publish at letsdoitdallas.com/<file>.

## Stack constraints (deliberate — don't "improve" these)

- **No build step, no Node.** No bundler, no framework, no npm. There is no
  `node` on this machine; JS can't be syntax-checked locally — verify in the
  browser preview instead.
- **Python stdlib only** in `scripts/fetch_events.py`. It runs on a GitHub
  Actions runner with no pip install step. The one exception in `scripts/` is
  the social poster (`social_card.py` needs Pillow), which runs in its own
  workflow that *does* install — see "Daily social post". Nothing the nightly
  aggregator imports may depend on it.
- Only external dependency is Google Fonts.

## Layout

    index.html              the app (single page)
    css/styles.css
    js/data.js              CONFIG + curated events + SPONSORED + DISTRICTS/ITINERARIES
    js/sources.js           browser-side feed loading + dedupe
    js/app.js               engine + UI (filters, drawer, JSON-LD, URL params)
    js/tower.js             canvas wireframe Reunion Tower
    js/radar.js             SVG district radar
    js/scenes.js            unloaded, kept in repo
    scripts/fetch_events.py the nightly aggregator (also generates pages)
    scripts/feeds.json      DATA: which feeds/venues/artists to pull
    scripts/social_post.py  daily Facebook + Instagram poster (own workflow)
    scripts/social_card.py  renders the 1080x1350 card social_post.py posts
    social/cards/*.jpg      GENERATED daily; Instagram fetches these by URL
    social/posted.json      GENERATED daily; the anti-double-post log
    venue-aliases.json      DATA: venue rename map for dedupe
    venue-districts.json    DATA: venue -> district, for venues whose `area`
                                  never names one (36% of rows)
    partners.json           DATA: founding-partner wall
    live-events.json        GENERATED nightly
    press.json              GENERATED nightly

## Generated files — never hand-edit

`fetch_events.py` regenerates these nightly and pushes to `main`. Hand edits
get clobbered; change the **Python** instead:

- `live-events.json`, `press.json`, `sitemap.xml`, `robots.txt`
- `social/cards/*.jpg` and `social/posted.json` — written by the *other*
  workflow (`scripts/social_post.py`, see "Daily social post"), not by
  `fetch_events.py`. Editing `posted.json` by hand is how you double-post.
- `/tonight/`, `/this-weekend/`, `/free-events/`, `/district/*/` hub pages
- `/venue/*/` venue pages (`write_venues()`), `/venue/` directory
  (`write_venue_index()`)
- `/advertise/` (`write_advertise()`), `/submit/` (`write_submit()`)
- the district link block in **`index.html`**, between the `SITEMAP-NAV:START`
  / `SITEMAP-NAV:END` comments (`write_home_nav()`). The rest of index.html is
  hand-written; only that block is generated, so DISTRICTS isn't hand-copied a
  third time. Delete the markers and the build warns and leaves it stale.

Pages go through `_write_page()`, which skips the write when the bytes are
unchanged and records that in `_PAGE_CHANGED`. The sitemap reads it and carries
the previously published `<lastmod>` forward for anything that didn't move —
stamping all 59 URLs with today's date every night is a freshness signal Google
learns to discard. The homepage is deliberately always today: its markup is
static but the listings it renders come from `live-events.json`.

`write_venues()` **deletes** venue directories that no longer clear
`VENUE_MIN_EVENTS` (`_prune_stale_venues()`). Without it a venue that went
quiet kept serving a 200 while dropping out of the sitemap and every listing —
an orphan Google can never confirm. It refuses to prune when it would delete
more pages than it kept, on the same reasoning as `COLLAPSE_GUARD_RATIO`.

A hub page with **no listings** is written and stays linked from `_site_nav()`,
but gets `robots: noindex,follow` and is held out of the sitemap until it has
something (`emit()` / `_hub_html()`). Boilerplate-plus-nav is what Google parks
in "Discovered - currently not indexed"; this says so honestly instead, and
reverses itself the first night the district books an event. The run prints
which hubs it held back.

`main()` also calls `prune_eventbrite()`, which drops finished events from
`eventbrite.json`. That file is hand-refreshed (Eventbrite 405s datacenter IPs)
so nothing else ages it out — by 2026-08-27 all 168 rows had expired and every
visitor was downloading ~30 KB gzipped of them to render nothing. Pruning needs
no network, so CI can do it even though the refresh can't. **An empty
`eventbrite.json` means the source needs a hand refresh**, not that it broke.

`write_hubs()` calls `write_venues()` **first** — it populates `_VENUE_PAGES`,
which `_hub_row()` reads to link listings to venue pages. Reorder that and the
links silently vanish. A venue needs `VENUE_MIN_EVENTS` (3) upcoming events to
get a page; `_is_real_venue()` rejects district labels (`area` sometimes
reports "Lower Greenville") and touring shows that pose as venues.

`/advertise/` and `/submit/` read `CONFIG` values from `js/data.js` at **build
time** — after changing an endpoint there, regenerate the page.

## Internal links are the crawl budget

Search Console had all 56 sub-pages in "Discovered - currently not indexed" on
2026-07-23: Google had crawled the homepage and nothing else. Nothing was
technically wrong — 200s, canonicals, valid sitemap — the site just had almost
no internal linking, and on a domain this new that is the whole signal. The
homepage linked 3 of 15 districts and 0 of 38 venue pages; venue pages linked
only to `/`, `/submit/` and `/advertise/`, so every crawl path dead-ended.

Every generated page now carries `_site_nav()` (all hubs, all districts,
`/venue/`), venue pages carry a district breadcrumb plus same-city siblings,
and `/venue/` is a permanent parent for all of them. **Keep the audit at zero
orphans** — no page below the homepage should depend on a listing row for its
only inbound link, because those rows move every night:

```python
import re, pathlib, collections
pages = {}
for f in list(pathlib.Path('.').glob('*/index.html')) + list(pathlib.Path('.').glob('*/*/index.html')) + [pathlib.Path('index.html')]:
    u = str(f.parent).replace('.', '').strip('/')
    pages['/' if not u else f'/{u}/'] = {h.split('?')[0] for h in
        re.findall(r'href="(/[^"]*)"', f.read_text().split('</head>', 1)[-1])}
inbound = collections.Counter(l for s, ls in pages.items() for l in ls if l in pages and l != s)
print([u for u in pages if not inbound[u] and u != '/'])   # must be []
```

Those links must be in the **served HTML**, not rendered by `app.js`. Google
defers JS rendering to a second queue and a new domain does not get to the
front of it — that is why the district block in index.html is generated into
the markup rather than built from `DISTRICTS` at runtime.

## Do NOT run `main()` locally

`scripts/fetch_events.py` needs `TICKETMASTER_KEY` / `SEATGEEK_CLIENT_ID`,
which live in repo secrets, not on this machine. Running `main()` here
overwrites `live-events.json` with a fraction of the data and regenerates
every hub page from it.

Test individual fetchers instead:

```python
import sys; sys.path.insert(0, 'scripts')
import fetch_events as F
from datetime import datetime, timedelta, timezone
start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
F.fetch_ics_feeds(start, start + timedelta(days=30))   # or write_submit(...), etc.
```

## Dedupe — the highest-risk code in the repo

Lives in `dedupe()` / `_same_event()` / `_norm_name()` / `_venue_tokens()` in
`fetch_events.py`, mirrored in `js/sources.js`. **Both layers must agree.**

`_same_event()` requires **all three**: a shared meaningful title token AND
venue-token equality-or-subset AND start times within 90 minutes.

- Every clause is load-bearing. Dropping the title-token check merged two
  different comedians playing the same room at the same nominal time.
- The 90-minute window deliberately keeps a 2 PM matinee separate from the
  8 PM show. Don't collapse it back to a name+date key — that silently ate
  14% of events when `sources.js` did exactly that.
- Renamed venues share no tokens, which no normalization fixes. Add the
  rename to `venue-aliases.json` as **data** rather than loosening
  `_same_event()`. Never alias two rooms in one building (House of Blues vs
  its Cambridge Room) — they run different shows the same night.

**Audit after any dedupe or alias change** (must be 0 orphans):

```python
combined = F.dedupe(live + new_rows)
kept = {id(r) for r in combined}
toks = lambda n: {t for t in F._norm_name(n).split() if t not in F._STOP and len(t) > 1}
orphans = [r['name'] for r in live + new_rows if id(r) not in kept
           and not any(s['date'] == r['date'] and (toks(r['name']) & toks(s['name']))
                       for s in combined)]
```

## Sources

Configured in `scripts/feeds.json`; see its `_readme` and the module docstring
in `fetch_events.py`, which also records **sources already evaluated and
rejected** so they don't get re-probed.

- All city-reporting sources are gated through `is_dfw_city()`. The lat/long
  radius sent to Ticketmaster/SeatGeek is **not** trustworthy on its own.
- Eventbrite answers 405 to datacenter IPs (not UA-based), so it is refreshed
  by hand via `scripts/fetch_eventbrite_local.py`, never in CI.
- Do214's parser is written but **disabled**: it 403s all non-browser UAs and
  their ToS forbids scraping. Don't enable it by faking a User-Agent.
- Dallasites101 (`fetch_dallasites101`) discovers events via `/event/rss/`
  (its `/calendar/` page was rebuilt as a client-rendered widget sometime
  after 2026-07-21 and stopped shipping any `/event/` links server-side,
  silently taking the scraper from ~8 events/night to 0 until this was
  caught and fixed 2026-08-27) then follows each RSS `<link>` to the
  per-event page's JSON-LD — no bulk API. Small yield (~8), no key,
  `Crawl-delay: 2` in its robots.txt is honored with a `time.sleep(2.0)` per
  event page. Its JSON-LD has no time-of-day and no ticket link; both are
  recovered from a `var time = "..."` string and an embedded `"Tickets
  URL"`/`"admission"` blob in the page source. The RSS feed has no category
  or pagination query params — `?category=`, `?page=` are silently ignored —
  it's a fixed ~30-item rolling window, not a filterable query. CultureMap
  Fort Worth was evaluated the same day (2026-07-21) and rejected
  (client-rendered shell, no feed) — see the module docstring before
  re-probing either.
- A run that produces fewer than half the previous file's events refuses to
  write (`COLLAPSE_GUARD_RATIO` in `fetch_events.py`, main()) and exits
  non-zero instead — this is almost always a dead API key or a source's
  markup changing, not DFW actually going quiet. The Action then fails
  visibly rather than silently pushing a gutted site.
- Adding an ICS feed: try `<site>/events/?ical=1`, then
  `/wp-json/tribe/events/v1/events`. **Confirm the content-type is
  `text/calendar`** — several DFW sites answer 200 with an HTML page.

## Submit form

`SUBMIT_FIELDS` in `fetch_events.py` is the single definition of the form.
`/submit/` renders from it; the modal in `index.html` is hand-written to
match, and `_check_modal_drift()` warns during the nightly build when they
diverge. Add a field in both places.

## Daily social post

`.github/workflows/social-post.yml` posts two pick-lists a day -- "morning"
and "midday" -- to the Facebook Page and the linked Instagram Business
account, from `live-events.json`. `scripts/social_post.py` is the whole
thing; `scripts/social_card.py` only draws the image.

**It is a separate workflow on purpose.** The nightly refresh is the site;
this is marketing. A dead Meta token or an Instagram outage must never be able
to leave `/tonight/` serving yesterday, and a run that tripped
`COLLAPSE_GUARD_RATIO` must never be able to post from a gutted feed. The
poster only ever *reads* `live-events.json`.

**The build/publish split is not stylistic — Instagram forces it.** The
Content Publishing API takes an `image_url` that *Meta's* servers fetch; there
is no way to hand it bytes, and no text-only post. So the card has to be
committed and live on GitHub Pages *before* the container call, and the
workflow runs `build` → commit+push → `publish`. `publish` blocks on the URL
going live (`_wait_for_pages`), comparing `Content-Length` and not just the
status — a re-run on the same date rewrites the same path, and Pages' CDN will
serve the previous bytes for a while, which would publish yesterday's card
under today's caption.

Facebook has no such constraint: `/{page-id}/photos` takes a multipart upload
(`_multipart()`, hand-rolled because stdlib has no encoder). That is why
**Facebook is posted first** — it cannot be blocked by a slow Pages deploy.

**Why the card is ours and not the feed's `image`.** Measured 2026-08-28: 647
of 667 rows carry one, but 105 are 280x210 SeatGeek thumbnails (under
Instagram's 320px floor), several hosts serve PNG (the API takes JPEG and
nothing else), and the 485 usable Ticketmaster JPEGs are the promoter's
copyrighted key art — not something to repost daily under our own handle
forever. `verify_card()` asserts every constraint in the media spec locally,
because a bad container comes back as a generic "media upload failed" with no
field naming the reason.

**Idempotency is keyed on date + slot + platform**, in `social/posted.json`, written
back after *each* platform. A Facebook-succeeded/Instagram-failed run exits
non-zero but records the Facebook id, so the retry only retries Instagram.
This is why the "Commit the posted log" step is `if: always()` — losing that
file after a partial post is what makes the next run double-post to Facebook.

Two secrets, both in repo Settings > Secrets, following the
`TICKETMASTER_KEY` pattern: **`META_SYSTEM_USER_TOKEN`** (a Business
*system-user* token — the 60-day Page token from the Graph Explorer expires
and silently breaks this) and **`META_PAGE_ID`**. The Instagram user id is
deliberately *not* configured: it is read from the Page node
(`instagram_business_account`) at runtime, so there is one less thing to
rotate and the preflight can tell "not linked" from "wrong id".

`python scripts/social_post.py check` is the preflight — it prints the Page,
the linked IG account, the 24h publishing quota, and whether the token is a
system-user token that never expires. Run it after any credential change.

**Both secrets absent = the run skips silently and touches nothing.** Standing
up the Meta side is manual, Meta-UI-only work that takes days, and failing red
every night through that window trains you to ignore the one alarm that
matters. The quiet path is deliberately narrow: exactly *one* secret missing
is a typo or a half-finished setup and exits non-zero, as does a token that is
present but rejected. Deleting a secret later still fails loudly, because
deleting one of two leaves the other behind. `_creds(allow_unconfigured=)` is
the switch; `check` never takes the quiet path.

**The schedule is live** — resumed 2026-08-31 after a few days of hand-posting
warmed up both brand-new accounts, the risk the pause existed for. To pause it
again (e.g. a new account needs warming up), comment out both `cron:` lines in
`social-post.yml`'s `schedule:` block — `workflow_dispatch` still works for a
dry run either way.

**Two posts a day, never the same events.** `morning` runs ~8:00 AM Dallas
(13:00 UTC), `midday` ~12:00 PM Dallas (17:00 UTC) — both pinned to CDT; once
Dallas falls back to CST in November both read an hour earlier on the clock
until DST resumes, the same tolerance already accepted for
`fetch-events.yml`'s own cron comments. The 17:00 UTC slot deliberately
overlaps `fetch-events.yml`'s catch-up cron; both jobs push through
`push-with-retry.sh`, so the collision costs a retry, not a broken run.
`github.event.schedule` in the workflow tells the job which cron fired --
**this is load-bearing, not cosmetic**: a run landing near 17:00 UTC is not
proof it was the midday cron, it could be a badly-delayed morning one, which
is exactly what happened on 2026-09-01 (see below). A manual dispatch has no
schedule string, so it falls back to a `slot` input.

**Each slot has a catch-up cron too**, ~1.5h after its primary (morning:
13:00 + 14:30 UTC catch-up; midday: 17:00 + 18:30 UTC catch-up) — the same
self-heal shape `fetch-events.yml` uses, added after the first two days of
real operation proved it necessary: 2026-08-31's only schedule-triggered run
fired 4h42m late, and on 2026-09-01 the morning cron fired 4h07m late while
midday never fired at all as of 1h42m past due (both posted manually instead
that day). The catch-up works because build is idempotent per (date, slot) --
if the primary already posted, the catch-up's `already posted` check no-ops
it; if the primary was dropped or still queued, the catch-up does the real
work. The original single-cron-per-slot design accepted "a missed post is a
missed post" as the tradeoff for never risking a stale retry after midnight
UTC; two days of real data showed that tradeoff firing far more than a
one-cron design can absorb, so a slot now gets two independent chances before
that's actually true.

A day's second slot must never repeat the first's picks, so
`social/posted.json` is nested `date -> slot -> {facebook, instagram, picks,
venues}`, and `already_posted_today()` hard-excludes (in `select_picks()`,
before scoring even starts) any event a same-day earlier slot already
featured — see that function's docstring for why this is a hard filter and
not the same -6 soft penalty `recent_venues` applies across days. On a day
thin enough that the second slot has nothing left to post, it skips cleanly
rather than repeating the first slot's picks or posting an empty card. Cards
are named `<date>-<slot>.jpg` so the two posts never share a file.
`load_posted()` migrates the pre-2026-08-31 flat one-post-a-day shape
on read, folding a legacy day into `"midday"` (the closest slot in time to
the old single 18:30 UTC run) without rewriting the file until something
posts for that day again.

What breaks it:

- **The Instagram account being personal or unlinked.** `instagram_business_
  account` is then *absent* from the Page node. This cannot be fixed in code
  or over the API; it is a manual conversion in the Instagram app plus a link
  in Meta Business Suite. `_accounts()` raises with those exact steps.
- **That link is made from the PAGE, not from the Instagram asset.** Claiming
  the Instagram account into the Business portfolio and assigning it to the
  system user is *not* enough and does not populate the field — verified the
  hard way on 2026-08-28, where the account was claimed, owned and assigned
  with full access while the Page still reported nothing. `Business settings >
  Accounts > Instagram accounts > Connect assets` offers only ad accounts. The
  real control is `Business settings > Accounts > Pages > <page> > Connect
  assets > Instagram account`, and it requires an interactive Instagram login,
  so it can never be automated.
- **Diagnosing a Page-token failure**: Graph returns the same
  `(#100) nonexisting field (access_token)` for a wrong id, an unassigned Page
  and a token missing scopes. `_diagnose()` prints what the id resolves to,
  which Pages the token can list, and its scopes, because guessing between
  them sent someone into the Meta UI twice for nothing. Note that ids matching
  a secret come back masked as `***` in Actions logs — a Page id printing as
  a raw number is itself proof it differs from `META_PAGE_ID`, which is how
  the wrong-id case was finally caught.
- **Moving the cron into 00:00–05:00 UTC.** `_today()` uses the UTC date so it
  agrees with `write_hubs()`; inside that band Dallas is still on the previous
  day and the post would advertise picks that `/tonight/` does not list.
- **Forgetting `social` in the workflow's `git add` allowlist** — the same
  trap documented under Deploy, and worse here: an uncommitted card means the
  Instagram fetch 404s.
- Instagram allows 100 API-published posts per rolling 24h (verified
  2026-08-28, printed by `check`). This posts once, so the limit is only ever
  reached by a loop bug.

Pick selection (`select_picks()`) is all proxies, because the feed has no
attendance or capacity and `cost` is null on 83% of rows — so "price as a
proxy for notability" can't carry it. It leans on what the site already
computed: a venue that earned its own `/venue/` page is a real recurring room.
Full scoring rationale is in that function's docstring. Three passes drop one
constraint at a time (distinct category + distinct venue → distinct venue →
anything); the venue rule outranks the category rule because the two-pass
version put one Deep Ellum room in slots 01 and 03 of the same card.

## Performance rules learned the hard way

- **Never use `ctx.shadowBlur` in a per-frame canvas path.** It's a full
  gaussian blur per draw call; it cost 39,888 ms of main-thread work and took
  PSI mobile to a TBT of 33 seconds. Use stacked additive strokes and
  pre-rendered sprites (see `js/tower.js`).
- Feed files are fetched once per page view and cached in `_fileCache`
  (`sources.js`). Don't re-fetch on date change.
- All feed text must go through `esc()` before `innerHTML`, and feed URLs
  through `safeUrl()`. Real listings contain `<angle brackets>` and quotes.
  **The same rule applies in Python**: `_hub_row()` escapes via `_html.escape`
  and whitelists the URL scheme. 107 of 541 names carry a bare `&`.

## Two-layer mirrors

Like `dedupe()`, some logic exists in both Python and JS and **both must
agree**: `_split_area()` (`fetch_events.py`) / `splitArea()` (`js/app.js`) feed
the same schema.org address into the generated pages and the homepage's runtime
JSON-LD. Cross-check by hashing both over `live-events.json` after any change.
`addressLocality` must be a city — it once held the whole postal address.

`_slugify_matches()` (`fetch_events.py`) / `districtOf()` (`js/radar.js`) is the
other one: it decides an event's district for both the generated
`/district/*/` pages and the homepage radar's per-district counts. Both do a
substring pass over `area` first, then fall back to `venue-districts.json`.

**Districts are matched on free text, so a venue whose name doesn't contain its
district never matched.** Most ticketing rows are just "Venue, City", so on
2026-08-27 that was 230 of 635 events in no district at all and six district
pages — Downtown Dallas, Arts District, Uptown, Design District, Stockyards,
Grapevine — serving nothing but boilerplate and nav, which is exactly the
"Discovered/Crawled - currently not indexed" bucket. Fix it by **adding the
venue to `venue-districts.json`**, never by loosening the substring list: a
term broad enough to catch American Airlines Center ("dallas") swallows the
whole metroplex. Values are validated against `DISTRICTS` at load, so a typo'd
slug fails the run instead of silently dropping the venue.

Adding a NEW district means hand-placing it on the radar (`DISTRICTS` in
`js/data.js` carries x/y and `labelDir` tuned to avoid label collisions), which
is why Fair Park and The Cedars are currently filed under `downtown-dallas` in
`venue-districts.json` rather than getting their own entries.

## Verification gotchas

- **Grepping a generated page: split on `</head>` first.** The inline
  `<style>` block matches class-name regexes and produces false readings.
- The embedded browser pane loads pages in a hidden tab. Deep-scroll
  screenshots render black (hide preceding sections via JS instead), rAF
  loops are paused, and `first-contentful-paint` reads ~2400 ms as an
  artifact. Check `document.visibilityState` before believing paint metrics.
- PageSpeed Insights: the keyless API is quota-exhausted and the report is a
  SPA. Open the `pagespeed.web.dev/analysis/...` URL in the Browser pane and
  read `window.__LIGHTHOUSE_MOBILE_JSON__`.

## Deploy

`git push origin main` deploys (PAT in macOS Keychain, no prompt). **Two**
Actions also push — the nightly fetch and the daily social post — so **rebase
before pushing** if either has run. They push through
`.github/push-with-retry.sh`, which rebases and retries rather than failing,
because a rejected push in the social job strands a card that the Instagram
call is about to fetch from Pages.

No `gh` CLI on this machine — dispatch workflows via the REST API using the
PAT from `git credential fill`. A **204** means accepted. Parse run payloads
with `json.loads(..., strict=False)`: they echo the triggering commit
message, and a multi-line message puts raw control characters in a JSON
string.

The Action's commit step uses an **explicit path allowlist** for `git add`.
Anything `fetch_events.py` writes that isn't listed is regenerated on the
runner and then thrown away, while `sitemap.xml` — which *is* listed — still
advertises it: that is how 14 venue pages 404'd for 18 days. **Add a writer,
add its directory to that line in the same commit.**

Scheduled runs are **not guaranteed** — GitHub delays them under load and
sometimes drops them entirely (2026-08-27: the 09:00 slot never fired). Because
hub pages bake "today" in at build time, a skipped run leaves `/tonight/`
serving yesterday under a heading that says TONIGHT, silently and all day.
Hence two crons; the second no-ops when the first worked. If you're debugging
"the site says the wrong day", check the Actions run list before the code.

## Local shell

zsh, which does **not** word-split unquoted variables. `R="--resolve h:p:ip";
curl $R url` passes one giant argument and fails. Quote the flag or use an
array.
