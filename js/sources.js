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
async function loadGoogleSheet(date) {
  if (!CONFIG.googleSheetCsvUrl) return [];
  try {
    const res = await fetch(CONFIG.googleSheetCsvUrl);
    if (!res.ok) throw new Error("Sheet " + res.status);
    const rows = _parseCsv(await res.text());
    return _fromRows(rows, date, "sheet");
  } catch (err) {
    console.warn("Google Sheet source unavailable:", err.message);
    return [];
  }
}

/* ---- Hand-editable events.json (ships with the site) ------------------- */
async function loadEventsJson(date) {
  return _loadJsonFile(CONFIG.eventsJsonUrl, date, "json");
}

/* ---- Aggregated live-events.json (written nightly by GitHub Action) ---- */
async function loadAggregatedJson(date) {
  return _loadJsonFile(CONFIG.aggregatedJsonUrl, date, "auto");
}

async function _loadJsonFile(fileUrl, date, source) {
  if (!fileUrl) return [];
  try {
    const res = await fetch(fileUrl, { cache: "no-cache" });
    if (!res.ok) throw new Error("JSON " + res.status);
    const rows = await res.json();
    return _fromRows(rows, date, source);
  } catch (err) {
    // Silent: over file:// (or before the Action's first run) this is expected.
    return [];
  }
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
  const results = await Promise.allSettled([
    loadEventsJson(date),
    loadGoogleSheet(date),
    loadAggregatedJson(date),
  ]);
  const all = results.flatMap((r) => (r.status === "fulfilled" ? r.value : []));
  const seen = new Set();
  return all.filter((e) => {
    const key = _dedupeKey(e.name);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/* Normalized title used for de-duplication. Mirrors the server-side key in
   scripts/fetch_events.py so both layers collapse the same near-duplicates. */
function _dedupeKey(name) {
  return (name || "").toLowerCase()
    .replace(/\(.*?\)/g, " ")            // drop parentheticals like (18+)
    .replace(/&/g, " and ")
    .replace(/\b(tickets?|tour|live|concert|presents?|featuring|feat|with special guests?)\b/g, " ")
    .replace(/[^a-z0-9]+/g, " ").trim()
    .replace(/^the /, "");
}
