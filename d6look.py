"""
d6look - how every key on the D6 looks.

Each key is a tiny 85 x 85 pixel screen. This file turns a key description (see SPEC.md)
into a picture. It never talks to the device: everything here is "inputs in, picture out",
so it can be tested by saving PNG files and looking at them.

How it is organised (top to bottom):
    colours and fonts      small helpers
    themes                 the four looks: what the tile, icons and text look like
    icons                  built-in icons (Segoe Fluent Icons font) and emoji
    Canvas                 one key being painted: icon, text, ring gauge, bar ...
    widgets                the live keys (clock, volume, Spotify ...)
    public API             the functions the rest of the app uses (listed in SPEC.md)
"""
import colorsys
import hashlib
import io
import math
import sys
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

BASE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
COLS, ROWS, NKEYS = 5, 3, 15
LANCZOS = Image.Resampling.LANCZOS
BICUBIC = Image.Resampling.BICUBIC

WHITE = (255, 255, 255)
GREY = (170, 178, 186)
BLACK = (0, 0, 0)
SPOTIFY_GREEN = (30, 215, 96)
RED = (255, 96, 96)
AMBER = (255, 190, 64)
GREEN = (70, 222, 132)

# The looks, in menu order. "neon" is the default.
THEMES = {"neon": "Neon", "vivid": "Vivid", "mono": "Mono", "classic": "Classic"}

# Nice key colours for the colour picker (the themes brighten or darken them as needed).
COLORS = ["#1f6feb", "#1b9aaa", "#0f8a6f", "#238636", "#7a9a00", "#b08800", "#c9660a",
          "#b5382e", "#a3214a", "#b0309a", "#6e40c9", "#4752c4", "#2d3a8c", "#3d4451",
          "#57606a", "#1c2430"]


def log(msg):
    """The app swaps this for its own logger (look.log = log)."""
    print(msg)


# ----------------------------------------------------------------- colours
def hex_rgb(h):
    """'#rrggbb' -> (r, g, b). A broken colour becomes dark grey instead of an error."""
    try:
        h = str(h).lstrip("#")
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (51, 51, 51)


def _shade(rgb, f):
    return tuple(max(0, min(255, int(c * f))) for c in rgb)


def _mix(a, b, t):
    """Colour between a and b (t = 0 gives a, t = 1 gives b)."""
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _hsv(rgb):
    return colorsys.rgb_to_hsv(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)


def _from_hsv(h, s, v):
    return tuple(int(round(c * 255)) for c in colorsys.hsv_to_rgb(h % 1.0, s, v))


def _accent(rgb):
    """The bright, glowing version of a key colour (key colours are usually dark)."""
    h, s, v = _hsv(rgb)
    if s < 0.12:                       # greys stay a cool white instead of turning blue
        return (206, 214, 228)
    return _from_hsv(h, min(1.0, max(s, min(0.62, s * 2.2))), 1.0)


def level_color(pct):
    """Meter colour for a 0-100 load: green, turning amber, then red."""
    pct = max(0, min(100, pct or 0))
    if pct <= 50:
        return GREEN
    if pct <= 75:
        return _mix(GREEN, AMBER, (pct - 50) / 25)
    if pct <= 92:
        return _mix(AMBER, RED, (pct - 75) / 17)
    return RED


# ----------------------------------------------------------------- fonts
# kind -> (font file, variation name or None). Bahnschrift is one file with many widths.
_FONT_FILES = {
    "label": ("segoeuib.ttf", None),
    "label_semi": ("seguisb.ttf", None),
    "narrow": ("bahnschrift.ttf", "SemiBold SemiCondensed"),
    "number": ("bahnschrift.ttf", "Bold"),
    "number_narrow": ("bahnschrift.ttf", "Bold SemiCondensed"),
    "number_tight": ("bahnschrift.ttf", "Bold Condensed"),
    "classic": ("arialbd.ttf", None),
}
_FALLBACK_FONTS = ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf")
_font_cache = {}


def font(size, kind="label"):
    """A font of this pixel size. kind: "label" (Segoe UI Bold) or "number" (Bahnschrift Bold);
    also "narrow", "number_narrow", "number_tight", "label_semi", "classic". Falls back to Arial."""
    size = max(6, int(round(size)))
    key = (size, kind)
    if key not in _font_cache:
        _font_cache[key] = _load_font(size, kind)
    return _font_cache[key]


def _load_font(size, kind):
    name, variation = _FONT_FILES.get(kind, _FONT_FILES["label"])
    try:
        f = ImageFont.truetype(name, size)
        if variation:
            f.set_variation_by_name(variation)
        return f
    except Exception:
        pass
    for name in _FALLBACK_FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _width(text, fnt):
    return fnt.getlength(text)


def _cap_height(fnt):
    """Height of a capital letter / digit - used to centre text by what the eye sees."""
    return -fnt.getbbox("H", anchor="ls")[1]


def _fit(text, kinds, size, maxw, min_size=8):
    """Biggest font that fits `text` into maxw pixels. `kinds` are tried in order at full
    size (wide first, then narrower cuts) before the text is made smaller."""
    if isinstance(kinds, str):
        kinds = (kinds,)
    size = int(round(size))
    for kind in kinds:
        f = font(size, kind)
        if _width(text, f) <= maxw:
            return f
    kind = kinds[-1]
    while size > min_size:
        size -= 1
        f = font(size, kind)
        if _width(text, f) <= maxw:
            return f
    return font(min_size, kind)


def _clip(text, fnt, maxw):
    """Text cut down with "..." so it can never run off the key."""
    if _width(text, fnt) <= maxw:
        return text
    while len(text) > 1 and _width(text + "…", fnt) > maxw:
        text = text[:-1]
    return text.rstrip() + "…"


def _wrap(text, fnt, maxw, maxlines):
    """Split text into lines that fit. Returns (lines, cut) - cut is True if text was lost."""
    lines, cur, cut = [], "", False
    for word in str(text).split():
        trial = (cur + " " + word).strip()
        if _width(trial, fnt) <= maxw:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = word
        if _width(cur, fnt) > maxw:          # one very long word
            cur, cut = _clip(cur, fnt, maxw), True
    if cur:
        lines.append(cur)
    if len(lines) > maxlines:
        lines, cut = lines[:maxlines], True
        lines[-1] = _clip(lines[-1] + "…", fnt, maxw) if not lines[-1].endswith("…") else lines[-1]
    return lines, cut


def fmt_time(sec):
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ----------------------------------------------------------------- tile shapes and gradients
def _theme_of(cfg):
    t = cfg.get("theme")
    return t if t in THEMES else "neon"


def _corner(s, theme):
    """(radius, inset) of the rounded key shape, in pixels."""
    u = s / 85.0
    return (10 * u, 2 * u) if theme == "classic" else (14 * u, 1 * u)


@lru_cache(maxsize=64)
def _mask_cached(s, radius, inset):
    k = 4
    m = Image.new("L", (s * k, s * k), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        [inset * k, inset * k, s * k - inset * k - 1, s * k - inset * k - 1],
        radius=radius * k, fill=255)
    return m.resize((s, s), LANCZOS)


def round_mask(s, inset=2, radius=10):
    """White rounded square on black: the shape of a key."""
    return _mask_cached(s, radius, inset)


def _theme_mask(s, theme):
    radius, inset = _corner(s, theme)
    return _mask_cached(s, radius, inset)


@lru_cache(maxsize=64)
def _edge_mask(s, radius, inset, width, blur=0.0):
    """The outline of the key shape (drawn 4x bigger and shrunk, for smooth edges)."""
    k = 4
    m = Image.new("L", (s * k, s * k), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        [inset * k, inset * k, s * k - inset * k - 1, s * k - inset * k - 1],
        radius=radius * k, outline=255, width=max(1, int(round(width * k))))
    if blur:
        m = m.filter(ImageFilter.GaussianBlur(blur * k))
    return m.resize((s, s), LANCZOS)


def _vertical(s):
    """Greyscale ramp: black at the top, white at the bottom."""
    return Image.linear_gradient("L").resize((s, s), BICUBIC)


def _diagonal(s):
    """Greyscale ramp: black top-left, white bottom-right."""
    v = _vertical(s)
    return Image.blend(v, v.transpose(Image.Transpose.TRANSPOSE), 0.5)


def _two_tone(s, first, second, ramp):
    """Picture going from colour `first` (where the ramp is black) to `second` (white)."""
    return Image.composite(Image.new("RGB", (s, s), second), Image.new("RGB", (s, s), first), ramp)


def _scaled(mask, f):
    """A mask made weaker (f < 1) or stronger."""
    return mask.point(lambda v: min(255, int(v * f)))


def _spot(s, cx, cy, rx, ry, strength):
    """Soft round glow as a mask: brightest at (cx, cy), fading out at radius rx / ry."""
    spot = ImageOps.invert(Image.radial_gradient("L")).resize(
        (max(2, int(rx * 2)), max(2, int(ry * 2))), BICUBIC)
    spot = spot.point(lambda v: int(v * v / 255 * strength))
    m = Image.new("L", (s, s), 0)
    m.paste(spot, (int(cx - rx), int(cy - ry)))
    return m


def _gloss(s, strength):
    """Curved shine across the top half of the key, as a mask."""
    k = 2
    m = Image.new("L", (s * k, s * k), 0)
    ImageDraw.Draw(m).ellipse([-0.5 * s * k, -1.15 * s * k, 1.5 * s * k, 0.47 * s * k], fill=255)
    m = m.resize((s, s), LANCZOS)
    return _scaled(ImageChops.multiply(m, ImageOps.invert(_vertical(s))), strength)


def make_tile(s, color):
    """The classic key background: rounded, with a soft top-to-bottom gradient."""
    base = hex_rgb(color)
    grad = _two_tone(s, _shade(base, 1.3), _shade(base, 0.62), _vertical(s))
    return Image.composite(grad, Image.new("RGB", (s, s), BLACK), _theme_mask(s, "classic"))


def _bg_neon(s, rgb):
    """Near-black glass with the key colour glowing up from the bottom and a bright rim."""
    u = s / 85.0
    acc = _accent(rgb)
    radius, inset = _corner(s, "neon")
    img = _two_tone(s, (25, 28, 37), (7, 8, 11), _vertical(s))
    img.paste(Image.new("RGB", (s, s), acc), (0, 0), _spot(s, s * 0.5, s * 1.08, s * 0.95, s * 0.80, 0.60))
    img.paste(Image.new("RGB", (s, s), WHITE), (0, 0), _gloss(s, 0.11))
    rim = Image.new("RGB", (s, s), _mix(acc, WHITE, 0.22))
    img.paste(rim, (0, 0), _scaled(_edge_mask(s, radius, inset, 4.0 * u, 2.2 * u), 0.55))   # soft inner glow
    fade = ImageOps.invert(_scaled(_vertical(s), 0.42))                                      # rim is brightest on top
    img.paste(rim, (0, 0), ImageChops.multiply(_edge_mask(s, radius, inset, 1.6 * u), fade))
    return img


def _vivid_colors(rgb):
    """Top-left and bottom-right colours of the vivid gradient."""
    h, sat, v = _hsv(rgb)
    if sat < 0.12:
        return _from_hsv(h, sat, 0.50), _from_hsv(h, sat, 0.17)
    sat2 = min(1.0, sat * 1.08 + 0.06)
    top_v = max(0.58, min(0.93, v * 1.45 + 0.28))
    return _from_hsv(h - 0.035, sat2 * 0.88, top_v), _from_hsv(h + 0.045, sat2, top_v * 0.40)


def _bg_vivid(s, rgb):
    """Rich diagonal colour gradient with a soft shine on top."""
    u = s / 85.0
    radius, inset = _corner(s, "vivid")
    top, bottom = _vivid_colors(rgb)
    img = _two_tone(s, top, bottom, _diagonal(s))
    white = Image.new("RGB", (s, s), WHITE)
    img.paste(white, (0, 0), _gloss(s, 0.24))
    fade = ImageOps.invert(_vertical(s))
    img.paste(white, (0, 0), _scaled(ImageChops.multiply(_edge_mask(s, radius, inset, 1.3 * u), fade), 0.55))
    return img


def _bg_mono(s, rgb):
    """Black with a thin grey rim."""
    u = s / 85.0
    radius, inset = _corner(s, "mono")
    img = _two_tone(s, (17, 17, 19), (5, 5, 6), _vertical(s))
    img.paste(Image.new("RGB", (s, s), (104, 106, 114)), (0, 0), _edge_mask(s, radius, inset, 1.4 * u))
    return img


@lru_cache(maxsize=256)
def _background(s, theme, color):
    """Finished key background (cached - copy it before drawing on it)."""
    if theme == "classic":
        return make_tile(s, color)
    paint = {"neon": _bg_neon, "vivid": _bg_vivid, "mono": _bg_mono}[theme]
    return Image.composite(paint(s, hex_rgb(color)), Image.new("RGB", (s, s), BLACK),
                           _theme_mask(s, theme))


@lru_cache(maxsize=256)
def _ink(theme, color):
    """The colours and fonts used to draw on a key of this colour in this theme."""
    rgb = hex_rgb(color)
    acc = _accent(rgb)
    if theme == "neon":
        return SimpleNamespace(
            icon=_mix(acc, WHITE, 0.38), label=(236, 240, 248), big=(244, 247, 252),
            sub=(152, 162, 180), accent=_mix(acc, WHITE, 0.12), ring=_mix(acc, WHITE, 0.10),
            track=(255, 255, 255, 30), glow=acc, shadow=None, text_shadow=None,
            label_kind="narrow", num_kinds=("number", "number_narrow", "number_tight"),
            music=_mix(SPOTIFY_GREEN, WHITE, 0.15))
    if theme == "vivid":
        dark = _shade(_vivid_colors(rgb)[1], 0.45)
        return SimpleNamespace(
            icon=WHITE, label=WHITE, big=WHITE, sub=(232, 238, 246), accent=WHITE, ring=WHITE,
            track=(0, 0, 0, 88), glow=None, shadow=BLACK, text_shadow=dark,
            label_kind="label", num_kinds=("number", "number_narrow", "number_tight"),
            music=WHITE)
    if theme == "mono":
        return SimpleNamespace(
            icon=(246, 246, 250), label=(160, 163, 172), big=(246, 246, 250), sub=(128, 131, 140),
            accent=(200, 203, 212), ring=(240, 240, 246), track=(255, 255, 255, 38), glow=None,
            shadow=None, text_shadow=None,
            label_kind="narrow", num_kinds=("number", "number_narrow", "number_tight"),
            music=(200, 203, 212))
    return SimpleNamespace(   # classic
        icon=WHITE, label=WHITE, big=WHITE, sub=GREY, accent=WHITE, ring=WHITE,
        track=(0, 0, 0, 100), glow=None, shadow=None, text_shadow=None,
        label_kind="classic", num_kinds=("classic",), music=SPOTIFY_GREEN)


# ----------------------------------------------------------------- JPEG in and out
def finalize(img, cfg):
    """Apply the screen flip/rotation and encode as JPEG for the D6."""
    if cfg["image"].get("flip"):
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    img = img.rotate(cfg["image"].get("rotate", 90))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def upright(jpeg, cfg):
    """Undo finalize(): a JPEG made for the D6 -> a normal upright picture."""
    img = from_jpeg(jpeg).rotate(-cfg["image"].get("rotate", 90))
    if cfg["image"].get("flip"):
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    return img


def from_jpeg(data):
    return Image.open(io.BytesIO(data)).convert("RGB")


def to_jpeg(img):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=90)
    return buf.getvalue()


_black_cache = {}


def black_jpeg(cfg):
    s = cfg["image"]["size"]
    if s not in _black_cache:
        _black_cache[s] = to_jpeg(Image.new("RGB", (s, s), BLACK))
    return _black_cache[s]


# ----------------------------------------------------------------- animation frames
# Frames are made from the finished (already rotated) JPEG. Every effect is centred and
# symmetric, so the screen rotation/flip doesn't matter.
def press_frames(jpeg):
    """Button press: the key sinks in (smaller, brighter), then pops back with a glow ring."""
    img = from_jpeg(jpeg)
    s = img.width
    u = s / 85.0
    small = ImageEnhance.Brightness(img).enhance(1.35).resize((int(s * 0.84),) * 2, LANCZOS)
    sunk = Image.new("RGB", (s, s), BLACK)
    sunk.paste(small, ((s - small.width) // 2,) * 2)
    glow = ImageEnhance.Brightness(img).enhance(1.18)
    white = Image.new("RGB", (s, s), WHITE)
    glow.paste(white, (0, 0), _scaled(_edge_mask(s, 13 * u, 1.5 * u, 6 * u, 2.5 * u), 0.6))
    glow.paste(white, (0, 0), _scaled(_edge_mask(s, 13 * u, 1.5 * u, 2.6 * u), 0.9))
    return to_jpeg(sunk), to_jpeg(glow)


def icon_over(jpeg, icon, cfg):
    """The key's current picture, darkened, with a big icon on top (e.g. "prev" on the cover)."""
    img = ImageEnhance.Brightness(upright(jpeg, cfg)).enhance(0.38)
    s = img.width
    _stamp_icon(img, icon, int(s * 0.52), s / 2, s / 2, WHITE, shadow=BLACK)
    return finalize(img, cfg)


def blend_frames(old, new, steps=(0.35, 0.7)):
    """In-between pictures for a smooth crossfade from one JPEG to another."""
    a, b = from_jpeg(old), from_jpeg(new)
    if a.size != b.size:
        return []
    return [to_jpeg(Image.blend(a, b, t)) for t in steps]


# ----------------------------------------------------------------- icons
# Built-in icons: name -> (glyph in the Segoe Fluent Icons font, description).
# Two glyphs = drawn side by side.
_ICON_TABLE = {
    "play_pause": ((0xE768, 0xE769), "Play / pause"),
    "play": (0xE768, "Play"),
    "pause": (0xE769, "Pause"),
    "play_solid": (0xF5B0, "Play (filled)"),
    "pause_solid": (0xF8AE, "Pause (filled)"),
    "next": (0xE893, "Next song"),
    "prev": (0xE892, "Previous song"),
    "shuffle": (0xE8B1, "Shuffle"),
    "repeat": (0xE8EE, "Repeat"),
    "record": (0xE7C8, "Record"),
    "heart": (0xEB52, "Heart"),
    "heart_outline": (0xEB51, "Heart (outline)"),
    "note": (0xEC4F, "Music note"),
    "notes": (0xE8D6, "Music notes"),
    "headphones": (0xE7F6, "Headphones"),
    "speaker": (0xE767, "Speaker"),
    "speaker_muted": (0xE74F, "Speaker muted"),
    "vol_up": (0xE995, "Volume up"),
    "vol_down": (0xE993, "Volume down"),
    "mic": (0xE720, "Microphone"),
    "mic_off": (0xEC54, "Microphone off"),
    "chat": (0xE8BD, "Chat"),
    "sun": (0xE706, "Sun"),
    "bright_up": (0xE706, "Brighter"),
    "bright_down": (0xEC8A, "Dimmer"),
    "moon": (0xE708, "Moon"),
    "bulb": (0xEA80, "Light bulb"),
    "power": (0xE7E8, "Power"),
    "restart": (0xE72C, "Restart"),
    "lock": (0xE72E, "Lock"),
    "unlock": (0xE785, "Unlock"),
    "key": (0xE8D7, "Key"),
    "gear": (0xE713, "Settings"),
    "folder": (0xE8B7, "Folder"),
    "camera": (0xE722, "Camera"),
    "camera_video": (0xE714, "Video camera"),
    "picture": (0xE91B, "Picture"),
    "slideshow": (0xE786, "Slideshow"),
    "monitor": (0xE7F4, "Monitor"),
    "pc": (0xE977, "PC"),
    "pulse": (0xE9D9, "Activity"),
    "cpu": (0xE950, "Processor"),
    "memory": (0xE7F1, "Memory"),
    "speed": (0xEC4A, "Speed"),
    "wifi": (0xE701, "Wi-Fi"),
    "gamepad": (0xE7FC, "Game controller"),
    "puzzle": (0xEA86, "Puzzle"),
    "apps": (0xF0E2, "Apps"),
    "palette": (0xE790, "Palette"),
    "brush": (0xE771, "Brush"),
    "scissors": (0xE8C6, "Scissors"),
    "stopwatch": (0xE916, "Stopwatch"),
    "clock": (0xE823, "Clock"),
    "calendar": (0xE787, "Calendar"),
    "home": (0xE80F, "Home"),
    "star": (0xE735, "Star"),
    "star_outline": (0xE734, "Star (outline)"),
    "flag": (0xE7C1, "Flag"),
    "bolt": (0xE945, "Lightning"),
    "thumbs_up": (0xE8E1, "Thumbs up"),
    "smile": (0xE76E, "Smile"),
    "eye": (0xE7B3, "Eye"),
    "check": (0xE8FB, "Check mark"),
    "close": (0xE711, "Cross"),
    "plus": (0xE710, "Plus"),
    "minus": (0xE738, "Minus"),
    "warning": (0xE7BA, "Warning"),
    "info": (0xE946, "Info"),
    "trash": (0xE74D, "Trash"),
    "download": (0xE896, "Download"),
    "upload": (0xE898, "Upload"),
    "arrow_right": (0xE72A, "Arrow right"),
    "arrow_left": (0xE72B, "Arrow left"),
    "chevron_right": (0xE76C, "Chevron right"),
    "chevron_left": (0xE76B, "Chevron left"),
    "search": (0xE721, "Search"),
    "document": (0xE8A5, "Document"),
    "edit": (0xE70F, "Pencil"),
    "paste": (0xE77F, "Paste"),
    "library": (0xE8F1, "Books"),
    "mail": (0xE715, "Envelope"),
}
ICONS = {name: desc for name, (_, desc) in _ICON_TABLE.items()}

_ICON_FONTS = ("SegoeIcons.ttf", "segmdl2.ttf")   # Windows 11, then the Windows 10 icon font
_icon_font_name = []                              # [name] or [None] once we have looked


def _icon_font(size):
    """The icon font at this size, or None when this PC has neither icon font."""
    if not _icon_font_name:
        found = None
        for name in _ICON_FONTS:
            try:
                ImageFont.truetype(name, 20)
                found = name
                break
            except OSError:
                continue
        if found is None:
            log("Icon font not found - using the simple built-in icon drawings")
        _icon_font_name.append(found)
    if _icon_font_name[0] is None:
        return None
    return ImageFont.truetype(_icon_font_name[0], size)


def _glyph_bytes(ch, fnt):
    im = Image.new("L", (40, 40), 0)
    ImageDraw.Draw(im).text((20, 20), ch, font=fnt, fill=255, anchor="mm")
    return im.tobytes()


@lru_cache(maxsize=None)
def _has_glyph(cp):
    """Is this icon really in the font? (A missing one draws as the same empty box.)"""
    fnt = _icon_font(28)
    return fnt is not None and _glyph_bytes(chr(cp), fnt) != _glyph_bytes("￿", fnt)


def _centered(im):
    """Move the drawn part of a mask to the middle of its square."""
    box = im.getbbox()
    if not box:
        return im
    out = Image.new("L", im.size, 0)
    part = im.crop(box)
    out.paste(part, ((im.width - part.width) // 2, (im.height - part.height) // 2))
    return out


def _glyph_mask(cps, px):
    """Icon-font glyph(s) as a px-square mask, or None if the font can't draw them.
    Drawn 3x bigger with a slightly fattened line, then shrunk: thin line icons would
    look faint on the small screens otherwise."""
    cps = cps if isinstance(cps, tuple) else (cps,)
    if not all(_has_glyph(cp) for cp in cps):
        return None
    k = 3
    S = px * k
    stroke = max(1, int(round(S * 0.02)))
    if len(cps) == 1:
        im = Image.new("L", (S, S), 0)
        ImageDraw.Draw(im).text((S / 2, S / 2), chr(cps[0]), font=_icon_font(int(S * 0.84)),
                                fill=255, anchor="mm", stroke_width=stroke, stroke_fill=255)
        im = _centered(im)
    else:                              # two glyphs side by side (play + pause)
        im = Image.new("L", (S, S), 0)
        d = ImageDraw.Draw(im)
        fnt = _icon_font(int(S * 0.56))
        for i, cp in enumerate(cps):
            d.text((S * (0.25 + 0.5 * i), S / 2), chr(cp), font=fnt, fill=255, anchor="mm",
                   stroke_width=stroke, stroke_fill=255)
        im = _centered(im)
    return im.resize((px, px), LANCZOS)


def icon_layer(name, px, tint=WHITE):
    """Simple hand-drawn icons - only used when the Windows icon font is missing.
    Drawn at 4x and shrunk for smooth edges. Returns RGBA."""
    S, k = 400, 4.0
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    W = tuple(tint) + (255,)
    CLEAR = (0, 0, 0, 0)

    def poly(pts, fill=W):
        d.polygon([(x * k, y * k) for x, y in pts], fill=fill)

    def rect(x0, y0, x1, y1, r=0, fill=W):
        d.rounded_rectangle([x0 * k, y0 * k, x1 * k, y1 * k], radius=r * k, fill=fill)

    def circle(cx, cy, r, fill=W):
        d.ellipse([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k], fill=fill)

    def line(x0, y0, x1, y1, w, fill=W):
        d.line([x0 * k, y0 * k, x1 * k, y1 * k], fill=fill, width=int(w * k))

    def arc(cx, cy, r, a0, a1, w, fill=W):
        d.arc([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k], a0, a1,
              fill=fill, width=int(w * k))

    def speaker():
        rect(8, 38, 24, 62, 2)
        poly([(24, 38), (46, 20), (46, 80), (24, 62)])

    def plus(cx, cy, sz=12, t=6):
        rect(cx - sz, cy - t / 2, cx + sz, cy + t / 2, 1.5)
        rect(cx - t / 2, cy - sz, cx + t / 2, cy + sz, 1.5)

    def minus(cx, cy, sz=12, t=6):
        rect(cx - sz, cy - t / 2, cx + sz, cy + t / 2, 1.5)

    def sun(cx, cy, scale=1.0):
        circle(cx, cy, 15 * scale)
        for i in range(8):
            a = math.radians(i * 45)
            line(cx + 23 * scale * math.cos(a), cy + 23 * scale * math.sin(a),
                 cx + 33 * scale * math.cos(a), cy + 33 * scale * math.sin(a), 6 * scale)

    if name == "play_pause":
        poly([(8, 16), (8, 84), (52, 50)])
        rect(62, 18, 74, 82, 3)
        rect(80, 18, 92, 82, 3)
    elif name in ("play", "play_solid"):
        poly([(22, 14), (22, 86), (86, 50)])
    elif name in ("pause", "pause_solid"):
        rect(22, 16, 42, 84, 4)
        rect(58, 16, 78, 84, 4)
    elif name == "vol_up":
        speaker()
        plus(74, 50)
    elif name == "vol_down":
        speaker()
        minus(74, 50)
    elif name == "speaker":
        speaker()
        arc(46, 50, 17, -50, 50, 6)
        arc(46, 50, 31, -50, 50, 6)
    elif name == "speaker_muted":
        speaker()
        line(58, 36, 88, 64, 7)
        line(88, 36, 58, 64, 7)
    elif name == "sun":
        sun(50, 50)
    elif name == "bright_up":
        sun(34, 50, 0.85)
        plus(80, 50, 10, 6)
    elif name == "bright_down":
        sun(34, 50, 0.85)
        minus(80, 50, 10, 6)
    elif name == "headphones":
        arc(50, 54, 34, 180, 360, 8)
        rect(10, 52, 28, 84, 6)
        rect(72, 52, 90, 84, 6)
    elif name == "chat":
        rect(8, 16, 92, 70, 16)
        poly([(26, 64), (20, 88), (46, 68)])
        for cx in (30, 50, 70):
            circle(cx, 43, 6.5, fill=CLEAR)
    elif name == "gear":
        circle(50, 50, 28)
        for i in range(8):
            a = math.radians(i * 45)
            ca, sa = math.cos(a), math.sin(a)
            pts = []
            for dr, dw in ((24, -7), (42, -7), (42, 7), (24, 7)):
                pts.append((50 + dr * ca - dw * sa, 50 + dr * sa + dw * ca))
            poly(pts)
        circle(50, 50, 12, fill=CLEAR)
    elif name in ("arrow_right", "chevron_right"):
        rect(12, 42, 62, 58, 4)
        poly([(56, 22), (90, 50), (56, 78)])
    elif name in ("arrow_left", "chevron_left"):
        rect(38, 42, 88, 58, 4)
        poly([(44, 22), (10, 50), (44, 78)])
    elif name == "power":
        arc(50, 54, 32, 300, 600, 9)
        rect(45, 10, 55, 52, 4)
    elif name == "restart":
        arc(50, 52, 32, 40, 330, 9)
        poly([(72, 16), (90, 40), (64, 44)])
    elif name == "moon":
        circle(50, 50, 36)
        circle(66, 38, 32, fill=CLEAR)
    elif name == "next":
        poly([(10, 20), (10, 80), (56, 50)])
        poly([(44, 20), (44, 80), (88, 50)])
        rect(84, 20, 94, 80, 2)
    elif name in ("heart", "heart_outline"):
        circle(32, 38, 22)
        circle(68, 38, 22)
        poly([(11, 44), (89, 44), (50, 88)])
    elif name == "prev":
        poly([(90, 20), (90, 80), (44, 50)])
        poly([(56, 20), (56, 80), (12, 50)])
        rect(6, 20, 16, 80, 2)
    elif name == "camera_video":
        rect(6, 28, 66, 74, 9)
        poly([(62, 51), (94, 28), (94, 74)])
        circle(24, 42, 6, fill=CLEAR)
    elif name in ("mic", "mic_off"):
        rect(36, 8, 64, 58, 14)
        arc(50, 40, 25, 0, 180, 7)
        rect(46.5, 64, 53.5, 82)
        rect(32, 80, 68, 89, 3)
    elif name in ("lock", "unlock"):
        arc(50, 44, 22, 180, 360, 9)
        rect(28, 43, 36, 50)
        rect(64, 43, 72, 50)
        rect(18, 44, 82, 88, 8)
        circle(50, 62, 7, fill=CLEAR)
        rect(47, 62, 53, 76, 1, fill=CLEAR)
    elif name == "folder":
        rect(8, 22, 44, 40, 5)
        rect(8, 32, 92, 82, 7)
    elif name == "camera":
        rect(30, 18, 70, 36, 5)
        rect(8, 28, 92, 82, 10)
        circle(50, 55, 19, fill=CLEAR)
        circle(50, 55, 12)
    elif name == "gamepad":
        rect(6, 30, 94, 76, 22)
        rect(18, 50, 40, 57, 1, fill=CLEAR)
        rect(25.5, 42.5, 32.5, 64.5, 1, fill=CLEAR)
        circle(66, 47, 5.5, fill=CLEAR)
        circle(78, 59, 5.5, fill=CLEAR)
    elif name in ("monitor", "pc"):
        rect(6, 14, 94, 68, 7)
        rect(14, 22, 86, 60, 2, fill=CLEAR)
        rect(44, 68, 56, 80)
        rect(28, 79, 72, 88, 3)
    elif name == "pulse":
        rect(6, 14, 94, 86, 10)
        rect(13, 21, 87, 79, 5, fill=CLEAR)
        d.line([(x * k, y * k) for x, y in
                ((16, 52), (34, 52), (42, 32), (54, 70), (62, 46), (68, 52), (84, 52))],
               fill=W, width=int(6 * k), joint="curve")
    elif name in ("note", "notes"):
        d.ellipse([22 * k, 62 * k, 50 * k, 82 * k], fill=W)
        rect(44, 20, 51, 72, 2)
        poly([(51, 20), (78, 30), (78, 46), (51, 36)])
    elif name == "plus":
        plus(50, 50, 34, 12)
    elif name == "minus":
        minus(50, 50, 34, 12)
    else:                              # no drawing for this one: a plain ring
        arc(50, 50, 34, 0, 360, 9)
    return im.resize((px, px), LANCZOS)


def _is_emoji(name):
    """Anything that isn't a plain word is treated as an emoji."""
    return bool(name) and not str(name).isascii()


@lru_cache(maxsize=256)
def _emoji_image(text, px):
    """Colour emoji, centred in a px square (drawn 2x bigger and shrunk)."""
    k = 2
    S = px * k
    im = Image.new("RGBA", (S * 2, S * 2), (0, 0, 0, 0))
    try:
        fnt = ImageFont.truetype("seguiemj.ttf", int(S * 0.80))
        ImageDraw.Draw(im).text((S, S), text, font=fnt, anchor="mm", embedded_color=True)
    except Exception:
        ImageDraw.Draw(im).text((S, S), text[:2], font=font(int(S * 0.6)), anchor="mm", fill=WHITE)
    box = im.getbbox()
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    if box:
        part = im.crop(box)
        if part.width > S or part.height > S:
            part.thumbnail((S, S), LANCZOS)
        out.paste(part, ((S - part.width) // 2, (S - part.height) // 2))
    return out.resize((px, px), LANCZOS)


@lru_cache(maxsize=1024)
def _icon_cached(name, px, color):
    if name in _ICON_TABLE:
        mask = _glyph_mask(_ICON_TABLE[name][0], px)
        if mask is None:
            return icon_layer(name, px, color)
        im = Image.new("RGBA", (px, px), color + (0,))
        im.putalpha(mask)
        return im
    if _is_emoji(name):
        return _emoji_image(name, px)
    im = Image.new("RGBA", (px, px), (0, 0, 0, 0))     # an icon name we don't know
    ImageDraw.Draw(im).text((px / 2, px / 2), "?", font=font(int(px * 0.8)), anchor="mm",
                            fill=color + (255,))
    return im


def icon_image(name, px, color=WHITE):
    """RGBA picture (px square) of a built-in icon in `color`, or of an emoji."""
    return _icon_cached(str(name), max(4, int(px)), tuple(color)[:3]).copy()


@lru_cache(maxsize=512)
def _halo(name, px, color, blur, strength):
    """Blurred single-colour copy of an icon: its glow (or, in black, its shadow)."""
    pad = int(math.ceil(blur * 3))
    alpha = Image.new("L", (px + 2 * pad, px + 2 * pad), 0)
    alpha.paste(_icon_cached(name, px, WHITE).getchannel("A"), (pad, pad))
    alpha = _scaled(alpha.filter(ImageFilter.GaussianBlur(blur)), strength)
    im = Image.new("RGBA", alpha.size, color + (0,))
    im.putalpha(alpha)
    return im, pad


def _stamp_icon(img, name, px, cx, cy, color, glow=None, shadow=None):
    """Put an icon on a picture, centred at (cx, cy), with an optional glow or shadow."""
    name, px = str(name), max(4, int(px))
    x, y = int(round(cx - px / 2)), int(round(cy - px / 2))
    if glow:
        halo, pad = _halo(name, px, tuple(glow), px * 0.10, 0.85)
        img.paste(halo, (x - pad, y - pad), halo)
    if shadow is not None:
        halo, pad = _halo(name, px, tuple(shadow), px * 0.05, 0.55)
        img.paste(halo, (x - pad, y - pad + max(1, px // 22)), halo)
    ic = _icon_cached(name, px, tuple(color)[:3])
    img.paste(ic, (x, y), ic)


def paste_icon(img, name, px, y, tint=WHITE):
    """Icon centred left-to-right with its top at y."""
    _stamp_icon(img, name, px, img.width / 2, y + px / 2, tint)


_file_icon_cache = {}


def _file_icon(rel, px):
    """A picture file used as an icon (cached until the file changes). None if unreadable."""
    path = BASE / rel
    try:
        key = (str(path), path.stat().st_mtime, px)
        if key not in _file_icon_cache:
            if len(_file_icon_cache) > 64:
                _file_icon_cache.clear()
            pic = Image.open(path).convert("RGBA")
            pic.thumbnail((px, px), LANCZOS)
            _file_icon_cache[key] = pic
        return _file_icon_cache[key]
    except Exception as e:
        log(f"Could not load icon_file {rel}: {e}")
        return None


# ----------------------------------------------------------------- gauges, bars, dots (cached layers)
def _polar(cx, cy, r, deg):
    a = math.radians(deg)
    return cx + r * math.cos(a), cy + r * math.sin(a)


@lru_cache(maxsize=1024)
def _ring_layer(s, frac_k, color, track, full, glow):
    """Round gauge as a see-through layer. frac_k = 0..1000 (how full).
    full=False: a 270 degree arc open at the bottom (room for a caption);
    full=True: a whole circle starting at 12 o'clock."""
    k = 4
    S = s * k
    cx, cy = S / 2, S * (0.5 if full else 0.485)
    r, w = S * 0.37, max(2, int(round(S * 0.085)))
    start, span = (-90, 360) if full else (135, 270)
    box = [cx - r - w / 2, cy - r - w / 2, cx + r + w / 2, cy + r + w / 2]

    def arc(d, a0, a1, fill):
        d.arc(box, a0, a1, fill=fill, width=w)
        for a in (a0, a1):               # round ends
            x, y = _polar(cx, cy, r, a)
            d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=fill)

    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if full:
        d.arc(box, 0, 360, fill=track, width=w)
    else:
        arc(d, start, start + span, track)
    if frac_k > 0:
        value = Image.new("RGBA", (S, S), color + (0,))
        arc(ImageDraw.Draw(value), start, start + span * frac_k / 1000.0, color + (255,))
        if glow:
            halo = Image.new("RGBA", (S, S), glow + (0,))
            halo.putalpha(_scaled(value.getchannel("A").filter(ImageFilter.GaussianBlur(S * 0.04)), 0.75))
            im = Image.alpha_composite(im, halo)
        im = Image.alpha_composite(im, value)
    return im.resize((s, s), LANCZOS)


@lru_cache(maxsize=512)
def _bar_layer(s, fill_px, color, track, glow):
    """Progress bar with round ends and a knob, as a see-through layer the size of the key."""
    k = 4
    S = s * k
    x0, x1, cy, h = S * 0.14, S * 0.86, S * 0.815, S * 0.07
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle([x0, cy - h / 2, x1, cy + h / 2], radius=h / 2, fill=track)
    xe = x0 + fill_px * k
    value = Image.new("RGBA", (S, S), color + (0,))
    vd = ImageDraw.Draw(value)
    vd.rounded_rectangle([x0, cy - h / 2, max(xe, x0 + h), cy + h / 2], radius=h / 2, fill=color + (255,))
    vd.ellipse([xe - h * 0.95, cy - h * 0.95, xe + h * 0.95, cy + h * 0.95], fill=color + (255,))
    if glow:
        halo = Image.new("RGBA", (S, S), glow + (0,))
        halo.putalpha(_scaled(value.getchannel("A").filter(ImageFilter.GaussianBlur(S * 0.035)), 0.7))
        im = Image.alpha_composite(im, halo)
    im = Image.alpha_composite(im, value)
    return im.resize((s, s), LANCZOS)


@lru_cache(maxsize=256)
def _dots_layer(s, page, count, on, off, cy_k):
    """Row of page dots: the current page is a longer, brighter pill."""
    k = 4
    S = s * k
    dot, gap, pill = S * 0.085, S * 0.065, S * 0.20
    if count > 5:                       # many pages: tighter dots
        dot, gap, pill = S * 0.07, S * 0.045, S * 0.15
    total = (count - 1) * (dot + gap) + pill
    x, cy = (S - total) / 2, S * cy_k / 1000.0
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i in range(count):
        w = pill if i == page else dot
        d.rounded_rectangle([x, cy - dot / 2, x + w, cy + dot / 2], radius=dot / 2,
                            fill=on if i == page else off)
        x += w + gap
    return im.resize((s, s), LANCZOS)


# ----------------------------------------------------------------- Canvas: one key being painted
class Canvas:
    """A key picture in progress. Sizes are given as fractions of the key size, so the same
    code draws the 85 px device keys and the bigger previews in the settings app."""

    def __init__(self, cfg, color="#333333"):
        self.s = int(cfg["image"]["size"])
        self.theme = _theme_of(cfg)
        self.color = str(color or "#333333")
        self.ink = _ink(self.theme, self.color)
        self.img = _background(self.s, self.theme, self.color).copy()
        self.d = ImageDraw.Draw(self.img)

    # ---- text
    def text(self, text, cx, cy, fnt, fill, glow=False):
        """One line of text centred on (cx, cy) (fractions of the key)."""
        s = self.s
        x, y = cx * s, cy * s + _cap_height(fnt) / 2
        if glow and self.ink.glow:
            m = Image.new("L", (s, s), 0)
            ImageDraw.Draw(m).text((x, y), text, font=fnt, fill=255, anchor="ms")
            m = _scaled(m.filter(ImageFilter.GaussianBlur(s * 0.05)), 0.8)
            self.img.paste(Image.new("RGB", (s, s), self.ink.glow), (0, 0), m)
        if self.ink.text_shadow:
            self.d.text((x, y + max(1, s / 70)), text, font=fnt, fill=self.ink.text_shadow, anchor="ms")
        self.d.text((x, y), text, font=fnt, fill=fill, anchor="ms")

    def run(self, parts, cy, fnt, gap=0.05):
        """Several differently coloured bits of text on one centred line: [(text, colour)]."""
        parts = [(t, c) for t, c in parts if t]
        widths = [_width(t, fnt) for t, _ in parts]
        g = gap * self.s
        x = (self.s - sum(widths) - g * (len(parts) - 1)) / 2
        y = cy * self.s + _cap_height(fnt) / 2
        for (t, c), w in zip(parts, widths):
            if self.ink.text_shadow:
                self.d.text((x, y + max(1, self.s / 70)), t, font=fnt, fill=self.ink.text_shadow, anchor="ls")
            self.d.text((x, y), t, font=fnt, fill=c, anchor="ls")
            x += w + g

    def number(self, text, cy, size, maxw=0.84, fill=None, glow=True, unit=""):
        """Big number / time, shrunk to fit. `unit` (like "%") is drawn smaller after it."""
        s = self.s
        fill = fill or self.ink.big
        if not unit:
            self.text(text, 0.5, cy, _fit(text, self.ink.num_kinds, size * s, maxw * s), fill, glow)
            return
        ufnt = font(size * s * 0.52, self.ink.num_kinds[0])
        uw = _width(unit, ufnt) + s * 0.012
        fnt = _fit(text, self.ink.num_kinds, size * s, maxw * s - uw)
        w = _width(text, fnt)
        left = (s - w - uw) / 2
        self.text(text, (left + w / 2) / s, cy, fnt, fill, glow)
        y = cy * s + _cap_height(fnt) / 2
        self.d.text((left + w + s * 0.012, y), unit, font=ufnt, fill=self.ink.sub, anchor="ls")

    def caption(self, text, cy, fill=None, size=0.15, maxw=0.86):
        """Small line of text (shrinks to fit, never clipped)."""
        text = str(text)
        fnt = _fit(text, self.ink.label_kind, size * self.s, maxw * self.s)
        self.text(_clip(text, fnt, maxw * self.s), 0.5, cy, fnt, fill or self.ink.sub)

    def words(self, text, cy, size, maxlines=3, fill=None, min_size=0.13, maxw=0.86, kind=None):
        """A word or sentence, as big as fits: one line if possible, else wrapped."""
        s = self.s
        text, kind = str(text), kind or self.ink.label_kind
        fill = fill or self.ink.big
        px = int(round(size * s))
        while True:
            fnt = font(px, kind)
            lines, cut = _wrap(text, fnt, maxw * s, maxlines)
            if not cut or px <= max(8, int(min_size * s)):
                break
            px -= 1
        step = px * 1.16 / s
        y = cy - step * (len(lines) - 1) / 2
        for line in lines:
            self.text(line, 0.5, y, fnt, fill)
            y += step

    # ---- icons
    def icon(self, name, px, cx=0.5, cy=0.5, color=None, glow=True):
        """Built-in icon or emoji, px = size as a fraction of the key."""
        _stamp_icon(self.img, name, int(round(px * self.s)), cx * self.s, cy * self.s,
                    color or self.ink.icon,
                    glow=self.ink.glow if glow else None, shadow=self.ink.shadow)

    def picture(self, pic, cx, cy):
        """A ready-made RGBA picture (icon_file), centred."""
        self.img.paste(pic, (int(cx * self.s - pic.width / 2), int(cy * self.s - pic.height / 2)), pic)

    # ---- ready-made layouts
    def icon_label(self, icon=None, label="", pic=None):
        """The standard button: icon on top, label underneath (two lines if it is long)."""
        s, ink = self.s, self.ink
        label = str(label or "").strip()

        def put(px, cy):
            if pic is not None:
                p = pic.copy()
                p.thumbnail((int(px * s), int(px * s)), LANCZOS)
                self.picture(p, 0.5, cy)
            elif icon:
                self.icon(icon, px, 0.5, cy)

        if not label:
            put(0.64, 0.5)
            return
        maxw = 0.88 * s
        words = label.split()
        one = _fit(label, ink.label_kind, 0.17 * s, maxw, min_size=int(0.14 * s))
        # One line, unless that would make the text small and it can be split into two lines.
        if _width(label, one) <= maxw and (one.size >= 0.15 * s or len(words) < 2):
            put(0.50, 0.385)
            self.text(label, 0.5, 0.815, one, ink.label)
            return
        if len(words) >= 2:               # two lines, split where both halves are most even
            fnt_size = int(round(0.155 * s))
            splits = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))]
            a, b = min(splits, key=lambda p: max(_width(p[0], font(fnt_size, ink.label_kind)),
                                                 _width(p[1], font(fnt_size, ink.label_kind))))
            longest = a if _width(a, font(fnt_size, ink.label_kind)) >= _width(b, font(fnt_size, ink.label_kind)) else b
            fnt = _fit(longest, ink.label_kind, fnt_size, maxw)
            put(0.42, 0.30)
            self.text(_clip(a, fnt, maxw), 0.5, 0.655, fnt, ink.label)
            self.text(_clip(b, fnt, maxw), 0.5, 0.835, fnt, ink.label)
            return
        # One very long word: keep it readable and cut it off with "..." instead of tiny text.
        fnt = _fit(label, (ink.label_kind, "narrow"), 0.14 * s, maxw, min_size=int(0.13 * s))
        put(0.50, 0.385)
        self.text(_clip(label, fnt, maxw), 0.5, 0.815, fnt, ink.label)

    def ring(self, frac, color=None, full=False, track=None):
        """Round gauge, frac = 0..1."""
        frac_k = int(round(max(0.0, min(1.0, frac or 0)) * 1000))
        color = tuple(color or self.ink.ring)[:3]
        glow = color if self.ink.glow else None
        layer = _ring_layer(self.s, frac_k, color, tuple(track or self.ink.track), full, glow)
        self.img.paste(layer, (0, 0), layer)

    def gauge(self, frac, color=None, text="", unit="", caption=None, caption_icon=None,
              text_fill=None, caption_fill=None):
        """Ring gauge open at the bottom, a number in the middle, a small caption below."""
        self.ring(frac, color)
        if text:
            self.number(str(text), 0.485, 0.31, maxw=0.56, fill=text_fill, unit=unit)
        if caption_icon:
            self.icon(caption_icon, 0.23, 0.5, 0.855, color=caption_fill or self.ink.sub, glow=False)
        elif caption:
            self.caption(str(caption).upper() if self.theme != "classic" else str(caption),
                         0.87, fill=caption_fill or self.ink.sub, size=0.15, maxw=0.48)

    def bar(self, frac, color=None):
        """Progress bar along the bottom."""
        color = tuple(color or self.ink.ring)[:3]
        fill_px = int(round(max(0.0, min(1.0, frac or 0)) * 0.72 * self.s))
        layer = _bar_layer(self.s, fill_px, color, self.ink.track, color if self.ink.glow else None)
        self.img.paste(layer, (0, 0), layer)

    def done(self, dim=1.0):
        if dim != 1.0:
            self.img = ImageEnhance.Brightness(self.img).enhance(max(0.0, dim))
        return self.img


# ----------------------------------------------------------------- widgets (live keys)
WIDGETS = {
    "clock": {"label": "Clock", "desc": "The time and date"},
    "brightness": {"label": "Brightness", "desc": "Screen brightness level"},
    "status": {"label": "Status", "desc": "Shows D6Deck is running, and for how long"},
    "volume": {"label": "Volume", "desc": "PC volume level"},
    "spotify_cover": {"label": "Album cover", "desc": "Cover of the song playing in Spotify"},
    "spotify_title": {"label": "Song title", "desc": "Song and artist playing in Spotify"},
    "spotify_time": {"label": "Song time", "desc": "Time left in the song"},
    "play_pause": {"label": "Play / Pause", "desc": "Shows whether music is playing"},
    "page": {"label": "Page", "desc": "Which page you are on"},
    "cpu": {"label": "CPU", "desc": "How busy the processor is"},
    "ram": {"label": "Memory", "desc": "How much memory is in use"},
    "stopwatch": {"label": "Stopwatch", "desc": "A stopwatch"},
    "theme": {"label": "Theme", "desc": "Name of the current look"},
    "saver": {"label": "Screen saver", "desc": "Name of the current screen saver"},
}

APP_START = time.monotonic()


def uptime_text():
    m = int((time.monotonic() - APP_START) // 60)
    return f"{m // 60}h {m % 60}m" if m >= 60 else f"{m}m"


def clock_text(k):
    """(time, small text): ("3:07", "PM") or, for a 24-hour clock, ("15:07", "Sun")."""
    now = datetime.now()
    if k.get("hour24"):
        return now.strftime("%H:%M"), now.strftime("%a")
    return now.strftime("%I:%M").lstrip("0"), now.strftime("%p")


def _clock_parts(k):
    """Everything the clock key shows: (time, "AM"/"PM" or "", date)."""
    now = datetime.now()
    t, small = clock_text(k)
    if k.get("hour24"):
        return t, "", f"{small} {now.day} {now.strftime('%b')}"
    return t, small, f"{now.strftime('%a')} {now.day}"


def stopwatch_seconds(cfg):
    sw = cfg.get("stopwatch") or {}
    t = float(sw.get("elapsed", 0.0))
    if sw.get("running") and sw.get("since") is not None:
        t += time.monotonic() - sw["since"]
    return t


def _w_clock(k, cfg, live):
    c = Canvas(cfg, k.get("color", "#1c2430"))
    t, ampm, date = _clock_parts(k)
    kinds = c.ink.num_kinds[1:] or c.ink.num_kinds      # the narrower cut: same look at every hour
    fnt = _fit(t, kinds, 0.42 * c.s, 0.84 * c.s)
    c.text(t, 0.5, 0.40, fnt, c.ink.big, glow=True)
    small = font(0.165 * c.s, c.ink.label_kind)
    c.run([(ampm, c.ink.accent), (date, c.ink.sub)], 0.755, small)
    return c.done()


def _w_brightness(k, cfg, live):
    c = Canvas(cfg, k.get("color", "#7a5200"))
    b = int(cfg.get("brightness", 0))
    c.gauge(b / 100, text=str(b), unit="%", caption_icon="sun")
    return c.done()


def _w_volume(k, cfg, live):
    c = Canvas(cfg, k.get("color", "#17402a"))
    vol = live.volume()
    if vol is None:
        c.gauge(0, text="--", caption_icon="speaker")
        return c.done()
    pct, muted = vol
    if muted:                          # level still visible, but dull red with a crossed speaker
        c.ring(pct / 100, color=(150, 58, 58))
        c.icon("speaker_muted", 0.36, 0.5, 0.47, color=RED, glow=False)
        c.caption("MUTED", 0.87, fill=RED, size=0.15, maxw=0.48)
        return c.done()
    c.gauge(pct / 100, text=str(int(pct)), unit="%", caption_icon="speaker")
    return c.done()


def _w_meter(k, cfg, live):
    which = k.get("widget")
    c = Canvas(cfg, k.get("color", "#0b4a6f" if which == "cpu" else "#4a2a7a"))
    v = live.cpu() if which == "cpu" else live.ram()
    name = "CPU" if which == "cpu" else "RAM"
    if v is None:
        c.gauge(0, text="--", caption=name)
    else:
        c.gauge(v / 100, color=level_color(v), text=str(int(v)), unit="%", caption=name)
    return c.done()


_cover_cache = {}


def _cover(data, s):
    """Album art cropped square and shrunk to the key size (cached). None if unreadable."""
    key = (hashlib.md5(data).digest(), s)
    if key not in _cover_cache:
        if len(_cover_cache) > 12:
            _cover_cache.clear()
        try:
            pic = ImageOps.fit(Image.open(io.BytesIO(data)).convert("RGB"), (s, s), LANCZOS)
        except Exception as e:
            log(f"Could not read cover image: {e}")
            pic = None
        _cover_cache[key] = pic
    return _cover_cache[key]


def _w_spotify_cover(k, cfg, live):
    view = live.spotify()
    s, theme = cfg["image"]["size"], _theme_of(cfg)
    pic = _cover(view["cover"], s) if view and view.get("cover") else None
    if pic is None:
        c = Canvas(cfg, k.get("color", "#121212"))
        c.icon("note", 0.50, 0.5, 0.385, color=c.ink.music)
        c.caption("No music", 0.815, fill=c.ink.sub, size=0.165)
        return c.done()
    u = s / 85.0
    radius, inset = _corner(s, theme)
    pic = pic.copy()
    if not view.get("playing"):        # paused: dimmed, with a pause sign
        pic = ImageEnhance.Brightness(pic).enhance(0.42)
        _stamp_icon(pic, "pause_solid", int(s * 0.44), s / 2, s / 2, WHITE, shadow=BLACK)
    if theme != "classic":             # thin light rim so the art sits in the same "glass" as the other keys
        pic.paste(Image.new("RGB", (s, s), WHITE), (0, 0),
                  _scaled(_edge_mask(s, radius, inset, 1.3 * u), 0.30))
    return Image.composite(pic, Image.new("RGB", (s, s), BLACK), _theme_mask(s, theme))


def _w_spotify_title(k, cfg, live):
    c = Canvas(cfg, k.get("color", "#10231a"))
    view = live.spotify()
    s, ink = c.s, c.ink
    if not view or not view.get("title"):
        c.icon("note", 0.34, 0.5, 0.30, color=ink.music)
        c.caption("Spotify", 0.62, fill=ink.big, size=0.17)
        c.caption("not playing", 0.81, fill=ink.sub, size=0.15)
        return c.done()
    maxw = 0.86 * s
    artist = str(view.get("artist") or "")
    # Biggest title size that fits: 2 lines when large, up to 3 when smaller.
    for size, maxlines in ((0.21, 2), (0.185, 3), (0.165, 3), (0.15, 3)):
        tfont = font(size * s, ink.label_kind)
        lines, cut = _wrap(view["title"], tfont, maxw, maxlines)
        if not cut:
            break
    afont = (_fit(artist, (ink.label_kind, "narrow"), 0.155 * s, maxw, min_size=int(0.135 * s))
             if artist else None)
    step = tfont.size * 1.14 / s
    astep = (afont.size * 1.3 / s) if artist else 0
    y = 0.5 - (step * len(lines) + astep) / 2 + step / 2
    for line in lines:
        c.text(line, 0.5, y, tfont, ink.big)
        y += step
    if artist:
        c.text(_clip(artist, afont, maxw), 0.5, y - step / 2 + astep / 2 + 0.01, afont, ink.music)
    return c.done()


def _w_spotify_time(k, cfg, live):
    c = Canvas(cfg, k.get("color", "#10231a"))
    view = live.spotify()
    if not view or view.get("rem") is None:
        c.number("--:--", 0.36, 0.30, fill=c.ink.sub, glow=False)
        c.caption("no music", 0.60)
        c.bar(0)
        return c.done()
    playing = view.get("playing")
    c.number("-" + fmt_time(view["rem"]), 0.34, 0.31, fill=c.ink.big if playing else c.ink.sub,
             glow=bool(playing))
    c.caption(f"of {fmt_time(view['dur'])}" if playing else "paused", 0.585)
    c.bar(view["pos"] / view["dur"] if view.get("dur") else 0,
          color=c.ink.music if c.theme in ("neon", "classic") else None)
    return c.done()


def _w_play_pause(k, cfg, live):
    c = Canvas(cfg, k.get("color", "#1f6feb"))
    view = live.spotify()
    playing = bool(view and view.get("playing"))
    c.icon_label("pause_solid" if playing else "play_solid", k.get("label", "Play/Pause"))
    return c.done()


def _w_page(k, cfg, live):
    c = Canvas(cfg, k.get("color", "#3d4451"))
    page, count = int(cfg.get("page", 0)), max(1, len(cfg.get("pages") or [1]))
    c.icon("arrow_right", 0.46, 0.5, 0.375)
    if count <= 8:
        off = c.ink.track[:3] + (min(255, c.ink.track[3] * 3),)
        layer = _dots_layer(c.s, page, count, tuple(c.ink.accent) + (255,), off, 790)
        c.img.paste(layer, (0, 0), layer)
    else:
        c.caption(f"Page {page + 1} of {count}", 0.80, fill=c.ink.label, size=0.16)
    return c.done()


def _w_status(k, cfg, live):
    c = Canvas(cfg, k.get("color", "#1c2430"))
    s = c.s
    c.caption("D6Deck", 0.25, fill=c.ink.big, size=0.21)
    fnt = font(0.16 * s, c.ink.label_kind)
    ok = GREEN if c.theme != "mono" else c.ink.big
    w = _width("running", fnt)
    r = 0.04 * s
    x = (s - w - r * 2 - 0.05 * s) / 2
    k4 = 4                               # the little "on" light, drawn smooth
    dot = Image.new("L", (int(r * 2 * k4),) * 2, 0)
    ImageDraw.Draw(dot).ellipse([0, 0, dot.width - 1, dot.height - 1], fill=255)
    dot = dot.resize((max(2, int(round(r * 2))),) * 2, LANCZOS)
    c.img.paste(Image.new("RGB", dot.size, ok), (int(x), int(0.52 * s - r)), dot)
    c.d.text((x + r * 2 + 0.05 * s, 0.52 * s + _cap_height(fnt) / 2), "running", font=fnt, fill=ok, anchor="ls")
    c.number(uptime_text(), 0.78, 0.20, fill=c.ink.sub, glow=False)
    return c.done()


def _w_stopwatch(k, cfg, live):
    c = Canvas(cfg, k.get("color", "#1c2430"))
    secs = int(stopwatch_seconds(cfg))
    running = bool((cfg.get("stopwatch") or {}).get("running"))
    text = fmt_time(secs)
    lap = (secs % 60) / 60 if secs else 0          # the ring goes round once a minute
    if running:
        c.ring(lap if lap else 1 / 60, color=GREEN if c.theme != "mono" else None, full=True)
        c.number(text, 0.455, 0.27, maxw=0.56)
        c.caption("RUNNING", 0.675, fill=GREEN if c.theme != "mono" else c.ink.sub, size=0.135, maxw=0.5)
    elif secs:
        c.ring(lap if lap else 1.0, color=AMBER if c.theme != "mono" else (150, 150, 156), full=True)
        c.number(text, 0.455, 0.27, maxw=0.56, fill=AMBER if c.theme != "mono" else c.ink.sub, glow=False)
        c.caption("PAUSED", 0.675, fill=c.ink.sub, size=0.135, maxw=0.5)
    else:
        c.ring(0, full=True)
        c.icon("stopwatch", 0.26, 0.5, 0.33, color=c.ink.sub, glow=False)
        c.number(text, 0.62, 0.22, maxw=0.5, fill=c.ink.sub, glow=False)
    return c.done()


def _w_theme(k, cfg, live):
    c = Canvas(cfg, k.get("color", "#7a2a5c"))
    c.icon_label("palette", THEMES[c.theme])
    return c.done()


def _w_saver(k, cfg, live):
    c = Canvas(cfg, k.get("color", "#2d3a8c"))
    style = str((cfg.get("screensaver") or {}).get("style", "auto"))
    c.icon_label("slideshow", style.replace("_", " ").title())
    return c.done()


_WIDGET_DRAW = {
    "clock": _w_clock, "brightness": _w_brightness, "status": _w_status, "volume": _w_volume,
    "spotify_cover": _w_spotify_cover, "spotify_title": _w_spotify_title,
    "spotify_time": _w_spotify_time, "play_pause": _w_play_pause, "page": _w_page,
    "cpu": _w_meter, "ram": _w_meter, "stopwatch": _w_stopwatch, "theme": _w_theme,
    "saver": _w_saver,
}


def signature(k, cfg, live):
    """Changes exactly when the key's picture needs redrawing (None = never by itself)."""
    w = k.get("widget")
    if w is None:
        return None
    if w == "clock":
        return _clock_parts(k)
    if w == "brightness":
        return cfg.get("brightness")
    if w == "status":
        return uptime_text()
    if w == "volume":
        return live.volume()
    if w == "cpu":
        return live.cpu()
    if w == "ram":
        return live.ram()
    if w == "page":
        return (cfg.get("page", 0), len(cfg.get("pages") or []))
    if w == "stopwatch":
        return int(stopwatch_seconds(cfg)), bool((cfg.get("stopwatch") or {}).get("running"))
    if w == "theme":
        return cfg.get("theme")
    if w == "saver":
        return (cfg.get("screensaver") or {}).get("style")
    if w in ("spotify_cover", "spotify_title", "spotify_time", "play_pause"):
        v = live.spotify()
        if v is None:
            return None
        if w == "spotify_cover":
            return (v.get("key"), bool(v.get("cover")), bool(v.get("playing")))
        if w == "spotify_title":
            return (v.get("title"), v.get("artist"))
        if w == "spotify_time":
            return (v.get("rem"), bool(v.get("playing")), int(v.get("dur") or 0))
        return bool(v.get("playing"))
    return ("static",)


# ----------------------------------------------------------------- public API (see SPEC.md)
def _button(k, cfg):
    """An ordinary button: icon (or picture file) with a label, or just text."""
    c = Canvas(cfg, k.get("color", "#333333"))
    label = str(k.get("label", "") or "")
    pic = _file_icon(k["icon_file"], int(c.s * 0.66)) if k.get("icon_file") else None
    if pic is not None or k.get("icon"):
        c.icon_label(k.get("icon"), label, pic=pic)
    elif label:
        c.words(label, 0.5, 0.24, fill=c.ink.label)
    return c.done()


def render_image(k, cfg, live):
    """Key k as it looks right now: an upright s x s RGB picture."""
    try:
        draw = _WIDGET_DRAW.get(k.get("widget"))
        return draw(k, cfg, live) if draw else _button(k, cfg)
    except Exception as e:             # one broken key must never stop the deck
        log(f"Could not draw key {k.get('label') or k.get('widget') or '?'}: {e}")
        return Canvas(cfg, k.get("color", "#333333")).done()


def render_key(k, cfg, live):
    """JPEG (ready for the D6) of key k as it should look right now."""
    return finalize(render_image(k, cfg, live), cfg)


def tile_image(cfg, color="#333333", icon=None, label=None, big=None, sub=None,
               ring=None, ring_color=None, dim=1.0):
    """A themed key picture built from parts (upright). See SPEC.md for what each part is."""
    c = Canvas(cfg, color)
    big = None if big is None else str(big)
    small = sub if sub is not None else label           # the small line under the main thing
    if ring is not None:
        c.ring(ring, color=ring_color)
        if big:
            c.number(big, 0.485, 0.31, maxw=0.56)
        elif icon:
            c.icon(icon, 0.36, 0.5, 0.475)
        if small:
            c.caption(str(small), 0.87, size=0.15, maxw=0.48,
                      fill=c.ink.label if sub is None else c.ink.sub)
    elif icon and big:
        if label:                                        # icon, big text and a label
            c.icon(icon, 0.36, 0.5, 0.25)
            c.number(big, 0.585, 0.24)
            c.caption(str(label), 0.845, fill=c.ink.label)
        else:
            c.icon(icon, 0.44, 0.5, 0.33)
            c.number(big, 0.76, 0.26)
    elif icon:
        c.icon_label(icon, label or "")
    elif big:
        _big_text(c, big, small, sub is None)
    elif label:
        c.words(str(label), 0.5, 0.24, fill=c.ink.label)
    return c.done(dim)


def _big_text(c, big, small, small_is_label):
    """Large text in the middle (a number, a time or a few words) with an optional small line."""
    s = c.s
    cy = 0.42 if small else 0.5
    one = _fit(big, c.ink.num_kinds, 0.42 * s, 0.84 * s)
    if one.size >= 0.24 * s or " " not in big.strip():
        c.text(_clip(big, one, 0.84 * s), 0.5, cy, one, c.ink.big, glow=True)
    else:                                                # a phrase: wrap it instead of shrinking it tiny
        c.words(big, cy, 0.25, maxlines=2 if small else 3, kind=c.ink.num_kinds[-1], maxw=0.84)
    if small:
        c.caption(str(small), 0.775 if one.size >= 0.24 * s or " " not in big.strip() else 0.82,
                  fill=c.ink.label if small_is_label else c.ink.sub, size=0.16)


def tile(cfg, **parts):
    return finalize(tile_image(cfg, **parts), cfg)


_plain_cache = {}


def plain(cfg, rgb):
    """A rounded tile in one colour with a soft shine (for game pads and light shows). Cached."""
    s = cfg["image"]["size"]
    rgb = tuple(int(v) for v in rgb)[:3]
    key = (s, rgb, cfg["image"].get("rotate"), cfg["image"].get("flip"))
    if key not in _plain_cache:
        u = s / 85.0
        img = _two_tone(s, _shade(rgb, 1.18), _shade(rgb, 0.80), _vertical(s))
        white = Image.new("RGB", (s, s), WHITE)
        img.paste(Image.new("RGB", (s, s), _mix(rgb, WHITE, 0.5)), (0, 0), _gloss(s, 0.22))
        if max(rgb) > 0:
            img.paste(white, (0, 0), _scaled(ImageChops.multiply(
                _edge_mask(s, 14 * u, 1 * u, 1.3 * u), ImageOps.invert(_vertical(s))), 0.35))
        img = Image.composite(img, Image.new("RGB", (s, s), BLACK), _mask_cached(s, 14 * u, 1 * u))
        _plain_cache[key] = finalize(img, cfg)
    return _plain_cache[key]


def wall_size(cfg):
    s = cfg["image"]["size"]
    return COLS * s, ROWS * s


def slice_wall(img, cfg, seen=None):
    """Cut one big picture into the 15 key pictures -> {pos: JPEG}.
    Pass the same `seen` dict for every frame of an animation: identical-looking tiles then
    come back as the very same bytes object, so unchanged keys are never re-sent."""
    s = cfg["image"]["size"]
    if img.size != wall_size(cfg):
        img = ImageOps.fit(img.convert("RGB"), wall_size(cfg), LANCZOS)
    seen = {} if seen is None else seen
    out = {}
    for pos in range(1, NKEYS + 1):
        c, r = (pos - 1) % COLS, (pos - 1) // COLS
        t = img.crop((c * s, r * s, (c + 1) * s, (r + 1) * s)).convert("RGB")
        h = hashlib.md5(t.tobytes()).digest()
        if h not in seen:
            seen[h] = finalize(t, cfg)
        out[pos] = seen[h]
    return out


class DemoLive:
    """Made-up live values, so previews look real without Spotify or the D6.
    The keyword options let a preview show other states: DemoLive(playing=False),
    DemoLive(music=False), DemoLive(muted=True), DemoLive(cpu=88) ..."""

    def __init__(self, playing=True, music=True, volume=42, muted=False, cpu=23, ram=61):
        self.playing, self.music = playing, music
        self.vol, self.muted, self._cpu, self._ram = volume, muted, cpu, ram
        self.cover = self._make_cover()

    @staticmethod
    def _make_cover():
        """A made-up album cover: a sunset over a dark horizon."""
        n = 240
        sky = _two_tone(n, (60, 20, 110), (255, 120, 70), _vertical(n))
        d = ImageDraw.Draw(sky)
        d.ellipse([70, 70, 170, 170], fill=(255, 226, 150))
        d.rectangle([0, 150, n, n], fill=(24, 12, 48))
        for i, y in enumerate(range(156, n, 12)):
            d.line([(0, y + i * 2), (n, y + i * 2)], fill=(255, 90, 160), width=2)
        buf = io.BytesIO()
        sky.save(buf, "JPEG", quality=90)
        return buf.getvalue()

    def spotify(self):
        if not self.music:
            return None
        return {"key": "demo", "title": "Bury the Light", "artist": "Casey Edwards",
                "playing": self.playing, "pos": 75.0, "dur": 580.0, "rem": 505, "cover": self.cover}

    def volume(self):
        return (self.vol, self.muted)

    def cpu(self):
        return self._cpu

    def ram(self):
        return self._ram
