/* =========================================================================
 *  RJ Does Dallas — application logic
 *  ========================================================================= */

/* ---- recurrence engine (for curated + sponsored) ----------------------- */
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

/* ---- state ------------------------------------------------------------- */
const state = {
  date: new Date(),
  activeCats: new Set(),
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

/* ---- date helpers ------------------------------------------------------ */
function fmtDate(d) {
  return d.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
}
function fmtShort(d) {
  return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
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

/* ---- data assembly ----------------------------------------------------- */
function sponsoredForDate(date) {
  const iso = isoDate(date);
  return SPONSORED
    .filter((s) => !s.sponsorUntil || s.sponsorUntil >= iso)
    .filter((s) => happensOn(s, date))
    .map((s) => ({ ...s, source: "sponsored", sponsor: s.sponsor || "Sponsored" }));
}

function baseListForDate(date) {
  const curated = ACTIVITIES.filter((a) => happensOn(a, date)).map((a) => ({ ...a, source: "curated" }));
  const live = state.live.slice();
  // de-dupe live against curated by name similarity
  const seen = new Set(curated.map((c) => c.name.toLowerCase()));
  const liveClean = live.filter((l) => !seen.has((l.name || "").toLowerCase()));
  return [...curated, ...liveClean];
}

function applyFilters(list) {
  const q = state.search.trim().toLowerCase();
  let out = list.slice();
  if (state.activeCats.size) out = out.filter((a) => state.activeCats.has(a.cat));
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

/* ---- rendering --------------------------------------------------------- */
function buildFilters() {
  const box = el("filters");
  box.innerHTML = "";
  const mk = (label, active, onclick, dot) => {
    const c = document.createElement("button");
    c.className = "chip" + (active ? " active" : "");
    c.innerHTML = (dot ? `<span class="dot" style="background:${dot}"></span>` : "") + label;
    c.onclick = onclick;
    return c;
  };
  box.appendChild(mk("All", state.activeCats.size === 0, () => { state.activeCats.clear(); render(); }));
  for (const [key, c] of Object.entries(CATEGORIES)) {
    box.appendChild(mk(`${c.emoji} ${c.label}`, state.activeCats.has(key), () => {
      state.activeCats.has(key) ? state.activeCats.delete(key) : state.activeCats.add(key);
      render();
    }, c.color));
  }
}

function costBadge(a) {
  if (a.cost === 0) return `<span class="badge free">Free</span>`;
  if (a.cost == null) return `<span class="badge">Ticketed</span>`;
  return `<span class="badge">$${a.cost}${a.cost >= 25 ? "+" : ""}</span>`;
}

function cardHtml(a, i) {
  const c = CATEGORIES[a.cat] || { label: "Event", color: "#4f8cff" };
  const fav = state.faves.has(uid(a));
  const sponsored = a.source === "sponsored" || a.sponsor;
  const liveTag = (a.source === "ticketmaster" || a.source === "seatgeek" || a.source === "predicthq")
    ? `<span class="src">● live</span>` : "";
  return `
    <article class="card ${sponsored ? "sponsored" : ""}" data-id="${uid(a)}"
             style="--d:${Math.min((i || 0) * 45, 450)}ms">
      ${sponsored ? `<div class="sponsor-ribbon">★ Sponsored${a.sponsor && a.sponsor !== "Sponsored" ? " · " + a.sponsor : ""}</div>` : ""}
      <div class="top">
        <h3>${a.name}</h3>
        <span class="cat-tag" style="background:${c.color}">${c.label}</span>
      </div>
      <div class="time">🕒 ${a.time} ${liveTag}</div>
      <div class="where">📍 ${a.area}</div>
      <div class="desc">${a.desc || ""}</div>
      <div class="foot">
        ${costBadge(a)}
        <div class="foot-actions">
          <button class="icon-btn fav ${fav ? "on" : ""}" title="Save" data-act="fav">${fav ? "♥" : "♡"}</button>
          <button class="icon-btn" title="Details" data-act="open">Details</button>
        </div>
      </div>
    </article>`;
}

function adCardHtml() {
  if (!CONFIG.adsEnabled) return "";
  return `<article class="card ad-card"><div class="ad-label">Advertisement</div>
    <div class="ad-slot">Your 300×250 ad here</div></article>`;
}

function render() {
  el("dateDisplay").textContent = fmtDate(state.date);
  el("datePicker").value = isoDate(state.date);
  buildFilters();
  updateQuickButtons();
  el("freeToggle").classList.toggle("active", state.freeOnly);
  el("faveToggle").classList.toggle("active", state.favesOnly);
  el("faveToggle").textContent = `♥ Saved (${state.faves.size})`;

  const sponsored = sponsoredForDate(state.date);
  const base = applyFilters(baseListForDate(state.date));
  // sponsored always pinned on top, not duplicated
  const sponsoredIds = new Set(sponsored.map(uid));
  const list = [...sponsored.filter((s) => {
      if (state.activeCats.size && !state.activeCats.has(s.cat)) return false;
      if (state.freeOnly && s.cost !== 0) return false;
      return true;
    }), ...base.filter((b) => !sponsoredIds.has(uid(b)))];

  const total = list.length;
  el("count").innerHTML = state.loadingLive
    ? `${total} ${total === 1 ? "activity" : "activities"} · <span class="live-loading">checking live events…</span>`
    : `${total} ${total === 1 ? "activity" : "activities"} on ${fmtDate(state.date)}`;

  const grid = el("grid");
  if (!total) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1">
      <div class="big">🗺️</div><div><strong>Nothing matches yet.</strong></div>
      <div>Try another date, clear filters, or widen your search.</div></div>`;
    return;
  }
  let html = "";
  list.forEach((a, i) => {
    html += cardHtml(a, i);
    if (CONFIG.adsEnabled && i === 5) html += adCardHtml(); // one native ad slot after row 2
  });
  grid.innerHTML = html;

  grid.querySelectorAll(".card").forEach((cardEl) => {
    const id = cardEl.dataset.id;
    const item = list.find((x) => uid(x) === id);
    if (!item) return;
    const favBtn = cardEl.querySelector('[data-act="fav"]');
    const openBtn = cardEl.querySelector('[data-act="open"]');
    if (favBtn) favBtn.onclick = (e) => { e.stopPropagation(); toggleFav(item); };
    if (openBtn) openBtn.onclick = (e) => { e.stopPropagation(); openModal(item); };
    cardEl.onclick = () => openModal(item);
  });
}

function updateQuickButtons() {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const sel = new Date(state.date); sel.setHours(0, 0, 0, 0);
  const diff = Math.round((sel - today) / 86400000);
  const isWeekend = sel.getDay() === 6 || sel.getDay() === 0;
  document.querySelectorAll(".quick button").forEach((b) => {
    const q = b.dataset.quick;
    b.classList.toggle("active",
      (q === "today" && diff === 0) || (q === "tomorrow" && diff === 1) || (q === "weekend" && isWeekend));
  });
}

/* ---- favorites --------------------------------------------------------- */
function toggleFav(item) {
  const id = uid(item);
  state.faves.has(id) ? state.faves.delete(id) : state.faves.add(id);
  localStorage.setItem("rjdd:faves", JSON.stringify([...state.faves]));
  render();
}

/* ---- modal ------------------------------------------------------------- */
function openModal(a) {
  const c = CATEGORIES[a.cat] || { label: "Event", color: "#4f8cff" };
  const mapQ = encodeURIComponent(`${a.name} ${a.area}`);
  const outUrl = withAffiliate(a.url);
  const isHouseAd = a.url === "#advertise";
  el("modalBody").innerHTML = `
    <span class="cat-tag" style="background:${c.color}">${c.label}</span>
    <h2>${a.name}</h2>
    <p class="modal-meta">🗓️ ${fmtDate(state.date)} &nbsp; 🕒 ${a.time}</p>
    <p class="modal-meta">📍 ${a.area}</p>
    <p class="modal-desc">${a.desc || "No description provided."}</p>
    <p class="modal-meta">${costBadge(a)} ${a.sponsor ? `<span class="badge">Sponsored</span>` : ""}</p>
    <div class="modal-actions">
      ${isHouseAd
        ? `<button class="btn primary" id="advertiseBtn">Get started ↗</button>`
        : (a.url && a.url !== "#" ? `<a class="btn primary" href="${outUrl}" target="_blank" rel="noopener">Tickets & info ↗</a>` : "")}
      ${isHouseAd ? "" : `<a class="btn" href="https://www.google.com/maps/search/?api=1&query=${mapQ}" target="_blank" rel="noopener">Map ↗</a>
      <button class="btn" id="icsBtn">Add to calendar</button>
      <button class="btn" id="shareBtn">Share</button>
      <button class="btn ${state.faves.has(uid(a)) ? "primary" : ""}" id="modalFav">${state.faves.has(uid(a)) ? "♥ Saved" : "♡ Save"}</button>`}
    </div>`;
  el("modal").classList.add("open");
  if (isHouseAd) {
    el("advertiseBtn").onclick = () => {
      location.href = "mailto:" + CONFIG.contactEmail
        + "?subject=" + encodeURIComponent("Sponsored listing inquiry — " + CONFIG.siteName)
        + "&body=" + encodeURIComponent(
          "Hi! I'd like to feature my event/venue on " + CONFIG.siteName + ".\n\n"
          + "Business/event name:\nDates I want featured:\nLink:\n");
      closeModal();
    };
  } else {
    el("icsBtn").onclick = () => downloadIcs(a);
    el("shareBtn").onclick = () => shareEvent(a);
    el("modalFav").onclick = () => { toggleFav(a); openModal(a); };
  }
}
function closeModal() { el("modal").classList.remove("open"); }

/* ---- calendar (.ics) --------------------------------------------------- */
function downloadIcs(a) {
  const start = new Date(state.date);
  const mins = parseTimeToMinutes(a.time);
  if (mins < 24 * 60) start.setHours(Math.floor(mins / 60), mins % 60, 0, 0);
  else start.setHours(10, 0, 0, 0);
  const end = new Date(start.getTime() + 2 * 3600 * 1000);
  const fmt = (d) => d.toISOString().replace(/[-:]/g, "").split(".")[0] + "Z";
  const ics = [
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//RJ Does Dallas//EN",
    "BEGIN:VEVENT", `UID:${uid(a)}-${fmt(start)}@rjdoesdallas`,
    `DTSTAMP:${fmt(new Date())}`, `DTSTART:${fmt(start)}`, `DTEND:${fmt(end)}`,
    `SUMMARY:${a.name}`, `LOCATION:${a.area}`,
    `DESCRIPTION:${(a.desc || "").replace(/\n/g, " ")} — via RJ Does Dallas`,
    a.url && a.url !== "#" ? `URL:${a.url}` : "", "END:VEVENT", "END:VCALENDAR",
  ].filter(Boolean).join("\r\n");
  const blob = new Blob([ics], { type: "text/calendar" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${a.name.replace(/[^\w]+/g, "-").toLowerCase()}.ics`;
  link.click();
}

/* ---- share ------------------------------------------------------------- */
async function shareEvent(a) {
  const text = `${a.name} — ${a.time}, ${a.area}. Found on RJ Does Dallas.`;
  const url = location.href;
  if (navigator.share) {
    try { await navigator.share({ title: a.name, text, url }); return; } catch (_) {}
  }
  try {
    await navigator.clipboard.writeText(`${text} ${url}`);
    toast("Copied to clipboard");
  } catch (_) { toast("Share not supported here"); }
}

/* ---- affiliate wrapping ------------------------------------------------ */
function withAffiliate(url) {
  if (!url || url === "#" || !CONFIG.affiliateTag) return url;
  try {
    const u = new URL(url);
    u.searchParams.set("aff", CONFIG.affiliateTag);
    return u.toString();
  } catch (_) { return url; }
}

/* ---- toast ------------------------------------------------------------- */
let toastTimer;
function toast(msg) {
  let t = el("toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; t.className = "toast"; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

/* ---- newsletter + submit-event forms ----------------------------------- */
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
      const body = Object.entries(payload)
        .map(([k, v]) => `${k}: ${v}`).join("\n");
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

/* ---- live loading ------------------------------------------------------ */
let liveToken = 0;
async function refreshLive() {
  const my = ++liveToken;
  state.loadingLive = true;
  state.live = [];
  render();
  const events = await loadLiveEvents(state.date);
  if (my !== liveToken) return;          // a newer date was picked; ignore stale
  state.live = events;
  state.loadingLive = false;
  render();
}

/* ---- navigation -------------------------------------------------------- */
function goToDate(d) { state.date = d; refreshLive(); }
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
  el("modalClose").onclick = closeModal;
  el("modal").onclick = (e) => { if (e.target.id === "modal") closeModal(); };
  el("submitModal").onclick = (e) => { if (e.target.id === "submitModal") el("submitModal").classList.remove("open"); };
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeModal(); el("submitModal").classList.remove("open"); }
    if (e.key === "ArrowLeft" && !isTyping()) el("prevDay").click();
    if (e.key === "ArrowRight" && !isTyping()) el("nextDay").click();
  });
}
function isTyping() {
  const a = document.activeElement;
  return a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA");
}

/* ---- boot -------------------------------------------------------------- */
function boot() {
  el("brandName").textContent = CONFIG.siteName;
  el("brandTag").textContent = CONFIG.tagline;
  document.title = `${CONFIG.siteName} — ${CONFIG.tagline}`;
  el("year").textContent = new Date().getFullYear();
  wireControls();
  wireForms();
  render();
  refreshLive();
}
document.addEventListener("DOMContentLoaded", boot);
