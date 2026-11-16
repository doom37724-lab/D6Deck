"""
d6fun - the fun side of D6Deck: screen savers, mini-games, toys and sound effects.

Everything here draws through the Screen the app hands over (see SPEC.md) and never talks
to the device itself. The one rule that shapes every design: sending a picture to one key
takes about 30 ms, so every animation changes only a few keys per step.

    make_saver(screen)       -> a screen saver (or None for "dark")
    make_game(name, screen)  -> a mini-game
    toy(name, screen)        -> a short animation for one key (dice, coin, 8-ball)
    sound(name, cfg)         -> a tiny sound effect
"""
import bisect
import colorsys
import json
import math
import os
import random
import struct
import tempfile
import time
import wave
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import d6look as look

BASE = Path(__file__).resolve().parent   # the app folder (where screensaver.gif would be)
COLS, ROWS, NKEYS = 5, 3, 15
ALL_KEYS = range(1, NKEYS + 1)

SAVER_STYLES = {
    "auto": "My picture",
    "rainbow": "Rainbow",
    "matrix": "Matrix rain",
    "bounce": "Bouncing logo",
    "clock": "Big clock",
    "dark": "Dark",
}
GAMES = {
    "whack": {"label": "Whack", "icon": "🐹", "color": "#8a5a1c"},
    "simon": {"label": "Simon", "icon": "🧠", "color": "#8a2a6a"},
    "lights": {"label": "Lights Out", "icon": "💡", "color": "#8a7a10"},
    "reaction": {"label": "Reaction", "icon": "⚡", "color": "#a35a00"},
    "memory": {"label": "Memory", "icon": "🃏", "color": "#1b6ac9"},
    "party": {"label": "Party", "icon": "🎉", "color": "#a3214a"},
}
TOYS = {
    "dice": {"label": "Dice", "icon": "🎲", "color": "#8b2e2e"},
    "coin": {"label": "Coin", "icon": "🪙", "color": "#9a6700"},
    "8ball": {"label": "8-Ball", "icon": "🎱", "color": "#3a3f8c"},
}


# ----------------------------------------------------------------- small helpers
def _col(pos):
    return (pos - 1) % COLS


def _row(pos):
    return (pos - 1) // COLS


def _hsv(h, s=0.85, v=1.0):
    """A rainbow colour as (r, g, b); h goes round and round (0..1)."""
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


def _dim(rgb, f):
    return tuple(int(c * f) for c in rgb)


_tile_cache = {}


def _tile(cfg, **parts):
    """look.tile(), remembered: games show the same few pictures again and again."""
    im = cfg["image"]
    key = (im["size"], im.get("rotate"), im.get("flip"), cfg.get("theme"),
           tuple(sorted(parts.items())))
    if key not in _tile_cache:
        if len(_tile_cache) > 500:   # scores and times make new pictures; don't grow forever
            _tile_cache.clear()
        _tile_cache[key] = look.tile(cfg, **parts)
    return _tile_cache[key]


def _fit_font(draw, lines, max_w, max_h, start, kind="label"):
    """The biggest font (from `start` px down) at which all the lines fit the box."""
    size = start
    while size > 8:
        f = look.font(size, kind)
        if (max(draw.textlength(l, font=f) for l in lines) <= max_w
                and len(lines) * size * 1.2 <= max_h):
            break
        size -= 1
    return look.font(size, kind), size


def _draw_lines(img, lines, fill, cy=None, start=22, max_w=None, max_h=None):
    """Centred block of text lines, shrunk until it fits."""
    s = img.width
    d = ImageDraw.Draw(img)
    f, size = _fit_font(d, lines, max_w or s - 12, max_h or s - 14, start)
    step = size * 1.2
    y = (s / 2 if cy is None else cy) - step * (len(lines) - 1) / 2
    for line in lines:
        d.text((s / 2, y), line, font=f, fill=fill, anchor="mm")
        y += step


# ================================================================= sound effects
# Each sound is a few notes: (pitch in Hz, seconds). They are written once as small WAV
# files in the temp folder and then played by Windows in the background.
SOUNDS = {
    "blip": [(880, 0.05)],
    "pop": [(520, 0.04), (780, 0.07)],
    "good": [(660, 0.07), (990, 0.12)],
    "bad": [(196, 0.12), (147, 0.20)],
    "go": [(1175, 0.09)],
    "win": [(523, 0.09), (659, 0.09), (784, 0.09), (1047, 0.24)],
    "lose": [(392, 0.13), (330, 0.13), (262, 0.28)],
    # one note per Simon pad (a friendly five-note scale, so any order sounds like a tune)
    "pad0": [(262, 0.26)], "pad1": [(294, 0.26)], "pad2": [(330, 0.26)],
    "pad3": [(392, 0.26)], "pad4": [(440, 0.26)], "pad5": [(523, 0.26)],
}
SOUND_RATE = 22050
SOUND_VOLUME = 0.25   # quiet on purpose
SOUND_DIR = Path(tempfile.gettempdir()) / "d6deck_sounds_v1"


def _note_samples(freq, seconds):
    """One soft note: a sine wave that fades in fast and rings out (so there are no clicks)."""
    n = int(SOUND_RATE * seconds)
    out = []
    for i in range(n):
        t = i / SOUND_RATE
        fade = min(1.0, t / 0.006, (seconds - t) / 0.025)
        ring = math.exp(-2.2 * t / seconds)
        w = 2 * math.pi * freq * t
        v = (math.sin(w) + 0.25 * math.sin(2 * w)) / 1.25
        out.append(int(32767 * SOUND_VOLUME * fade * ring * v))
    return out


def sound_file(name):
    """Path of the WAV file for a sound (made the first time it is needed); None if unknown."""
    notes = SOUNDS.get(name)
    if not notes:
        return None
    path = SOUND_DIR / f"{name}.wav"
    if not path.exists():
        SOUND_DIR.mkdir(parents=True, exist_ok=True)
        samples = [v for freq, seconds in notes for v in _note_samples(freq, seconds)]
        tmp = path.with_suffix(".tmp")
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SOUND_RATE)
            w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        os.replace(tmp, path)
    return path


def sound(name, cfg):
    """Play a short sound effect in the background. Never waits and never fails:
    it simply does nothing when sounds are off, off Windows, or if anything goes wrong."""
    try:
        if not cfg.get("sounds", True):
            return
        import winsound
        path = sound_file(name)
        if path:
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC
                               | winsound.SND_NODEFAULT)
    except Exception:
        pass


# ================================================================= screen savers
MIN_BRIGHTNESS = 10
SAVER_NAMES = ("screensaver.gif", "screensaver.webp", "screensaver.png",
               "screensaver.jpg", "screensaver.jpeg")
SAVER_MAX_FRAMES = 150  # longer animations are thinned out evenly to stay light
_wall_cache = {}        # the sliced-up picture/GIF, kept so the next Screen Off is instant


def saver_brightness(cfg):
    """Screen saver brightness: never brighter than the deck itself, never fully dark."""
    want = int(cfg.get("screensaver", {}).get("brightness", 35))
    return max(MIN_BRIGHTNESS, min(want, int(cfg["brightness"])))


def saver_file(cfg, log=None):
    """The owner's own screen saver picture/GIF, or None if there isn't one."""
    name = (cfg.get("screensaver", {}).get("file") or "").strip()
    if name:
        p = Path(os.path.expandvars(name))
        p = p if p.is_absolute() else BASE / p
        if p.exists():
            return p
        if log:
            log(f"Screen saver file not found: {p}")
        return None
    for n in SAVER_NAMES:
        if (BASE / n).exists():
            return BASE / n
    return None


def load_wall(path, cfg):
    """Cut a picture or animation into 15 key-sized tiles per frame.
    Returns (frames, seconds): frames = [{pos: JPEG}], seconds = how long each one shows.
    Tiles that look identical share one JPEG, so parts that don't move are never re-sent."""
    st = path.stat()
    key = (str(path), st.st_mtime, st.st_size, json.dumps(cfg["image"], sort_keys=True))
    if _wall_cache.get("key") == key:
        return _wall_cache["frames"], _wall_cache["seconds"]
    frames, seconds, seen = [], [], {}
    with Image.open(path) as im:
        n = getattr(im, "n_frames", 1)
        keep_every = max(1, math.ceil(n / SAVER_MAX_FRAMES))
        for i in range(n):
            im.seek(i)
            dur = (im.info.get("duration") or 100) / 1000
            if i % keep_every:  # skipped frame: the one before it just stays up longer
                seconds[-1] += dur
                continue
            frames.append(look.slice_wall(im.convert("RGB"), cfg, seen))
            seconds.append(dur)
    _wall_cache.update(key=key, frames=frames, seconds=seconds)
    return frames, seconds


class Saver:
    """What every screen saver shares. `now` always comes from the app (time.monotonic())."""
    wait_ms = 100

    def __init__(self, screen):
        self.screen, self.cfg = screen, screen.cfg
        self.t0 = None   # time of the first tick; animations count from there

    def brightness(self):
        return saver_brightness(self.cfg)

    def _elapsed(self, now):
        if self.t0 is None:
            self.t0 = now
        return now - self.t0


class WallSaver(Saver):
    """Your own picture or GIF stretched across all 15 keys like one big screen.
    A still picture is sent once and then costs nothing. A GIF plays at its real speed;
    if the D6 can't keep up, frames are skipped instead of slowing down."""

    def __init__(self, screen, frames, seconds):
        super().__init__(screen)
        self.frames = frames
        self.ends = []
        total = 0.0
        for sec in seconds:
            total += max(0.02, sec)
            self.ends.append(total)
        self.total = total
        self.shown = {}  # pos -> the JPEG on that key now
        self.idx = 0
        self.rest_until = 0.0
        self.wait_ms = 100 if len(frames) == 1 else 15

    def _show(self, idx):
        t0 = time.monotonic()
        for pos, jpeg in self.frames[idx].items():
            if self.shown.get(pos) is not jpeg:
                self.screen.put(pos, jpeg)
                self.shown[pos] = jpeg
        self.screen.flush()
        self.idx = idx
        # Give the D6 a rest as long as the drawing took (at least 0.1 s), so it is never
        # drawing non-stop: that is when it stops noticing key presses.
        spent = time.monotonic() - t0
        self.rest_until = time.monotonic() + max(0.1, spent)

    def start(self):
        self._show(0)

    def tick(self, now):
        if len(self.frames) == 1 or now < self.rest_until:
            return
        t = self._elapsed(now) % self.total
        idx = min(bisect.bisect_right(self.ends, t), len(self.frames) - 1)
        if idx != self.idx:
            self._show(idx)


class RainbowSaver(Saver):
    """A slow rainbow wave rolling across all 15 keys, with the time on one key that hops
    to a new spot now and then. Only one key is refreshed per tick."""
    wait_ms = 80
    HUE_STEPS = 72        # colours in the rainbow (each tile picture is made once, then reused)
    CYCLE_SECONDS = 40    # time for a key to go through the whole rainbow
    CLOCK_HOP_SECONDS = 20
    # Keys are refreshed corner to corner, so the colours roll like a wave.
    ORDER = sorted(ALL_KEYS, key=lambda p: (_col(p) + _row(p), p))

    def __init__(self, screen):
        super().__init__(screen)
        self.shown = {}  # pos -> what is on that key now, to skip pointless redraws
        self.i = 0
        self.clock_pos = random.randint(1, NKEYS)
        self.next_hop = self.CLOCK_HOP_SECONDS

    def _draw(self, pos, t):
        hue = t / self.CYCLE_SECONDS + (_col(pos) + _row(pos)) * 0.055
        step = int(hue * self.HUE_STEPS) % self.HUE_STEPS
        if pos == self.clock_pos:
            text = datetime.now().strftime("%I:%M").lstrip("0")
            what = (step, text)
            if self.shown.get(pos) != what:
                self.screen.put(pos, look.tile(
                    self.cfg, color=_hex(_hsv(step / self.HUE_STEPS, 0.78, 0.3)),
                    big=text, sub=datetime.now().strftime("%p")))
        else:
            what = step
            if self.shown.get(pos) != what:
                self.screen.put(pos, look.plain(self.cfg, _hsv(step / self.HUE_STEPS, 0.78, 0.6)))
        self.shown[pos] = what

    def start(self):
        for pos in self.ORDER:
            self._draw(pos, 0.0)
        self.screen.flush()

    def tick(self, now):
        t = self._elapsed(now)
        if t >= self.next_hop:
            old, self.next_hop = self.clock_pos, t + self.CLOCK_HOP_SECONDS
            self.clock_pos = random.choice([p for p in ALL_KEYS if p != old])
            self._draw(old, t)
            self._draw(self.clock_pos, t)
        self._draw(self.ORDER[self.i], t)
        self.i = (self.i + 1) % NKEYS
        self.screen.flush()


class MatrixSaver(Saver):
    """Green "digital rain". Every key is covered in faint green letters; in each of the 5
    columns a bright drop falls down the 3 keys with a glowing tail behind it, then that
    column rests for a moment. A step of one drop changes at most 3 keys, and at most 2
    drops step per tick."""
    wait_ms = 50
    LEVELS = ("head", "tail", "faint")       # how bright a key is: the drop, just behind it, at rest
    SHADE = {"head": 1.0, "tail": 0.62, "faint": 0.3}
    VARIANTS = 4                              # different letter patterns per brightness
    GLYPHS = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾅﾆﾇﾈﾊﾋﾎﾏﾐﾑﾒﾓﾔﾕﾗﾘﾜ0123456789"

    def __init__(self, screen):
        super().__init__(screen)
        self.pics = {}
        rng = random.Random(6)
        for level in self.LEVELS:
            self.pics[level] = [self._picture(level, rng) for _ in range(self.VARIANTS)]
        self.level = {}                                   # pos -> level name
        self.head = [random.randint(-1, 3) for _ in range(COLS)]   # row of each column's drop
        self.next = [random.uniform(0.2, 1.5) for _ in range(COLS)]
        self.speed = [random.uniform(0.32, 0.5) for _ in range(COLS)]

    def _glyph_font(self, size):
        """A font that has the Japanese letters of the film; plain digits if there is none."""
        for name in ("YuGothB.ttc", "meiryob.ttc", "msgothic.ttc"):
            try:
                return ImageFont.truetype(name, size), self.GLYPHS
            except OSError:
                continue
        return look.font(size, "number"), "0123456789"

    def _picture(self, level, rng):
        s = self.cfg["image"]["size"]
        img = Image.new("RGB", (s, s), (0, 0, 0))
        d = ImageDraw.Draw(img)
        n = 4
        cell = s / n
        font, glyphs = self._glyph_font(int(cell * 0.92))
        for r in range(n):
            for c in range(n):
                if rng.random() < 0.12:
                    continue   # a few gaps look more like rain
                shade = self.SHADE[level] * (0.35 + 0.65 * r / (n - 1)) * rng.uniform(0.75, 1.0)
                fill = (int(40 * shade), int(255 * shade), int(95 * shade))
                if level == "head" and r == n - 1:
                    fill = (205, 255, 215)   # the leading edge of the drop is almost white
                d.text((c * cell + cell / 2, r * cell + cell / 2), rng.choice(glyphs),
                       font=font, fill=fill, anchor="mm")
        return look.finalize(img, self.cfg)

    def _paint(self, col):
        """Show column `col` for where its drop is now; only keys whose brightness changed."""
        for r in range(ROWS):
            pos = r * COLS + col + 1
            behind = self.head[col] - r
            level = self.LEVELS[behind] if behind in (0, 1) else "faint"
            if self.level.get(pos) != level:
                self.level[pos] = level
                self.screen.put(pos, random.choice(self.pics[level]))

    def start(self):
        for col in range(COLS):
            self._paint(col)
        self.screen.flush()

    def tick(self, now):
        t = self._elapsed(now)
        stepped = 0
        for col in range(COLS):
            if t < self.next[col] or stepped == 2:
                continue
            stepped += 1
            self.head[col] += 1
            self.next[col] = t + self.speed[col]
            if self.head[col] > ROWS:   # drop and tail have left the bottom: rest a while
                self.head[col] = -1
                self.next[col] = t + random.uniform(0.4, 2.6)
                self.speed[col] = random.uniform(0.32, 0.5)
            self._paint(col)
        self.screen.flush()


class BounceSaver(Saver):
    """A little "D6" logo drifting around the whole wall of keys like the DVD logo, taking a
    new colour every time it bounces off an edge. Only the keys it touches are re-sent."""
    wait_ms = 100
    STEP = 0.2                    # seconds between moves
    SPEED = (47.0, 31.0)          # pixels per second, across and down
    COLORS = [(255, 70, 70), (255, 160, 30), (250, 225, 50), (70, 220, 100),
              (60, 200, 255), (110, 120, 255), (225, 90, 240)]

    def __init__(self, screen):
        super().__init__(screen)
        s = self.cfg["image"]["size"]
        self.w, self.h = look.wall_size(self.cfg)
        self.lw, self.lh = int(s * 0.74), int(s * 0.5)
        self.x0 = random.uniform(0, self.w - self.lw)
        self.y0 = random.uniform(0, self.h - self.lh)
        self.logos = {}
        self.seen = {}
        self.k = -1

    def _logo(self, rgb):
        """The logo in one colour: a glowing pill with "D6" on it (drawn big, shrunk smooth)."""
        if rgb not in self.logos:
            z = 4
            im = Image.new("RGB", (self.lw * z, self.lh * z), (0, 0, 0))
            d = ImageDraw.Draw(im)
            d.rounded_rectangle([z, z, self.lw * z - z - 1, self.lh * z - z - 1],
                                radius=self.lh * z // 2, fill=rgb)
            d.rounded_rectangle([5 * z, 5 * z, self.lw * z - 5 * z - 1, self.lh * z - 5 * z - 1],
                                radius=self.lh * z // 2 - 4 * z, fill=_dim(rgb, 0.16))
            d.text((self.lw * z / 2, self.lh * z / 2), "D6", fill=rgb, anchor="mm",
                   font=look.font(int(self.lh * z * 0.6), "number"))
            self.logos[rgb] = im.resize((self.lw, self.lh), Image.Resampling.LANCZOS)
        return self.logos[rgb]

    @staticmethod
    def _bounce(dist, span):
        """Where something that has travelled `dist` is inside 0..span, and how often it hit an edge."""
        m = dist % (2 * span)
        return (m if m <= span else 2 * span - m), int(dist // span)

    def _frame(self, k):
        t = k * self.STEP
        x, hits_x = self._bounce(self.x0 + self.SPEED[0] * t, self.w - self.lw)
        y, hits_y = self._bounce(self.y0 + self.SPEED[1] * t, self.h - self.lh)
        wall = Image.new("RGB", (self.w, self.h), (0, 0, 0))
        wall.paste(self._logo(self.COLORS[(hits_x + hits_y) % len(self.COLORS)]), (int(x), int(y)))
        if len(self.seen) > 400:   # every position makes new tiles; don't keep them forever
            self.seen.clear()
        self.screen.fill(look.slice_wall(wall, self.cfg, self.seen))

    def start(self):
        self._frame(0)

    def tick(self, now):
        k = int(self._elapsed(now) / self.STEP)
        if k != self.k:
            self.k = k
            self._frame(k)


class ClockSaver(Saver):
    """The time in huge digits across the middle row, one digit per key, in a colour that
    drifts slowly through the rainbow. Almost free: a key is only re-sent when its digit
    changes, or about every 2 seconds when one digit takes the next shade."""
    wait_ms = 250
    SHADES = 90            # steps round the rainbow
    SHADE_SECONDS = 10     # each digit moves one shade this often (the digits take turns)
    DIGIT_KEYS = (6, 7, 8, 9, 10)
    DATE_KEY = 13

    def __init__(self, screen):
        super().__init__(screen)
        self.clock = datetime.now     # tests swap this for a made-up time
        self.pics = {}
        self.black = look.black_jpeg(self.cfg)

    def _digit(self, ch, shade):
        if (ch, shade) not in self.pics:
            if len(self.pics) > 300:
                self.pics.clear()
            s = self.cfg["image"]["size"]
            img = Image.new("RGB", (s, s), (0, 0, 0))
            ImageDraw.Draw(img).text((s / 2, s / 2), ch, anchor="mm",
                                     font=look.font(int(s * 0.95), "number"),
                                     fill=_hsv(shade / self.SHADES, 0.7, 1.0))
            self.pics[(ch, shade)] = look.finalize(img, self.cfg)
        return self.pics[(ch, shade)]

    def _date(self, when):
        key = when.strftime("%p %a %d %b")
        if key not in self.pics:
            s = self.cfg["image"]["size"]
            img = Image.new("RGB", (s, s), (0, 0, 0))
            _draw_lines(img, [when.strftime("%p"), when.strftime("%a %d %b").replace(" 0", " ")],
                        (120, 128, 140), start=int(s * 0.2))
            self.pics[key] = look.finalize(img, self.cfg)
        return self.pics[key]

    def _draw(self, t):
        when = self.clock()
        text = when.strftime("%I:%M")
        if text[0] == "0":
            text = " " + text[1:]
        for i, (pos, ch) in enumerate(zip(self.DIGIT_KEYS, text)):
            # each key is one fifth of a step ahead of its neighbour, so they change in turn
            shade = int(t / self.SHADE_SECONDS + i / 5) + i * 3
            self.screen.put(pos, self.black if ch == " " else self._digit(ch, shade % self.SHADES))
        self.screen.put(self.DATE_KEY, self._date(when))
        self.screen.flush()

    def start(self):
        for pos in ALL_KEYS:
            self.screen.put(pos, self.black)
        self._draw(0.0)

    def tick(self, now):
        self._draw(self._elapsed(now))


def make_saver(screen):
    """The screen saver the owner picked (cfg["screensaver"]["style"]); None means stay dark."""
    cfg = screen.cfg
    style = cfg.get("screensaver", {}).get("style", "auto")
    if style == "dark":
        return None
    simple = {"rainbow": RainbowSaver, "matrix": MatrixSaver, "bounce": BounceSaver,
              "clock": ClockSaver}
    if style in simple:
        return simple[style](screen)
    path = saver_file(cfg, screen.log)   # "auto": their own picture, else the rainbow
    if path is not None:
        try:
            frames, seconds = load_wall(path, cfg)
            screen.log(f"Screen saver: {path.name} ({len(frames)} frame{'s' if len(frames) != 1 else ''})")
            return WallSaver(screen, frames, seconds)
        except Exception as e:
            screen.log(f"Could not use {path.name} as the screen saver ({e}); showing the rainbow")
    return RainbowSaver(screen)


# ================================================================= games
INFO_KEY, AGAIN_KEY, EXIT_KEY = 5, 10, 15          # the right-hand column
FIELD = [p for p in ALL_KEYS if _col(p) < 4]       # the 12 keys you play on
BACK_ROWS = [p for p in FIELD if _row(p) > 0]      # the 8 field keys under the result row
IDLE_SECONDS = 60          # nobody playing: give the deck back
RESULT_SECONDS = 20        # how long a result screen stays up
RESULT_GUARD = 0.7         # ignore presses this long after a game ends (you were still mashing)


class Game:
    """What every game shares: the info column, Exit / Play again, giving up when nobody
    plays, and the result screen with best scores.

    A game fills in new_game(now), on_press(pos, now) and on_tick(now), and only queues
    pictures with self.put(); this class sends them (one flush per press or tick)."""
    name = ""
    wait_ms = 40

    def __init__(self, screen):
        self.screen, self.cfg = screen, screen.cfg
        self.clock = time.monotonic   # tests swap this for a made-up clock
        self.done = False
        self.over = False             # True while the result screen is up
        self.over_at = 0.0
        self.last_press = 0.0
        self.sparkles = []            # keys lit by the "new best" celebration
        self.sparkle_next = 0.0
        self.sparkle_until = 0.0

    # --- drawing helpers
    def put(self, pos, jpeg):
        self.screen.put(pos, jpeg)

    def tile(self, **parts):
        return _tile(self.cfg, **parts)

    def plain(self, rgb):
        return look.plain(self.cfg, rgb)

    def black(self):
        return look.black_jpeg(self.cfg)

    def info(self, big, sub, **more):
        """The top-right key: score, time or level."""
        self.put(INFO_KEY, self.tile(color=GAMES[self.name]["color"], big=str(big), sub=sub, **more))

    def sound(self, name):
        sound(name, self.cfg)

    # --- what the app calls
    def start(self):
        now = self.clock()
        self.last_press = now
        self._begin(now)

    def _begin(self, now):
        self.over = False
        self.sparkles = []
        self.put(AGAIN_KEY, self.tile(color="#1f5f8a", icon="🔄", label="Restart"))
        self.put(EXIT_KEY, self.tile(color="#8b2e2e", icon="🚪", label="Exit"))
        self.new_game(now)
        self.screen.flush()

    def press(self, pos):
        if self.done:
            return
        now = self.clock()
        self.last_press = now
        if pos == EXIT_KEY:
            self.done = True
        elif pos == AGAIN_KEY:
            self.sound("blip")
            self._begin(now)
        elif self.over:
            if now - self.over_at >= RESULT_GUARD:
                self.done = True   # any other key on the result screen = back to the buttons
        elif pos in FIELD:
            self.on_press(pos, now)
            self.screen.flush()

    def tick(self, now):
        if self.done:
            return
        if self.over:
            waited, limit = now - max(self.last_press, self.over_at), RESULT_SECONDS
        else:
            waited, limit = now - self.last_press, IDLE_SECONDS
        if waited > limit:
            self.done = True
            return
        if self.over:
            self._sparkle(now)
        else:
            self.on_tick(now)
        self.screen.flush()

    # --- for the games to fill in
    def new_game(self, now):
        pass

    def on_press(self, pos, now):
        pass

    def on_tick(self, now):
        pass

    # --- the result screen
    def finish(self, now, score, headline, unit, lower_better=False, won=True):
        """Show the result: headline, score and best across the top row, the rest dark.
        A new best score is saved and celebrated."""
        bests = self.screen.state.setdefault("best", {})
        old = bests.get(self.name)
        if not isinstance(old, (int, float)):
            old = None
        new_best = score > 0 and (old is None or (score < old if lower_better else score > old))
        if new_best:
            bests[self.name] = score
            self.screen.save()
        best = bests.get(self.name)
        game = GAMES[self.name]
        self.put(1, self.tile(color=game["color"], icon=game["icon"], label=headline))
        self.put(2, self.tile(color="#22303f", big=str(score), sub=unit))
        self.put(3, self.tile(color="#22303f", big="--" if best is None else str(best), sub="best"))
        self.put(4, self.tile(color="#a37800", icon="🏆", label="New best!") if new_best
                 else self.black())
        for pos in BACK_ROWS:
            self.put(pos, self.black())
        self.put(AGAIN_KEY, self.tile(color="#1f7a3a", icon="🔄", label="Again"))
        self.sound("win" if (won or new_best) else "lose")
        self.over, self.over_at = True, now
        self.sparkle_until = now + 2.6 if new_best else 0.0
        self.sparkle_next = now + 0.3

    def _sparkle(self, now):
        """New best: little stars twinkle on the dark keys for a moment (4 keys per step)."""
        if now < self.sparkle_next or not (self.sparkles or now < self.sparkle_until):
            return
        self.sparkle_next = now + 0.16
        for pos in self.sparkles:
            self.put(pos, self.black())
        self.sparkles = []
        if now < self.sparkle_until:
            self.sparkles = random.sample(BACK_ROWS, 2)
            for pos in self.sparkles:
                self.put(pos, self.tile(color=_hex(_hsv(random.random(), 0.8, 0.55)),
                                        icon=random.choice("✨🎉⭐🎈")))


class Whack(Game):
    """Whack-a-mole: hamsters pop up, hit them before they duck. Bombs cost 3 points.
    It gets faster for 30 seconds."""
    name = "whack"
    wait_ms = 30
    ROUND = 30.0
    READY = 1.0            # a breath before the first hamster
    BOMB_COST = 3

    def new_game(self, now):
        self.score = 0
        self.up = {}       # pos -> (what is there: "mole" or "bomb", when it ducks)
        self.splat = {}    # pos -> when the "hit!" picture goes away
        self.t0 = now + self.READY
        self.next_spawn = self.t0
        self.hole = self.plain((38, 27, 18))
        for pos in FIELD:
            self.put(pos, self.hole)
        self._info(now)

    def _info(self, now):
        left = min(int(self.ROUND), max(0, math.ceil(self.t0 + self.ROUND - now)))
        self.info(self.score, f"{left} s", ring=left / self.ROUND, ring_color=(255, 190, 60))

    def on_press(self, pos, now):
        if pos not in self.up:
            return
        what, _ = self.up.pop(pos)
        if what == "mole":
            self.score += 1
            self.sound("pop")
            self.put(pos, self.tile(color="#1f7a3a", icon="💥"))
        else:
            self.score = max(0, self.score - self.BOMB_COST)
            self.sound("bad")
            self.put(pos, self.tile(color="#a11d1d", icon="💥", label=f"-{self.BOMB_COST}"))
        self.splat[pos] = now + 0.3
        self._info(now)

    def on_tick(self, now):
        played = now - self.t0
        if played >= self.ROUND:
            self.info(self.score, "points")
            self.finish(now, self.score, "Time's up!", "points")
            return
        for pos in [p for p, (_, until) in self.up.items() if now >= until]:
            del self.up[pos]
            self.put(pos, self.hole)
        for pos in [p for p, until in self.splat.items() if now >= until]:
            del self.splat[pos]
            self.put(pos, self.hole)
        most = 2 if played < 12 else 3
        if now >= self.next_spawn and len(self.up) < most:
            free = [p for p in FIELD if p not in self.up and p not in self.splat]
            pos = random.choice(free)
            bomb = played > 3 and random.random() < 0.16
            stay = max(0.6, 1.25 - 0.022 * played)
            self.up[pos] = ("bomb" if bomb else "mole", now + stay)
            self.put(pos, self.tile(color="#5a1f1f", icon="💣") if bomb
                     else self.tile(color="#c98a2b", icon="🐹"))
            self.next_spawn = now + max(0.3, 0.8 - 0.017 * played) * random.uniform(0.7, 1.2)
        self._info(now)


class Simon(Game):
    """Simon: six coloured pads in a ring play a sequence; repeat it. One step longer each round."""
    name = "simon"
    wait_ms = 30
    # pad key -> colour, clockwise round the ring; each pad also has its own note
    PADS = {2: (235, 60, 60), 3: (60, 120, 245), 9: (245, 205, 40),
            13: (60, 205, 95), 12: (245, 135, 30), 6: (175, 85, 235)}
    STATUS_KEY, STEPS_KEY = 7, 8

    def new_game(self, now):
        self.pads = list(self.PADS)
        self.seq = [random.choice(self.pads)]
        self.lit = None        # (pad, when it goes dark again)
        for pos in FIELD:
            self.put(pos, self._pad(pos, False) if pos in self.PADS else self.black())
        self._watch(now + 0.8)

    def _pad(self, pos, on):
        return self.plain(self.PADS[pos] if on else _dim(self.PADS[pos], 0.3))

    def _light(self, pos, until):
        if self.lit and self.lit[0] != pos:
            self.put(self.lit[0], self._pad(self.lit[0], False))
        self.put(pos, self._pad(pos, True))
        self.lit = (pos, until)

    def _watch(self, when):
        """Start showing the sequence at time `when`."""
        self.phase, self.i, self.next = "watch", 0, when
        level = len(self.seq)
        self.on_time = max(0.22, 0.5 - 0.02 * level)
        self.gap = max(0.1, 0.2 - 0.008 * level)
        self.info(level, "level")
        self.put(self.STATUS_KEY, self.tile(color="#3d4451", icon="👀", label="Watch"))
        self.put(self.STEPS_KEY, self.tile(color="#22303f", big=str(level),
                                           sub="step" if level == 1 else "steps"))

    def on_tick(self, now):
        if self.lit and now >= self.lit[1]:
            self.put(self.lit[0], self._pad(self.lit[0], False))
            self.lit = None
            if self.phase == "watch":
                self.next = now + self.gap
        if self.phase == "watch" and not self.lit and now >= self.next:
            if self.i == len(self.seq):
                self.phase, self.i = "play", 0
                self.put(self.STATUS_KEY, self.tile(color="#1f7a3a", icon="👉", label="Your turn"))
                self.put(self.STEPS_KEY, self.tile(color="#22303f", big=f"0/{len(self.seq)}", sub="done"))
            else:
                pad = self.seq[self.i]
                self.i += 1
                self._light(pad, now + self.on_time)
                self.sound(f"pad{self.pads.index(pad)}")
        elif self.phase == "next" and now >= self.next:
            self.seq.append(random.choice(self.pads))
            self._watch(now)

    def on_press(self, pos, now):
        if self.phase != "play" or pos not in self.PADS:
            return
        self._light(pos, now + 0.22)
        if pos != self.seq[self.i]:
            self.info(f"Lv {len(self.seq)}", "reached")
            self.finish(now, len(self.seq) - 1, "Game over", "rounds", won=False)
            return
        self.i += 1
        self.put(self.STEPS_KEY, self.tile(color="#22303f", big=f"{self.i}/{len(self.seq)}", sub="done"))
        if self.i == len(self.seq):
            self.sound("good")
            self.phase, self.next = "next", now + 0.9
            self.put(self.STATUS_KEY, self.tile(color="#1f7a3a", icon="✅", label="Nice!"))
        else:
            self.sound(f"pad{self.pads.index(pos)}")


class LightsOut(Game):
    """Lights Out: pressing a key flips it and the keys above, below, left and right of it.
    Turn every light off in as few moves as you can."""
    name = "lights"
    wait_ms = 100

    def new_game(self, now):
        self.on = set()
        self.moves = 0
        # Start from "all off" and press random keys: that can always be undone, so every
        # puzzle is solvable.
        while len(self.on) < 3:
            for pos in random.sample(FIELD, random.randint(4, 7)):
                self._flip(pos)
        for pos in FIELD:
            self._show(pos)
        self.info(0, "moves")

    def _flip(self, pos):
        """Flip a key and its neighbours; returns the keys that changed."""
        c, r = _col(pos), _row(pos)
        changed = [pos]
        for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if 0 <= c + dc < 4 and 0 <= r + dr < ROWS:
                changed.append((r + dr) * COLS + c + dc + 1)
        self.on.symmetric_difference_update(changed)
        return changed

    def _show(self, pos):
        self.put(pos, self.tile(color="#d1a000", icon="💡") if pos in self.on
                 else self.plain((22, 24, 34)))

    def on_press(self, pos, now):
        self.moves += 1
        for p in self._flip(pos):
            self._show(p)
        self.info(self.moves, "move" if self.moves == 1 else "moves")
        if self.on:
            self.sound("blip")
        else:
            self.finish(now, self.moves, "Solved!", "moves", lower_better=True)


class Reaction(Game):
    """Reaction test: wait... one key lights up, hit it as fast as you can. Five goes,
    then your average in milliseconds (lower is better)."""
    name = "reaction"
    wait_ms = 15
    ROUNDS = 5

    def new_game(self, now):
        self.times = []
        self.early = None      # (key showing "Too soon!", when it clears)
        self.target = None
        self.dark = self.plain((20, 22, 30))
        for pos in FIELD:
            self.put(pos, self.dark)
        self._arm(now)

    def _arm(self, now):
        """Wait a random while before lighting the next key, so it can't be guessed."""
        self.phase, self.go_at = "wait", now + random.uniform(1.5, 4.0)
        self.info(f"{len(self.times) + 1}/{self.ROUNDS}", "wait for it")

    def on_tick(self, now):
        if self.early and now >= self.early[1]:
            self.put(self.early[0], self.dark)
            self.early = None
        if self.phase == "wait" and now >= self.go_at:
            if self.early:
                self.put(self.early[0], self.dark)
                self.early = None
            self.target = random.choice(FIELD)
            self.put(self.target, self.tile(color="#19c44a", icon="⚡"))
            self.info(f"{len(self.times) + 1}/{self.ROUNDS}", "hit it!")
            self.screen.flush()
            self.sound("go")
            self.lit_at = self.clock()   # the clock starts when the picture is really out
            self.phase = "lit"
        elif self.phase == "shown" and now >= self.next:
            self.put(self.target, self.dark)
            if len(self.times) == self.ROUNDS:
                avg = round(sum(self.times) / len(self.times))
                self.info(avg, "ms avg")
                self.finish(now, avg, "Done!", "ms avg", lower_better=True)
            else:
                self._arm(now)

    def on_press(self, pos, now):
        if self.phase == "wait":
            if self.early and self.early[0] != pos:
                self.put(self.early[0], self.dark)
            self.put(pos, self.tile(color="#a11d1d", big="Oops", sub="too soon!"))
            self.early = (pos, now + 0.9)
            self.sound("bad")
            self._arm(now)
        elif self.phase == "lit" and pos == self.target:
            ms = max(1, round((now - self.lit_at) * 1000))
            self.times.append(ms)
            self.put(pos, self.tile(color="#1f7a3a", big=str(ms), sub="ms"))
            self.info(f"{len(self.times)}/{self.ROUNDS}", "nice!")
            self.sound("good")
            self.phase, self.next = "shown", now + 1.3


class Memory(Game):
    """Memory: 12 face-down cards hide 6 pairs. Flip two; pairs stay up. Fewest moves wins."""
    name = "memory"
    wait_ms = 100
    FACES = "🐶🐱🦊🐸🐵🐼🦄🐙🍕🍩🍓🚀⚽🎸🌈⭐"

    def new_game(self, now):
        faces = random.sample(self.FACES, 6) * 2
        random.shuffle(faces)
        self.cards = dict(zip(FIELD, faces))
        self.matched = set()
        self.up = []           # cards face up that are not (yet) a pair
        self.hide_at = 0.0
        self.moves = 0
        self.back = self.tile(color="#274b8f", big="?")
        for pos in FIELD:
            self.put(pos, self.back)
        self.info(0, "moves")

    def _hide(self):
        for pos in self.up:
            self.put(pos, self.back)
        self.up = []

    def on_tick(self, now):
        if len(self.up) == 2 and now >= self.hide_at:
            self._hide()

    def on_press(self, pos, now):
        if pos in self.matched or pos in self.up:
            return
        if len(self.up) == 2:
            self._hide()       # didn't wait for the last two to turn back: do it now
        self.up.append(pos)
        self.put(pos, self.tile(color="#6b4fa0", icon=self.cards[pos]))
        if len(self.up) < 2:
            self.sound("blip")
            return
        self.moves += 1
        self.info(self.moves, "move" if self.moves == 1 else "moves")
        a, b = self.up
        if self.cards[a] != self.cards[b]:
            self.hide_at = now + 1.0
            self.sound("blip")
            return
        self.up = []
        self.matched.update((a, b))
        for p in (a, b):
            self.put(p, self.tile(color="#1f7a3a", icon=self.cards[p]))
        if len(self.matched) == len(FIELD):
            self.finish(now, self.moves, "All found!", "moves", lower_better=True)
        else:
            self.sound("good")


class Party(Game):
    """A four-second light show: rainbow stripes roll across the deck, then party emoji pop.
    It ends by itself; any key ends it early."""
    name = "party"
    wait_ms = 20
    STEP = 0.1
    WAVE_STEPS, POP_STEPS = 28, 12
    # keys grouped into slanted stripes (top-left to bottom-right), 1-3 keys each
    STRIPES = [[p for p in ALL_KEYS if _col(p) + _row(p) == d] for d in range(COLS + ROWS - 1)]

    def _begin(self, now):
        self.t0, self.k = now, 0
        for d, stripe in enumerate(self.STRIPES):
            for pos in stripe:
                self.put(pos, self.plain(_hsv(d / len(self.STRIPES))))
        self.screen.flush()
        self.sound("win")

    def press(self, pos):
        self.done = True

    def tick(self, now):
        if self.done:
            return
        k = int((now - self.t0) / self.STEP)
        if k == self.k:
            return
        self.k = k
        if k > self.WAVE_STEPS + self.POP_STEPS:
            self.done = True
        elif k <= self.WAVE_STEPS:
            # one stripe per step takes the next colour, so the bands keep rolling
            for pos in self.STRIPES[k % len(self.STRIPES)]:
                self.put(pos, self.plain(_hsv(k * 0.11 + 0.5)))
        else:
            for pos in random.sample(list(ALL_KEYS), 3):
                self.put(pos, self.tile(color=_hex(_hsv(random.random(), 0.85, 0.6)),
                                        icon=random.choice("🎉✨🎈🥳⭐🎊")))
        self.screen.flush()


_GAME_CLASSES = {"whack": Whack, "simon": Simon, "lights": LightsOut, "reaction": Reaction,
                 "memory": Memory, "party": Party}


def make_game(name, screen):
    """The mini-game called `name` (see GAMES), ready for start()."""
    return _GAME_CLASSES[name](screen)


# ================================================================= toys
TOY_STEP = 0.08    # seconds between animation frames
TOY_HOLD = 4.0     # how long the result stays on the key

EIGHT_BALL = {     # the classic answers: colour of the mood -> answers
    "#1f7a3a": ["It is certain", "Without a doubt", "Yes definitely", "You may rely on it",
                "As I see it, yes", "Most likely", "Outlook good", "Yes", "Signs point to yes",
                "It is decidedly so"],
    "#8a6a10": ["Reply hazy, try again", "Ask again later", "Better not tell you now",
                "Cannot predict now", "Concentrate and ask again"],
    "#8b2e2e": ["Don't count on it", "My reply is no", "My sources say no",
                "Outlook not so good", "Very doubtful"],
}
PIPS = {1: [(1, 1)], 2: [(0, 0), (2, 2)], 3: [(0, 0), (1, 1), (2, 2)],
        4: [(0, 0), (2, 0), (0, 2), (2, 2)], 5: [(0, 0), (2, 0), (1, 1), (0, 2), (2, 2)],
        6: [(0, 0), (2, 0), (0, 1), (2, 1), (0, 2), (2, 2)]}


def _die(px, face):
    """A white die showing `face`, px wide, with real pips (drawn big, shrunk smooth)."""
    z = 4
    n = px * z
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, n - 1, n - 1], radius=n // 6, fill=(250, 248, 240, 255),
                        outline=(190, 185, 175, 255), width=z)
    r = n * 0.095
    for cx, cy in PIPS[face]:
        x, y = n * (0.25 + 0.25 * cx), n * (0.25 + 0.25 * cy)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(30, 30, 36, 255))
    return im.resize((px, px), Image.Resampling.LANCZOS)


def _toy_dice(cfg):
    s = cfg["image"]["size"]
    pics = []
    faces = random.sample(range(1, 7), 5)
    result = random.randint(1, 6)
    for face, angle, dy in zip(faces, (-24, 18, -40, 28, -12), (-8, -14, -10, -4, 0)):
        img = look.tile_image(cfg, color=TOYS["dice"]["color"]).convert("RGB")
        die = _die(int(s * 0.5), face).rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
        img.paste(die, ((s - die.width) // 2, (s - die.height) // 2 + dy), die)
        pics.append(img)
    img = look.tile_image(cfg, color=TOYS["dice"]["color"]).convert("RGB")
    die = _die(int(s * 0.66), result)
    img.paste(die, ((s - die.width) // 2, (s - die.height) // 2), die)
    pics.append(img)
    return pics


def _coin(img, width, cy, side=None):
    """Draw a gold coin on img, squashed to `width` (0..1) as if seen while spinning."""
    s = img.width
    z = 4
    n = int(s * 0.6) * z
    coin = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(coin)
    d.ellipse([0, 0, n - 1, n - 1], fill=(214, 158, 26, 255))
    d.ellipse([n * 0.07, n * 0.07, n * 0.93, n * 0.93], fill=(248, 204, 72, 255))
    if side:
        d.text((n / 2, n / 2), side, anchor="mm", fill=(150, 100, 10, 255),
               font=look.font(int(n * 0.62), "number"))
    coin = coin.resize((max(3, int(n / z * width)), n // z), Image.Resampling.LANCZOS)
    img.paste(coin, ((s - coin.width) // 2, int(cy - coin.height / 2)), coin)


def _toy_coin(cfg):
    s = cfg["image"]["size"]
    heads = random.random() < 0.5
    pics = []
    # up in the air and down again, spinning
    for width, lift in ((1.0, 0.0), (0.5, 0.08), (0.1, 0.13), (0.55, 0.15), (1.0, 0.13),
                        (0.5, 0.08), (0.1, 0.03)):
        img = look.tile_image(cfg, color=TOYS["coin"]["color"]).convert("RGB")
        _coin(img, width, s * (0.5 - lift))
        pics.append(img)
    img = look.tile_image(cfg, color=TOYS["coin"]["color"]).convert("RGB")
    _coin(img, 1.0, s * 0.40, "H" if heads else "T")
    _draw_lines(img, ["HEADS" if heads else "TAILS"], (255, 255, 255), cy=s * 0.85,
                start=int(s * 0.2))
    pics.append(img)
    return pics


def _toy_8ball(cfg):
    s = cfg["image"]["size"]
    pics = []
    z = 4
    n = int(s * 0.74) * z
    ball = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(ball)
    d.ellipse([0, 0, n - 1, n - 1], fill=(12, 12, 16, 255), outline=(70, 70, 84, 255), width=z)
    d.ellipse([n * 0.27, n * 0.2, n * 0.73, n * 0.66], fill=(245, 245, 245, 255))
    d.text((n / 2, n * 0.43), "8", anchor="mm", fill=(12, 12, 16, 255),
           font=look.font(int(n * 0.36), "number"))
    ball = ball.resize((n // z, n // z), Image.Resampling.LANCZOS)
    for dx, dy in ((-7, 2), (7, -3), (-5, -4), (6, 4), (-3, 3), (0, 0)):   # shake, then settle
        img = look.tile_image(cfg, color=TOYS["8ball"]["color"]).convert("RGB")
        img.paste(ball, ((s - ball.width) // 2 + dx, (s - ball.height) // 2 + dy), ball)
        pics.append(img)
    color = random.choice([c for c, answers in EIGHT_BALL.items() for _ in answers])
    answer = random.choice(EIGHT_BALL[color])
    img = look.tile_image(cfg, color=color).convert("RGB")
    _draw_lines(img, _wrap(answer), (255, 255, 255), start=int(s * 0.24))
    pics.append(img)
    return pics


def _wrap(text, longest=9):
    """Break a short sentence into lines of about `longest` letters (whole words only)."""
    lines = []
    for word in text.split():
        if lines and len(lines[-1]) + 1 + len(word) <= longest:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines


_TOY_MAKERS = {"dice": _toy_dice, "coin": _toy_coin, "8ball": _toy_8ball}


def toy(name, screen):
    """A short animation for the pressed key: -> (frames, hold).
    frames = [(seconds after the press, JPEG), ...]; the last one is the result and stays
    up for `hold` seconds."""
    cfg = screen.cfg
    pics = _TOY_MAKERS[name](cfg)
    sound("blip", cfg)
    return [(i * TOY_STEP, look.finalize(img, cfg)) for i, img in enumerate(pics)], TOY_HOLD
