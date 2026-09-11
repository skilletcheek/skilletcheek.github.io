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
    scripts/notify_submitter.py  hand-run: "your event is live" mail
    scripts/venue_outreach.py    hand-run: "we built you a page" mail
    scripts/pinterest_post.py    weekly Pinterest board (own workflow)
    scripts/social_post.py  daily Facebook + Instagram poster (own workflow)
    scripts/social_card.py  renders the 1080x1350 images social_post.py posts
    social/cards/*.jpg      GENERATED daily; the Facebook card
    social/cards/*.mp4      GENERATED daily; the Instagram Reel, fetched by URL
    social/posted.json      GENERATED daily; the anti-double-post log
    social/pinned.json      GENERATED weekly; which pages are already pinned
    venue-aliases.json      DATA: venue rename map for dedupe
    venue-districts.json    DATA: venue -> district, for venues whose `area`
                                  never names one (36% of rows)
    partners.json           DATA: founding-partner wall
    live-events.json        GENERATED nightly
    press.json              GENERATED nightly
    feed.xml                GENERATED nightly; site-wide RSS
    calendar.ics            GENERATED nightly; site-wide iCalendar
    <key>.txt               GENERATED; IndexNow ownership proof (public)

## Generated files — never hand-edit

`fetch_events.py` regenerates these nightly and pushes to `main`. Hand edits
get clobbered; change the **Python** instead:

- `live-events.json`, `press.json`, `sitemap.xml`, `robots.txt`
- `feed.xml`, `calendar.ics`, and every `*/calendar.ics` under `/district/`,
  `/city/`, `/venue/` and `/free-events/`
- `ffd9813217969d9353baca4cea7b0cb1.txt` (`write_indexnow_key()`)
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
reports "Lower Greenville"), **bare city names** and touring shows that pose
as venues. The city check reads `DFW_CITIES` and matches exactly, so a venue
that legitimately carries a city in its name ("Arlington Music Hall",
"Addison Improv") still passes. It was added 2026-09-11 when
`fetch_civicplus` — whose `area` is a bare city — would otherwise have built
`/venue/garland/` from 46 library programs and `/venue/cedar-hill/` from 94.

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

**The site links out to the accounts too, since 2026-09-10.** Until then the
funnel ran one way: three posts a day pushed traffic to the site and not one
of the 72 pages linked back to either profile, so everyone who arrived from
search left without knowing the accounts existed. `SOCIAL` in
`fetch_events.py` is the single definition and `_site_nav()` puts a `/ FOLLOW`
row on every generated page. index.html is hand-written, so it mirrors that
list in **two** more places — the `Organization` node's `sameAs` and the
footer's `/ CONNECT` column. **Change one, change all three.** `sameAs` is the
half that matters to Google: it is what ties the domain and the profiles into
one entity, and nothing else on the domain asserted the connection.

These are the only external links in `_site_nav()`, and they do not affect the
orphan audit above — it counts only hrefs that resolve to a page in this repo.

**`og:image` is per-page on listing pages**, via `_og_image()`. Every generated
page used to ship the same `og-image.png`, so a share of any one of them — a
text, a DM, a Facebook post — looked identical to a share of every other. Hub
and venue pages now use their top listing's own art, falling back to the site
card when the page has no events or the top row has no image. That art is the
promoter's, exactly as it already appears in the same page's Event JSON-LD, so
it asserts nothing new — which is a different question from `social_card.py`'s
refusal to build the *daily post* out of the same images. The `/venue/` and
`/city/` directories, `/advertise/` and `/submit/` keep the site card: they are
not listings, so there is no "top event" whose art would mean anything.

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
- **CivicPlus city calendars** (`fetch_civicplus`, `civicplus_sites` in
  `feeds.json`) — six DFW suburbs (Garland, Cedar Hill, Grapevine, McKinney,
  Frisco, Lancaster) all publish the same RSS at the same path, so this is
  **one parser for six sources** and a seventh city is one `{site, city}` line.
  ~350 events a run, the library/parks/rec layer the ticketing APIs never
  list. Public records from municipal governments, so no ToS question.

  **The RSS `pubDate` is NOT the event date** — it is when the listing was
  published, and Garland's first item carried 22 May for an event on
  11 September. The real date, time and address are inside the HTML-escaped
  `<description>` on a fixed CivicPlus template. Parsing `pubDate` would file
  every event under the wrong day while the feed still looked healthy.

  **Read the city from its own line.** The first version ran the regex over
  the flattened Location block and greedily matched `"Broadway Blvd. Garland"`
  out of `"4845 Broadway Blvd. Garland, TX 75043"`, which is not a DFW city —
  so `is_dfw_city()` silently dropped 104 of Garland's 105 events. Grapevine
  was the only city that appeared to work, purely because its block carries no
  street line. The counters print `municipal` and `off-area` **separately**
  for exactly this reason: one combined "filtered" number is what disguised a
  parsing bug as a working noise filter.

  **These feeds publish no venue name**, only a street line and the city, so
  `area` is deliberately the city alone — feeding the street to
  `_split_area()` would mint venue pages named "6861 W Eldorado Parkway" once
  three library programs shared an address. The consequence is that this
  source produces no venue pages and (via `CITY_MIN_VENUES`) no city pages
  either; its events reach the homepage, the time hubs and the feeds only.

  Its window is a rolling **~14 days**, not `DAYS_AHEAD`, so it thins toward
  the end of the month where Ticketmaster does not. `civicplus_skip` drops
  municipal governance by title substring and `civicplus_categories` infers a
  category (the RSS carries none) — both **data in `feeds.json`**, because
  every city words its agendas differently and the mapping is a heuristic that
  wants tuning from the feed, not a code change.
- Adding an ICS feed: try `<site>/events/?ical=1`, then
  `/wp-json/tribe/events/v1/events`. **Confirm the content-type is
  `text/calendar`** — several DFW sites answer 200 with an HTML page.

## Feeds — RSS and iCalendar

The site parsed ICS from a dozen DFW venues and published none of its own
until 2026-09-10. `write_feed()` and `write_calendar()` in `fetch_events.py`
fix that: `/feed.xml` carries the next `FEED_DAYS` (7) of events, and
`calendar.ics` files carry the next `CAL_DAYS` (30).

**Calendars are only offered where the filter is durable** — a district, a
city, a venue, or "free". `/tonight/` and `/this-weekend/` deliberately get
none: they are moving windows over events the site-wide calendar already
carries, and subscribing to "tonight" hands someone a feed whose meaning
changes under them every night. `emit(..., calendar=True)` is the switch.

**A hub with no events gets no calendar and no subscribe link.** An empty
calendar is worse than no link — it subscribes someone to silence and reads as
broken rather than quiet. `write_calendar()` returns `None` and
`_subscribe_block()` renders nothing. The site-wide `calendar.ics` is the one
exception (`always=True`): `_site_nav()` links it from every page without
checking, and `fetch-events.yml` names it literally in `git add`, where a
missing path is a **fatal error that fails the whole nightly run**.

### The two ids are not the same id

- `_event_uid()` mirrors `uid()` in `js/app.js` and carries **no date**,
  because app.js resolves it within a single day's list. It is used *only* to
  build the `?e=` deep link, which that function has to match.
- `_feed_id()` appends the date and is what iCalendar `UID` and RSS `guid`
  use. Without the date, 669 events collapsed to 554 ids — a daily listing
  like "Tea Around Town" is one name at one venue at one time on 30 dates, so
  a calendar client keyed on UID kept **one** of them and the tour appeared
  once a month. An RSS reader does the same with a repeated guid.

**Both are frozen once published.** Clients key on them: change either shape
and every event in every subscriber's calendar is deleted and re-added as new.
`_feed_id()` deliberately does *not* match the UID `js/app.js` writes for its
single-event ADD TO CALENDAR download — that one builds its timestamp from the
visitor's own clock, so it already differs between two people in different
timezones and cannot be reproduced server-side.

### Details that are load-bearing

- **`_event_start()` uses real `America/Chicago`**, not the fixed `-05:00`
  that `_jsonld()` hardcodes. That constant is an hour wrong for every event
  between November and March; a calendar entry is a promise about an instant,
  so it cannot inherit that bug. **`_jsonld()` still has it** — worth fixing
  separately.
- **Rows with no usable time become all-day entries**, not 10 AM. `app.js`'s
  download button guesses 10 AM; a subscription repeating that guess 30 times
  in someone's calendar is a different thing from doing it once on request.
- **ICS is CRLF and folded at 75 OCTETS** (RFC 5545 3.1), folded on encoded
  bytes rather than characters — an em-dash or accent is multi-byte, and
  splitting mid-character yields a file some clients reject.
- **The feeds are byte-stable across identical builds.** `DTSTAMP` is derived
  from the event rather than `now()`, and RSS carries no `lastBuildDate`;
  otherwise all 66 files would rewrite every night and commit a diff that says
  nothing.
- **Neither goes in `sitemap.xml`** — that is a list of HTML pages for a
  crawler. Discovery is `<link rel="alternate">` in every head, the `/ FEEDS`
  row in `_site_nav()`, and the per-page subscribe line.
- `webcal://` is what makes a calendar link a *subscription*; the `https`
  `.ics` sits beside it because `webcal://` does nothing on a machine with no
  calendar app registered.

## IndexNow

Bing, DuckDuckGo, Yandex, Ecosia and Seznam share one push endpoint: submit a
URL and they crawl it in minutes rather than whenever they next re-read the
sitemap. **Google does not participate**, so this moves the non-Google share
only — which on a domain this new is not nothing.

**The key is not a secret.** `INDEXNOW_KEY` is an ownership *proof*, not a
credential: the protocol requires it to be readable by anyone at
`https://letsdoitdallas.com/<key>.txt`, which is how the endpoint confirms whoever
submitted a URL controls the host. It is the one string in this repo that looks
like an API key and is *meant* to be published — the "never commit API keys"
rule at the top does not apply to it. Rotating it means changing the constant,
letting the nightly run write the new `.txt`, **and updating the filename in
`fetch-events.yml`'s `git add` allowlist**, where it is spelled out literally.
A wrong or missing name is loud rather than silent: the submit step reports
`403 — key file not reachable`.

**Only pages that actually moved get submitted.** `_write_page()` already
tracks this in `_PAGE_CHANGED`, so `changed_urls()` is the handful that really
changed and not all 72 — repeatedly resubmitting unchanged URLs is exactly
what the protocol asks you not to do. On an unchanged rebuild the list settles
to one entry. The homepage is *always* included, for the same reason
`write_hubs()` always stamps it with today in the sitemap: its markup is
static so `_write_page()` never sees it change, but everything it renders
comes from `live-events.json`.

**It runs in its own workflow step, AFTER the push** (`fetch_events.py
indexnow <urls.json>`), for the same reason the social poster splits build
from publish: the engines fetch what you point them at, so submitting before
GitHub Pages has deployed makes every one of them re-crawl the *previous*
version of each page. `cmd_indexnow()` blocks on the deploy actually landing
first.

**The deploy probe is `sitemap.xml`, deliberately, and not one of the
submitted URLs.** `changed_urls()` puts the homepage first and `index.html` is
hand-written, so it usually did *not* change in this push and would confirm a
deploy that has not landed. `sitemap.xml` is rewritten every run and its bytes
differ exactly when something moved — which also makes the no-op case right:
nothing changed means nothing was pushed, and the served copy already matches.

The step is **`continue-on-error: true`**. An indexing hint must never be able
to fail the run that regenerates the site. A failure shows red on the step and
green on the job, which is the visibility this deserves and no more.

## Submit form

`SUBMIT_FIELDS` in `fetch_events.py` is the single definition of the form.
`/submit/` renders from it; the modal in `index.html` is hand-written to
match, and `_check_modal_drift()` warns during the nightly build when they
diverge. Add a field in both places.

## Pinterest board

`.github/workflows/pinterest-post.yml` runs `scripts/pinterest_post.py`
weekly (Sunday 11:00 UTC, clear of every other cron here) and pins up to
`--limit` (5) pages that have never been pinned.

**Pinterest is a search index, not a feed, and that changes the unit.** A pin
has a multi-year half life, so the daily "DFW TODAY — THU SEP 10" card is
exactly the wrong thing to put on a board: stale tomorrow and stale forever
after. The unit here is a **place** — one pin per district, city,
`/free-events/` and venue page. "Things to Do in Deep Ellum" stays true, and
the page it links to refreshes itself nightly.

That is why it posts **once per target and then never again**, why it is
weekly rather than daily, and why it is its own script and workflow rather
than a third platform in `social_post.py`. It also means the board grows with
the site by itself: a venue that clears `VENUE_MIN_EVENTS` next month becomes
a new target on the next run, and when everything is pinned the run no-ops.

**`render_pin()` carries no event names and no date.** Listing "Sep 12: The
Holdup" would make a pin still being surfaced in two years look abandoned. The
pin's value is the click through to a page that is always current, not the
sample. The category tagline *is* read from the feed, so it cannot drift from
what the place actually books.

**The title shrinks to fit; it is never clipped.** `_wrap()` breaks on spaces,
so a single word longer than the column ("STOCKYARDS" at 88px) ran off the
edge, and at the line cap it ellipsized "The National Multicultural Western
Heritage Museum" into "THE NATIONAL MULTICU…". A pin is a search result that
has to say what it is, so the type adapts to the name. All 66 current target
names were checked to render in full.

**No build/publish split.** Unlike Instagram, Pinterest's create-pin endpoint
takes the image bytes directly (`media_source.source_type = "image_base64"`),
so there is no commit-to-Pages dance, no `_wait_for_pages()`, and **nothing is
committed but the log** — a public repo does not grow an image a week forever.

**A hub with no upcoming events is never pinned.** Those pages carry
`noindex` until they have something; pinning boilerplate is how a board stops
being worth following.

One secret, **`PINTEREST_ACCESS_TOKEN`**. The board id is deliberately not
configured — it is resolved from `BOARD_NAME` at runtime, the same reasoning
that keeps the Instagram user id out of the secrets. Absent = the run skips
quietly and touches nothing, the same narrow path `social_post.py` takes while
a platform is being stood up by hand.

What breaks it:

- **Token expiry.** Pinterest access tokens are short-lived unless minted
  through the refresh flow; there is no equivalent of Meta's never-expiring
  system-user token. `python scripts/pinterest_post.py check` is the preflight.
  A 401 is reported with that hint attached rather than as a traceback.
- **The board not existing.** `_board_id()` raises with the boards it *did*
  find and refuses to create one — a script should not guess which existing
  board was meant, and creating one silently scatters pins onto a board you
  use for something else.
- The request *bodies* come from Pinterest's v5 docs and have **not** been
  exercised against a real token. The three endpoints were probed on
  2026-09-10 and all exist (401 to a bad token, not 404), but treat the first
  live run as the test and read the error text — the same doc/reality gap that
  cost a wrong guess on Instagram's carousel `children` parameter.

## Hand-run outreach mail

Two scripts draft an email and print it. **Neither one sends anything, and
neither may grow the ability to** — `scripts/notify_submitter.py` (a submitter's
event is live) and `scripts/venue_outreach.py` (a venue has its own page).

**No contact list, ever.** This repo is public and `events.json` is served at
`letsdoitdallas.com/events.json`, so an address stored anywhere in it is an
address published to the world — a directory of local venue owners and event
submitters. Both scripts take the address on `--to`, use it once for a
`mailto:`, and write it nowhere. That is the reason the "email the submitter on
publish" Action does not exist, and it is the same reason there is no outreach
queue: the ceiling is deliberate.

`venue_outreach.py` is the backlink half of the crawl-budget problem. Internal
linking is what a new domain can do for itself; an inbound link from the
venue's own site is the part it cannot, and the venue has a real reason to give
one, because the page is about them. The second effect is the one that pays
sooner: a venue that likes the page often posts it to their own audience.

Details that matter in the draft:

- **The correction offer comes before the link ask.** The correction is the
  part with value for the venue and the part that gets a reply; leading with
  the ask makes the whole email read as link-begging.
- **Every number and date is read from the live feed**, so the venue can
  verify the email by clicking one link. The ask only lands if what is being
  offered is obviously already real.
- **The calendar line only appears when that venue's `calendar.ics` actually
  exists on disk.** The per-venue files are written by the nightly build, so
  between shipping a change and the next run they do not — and a second link
  that 404s undoes the one thing the email is trying to prove.
- The venue-slug mapping is `_split_area()` + `_venue_slug()` **imported** from
  `fetch_events.py`, not reimplemented. Get it wrong and you send a venue a
  link to somebody else's page.
- `--list` ranks by upcoming show count and flags likely arenas/sheds from
  their name. It is a guess shown for skipping, never a filter: a
  Ticketmaster-fed arena has a marketing department, no incentive to link out,
  and nobody who answers this email.

Bulk-mailing 48 venues from a script is how a new domain becomes a spam
complaint. 48 personal emails sent by a person over a couple of weeks is how it
gets 48 links.

## Daily social post

`.github/workflows/social-post.yml` posts three pick-lists a day -- "morning",
"midday" and "afternoon" -- to the Facebook Page and the linked Instagram
Business account, from `live-events.json`. `scripts/social_post.py` is the
whole thing; `scripts/social_card.py` only draws the images.

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

**The two platforms get different media.** Facebook still gets one dense card
(`render_card()`, all three picks) — its caption already carries the full text
and there's no algorithmic reward on FB for a format change.

**Instagram gets a Reel, since 2026-09-10** (`IG_FORMAT` in `social_post.py`;
set it to `"carousel"` to restore exactly what ran between 2026-09-05 and
then). It is the same cover-plus-one-slide-per-pick sequence the carousel
used, drawn at 9:16 and muxed into a ~11s MP4 by `render_reel()`, posted with
`post_instagram_reel()`. A carousel beats a single image — that was the
2026-09-05 change — but both are *feed* posts, shown mostly to existing
followers; a Reel is the only native format Instagram pushes to people who
don't follow the account, which is the whole problem for an account this new.
**Never post both in one slot**: they would be the same three picks twice.

`render_reel()` shells out to **ffmpeg**, which is *not* a pip dependency and
**is not preinstalled on `ubuntu-latest`** — `social-post.yml` installs it with
apt in its own step before the build. That was assumed the other way on
2026-09-10 and disproved the same day by a dry run on a throwaway branch; if
you are tempted to drop the install step because "the runner has ffmpeg",
it does not. `_ffmpeg()` names that step as the cause when the binary is
missing instead of raising a bare `FileNotFoundError`. `verify_reel()` is `verify_card()` for video and
asserts the Reels spec locally, for the same reason: a bad container comes
back from Meta as a generic "media upload failed" naming no field.

The frames are **the carousel's own renderers** at a different size —
`render_cover_slide()` / `render_pick_slide()` take `size`, `scale`,
`safe_bottom`, `swipe` and `max_name_lines`, and that is the entire
difference. A second set of reel-only drawing code would be exactly the
two-layer-mirror drift this file warns about elsewhere. Three of those
parameters are not cosmetic: `safe_bottom` (340px) keeps the footer out from
under Instagram's caption overlay; `scale` (1.3) exists because a 1920-tall
frame is 42% taller than the carousel slide but exactly as wide, so type sized
for 4:5 reads small in it; and `max_name_lines` (6, against the carousel's 4)
is required *by* that scale — at 1.3x a long name wraps past four lines, and
leaving the cap alone made the bigger format ellipsize names the smaller one
printed in full.

The reel commits **one** file a day where the carousel committed four: the
9:16 frames are rendered into a temp directory and only the MP4 is kept.
`prune_cards()` deletes from the working tree but git keeps every card this
site has ever posted, in a public repo, forever — so the frames are cuts over
static images with a short crossfade rather than a pan/zoom, which would make
every frame differ from the last and multiply the bitrate for decoration.
A silent AAC track is muxed in deliberately; Meta's spec states audio
requirements without stating audio is optional.

Each pick slide's category tag renders in that
category's own site color (`CATEGORY_COLOR`, mirroring `CATEGORIES` in
`js/data.js` the same way `_split_area()` mirrors `splitArea()`) instead of
the brand green everything else uses — color that changes slide to slide is
itself a reason to keep swiping, not decoration.

**A pick slide's content block is vertically centered, not pinned under the
masthead.** The first version top-anchored everything, and a one-line name
("Mo Amer") next to a four-line one ("Half Foot Hog, Asshats, Kiss With Your
Teeth") left the short-name slides looking roughly half-empty while the long
one filled the frame — inconsistent density between slides of the *same*
carousel reads as unfinished, not intentional. `render_pick_slide()` measures
the wrapped name first and centers the whole block (tag + name + meta) in the
middle band between the masthead and the footer, so both cases land with the
same visual weight.

**The carousel's `children` parameter is a comma-separated string
("id1,id2,id3"), not the JSON array Meta's own reference page describes it
as** — verified against multiple independent working examples on 2026-09-05
rather than trusted from the doc text, the same kind of doc/reality gap that
already cost one wrong guess this session on the Page-token failure message.
Carousel children also take NO caption (Instagram rejects it); the caption
lives on the parent container only. Every container — each child, then the
parent — is polled to FINISHED (`_poll_container()`) before the next step,
the same caution the original single-image flow used, so an unfinished child
referenced by the parent never surfaces as an opaque "media upload failed"
with no field naming which slide was the problem.

**Why the card is ours and not the feed's `image`.** Measured 2026-08-28: 647
of 667 rows carry one, but 105 are 280x210 SeatGeek thumbnails (under
Instagram's 320px floor), several hosts serve PNG (the API takes JPEG and
nothing else), and the 485 usable Ticketmaster JPEGs are the promoter's
copyrighted key art — not something to repost daily under our own handle
forever. `verify_card()` asserts every constraint in the media spec locally,
because a bad container comes back as a generic "media upload failed" with no
field naming the reason. It runs on every slide of the carousel too, not just
the Facebook card.

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
again (e.g. a new account needs warming up), comment out all six `cron:` lines
in `social-post.yml`'s `schedule:` block — `workflow_dispatch` still works for
a dry run either way.

**Three posts a day, never the same events**, since 2026-09-02: `morning`
targets ~8:00 AM Dallas, `midday` ~12:00 PM, `afternoon` ~3:00 PM — all
pinned to CDT; once Dallas falls back to CST in November every trigger reads
an hour earlier on the clock until DST resumes, the same tolerance already
accepted for `fetch-events.yml`'s own cron comments. `github.event.schedule`
in the workflow tells the job which cron fired — **this is load-bearing, not
cosmetic**: a run landing near a given hour is not proof of which slot it
is, which is exactly what caused a misread on 2026-09-01 (a 4h07m-late
morning run was briefly mistaken for an on-time midday one by timestamp
alone). A manual dispatch has no schedule string, so it falls back to a
`slot` input.

**The cron trigger times are NOT the target times.** Each slot's PRIMARY
cron is scheduled ~4h before its real target (morning 09:00 UTC for an
8:00 AM target, midday 13:00 UTC for noon, afternoon 16:00 UTC for 3:00 PM),
betting on GitHub's own typical delay to carry the actual run to near the
target instead of hours past it. This is a deliberate compensation, added
2026-09-02 after measuring `fetch-events.yml`'s full run history (a workflow
this one never touches) and finding every recorded run over 8 samples
delayed 2.56–5.07h behind its nominal cron time (mean 3.93h, median 4.68h) —
a property of GitHub's scheduler on this repo during roughly 4 AM–1 PM
Eastern (its own peak shared-runner demand window), not a bug in either
workflow. It is a best-effort correction against a noisy distribution, not a
guarantee: a below-average delay lands a post noticeably early of target, an
above-average one lands it noticeably late.

**Each slot still has a catch-up cron too**, 5h after its own primary (so
morning: 09:00 + 14:00 UTC; midday: 13:00 + 18:00 UTC; afternoon: 16:00 +
21:00 UTC) — the same self-heal shape `fetch-events.yml` uses, first added
2026-09-01 after the first two days of real operation proved a single cron
per slot necessary but insufficient: 2026-08-31's only schedule-triggered
run fired 4h42m late, and on 2026-09-01 the morning cron fired 4h07m late
while midday never fired at all as of 1h42m past due (both posted manually
instead that day). **The catch-up cron is itself a scheduled run and
inherits the same delay** — it is a second independent chance, not a faster
or more reliable one, so it shrinks the odds a whole slot is missed outright
without making any post land "on time" in a strict sense. A day that
genuinely needs a post out by a deadline needs a manual dispatch (Actions →
Daily social post → Run workflow), not a shorter cron gap — no gap here
escapes GitHub's own queue. The catch-up works because build is idempotent
per (date, slot): if the primary already posted, the catch-up's `already
posted` check no-ops it; if the primary was dropped or still queued, the
catch-up does the real work.

One accepted overlap between the two workflows: morning's primary (09:00
UTC) exactly matches `fetch-events.yml`'s own primary cron. Both push
through `push-with-retry.sh`, so the collision costs a retry, not a broken
run. Within `social-post.yml` itself, all six cron strings are distinct —
they have to be, since `github.event.schedule`'s case match in "Determine
slot" can only map one slot name per unique string.

A day's second slot must never repeat the first's picks, so
`social/posted.json` is nested `date -> slot -> {facebook, instagram, picks,
venues}`, and `already_posted_today()` hard-excludes (in `select_picks()`,
before scoring even starts) any event a same-day earlier slot already
featured — see that function's docstring for why this is a hard filter and
not the same -6 soft penalty `recent_venues` applies across days. On a day
thin enough that a later slot has nothing left to post, it skips cleanly
rather than repeating an earlier slot's picks or posting an empty card. Cards
are named `<date>-<slot>.jpg` so no two of the day's posts ever share a file.
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
  Instagram fetch 404s. The reel is a `.mp4` under the same directory, so the
  existing `git add -A social` covers it.
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

`_dallas_offset()` (`fetch_events.py`) / `dallasOffset()` (`js/app.js`) is the
timezone half of that same schema.org pair. **Both hardcoded `-05:00` until
2026-09-11** — correct on CDT, an hour wrong from early November to mid-March,
and `startDate` is an instant, so half the year of listings told Google the
wrong one. Both now ask their platform (`zoneinfo` / `Intl`) for the offset at
**12:00 UTC on the event's date** — morning in Dallas, so always the same
calendar day and safely past the 2 AM switch.

Resolving per-date rather than per-instant is what makes the two layers
trivially comparable; the exact cost is that an event starting between
midnight and 2 AM on one of the two transition days a year takes the rest of
that day's offset. Cross-check by running both over every date in
`live-events.json` plus the transition dates — they agreed on all 41 on
2026-09-11. `js/app.js` cannot be syntax-checked on this machine, so that
check runs in the browser preview.

Note the ICS feeds do **not** use this: `_event_start()` resolves the real
instant through `ZoneInfo`, because a calendar entry is a promise about a
moment and has no mirror to stay comparable with.

`SOCIAL` (`fetch_events.py`) is a three-way one, and the only one where two of
the three copies are hand-written markup: the constant, index.html's `sameAs`,
and index.html's footer column. See "Internal links are the crawl budget".

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
