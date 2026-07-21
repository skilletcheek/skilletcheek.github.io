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
    partners.json           DATA: founding-partner wall
    live-events.json        GENERATED nightly
    press.json              GENERATED nightly

## Generated files — never hand-edit

`fetch_events.py` regenerates these nightly and pushes to `main`. Hand edits
get clobbered; change the **Python** instead:

- `live-events.json`, `press.json`, `sitemap.xml`, `robots.txt`
- `/tonight/`, `/this-weekend/`, `/free-events/`, `/district/*/` hub pages
- `/advertise/` (`write_advertise()`), `/submit/` (`write_submit()`)

`/advertise/` and `/submit/` read `CONFIG` values from `js/data.js` at **build
time** — after changing an endpoint there, regenerate the page.

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

## Local shell

zsh, which does **not** word-split unquoted variables. `R="--resolve h:p:ip";
curl $R url` passes one giant argument and fails. Quote the flag or use an
array.
