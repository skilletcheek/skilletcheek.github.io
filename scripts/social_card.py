"""Renders the daily media posted to Instagram and Facebook: a 1080x1350
JPEG card for Facebook, and for Instagram either a 9:16 Reel (render_reel(),
the default since 2026-09-10) or the 4:5 carousel slides that preceded it.
Which one is social_post.py's IG_FORMAT.

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

The Reels spec, enforced the same way by verify_reel():
    MP4 . H.264 high . yuv420p . AAC audio . 9:16 . 3s..15min . <= 1 GB
The reel is assembled by ffmpeg from frames this file draws — ffmpeg is NOT a
pip dependency and is not installed by the workflow; it is preinstalled on
ubuntu-latest runners, which is the only place this runs. _ffmpeg() says so
when it is missing rather than letting subprocess raise a bare
FileNotFoundError.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
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

# Instagram Reels are 9:16. The same slide renderers below draw both formats
# -- passing `size` is the whole difference -- so there is one definition of
# what a slide looks like rather than a 4:5 copy and a 9:16 copy that drift.
REEL_W, REEL_H = 1080, 1920

# Instagram's own chrome covers the bottom of a Reel (caption, handle, audio
# ticker) and the right edge (the like/comment/share rail). Reel frames inset
# their footer and their content zone by this much so nothing that has to be
# read sits under it. A 4:5 carousel slide has no such overlay and passes 0.
REEL_SAFE_BOTTOM = 340

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
    cx, ball_y, ball_rx, ball_ry, base = img.width - PAD - 60, PAD + 62, 54, 33, PAD + 210
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


# --------------------------------------------------------------- reels
# Instagram's Content Publishing API takes a video_url for media_type=REELS
# exactly the way it takes an image_url for a photo -- Meta's servers fetch
# it -- so the reel goes through the same commit-to-Pages-then-publish dance
# the carousel already needs, and nothing about that flow changes.
#
# THE FRAMES ARE THE CAROUSEL'S FRAMES, drawn at 9:16 instead of 4:5. Same
# two renderers, same layout rules, `size`/`scale`/`safe_bottom` are the
# whole difference -- a second set of reel-only drawing code is exactly the
# two-layer-mirror drift CLAUDE.md warns about, and this file already carries
# one such mirror (CATEGORY_COLOR) under protest.
REEL_SECONDS = 3.0        # per slide; 4 slides -> 10.8s, a normal reel length
REEL_XFADE = 0.4          # crossfade between slides
REEL_FPS = 30

# The reel frame is 42% taller than the carousel slide but exactly as wide,
# so type sized for 1080x1350 reads small in it. Content is drawn 1.3x while
# the chrome (masthead, footer, page counter) stays put -- the domain mark
# should not balloon just because the canvas grew.
REEL_SCALE = 1.3
# ...and at 1.3x a long name wraps past the carousel's 4-line cap, so the
# taller format gets a taller cap rather than ellipsizing what 4:5 prints in
# full. 6 lines is 793px of a 1110px content band at this scale.
REEL_NAME_LINES = 6


def _ffmpeg(name: str) -> str:
    """Locate ffmpeg/ffprobe, or say plainly that it is missing.

    Preinstalled on GitHub's ubuntu-latest runners, which is the only place
    this runs -- but a bare FileNotFoundError from subprocess names no cause,
    and this is the one dependency in the repo that is neither stdlib nor the
    workflow's single pip install.
    """
    found = shutil.which(name)
    if not found:
        raise RuntimeError(
            f"{name} not found on PATH. render_reel() needs ffmpeg; it ships "
            f"with ubuntu-latest runners, so on CI this means the image "
            f"changed. Locally: brew install ffmpeg.")
    return found


def reel_filtergraph(n: int) -> tuple[list[str], str, float]:
    """(filter steps, final label, total seconds) for `n` still frames.

    Split out of render_reel() because it is the part with arithmetic in it.
    xfade's `offset` is measured from the start of the WHOLE chain, not from
    the clip being faded in, so each transition starts one slide-minus-one-
    overlap later than the last -- an off-by-one here does not fail, it
    silently produces a reel that holds on slide 1 and then flickers.

    Every input is normalised before it reaches xfade: xfade rejects inputs
    that disagree on pixel format, frame rate or sample aspect, and "they all
    came from the same renderer" is an assumption, not a guarantee.
    """
    if n < 1:
        raise ValueError("a reel needs at least one frame")
    steps = [f"[{i}:v]format=yuv420p,fps={REEL_FPS},setsar=1[v{i}]"
             for i in range(n)]
    last, offset = "v0", 0.0
    for i in range(1, n):
        offset += REEL_SECONDS - REEL_XFADE
        steps.append(f"[{last}][v{i}]xfade=transition=fade:"
                     f"duration={REEL_XFADE}:offset={offset:.3f}[x{i}]")
        last = f"x{i}"
    return steps, last, n * REEL_SECONDS - (n - 1) * REEL_XFADE


def render_reel(frames: list[Path], out_path: Path) -> Path:
    """Assemble already-rendered 9:16 frames into an Instagram Reel.

    Hard cuts with a short crossfade over STATIC frames, deliberately: a
    pan/zoom over every frame would make each one differ from the last and
    multiply the bitrate, and these files are committed to a public repo
    forever (git keeps them long after prune_cards() deletes them from the
    tree). Static frames with brief transitions encode to roughly what the
    five JPEGs it replaces already cost.

    A silent AAC track is muxed in rather than shipping a video with no audio
    stream at all -- Meta's spec states audio requirements without stating
    that audio is optional, and a missing stream is the kind of thing that
    surfaces as an opaque "media upload failed" with no field naming it.
    """
    if not frames:
        raise ValueError("render_reel() needs at least one frame")
    steps, last, total = reel_filtergraph(len(frames))

    cmd = [_ffmpeg("ffmpeg"), "-y", "-loglevel", "error"]
    for f in frames:
        cmd += ["-loop", "1", "-t", f"{REEL_SECONDS}", "-i", str(f)]
    cmd += ["-f", "lavfi", "-t", f"{total}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

    cmd += ["-filter_complex", ";".join(steps),
            "-map", f"[{last}]", "-map", f"{len(frames)}:a",
            "-c:v", "libx264", "-profile:v", "high", "-preset", "veryfast",
            "-crf", "23", "-pix_fmt", "yuv420p", "-r", str(REEL_FPS),
            "-g", str(REEL_FPS * 2), "-c:a", "aac", "-b:a", "96k",
            "-shortest", "-movflags", "+faststart", str(out_path)]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): "
                           f"{proc.stderr.strip()[-1200:]}")
    return out_path


def verify_reel(path: Path) -> None:
    """The verify_card() of video: assert Meta's Reels spec locally.

    Same reasoning, and more of it -- a video container has more ways to be
    quietly wrong than a JPEG, and every one of them comes back from Meta as
    the same generic media error.
    """
    proc = subprocess.run(
        [_ffmpeg("ffprobe"), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise ValueError(f"ffprobe could not read {path.name}: {proc.stderr.strip()}")
    info = json.loads(proc.stdout)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    size = path.stat().st_size
    problems = []
    if video is None:
        problems.append("no video stream")
    if audio is None:
        problems.append("no audio stream; Instagram expects an AAC track")
    elif audio.get("codec_name") != "aac":
        problems.append(f"audio is {audio.get('codec_name')}; must be aac")
    if size > 1024 ** 3:
        problems.append(f"{size} bytes exceeds the 1 GB limit")

    w = h = 0
    if video is not None:
        w, h = int(video.get("width", 0)), int(video.get("height", 0))
        if video.get("codec_name") != "h264":
            problems.append(f"video is {video.get('codec_name')}; must be h264")
        if video.get("pix_fmt") != "yuv420p":
            problems.append(f"pix_fmt {video.get('pix_fmt')}; must be yuv420p")
        ratio = w / h if h else 0
        if abs(ratio - 9 / 16) > 0.01:
            problems.append(f"aspect {ratio:.3f} is not 9:16 (0.563)")
    dur = float(info.get("format", {}).get("duration") or 0)
    if not 3.0 <= dur <= 900.0:
        problems.append(f"duration {dur:.2f}s is outside Instagram's 3s..15min")
    if problems:
        raise ValueError(f"{path.name} violates the Instagram Reels spec: "
                         + "; ".join(problems))
    print(f"  reel ok: {w}x{h} h264 {dur:.1f}s {size / 1024:.0f} KB")


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


def _footer(d, cache_dir, cta: str | None, w: int = W, h: int = H,
            safe_bottom: int = 0):
    """Domain mark on every slide -- a screenshot of any ONE slide, out of
    context, should still say where it came from, the same reasoning
    render_card() carries for its single image. `cta` renders a second line
    above it for the cover and final slides only; None leaves plain slides
    uncluttered.

    `safe_bottom` lifts the whole footer clear of a Reel's caption overlay;
    it is 0 for the 4:5 carousel, which has nothing drawn over it.
    """
    mono = lambda s: _load("JetBrains+Mono:wght@500", s, cache_dir)
    fy = h - safe_bottom - PAD - 58
    d.line([(PAD, fy), (w - PAD, fy)], fill=LINE, width=2)
    _tracked(d, (PAD, fy + 22), "LETSDOITDALLAS.COM", mono(26), GOLD, 3.8)
    if cta:
        tfont = mono(22)
        _tracked(d, (w - PAD - _tracked_width(cta, tfont, 3.0), fy + 26),
                 cta, tfont, DIM, 3.0)


def render_cover_slide(headline: str, datestamp: str, pick_count: int,
                       out_path: Path, cache_dir: Path,
                       size: tuple[int, int] = (W, H),
                       safe_bottom: int = 0, swipe: bool = True,
                       scale: float = 1.0) -> Path:
    """Slide 1 of the carousel: title card + a swipe cue.

    The cue exists because Instagram's carousel dots are small and easy to
    miss -- a viewer who doesn't know there's more to see never swipes, and
    the reach advantage of posting a carousel at all depends on them doing
    so. Naming the pick count ("3 PICKS") gives a concrete reason to swipe
    rather than a vague "see more."
    """
    w, h = size
    img = Image.new("RGBA", size, (*BG, 255))
    _tower(img)
    d = ImageDraw.Draw(img)
    disp = lambda s: _load("Syne:wght@800", s, cache_dir)
    mono = lambda s: _load("JetBrains+Mono:wght@500", s, cache_dir)

    _masthead(d, cache_dir)

    px = lambda v: int(round(v * scale))
    y = PAD + 92
    hfont = disp(px(96))
    lines = _wrap(headline.upper(), hfont, w - PAD * 2, 2)
    for line in lines:
        d.text((PAD, y), line, font=hfont, fill=WHITE)
        y += px(104)
    y += px(104) * (2 - len(lines))
    y += 6
    _tracked(d, (PAD, y), datestamp.upper(), mono(px(25)), SILVER, 4.0)

    # A Reel is watched, not swiped -- the same words that tell a carousel
    # viewer what to do would be an instruction a Reel viewer cannot follow.
    cue = f"{pick_count} PICKS — SWIPE →" if swipe else f"{pick_count} PICKS"
    cfont = mono(px(30))
    cy = (y + 60 + (h - safe_bottom - PAD - 148)) // 2
    _tracked(d, ((w - _tracked_width(cue, cfont, 3.0)) / 2, cy), cue, cfont,
             GOLD, 3.0)

    _footer(d, cache_dir, None, w, h, safe_bottom)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "JPEG", quality=88,
                            optimize=True, progressive=False)
    return out_path


def render_pick_slide(index: int, total: int, name: str, meta: str,
                      tag: str, cat_slug: str, is_last: bool,
                      out_path: Path, cache_dir: Path,
                      size: tuple[int, int] = (W, H),
                      safe_bottom: int = 0, swipe: bool = True,
                      scale: float = 1.0, max_name_lines: int = 4) -> Path:
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
    w, h = size
    img = Image.new("RGBA", size, (*BG, 255))
    d = ImageDraw.Draw(img)
    disp = lambda s: _load("Syne:wght@800", s, cache_dir)
    mono = lambda s: _load("JetBrains+Mono:wght@500", s, cache_dir)

    _masthead(d, cache_dir)
    page = f"{index:02d} / {total:02d}"
    pfont = mono(23)
    _tracked(d, (w - PAD - _tracked_width(page, pfont, 2.0), PAD + 4),
             page, pfont, SILVER, 2.0)

    px = lambda v: int(round(v * scale))
    color = CATEGORY_COLOR.get(cat_slug, EM)
    tfont = mono(px(26))
    nfont = disp(px(74))
    mfont = mono(px(28))
    # 4 is right for the 1350-tall carousel slide. A 1920-tall reel frame has
    # the room for more, and it needs it: at the reel's larger `scale` the
    # same name wraps to more lines, so keeping 4 here would ELLIPSIZE names
    # ("Half Foot Hog, Asshats, Kiss With...") that the carousel prints whole
    # -- the bigger format losing information to the smaller one.
    name_lines = _wrap(name, nfont, w - PAD * 2, max_name_lines)

    TAG_H, LINE_H, GAP, META_H = px(56), px(82), px(20), px(40)
    block_h = (TAG_H if tag else 0) + LINE_H * len(name_lines) + GAP + META_H
    zone_top, zone_bottom = PAD + 170, h - safe_bottom - PAD - 148
    y = zone_top + max(0, (zone_bottom - zone_top - block_h)) // 2

    if tag:
        _tracked(d, (PAD, y), f"[ {tag.upper()} ]", tfont, color, 2.6)
        y += TAG_H
    for line in name_lines:
        d.text((PAD, y), line, font=nfont, fill=WHITE)
        y += LINE_H
    y += GAP
    _tracked(d, (PAD, y), meta.upper(), mfont, DIM, 2.2)

    cta = "FULL LIST →" if is_last else ("KEEP SWIPING →" if swipe else "")
    _footer(d, cache_dir, cta or None, w, h, safe_bottom)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "JPEG", quality=88,
                            optimize=True, progressive=False)
    return out_path
