/* =========================================================================
 *  Lets Do It Dallas — DFW district radar
 *  Self-contained dark vector map: glowing district nodes sized by tonight's
 *  event count, hover preview card, click-to-filter. No map libraries.
 *  app.js calls RADAR.init(api) then RADAR.update(list) on each render.
 *  ========================================================================= */

const RADAR = (function () {
  const NS = "http://www.w3.org/2000/svg";
  let api = null;
  let svg, card, listBox;
  let counts = {};

  function districtOf(a) {
    const area = (a.area || "").toLowerCase();
    for (const d of DISTRICTS) {
      if (d.match.some((m) => area.includes(m))) return d.slug;
    }
    return null;
  }

  function el(tag, attrs, parent) {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
    if (parent) parent.appendChild(n);
    return n;
  }

  function build() {
    svg = document.getElementById("radarMap");
    card = document.getElementById("radarCard");
    listBox = document.getElementById("radarList");
    if (!svg) return;

    // background grid
    const grid = el("g", { class: "radar-grid" }, svg);
    for (let x = 0; x <= 800; x += 50) el("line", { x1: x, y1: 0, x2: x, y2: 520 }, grid);
    for (let y = 0; y <= 520; y += 50) el("line", { x1: 0, y1: y, x2: 800, y2: y }, grid);
    // range rings centered between the two downtowns
    const rings = el("g", { class: "radar-rings" }, svg);
    [90, 180, 270].forEach((r) => el("circle", { cx: 400, cy: 280, r }, rings));
    el("line", { x1: 150, y1: 318, x2: 588, y2: 318, class: "radar-axis" }, svg);

    // nodes
    for (const d of DISTRICTS) {
      const g = el("g", { class: "radar-node", "data-slug": d.slug, transform: `translate(${d.x} ${d.y})` }, svg);
      el("circle", { class: "halo", r: 14 }, g);
      el("circle", { class: "core", r: 4 }, g);
      const lbl = el("text", { class: "nlabel", x: 10, y: 4 }, g);
      lbl.textContent = d.label.toUpperCase();

      g.addEventListener("mouseenter", () => showCard(d));
      g.addEventListener("mouseleave", hideCard);
      g.addEventListener("click", () => api && api.onDistrict(
        api.activeDistrict() === d.slug ? null : d.slug));
    }
  }

  function showCard(d) {
    if (!card || !api) return;
    const todays = api.getDayList().filter((a) => districtOf(a) === d.slug);
    const top = todays.slice(0, 3);
    card.innerHTML = `
      <div class="rc-head">/ ${d.label.toUpperCase()} — ${todays.length} TODAY</div>
      ${top.length
        // event names/times are third-party feed text — escape (esc() lives in app.js)
        ? top.map((a) => `<div class="rc-row"><span>${esc(a.name)}</span><em>${esc(a.time)}</em></div>`).join("")
        : `<div class="rc-row"><span>Quiet today — check another date.</span></div>`}`;
    card.style.left = Math.min(d.x / 800 * 100, 62) + "%";
    card.style.top = Math.max(d.y / 520 * 100 - 8, 4) + "%";
    card.classList.add("on");
  }
  function hideCard() { card && card.classList.remove("on"); }

  function update(dayList) {
    if (!svg) return;
    counts = {};
    for (const a of dayList) {
      const s = districtOf(a);
      if (s) counts[s] = (counts[s] || 0) + 1;
    }
    const active = api ? api.activeDistrict() : null;
    svg.querySelectorAll(".radar-node").forEach((g) => {
      const slug = g.dataset.slug;
      const n = counts[slug] || 0;
      g.classList.toggle("hot", n > 0);
      g.classList.toggle("sel", active === slug);
      const core = g.querySelector(".core");
      core.setAttribute("r", n ? Math.min(4 + Math.sqrt(n) * 2, 12) : 3);
    });

    if (listBox) {
      const rows = DISTRICTS
        .map((d) => ({ d, n: counts[d.slug] || 0 }))
        .sort((a, b) => b.n - a.n);
      listBox.innerHTML = rows.map(({ d, n }, i) => `
        <button class="radar-row ${active === d.slug ? "sel" : ""} ${n ? "" : "dim"}" data-slug="${d.slug}">
          <span class="idx">(${String(i + 1).padStart(2, "0")})</span>
          <span class="lbl">${d.label}</span>
          <span class="cnt">${n} ${n === 1 ? "EVENT" : "EVENTS"}</span>
        </button>`).join("");
      listBox.querySelectorAll(".radar-row").forEach((b) => {
        b.onclick = () => api.onDistrict(api.activeDistrict() === b.dataset.slug ? null : b.dataset.slug);
      });
    }
  }

  return {
    init(a) { api = a; build(); },
    update,
    districtOf,
  };
})();
