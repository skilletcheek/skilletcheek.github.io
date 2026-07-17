# RJ Does Dallas 🤠

Your daily guide to Dallas–Fort Worth. Pick any day and see what's happening —
live music, markets, festivals, sports, family fun and more.

It's a fast, dependency-free static site: plain HTML/CSS/JS, no build step, no
framework. It runs three ways at once:

1. **Curated recurring venues** (baked in) — always works, even offline.
2. **A hand-editable `events.json`** — add one-off events without touching code.
3. **Live event APIs** (Ticketmaster, optional PredictHQ, optional Google Sheet).

---

## Run it locally

Because the app fetches `events.json`, open it through a tiny web server rather
than double-clicking (browsers block `fetch` on bare `file://`):

```bash
cd rj-does-dallas
python3 -m http.server 8080
# then visit http://localhost:8080
```

(Double-clicking `index.html` still works — you just won't get the JSON/live
feeds, only the curated listings.)

---

## Put it on the internet (with free HTTPS)

Every host below issues and renews a TLS certificate for you automatically — you
do **not** buy or install a cert. Pick one:

### Option A — Netlify (drag-and-drop, easiest)
1. Create a free account at https://netlify.com
2. Go to **Add new site → Deploy manually**.
3. Drag the whole `rj-does-dallas` folder onto the page.
4. You get a live `https://your-name.netlify.app` URL in ~20 seconds.
   `netlify.toml` (included) sets caching + security headers automatically.

### Option B — Vercel (CLI)
```bash
npm i -g vercel
cd rj-does-dallas
vercel        # log in, accept defaults
vercel --prod # promote to your production URL
```
`vercel.json` (included) handles headers.

### Option C — Cloudflare Pages
1. Push this folder to a GitHub repo.
2. In Cloudflare dashboard → **Pages → Create → Connect to Git**.
3. Framework preset: **None**. Build command: *(blank)*. Output dir: `/`.

### Option D — GitHub Pages (free, GitHub-hosted)
1. Create a repo, push these files.
2. Repo **Settings → Pages → Deploy from branch → main / root**.
3. Live at `https://<user>.github.io/<repo>/` with HTTPS on.

### Custom domain (e.g. rjdoesdallas.com)
Buy the domain (Namecheap/Cloudflare/Porkbun, ~$10/yr). In your host's dashboard
add the domain and follow its DNS instructions. HTTPS for the custom domain is
still provisioned free and automatically.

---

## Add real live events

Open `js/data.js` and fill in the `CONFIG` block:

```js
ticketmasterApiKey: "YOUR_KEY",   // free at developer.ticketmaster.com
```

**Ticketmaster (recommended, free):**
1. Sign up at https://developer-acct.ticketmaster.com
2. Create an app → copy the **Consumer Key**.
3. Paste it into `CONFIG.ticketmasterApiKey`. Done — concerts, sports and
   theatre across a 40-mile DFW radius now appear on the day they occur.

**Google Sheet (let non-coders add events):**
1. Make a sheet with columns: `name, category, area, date, time, cost, description, url`
   (`date` as `YYYY-MM-DD`, `category` one of the keys in `CATEGORIES`).
2. **File → Share → Publish to web → CSV**.
3. Paste that URL into `CONFIG.googleSheetCsvUrl`.

**events.json:** just edit the file — same columns, ships with the site.

> Note: API keys placed in `CONFIG` are visible in the browser (it's a static
> site). Ticketmaster keys are meant to be public/rate-limited, so that's fine.
> For anything secret, put a serverless function in front (ask and I'll add one).

---

## Monetization — how this makes money

The site is built with revenue hooks already in place. Fastest → slowest to earn:

| # | Revenue stream | Where it lives | How to turn it on |
|---|----------------|----------------|-------------------|
| 1 | **Sponsored/featured listings** — sell a pinned, gold-badged spot to a venue | `SPONSORED` array in `js/data.js` | Add an entry with `sponsor` + `sponsorUntil`. Charge $50–300/mo per pin. Highest-margin, works from day one. |
| 2 | **"Feature your event" upsell** — paid boost after someone submits | Submit-event modal + `SPONSORED` | Charge $10–25 to promote a submitted event. |
| 3 | **Display ads** — leaderboard + in-grid native card | `adsEnabled` in `CONFIG`, slots already laid out | Apply to Google AdSense (low traffic ok) or Ezoic/Mediavine (needs traffic). Flip `adsEnabled: true`. |
| 4 | **Affiliate ticket links** — commission on tickets sold | `affiliateTag` in `CONFIG`, `withAffiliate()` wraps outbound links | Join Ticketmaster/StubHub/Viator affiliate programs, set your tag. |
| 5 | **Newsletter sponsorship** — sell the Thursday email to local businesses | Newsletter signup band | Connect a list (Mailchimp/Beehiiv/Buttondown), then sell a sponsor slot per issue. |
| 6 | **Local business directory / "Pro" venue profiles** | new section (ask me to build) | Monthly subscription for venues to maintain a rich profile. |

**Realistic path:** start with #1 and #5 (direct sales to venues — no traffic
threshold, best margins), add #3 and #4 once you have steady visitors, and let
the newsletter compound the audience that makes all of them worth more.

To wire up the forms for real:
- `CONFIG.newsletterEndpoint` → a Formspree/Mailchimp/Beehiiv form URL.
- `CONFIG.submitEventEndpoint` → a Formspree URL (free tier is plenty to start).

---

## File map

```
rj-does-dallas/
├── index.html        # markup + SEO/social tags
├── css/styles.css    # all styling (dark + light, responsive)
├── js/data.js        # ← EDIT HERE: config, categories, curated + sponsored events
├── js/sources.js     # live loaders (Ticketmaster / PredictHQ / Sheet / JSON)
├── js/app.js         # recurrence engine, filtering, modal, calendar, favorites
├── events.json       # hand-editable one-off events
├── netlify.toml      # Netlify headers/caching
├── vercel.json       # Vercel headers/caching
└── README.md
```

## Features already built in
- Pick any date (arrows, calendar, Today/Tomorrow/This Weekend, ← → keys)
- Category filters, free-only toggle, search, sorting
- Event detail modal with **Add to calendar (.ics)**, **Map**, **Share**
- Save favorites (stored in the browser)
- Sponsored listings pinned with a gold badge
- Newsletter capture + Submit-an-event form
- Dark/light theme (follows the visitor's system), mobile-responsive
- SEO + Open Graph tags for nice link previews
