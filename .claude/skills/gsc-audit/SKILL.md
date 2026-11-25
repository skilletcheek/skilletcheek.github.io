---
name: gsc-audit
description: Monthly Google Search Console audit for letsdoitdallas.com. Pulls queries and pages from Search Console in the built-in browser, finds deleted pages that still get impressions, low-CTR titles, near-page-one rankings and search intents with no page, compares with last month, and proposes ranked fixes. Use when asked to "run the GSC audit", "do the Search Console audit", "check search performance", or on the monthly schedule.
---

# Monthly Search Console audit

The first audit (2026-11-25) found 17% of all impressions pointing at deleted
venue pages, titles that didn't use the words people searched, and that event
deep links (`/?date=…&e=…`) earned about half of all clicks. Each finding
became a shipped fix. Repeat the same checks monthly; the point is to catch
the next one of those early.

**Read-only until the user says otherwise.** The audit reports and proposes.
It changes no code, data or generated file without the user's go-ahead on a
specific fix. Never run `main()` (see CLAUDE.md).

## 1. Pull the data

Use the built-in browser (`mcp__Claude_Browser__*`). The Google sign-in
persists in its profile, but if the page lands on `accounts.google.com`, stop
and ask the user to sign in in the pane (check `tabs_context` first; if the
pane is hidden, ask them to press Cmd+Shift+B). Never type credentials.

Open, with all four metrics already on:

    https://search.google.com/search-console/performance/search-analytics?resource_id=sc-domain%3Aletsdoitdallas.com&num_of_days=28&metrics=CLICKS%2CIMPRESSIONS%2CCTR%2CPOSITION

Use `num_of_days=28` for the month and `num_of_days=90` for context.
Read the totals with `get_page_text`. Then set **Rows per page** to 500
(`find` "Number of rows per page", click it, click "500") and extract the
table with `javascript_tool`:

```js
const t=[...document.querySelectorAll('table')].find(t=>/Top (queries|pages)/.test(t.querySelector('thead')?.innerText||''));
[...t.querySelectorAll('tbody tr')].map(tr=>[...tr.querySelectorAll('td')].map(td=>td.innerText.split('\n')[0].trim().replace('https://letsdoitdallas.com','').replace(/ Copy URL.*$/,''))).filter(r=>r.length>=5).map(r=>r.join('\t')).join('\n')
```

Do it for **Queries**, then click the **Pages** tab (`find` "Pages") and
run it again. Columns are name, clicks, impressions, CTR, position.

Save both as TSV in the **scratchpad**, never in the repo.

## 2. Checks, in this order

Work from `git pull --rebase origin main` so the checkout matches the live
site.

1. **Pages with impressions that no longer exist.** For every `/venue/`,
   `/district/`, `/city/` and `/things-to-do/` URL in the Pages table, check
   whether its `index.html` exists. A missing venue page should not happen any
   more (see `VENUE_GRACE_DAYS` and `venue/pages.json`); if one does, find out
   why from `git log --diff-filter=D`. Also flag ranked URLs that are
   **dormant** (noindexed in `venue/pages.json` with no events) — that is
   expected, but say how many impressions sit on them.
2. **Duplicates.** Two venue URLs for one place (`ed-rae-…` / `ed-and-rae-…`,
   `wyly-theatre` / `wyly-theatre-at-t-…`) mean a missing alias. The fix is
   data in `venue-aliases.json`, following its rules (never two rooms in one
   building). Point the alias at the URL with the impressions.
3. **CTR on page one.** Position ≤ 12, impressions ≥ 20, CTR under ~3%: the
   title or description does not match the query. Put the top queries for
   that page beside its current `<title>` (split the file on `</head>`).
   Titles are generated: `_district_title()`, the `emit()` calls in
   `write_hubs()`, `_venue_html()`, `topic_pages` in `scripts/feeds.json`.
4. **Near page one.** Position 11–25 with real impressions: closest to paying
   off. Say what the page lacks against the query (thin listings, no
   evergreen content, weak internal links).
5. **Intents with no page.** Group queries by intent ("… tonight", "free …",
   "christmas …", a venue name, a neighbourhood). An intent with impressions
   and no matching page is a `topic_pages` candidate. Read `_topic_note`
   first: theater and date night were tried and rejected.
6. **Event deep links.** Share of clicks from `/?date=…&e=…` URLs and their
   positions. Watch the trend; a static page per event is the open idea.
7. **Last month's fixes.** For every action in the previous history entry,
   did its page's position, CTR or clicks move? Say so plainly, including
   "no change yet" (Google takes weeks).

## 3. History and report

History lives in `.claude/skills/gsc-audit/history/` (gitignored; the repo is
public). Read the latest `YYYY-MM.md` there first, and write this month's
when done:

```markdown
# GSC audit YYYY-MM-DD
28 days: clicks N, impressions N, CTR N%, avg position N
90 days: clicks N, impressions N, CTR N%, avg position N
Top pages by clicks: …
Findings: one line each
Actions proposed: numbered; mark which the user shipped
```

Report to the user:

- the headline numbers against last month
- findings, biggest impressions at stake first, each with the numbers behind
  it and the specific fix (file and function)
- what last month's fixes did

Then ask which fix to do first. Don't start one unprompted.
