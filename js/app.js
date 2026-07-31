/* =========================================================================
 *  Lets Do It Dallas — application logic
 *  All original functionality (recurrence engine, sources, filters, faves,
 *  calendar export, forms, sponsored pinning) is preserved; this build adds
 *  the status bar, vibe filters, district radar wiring, live-now detection,
 *  JSON-LD injection, dynamic meta, URL params and the slide-out drawer.
 *  ========================================================================= */

/* ---- recurrence engine (curated + sponsored) ---------------------------- */
function _mmdd(d) {
  return String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
}
function _inRange(date, start, end) {
  const cur = _mmdd(date);
  return start <= end ? (cur >= start && cur <= end) : (cur >= start || cur <= end);
}
function _nthWeekday(date) { return Math.floor((date.getDate() - 1) / 7) + 1; }

function happensOn(activity, date) {
  const r = activity.recur;
  if (!r) return false;
  const dow = date.getDay();
  if (r.daily) return true;
  if (r.weekly) return r.weekly.includes(dow);
  if (r.monthly) return dow === r.monthly.day && _nthWeekday(date) === r.monthly.week;
  if (r.dateRange) {
    if (!_inRange(date, r.dateRange.start, r.dateRange.end)) return false;
    return r.dateRange.weekly ? r.dateRange.weekly.includes(dow) : true;
  }
  return false;
}

/* ---- state -------------------------------------------------------------- */
const state = {
  date: new Date(),
  activeCats: new Set(),
  vibes: new Set(),
  district: null,
  city: null,             // canonical city name from cityOf(), or null for all
  search: "",
  sort: "time",
  freeOnly: false,
  favesOnly: false,
  live: [],
  liveStamp: 0,          // bumped whenever state.live is replaced (memo key)
  loadingLive: false,
  openEvent: null,       // uid of the event in the drawer, mirrored to ?e=
  pendingEvent: null,    // ?e= read at boot, opened once the day's list exists
  faves: new Set(JSON.parse(localStorage.getItem("rjdd:faves") || "[]")),
};

const el = (id) => document.getElementById(id);

/* Event text comes from third-party feeds (Ticketmaster, Eventbrite, Prekindle,
   the Google Sheet) and goes straight into innerHTML, so it must be escaped.
   This is not hypothetical: a real listing titled
   "2026 CORTIS TOUR <PUT YOUR PHONE DOWN> IN IRVING" rendered as
   "2026 CORTIS TOUR  IN IRVING" — the browser parsed the angle brackets as a
   tag and ate the words. Titles containing a double quote broke out of
   data-id="…" and left the card with no working click handler at all. */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* Feed URLs land in href/src attributes. Anything that is not http(s) — a
   javascript: or data: URL from a compromised or sloppy feed — becomes a
   harmless "#" rather than something the visitor can click into. */
function safeUrl(u) {
  const s = String(u ?? "").trim();
  return /^https?:\/\//i.test(s) ? s : "#";
}

/* Split a free-text `area` into [venue, street, city] for schema.org.

   Mirror of _split_area() in scripts/fetch_events.py — see that docstring for
   the shapes involved and why a missing city stays missing rather than being
   guessed at. Any element may be null; the caller omits the field. */
const _ADDR_NOISE = /^(united states|usa|us|tx|texas)$/i;
const _POSTAL = /^\d{5}(-\d{4})?$/;

function splitArea(area) {
  const parts = String(area ?? "").split(",").map((p) => p.trim())
    .filter((p) => p && !_ADDR_NOISE.test(p) && !_POSTAL.test(p)
      // If row()'s length cap in fetch_events.py ever severs an address, it
      // lands mid-word in the trailing country, leaving "Un" -- short enough
      // to otherwise survive as a locality. Any prefix of "united states" is
      // that artifact; no DFW city collides ("Union" diverges at the fourth
      // character). Ported from _split_area()'s noise() after that exact
      // truncation ("...Fort Worth, 76102, Un") made this pick "Un" as the
      // city instead of "Fort Worth" -- the cap has since been raised, but
      // this stays as a second line of defense.
      && !(p.length >= 2 && "united states".startsWith(p.toLowerCase())));
  if (!parts.length) return [null, null, null];
  if (parts.length === 1) return [parts[0], null, null];
  const [venue, ...rest] = parts;
  const street = rest.filter((p) => /^\d/.test(p));
  const city = rest.filter((p) => !street.includes(p));
  return [venue, street.join(", ") || null, city.length ? city[city.length - 1] : null];
}

/* ---- city resolution -----------------------------------------------------

   Mirror of DFW_CITIES / DISTRICT_CITY / _CITY_CASE / _city_of() in
   scripts/fetch_events.py. _check_city_drift() warns during the nightly build
   if the two sets stop agreeing — a city that exists on one side only would
   filter events out of the list on one layer and not the other.

   live-events.json rows carry a build-time `city`, so cityOf() usually just
   reads the field. The derive path is not dead code and cannot be dropped:
   the curated rows in js/data.js have no city field at all, eventbrite.json
   is refreshed by hand and keeps whatever schema it had at the time, and
   every row lacks the field until the next nightly build runs. */
const DFW_CITIES = new Set([
  "addison", "allen", "anna", "argyle", "arlington", "arlington heights",
  "aubrey", "azle", "balch springs", "bedford", "benbrook", "burleson",
  "carrollton", "cedar hill", "cleburne", "colleyville", "coppell",
  "corinth", "crowley", "dallas", "denton", "desoto", "duncanville",
  "ennis", "euless", "farmers branch", "farmersville", "flower mound",
  "forney", "fort worth", "frisco", "garland", "glenn heights", "granbury",
  "grand prairie", "grapevine", "greenville", "haltom city",
  "highland park", "highland village", "hurst", "irving", "justin",
  "keller", "lancaster", "las colinas", "lewisville", "little elm",
  "mansfield", "mckinney", "melissa", "mesquite", "midlothian", "murphy",
  "north richland hills", "plano", "prosper", "red oak", "richardson",
  "roanoke", "rockwall", "rowlett", "sachse", "saginaw", "sanger",
  "seagoville", "southlake", "terrell", "the colony", "trophy club",
  "university park", "watauga", "waxahachie", "weatherford", "westlake",
  "wylie",
]);

/* Sub-city neighborhoods only. Municipalities that happen to be surrounded by
   a bigger city — University Park, Highland Park, Las Colinas — stay out of
   this map on purpose and filter as themselves. */
const DISTRICT_CITY = {
  "downtown dallas": "Dallas",
  "victory park": "Dallas",
  "deep ellum": "Dallas",
  "arts district": "Dallas",
  "uptown": "Dallas",
  "oak cliff": "Dallas",
  "bishop arts": "Dallas",
  "design district": "Dallas",
  "lower greenville": "Dallas",
  "east dallas": "Dallas",
  "northwest dallas": "Dallas",
  "southside": "Fort Worth",
  "stockyards": "Fort Worth",
};

/* Title-casing is wrong for these and only these. */
const _CITY_CASE = { mckinney: "McKinney", desoto: "DeSoto" };

function cityOf(a) {
  if (a && a.city) return a.city;
  const [venue, , city] = splitArea(a && a.area);
  // The venue slot is consulted because splitArea() returns a bare label
  // ("Lower Greenville") there with no city. Safe only because both lookups
  // below are whitelists — a real venue name matches neither and yields null.
  const cand = String(city || venue || "").trim().toLowerCase();
  if (!cand) return null;
  if (DISTRICT_CITY[cand]) return DISTRICT_CITY[cand];
  if (DFW_CITIES.has(cand)) {
    return _CITY_CASE[cand] || cand.replace(/\b[a-z]/g, (c) => c.toUpperCase());
  }
  return null;
}

/* Mirror of _display_area() in scripts/fetch_events.py: "Tulips FTW · Fort
   Worth" instead of the raw feed string. cardHtml() and the OPEN NOW rail
   printed a.area directly, which is fine for "Downtown Dallas" but leaks the
   full postal address -- street, ZIP, "United States" -- for any venue the
   feed didn't already shorten, wrapping a row built to be one line into two
   or three. Deliberately not used in the drawer or the map/ICS/share strings:
   there, the full address is the useful form, not noise. */
function displayArea(area) {
  const [venue, , city] = splitArea(area);
  if (venue && city && !venue.toLowerCase().includes(city.toLowerCase())) return `${venue} · ${city}`;
  return venue || area || "";
}

/* Stable per-event identity, used for favorites and for matching a clicked
   card back to its data. Must include the time: venues run the same act twice
   in one night (a 7:30 and a 9:45 set) at the same address, and keying on
   name+area alone made those two cards indistinguishable — clicking the late
   show opened the early show's drawer and its ticket link. */
const uid = (a) => `${a.name}|${a.area}|${a.time}`.toLowerCase().replace(/[^a-z0-9|]+/g, "-");

/* How many real events a visitor sees before the unsold-inventory house ad.
   Low enough to still be seen, high enough that the page opens with events. */
const HOUSE_AD_SLOT = 4;

/* ---- date/time helpers --------------------------------------------------- */
function fmtDate(d) {
  return d.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
}
function isoDate(d) {
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
}
function parseTimeToMinutes(t) {
  const m = String(t).match(/(\d{1,2}):?(\d{2})?\s*(AM|PM)/i);
  if (!m) return 24 * 60;
  let h = parseInt(m[1], 10);
  const min = m[2] ? parseInt(m[2], 10) : 0;
  const ap = m[3].toUpperCase();
  if (ap === "PM" && h !== 12) h += 12;
  if (ap === "AM" && h === 12) h = 0;
  return h * 60 + min;
}
function timeRange(t) {
  const parts = String(t).split(/[–—-]/);
  const start = parseTimeToMinutes(parts[0]);
  let end = parts[1] ? parseTimeToMinutes(parts[1]) : start + 150;
  if (/late/i.test(t)) end = 26 * 60;
  if (end < start) end += 24 * 60;           // ranges crossing midnight
  return [start, end];
}
function isToday(d) {
  const n = new Date();
  return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate();
}
function nowMins() { const n = new Date(); return n.getHours() * 60 + n.getMinutes(); }

function isLiveNow(a) {
  if (!isToday(state.date)) return false;
  if (parseTimeToMinutes(a.time) >= 24 * 60) return false;
  const [s, e] = timeRange(a.time);
  const n = nowMins();
  return (n >= s && n <= e) || (n + 24 * 60 >= s && n + 24 * 60 <= e);
}

/* Before the evening, "12 LIVE NOW" mostly means parks and museums are open —
   say so. After 5 PM the word earns its stage energy. */
function liveWord() { return new Date().getHours() < 17 ? "OPEN" : "LIVE"; }

/* ---- vibes (derived from existing fields only) --------------------------- */
const VIBES = {
  "chill":      { label: "CHILL & ACOUSTIC", test: (a) => ["arts", "outdoors", "market"].includes(a.cat) || /jazz|acoustic|garden|trail|museum|story|stroll/i.test(a.name + " " + (a.desc || "")) },
  "high":       { label: "HIGH ENERGY", test: (a) => ["sports", "nightlife", "festival"].includes(a.cat) || /crawl|rodeo|honky|concert|country/i.test(a.name + " " + (a.desc || "")) },
  "late":       { label: "LATE NIGHT", test: (a) => parseTimeToMinutes(a.time) >= 21 * 60 || /late|midnight|2:00 AM/i.test(a.time) },
  "date":       { label: "FIRST DATE APPROVED", test: (a) => ["arts", "food", "music"].includes(a.cat) && (a.cost == null || a.cost <= 30) },
  "solo":       { label: "SOLO EXPLORER", test: (a) => ["arts", "outdoors", "market"].includes(a.cat) },
  "group":      { label: "GROUP OUTING", test: (a) => ["sports", "nightlife", "festival", "food"].includes(a.cat) },
  "next2h":     { label: "IN NEXT 2 HOURS", test: (a) => { if (!isToday(state.date)) return false; const s = parseTimeToMinutes(a.time); const n = nowMins(); return s >= n && s <= n + 120; } },
  "gems":       { label: "HIDDEN GEMS", test: (a) => ["curated", "json", "sheet", "sponsored"].includes(a.source) && (a.cost == null || a.cost <= 15) },
};

/* ---- data assembly ------------------------------------------------------- */
function sponsoredForDate(date) {
  const iso = isoDate(date);
  return SPONSORED
    .filter((s) => !s.sponsorUntil || s.sponsorUntil >= iso)
    .filter((s) => happensOn(s, date))
    .map((s) => ({ ...s, source: "sponsored", sponsor: s.sponsor || "Sponsored" }));
}

/* A single render asks for the day's list up to four times (grid, radar, ON NOW
   rail, status count), and each call re-ran the recurrence engine over every
   curated activity and rebuilt the array. Memoize it for the current date +
   live payload; refreshLive() and any state change that swaps state.live bumps
   the stamp, so this can never serve a stale day. */
let _dayCache = { key: null, list: null };
function baseListForDate(date) {
  const key = isoDate(date) + "|" + state.liveStamp;
  if (_dayCache.key === key) return _dayCache.list;
  const curated = ACTIVITIES.filter((a) => happensOn(a, date)).map((a) => ({ ...a, source: "curated" }));
  const seen = new Set(curated.map((c) => c.name.toLowerCase()));
  const liveClean = state.live.filter((l) => !seen.has((l.name || "").toLowerCase()));
  _dayCache = { key, list: [...curated, ...liveClean] };
  return _dayCache.list;
}

function applyFilters(list) {
  const q = state.search.trim().toLowerCase();
  let out = list.slice();
  if (state.activeCats.size) out = out.filter((a) => state.activeCats.has(a.cat));
  if (state.district) out = out.filter((a) => RADAR.districtOf(a) === state.district);
  if (state.city) out = out.filter((a) => cityOf(a) === state.city);
  for (const v of state.vibes) out = out.filter((a) => VIBES[v].test(a));
  if (state.freeOnly) out = out.filter((a) => a.cost === 0);
  if (state.favesOnly) out = out.filter((a) => state.faves.has(uid(a)));
  if (q) out = out.filter((a) =>
    `${a.name} ${a.desc} ${a.area} ${(CATEGORIES[a.cat] || {}).label || ""}`.toLowerCase().includes(q));
  out.sort((a, b) => {
    if (state.sort === "name") return a.name.localeCompare(b.name);
    if (state.sort === "cost") return (a.cost ?? 999) - (b.cost ?? 999) || a.name.localeCompare(b.name);
    return parseTimeToMinutes(a.time) - parseTimeToMinutes(b.time) || a.name.localeCompare(b.name);
  });
  return out;
}

/* ---- filter bars --------------------------------------------------------- */
function buildFilters() {
  const box = el("filters");
  box.innerHTML = "";
  const mk = (label, active, onclick) => {
    const c = document.createElement("button");
    c.className = "chip" + (active ? " active" : "");
    c.textContent = label;
    c.onclick = onclick;
    return c;
  };
  box.appendChild(mk("ALL", state.activeCats.size === 0, () => { state.activeCats.clear(); render(); }));
  for (const [key, c] of Object.entries(CATEGORIES)) {
    box.appendChild(mk(c.label.toUpperCase(), state.activeCats.has(key), () => {
      state.activeCats.has(key) ? state.activeCats.delete(key) : state.activeCats.add(key);
      render();
    }));
  }
}
/* Rebuilt per day from that day's events, so the list only offers cities the
   visitor can actually get results in — a static list of all 76 DFW_CITIES
   would be mostly dead options on any given night.

   Counts are taken BEFORE the other filters run, on purpose: making them
   react to the category and vibe chips would leave the numbers shifting under
   the visitor between every click, and a city reading "(0)" that still had
   events on it reads as a bug. */
function buildCities() {
  const sel = el("citySel");
  if (!sel) return;
  const counts = new Map();
  for (const a of baseListForDate(state.date)) {
    const c = cityOf(a);
    if (c) counts.set(c, (counts.get(c) || 0) + 1);
  }
  // A city picked on a busy night must survive the date moving to a quiet one.
  // Without this the select would fall back to showing ALL CITIES while
  // state.city was still filtering — the control and the list disagreeing.
  if (state.city && !counts.has(state.city)) counts.set(state.city, 0);

  const opts = [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  sel.innerHTML = "";
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "ALL CITIES";
  sel.appendChild(all);
  for (const [city, n] of opts) {
    const o = document.createElement("option");
    o.value = city;
    o.textContent = `${city.toUpperCase()} (${n})`;
    sel.appendChild(o);
  }
  sel.value = state.city || "";
}

function buildVibes() {
  const box = el("vibes");
  if (!box) return;
  box.innerHTML = "";
  for (const [key, v] of Object.entries(VIBES)) {
    const c = document.createElement("button");
    c.className = "chip vibe" + (state.vibes.has(key) ? " active" : "");
    c.textContent = "/ " + v.label;
    c.onclick = () => {
      state.vibes.has(key) ? state.vibes.delete(key) : state.vibes.add(key);
      render();
    };
    box.appendChild(c);
  }
}

/* ---- cards --------------------------------------------------------------- */
function costBadge(a) {
  if (a.cost === 0) return `<span class="badge free">FREE</span>`;
  if (a.cost == null) return `<span class="badge">TICKETED</span>`;
  return `<span class="badge">$${a.cost}${a.cost >= 25 ? "+" : ""}</span>`;
}

function cardHtml(a, i) {
  const c = CATEGORIES[a.cat] || { label: "Event" };
  const fav = state.faves.has(uid(a));
  const sponsored = a.source === "sponsored" || a.sponsor;
  const live = isLiveNow(a);
  const districtSlug = RADAR.districtOf(a);
  const dLabel = districtSlug ? (DISTRICTS.find((d) => d.slug === districtSlug) || {}).label : null;
  // width/height match the .card-thumb box so the browser can reserve the space
  // before the remote image lands (and it satisfies Lighthouse's sizing audit)
  const thumb = a.image
    ? `<div class="card-thumb"><img src="${esc(safeUrl(a.image))}" alt="" width="64" height="64" loading="lazy" decoding="async" onerror="this.parentElement.remove()"></div>`
    : "";
  return `
    <article class="card ${sponsored ? "sponsored" : ""}" data-id="${esc(uid(a))}"
             style="--d:${Math.min((i || 0) * 40, 400)}ms">
      <div class="card-toprow">
        <span class="idx">(${String((i || 0) + 1).padStart(2, "0")})</span>
        <span class="tag">/ ${esc(c.label.toUpperCase())}</span>
        ${live ? `<span class="live-ring" title="Happening now"><i></i>${liveWord()}</span>` : ""}
        ${sponsored ? `<span class="spon">★ SPONSORED</span>` : ""}
      </div>
      <div class="card-mid">
        <div class="card-txt">
          <h3>${esc(a.name)}</h3>
          <!-- No date here: the grid only ever holds one day, and the toolbar
               above already names it. Repeating it printed the same string on
               all 91 cards and cost a line of height on each. -->
          <div class="meta">/ ${esc(String(a.time).toUpperCase())}</div>
          <div class="meta">/ ${esc((dLabel || displayArea(a.area) || "DFW").toUpperCase())}</div>
        </div>
        ${thumb}
      </div>
      <p class="desc">${esc(a.desc || "")}</p>
      <div class="card-foot">
        ${costBadge(a)}
        <div class="foot-actions">
          <button class="icon-btn fav ${fav ? "on" : ""}" data-act="fav" title="Save">${fav ? "♥" : "♡"}</button>
          <button class="icon-btn" data-act="open">DETAILS</button>
        </div>
      </div>
    </article>`;
}

function adCardHtml() {
  if (!CONFIG.adsEnabled) return "";
  return `<article class="card ad-card"><div class="ad-label">ADVERTISEMENT</div>
    <div class="ad-slot">Your 300×250 ad here</div></article>`;
}

/* ---- render -------------------------------------------------------------- */
/* Maps the data-id on a rendered card back to its event. Rebuilt each render. */
let gridIndex = new Map();

function wireGridDelegation() {
  const grid = el("grid");
  if (!grid) return;
  grid.addEventListener("click", (e) => {
    const cardEl = e.target.closest(".card");
    if (!cardEl) return;
    const item = gridIndex.get(cardEl.dataset.id);
    if (!item) return;
    const act = e.target.closest("[data-act]");
    if (act && act.dataset.act === "fav") { e.stopPropagation(); toggleFav(item); return; }
    openDrawer(item);
  });
}

function render() {
  el("dateDisplay").textContent = fmtDate(state.date);
  el("datePicker").value = isoDate(state.date);
  buildFilters();
  buildCities();
  buildVibes();
  updateQuickButtons();
  el("freeToggle").classList.toggle("active", state.freeOnly);
  el("faveToggle").classList.toggle("active", state.favesOnly);
  /* Must reproduce the .nav-lbl span from index.html — CSS drops it below 720px
     so the label reads "♥ (3)" and the three nav actions fit one row. Only
     interpolation is Set.size, an integer, so there's nothing to esc() here.
     display:none also drops the word from the a11y tree, hence the aria-label. */
  el("faveToggle").innerHTML = `♥ <span class="nav-lbl">SAVED </span>(${state.faves.size})`;
  el("faveToggle").setAttribute("aria-label", `Saved events (${state.faves.size})`);

  const q = state.search.trim().toLowerCase();
  const sponsored = sponsoredForDate(state.date).filter((s) => {
    if (state.activeCats.size && !state.activeCats.has(s.cat)) return false;
    if (state.freeOnly && s.cost !== 0) return false;
    if (state.district && RADAR.districtOf(s) !== state.district) return false;
    // A paid placement still has to be in the city the visitor asked for —
    // same call the district filter already makes one line up.
    if (state.city && cityOf(s) !== state.city) return false;
    if (state.favesOnly && !state.faves.has(uid(s))) return false;
    // sponsored pins must match an active search too — otherwise a fruitless
    // query returns the house ad as its only "result"
    if (q && !`${s.name} ${s.desc} ${s.area}`.toLowerCase().includes(q)) return false;
    return true;
  });
  const base = applyFilters(baseListForDate(state.date));
  const sponsoredIds = new Set(sponsored.map(uid));
  const organic = base.filter((b) => !sponsoredIds.has(uid(b)));

  // A paying sponsor bought position 1 — that is the product, so it pins.
  // The house ad has not; it used to take the same slot, which meant the first
  // thing every visitor met was a full-width pulsing advert for advert space
  // (an entire screen of it on a phone) before a single real event.
  const paid = sponsored.filter((s) => !s.house);
  const house = sponsored.filter((s) => s.house);
  const list = [...paid, ...organic];
  const total = list.length;                 // the ad is not an event; don't count it
  // and don't run it at all on a day with nothing to advertise against
  if (house.length && total) list.splice(Math.min(HOUSE_AD_SLOT, list.length), 0, ...house);
  el("count").innerHTML = state.loadingLive
    ? `${total} LISTED · <span class="live-loading">SYNCING LIVE FEEDS…</span>`
    : `${total} ${total === 1 ? "EVENT" : "EVENTS"} — ${fmtDate(state.date).toUpperCase()}${state.district ? " / " + state.district.replace(/-/g, " ").toUpperCase() : ""}${state.city ? " / " + esc(state.city.toUpperCase()) : ""}`;

  const grid = el("grid");
  if (!total) {
    // Offer the way out, don't just describe it — the console is a scroll away.
    const filtered = state.activeCats.size || state.vibes.size || state.freeOnly
      || state.favesOnly || state.district || state.city || q;
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1">
      <div><strong>NO SIGNALS ON THIS FREQUENCY.</strong></div>
      <div>${filtered ? "Nothing matches those filters on this date." : "Nothing listed for this date yet."}</div>
      <div class="empty-actions">
        ${filtered ? `<button class="btn" data-empty="clear">CLEAR FILTERS</button>` : ""}
        <button class="btn" data-empty="next">NEXT DAY ›</button>
        <button class="btn" data-empty="weekend">THIS WEEKEND</button>
      </div></div>`;
    grid.querySelectorAll("[data-empty]").forEach((b) => {
      b.onclick = () => {
        const act = b.dataset.empty;
        if (act === "clear") {
          state.activeCats.clear(); state.vibes.clear();
          state.freeOnly = false; state.favesOnly = false; state.district = null;
          state.city = null;
          state.search = ""; el("searchInput").value = "";
          render();
        } else if (act === "next") {
          el("nextDay").click();
        } else {
          state.date = nextWeekend(new Date()); render();
        }
      };
    });
    gridIndex = new Map();   // otherwise a stale ?e= could resolve against the last day
  } else {
    // Group the day into scannable stretches when sorted by time. Unparseable
    // times sort to 24h+ and land under LISTED (doors/times on the venue page).
    const daypart = (a) => {
      const t = parseTimeToMinutes(a.time);
      if (t >= 24 * 60) return "/ ALSO ON — SEE LISTINGS FOR TIMES";
      if (t < 12 * 60) return "/ MORNING";
      if (t < 17 * 60) return "/ AFTERNOON";
      if (t < 21 * 60) return "/ TONIGHT";
      return "/ LATE NIGHT";
    };
    const useBreaks = state.sort === "time" && list.length > 9;
    let html = "", lastPart = null;
    list.forEach((a, i) => {
      // pinned sponsored cards sit above the timeline — no header over them
      if (useBreaks && !(a.source === "sponsored" || a.sponsor)) {
        const part = daypart(a);
        if (part !== lastPart) { html += `<div class="time-break">${part}</div>`; lastPart = part; }
      }
      html += cardHtml(a, i);
      if (CONFIG.adsEnabled && i === 5) html += adCardHtml();
    });
    grid.innerHTML = html;
    // Look-ups go through a Map instead of list.find() per card: wiring 400
    // cards meant 400 linear scans, each rebuilding uid() strings — ~80k
    // needless string ops. The click handlers themselves are delegated once in
    // wireControls() rather than re-bound on every render.
    gridIndex = new Map(list.map((a) => [uid(a), a]));
  }

  RADAR.update(baseListForDate(state.date).concat(sponsored));
  renderOnNow();
  const sky = el("skyDate");
  if (sky) sky.textContent = fmtDate(state.date).toUpperCase();
  updateStatusCount();
  updateSeo(list.filter((a) => !a.house));
  // A shared ?e= link. Held until the event actually exists in the day's list —
  // live feeds land after the first render, so the target often isn't there yet.
  if (state.pendingEvent) {
    const target = gridIndex.get(state.pendingEvent);
    if (target) { state.pendingEvent = null; openDrawer(target); }
  }
  syncUrl();
}

/* ---- ON NOW rail: what's literally happening at this minute --------------- */
function renderOnNow() {
  const box = el("onnow");
  if (!box) return;
  let live = isToday(state.date)
    ? baseListForDate(state.date).concat(sponsoredForDate(state.date)).filter(isLiveNow)
    : [];
  const count = live.length;
  live.sort((a, b) => timeRange(a.time)[1] - timeRange(b.time)[1]); // ending soonest first
  live = live.slice(0, 8);                                          // cap the rail
  if (!live.length) { box.hidden = true; return; }
  box.hidden = false;
  el("onnowLabel").textContent = `${liveWord()} NOW — ${count}`;
  el("onnowRail").innerHTML = live.map((a) => `
    <div class="onnow-card" data-id="${esc(uid(a))}">
      <div class="oc-name">${esc(a.name)}</div>
      <div class="oc-meta">/ ${esc(String(a.time).toUpperCase())}</div>
      <div class="oc-meta">/ ${esc((displayArea(a.area) || "DFW").toUpperCase())}</div>
    </div>`).join("");
  const byId = new Map(live.map((a) => [uid(a), a]));
  el("onnowRail").querySelectorAll(".onnow-card").forEach((c) => {
    c.onclick = () => {
      const item = byId.get(c.dataset.id);
      if (item) openDrawer(item);
    };
  });
  updateOnNowArrows();
}

/* Shows only the arrow(s) that have somewhere left to go, so a day with few
   enough live events that the rail fits needs no controls at all. Called
   after every render and on scroll/resize, since a window resize can turn a
   scrollable rail into one that fits (or the reverse). */
function updateOnNowArrows() {
  const rail = el("onnowRail"), prev = el("onnowPrev"), next = el("onnowNext");
  if (!rail || !prev || !next) return;
  const overflows = rail.scrollWidth > rail.clientWidth + 1;
  const atStart = rail.scrollLeft <= 1;
  const atEnd = rail.scrollLeft >= rail.scrollWidth - rail.clientWidth - 1;
  prev.hidden = !overflows || atStart;
  next.hidden = !overflows || atEnd;
}

function updateQuickButtons() {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const sel = new Date(state.date); sel.setHours(0, 0, 0, 0);
  const diff = Math.round((sel - today) / 86400000);
  const isWeekend = sel.getDay() === 6 || sel.getDay() === 0;
  document.querySelectorAll(".quick button").forEach((b) => {
    const q = b.dataset.quick;
    // TONIGHT wins when the date is today; WEEKEND only lights on a non-today weekend
    b.classList.toggle("active",
      (q === "today" && diff === 0) ||
      (q === "tomorrow" && diff === 1) ||
      (q === "weekend" && isWeekend && diff !== 0));
  });
}

/* ---- SEO: dynamic meta + JSON-LD ----------------------------------------- */
function updateSeo(list) {
  const where = state.district
    ? state.district.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
    : "Dallas–Fort Worth";
  const when = isToday(state.date) ? "Tonight" : fmtDate(state.date);
  /* Only retitle once the view is actually filtered. In the default state this
     used to render "Things to Do in Dallas–Fort Worth Tonight | ...", which is
     character-for-character the <title> of the /tonight/ hub — and since Google
     renders JS, the rewrite reached the index and put the two pages in the same
     query. The static <title> in index.html owns the unfiltered homepage; the
     dynamic one still earns its keep on shared ?district=/?date= URLs. */
  const filtered = state.district || !isToday(state.date);
  if (filtered) document.title = `Things to Do in ${where} ${when} | Lets Do It Dallas`;
  const md = document.querySelector('meta[name="description"]');
  if (md) md.setAttribute("content",
    `Discover live events, music, pop-ups, and nightlife in ${where} for ${fmtDate(state.date)}. Real-time event radar on Lets Do It Dallas.`);

  let tag = el("jsonld");
  if (!tag) {
    tag = document.createElement("script");
    tag.type = "application/ld+json"; tag.id = "jsonld";
    document.head.appendChild(tag);
  }
  const iso = isoDate(state.date);
  const fallbackImg = `${location.origin}/og-image.png`;
  const events = list.slice(0, 30).filter((a) => a.url && a.url !== "#" && a.url !== "#advertise").map((a) => {
    const startMins = parseTimeToMinutes(a.time);
    const timed = startMins < 24 * 60;
    // Mirrors _split_area() in scripts/fetch_events.py — both layers emit the
    // same schema.org address for the same event, so keep them in step.
    const [venue, street, city] = splitArea(a.area);
    const pad = (n) => String(n).padStart(2, "0");
    const startDate = timed ? `${iso}T${pad(Math.floor(startMins / 60) % 24)}:${pad(startMins % 60)}:00-05:00` : iso;
    // default a 3-hour run, clamped to the same day
    const endMins = Math.min(startMins + 180, 23 * 60 + 59);
    const endDate = timed ? `${iso}T${pad(Math.floor(endMins / 60))}:${pad(endMins % 60)}:00-05:00` : iso;
    return {
      "@type": "Event",
      name: a.name,
      startDate,
      endDate,
      eventStatus: "https://schema.org/EventScheduled",
      eventAttendanceMode: "https://schema.org/OfflineEventAttendanceMode",
      location: { "@type": "Place", name: venue || a.area, address: { "@type": "PostalAddress", addressRegion: "TX", ...(street ? { streetAddress: street } : {}), ...(city ? { addressLocality: city } : {}) } },
      image: [a.image || fallbackImg],
      description: a.desc || undefined,
      url: a.url,
      organizer: (a.sponsor || venue) ? { "@type": "Organization", name: a.sponsor || venue } : undefined,
      performer: { "@type": "PerformingGroup", name: a.name },
      offers: {
        "@type": "Offer",
        url: a.url,
        availability: "https://schema.org/InStock",
        validFrom: `${iso}T00:00:00-05:00`,
        ...(a.cost != null ? { price: a.cost, priceCurrency: "USD" } : {}),
      },
    };
  });
  tag.textContent = JSON.stringify({ "@context": "https://schema.org", "@type": "ItemList",
    itemListElement: events.map((e, i) => ({ "@type": "ListItem", position: i + 1, item: e })) });
}

function syncUrl() {
  const p = new URLSearchParams();
  if (!isToday(state.date)) p.set("date", isoDate(state.date));
  if (state.district) p.set("district", state.district);
  if (state.city) p.set("city", state.city);
  if (state.activeCats.size) p.set("cat", [...state.activeCats].join(","));
  if (state.freeOnly) p.set("free", "1");
  // SHARE used to send location.href, which was the day + filters and not the
  // event — the recipient landed on 91 cards with no idea which one you meant.
  if (state.openEvent) p.set("e", state.openEvent);
  const qs = p.toString();
  history.replaceState(null, "", qs ? "?" + qs : location.pathname);
}

function readUrl() {
  const p = new URLSearchParams(location.search);
  const d = p.get("date");
  if (d && /^\d{4}-\d{2}-\d{2}$/.test(d)) {
    const [y, m, dd] = d.split("-").map(Number);
    state.date = new Date(y, m - 1, dd);
  }
  const view = p.get("view");
  if (view === "weekend") state.date = nextWeekend(new Date());
  if (view === "tonight") state.date = new Date();
  if (p.get("free") === "1") state.freeOnly = true;
  const cat = p.get("cat");
  if (cat) cat.split(",").forEach((c) => CATEGORIES[c] && state.activeCats.add(c));
  const dist = p.get("district");
  if (dist && DISTRICTS.some((x) => x.slug === dist)) state.district = dist;
  // Run the param back through cityOf() so ?city=fort%20worth, ?city=FORT
  // WORTH and ?city=Fort+Worth all canonicalize to the same "Fort Worth" the
  // rows carry. Anything unrecognized resolves to null and is ignored, so a
  // junk param shows the full list rather than an empty one.
  const city = p.get("city");
  if (city) state.city = cityOf({ area: city });
  // resolved in render(), once the day's events have actually been built
  state.pendingEvent = p.get("e") || null;
}

/* ---- status bar ---------------------------------------------------------- */
const WMO = { 0: "CLEAR", 1: "CLEAR", 2: "PARTLY CLOUDY", 3: "OVERCAST", 45: "FOG", 48: "FOG",
  51: "DRIZZLE", 53: "DRIZZLE", 55: "DRIZZLE", 61: "RAIN", 63: "RAIN", 65: "HEAVY RAIN",
  80: "SHOWERS", 81: "SHOWERS", 82: "STORMS", 95: "THUNDERSTORMS", 96: "THUNDERSTORMS", 99: "THUNDERSTORMS" };
let weatherTxt = "";

function tickClock() {
  const t = new Date().toLocaleTimeString("en-US", { timeZone: "America/Chicago", hour: "numeric", minute: "2-digit" });
  const box = el("statusClock");
  if (box) box.textContent = `DALLAS — ${t} CT`;
}
async function fetchWeather() {
  try {
    const res = await fetch("https://api.open-meteo.com/v1/forecast?latitude=32.7767&longitude=-96.797&current=temperature_2m,weather_code&temperature_unit=fahrenheit");
    if (!res.ok) return;
    const d = await res.json();
    const c = d.current || {};
    weatherTxt = `${Math.round(c.temperature_2m)}°F ${WMO[c.weather_code] || ""}`.trim();
    const box = el("statusWx");
    if (box) box.textContent = weatherTxt;
  } catch (_) { /* status bar degrades gracefully */ }
}
function updateStatusCount() {
  const box = el("statusLive");
  if (!box) return;
  if (!isToday(state.date)) { box.textContent = ""; return; }
  const n = baseListForDate(state.date).filter(isLiveNow).length;
  box.innerHTML = n ? `<i class="pulse"></i>${n} ${liveWord()} NOW` : "";
}

/* ---- favorites ----------------------------------------------------------- */
function toggleFav(item) {
  const id = uid(item);
  state.faves.has(id) ? state.faves.delete(id) : state.faves.add(id);
  localStorage.setItem("rjdd:faves", JSON.stringify([...state.faves]));
  render();
}

/* ---- drawer (event detail) -----------------------------------------------
   The drawer used to open with focus left on <body>: a keyboard user pressed
   DETAILS and their next Tab went to the top of the page *behind* the panel,
   and a screen reader announced nothing. It now takes focus, keeps it, and
   hands it back to whatever opened it. -------------------------------------- */
let drawerOpener = null;

function trapFocus(panel, e) {
  if (e.key !== "Tab") return;
  const f = [...panel.querySelectorAll('a[href],button:not([disabled]),input,select,textarea,[tabindex]:not([tabindex="-1"])')]
    .filter((n) => n.offsetParent !== null || n === document.activeElement);
  if (!f.length) return;
  const first = f[0], last = f[f.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

function openDrawer(a) {
  drawerOpener = document.activeElement;
  const c = CATEGORIES[a.cat] || { label: "Event" };
  const mapQ = encodeURIComponent(`${a.name} ${a.area}`);
  const outUrl = withAffiliate(a.url);
  const isHouseAd = a.url === "#advertise";
  const live = isLiveNow(a);
  el("modalBody").innerHTML = `
    <div class="dr-tag">/ ${esc(c.label.toUpperCase())} ${live ? `<span class="live-ring"><i></i>${liveWord()} NOW</span>` : ""}</div>
    ${a.image ? `<div class="dr-img"><img src="${esc(safeUrl(a.image))}" alt="" width="640" height="360" decoding="async" onerror="this.parentElement.remove()"></div>` : ""}
    <h2 id="drawerTitle">${esc(a.name)}</h2>
    <div class="dr-meta">/ ${fmtDate(state.date).toUpperCase()}</div>
    <div class="dr-meta">/ ${esc(String(a.time).toUpperCase())}</div>
    <div class="dr-meta">/ ${esc((a.area || "DFW").toUpperCase())}</div>
    <p class="dr-desc">${esc(a.desc || "No description provided.")}</p>
    <div class="dr-meta">${costBadge(a)} ${a.sponsor ? `<span class="badge">SPONSORED · ${esc(a.sponsor.toUpperCase())}</span>` : ""}</div>
    <div class="modal-actions">
      ${isHouseAd
        ? `<button class="btn primary" id="advertiseBtn">GET STARTED ↗</button>`
        : (a.url && a.url !== "#" ? `<a class="btn primary" href="${esc(safeUrl(outUrl))}" target="_blank" rel="noopener">TICKETS & INFO ↗</a>` : "")}
      ${isHouseAd ? "" : `<a class="btn" href="https://www.google.com/maps/search/?api=1&query=${mapQ}" target="_blank" rel="noopener">DIRECTIONS ↗</a>
      <button class="btn" id="icsBtn">ADD TO CALENDAR</button>
      <button class="btn" id="shareBtn">SHARE</button>
      <button class="btn ${state.faves.has(uid(a)) ? "primary" : ""}" id="modalFav">${state.faves.has(uid(a)) ? "♥ SAVED" : "♡ SAVE"}</button>`}
    </div>`;
  const modal = el("modal");
  modal.classList.add("open");
  document.body.classList.add("drawer-open");
  // deep-link the open event so SHARE and a browser reload both land on it
  state.openEvent = uid(a);
  syncUrl();
  const panel = modal.querySelector(".modal-panel");
  panel.onkeydown = (e) => trapFocus(panel, e);
  el("modalClose").focus();
  if (isHouseAd) {
    // Send them to the sales page rather than straight to a mailto — the page
    // does the selling, and a blank compose window converts badly.
    el("advertiseBtn").onclick = () => { location.href = "/advertise/"; };
  } else {
    el("icsBtn").onclick = () => downloadIcs(a);
    el("shareBtn").onclick = () => shareEvent(a);
    el("modalFav").onclick = () => { toggleFav(a); openDrawer(a); };
  }
}
function closeDrawer() {
  const modal = el("modal");
  if (!modal.classList.contains("open")) return;
  modal.classList.remove("open");
  document.body.classList.remove("drawer-open");
  state.openEvent = null;
  syncUrl();
  // back to the card they came from, not the top of the document
  if (drawerOpener && document.contains(drawerOpener)) drawerOpener.focus();
  drawerOpener = null;
}

/* ---- calendar (.ics) ------------------------------------------------------ */
function downloadIcs(a) {
  const start = new Date(state.date);
  const mins = parseTimeToMinutes(a.time);
  if (mins < 24 * 60) start.setHours(Math.floor(mins / 60), mins % 60, 0, 0);
  else start.setHours(10, 0, 0, 0);
  const end = new Date(start.getTime() + 2 * 3600 * 1000);
  const fmt = (d) => d.toISOString().replace(/[-:]/g, "").split(".")[0] + "Z";
  const ics = [
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Lets Do It Dallas//EN",
    "BEGIN:VEVENT", `UID:${uid(a)}-${fmt(start)}@letsdoitdallas`,
    `DTSTAMP:${fmt(new Date())}`, `DTSTART:${fmt(start)}`, `DTEND:${fmt(end)}`,
    `SUMMARY:${a.name}`, `LOCATION:${a.area}`,
    `DESCRIPTION:${(a.desc || "").replace(/\n/g, " ")} — via Lets Do It Dallas`,
    a.url && a.url !== "#" ? `URL:${a.url}` : "", "END:VEVENT", "END:VCALENDAR",
  ].filter(Boolean).join("\r\n");
  const blob = new Blob([ics], { type: "text/calendar" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${a.name.replace(/[^\w]+/g, "-").toLowerCase()}.ics`;
  link.click();
}

/* ---- share ---------------------------------------------------------------- */
async function shareEvent(a) {
  const text = `${a.name} — ${a.time}, ${a.area}. Found on Lets Do It Dallas.`;
  // location.href already carries ?e= while the drawer is open (see syncUrl),
  // so the recipient opens on this event rather than the whole day's list.
  const url = location.href;
  if (navigator.share) {
    try { await navigator.share({ title: a.name, text, url }); return; } catch (_) {}
  }
  try {
    await navigator.clipboard.writeText(`${text} ${url}`);
    toast("Copied to clipboard");
  } catch (_) { toast("Share not supported here"); }
}

/* ---- affiliate wrapping --------------------------------------------------- */
function withAffiliate(url) {
  if (!url || url === "#" || !CONFIG.affiliateTag) return url;
  try {
    const u = new URL(url);
    u.searchParams.set("aff", CONFIG.affiliateTag);
    return u.toString();
  } catch (_) { return url; }
}

/* ---- toast ---------------------------------------------------------------- */
let toastTimer;
function toast(msg) {
  let t = el("toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; t.className = "toast"; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

/* ---- newsletter + submit-event forms -------------------------------------- */
/* Same focus contract as the drawer — take focus, keep it, hand it back. */
let submitOpener = null;
function openSubmit() {
  submitOpener = document.activeElement;
  const m = el("submitModal");
  m.classList.add("open");
  document.body.classList.add("drawer-open");
  const panel = m.querySelector(".modal-panel");
  panel.onkeydown = (e) => trapFocus(panel, e);
  // the first field, not the close button — they came here to type
  (m.querySelector('input[name="name"]') || el("submitClose")).focus();
}
function closeSubmit() {
  const m = el("submitModal");
  if (!m.classList.contains("open")) return;
  m.classList.remove("open");
  if (!el("modal").classList.contains("open")) document.body.classList.remove("drawer-open");
  if (submitOpener && document.contains(submitOpener)) submitOpener.focus();
  submitOpener = null;
}

function wireForms() {
  const nl = el("newsletterForm");
  nl.onsubmit = async (e) => {
    e.preventDefault();
    const email = el("nlEmail").value.trim();
    if (!email) return;
    if (!CONFIG.newsletterEndpoint) {
      location.href = "mailto:" + CONFIG.contactEmail
        + "?subject=" + encodeURIComponent("Newsletter signup — " + CONFIG.siteName)
        + "&body=" + encodeURIComponent("Please add me to the weekly rundown: " + email);
      toast("Opening your email app to finish signing up…");
      el("nlEmail").value = ""; return;
    }
    try {
      await fetch(CONFIG.newsletterEndpoint, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      toast("You're on the list! 🎉"); el("nlEmail").value = "";
    } catch (_) { toast("Something went wrong — try again."); }
  };

  el("submitEventBtn").onclick = () => openSubmit();
  el("submitClose").onclick = () => closeSubmit();
  el("submitForm").onsubmit = async (e) => {
    e.preventDefault();
    const payload = Object.fromEntries(new FormData(e.target).entries());

    // Spam trap: only a bot fills a field positioned off-screen. Fake success
    // rather than showing an error — telling a bot it failed invites a retry
    // with the field cleared.
    if (payload.company_website) {
      closeSubmit(); e.target.reset();
      toast("Thanks! We'll review your event."); return;
    }
    delete payload.company_website;

    const btn = e.target.querySelector('button[type="submit"]');
    if (btn && btn.disabled) return;          // double-submit guard

    if (!CONFIG.submitEventEndpoint) {
      const body = Object.entries(payload).map(([k, v]) => `${k}: ${v}`).join("\n");
      location.href = "mailto:" + CONFIG.contactEmail
        + "?subject=" + encodeURIComponent("Event submission — " + (payload.name || "untitled"))
        + "&body=" + encodeURIComponent(body + "\n\nSubmitted via " + CONFIG.siteName);
      toast("Opening your email app to send your event…");
      closeSubmit(); e.target.reset(); return;
    }

    if (btn) { btn.disabled = true; btn.textContent = "SENDING…"; }
    try {
      // Formspree needs an explicit Accept: application/json or it replies with
      // an HTML redirect page instead of a JSON result.
      const res = await fetch(CONFIG.submitEventEndpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          ...payload,
          _subject: "Event submission — " + (payload.name || "untitled"),
        }),
      });
      if (!res.ok) throw new Error(res.status);
      toast("Thanks! We'll review your event.");
      closeSubmit(); e.target.reset();
    } catch (_) {
      // Don't lose what they typed — the form stays filled so they can retry
      // or fall back to email.
      toast("Couldn't send — try again, or email " + CONFIG.contactEmail);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "SUBMIT EVENT"; }
    }
  };
}

/* ---- press wire ----------------------------------------------------------- */
async function loadWire() {
  try {
    const res = await fetch("press.json", { cache: "no-cache" });
    if (!res.ok) throw 0;
    const items = await res.json();
    if (!items.length) throw 0;
    el("wireList").innerHTML = items.slice(0, 10).map((p, i) => `
      <a class="wire-row" href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener">
        <span class="idx">(${String(i + 1).padStart(2, "0")})</span>
        <span class="wl-title">${esc(p.title)}</span>
        <span class="wl-src">/ ${esc((p.source || "").toUpperCase())}</span>
      </a>`).join("");
  } catch (_) {
    const sec = el("wireSection");
    if (sec) sec.style.display = "none";
  }
}

/* ---- itineraries ----------------------------------------------------------- */
function renderItineraries() {
  const box = el("itinGrid");
  if (!box || typeof ITINERARIES === "undefined") return;
  box.innerHTML = ITINERARIES.map((it) => `
    <div class="itin">
      <div class="itin-head"><span class="tag">/ ${it.district.toUpperCase()}</span><h3>${it.title}</h3></div>
      ${it.steps.map((s, i) => `
        <div class="itin-step">
          <span class="idx">(${String(i + 1).padStart(2, "0")})</span>
          <span class="itin-time">${s.time}</span>
          <div><div class="itin-title">${s.title}</div><div class="itin-note">${s.note}</div></div>
        </div>`).join("")}
    </div>`).join("");
}

/* ---- bridge scroll-draw ---------------------------------------------------- */
function wireBridge() {
  const sec = el("bridgeDivider");
  if (!sec || !("IntersectionObserver" in window)) { sec && sec.classList.add("drawn"); return; }
  new IntersectionObserver((entries, obs) => {
    entries.forEach((en) => { if (en.isIntersecting) { sec.classList.add("drawn"); obs.disconnect(); } });
  }, { threshold: 0.35 }).observe(sec);
}

/* ---- live loading ---------------------------------------------------------- */
let liveToken = 0;
async function refreshLive() {
  const my = ++liveToken;
  state.loadingLive = true;
  state.live = [];
  state.liveStamp++;
  render();
  const events = await loadLiveEvents(state.date);
  if (my !== liveToken) return;
  state.live = events;
  state.liveStamp++;
  state.loadingLive = false;
  render();
}

/* ---- navigation ------------------------------------------------------------ */
function goToDate(d) { state.date = d; refreshLive(); }

/* "LIVE IN DALLAS — EXPLORE TONIGHT" CTA: clear filters, jump to today,
   and scroll the full event list into view. */
function exploreTonight() {
  state.activeCats.clear();
  state.vibes.clear();
  state.district = null;
  state.freeOnly = false;
  state.favesOnly = false;
  state.search = "";
  const s = el("searchInput"); if (s) s.value = "";
  goToDate(new Date());
  const main = document.querySelector("main");
  if (main) main.scrollIntoView({ behavior: "smooth", block: "start" });
}
function nextWeekend(from) {
  const d = new Date(from);
  if (d.getDay() === 6 || d.getDay() === 0) return d;
  d.setDate(d.getDate() + ((6 - d.getDay() + 7) % 7));
  return d;
}

function wireControls() {
  el("prevDay").onclick = () => { const d = new Date(state.date); d.setDate(d.getDate() - 1); goToDate(d); };
  el("nextDay").onclick = () => { const d = new Date(state.date); d.setDate(d.getDate() + 1); goToDate(d); };
  el("datePicker").onchange = (e) => {
    if (!e.target.value) return;
    const [y, m, d] = e.target.value.split("-").map(Number);
    goToDate(new Date(y, m - 1, d));
  };
  /* Every keystroke used to rebuild the whole grid, re-serialize the JSON-LD
     block and call history.replaceState — ~5 ms a character on a desktop and
     several times that on a phone, so fast typing visibly stuttered. (Safari
     also rate-limits replaceState and throws once a burst exceeds its cap.)
     Coalesce to one render per input pause instead. */
  let searchTimer;
  el("searchInput").oninput = (e) => {
    state.search = e.target.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(render, 120);
  };
  el("sort").onchange = (e) => { state.sort = e.target.value; render(); };
  el("citySel").onchange = (e) => { state.city = e.target.value || null; render(); };
  el("freeToggle").onclick = () => { state.freeOnly = !state.freeOnly; render(); };
  el("faveToggle").onclick = () => { state.favesOnly = !state.favesOnly; render(); };
  document.querySelectorAll(".quick button").forEach((b) => {
    b.onclick = () => {
      const q = b.dataset.quick; const t = new Date();
      if (q === "tomorrow") t.setDate(t.getDate() + 1);
      else if (q === "weekend") return goToDate(nextWeekend(t));
      goToDate(t);
    };
  });
  /* ON NOW rail arrows: scroll by ~90% of a viewport-width "page" rather than
     a fixed card count, so the click feels proportionate at any width. The
     rail's own scroll (drag, trackpad, shift+wheel) also needs to keep the
     arrows in sync, hence the scroll listener alongside the click handlers. */
  const onnowRail = el("onnowRail");
  el("onnowPrev").onclick = () => onnowRail.scrollBy({ left: -onnowRail.clientWidth * 0.9 });
  el("onnowNext").onclick = () => onnowRail.scrollBy({ left: onnowRail.clientWidth * 0.9 });
  onnowRail.addEventListener("scroll", updateOnNowArrows, { passive: true });
  let onnowResizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(onnowResizeTimer);
    onnowResizeTimer = setTimeout(updateOnNowArrows, 120);
  });

  /* sticky date bar: slides in once the console scrolls out of view */
  el("skyPrev").onclick = () => el("prevDay").click();
  el("skyNext").onclick = () => el("nextDay").click();
  el("skyTonight").onclick = () => goToDate(new Date());
  el("skyTop").onclick = () => window.scrollTo({ top: 0, behavior: "smooth" });
  const consoleEl = document.querySelector(".console");
  window.addEventListener("scroll", () => {
    const past = consoleEl && consoleEl.getBoundingClientRect().bottom < 0;
    const bar = el("skybar");
    bar.classList.toggle("show", !!past);
    bar.setAttribute("aria-hidden", past ? "false" : "true");
  }, { passive: true });

  el("vibesToggle").onclick = (e) => {
    e.stopPropagation();
    const row = el("vibesRow");
    const collapsed = row.classList.toggle("collapsed");
    el("vibesToggle").textContent = collapsed ? "+ SHOW" : "− HIDE";
    el("vibesToggle").setAttribute("aria-expanded", String(!collapsed));
  };
  el("radarJump").onclick = () => {
    const r = document.querySelector(".radar-section") || el("radarMap");
    if (r) r.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  document.querySelectorAll(".marquee-track button").forEach((b) => {
    b.onclick = () => {
      state.activeCats = new Set([b.dataset.cat]);
      render();
      const main = document.querySelector("main");
      if (main) main.scrollIntoView({ behavior: "smooth", block: "start" });
    };
  });
  // .sb-right used to be wired here too, with copy identical to the hero badge.
  // It's a real link to /this-weekend/ now, so it needs nothing from JS.
  document.querySelectorAll(".hero-badge").forEach((b) => {
    b.setAttribute("role", "button");
    b.setAttribute("tabindex", "0");
    b.onclick = exploreTonight;
    b.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); exploreTonight(); }
    };
  });
  el("modalClose").onclick = closeDrawer;
  el("modal").onclick = (e) => { if (e.target.id === "modal") closeDrawer(); };
  el("submitModal").onclick = (e) => { if (e.target.id === "submitModal") closeSubmit(); };
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeDrawer(); closeSubmit(); }
    if (e.key === "ArrowLeft" && !isTyping()) el("prevDay").click();
    if (e.key === "ArrowRight" && !isTyping()) el("nextDay").click();
  });
}
function isTyping() {
  const a = document.activeElement;
  return a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA");
}

/* ---- boot ------------------------------------------------------------------ */
function boot() {
  el("year").textContent = new Date().getFullYear();
  // Full hero on a first visit, slim band on every one after — see .returning
  // in styles.css. Wrapped because Safari private mode throws on localStorage.
  try {
    if (localStorage.getItem("rjdd:seen")) document.body.classList.add("returning");
    else localStorage.setItem("rjdd:seen", "1");
  } catch (_) {}
  // The footer ships with a working mailto so the link survives a JS failure;
  // point it at CONFIG so data.js stays the single source for the address.
  // Footer link now points at /advertise/; the mailto in the markup stays as the
  // no-JS fallback, so this override is no longer needed.
  readUrl();
  RADAR.init({
    getDayList: () => baseListForDate(state.date).concat(sponsoredForDate(state.date)),
    onDistrict: (slug) => { state.district = slug; render(); },
    activeDistrict: () => state.district,
  });
  wireControls();
  wireGridDelegation();
  wireForms();
  wireBridge();
  renderItineraries();
  loadWire();
  tickClock();
  setInterval(tickClock, 30 * 1000);
  // The weather call is decorative (a line in the status bar) and goes to a
  // third-party host, so keep it off the critical path — it competed with the
  // event feed at boot for ~600ms.
  const startWeather = () => { fetchWeather(); setInterval(fetchWeather, 15 * 60 * 1000); };
  if ("requestIdleCallback" in window) requestIdleCallback(startWeather, { timeout: 3000 });
  else setTimeout(startWeather, 1200);
  setInterval(updateStatusCount, 60 * 1000);
  render();
  refreshLive();
}
document.addEventListener("DOMContentLoaded", boot);
