/* =========================================================================
 *  RJ Does Dallas — live event sources
 *  -------------------------------------------------------------------------
 *  Each loader takes a JS Date and returns a Promise<normalizedEvent[]>.
 *  Everything fails gracefully: if a key is missing or a request errors,
 *  it returns [] and the curated data still shows. Nothing here blocks
 *  the page from rendering.
 *  ========================================================================= */

function _dayBounds(date) {
  const start = new Date(date); start.setHours(0, 0, 0, 0);
  const end = new Date(date);   end.setHours(23, 59, 59, 0);
  return { start, end };
}

function _normalize(e) {
  return {
    name: e.name || "Untitled event",
    cat: e.cat || "festival",
    area: e.area || "Dallas–Fort Worth",
    time: e.time || "See details",
    cost: (e.cost === 0 || e.cost) ? e.cost : null,
    desc: e.desc || "",
    url: e.url || "#",
    dateISO: e.dateISO || null,      // one-off events carry a concrete date
    source: e.source || "local",
    sponsor: e.sponsor || null,
    image: e.image || null,          // optional artwork (duotone-treated in CSS)
  };
}

/* ---- Ticketmaster Discovery API ---------------------------------------- */
async function loadTicketmaster(date) {
  const key = CONFIG.ticketmasterApiKey;
  if (!key) return [];
  const { start, end } = _dayBounds(date);
  const seg = {
    "Music": "music", "Sports": "sports", "Arts & Theatre": "arts",
    "Film": "arts", "Family": "family", "Miscellaneous": "festival",
  };
  const url = new URL("https://app.ticketmaster.com/discovery/v2/events.json");
  url.search = new URLSearchParams({
    apikey: key,
    latlong: `${CONFIG.geo.lat},${CONFIG.geo.lng}`,
    radius: String(CONFIG.geo.radiusMiles),
    unit: "miles",
    startDateTime: start.toISOString().split(".")[0] + "Z",
    endDateTime: end.toISOString().split(".")[0] + "Z",
    size: "120", sort: "date,asc",
  }).toString();

  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error("TM " + res.status);
    const data = await res.json();
    const events = (data._embedded && data._embedded.events) || [];
    return events.map((ev) => {
      const cls = (ev.classifications && ev.classifications[0]) || {};
      const segName = cls.segment && cls.segment.name;
      const venue = ev._embedded && ev._embedded.venues && ev._embedded.venues[0];
      const priceRange = ev.priceRanges && ev.priceRanges[0];
      const localTime = ev.dates && ev.dates.start && ev.dates.start.localTime;
      return _normalize({
        name: ev.name,
        cat: seg[segName] || "festival",
        area: venue ? [venue.name, venue.city && venue.city.name].filter(Boolean).join(", ") : "DFW",
        time: localTime ? _pretty12h(localTime) : "See details",
        cost: priceRange ? Math.round(priceRange.min) : null,
        desc: (cls.genre && cls.genre.name && cls.genre.name !== "Undefined")
          ? `${cls.genre.name} event via Ticketmaster.` : "Live event via Ticketmaster.",
        url: ev.url,
        dateISO: ev.dates && ev.dates.start && ev.dates.start.localDate,
        source: "ticketmaster",
        image: (() => {
          const imgs = ev.images || [];
          const good = imgs.find((im) => im.ratio === "16_9" && im.width >= 500 && im.width <= 1200) || imgs[0];
          return good ? good.url : null;
        })(),
      });
    });
  } catch (err) {
    console.warn("Ticketmaster source unavailable:", err.message);
    return [];
  }
}

/* ---- SeatGeek ----------------------------------------------------------- */
async function loadSeatGeek(date) {
  const id = CONFIG.seatgeekClientId;
  if (!id) return [];
  const iso = _isoDate(date);
  const tax = (name) => {
    if (!name) return "festival";
    if (/sports|nba|nfl|mlb|nhl|mls|soccer|baseball|basketball|football|hockey|racing|rodeo/.test(name)) return "sports";
    if (/concert|music/.test(name)) return "music";
    if (/theater|broadway|classical|opera|ballet|dance|literary/.test(name)) return "arts";
    if (/comedy/.test(name)) return "nightlife";
    if (/family/.test(name)) return "family";
    if (/festival/.test(name)) return "festival";
    return "festival";
  };
  const url = new URL("https://api.seatgeek.com/2/events");
  url.search = new URLSearchParams({
    client_id: id,
    lat: String(CONFIG.geo.lat), lon: String(CONFIG.geo.lng),
    range: CONFIG.geo.radiusMiles + "mi",
    "datetime_local.gte": iso + "T00:00:00",
    "datetime_local.lte": iso + "T23:59:59",
    per_page: "100",
  }).toString();
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error("SG " + res.status);
    const data = await res.json();
    return (data.events || []).map((ev) => _normalize({
      name: ev.short_title || ev.title,
      cat: tax(((ev.taxonomies && ev.taxonomies[0]) || {}).name),
      area: ev.venue ? [ev.venue.name, ev.venue.city].filter(Boolean).join(", ") : "DFW",
      time: ev.datetime_local ? _pretty12h(ev.datetime_local.slice(11, 16)) : "See details",
      cost: ev.stats && ev.stats.lowest_price != null ? Math.round(ev.stats.lowest_price) : null,
      desc: ev.type ? `${ev.type.replace(/_/g, " ")} via SeatGeek.` : "Live event via SeatGeek.",
      url: ev.url,
      dateISO: ev.datetime_local ? ev.datetime_local.slice(0, 10) : null,
      source: "seatgeek",
      image: (ev.performers && ev.performers[0] && ev.performers[0].image) || null,
    }));
  } catch (err) {
    console.warn("SeatGeek source unavailable:", err.message);
    return [];
  }
}

/* ---- PredictHQ (optional, paid) ---------------------------------------- */
async function loadPredictHQ(date) {
  const token = CONFIG.predicthqToken;
  if (!token) return [];
  const { start, end } = _dayBounds(date);
  const cat = {
    concerts: "music", "performing-arts": "arts", sports: "sports",
    festivals: "festival", "community": "family", expos: "market",
  };
  const url = new URL("https://api.predicthq.com/v1/events/");
  url.search = new URLSearchParams({
    "within": `${CONFIG.geo.radiusMiles}mi@${CONFIG.geo.lat},${CONFIG.geo.lng}`,
    "active.gte": start.toISOString().slice(0, 10),
    "active.lte": end.toISOString().slice(0, 10),
    "limit": "100", "sort": "start",
  }).toString();
  try {
    const res = await fetch(url, { headers: { Authorization: "Bearer " + token, Accept: "application/json" } });
    if (!res.ok) throw new Error("PHQ " + res.status);
    const data = await res.json();
    return (data.results || []).map((ev) => _normalize({
      name: ev.title,
      cat: cat[ev.category] || "festival",
      area: (ev.geo && ev.geo.address && ev.geo.address.locality) || "DFW",
      time: ev.start ? _pretty12h(ev.start.slice(11, 16)) : "See details",
      desc: ev.description || "Event via PredictHQ.",
      url: "#",
      dateISO: ev.start ? ev.start.slice(0, 10) : null,
      source: "predicthq",
    }));
  } catch (err) {
    console.warn("PredictHQ source unavailable:", err.message);
    return [];
  }
}

/* ---- Google Sheet (Publish to web -> CSV) ------------------------------ */
/* Cached like the JSON feeds — the sheet is a whole-catalog CSV, so pulling it
   again on every date change was a cross-origin round-trip for data we had. */
let _sheetRows = null;
async function loadGoogleSheet(date) {
  if (!CONFIG.googleSheetCsvUrl) return [];
  if (!_sheetRows) {
    _sheetRows = fetch(CONFIG.googleSheetCsvUrl)
      .then((res) => {
        if (!res.ok) throw new Error("Sheet " + res.status);
        return res.text();
      })
      .then(_parseCsv)
      .catch((err) => {
        console.warn("Google Sheet source unavailable:", err.message);
        _sheetRows = null;
        return [];
      });
  }
  return _fromRows(await _sheetRows, date, "sheet");
}

/* ---- Hand-editable events.json (ships with the site) ------------------- */
async function loadEventsJson(date) {
  return _loadJsonFile(CONFIG.eventsJsonUrl, date, "json");
}

/* ---- Aggregated live-events.json (written nightly by GitHub Action) ---- */
async function loadAggregatedJson(date) {
  return _loadJsonFile(CONFIG.aggregatedJsonUrl, date, "auto");
}

/* ---- eventbrite.json (refreshed by hand — see scripts/fetch_eventbrite_local.py)
   Eventbrite blocks datacenter IPs, so it can't run in the nightly Action. The
   file already excludes anything live-events.json carries. ---------------- */
async function loadEventbriteJson(date) {
  return _loadJsonFile(CONFIG.eventbriteJsonUrl, date, "eventbrite");
}

/* Feed files are whole-catalog snapshots (live-events.json alone is ~226 KB
   covering 30 days), but each render needs a single date out of them. Fetching
   per date change meant three revalidation round-trips and a full re-parse
   every time the user pressed ← or →. Fetch each file at most once per page
   view instead, keep the parsed rows in memory, and filter by date locally.
   The in-flight promise is cached too, so the four parallel loaders never
   race into duplicate requests for the same file. */
const _fileCache = new Map();

function _fetchRows(fileUrl) {
  if (_fileCache.has(fileUrl)) return _fileCache.get(fileUrl);
  const p = fetch(fileUrl, { cache: "no-cache" })
    .then((res) => {
      if (!res.ok) throw new Error("JSON " + res.status);
      return res.json();
    })
    .catch(() => {
      // Silent: over file:// (or before the Action's first run) this is expected.
      // Drop the rejected promise so a later navigation can retry.
      _fileCache.delete(fileUrl);
      return [];
    });
  _fileCache.set(fileUrl, p);
  return p;
}

async function _loadJsonFile(fileUrl, date, source) {
  if (!fileUrl) return [];
  return _fromRows(await _fetchRows(fileUrl), date, source);
}

/* ---- helpers ----------------------------------------------------------- */
function _fromRows(rows, date, source) {
  const iso = _isoDate(date);
  return rows
    .filter((r) => (r.date || r.dateISO) === iso)
    .map((r) => _normalize({
      name: r.name,
      cat: (r.category || r.cat || "festival").toLowerCase(),
      area: r.area,
      time: r.time,
      cost: r.cost === "" || r.cost == null ? null : Number(r.cost),
      desc: r.description || r.desc,
      url: r.url,
      dateISO: r.date || r.dateISO,
      source,
      sponsor: r.sponsor || null,
      image: r.image || null,
    }));
}

function _isoDate(d) {
  return d.getFullYear() + "-" +
    String(d.getMonth() + 1).padStart(2, "0") + "-" +
    String(d.getDate()).padStart(2, "0");
}

function _pretty12h(hhmm) {
  const m = String(hhmm).match(/(\d{1,2}):(\d{2})/);
  if (!m) return hhmm;
  let h = parseInt(m[1], 10);
  const min = m[2];
  const ap = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;
  return `${h}:${min} ${ap}`;
}

/* Minimal CSV parser that tolerates quoted fields and commas inside quotes. */
function _parseCsv(text) {
  const lines = text.replace(/\r/g, "").split("\n").filter((l) => l.trim().length);
  if (!lines.length) return [];
  const parseLine = (line) => {
    const out = []; let cur = ""; let q = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (q) {
        if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
        else if (ch === '"') q = false;
        else cur += ch;
      } else {
        if (ch === '"') q = true;
        else if (ch === ",") { out.push(cur); cur = ""; }
        else cur += ch;
      }
    }
    out.push(cur);
    return out;
  };
  const headers = parseLine(lines[0]).map((h) => h.trim().toLowerCase());
  return lines.slice(1).map((line) => {
    const cells = parseLine(line);
    const obj = {};
    headers.forEach((h, i) => { obj[h] = (cells[i] || "").trim(); });
    return obj;
  });
}

/* Aggregate every configured live source for a given date.
   Order matters: earlier sources win when two carry the same event name
   (your own JSON/Sheet beats the auto-fetched file).

   NOTE: Ticketmaster and SeatGeek are intentionally NOT called from the
   browser. The nightly GitHub Action (scripts/fetch_events.py) already pulls
   both, de-duplicates them across sources, and writes live-events.json. Calling
   the APIs again here re-introduced the same events under slightly different
   titles (so the name-dedupe below missed them) and added a slow cross-origin
   round-trip to every date change. Loading the pre-built file instead is both
   duplicate-free and much faster. loadTicketmaster/loadSeatGeek/loadPredictHQ
   remain defined above if you ever want live API calls back. */
async function loadLiveEvents(date) {
  // The alias table must be in place before the dedupe pass below, but it is
  // not a source of rows — keep it out of the results array.
  const [results] = await Promise.all([
    Promise.allSettled([
      loadEventsJson(date),
      loadGoogleSheet(date),
      loadAggregatedJson(date),
      loadEventbriteJson(date),
    ]),
    _loadVenueAliases(),
  ]);
  const all = results.flatMap((r) => (r.status === "fulfilled" ? r.value : []));
  const kept = [];
  for (const e of all) {
    const norm = _dedupeKey(e.name);
    if (!norm) continue;
    const cand = {
      tokens: _meaningfulTokens(norm),
      venue: _venueTokens(e.area),
      mins: _timeMinutes(e.time),
    };
    if (!kept.some((k) => _sameEvent(cand, k.key))) kept.push({ event: e, key: cand });
  }
  return kept.map((k) => k.event);
}

/* Normalized title used for de-duplication. Mirrors _norm_name() in
   scripts/fetch_events.py so both layers collapse the same near-duplicates. */
function _dedupeKey(name) {
  return (name || "").toLowerCase()
    .replace(/\(.*?\)/g, " ")            // drop parentheticals like (18+)
    .replace(/&/g, " and ")
    .replace(/\b(tickets?|tour|live|concert|presents?|featuring|feat|with special guests?)\b/g, " ")
    .replace(/[^a-z0-9]+/g, " ").trim()
    .replace(/^the /, "");
}

/* The rest of this block mirrors _same_event()/_venue_tokens()/_time_minutes()
   in scripts/fetch_events.py. It has to: this layer only exists to catch the
   same show arriving from two *different* browser sources, and a title-only
   key is far too blunt for that. Keying on the name alone silently deleted
   every second showtime a venue runs — the 9:45 PM comedy set vanished because
   it shares a headliner with the 7:30, and the 8:00 PM performance vanished
   because it shares a title with the matinee. That was ~14% of the catalog on
   a busy Saturday. Two rows are one event only when the titles share a
   meaningful word AND the venues agree AND the start times are close. */
const _STOP = new Set(["the", "a", "an", "at", "vs", "v", "and", "of", "in", "on",
  "for", "with", "not", "featuring", "night", "show", "series"]);
const _VAGUE_TIMES = ["", "see details", "all day", "doors — see listing"];
// Sources disagree about doors vs downbeat, so allow slack — but keep it under
// the gap between a matinee and an evening show so those stay separate.
const _TIME_SLACK_MIN = 90;

function _meaningfulTokens(norm) {
  return new Set(norm.split(" ").filter((t) => t.length > 1 && !_STOP.has(t)));
}

/* variant venue name -> canonical, keyed by _venueKey. Populated from
   venue-aliases.json, the same file the nightly fetch reads. Empty until that
   file loads, which only costs us the duplicates it would have merged. */
let _venueAliases = new Map();

async function _loadVenueAliases() {
  if (_venueAliases.size) return;
  try {
    const raw = await _fetchRows("venue-aliases.json");
    const map = new Map();
    for (const [canonical, variants] of Object.entries(raw.aliases || {})) {
      for (const v of [...variants, canonical]) map.set(_venueKey(v), canonical);
    }
    _venueAliases = map;
  } catch (_) { /* duplicates survive; nothing else breaks */ }
}

/* Punctuation/suffix-insensitive form used to look an alias up. Mirrors
   _venue_key() in scripts/fetch_events.py. */
function _venueKey(name) {
  let v = String(name || "").split(",")[0].trim().toLowerCase();
  v = v.replace(/\s+-\s+[^-]+$/, "");          // trailing city: "… - Sanger"
  v = v.replace(/&/g, " and ").replace(/['’]/g, "");
  return v.replace(/[^a-z0-9]+/g, " ").trim();
}

/* A renamed venue shares no tokens with its old name, so rewrite known
   variants to one canonical name first — see venue-aliases.json. */
function _venueTokens(area) {
  const key = _venueKey(area);
  const canonical = _venueAliases.get(key);
  const v = canonical ? _venueKey(canonical) : key;
  return new Set(v.split(" ").filter((t) => t.length > 1 && !_STOP.has(t)));
}

function _timeMinutes(t) {
  const s = String(t || "").trim().toLowerCase();
  if (_VAGUE_TIMES.includes(s)) return null;
  const m = String(t || "").match(/^\s*(\d{1,2}):(\d{2})\s*(AM|PM)/i);
  if (!m) return null;
  return (parseInt(m[1], 10) % 12 + (m[3].toUpperCase() === "PM" ? 12 : 0)) * 60
    + parseInt(m[2], 10);
}

function _subsetOrEqual(a, b) {
  const fits = (x, y) => [...x].every((t) => y.has(t));
  return fits(a, b) || fits(b, a);
}

function _sameEvent(a, b) {
  if (![...a.tokens].some((t) => b.tokens.has(t))) return false;  // unrelated titles
  if (!a.venue.size || !b.venue.size) return false;               // unknown venue: don't guess
  if (!_subsetOrEqual(a.venue, b.venue)) return false;
  if (a.mins == null || b.mins == null) return true;              // unknown time can't prove separation
  return Math.abs(a.mins - b.mins) <= _TIME_SLACK_MIN;
}
