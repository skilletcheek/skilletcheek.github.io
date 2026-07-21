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
  let glowSprite = null, glowR = 0;   // pre-rendered vertex-light dot
  let backdropCv = null, backdropR = 0; // pre-rendered ambient glow

  // Under 900px the tower is a 30%-opacity backdrop behind the hero copy
  // (see .hero-canvas in styles.css), so it renders one static frame there:
  // nobody perceives the rotation, and phones are where the frame cost hurts.
  const SMALL = window.matchMedia && window.matchMedia("(max-width: 900px)").matches;
  const STATIC = REDUCED || SMALL;
  const FRAME_MS = 1000 / 30;    // 30fps is plenty for a 0.0035rad/frame drift

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

  /* Pre-render the ambient glow once into an offscreen canvas. Building a
     radial gradient and filling the whole canvas every frame was pure waste —
     the gradient never changes, only where it's stamped. */
  function makeBackdrop() {
    backdropR = Math.ceil(SPHERE_R * 2.1);
    const s = document.createElement("canvas");
    s.width = s.height = backdropR * 2;
    const c = s.getContext("2d");
    const g = c.createRadialGradient(backdropR, backdropR, 0, backdropR, backdropR, backdropR);
    g.addColorStop(0, "rgba(0,255,135,0.16)");
    g.addColorStop(0.45, "rgba(0,255,135,0.05)");
    g.addColorStop(1, "rgba(0,255,135,0)");
    c.fillStyle = g;
    c.fillRect(0, 0, backdropR * 2, backdropR * 2);
    backdropCv = s;
  }

  /* Pre-render one soft dot. Drawn with drawImage per light instead of
     shadowBlur, which is a per-call gaussian blur — the single most expensive
     thing you can ask a 2d canvas to do. */
  function makeGlowSprite() {
    glowR = 16;
    const s = document.createElement("canvas");
    s.width = s.height = glowR * 2;
    const c = s.getContext("2d");
    const g = c.createRadialGradient(glowR, glowR, 0, glowR, glowR, glowR);
    // tight bright core + fast falloff, so a scaled stamp still reads as a
    // point light rather than a blob
    g.addColorStop(0, "rgba(200,255,225,1)");
    g.addColorStop(0.16, "rgba(170,255,210,0.95)");
    g.addColorStop(0.34, "rgba(0,255,135,0.38)");
    g.addColorStop(1, "rgba(0,255,135,0)");
    c.fillStyle = g;
    c.fillRect(0, 0, glowR * 2, glowR * 2);
    glowSprite = s;
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

  // All loops sharing a style go into ONE path and one stroke() — a stroke per
  // loop meant 18 separate rasterisations of the same colour.
  function drawLoops(loops, rotY, rotX, style, width) {
    ctx.strokeStyle = style;
    ctx.lineWidth = width;
    ctx.beginPath();
    for (const loop of loops) {
      loop.pts.forEach((p, i) => {
        const [px, py] = project(p, rotY, rotX);
        i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
      });
    }
    ctx.stroke();
  }

  function backdrop(cx, cy) {
    // soft emerald "skyline" glow anchored behind the sphere, so the wireframe
    // reads as a lit landmark rather than floating lines on black
    ctx.drawImage(backdropCv, cx - backdropR, cy - backdropR);
  }

  function frame(tms) {
    ctx.clearRect(0, 0, W, H);
    const rotY = theta + scrollBoost;
    tiltX += (tiltTarget - tiltX) * 0.06;

    // ambient glow behind the sphere center
    const [scx, scy] = project([0, SPHERE_CY, 0], rotY, tiltX);
    backdrop(scx, scy);

    // additive blending so overlapping strokes build up light like neon
    ctx.globalCompositeOperation = "lighter";

    // column behind
    drawLoops(COLUMN_LOOPS, rotY * 0.4, tiltX * 0.5, "rgba(0,255,135,0.30)", 1);
    // sphere mesh: three stacked passes, widest+faintest first. Under "lighter"
    // these sum into the same neon bloom shadowBlur gave us, but as plain fill
    // rate instead of a gaussian blur per stroke.
    drawLoops(SPHERE_LOOPS, rotY, tiltX, "rgba(0,255,135,0.10)", 4.5);
    drawLoops(SPHERE_LOOPS, rotY, tiltX, "rgba(0,255,135,0.16)", 2.4);
    drawLoops(SPHERE_LOOPS, rotY, tiltX, "rgba(0,255,150,0.62)", 1);

    // vertex lights — pre-rendered sprite, scaled per light
    const t = tms / 1000;
    for (const L of lights) {
      const [px, py, s] = project(L.p, rotY, tiltX);
      const a = 0.3 + 0.7 * (0.5 + 0.5 * Math.sin(L.ph + t * L.sp));
      const front = s > 0.72 ? 1 : 0.45;
      const rad = (1.8 * s + a * 1.4) * 2.1; // sprite is mostly falloff, so scale up
      ctx.globalAlpha = a * front;
      ctx.drawImage(glowSprite, px - rad, py - rad, rad * 2, rad * 2);
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";

    theta += 0.0035 * (STATIC ? 1 : 2); // 30fps: advance twice per frame

    // Only keep the loop alive while the canvas is on-screen and the tab is
    // visible — a rotating canvas burns CPU/GPU for nothing once it scrolls
    // out of view.
    if (!STATIC && onScreen && !document.hidden) requestAnimationFrame(tick);
    else running = false;
  }

  // Throttle to ~30fps. The rotation drifts slowly enough that 60fps bought
  // nothing visible while doubling the frame budget.
  let lastDraw = 0;
  function tick(tms) {
    if (tms - lastDraw >= FRAME_MS) { lastDraw = tms; frame(tms); return; }
    if (!STATIC && onScreen && !document.hidden) requestAnimationFrame(tick);
    else running = false;
  }

  let running = false;
  let onScreen = true;
  function start() {
    if (running || STATIC) return;
    running = true;
    requestAnimationFrame(tick);
  }

  function fit() {
    const rect = canvas.parentElement.getBoundingClientRect();
    // A soft glowing wireframe gains nothing from a 2x buffer, and fill rate
    // scales with the square of this — 1.5 costs 44% less than 2.
    dpr = Math.min(window.devicePixelRatio || 1, SMALL ? 1 : 1.5);
    W = rect.width; H = rect.height;
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + "px"; canvas.style.height = H + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (STATIC) frame(0); // re-draw the single frame at the new size
  }

  function init() {
    canvas = document.getElementById("towerCanvas");
    if (!canvas) return;
    ctx = canvas.getContext("2d");
    makeLights();
    makeBackdrop();
    makeGlowSprite();
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
    if (STATIC) { frame(0); return; } // fit() already drew; keep it explicit
    // Pause when scrolled off-screen; resume when it comes back into view.
    if ("IntersectionObserver" in window) {
      new IntersectionObserver((entries) => {
        onScreen = entries[0].isIntersecting;
        if (onScreen) start();
      }, { threshold: 0.01 }).observe(canvas);
    }
    // Pause when the tab is backgrounded; resume on return.
    document.addEventListener("visibilitychange", () => { if (!document.hidden) start(); });
    start();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
