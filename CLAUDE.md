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
- **Python stdlib only** in `scripts/`. It runs on a GitHub Actions runner
  with no pip install step.
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

`git push origin main` deploys (PAT in macOS Keychain, no prompt). The
nightly Action also pushes, so **rebase before pushing** if it has run.

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
