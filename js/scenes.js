/* =========================================================================
 *  RJ Does Dallas — animated landmark scenes
 *  Four hand-built SVG scenes of DFW landmarks that crossfade periodically.
 *  Self-contained: no images, no libraries. Animations live in styles.css
 *  (classes inside the inline SVG), so prefers-reduced-motion can kill them.
 *  ========================================================================= */

const SCENES = [
  {
    id: "dallas-sunset",
    caption: "Reunion Tower · Downtown Dallas",
    svg: `
<svg viewBox="0 0 1200 520" preserveAspectRatio="xMinYMax slice" aria-hidden="true">
  <defs>
    <linearGradient id="s1-sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2b1a4e"/><stop offset=".45" stop-color="#7a2d5e"/>
      <stop offset=".75" stop-color="#e2725b"/><stop offset="1" stop-color="#ffb75e"/>
    </linearGradient>
    <radialGradient id="s1-sun" cx=".5" cy=".5" r=".5">
      <stop offset="0" stop-color="#fff3c4"/><stop offset=".55" stop-color="#ffcf5c"/>
      <stop offset="1" stop-color="#ffcf5c" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="1200" height="520" fill="url(#s1-sky)"/>
  <circle class="sun-glow" cx="880" cy="330" r="130" fill="url(#s1-sun)"/>
  <circle cx="880" cy="330" r="46" fill="#fff0b8"/>
  <g class="drift" fill="none" stroke="#3a2140" stroke-width="3" stroke-linecap="round">
    <path d="M300 150 q8 -9 16 0 M316 150 q8 -9 16 0"/>
    <path d="M370 180 q7 -8 14 0 M384 180 q7 -8 14 0" opacity=".7"/>
    <path d="M255 205 q6 -7 12 0 M267 205 q6 -7 12 0" opacity=".5"/>
  </g>
  <!-- back skyline -->
  <g fill="#57284f" opacity=".55">
    <rect x="330" y="270" width="55" height="250"/><rect x="400" y="235" width="70" height="285"/>
    <rect x="490" y="290" width="48" height="230"/><rect x="560" y="255" width="62" height="265"/>
    <rect x="700" y="280" width="52" height="240"/><rect x="775" y="240" width="66" height="280"/>
    <rect x="905" y="285" width="58" height="235"/><rect x="985" y="260" width="50" height="260"/>
    <rect x="1075" y="290" width="64" height="230"/>
  </g>
  <!-- front skyline -->
  <g fill="#2c1233">
    <polygon points="435,180 505,180 512,205 512,520 428,520 428,205"/>
    <rect x="540" y="215" width="80" height="305"/>
    <polygon points="660,150 668,138 676,150 676,520 660,520"/>
    <rect x="640" y="200" width="78" height="320"/>
    <rect x="745" y="170" width="95" height="350"/>
    <rect x="870" y="230" width="72" height="290"/>
    <polygon points="965,250 1040,195 1040,520 965,520"/>
    <rect x="1060" y="245" width="90" height="275"/>
  </g>
  <!-- lit windows -->
  <g fill="#ffd98a">
    <g opacity=".9">
      <rect x="760" y="195" width="7" height="9"/><rect x="778" y="215" width="7" height="9"/>
      <rect x="812" y="195" width="7" height="9"/><rect x="795" y="250" width="7" height="9"/>
      <rect x="760" y="290" width="7" height="9"/><rect x="820" y="310" width="7" height="9"/>
      <rect x="555" y="235" width="6" height="8"/><rect x="585" y="265" width="6" height="8"/>
      <rect x="600" y="300" width="6" height="8"/><rect x="655" y="230" width="6" height="8"/>
      <rect x="690" y="270" width="6" height="8"/><rect x="885" y="255" width="6" height="8"/>
      <rect x="915" y="300" width="6" height="8"/><rect x="1080" y="270" width="6" height="8"/>
    </g>
    <g class="w-flicker">
      <rect x="452" y="210" width="7" height="9"/><rect x="480" y="245" width="7" height="9"/>
      <rect x="795" y="340" width="7" height="9"/><rect x="1105" y="300" width="6" height="8"/>
      <rect x="990" y="280" width="6" height="8"/><rect x="668" y="310" width="6" height="8"/>
    </g>
  </g>
  <!-- green crown (Bank of America Plaza nod) -->
  <path d="M745 170 h95 M745 170 v60 M840 170 v60" fill="none" stroke="#4ade80"
        stroke-width="4" class="neon-green" stroke-linecap="round"/>
  <!-- Reunion Tower -->
  <g>
    <path d="M150 520 L196 250 M242 520 L196 250 M196 520 L196 240" stroke="#1d0b26"
          stroke-width="10" fill="none" stroke-linecap="round"/>
    <circle cx="196" cy="185" r="58" fill="#1d0b26"/>
    <circle cx="196" cy="185" r="58" fill="none" stroke="#3a1b45" stroke-width="2"/>
    <path d="M148 160 a58 58 0 0 1 96 0 M144 200 a58 58 0 0 0 104 0 M196 127 v116 M160 145 a58 58 0 0 1 72 80 M232 145 a58 58 0 0 0 -72 80"
          stroke="#3a1b45" stroke-width="2" fill="none"/>
    <g fill="#ffe08a">
      <circle class="tw t1" cx="170" cy="165" r="3.4"/><circle class="tw t2" cx="222" cy="160" r="3.4"/>
      <circle class="tw t3" cx="196" cy="145" r="3.4"/><circle class="tw t4" cx="158" cy="196" r="3.4"/>
      <circle class="tw t1" cx="234" cy="198" r="3.4"/><circle class="tw t3" cx="182" cy="218" r="3.4"/>
      <circle class="tw t2" cx="212" cy="215" r="3.4"/><circle class="tw t4" cx="196" cy="185" r="3.8"/>
    </g>
  </g>
</svg>`,
  },
  {
    id: "hunt-hill-bridge",
    caption: "Margaret Hunt Hill Bridge · Trinity River",
    svg: `
<svg viewBox="0 0 1200 520" preserveAspectRatio="xMidYMax slice" aria-hidden="true">
  <defs>
    <linearGradient id="s2-sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#131c3f"/><stop offset=".55" stop-color="#41346f"/>
      <stop offset=".85" stop-color="#8a4d7c"/><stop offset="1" stop-color="#c96a7e"/>
    </linearGradient>
    <linearGradient id="s2-water" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#1a1440"/><stop offset="1" stop-color="#0d0a24"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="440" fill="url(#s2-sky)"/>
  <g fill="#fff">
    <circle class="tw t1" cx="120" cy="70" r="2"/><circle class="tw t3" cx="300" cy="45" r="1.6"/>
    <circle class="tw t2" cx="520" cy="80" r="2"/><circle class="tw t4" cx="760" cy="40" r="1.7"/>
    <circle class="tw t1" cx="950" cy="90" r="2"/><circle class="tw t3" cx="1100" cy="55" r="1.6"/>
    <circle class="tw t2" cx="1010" cy="140" r="1.5"/><circle class="tw t4" cx="220" cy="130" r="1.5"/>
  </g>
  <circle cx="1030" cy="110" r="34" fill="#f6ecd4" opacity=".95"/>
  <circle cx="1043" cy="100" r="30" fill="#41346f"/>
  <!-- distant skyline -->
  <g fill="#241a4d" opacity=".8">
    <rect x="40" y="330" width="40" height="110"/><rect x="95" y="300" width="55" height="140"/>
    <rect x="165" y="345" width="34" height="95"/><rect x="1010" y="320" width="48" height="120"/>
    <rect x="1075" y="340" width="60" height="100"/>
  </g>
  <!-- water -->
  <rect y="440" width="1200" height="80" fill="url(#s2-water)"/>
  <g stroke="#8f7bd8" stroke-width="2" fill="none" opacity=".5" class="shimmer">
    <path d="M180 470 q30 6 60 0 t60 0"/>
    <path d="M540 490 q30 6 60 0 t60 0"/>
    <path d="M860 465 q30 6 60 0 t60 0"/>
  </g>
  <!-- arch + cables -->
  <path d="M100 428 Q600 30 1100 428" fill="none" stroke="#f5f2ff" stroke-width="11" stroke-linecap="round"/>
  <path d="M200 352 L262 428 M300 300 L352 428 M400 258 L442 428 M500 235 L530 428
           M600 228 L600 428 M700 235 L670 428 M800 258 L758 428 M900 300 L848 428 M1000 352 L938 428"
        stroke="#cfc4f5" stroke-width="2.2" opacity=".75" fill="none"/>
  <!-- deck -->
  <rect x="0" y="428" width="1200" height="12" fill="#0e0a20"/>
  <rect x="0" y="426" width="1200" height="3" fill="#6e5fae"/>
  <!-- traffic -->
  <g class="car-east">
    <circle cx="0" cy="422" r="4" fill="#fff8d6"/><circle cx="14" cy="422" r="4" fill="#fff8d6"/>
    <circle cx="7" cy="422" r="11" fill="#fff8d6" opacity=".25"/>
  </g>
  <g class="car-east c2">
    <circle cx="0" cy="422" r="4" fill="#fff8d6"/><circle cx="14" cy="422" r="4" fill="#fff8d6"/>
  </g>
  <g class="car-west">
    <circle cx="0" cy="422" r="3.6" fill="#ff6b6b"/><circle cx="12" cy="422" r="3.6" fill="#ff6b6b"/>
    <circle cx="6" cy="422" r="10" fill="#ff6b6b" opacity=".22"/>
  </g>
</svg>`,
  },
  {
    id: "texas-star",
    caption: "Texas Star · Fair Park",
    svg: `
<svg viewBox="0 0 1200 520" preserveAspectRatio="xMidYMax slice" aria-hidden="true">
  <defs>
    <linearGradient id="s3-sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#0a0f2c"/><stop offset=".7" stop-color="#1c1c4e"/>
      <stop offset="1" stop-color="#3d2a63"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="520" fill="url(#s3-sky)"/>
  <g fill="#fff">
    <circle class="tw t1" cx="90" cy="80" r="2"/><circle class="tw t2" cx="240" cy="140" r="1.6"/>
    <circle class="tw t3" cx="380" cy="60" r="2"/><circle class="tw t4" cx="1020" cy="70" r="2"/>
    <circle class="tw t1" cx="1120" cy="160" r="1.6"/><circle class="tw t2" cx="900" cy="45" r="1.7"/>
    <circle class="tw t3" cx="160" cy="220" r="1.5"/><circle class="tw t4" cx="1160" cy="250" r="1.5"/>
    <circle class="tw t2" cx="60" cy="300" r="1.5"/><circle class="tw t1" cx="490" cy="110" r="1.5"/>
  </g>
  <circle cx="150" cy="120" r="40" fill="#f6ecd4" opacity=".95"/>
  <circle cx="166" cy="108" r="36" fill="#0a0f2c"/>
  <!-- ground + fairground -->
  <rect y="470" width="1200" height="50" fill="#120d2b"/>
  <g fill="#1e1546">
    <polygon points="120,470 170,410 220,470"/><polygon points="205,470 245,425 285,470"/>
    <rect x="330" y="430" width="130" height="40" rx="6"/>
    <polygon points="950,470 1000,415 1050,470"/><rect x="1080" y="440" width="90" height="30" rx="5"/>
  </g>
  <g fill="#ffcf5c" class="w-flicker">
    <circle cx="170" cy="452" r="3"/><circle cx="395" cy="450" r="3"/><circle cx="1000" cy="452" r="3"/>
  </g>
  <!-- wheel -->
  <g stroke="#100a26" stroke-width="10" stroke-linecap="round">
    <path d="M600 262 L520 470 M600 262 L680 470" fill="none"/>
  </g>
  <g class="wheel">
    <circle cx="600" cy="262" r="172" fill="none" stroke="#ffd166" stroke-width="5"/>
    <circle cx="600" cy="262" r="150" fill="none" stroke="#ffd166" stroke-width="2" opacity=".6"/>
    <path d="M600 262 L772 262 M600 262 L749 348 M600 262 L686 411 M600 262 L600 434
             M600 262 L514 411 M600 262 L451 348 M600 262 L428 262 M600 262 L451 176
             M600 262 L514 113 M600 262 L600 90 M600 262 L686 113 M600 262 L749 176"
          stroke="#e8b84a" stroke-width="2.4" opacity=".85" fill="none"/>
    <g>
      <circle cx="772" cy="262" r="9" fill="#ff5d8f"/><circle cx="749" cy="348" r="9" fill="#4f8cff"/>
      <circle cx="686" cy="411" r="9" fill="#22d3a6"/><circle cx="600" cy="434" r="9" fill="#ffcf5c"/>
      <circle cx="514" cy="411" r="9" fill="#ff5d8f"/><circle cx="451" cy="348" r="9" fill="#4f8cff"/>
      <circle cx="428" cy="262" r="9" fill="#22d3a6"/><circle cx="451" cy="176" r="9" fill="#ffcf5c"/>
      <circle cx="514" cy="113" r="9" fill="#ff5d8f"/><circle cx="600" cy="90" r="9" fill="#4f8cff"/>
      <circle cx="686" cy="113" r="9" fill="#22d3a6"/><circle cx="749" cy="176" r="9" fill="#ffcf5c"/>
    </g>
  </g>
  <circle cx="600" cy="262" r="14" fill="#ffd166"/>
  <!-- star topper -->
  <path class="neon-gold" d="M600 54 l7 15 16 2 -12 11 3 16 -14 -8 -14 8 3 -16 -12 -11 16 -2 z"
        fill="#ffcf5c"/>
</svg>`,
  },
  {
    id: "stockyards",
    caption: "The Stockyards · Fort Worth",
    svg: `
<svg viewBox="0 0 1200 520" preserveAspectRatio="xMidYMax slice" aria-hidden="true">
  <defs>
    <linearGradient id="s4-sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#5a2a52"/><stop offset=".5" stop-color="#b8506a"/>
      <stop offset=".8" stop-color="#e88d5a"/><stop offset="1" stop-color="#ffc978"/>
    </linearGradient>
    <radialGradient id="s4-sun" cx=".5" cy=".5" r=".5">
      <stop offset="0" stop-color="#fff4cf"/><stop offset="1" stop-color="#ffcf5c" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="1200" height="520" fill="url(#s4-sky)"/>
  <circle class="sun-glow" cx="260" cy="330" r="120" fill="url(#s4-sun)"/>
  <circle cx="260" cy="330" r="40" fill="#fff0b8"/>
  <g class="drift" fill="none" stroke="#4a2030" stroke-width="3" stroke-linecap="round">
    <path d="M840 130 q8 -9 16 0 M856 130 q8 -9 16 0"/>
    <path d="M910 165 q7 -8 14 0 M924 165 q7 -8 14 0" opacity=".7"/>
  </g>
  <!-- ground -->
  <rect y="440" width="1200" height="80" fill="#3a2013"/>
  <rect y="436" width="1200" height="6" fill="#54301c"/>
  <!-- sign -->
  <g>
    <rect x="330" y="120" width="24" height="320" fill="#4a2418"/>
    <rect x="846" y="120" width="24" height="320" fill="#4a2418"/>
    <rect x="316" y="112" width="52" height="16" rx="4" fill="#5c2e1e"/>
    <rect x="832" y="112" width="52" height="16" rx="4" fill="#5c2e1e"/>
    <rect x="330" y="150" width="540" height="96" rx="10" fill="#2c1707"/>
    <rect x="338" y="158" width="524" height="80" rx="7" fill="#43220f"/>
    <text x="600" y="196" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif"
          font-size="34" font-weight="bold" fill="#ffe9bd" letter-spacing="6">FORT WORTH</text>
    <text x="600" y="228" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif"
          font-size="22" font-weight="bold" fill="#e8b878" letter-spacing="10">STOCKYARDS</text>
    <g fill="#ffe08a">
      <circle class="tw t1" cx="352" cy="166" r="3"/><circle class="tw t2" cx="600" cy="163" r="3"/>
      <circle class="tw t3" cx="848" cy="166" r="3"/><circle class="tw t4" cx="352" cy="230" r="3"/>
      <circle class="tw t2" cx="848" cy="230" r="3"/><circle class="tw t1" cx="476" cy="163" r="3"/>
      <circle class="tw t3" cx="724" cy="163" r="3"/>
    </g>
  </g>
  <!-- windmill -->
  <g>
    <path d="M1020 440 L1054 300 M1088 440 L1054 300 M1035 400 h38 M1043 360 h22"
          stroke="#3a2013" stroke-width="7" fill="none" stroke-linecap="round"/>
    <g class="rotor">
      <path d="M1054 300 l0 -46 M1054 300 l33 -33 M1054 300 l46 0 M1054 300 l33 33
               M1054 300 l0 46 M1054 300 l-33 33 M1054 300 l-46 0 M1054 300 l-33 -33"
            stroke="#4a2a16" stroke-width="5" stroke-linecap="round" fill="none"/>
      <path d="M1054 262 l-7 12 14 0 z M1080 273 l-13 3 10 10 z M1092 300 l-12 -7 0 14 z
               M1080 327 l-3 -13 -10 10 z M1054 338 l7 -12 -14 0 z M1028 327 l13 -3 -10 -10 z
               M1016 300 l12 7 0 -14 z M1028 273 l3 13 10 -10 z" fill="#4a2a16"/>
    </g>
    <circle cx="1054" cy="300" r="7" fill="#2c1707"/>
  </g>
  <!-- longhorn -->
  <g fill="#2c1707">
    <path d="M520 372 q-14 -26 8 -30 q4 -18 -12 -30 q-30 -6 -52 4 q-40 -22 -74 -6
             q-14 -18 -34 -14 q6 12 2 22 q-34 4 -34 34 q0 26 24 32 l-4 56 14 0 6 -44
             q22 8 46 6 l4 38 14 0 2 -40 q20 -4 30 -18 q10 10 24 8 q14 -2 22 -18 z"/>
    <path d="M404 300 q-42 -34 -96 -22 q-26 6 -38 24 q20 -6 34 -2 q-30 10 -38 34
             q24 -16 44 -16 q-4 -12 8 -22 q40 -14 78 8 z" opacity=".95"/>
    <path d="M416 300 q42 -34 96 -22 q26 6 38 24 q-20 -6 -34 -2 q30 10 38 34
             q-24 -16 -44 -16 q4 -12 -8 -22 q-40 -14 -78 8 z" opacity=".95"/>
  </g>
  <g fill="#54301c" opacity=".7">
    <ellipse cx="700" cy="460" rx="26" ry="4"/><ellipse cx="180" cy="468" rx="34" ry="4"/>
    <ellipse cx="940" cy="465" rx="22" ry="4"/>
  </g>
</svg>`,
  },
];

/* ---- rotation engine ---------------------------------------------------- */
(function () {
  const ROTATE_MS = 14000;
  let current = 0;
  let timer = null;

  function reducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function build() {
    const stage = document.getElementById("sceneStage");
    const dotsBox = document.getElementById("sceneDots");
    if (!stage || !dotsBox) return;

    SCENES.forEach((s, i) => {
      const layer = document.createElement("div");
      layer.className = "scene-layer" + (i === 0 ? " on" : "");
      layer.dataset.idx = i;
      layer.innerHTML = s.svg;
      stage.appendChild(layer);

      const dot = document.createElement("button");
      dot.className = "scene-dot" + (i === 0 ? " on" : "");
      dot.setAttribute("aria-label", "Show scene: " + s.caption);
      dot.onclick = () => { show(i); restart(); };
      dotsBox.appendChild(dot);
    });
    setCaption(0);
  }

  function setCaption(i) {
    const cap = document.getElementById("sceneCaption");
    if (cap) {
      cap.classList.remove("swap");
      void cap.offsetWidth; // restart the caption animation
      cap.textContent = SCENES[i].caption;
      cap.classList.add("swap");
    }
  }

  function show(i) {
    current = i;
    document.querySelectorAll(".scene-layer").forEach((l) =>
      l.classList.toggle("on", Number(l.dataset.idx) === i));
    document.querySelectorAll(".scene-dot").forEach((d, k) =>
      d.classList.toggle("on", k === i));
    setCaption(i);
  }

  function next() { show((current + 1) % SCENES.length); }

  function restart() {
    if (timer) clearInterval(timer);
    if (reducedMotion()) return;
    timer = setInterval(next, ROTATE_MS);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { if (timer) clearInterval(timer); timer = null; }
    else restart();
  });

  document.addEventListener("DOMContentLoaded", () => { build(); restart(); });
})();
