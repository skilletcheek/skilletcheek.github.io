/* =========================================================================
 *  Lets Do It Dallas — 3D wireframe Reunion Tower
 *  Pure-canvas 3D projection (no libraries): a geodesic wireframe sphere on
 *  a lattice column, slowly rotating, with cursor-depth tilt and scroll
 *  coupling. Emerald-on-obsidian, per the site's design system.
 *  ========================================================================= */

(function () {
  const REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const SPHERE_R = 150;          // model units
  const SPHERE_CY = -210;        // sphere center height above base
  const LAT_RINGS = 7;
  const LON_LINES = 12;
  const F = 700;                 // perspective focal length

  let canvas, ctx, W, H, dpr;
  let theta = 0;                 // continuous Y rotation
  let tiltX = 0, tiltTarget = 0; // cursor-driven X tilt
  let scrollBoost = 0;
  let lights = [];               // twinkling vertex lights on the sphere

  /* model geometry: arrays of 3D point loops (each loop is drawn as a path) */
  function buildLoops() {
    const loops = [];
    // latitude rings
    for (let i = 1; i < LAT_RINGS; i++) {
      const phi = (i / LAT_RINGS) * Math.PI;
      const r = SPHERE_R * Math.sin(phi);
      const y = SPHERE_CY - SPHERE_R * Math.cos(phi);
      const ring = [];
      for (let k = 0; k <= 48; k++) {
        const a = (k / 48) * Math.PI * 2;
        ring.push([r * Math.cos(a), y, r * Math.sin(a)]);
      }
      loops.push({ pts: ring, close: false });
    }
    // longitude lines
    for (let j = 0; j < LON_LINES; j++) {
      const a = (j / LON_LINES) * Math.PI * 2;
      const line = [];
      for (let k = 0; k <= 40; k++) {
        const phi = (k / 40) * Math.PI;
        const r = SPHERE_R * Math.sin(phi);
        line.push([r * Math.cos(a), SPHERE_CY - SPHERE_R * Math.cos(phi), r * Math.sin(a)]);
      }
      loops.push({ pts: line, close: false });
    }
    return loops;
  }

  function buildColumn() {
    const legs = [];
    const top = SPHERE_CY + SPHERE_R * 0.62;
    // three lattice legs meeting under the sphere, spreading to the base
    for (let j = 0; j < 3; j++) {
      const a = (j / 3) * Math.PI * 2 + Math.PI / 6;
      legs.push({ pts: [[Math.cos(a) * 64, 0, Math.sin(a) * 64], [Math.cos(a) * 12, top, Math.sin(a) * 12]], close: false });
    }
    // core shaft
    legs.push({ pts: [[0, 0, 0], [0, SPHERE_CY, 0]], close: false });
    // cross-struts between legs
    for (let s = 1; s <= 4; s++) {
      const t = s / 5;
      const ring = [];
      for (let j = 0; j <= 3; j++) {
        const a = (j / 3) * Math.PI * 2 + Math.PI / 6;
        const r = 64 + (12 - 64) * t;
        ring.push([Math.cos(a) * r, top * t, Math.sin(a) * r]);
      }
      legs.push({ pts: ring, close: false });
    }
    return legs;
  }

  const SPHERE_LOOPS = buildLoops();
  const COLUMN_LOOPS = buildColumn();

  function makeLights() {
    lights = [];
    for (let i = 0; i < 26; i++) {
      const phi = Math.random() * Math.PI;
      const a = Math.random() * Math.PI * 2;
      const r = SPHERE_R * Math.sin(phi);
      lights.push({
        p: [r * Math.cos(a), SPHERE_CY - SPHERE_R * Math.cos(phi), r * Math.sin(a)],
        ph: Math.random() * Math.PI * 2,
        sp: 0.5 + Math.random() * 1.5,
      });
    }
  }

  function project(p, rotY, rotX) {
    let [x, y, z] = p;
    // rotate around Y
    const cy = Math.cos(rotY), sy = Math.sin(rotY);
    [x, z] = [x * cy - z * sy, x * sy + z * cy];
    // rotate around X (tilt)
    const cx = Math.cos(rotX), sx = Math.sin(rotX);
    [y, z] = [y * cx - z * sx, y * sx + z * cx];
    const s = F / (F + z + 320);
    return [W / 2 + x * s, H * 0.88 + y * s, s];
  }

  function drawLoops(loops, rotY, rotX, style, width) {
    ctx.strokeStyle = style;
    ctx.lineWidth = width;
    for (const loop of loops) {
      ctx.beginPath();
      loop.pts.forEach((p, i) => {
        const [px, py] = project(p, rotY, rotX);
        i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
      });
      ctx.stroke();
    }
  }

  function frame(tms) {
    ctx.clearRect(0, 0, W, H);
    const rotY = theta + scrollBoost;
    tiltX += (tiltTarget - tiltX) * 0.06;

    // column behind
    drawLoops(COLUMN_LOOPS, rotY * 0.4, tiltX * 0.5, "rgba(0,255,135,0.20)", 1);
    // sphere mesh, two passes for depth glow
    drawLoops(SPHERE_LOOPS, rotY, tiltX, "rgba(0,255,135,0.10)", 2.2);
    drawLoops(SPHERE_LOOPS, rotY, tiltX, "rgba(0,255,135,0.34)", 0.8);

    // vertex lights
    const t = tms / 1000;
    for (const L of lights) {
      const [px, py, s] = project(L.p, rotY, tiltX);
      const a = 0.25 + 0.75 * (0.5 + 0.5 * Math.sin(L.ph + t * L.sp));
      ctx.fillStyle = `rgba(0,255,135,${(a * (s > 0.72 ? 1 : 0.35)).toFixed(3)})`;
      ctx.beginPath();
      ctx.arc(px, py, 1.6 * s + a * 1.2, 0, Math.PI * 2);
      ctx.fill();
    }

    theta += 0.0035;
    if (!REDUCED) requestAnimationFrame(frame);
  }

  function fit() {
    const rect = canvas.parentElement.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = rect.width; H = rect.height;
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + "px"; canvas.style.height = H + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function init() {
    canvas = document.getElementById("towerCanvas");
    if (!canvas) return;
    ctx = canvas.getContext("2d");
    makeLights();
    fit();
    window.addEventListener("resize", fit);
    window.addEventListener("pointermove", (e) => {
      const r = canvas.getBoundingClientRect();
      const dx = (e.clientX - (r.left + r.width / 2)) / r.width;
      const dy = (e.clientY - (r.top + r.height / 2)) / r.height;
      tiltTarget = Math.max(-0.5, Math.min(0.5, dy * 0.5));
      scrollBoost = scrollBoost * 0.9 + dx * 0.12;
    }, { passive: true });
    window.addEventListener("scroll", () => {
      scrollBoost = window.scrollY * 0.0006;
    }, { passive: true });
    if (REDUCED) { frame(0); } else { requestAnimationFrame(frame); }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
