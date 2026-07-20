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
  search: "",
  sort: "time",
  freeOnly: false,
  favesOnly: false,
  live: [],
  loadingLive: false,
  faves: new Set(JSON.parse(localStorage.getItem("rjdd:faves") || "[]")),
};

const el = (id) => document.getElementById(id);
const uid = (a) => `${a.name}|${a.area}`.toLowerCase().replace(/\s+/g, "-");

/* ---- date/time helpers --------------------------------------------------- */
function fmtDate(d) {
  return d.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
}
function fmtMono(d) {
  return d.toLocaleDateString("en-US", { month: "short", day: "2-digit" }).toUpperCase();
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

function baseListForDate(date) {
  const curated = ACTIVITIES.filter((a) => happensOn(a, date)).map((a) => ({ ...a, source: "curated" }));
  const seen = new Set(curated.map((c) => c.name.toLowerCase()));
  const liveClean = state.live.filter((l) => !seen.has((l.name || "").toLowerCase()));
  return [...curated, ...liveClean];
}

function applyFilters(list) {
  const q = state.search.trim().toLowerCase();
  let out = list.slice();
  if (state.activeCats.size) out = out.filter((a) => state.activeCats.has(a.cat));
  if (state.district) out = out.filter((a) => RADAR.districtOf(a) === state.district);
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
    ? `<div class="card-thumb"><img src="${a.image}" alt="" width="64" height="64" loading="lazy" decoding="async" onerror="this.parentElement.remove()"></div>`
    : "";
  return `
    <article class="card ${sponsored ? "sponsored" : ""}" data-id="${uid(a)}"
             style="--d:${Math.min((i || 0) * 40, 400)}ms">
      <div class="card-toprow">
        <span class="idx">(${String((i || 0) + 1).padStart(2, "0")})</span>
        <span class="tag">/ ${c.label.toUpperCase()}</span>
        ${live ? `<span class="live-ring" title="Happening now"><i></i>${liveWord()}</span>` : ""}
        ${sponsored ? `<span class="spon">★ SPONSORED</span>` : ""}
      </div>
      <div class="card-mid">
        <div class="card-txt">
          <h3>${a.name}</h3>
          <div class="meta">/ ${fmtMono(state.date)} · ${String(a.time).toUpperCase()}</div>
          <div class="meta">/ ${(dLabel || a.area || "DFW").toUpperCase()}</div>
        </div>
        ${thumb}
      </div>
      <p class="desc">${a.desc || ""}</p>
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
function render() {
  el("dateDisplay").textContent = fmtDate(state.date);
  el("datePicker").value = isoDate(state.date);
  buildFilters();
  buildVibes();
  updateQuickButtons();
  el("freeToggle").classList.toggle("active", state.freeOnly);
  el("faveToggle").classList.toggle("active", state.favesOnly);
  el("faveToggle").textContent = `♥ SAVED (${state.faves.size})`;

  const sponsored = sponsoredForDate(state.date);
  const base = applyFilters(baseListForDate(state.date));
  const sponsoredIds = new Set(sponsored.map(uid));
  const q = state.search.trim().toLowerCase();
  const list = [...sponsored.filter((s) => {
      if (state.activeCats.size && !state.activeCats.has(s.cat)) return false;
      if (state.freeOnly && s.cost !== 0) return false;
      if (state.district && RADAR.districtOf(s) !== state.district) return false;
      if (state.favesOnly && !state.faves.has(uid(s))) return false;
      // sponsored pins must match an active search too — otherwise a fruitless
      // query returns the house ad as its only "result"
      if (q && !`${s.name} ${s.desc} ${s.area}`.toLowerCase().includes(q)) return false;
      return true;
    }), ...base.filter((b) => !sponsoredIds.has(uid(b)))];

  const total = list.length;
  el("count").innerHTML = state.loadingLive
    ? `${total} LISTED · <span class="live-loading">SYNCING LIVE FEEDS…</span>`
    : `${total} ${total === 1 ? "EVENT" : "EVENTS"} — ${fmtDate(state.date).toUpperCase()}${state.district ? " / " + state.district.replace(/-/g, " ").toUpperCase() : ""}`;

  const grid = el("grid");
  if (!total) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1">
      <div><strong>NO SIGNALS ON THIS FREQUENCY.</strong></div>
      <div>Try another date, clear filters, or widen your search.</div></div>`;
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
    grid.querySelectorAll(".card").forEach((cardEl) => {
      const id = cardEl.dataset.id;
      const item = list.find((x) => uid(x) === id);
      if (!item) return;
      const favBtn = cardEl.querySelector('[data-act="fav"]');
      const openBtn = cardEl.querySelector('[data-act="open"]');
      if (favBtn) favBtn.onclick = (e) => { e.stopPropagation(); toggleFav(item); };
      if (openBtn) openBtn.onclick = (e) => { e.stopPropagation(); openDrawer(item); };
      cardEl.onclick = () => openDrawer(item);
    });
  }

  RADAR.update(baseListForDate(state.date).concat(sponsored));
  renderOnNow();
  const sky = el("skyDate");
  if (sky) sky.textContent = fmtDate(state.date).toUpperCase();
  updateStatusCount();
  updateSeo(list);
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
    <div class="onnow-card" data-id="${uid(a)}">
      <div class="oc-name">${a.name}</div>
      <div class="oc-meta">/ ${String(a.time).toUpperCase()}</div>
      <div class="oc-meta">/ ${(a.area || "DFW").toUpperCase()}</div>
    </div>`).join("");
  el("onnowRail").querySelectorAll(".onnow-card").forEach((c) => {
    c.onclick = () => {
      const item = live.find((x) => uid(x) === c.dataset.id);
      if (item) openDrawer(item);
    };
  });
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
  document.title = `Things to Do in ${where} ${when} | Lets Do It Dallas`;
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
      location: { "@type": "Place", name: a.area, address: { "@type": "PostalAddress", addressRegion: "TX", addressLocality: a.area } },
      image: [a.image || fallbackImg],
      description: a.desc || undefined,
      url: a.url,
      organizer: { "@type": "Organization", name: a.sponsor || a.area, url: a.url },
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
  if (state.activeCats.size) p.set("cat", [...state.activeCats].join(","));
  if (state.freeOnly) p.set("free", "1");
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

/* ---- drawer (event detail) ----------------------------------------------- */
function openDrawer(a) {
  const c = CATEGORIES[a.cat] || { label: "Event" };
  const mapQ = encodeURIComponent(`${a.name} ${a.area}`);
  const outUrl = withAffiliate(a.url);
  const isHouseAd = a.url === "#advertise";
  const live = isLiveNow(a);
  el("modalBody").innerHTML = `
    <div class="dr-tag">/ ${c.label.toUpperCase()} ${live ? `<span class="live-ring"><i></i>${liveWord()} NOW</span>` : ""}</div>
    ${a.image ? `<div class="dr-img"><img src="${a.image}" alt="" width="640" height="360" decoding="async" onerror="this.parentElement.remove()"></div>` : ""}
    <h2>${a.name}</h2>
    <div class="dr-meta">/ ${fmtDate(state.date).toUpperCase()}</div>
    <div class="dr-meta">/ ${String(a.time).toUpperCase()}</div>
    <div class="dr-meta">/ ${(a.area || "DFW").toUpperCase()}</div>
    <p class="dr-desc">${a.desc || "No description provided."}</p>
    <div class="dr-meta">${costBadge(a)} ${a.sponsor ? `<span class="badge">SPONSORED · ${a.sponsor.toUpperCase()}</span>` : ""}</div>
    <div class="modal-actions">
      ${isHouseAd
        ? `<button class="btn primary" id="advertiseBtn">GET STARTED ↗</button>`
        : (a.url && a.url !== "#" ? `<a class="btn primary" href="${outUrl}" target="_blank" rel="noopener">TICKETS & INFO ↗</a>` : "")}
      ${isHouseAd ? "" : `<a class="btn" href="https://www.google.com/maps/search/?api=1&query=${mapQ}" target="_blank" rel="noopener">DIRECTIONS ↗</a>
      <button class="btn" id="icsBtn">ADD TO CALENDAR</button>
      <button class="btn" id="shareBtn">SHARE</button>
      <button class="btn ${state.faves.has(uid(a)) ? "primary" : ""}" id="modalFav">${state.faves.has(uid(a)) ? "♥ SAVED" : "♡ SAVE"}</button>`}
    </div>`;
  el("modal").classList.add("open");
  document.body.classList.add("drawer-open");
  if (isHouseAd) {
    el("advertiseBtn").onclick = () => {
      location.href = "mailto:" + CONFIG.contactEmail
        + "?subject=" + encodeURIComponent("Sponsored listing inquiry — " + CONFIG.siteName)
        + "&body=" + encodeURIComponent(
          "Hi! I'd like to feature my event/venue on " + CONFIG.siteName + ".\n\n"
          + "Business/event name:\nDates I want featured:\nLink:\n");
      closeDrawer();
    };
  } else {
    el("icsBtn").onclick = () => downloadIcs(a);
    el("shareBtn").onclick = () => shareEvent(a);
    el("modalFav").onclick = () => { toggleFav(a); openDrawer(a); };
  }
}
function closeDrawer() {
  el("modal").classList.remove("open");
  document.body.classList.remove("drawer-open");
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

  el("submitEventBtn").onclick = () => el("submitModal").classList.add("open");
  el("submitClose").onclick = () => el("submitModal").classList.remove("open");
  el("submitForm").onsubmit = async (e) => {
    e.preventDefault();
    const payload = Object.fromEntries(new FormData(e.target).entries());
    if (!CONFIG.submitEventEndpoint) {
      const body = Object.entries(payload).map(([k, v]) => `${k}: ${v}`).join("\n");
      location.href = "mailto:" + CONFIG.contactEmail
        + "?subject=" + encodeURIComponent("Event submission — " + (payload.name || "untitled"))
        + "&body=" + encodeURIComponent(body + "\n\nSubmitted via " + CONFIG.siteName);
      toast("Opening your email app to send your event…");
      el("submitModal").classList.remove("open"); e.target.reset(); return;
    }
    try {
      await fetch(CONFIG.submitEventEndpoint, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      toast("Thanks! We'll review your event."); el("submitModal").classList.remove("open"); e.target.reset();
    } catch (_) { toast("Something went wrong — try again."); }
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
      <a class="wire-row" href="${p.url}" target="_blank" rel="noopener">
        <span class="idx">(${String(i + 1).padStart(2, "0")})</span>
        <span class="wl-title">${p.title}</span>
        <span class="wl-src">/ ${(p.source || "").toUpperCase()}</span>
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
  render();
  const events = await loadLiveEvents(state.date);
  if (my !== liveToken) return;
  state.live = events;
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
  el("searchInput").oninput = (e) => { state.search = e.target.value; render(); };
  el("sort").onchange = (e) => { state.sort = e.target.value; render(); };
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
  document.querySelectorAll(".hero-badge, .sb-right").forEach((b) => {
    b.setAttribute("role", "button");
    b.setAttribute("tabindex", "0");
    b.onclick = exploreTonight;
    b.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); exploreTonight(); }
    };
  });
  el("modalClose").onclick = closeDrawer;
  el("modal").onclick = (e) => { if (e.target.id === "modal") closeDrawer(); };
  el("submitModal").onclick = (e) => { if (e.target.id === "submitModal") el("submitModal").classList.remove("open"); };
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeDrawer(); el("submitModal").classList.remove("open"); }
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
  readUrl();
  RADAR.init({
    getDayList: () => baseListForDate(state.date).concat(sponsoredForDate(state.date)),
    onDistrict: (slug) => { state.district = slug; render(); },
    activeDistrict: () => state.district,
  });
  wireControls();
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
