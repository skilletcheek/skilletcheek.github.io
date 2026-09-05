"""Renders the daily 1080x1350 JPEG posted to Instagram and Facebook.

WHY THIS EXISTS AT ALL. 647 of 667 rows in live-events.json carry an `image`,
so posting the promoter's own artwork looked like the cheap path. It was
measured on 2026-08-28 and rejected:

  * 105 of them are seatgeekimages.com thumbnails at 280x210 — below
    Instagram's 320px minimum width, so IG upscales a postage stamp.
  * assets.simpleviewinc.com and image.seated.com serve PNG. The Content
    Publishing API takes JPEG and nothing else, so those containers error.
  * The 485 Ticketmaster JPEGs are fine technically (1136x639, 1.778, inside
    the 4:5..1.91:1 window) but they are the promoter's copyrighted key art.
    Reposting it under our own handle, daily, forever, is not a fight worth
    picking for an events aggregator.

So the card is ours: one consistent 4:5 frame in the site's own palette that
works no matter what the feed hands us, and that carries the domain in the
image itself — where it survives a screenshot and a repost.

Pillow is the ONE pip dependency in this repo (installed by
.github/workflows/social-post.yml). scripts/fetch_events.py stays stdlib-only;
that constraint is about the nightly aggregator, which has no install step.

Instagram image spec this file is built to satisfy — all of it verified against
the Meta docs on 2026-08-28, and all of it enforced by verify_card():
    JPEG only . <= 8 MB . aspect 4:5 to 1.91:1 . width 320..1440 . sRGB
1080x1350 is exactly 4:5, the tallest frame IG allows and so the most feed
real estate per post.
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- palette
# Lifted from css/styles.css :root. If the site is restyled, restyle here too —
# the card is the site's face on someone else's feed.
BG      = (8, 9, 11)         # --bg      #08090B
LINE    = (25, 28, 34)       # --line    #191C22
EM      = (0, 255, 135)      # --em      #00FF87
WHITE   = (255, 255, 255)    # --white
SILVER  = (138, 144, 158)    # --silver  #8A909E
DIM     = (118, 125, 140)    # --dim     #767D8C
GOLD    = (255, 207, 92)     # --gold    #FFCF5C

W, H = 1080, 1350
PAD = 76

# Mirrors js/data.js CATEGORIES. Two-layer mirror like _split_area()/
# splitArea() elsewhere in this repo -- change one, change both, or a pick's
# card color stops matching the color it has everywhere else on the site.
CATEGORY_COLOR = {
    "music":     (255, 93, 143),   # #ff5d8f
    "food":      (255, 176, 32),   # #ffb020
    "arts":      (167, 139, 250),  # #a78bfa
    "outdoors":  (34, 211, 166),   # #22d3a6
    "sports":    (79, 140, 255),   # #4f8cff
    "family":    (56, 189, 248),   # #38bdf8
    "market":    (249, 115, 22),   # #f97316
    "nightlife": (192, 132, 252),  # #c084fc
    "festival":  (244, 63, 94),    # #f43f5e
}

# ------------------------------------------------------------------ fonts
# Google Fonts is already the site's only external dependency, so pulling the
# same two families here keeps the card typographically identical to the page.
# The css2 endpoint hands back .woff2 to a modern User-Agent and .ttf to an
# ancient one, and Pillow reads TTF — hence the deliberately fossil UA.
#
# Never fatal: a runner with no egress to fonts.gstatic.com still gets a card,
# just in DejaVu. A missing font is not a reason to skip the day's post.
_GF_CSS = "https://fonts.googleapis.com/css2?family={family}"
_FALLBACKS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",   # ubuntu-latest
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",      # this laptop
]
_cache: dict[str, Path] = {}


def _font_file(family: str, cache_dir: Path) -> Path | None:
    """Download one Google Fonts TTF, memoized on disk and in-process."""
    if family in _cache:
        return _cache[family]
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / (re.sub(r"[^A-Za-z0-9]+", "-", family).strip("-") + ".ttf")
    if not dest.exists():
        try:
            req = urllib.request.Request(
                _GF_CSS.format(family=family),
                headers={"User-Agent": "Mozilla/4.0"})
            css = urllib.request.urlopen(req, timeout=20).read().decode()
            url = re.search(r"url\((https://[^)]+\.ttf)\)", css).group(1)
            dest.write_bytes(urllib.request.urlopen(url, timeout=20).read())
        except Exception as exc:                       # noqa: BLE001 - see docstring
            print(f"  font: {family} unavailable ({exc}); falling back")
            return None
    _cache[family] = dest
    return dest


def _load(family: str, size: int, cache_dir: Path) -> ImageFont.FreeTypeFont:
    path = _font_file(family, cache_dir)
    for candidate in ([path] if path else []) + _FALLBACKS:
        try:
            return ImageFont.truetype(str(candidate), size)
        except (OSError, TypeError):
            continue
    return ImageFont.load_default(size)


# --------------------------------------------------------------- drawing
def _tracked(draw, xy, text, font, fill, tracking=0.0):
    """Draw text with letter-spacing, which Pillow has no concept of.

    The site's mono kickers all carry .08em-.22em tracking; without it the
    card reads as a different brand than the page it links to.
    """
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + tracking
    return x


def _tracked_width(text, font, tracking=0.0) -> float:
    return sum(font.getlength(c) + tracking for c in text) - (tracking if text else 0)


def _wrap(text: str, font, max_w: float, max_lines: int) -> list[str]:
    """Greedy pixel-measured wrap. The last line is ellipsized rather than
    dropped — a truncated headline still names the event, a missing one
    doesn't."""
    words, lines, cur = text.split(), [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if font.getlength(trial) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
            if len(lines) == max_lines:
                break
    else:
        if cur:
            lines.append(cur)
    if len(lines) > max_lines or (len(lines) == max_lines and cur not in lines):
        lines = lines[:max_lines]
        tail = lines[-1]
        while tail and font.getlength(tail + "…") > max_w:
            tail = tail[:-1].rstrip()
        lines[-1] = tail + "…"
    return lines[:max_lines]


def _tower(img: Image.Image):
    """A small Reunion Tower emblem, the landmark js/tower.js draws on the
    homepage.

    It lives in the top-right corner and NOT floating behind the listings: the
    first draft put it mid-frame as a watermark and the ball landed on top of
    pick 02's title, because the three rows flow to whatever height the names
    wrap to and there is no reliably empty band below the masthead. The corner
    is the one region whose occupancy this file controls.
    """
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, ball_y, ball_rx, ball_ry, base = W - PAD - 60, PAD + 62, 54, 33, PAD + 210
    glow = (*EM, 52)
    for dx in (-9, 9):                                       # the shaft
        d.line([(cx + dx, ball_y), (cx + dx * 1.6, base)], fill=glow, width=2)
    for i in range(1, 4):                                    # the geodesic ball
        ry = ball_ry * (1 - i * 0.3)
        d.ellipse([cx - ball_rx, ball_y - ry, cx + ball_rx, ball_y + ry],
                  outline=glow, width=1)
    d.ellipse([cx - ball_rx, ball_y - ball_ry, cx + ball_rx, ball_y + ball_ry],
              outline=(*EM, 90), width=2)
    for i in range(7):                                       # the light ring
        x = cx - ball_rx + (2 * ball_rx) * i / 6
        d.ellipse([x - 2, ball_y - 2, x + 2, ball_y + 2], fill=(*EM, 120))
    img.alpha_composite(layer)


def render_card(headline: str, datestamp: str, picks: list[dict],
                out_path: Path, cache_dir: Path) -> Path:
    """Render the card. `picks` are dicts of {name, meta, tag}, max 3.

    Three is a layout limit, not a taste one: a fourth row at a legible size
    pushes the footer off a 1350px frame.
    """
    img = Image.new("RGBA", (W, H), (*BG, 255))
    _tower(img)
    d = ImageDraw.Draw(img)

    mono   = lambda s: _load("JetBrains+Mono:wght@500", s, cache_dir)
    disp   = lambda s: _load("Syne:wght@800", s, cache_dir)
    disp7  = lambda s: _load("Syne:wght@700", s, cache_dir)

    # ---- masthead
    d.line([(PAD, PAD), (PAD + 92, PAD)], fill=EM, width=3)
    _tracked(d, (PAD, PAD + 22), "/ LETS DO IT DALLAS", mono(23), EM, 3.4)

    # ---- headline. Two lines max; "TONIGHT IN DALLAS-FORT WORTH" needs both,
    # but a short one-line headline ("DFW TODAY") still reserves the same
    # two-line height -- otherwise the listings start higher up and the card
    # reads as unbalanced next to a two-line headline's card, which matters
    # now that two different slot headlines both have to look right.
    y = PAD + 92
    hfont = disp(96)
    lines = _wrap(headline.upper(), hfont, W - PAD * 2, 2)
    for line in lines:
        d.text((PAD, y), line, font=hfont, fill=WHITE)
        y += 104
    y += 104 * (2 - len(lines))
    y += 6
    _tracked(d, (PAD, y), datestamp.upper(), mono(25), SILVER, 4.0)

    # ---- listings
    y += 74
    d.line([(PAD, y), (W - PAD, y)], fill=LINE, width=2)
    y += 46

    nfont, mfont, ifont, tfont = disp7(50), mono(24), mono(30), mono(20)
    for i, pick in enumerate(picks[:3], 1):
        _tracked(d, (PAD, y + 6), f"{i:02d}", ifont, EM, 2.0)
        text_x = PAD + 78
        for line in _wrap(pick["name"], nfont, W - text_x - PAD, 2):
            d.text((text_x, y), line, font=nfont, fill=WHITE)
            y += 58
        _tracked(d, (text_x, y + 8), pick["meta"].upper(), mfont, DIM, 2.2)
        y += 46
        if pick.get("tag"):
            label = f"[ {pick['tag'].upper()} ]"
            _tracked(d, (text_x, y + 6), label, tfont, EM, 2.6)
            y += 34
        y += 30
        if i < len(picks[:3]):
            d.line([(PAD, y - 16), (W - PAD, y - 16)], fill=LINE, width=1)

    # ---- footer, pinned to the bottom rather than flowed, so a short day and
    # a long day produce the same frame.
    fy = H - PAD - 58
    d.line([(PAD, fy), (W - PAD, fy)], fill=LINE, width=2)
    _tracked(d, (PAD, fy + 22), "LETSDOITDALLAS.COM", mono(26), GOLD, 3.8)
    tail = "/ FULL LIST"
    tfoot = mono(22)
    _tracked(d, (W - PAD - _tracked_width(tail, tfoot, 3.0), fy + 26),
             tail, tfoot, DIM, 3.0)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # RGB + quality 88: IG converts anything else to sRGB JPEG anyway, and
    # doing it here is what keeps verify_card() honest about what we shipped.
    img.convert("RGB").save(out_path, "JPEG", quality=88,
                            optimize=True, progressive=False)
    return out_path


def verify_card(path: Path) -> None:
    """Fail loudly here rather than as an opaque Meta error code later.

    A container that violates the media spec comes back as a generic
    'media upload failed' with no field naming the reason, so every constraint
    the docs state is asserted locally where the message can be useful.
    """
    size = path.stat().st_size
    with Image.open(path) as im:
        fmt, (w, h) = im.format, im.size
    ratio = w / h
    problems = []
    if fmt != "JPEG":
        problems.append(f"format is {fmt}; Instagram accepts JPEG only")
    if size > 8 * 1024 * 1024:
        problems.append(f"{size} bytes exceeds the 8 MB limit")
    if not 320 <= w <= 1440:
        problems.append(f"width {w} is outside Instagram's 320..1440")
    if not 0.8 - 1e-6 <= ratio <= 1.91 + 1e-6:
        problems.append(f"aspect {ratio:.3f} is outside 4:5 (0.800)..1.91")
    if problems:
        raise ValueError(f"{path.name} violates the Instagram media spec: "
                         + "; ".join(problems))
    print(f"  card ok: {w}x{h} {fmt} {size / 1024:.0f} KB ratio {ratio:.3f}")


# ------------------------------------------------------- carousel slides
# A single dense card is Instagram's weakest-performing native format --
# carousels (2-10 swipeable images under one post) reliably get more reach
# and saves. These two functions render one carousel: a cover slide plus one
# slide per pick, each slide the same 1080x1350 frame as render_card() so
# Instagram's own "every child is cropped to the first image's aspect ratio"
# behavior never bites. Facebook is untouched -- it keeps posting
# render_card()'s single dense image, since FB's caption already carries the
# full text and there's no algorithmic reward there for a format change.
def _masthead(d, cache_dir):
    mono = lambda s: _load("JetBrains+Mono:wght@500", s, cache_dir)
    d.line([(PAD, PAD), (PAD + 92, PAD)], fill=EM, width=3)
    _tracked(d, (PAD, PAD + 22), "/ LETS DO IT DALLAS", mono(23), EM, 3.4)


def _footer(d, cache_dir, cta: str | None):
    """Domain mark on every slide -- a screenshot of any ONE slide, out of
    context, should still say where it came from, the same reasoning
    render_card() carries for its single image. `cta` renders a second line
    above it for the cover and final slides only; None leaves plain slides
    uncluttered.
    """
    mono = lambda s: _load("JetBrains+Mono:wght@500", s, cache_dir)
    fy = H - PAD - 58
    d.line([(PAD, fy), (W - PAD, fy)], fill=LINE, width=2)
    _tracked(d, (PAD, fy + 22), "LETSDOITDALLAS.COM", mono(26), GOLD, 3.8)
    if cta:
        tfont = mono(22)
        _tracked(d, (W - PAD - _tracked_width(cta, tfont, 3.0), fy + 26),
                 cta, tfont, DIM, 3.0)


def render_cover_slide(headline: str, datestamp: str, pick_count: int,
                       out_path: Path, cache_dir: Path) -> Path:
    """Slide 1 of the carousel: title card + a swipe cue.

    The cue exists because Instagram's carousel dots are small and easy to
    miss -- a viewer who doesn't know there's more to see never swipes, and
    the reach advantage of posting a carousel at all depends on them doing
    so. Naming the pick count ("3 PICKS") gives a concrete reason to swipe
    rather than a vague "see more."
    """
    img = Image.new("RGBA", (W, H), (*BG, 255))
    _tower(img)
    d = ImageDraw.Draw(img)
    disp = lambda s: _load("Syne:wght@800", s, cache_dir)
    mono = lambda s: _load("JetBrains+Mono:wght@500", s, cache_dir)

    _masthead(d, cache_dir)

    y = PAD + 92
    hfont = disp(96)
    lines = _wrap(headline.upper(), hfont, W - PAD * 2, 2)
    for line in lines:
        d.text((PAD, y), line, font=hfont, fill=WHITE)
        y += 104
    y += 104 * (2 - len(lines))
    y += 6
    _tracked(d, (PAD, y), datestamp.upper(), mono(25), SILVER, 4.0)

    cue = f"{pick_count} PICKS — SWIPE →"
    cfont = mono(30)
    cy = (y + 60 + (H - PAD - 148)) // 2
    _tracked(d, ((W - _tracked_width(cue, cfont, 3.0)) / 2, cy), cue, cfont,
             GOLD, 3.0)

    _footer(d, cache_dir, None)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "JPEG", quality=88,
                            optimize=True, progressive=False)
    return out_path


def render_pick_slide(index: int, total: int, name: str, meta: str,
                      tag: str, cat_slug: str, is_last: bool,
                      out_path: Path, cache_dir: Path) -> Path:
    """One event per slide, page `index` of `total` (both 1-based).

    Everything here is bigger than render_card()'s equivalent row, because a
    single-pick slide has the room a 3-pick card never does. The category
    label renders in CATEGORY_COLOR instead of always the brand green --
    color that changes slide to slide is itself a reason to keep swiping,
    and it is real information (what kind of event this is), not decoration.

    The content block is vertically centered in the middle band rather than
    pinned under the masthead: a one-line name ("Mo Amer") and a four-line
    one ("Half Foot Hog, Asshats, Kiss With Your Teeth") sit in the same
    frame, and top-anchoring left the short-name slides with roughly half
    the frame empty while a long name filled it -- inconsistent density
    between slides of the same carousel read as unfinished, not intentional.
    """
    img = Image.new("RGBA", (W, H), (*BG, 255))
    d = ImageDraw.Draw(img)
    disp = lambda s: _load("Syne:wght@800", s, cache_dir)
    mono = lambda s: _load("JetBrains+Mono:wght@500", s, cache_dir)

    _masthead(d, cache_dir)
    page = f"{index:02d} / {total:02d}"
    pfont = mono(23)
    _tracked(d, (W - PAD - _tracked_width(page, pfont, 2.0), PAD + 4),
             page, pfont, SILVER, 2.0)

    color = CATEGORY_COLOR.get(cat_slug, EM)
    tfont = mono(26)
    nfont = disp(74)
    mfont = mono(28)
    name_lines = _wrap(name, nfont, W - PAD * 2, 4)

    TAG_H, LINE_H, GAP, META_H = 56, 82, 20, 40
    block_h = (TAG_H if tag else 0) + LINE_H * len(name_lines) + GAP + META_H
    zone_top, zone_bottom = PAD + 170, H - PAD - 148
    y = zone_top + max(0, (zone_bottom - zone_top - block_h)) // 2

    if tag:
        _tracked(d, (PAD, y), f"[ {tag.upper()} ]", tfont, color, 2.6)
        y += TAG_H
    for line in name_lines:
        d.text((PAD, y), line, font=nfont, fill=WHITE)
        y += LINE_H
    y += GAP
    _tracked(d, (PAD, y), meta.upper(), mfont, DIM, 2.2)

    cta = "FULL LIST →" if is_last else "KEEP SWIPING →"
    _footer(d, cache_dir, cta)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "JPEG", quality=88,
                            optimize=True, progressive=False)
    return out_path
