"""
d6ui - the D6Deck settings window.

A normal desktop window where people set up their deck by clicking instead of editing files:
the 15 keys as they really look, a panel to change the clicked key, and the deck's settings.
The app opens it with:

    d6ui.run(host)          # returns when the window is closed

`host` (see SPEC.md) is the only thing this file talks to: it loads and saves the buttons and
settings, starts/stops the deck, and so on. Nothing is written until "Save to deck".

Try it without the real deck:   py -3.14 d6ui.py --demo

How it is built:
  - Core        everything that is not the window: checking input, loading, saving, pictures.
  - Painter     a helper thread that draws the key pictures, so the window never freezes.
  - Window      the window itself (tkinter), plus a few small home-made dark widgets.
"""
import copy
import ctypes
import json
import os
import queue
import re
import sys
import threading
import time
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import colorchooser, filedialog, ttk

from PIL import Image, ImageDraw, ImageTk

import d6look as look

NKEYS = 15
MAX_PAGES = 8
ICON_TYPES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico")
SAVER_TYPES = (".gif", ".webp", ".png", ".jpg", ".jpeg")
PAGE_KEY = {"widget": "page", "color": "#3d4451", "action": {"type": "page", "to": "next"}}

# Used only until d6fun.py exists (the names come from SPEC.md).
FALLBACK_SAVERS = {"auto": "Automatic", "rainbow": "Rainbow", "matrix": "Matrix",
                   "bounce": "Bounce", "clock": "Clock", "dark": "Dark"}
FALLBACK_GAMES = {"whack": "Whack", "simon": "Simon", "lights": "Lights Out",
                  "reaction": "Reaction", "memory": "Memory", "party": "Party"}
FALLBACK_TOYS = {"dice": "Dice", "coin": "Coin", "8ball": "8-Ball"}

# What a key can do when pressed (SPEC.md "Actions"), in plain words, in menu order.
ACTIONS = [
    ("", "Nothing"),
    ("launch", "Open an app, file or link"),
    ("url", "Open a website"),
    ("launch_admin", "Open an app as administrator"),
    ("hotkey", "Press a keyboard shortcut"),
    ("text", "Type some text"),
    ("volume", "PC volume up / down"),
    ("mute", "Mute / unmute the PC"),
    ("previous_song", "Spotify: previous song"),
    ("spotify_like", "Spotify: like the current song"),
    ("obsidian", "Obsidian: notes"),
    ("page", "Go to another page"),
    ("brightness", "Deck brightness up / down"),
    ("sleep", "Screen off (shows the screen saver)"),
    ("theme", "Switch to the next look"),
    ("saver", "Switch to the next screen saver"),
    ("stopwatch", "Start / pause / reset the stopwatch"),
    ("game", "Play a mini-game"),
    ("toy", "Roll a die, flip a coin, ask the 8-ball"),
    ("game_mode", "Game mode: close memory-hungry apps"),
    ("command", "Run a command (advanced)"),
    ("restart", "Restart D6Deck"),
    ("quit", "Quit D6Deck"),
]
# The fields a freshly chosen action starts with.
NEW_ACTION = {"launch": {"target": ""}, "url": {"target": "https://"}, "launch_admin": {"target": ""},
              "hotkey": {"target": ""}, "text": {"target": ""}, "command": {"target": ""},
              "volume": {"delta": 4}, "brightness": {"delta": 10}, "page": {"to": "next"},
              "obsidian": {"do": "open"}}
# What an Obsidian key can do (the same list as d6deck.OBSIDIAN_DO).
OBSIDIAN_DO = [("open", "Open the vault"), ("daily", "Open today's daily note"),
               ("new", "Make a new note"), ("search", "Search the vault"),
               ("inbox", "Open the Inbox note"), ("clip", "Add the copied text to the Inbox")]
MEDIA_KEYS = [("media_play_pause", "Play / pause"), ("media_next", "Next song"),
              ("media_previous", "Previous song"), ("media_volume_up", "Volume up"),
              ("media_volume_down", "Volume down"), ("media_volume_mute", "Mute")]
EMOJI = ["🎮", "🎵", "🎧", "🎬", "📷", "💬", "📁", "🌐", "✉️", "⭐", "❤️", "🔥", "⚡", "💡",
         "🔒", "🔊", "🔇", "🎤", "🖥️", "⌨️", "🕹️", "🎲", "🏠", "🔍", "📝", "📌", "⏰", "☕",
         "🚀", "🐹", "🧠", "🃏", "🪙", "🎱", "🎉", "🌙"]


class Problem(Exception):
    """Something the person can understand and fix. Its text is shown as it is."""


# ================================================================= checking what was entered
def fun_lists():
    """Screen saver styles, games and toys as {name: label}. Falls back if d6fun is not there."""
    try:
        import d6fun
        savers = {n: str(label) for n, label in d6fun.SAVER_STYLES.items()}
        games = {n: str(g.get("label", n)) for n, g in d6fun.GAMES.items()}
        toys = {n: str(t.get("label", n)) for n, t in d6fun.TOYS.items()}
        return savers, games, toys
    except Exception:  # not written yet, or broken: the settings window must still open
        return dict(FALLBACK_SAVERS), dict(FALLBACK_GAMES), dict(FALLBACK_TOYS)


def whole_number(value, low, high, what):
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        raise Problem(f"{what} must be a number.")
    return max(low, min(high, n))


def clean_color(value):
    text = str(value).strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", text):
        text = "#" + "".join(c * 2 for c in text[1:])
    if not re.fullmatch(r"#[0-9a-f]{6}", text):
        raise Problem(f'"{value}" is not a colour. Colours look like #1f6feb.')
    return text


def inside(base, name):
    """`name` as a path inside the app folder, or None if it would point anywhere else."""
    if not isinstance(name, str) or not name.strip():
        return None
    base = Path(base).resolve()
    try:
        full = (base / name).resolve()
    except (OSError, ValueError):
        return None
    return full if full != base and full.is_relative_to(base) else None


def clean_action(action):
    """Check one "When pressed" setting. Fields we do not know are kept as they are."""
    if not isinstance(action, dict) or not isinstance(action.get("type"), str) or not action["type"]:
        return None
    a = dict(action)
    kind = a["type"]
    if kind in ("volume", "brightness"):
        a["delta"] = whole_number(a.get("delta", 0), -100, 100, "The amount")
    elif kind == "page":
        to = a.get("to", "next")
        a["to"] = to if to in ("next", "prev") else whole_number(to, 1, MAX_PAGES, "The page number")
    elif kind in ("launch", "launch_admin", "url", "hotkey", "text", "command"):
        a["target"] = str(a.get("target", ""))
        if kind == "hotkey":
            a["target"] = a["target"].strip().lower().replace(" ", "")
            if "repeat" in a:
                a["repeat"] = whole_number(a["repeat"], 1, 50, "How many times to press")
    elif kind in ("game", "toy"):
        a["name"] = str(a.get("name", ""))
    elif kind == "obsidian":
        a["do"] = a.get("do") if a.get("do") in dict(OBSIDIAN_DO) else "open"
        a["vault"] = str(a.get("vault", "")).strip()
        if not a["vault"]:
            a.pop("vault")
    return a


def clean_key(key, base):
    """Check one key. Returns the tidy key, or None when the key is empty."""
    if not isinstance(key, dict):
        raise Problem("A key was not understood. Please try again.")
    k = dict(key)
    if "color" in k:
        k["color"] = clean_color(k["color"])
    for name in ("label", "icon", "widget"):
        if name in k:
            k[name] = str(k[name]).strip()[:60]
            if not k[name]:
                del k[name]
    if "icon_file" in k:
        if not k["icon_file"]:
            del k["icon_file"]
        elif inside(base, k["icon_file"]) is None:
            raise Problem("A key picture must be inside the D6Deck folder. Please choose it again.")
    if "action" in k:
        action = clean_action(k["action"])
        if action is None:
            del k["action"]
        else:
            k["action"] = action
    has_something = any(n in k for n in ("label", "icon", "icon_file", "widget", "action"))
    return k if has_something else None


def clean_pages(pages, base):
    if not isinstance(pages, list) or not pages:
        raise Problem("There must be at least one page.")
    if len(pages) > MAX_PAGES:
        raise Problem(f"You can have up to {MAX_PAGES} pages.")
    out = []
    for page in pages:
        if not isinstance(page, dict):
            raise Problem("A page was not understood. Please try again.")
        tidy = {}
        for pos in sorted(page, key=lambda p: int(p) if str(p).isdigit() else 0):
            if not str(pos).isdigit() or not 1 <= int(pos) <= NKEYS:
                raise Problem(f"There is no key number {pos}. Keys are numbered 1 to {NKEYS}.")
            key = clean_key(page[pos], base)
            if key is not None:
                tidy[str(int(pos))] = key
        out.append(tidy)
    return out


def clean_settings(settings, defaults, saver_styles, base):
    """Check the settings. Anything missing or odd goes back to its factory value."""
    if not isinstance(settings, dict):
        raise Problem("The settings were not understood. Please try again.")
    s = {**defaults, **settings}
    if s.get("theme") not in look.THEMES:
        s["theme"] = defaults.get("theme", "neon")
    s["brightness"] = whole_number(s.get("brightness"), 10, 100, "Brightness")
    s["saver_brightness"] = whole_number(s.get("saver_brightness"), 10, 100, "Screen saver brightness")
    s["saver_enabled"] = bool(s.get("saver_enabled"))
    s["sounds"] = bool(s.get("sounds"))
    if s.get("saver_style") not in saver_styles:
        s["saver_style"] = defaults.get("saver_style", "auto")
    if not s.get("saver_file") or inside(base, s["saver_file"]) is None:
        s["saver_file"] = ""
    return s


# ================================================================= Core: everything but the window
class Core:
    """Loading, saving and pictures. No window here, so it can be tested on its own."""

    def __init__(self, host):
        self.host = host
        self.base = Path(host.base)
        self.savers, self.games, self.toys = fun_lists()
        self.live = look.DemoLive()
        self.pictures = {}   # picture id -> PIL image (drawing is the slow part)

    def factory(self):
        d = self.host.defaults()
        return copy.deepcopy(d["pages"]), dict(d["settings"])

    def check(self, pages, settings):
        """Tidy pages and settings, or raise Problem with a readable message."""
        return (clean_pages(pages, self.base),
                clean_settings(settings, self.host.defaults()["settings"], self.savers, self.base))

    def load(self):
        """What is on disk now. A damaged file falls back to the factory look, never a crash."""
        data = self.host.load() or {}
        pages, settings = self.factory()
        try:
            pages = clean_pages(data.get("pages"), self.base)
        except Problem:
            pages = clean_pages(pages, self.base)
        try:
            settings = clean_settings(data.get("settings"), settings, self.savers, self.base)
        except Problem:
            pass
        return pages, settings

    def save(self, pages, settings):
        pages, settings = self.check(pages, settings)
        self.host.save(pages, settings)
        return pages, settings

    # --- pictures (called from the Painter thread)
    @staticmethod
    def picture_id(key, settings, page, npages, size):
        """Names everything a key's picture depends on. The minute is in it for clocks."""
        return json.dumps([key, settings.get("theme"), settings.get("brightness"),
                           settings.get("saver_style"), page, npages, size, int(time.time() // 60)],
                          sort_keys=True, ensure_ascii=False)

    def draw(self, key, cfg, pid):
        if pid not in self.pictures:
            if len(self.pictures) > 600:
                self.pictures.clear()
            shown = dict(key)
            if shown.get("icon_file"):   # full path, so it is found whatever folder d6look uses
                shown["icon_file"] = str(self.base / shown["icon_file"])
            try:
                img = look.render_image(shown, cfg, self.live).convert("RGB")
            except Exception:   # a half-typed key must not stop the others being drawn
                traceback.print_exc()
                s = cfg["image"]["size"]
                img = Image.new("RGB", (s, s), (40, 44, 54))
            self.pictures[pid] = img
        return self.pictures[pid]

    def job_deck(self, pages, settings, page, size):
        """Pictures of every key on one page -> {pos: (picture id, PIL image)}."""
        page = max(0, min(page, len(pages) - 1))
        cfg = self.host.preview_cfg(settings, pages, page, size)
        return {pos: (pid, self.draw(key, cfg, pid))
                for pos, key in pages[page].items()
                for pid in [self.picture_id(key, settings, page, len(pages), size)]}

    def job_themes(self, pages, settings, size):
        """A short row of real keys drawn in every look, for the theme cards."""
        sample = [k for page in pages for k in page.values()][:4]
        out = {}
        for theme in look.THEMES:
            s = {**settings, "theme": theme}
            cfg = self.host.preview_cfg(s, pages, 0, size)
            out[theme] = [self.draw(k, cfg, self.picture_id(k, s, 0, len(pages), size)) for k in sample]
        return out

    def job_icons(self, px):
        """Small pictures of the built-in icons and the suggested emoji, for the icon picker."""
        out = {}
        for name in list(look.ICONS) + EMOJI:
            try:
                out[name] = look.icon_image(name, px)
            except Exception:   # one broken icon must not empty the whole picker
                continue
        return out

    # --- files
    def copy_icon(self, path):
        """Keep a small copy of the person's picture in the app's icons folder -> "icons/x.png"."""
        src = Path(path)
        if src.suffix.lower() not in ICON_TYPES:
            raise Problem("Please choose a picture (PNG, JPG, GIF, WEBP, BMP or ICO).")
        try:
            pic = Image.open(src).convert("RGBA")
            pic.thumbnail((256, 256), Image.Resampling.LANCZOS)
        except Exception:
            raise Problem("That picture could not be opened. Please try a different one.")
        folder = self.base / "icons"
        folder.mkdir(exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", src.stem).strip("_")[:40] or "picture"
        dest, n = folder / f"{stem}.png", 1
        while dest.exists():
            try:
                if Image.open(dest).convert("RGBA").tobytes() == pic.tobytes():
                    break   # the same picture is already there
            except Exception:
                pass
            n += 1
            dest = folder / f"{stem}_{n}.png"
        pic.save(dest)
        return f"icons/{dest.name}"

    def set_saver(self, path):
        """Use a picture/GIF as the screen saver (None removes it). Saved at once.
        Returns the new saver_file setting."""
        if path:
            if Path(path).suffix.lower() not in SAVER_TYPES:
                raise Problem("Please choose a picture or GIF (GIF, WEBP, PNG or JPG).")
            try:
                Image.open(path).verify()
            except Exception:
                raise Problem("That picture could not be opened. Please try a different one.")
        name = self.host.set_saver_file(path or None) or ""
        pages, settings = self.load()   # what is saved now, not the unsaved edits
        settings["saver_file"] = name
        self.host.save(pages, settings)
        return name

    def saver_picture(self, saver_file=""):
        named = inside(self.base, saver_file)
        if named and named.is_file():
            return named
        for ext in SAVER_TYPES:
            f = self.base / f"screensaver{ext}"
            if f.is_file():
                return f
        return None

    def saver_thumb(self, saver_file, size):
        f = self.saver_picture(saver_file)
        if f is None:
            return None
        try:
            pic = Image.open(f)
            pic.seek(0)
            pic = pic.convert("RGB")
            pic.thumbnail(size, Image.Resampling.LANCZOS)
            return pic
        except Exception:
            return None


class Painter(threading.Thread):
    """Draws pictures on a helper thread. Only the newest request of each kind is drawn."""

    def __init__(self, core):
        super().__init__(daemon=True)
        self.core = core
        self.jobs = queue.Queue()
        self.results = queue.Queue()   # (kind, args, result), read by the window

    def ask(self, kind, *args):
        self.jobs.put((kind, args))

    def run(self):
        while True:
            pending = [self.jobs.get()]
            while True:
                try:
                    pending.append(self.jobs.get_nowait())
                except queue.Empty:
                    break
            latest = {}
            for kind, args in pending:
                latest.pop(kind, None)
                latest[kind] = args
            if "stop" in latest:
                return
            for kind, args in latest.items():
                try:
                    out = getattr(self.core, "job_" + kind)(*args)
                except Exception:
                    traceback.print_exc()
                    continue
                self.results.put((kind, args, out))


# ================================================================= look of the window
BG, PANEL, PANEL2, CARD2, FIELD = "#0d0f13", "#161920", "#1d212a", "#12151b", "#0f1217"
LINE, LINE2, HOVER, DECK = "#2a2f3b", "#3a4150", "#262b36", "#000000"   # deck: black, like the device
TEXT, MUTED, DIM = "#e9ecf2", "#98a1b2", "#5d6574"
ACCENT, ACCENT_HI, ACCENT_SOFT = "#5b8cff", "#7aa2ff", "#1e2a46"
GOOD, WARN, BAD = "#3fb950", "#f0b429", "#f2555a"
FONT = {
    "body": ("Segoe UI", 10), "small": ("Segoe UI", 9), "bold": ("Segoe UI Semibold", 10),
    "h1": ("Segoe UI Semibold", 16), "h2": ("Segoe UI Semibold", 12), "cap": ("Segoe UI Semibold", 8),
    "brand": ("Segoe UI", 14, "bold"), "tab": ("Segoe UI Semibold", 11), "plus": ("Segoe UI Light", 24),
    "emoji": ("Segoe UI Emoji", 16), "mono": ("Consolas", 10), "kbd": ("Segoe UI Semibold", 10),
}
HEX = re.compile(r"#[0-9a-fA-F]{6}")


def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    """A rounded rectangle on a tk Canvas (smoothed polygon)."""
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def dark_title_bar(win):
    """Ask Windows for a dark title bar (Windows 10 2004+ / 11). Harmless if it cannot."""
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        on = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
    except Exception:
        pass


class Btn(tk.Label):
    """A flat button with a hover colour. kind: normal, primary, danger, ghost, on, rec."""
    STYLES = {  # background, text, hover background, border  (None = parent's background)
        "normal": (PANEL2, TEXT, HOVER, LINE2), "primary": (ACCENT, "#ffffff", ACCENT_HI, ACCENT),
        "danger": (BAD, "#ffffff", "#ff7579", BAD), "ghost": (None, MUTED, PANEL2, None),
        "on": (ACCENT_SOFT, TEXT, ACCENT_SOFT, ACCENT), "rec": ("#3a1d22", "#ffb3b6", "#4a2228", BAD),
    }

    def __init__(self, parent, text, command=None, kind="normal", small=False, font=None, **kw):
        self.parent_bg = parent.cget("bg")
        super().__init__(parent, text=text, font=font or FONT["small" if small else "body"],
                         padx=10 if small else 14, pady=3 if small else 6, bd=0,
                         highlightthickness=1, **kw)
        self.command, self.enabled, self.hover, self.kind = command, True, False, kind
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        self._paint()

    def _hover(self, on):
        self.hover = on
        self._paint()

    def _paint(self):
        bg, fg, hover_bg, border = self.STYLES[self.kind]
        bg = bg or self.parent_bg
        border = border or bg
        if not self.enabled:
            if self.kind in ("primary", "danger"):
                bg, border = PANEL2, LINE2
            fg = DIM
        elif self.hover:
            bg = hover_bg
            fg = TEXT if self.kind == "ghost" else fg
        self.configure(bg=bg, fg=fg, highlightbackground=border, highlightcolor=border,
                       cursor="hand2" if self.enabled else "arrow")

    def set_kind(self, kind):
        self.kind = kind
        self._paint()

    def set_enabled(self, on):
        self.enabled = bool(on)
        self._paint()

    def _click(self, e):
        inside_me = 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height()
        if self.enabled and self.command and inside_me:
            self.command()


class Switch(tk.Canvas):
    """An on/off switch. command(value) is called when it is clicked."""

    def __init__(self, parent, value=False, command=None):
        super().__init__(parent, width=44, height=24, bg=parent.cget("bg"), highlightthickness=0,
                         cursor="hand2")
        self.value, self.command = bool(value), command
        self.bind("<Button-1>", self.toggle)
        self.draw()

    def draw(self):
        self.delete("all")
        c = ACCENT if self.value else LINE2
        self.create_oval(0, 0, 23, 23, fill=c, outline=c)
        self.create_oval(20, 0, 43, 23, fill=c, outline=c)
        self.create_rectangle(12, 0, 32, 23, fill=c, outline=c)
        x = 23 if self.value else 3
        self.create_oval(x, 3, x + 17, 20, fill="#ffffff", outline="#ffffff")

    def set(self, value):
        self.value = bool(value)
        self.draw()

    def toggle(self, e=None):
        self.set(not self.value)
        if self.command:
            self.command(self.value)


class Slider(tk.Canvas):
    """A flat slider. command(value) is called while it is dragged."""

    def __init__(self, parent, lo, hi, step, value, command):
        super().__init__(parent, height=26, width=200, bg=parent.cget("bg"), highlightthickness=0,
                         cursor="hand2")
        self.lo, self.hi, self.step, self.value, self.command = lo, hi, step, value, command
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", self._at)
        self.bind("<B1-Motion>", self._at)

    def _at(self, e):
        r, w = 9, max(self.winfo_width(), 30)
        frac = min(1, max(0, (e.x - r) / (w - 2 * r)))
        v = self.lo + round(frac * (self.hi - self.lo) / self.step) * self.step
        if v != self.value:
            self.value = v
            self.draw()
            self.command(v)

    def set(self, value):
        self.value = value
        self.draw()

    def draw(self):
        self.delete("all")
        r, w, y = 9, max(self.winfo_width(), 30), 13
        x = r + (w - 2 * r) * (self.value - self.lo) / max(1, self.hi - self.lo)
        self.create_line(r, y, w - r, y, fill=LINE2, width=4, capstyle="round")
        self.create_line(r, y, x, y, fill=ACCENT, width=4, capstyle="round")
        self.create_oval(x - r, y - r, x + r, y + r, fill="#ffffff", outline=ACCENT, width=2)


class Seg(tk.Frame):
    """A row of choices where one is on. options = [(value, title, small text or None)]."""

    def __init__(self, parent, options, value, command):
        super().__init__(parent, bg=FIELD, highlightthickness=1, highlightbackground=LINE, padx=3, pady=3)
        self.items = {}
        for i, (val, title, sub) in enumerate(options):
            cell = tk.Frame(self, highlightthickness=1, cursor="hand2", padx=6, pady=7 if sub else 4)
            parts = [tk.Label(cell, text=title, font=FONT["bold"] if sub else FONT["body"], bd=0)]
            if sub:
                parts.append(tk.Label(cell, text=sub, font=FONT["small"], bd=0))
            for p in parts:
                p.pack()
            for w in [cell] + parts:
                w.bind("<Button-1>", lambda e, v=val: command(v))
            cell.grid(row=0, column=i, sticky="nsew", padx=1)
            self.columnconfigure(i, weight=1, uniform="seg")
            self.items[val] = (cell, parts)
        self.set(value)

    def set(self, value):
        for val, (cell, parts) in self.items.items():
            on = val == value
            bg = ACCENT_SOFT if on else FIELD
            cell.configure(bg=bg, highlightbackground=ACCENT if on else FIELD,
                           highlightcolor=ACCENT if on else FIELD)
            for i, p in enumerate(parts):
                p.configure(bg=bg, fg=(TEXT if on else MUTED) if i == 0 else MUTED)


class Scroller(tk.Frame):
    """A frame that scrolls up and down. Put things in .inner."""

    def __init__(self, parent, bg):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        style = ttk.Style()
        name = f"S{bg[1:]}.Vertical.TScrollbar"
        style.configure(name, background=PANEL2, troughcolor=bg, bordercolor=bg, arrowcolor=MUTED,
                        lightcolor=PANEL2, darkcolor=PANEL2, gripcount=0, arrowsize=12)
        style.map(name, background=[("active", LINE2)])
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview, style=name)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner.bind("<Configure>", lambda e: self._update())
        self.canvas.bind("<Configure>", self._resize)

    def _resize(self, e):
        self.canvas.itemconfigure(self.win, width=e.width)
        self._update()

    def _update(self):
        h = max(self.inner.winfo_reqheight(), self.canvas.winfo_height())
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), h))

    def wheel(self, delta):
        if self.inner.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(-int(delta / 40) or (-1 if delta > 0 else 1), "units")

    def top(self):
        self.canvas.yview_moveto(0)


# ================================================================= the window
class Window:
    """The settings window. `saved` = what the deck has now; `work` = what you are editing."""

    def __init__(self, host):
        try:   # sharp text on high-resolution screens
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        self.core = Core(host)
        self.host = host
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("D6Deck Settings")
        self.root.configure(bg=BG)
        self.root.geometry("1280x860")
        self.root.minsize(900, 640)
        self.root.report_callback_exception = self._oops
        self._set_window_icon()
        self._styles()

        pages, settings = self.core.load()
        self.saved = {"pages": pages, "settings": settings}
        self.work = copy.deepcopy(self.saved)
        self.lists = {"themes": dict(look.THEMES), "icons": dict(look.ICONS), "colors": list(look.COLORS),
                      "widgets": {n: {"label": str(w.get("label", n)), "desc": str(w.get("desc", ""))}
                                  for n, w in look.WIDGETS.items()},
                      "savers": self.core.savers, "games": self.core.games, "toys": self.core.toys}
        self.view, self.page, self.sel, self.clip = "keys", 0, None, None
        self.undo_list, self.redo_list, self.last_tag, self.last_time = [], [], "", 0.0
        self.kpx = 112                 # size of a key picture on screen
        self.photos = {}               # picture id -> PhotoImage
        self.shown = {}                # (page, pos) -> PhotoImage on that key now
        self.icon_pics, self.icon_photos = None, {}
        self.theme_photos = []
        self.running, self.startup, self.busy = None, False, False
        self.hover = self.drag = self.drop = None
        self.icon_tab, self.icon_search = None, ""
        self.stop_recording = None
        self.preview_timer = self.theme_timer = self.resize_timer = None
        self.toast_label = None
        self.tabs_drawn = None

        self.painter = Painter(self.core)
        self.painter.start()
        self._build()
        self.painter.ask("icons", 40)
        self.after_edit()
        self.render_editor()
        self.set_view("keys")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._keyboard()
        self.root.after(40, self._poll)
        self.root.after(200, self._status_tick)
        self.root.after(60_000, self._minute)
        self.root.deiconify()
        dark_title_bar(self.root)

    # ------------------------------------------------------------- setup
    def _set_window_icon(self):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        colors = ["#5b8cff", "#8f6bff", "#5b8cff", "#38c793", "#5b8cff", "#f0b429"]
        for i, c in enumerate(colors):
            x, y = 4 + (i % 3) * 20, 14 + (i // 3) * 20
            d.rounded_rectangle([x, y, x + 16, y + 16], 4, fill=c)
        self._icon = ImageTk.PhotoImage(img)
        self.root.iconphoto(True, self._icon)

    def _styles(self):
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure("Dark.TCombobox", fieldbackground=FIELD, background=PANEL2, foreground=TEXT,
                     arrowcolor=TEXT, bordercolor=LINE2, lightcolor=FIELD, darkcolor=FIELD, padding=5,
                     insertcolor=TEXT, selectbackground=FIELD, selectforeground=TEXT, arrowsize=14)
        st.map("Dark.TCombobox",
               fieldbackground=[("readonly", FIELD), ("disabled", PANEL)],
               foreground=[("disabled", DIM)], background=[("active", HOVER), ("pressed", HOVER)],
               bordercolor=[("focus", ACCENT), ("hover", "#4a5264")],
               selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", TEXT)])
        for opt, val in (("background", PANEL2), ("foreground", TEXT), ("selectBackground", ACCENT),
                         ("selectForeground", "#ffffff"), ("font", FONT["body"]), ("borderWidth", 0),
                         ("relief", "flat")):
            self.root.option_add(f"*TCombobox*Listbox.{opt}", val)

    def _oops(self, *exc):
        """Something unexpected went wrong in the window: say so in plain words, keep going."""
        traceback.print_exception(*exc)
        self.toast("Something went wrong. Please try again.", "bad")

    def _keyboard(self):
        r = self.root

        def typing():
            w = r.focus_get()
            return isinstance(w, (tk.Entry, tk.Text, ttk.Combobox))

        def shortcut(fn, always=False):
            def go(e):
                if self.stop_recording or (typing() and not always):
                    return None
                fn()
                return "break"
            return go
        r.bind("<Control-s>", shortcut(self.save, always=True))
        r.bind("<Control-z>", shortcut(self.undo))
        r.bind("<Control-y>", shortcut(self.redo))
        r.bind("<Control-Z>", shortcut(self.redo))
        r.bind("<Control-c>", shortcut(lambda: self.view == "keys" and self.copy_key()))
        r.bind("<Control-v>", shortcut(lambda: self.view == "keys" and self.paste_key()))
        r.bind("<Delete>", shortcut(lambda: self.view == "keys" and self.clear_key()))
        r.bind_all("<MouseWheel>", self._wheel)
        r.bind_class("TCombobox", "<MouseWheel>", lambda e: (self._wheel(e), "break")[1])

    def _wheel(self, e):
        try:
            w = self.root.winfo_containing(e.x_root, e.y_root)
        except (KeyError, tk.TclError):
            return
        while w is not None and not isinstance(w, Scroller):
            w = w.master
        if w is not None:
            w.wheel(e.delta)

    # ------------------------------------------------------------- building the window
    def _build(self):
        r = self.root
        # --- top bar
        top = tk.Frame(r, bg=PANEL, padx=18, pady=10)
        top.pack(fill="x")
        brand = tk.Frame(top, bg=PANEL)
        brand.pack(side="left")
        logo = tk.Canvas(brand, width=30, height=22, bg=PANEL, highlightthickness=0)
        for i, c in enumerate(["#5b8cff", "#8f6bff", "#5b8cff", "#38c793", "#5b8cff", "#f0b429"]):
            x, y = (i % 3) * 10, 2 + (i // 3) * 10
            round_rect(logo, x, y, x + 8, y + 8, 2, fill=c, outline="")
        logo.pack(side="left", padx=(0, 8))
        tk.Label(brand, text="D6Deck", font=FONT["brand"], bg=PANEL, fg=TEXT).pack(side="left")
        tk.Label(brand, text="Settings", font=("Segoe UI", 14), bg=PANEL, fg=MUTED).pack(side="left", padx=(6, 0))

        right = tk.Frame(top, bg=PANEL)
        right.pack(side="right")
        self.unsaved = tk.Label(right, font=FONT["bold"], bg=PANEL)
        self.unsaved.pack(side="left", padx=(0, 14))
        self.btn_undo = Btn(right, "Undo", self.undo)
        self.btn_undo.pack(side="left", padx=(0, 6))
        self.btn_redo = Btn(right, "Redo", self.redo)
        self.btn_redo.pack(side="left", padx=(0, 12))
        self.btn_discard = Btn(right, "Discard changes", self.discard)
        self.btn_discard.pack(side="left", padx=(0, 8))
        self.btn_save = Btn(right, "Save to deck", self.save, kind="primary", font=FONT["bold"])
        self.btn_save.pack(side="left", padx=(0, 8))
        self.btn_more = Btn(right, "⋯", self.more_menu, font=("Segoe UI", 12))
        self.btn_more.configure(pady=1)
        self.btn_more.pack(side="left")
        tk.Frame(r, bg=LINE, height=1).pack(fill="x")

        # --- second row: Buttons / Settings, and the deck's status
        row = tk.Frame(r, bg=BG, padx=18)
        row.pack(fill="x")
        self.view_tabs = {}
        for name, text in (("keys", "Buttons"), ("settings", "Settings")):
            cell = tk.Frame(row, bg=BG, cursor="hand2")
            cell.pack(side="left", padx=(0, 18))
            lab = tk.Label(cell, text=text, font=FONT["tab"], bg=BG, pady=9)
            lab.pack()
            bar = tk.Frame(cell, height=3, bg=BG)
            bar.pack(fill="x")
            for w in (cell, lab):
                w.bind("<Button-1>", lambda e, n=name: self.set_view(n))
            self.view_tabs[name] = (lab, bar)

        pill = tk.Frame(row, bg=FIELD, highlightthickness=1, highlightbackground=LINE, padx=10, pady=4)
        pill.pack(side="right", pady=6)
        self.dot = tk.Canvas(pill, width=12, height=12, bg=FIELD, highlightthickness=0)
        self.dot.pack(side="left")
        self.status_text = tk.Label(pill, text="Checking…", font=FONT["body"], bg=FIELD, fg=TEXT)
        self.status_text.pack(side="left", padx=(6, 10))
        self.btn_start = Btn(pill, "Start", lambda: self.control("start", "Starting…", "The deck is running."), small=True)
        self.btn_restart = Btn(pill, "Restart", lambda: self.control("restart", "Restarting…", "The deck was restarted."), small=True)
        self.btn_stop = Btn(pill, "Stop", lambda: self.control("stop", "Stopping…", "The deck is stopped."), small=True)
        tk.Frame(r, bg=LINE, height=1).pack(fill="x")

        # --- the two views
        self.body = tk.Frame(r, bg=BG)
        self.body.pack(fill="both", expand=True)
        self._build_keys_view()
        self._build_settings_view()

    def _build_keys_view(self):
        v = self.keys_view = tk.Frame(self.body, bg=BG)
        v.columnconfigure(0, weight=1)
        v.rowconfigure(0, weight=1)
        self.stage = tk.Frame(v, bg=BG)
        self.stage.grid(row=0, column=0, sticky="nsew")
        self.stage.bind("<Configure>", lambda e: self._resized())
        ed_wrap = tk.Frame(v, bg=LINE)
        ed_wrap.grid(row=0, column=1, sticky="ns")
        self.editor = Scroller(ed_wrap, PANEL)
        self.editor.configure(width=410)
        self.editor.pack(side="right", fill="y", padx=(1, 0))
        self.editor.pack_propagate(False)
        self.editor.canvas.configure(width=392)

        col = self.column = tk.Frame(self.stage, bg=BG)
        col.place(relx=0.5, rely=0.5, anchor="center")
        self.pagebar = tk.Frame(col, bg=BG)
        self.pagebar.pack(fill="x", pady=(0, 14))
        self.page_tabs = tk.Frame(self.pagebar, bg=BG)
        self.page_tabs.pack(side="left")
        self.btn_del_page = Btn(self.pagebar, "Delete this page", self.delete_page, kind="ghost", small=True)
        deck = tk.Frame(col, bg=DECK, highlightthickness=1, highlightbackground="#262a33", padx=16, pady=16)
        deck.pack()
        self.key_canvases = {}
        for pos in range(1, NKEYS + 1):
            c = tk.Canvas(deck, bg=DECK, highlightthickness=0, cursor="hand2")
            c.grid(row=(pos - 1) // 5, column=(pos - 1) % 5, padx=3, pady=3)
            c.bind("<ButtonPress-1>", lambda e, p=pos: self._press(p, e))
            c.bind("<B1-Motion>", lambda e, p=pos: self._motion(p, e))
            c.bind("<ButtonRelease-1>", lambda e, p=pos: self._release(p, e))
            c.bind("<Enter>", lambda e, p=pos: self._hover(p))
            c.bind("<Leave>", lambda e, p=pos: self._hover(None))
            self.key_canvases[pos] = c
        self.stuck = tk.Label(col, bg="#2a2412", fg="#f5cf73", font=FONT["small"], padx=12, pady=6,
                              text="This page has no “Go to another page” key, so on the deck you could not leave it.")
        self.deck_hint = tk.Label(col, bg=BG, fg=MUTED, font=FONT["small"],
                                  text="Click a key to change it.  Drag a key onto another one to swap them.")
        self.deck_hint.pack(pady=(14, 0))
        self._size_keys()

    def _build_settings_view(self):
        v = self.settings_view = Scroller(self.body, BG)
        page = tk.Frame(v.inner, bg=BG, padx=28, pady=24)
        page.pack(fill="x")
        page.columnconfigure(0, weight=1, uniform="c")
        page.columnconfigure(1, weight=1, uniform="c")

        def card(r, c, span=1):
            f = tk.Frame(page, bg=PANEL, highlightthickness=1, highlightbackground=LINE, padx=22, pady=18)
            f.grid(row=r, column=c, columnspan=span, sticky="nsew", padx=8, pady=8)
            return f

        def heading(parent, title, hint=None):
            tk.Label(parent, text=title, font=FONT["h2"], bg=PANEL, fg=TEXT, anchor="w").pack(fill="x")
            if hint:
                tk.Label(parent, text=hint, font=FONT["small"], bg=PANEL, fg=MUTED, anchor="w",
                         justify="left").pack(fill="x", pady=(2, 0))

        look_card = card(0, 0, 2)
        heading(look_card, "Look", "Pick how your keys are drawn. The pictures show your own keys.")
        self.theme_grid = tk.Frame(look_card, bg=PANEL)
        self.theme_grid.pack(fill="x", pady=(12, 0))
        self.theme_cards = {}
        for name, label in self.lists["themes"].items():
            cell = tk.Frame(self.theme_grid, bg=CARD2, highlightthickness=2, highlightbackground=LINE,
                            padx=10, pady=10, cursor="hand2")
            pics = tk.Frame(cell, bg=DECK, padx=6, pady=6)   # a little black deck
            pics.pack(anchor="w")
            slots = [tk.Label(pics, bg=DECK, bd=0, width=8, height=4) for _ in range(4)]
            for i, s in enumerate(slots):
                s.grid(row=0, column=i, padx=(0, 4 if i < 3 else 0))
            title = tk.Label(cell, text=label, font=FONT["bold"], bg=CARD2, fg=TEXT, anchor="w")
            title.pack(fill="x", pady=(8, 0))
            for w in [cell, pics, title] + slots:
                w.bind("<Button-1>", lambda e, n=name: self.set_setting("", "theme", n))
            self.theme_cards[name] = (cell, pics, title, slots)
        self.theme_grid.bind("<Configure>", lambda e: self._theme_columns(e.width))

        bright = card(1, 0)
        heading(bright, "Brightness", "How bright the key screens are.")
        self.s_bright, self.s_bright_out = self._slider_row(
            bright, 10, 100, 5, self.work["settings"]["brightness"],
            lambda v: self.set_setting("bright", "brightness", v))

        misc = card(1, 1)
        self.s_sounds = self._switch_row(misc, "Sounds", "Little sound effects in the games.",
                                         lambda v: self.set_setting("", "sounds", v))
        tk.Frame(misc, bg=LINE, height=1).pack(fill="x", pady=12)
        self.s_startup = self._switch_row(misc, "Start with Windows",
                                          "Turn the deck on by itself when the PC starts.\nThis one changes straight away.",
                                          self.set_startup)

        saver = card(2, 0, 2)
        self.s_saver_on = self._switch_row(saver, "Screen saver",
                                           "What the deck shows after you press a “Screen off” key. Off = the screens just go dark.",
                                           lambda v: self.set_setting("", "saver_enabled", v))
        self.saver_more = tk.Frame(saver, bg=PANEL)
        self.saver_more.pack(fill="x")
        grid = tk.Frame(self.saver_more, bg=PANEL)
        grid.pack(fill="x", pady=(10, 0))
        grid.columnconfigure(0, weight=1, uniform="s")
        grid.columnconfigure(1, weight=1, uniform="s")
        left = tk.Frame(grid, bg=PANEL)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 20))
        self._label(left, "Style")
        names = list(self.lists["savers"].values())
        self.s_style = ttk.Combobox(left, values=names, state="readonly", style="Dark.TCombobox", font=FONT["body"])
        self.s_style.pack(fill="x")
        self.s_style.bind("<<ComboboxSelected>>", lambda e: self.set_setting(
            "", "saver_style", list(self.lists["savers"])[self.s_style.current()]))
        self._label(left, "Screen saver brightness", top=14)
        self.s_saver_bright, self.s_saver_bright_out = self._slider_row(
            left, 10, 100, 5, self.work["settings"]["saver_brightness"],
            lambda v: self.set_setting("sbright", "saver_brightness", v))
        right = tk.Frame(grid, bg=PANEL)
        right.grid(row=0, column=1, sticky="nsew")
        self._label(right, "Your own picture or GIF")
        box = tk.Frame(right, bg=PANEL)
        box.pack(fill="x")
        self.s_thumb = tk.Label(box, bg=FIELD, fg=DIM, font=FONT["small"], text="No picture yet",
                                width=26, height=6, highlightthickness=1, highlightbackground=LINE2)
        self.s_thumb.pack(side="left")
        side = tk.Frame(box, bg=PANEL)
        side.pack(side="left", fill="x", expand=True, padx=(14, 0))
        self.s_pick = Btn(side, "Use my own picture or GIF…", self.pick_saver)
        self.s_pick.pack(anchor="w")
        self.s_remove = Btn(side, "Remove", self.remove_saver, kind="ghost")
        self.s_saver_hint = tk.Label(right, bg=PANEL, fg=MUTED, font=FONT["small"], justify="left",
                                     anchor="w", wraplength=380)
        self.s_saver_hint.pack(fill="x", pady=(8, 0))
        self.s_saver_hint.bind("<Configure>", lambda e: self.s_saver_hint.configure(wraplength=max(200, e.width - 4)))
        self._label(right, "Picture when the PC is off", top=14)
        self.s_off_pick = Btn(right, "Choose a picture for when the PC is off…", self.pick_offscreen)
        self.s_off_pick.pack(anchor="w")
        tk.Label(right, bg=PANEL, fg=MUTED, font=FONT["small"], justify="left", anchor="w", wraplength=380,
                 text="Saved inside the D6 itself, so it shows even when D6Deck isn't running. "
                      "Still pictures only (PNG, JPG or BMP), no GIFs. Wide pictures work best.").pack(
            fill="x", pady=(8, 0))

    def _theme_columns(self, width):
        cols = max(1, min(len(self.theme_cards), width // 270))
        if getattr(self, "_theme_cols", None) == cols:
            return
        self._theme_cols = cols
        for i in range(8):
            self.theme_grid.columnconfigure(i, weight=0, uniform="")
        for i, (cell, *_ignored) in enumerate(self.theme_cards.values()):
            cell.grid(row=i // cols, column=i % cols, sticky="nsew", padx=(0, 12), pady=(0, 12))
        for i in range(cols):
            self.theme_grid.columnconfigure(i, weight=1, uniform="t")

    # small building blocks
    def _label(self, parent, text, top=0):
        tk.Label(parent, text=text, font=FONT["small"], bg=parent.cget("bg"), fg=MUTED,
                 anchor="w").pack(fill="x", pady=(top, 5))

    def _slider_row(self, parent, lo, hi, step, value, command, suffix="%"):
        row = tk.Frame(parent, bg=parent.cget("bg"))
        row.pack(fill="x", pady=(8, 0))
        out = tk.Label(row, text=f"{value}{suffix}", width=5, anchor="e", font=FONT["bold"],
                       bg=parent.cget("bg"), fg=TEXT)
        out.pack(side="right")

        def changed(v):
            out.configure(text=f"{v}{suffix}")
            command(v)
        s = Slider(row, lo, hi, step, value, changed)
        s.pack(side="left", fill="x", expand=True, padx=(0, 10))
        return s, out

    def _switch_row(self, parent, title, sub, command, big=True):
        row = tk.Frame(parent, bg=parent.cget("bg"))
        row.pack(fill="x")
        sw = Switch(row, False, command)
        sw.pack(side="right", padx=(16, 0))
        text = tk.Frame(row, bg=parent.cget("bg"))
        text.pack(side="left", fill="x", expand=True)
        tk.Label(text, text=title, font=FONT["h2"] if big else FONT["body"], bg=parent.cget("bg"),
                 fg=TEXT, anchor="w").pack(fill="x")
        if sub:
            tk.Label(text, text=sub, font=FONT["small"], bg=parent.cget("bg"), fg=MUTED, anchor="w",
                     justify="left", wraplength=520).pack(fill="x")
        for w in (row, text):
            w.bind("<Button-1>", lambda e: sw.toggle())
        return sw

    def _entry(self, parent, value, on_change, font=None, width=None):
        """A dark text box. on_change(text) is called as the person types."""
        frame = tk.Frame(parent, bg=FIELD, highlightthickness=1, highlightbackground=LINE2, padx=8)
        var = tk.StringVar(value=value)
        ent = tk.Entry(frame, textvariable=var, bg=FIELD, fg=TEXT, insertbackground=TEXT, relief="flat",
                       bd=0, font=font or FONT["body"], width=width or 10, highlightthickness=0,
                       selectbackground=ACCENT, selectforeground="#ffffff")
        ent.pack(fill="x", ipady=6)
        ent.bind("<FocusIn>", lambda e: frame.configure(highlightbackground=ACCENT))
        ent.bind("<FocusOut>", lambda e: frame.configure(highlightbackground=LINE2))
        var.trace_add("write", lambda *a: on_change(var.get()))
        return frame, ent, var

    def _combo(self, parent, names, current, on_pick):
        """A dark drop-down list. on_pick(index) is called when something is chosen."""
        box = ttk.Combobox(parent, values=names, state="readonly", style="Dark.TCombobox", font=FONT["body"])
        if 0 <= current < len(names):
            box.current(current)
        box.bind("<<ComboboxSelected>>", lambda e: (box.selection_clear(), on_pick(box.current())))
        return box

    # ------------------------------------------------------------- messages
    def toast(self, text, kind=None):
        if self.toast_label is not None:
            self.toast_label.destroy()
        colors = {"good": ("#16361f", "#2c6e3b", "#b9f0c4"), "bad": ("#43191c", "#8a2f34", "#ffc9cb"),
                  None: ("#2a303c", LINE2, TEXT)}
        bg, border, fg = colors[kind]
        t = tk.Label(self.root, text=text, bg=bg, fg=fg, font=FONT["bold"], padx=18, pady=10,
                     highlightthickness=1, highlightbackground=border, wraplength=520, justify="left")
        t.place(relx=0.5, rely=1.0, y=-24, anchor="s")
        t.lift()
        self.toast_label = t
        t.after(6000 if kind == "bad" else 3200, lambda: t.winfo_exists() and t.destroy())

    def dialog(self, title, message, buttons, body=None):
        """A dark question box. buttons = [(label, kind, value)], the last one is the default.
        Returns the value of the button pressed, or None if it was closed."""
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title(title)
        win.configure(bg=PANEL2)
        win.resizable(False, False)
        win.transient(self.root)
        answer = {"value": None}
        box = tk.Frame(win, bg=PANEL2, padx=24, pady=20)
        box.pack(fill="both", expand=True)
        tk.Label(box, text=title, font=FONT["h2"], bg=PANEL2, fg=TEXT, anchor="w").pack(fill="x")
        if message:
            tk.Label(box, text=message, font=FONT["body"], bg=PANEL2, fg="#c3c9d6", wraplength=420,
                     justify="left", anchor="w").pack(fill="x", pady=(6, 0))
        if body:
            body(box)
        row = tk.Frame(box, bg=PANEL2)
        row.pack(fill="x", pady=(18, 0))

        def pick(value):
            answer["value"] = value
            win.destroy()
        for label, kind, value in reversed(buttons):
            Btn(row, label, lambda v=value: pick(v), kind=kind).pack(side="right", padx=(8, 0))
        win.bind("<Escape>", lambda e: pick(None))
        win.bind("<Return>", lambda e: pick(buttons[-1][2]))
        win.protocol("WM_DELETE_WINDOW", lambda: pick(None))
        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_reqheight()) // 3
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
        win.deiconify()
        dark_title_bar(win)
        win.grab_set()
        win.focus_set()
        self.root.wait_window(win)
        return answer["value"]

    def ask(self, title, message, ok_label, danger=False):
        return self.dialog(title, message, [("Cancel", "normal", False),
                                            (ok_label, "danger" if danger else "primary", True)]) is True

    # ------------------------------------------------------------- background work
    def errand(self, work, then=None):
        """Run a slow host call on a helper thread, then call then(result) in the window."""
        def go():
            try:
                out = (True, work())
            except Problem as e:
                out = (False, str(e))
            except Exception:
                traceback.print_exc()
                out = (False, "Something went wrong. Please try again.")
            self.painter.results.put(("errand", then, out))
        threading.Thread(target=go, daemon=True).start()

    def _poll(self):
        """Pick up finished pictures and errands (tkinter may only be used from this thread)."""
        try:
            while True:
                kind, args, out = self.painter.results.get_nowait()
                if kind == "errand":
                    ok, value = out
                    if not ok:
                        self.toast(value, "bad")
                    elif args:
                        args(value)
                elif kind == "deck":
                    self._got_deck(args, out)
                elif kind == "themes":
                    self._got_themes(out)
                elif kind == "icons":
                    self.icon_pics = out
                    if self.sel and self.kind_of(self.sel_key()) == "button":
                        self.render_editor()
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(40, self._poll)

    def _minute(self):
        """Clocks on the keys move on."""
        self.schedule_preview()
        self.root.after(60_000, self._minute)

    # ------------------------------------------------------------- deck status
    def _status_tick(self):
        if not self.busy:
            self.errand(lambda: (bool(self.host.running()), bool(self.host.startup())), self._got_status)
        self.root.after(4000, self._status_tick)

    def _got_status(self, value):
        if not self.busy:
            self.running, self.startup = value
            self.draw_status()

    def draw_status(self, doing=None):
        on = self.running
        color = WARN if doing else (MUTED if on is None else GOOD if on else BAD)
        self.dot.delete("all")
        self.dot.create_oval(1, 1, 11, 11, fill=color, outline=color)
        self.status_text.configure(text=doing or ("Checking…" if on is None else
                                                  "Deck is running" if on else "Deck is stopped"))
        for b in (self.btn_start, self.btn_restart, self.btn_stop):
            b.pack_forget()
            b.set_enabled(not doing)
        if on is False:
            self.btn_start.pack(side="left")
        elif on:
            self.btn_restart.pack(side="left", padx=(0, 6))
            self.btn_stop.pack(side="left")
        self.s_startup.set(self.startup)

    def control(self, what, doing, done):
        self.busy = True
        self.draw_status(doing)

        def finished(running):
            self.busy = False
            self.running = running
            self.draw_status()
            self.toast(done, "good")

        def work():
            try:
                self.host.control(what)
                return bool(self.host.running())
            except Exception:
                self.busy = False   # the error message is shown by errand(); polling resumes
                raise
        self.errand(work, finished)

    def set_startup(self, enable):
        def done(on):
            self.startup = on
            self.s_startup.set(on)
            self.toast("The deck will start with Windows." if on else
                       "The deck will no longer start with Windows.", "good")
        self.errand(lambda: bool(self.host.startup(bool(enable))), done)

    # ------------------------------------------------------------- changing things, undo
    def keys(self):
        return self.work["pages"][self.page]

    def sel_key(self):
        return self.keys().get(self.sel) if self.sel else None

    @staticmethod
    def kind_of(k):
        return "empty" if not k else "live" if k.get("widget") else "button"

    def dirty(self):
        return json.dumps(self.work, sort_keys=True) != json.dumps(self.saved, sort_keys=True)

    def _snapshot(self):
        return json.dumps({"work": self.work, "page": self.page, "sel": self.sel}, ensure_ascii=False)

    def edit(self, tag, change):
        """Every change goes through here. Quick repeats with the same tag (typing a name)
        become one undo step."""
        now = time.monotonic()
        if not (tag and tag == self.last_tag and now - self.last_time < 1.5):
            self.undo_list.append(self._snapshot())
            del self.undo_list[:-60]
        self.redo_list.clear()
        self.last_tag, self.last_time = tag, now
        change()
        self.after_edit()

    def _step(self, source, target):
        if not source:
            return
        target.append(self._snapshot())
        old = json.loads(source.pop())
        self.work = old["work"]
        self.page = min(old["page"], len(self.work["pages"]) - 1)
        self.sel = old["sel"]
        self.last_tag = ""
        self.after_edit()
        self.render_editor()
        self.sync_settings()

    def undo(self):
        self._step(self.undo_list, self.redo_list)

    def redo(self):
        self._step(self.redo_list, self.undo_list)

    def after_edit(self):
        """After a change: redraw the cheap parts now and ask for fresh key pictures."""
        self.update_top()
        self.draw_tabs()
        self.draw_deck()
        self.schedule_preview()
        if self.view == "settings":
            self.sync_settings()
            self.schedule_themes()

    def update_top(self):
        d = self.dirty()
        self.unsaved.configure(text="●  Unsaved changes" if d else "✓  Everything is saved",
                               fg=WARN if d else DIM)
        self.btn_save.set_enabled(d)
        self.btn_discard.set_enabled(d)
        self.btn_undo.set_enabled(bool(self.undo_list))
        self.btn_redo.set_enabled(bool(self.redo_list))
        self.root.title(("● " if d else "") + "D6Deck Settings")

    # ------------------------------------------------------------- top bar actions
    def save(self):
        if not self.dirty():
            return True
        try:
            pages, settings = self.core.save(self.work["pages"], self.work["settings"])
        except Problem as e:
            self.toast(str(e), "bad")
            return False
        except Exception:
            traceback.print_exc()
            self.toast("Your changes could not be saved. Please try again.", "bad")
            return False
        self.saved = {"pages": pages, "settings": settings}
        self.work = copy.deepcopy(self.saved)
        self.page = min(self.page, len(pages) - 1)
        self.after_edit()
        self.render_editor()
        self.toast("Saved. Start the deck to see your changes." if self.running is False else
                   "Saved. Your deck is updated.", "good")
        return True

    def discard(self):
        if not self.dirty() or not self.ask("Discard changes?", "Everything goes back to how your deck is now.",
                                            "Discard changes", danger=True):
            return
        self.edit("", lambda: self._set_work(copy.deepcopy(self.saved)))
        self.render_editor()
        self.sync_settings()

    def _set_work(self, work, page=None):
        self.work = work
        self.page = min(self.page if page is None else page, len(work["pages"]) - 1)
        self.sel = None

    def more_menu(self):
        m = tk.Menu(self.root, tearoff=0, bg=PANEL2, fg=TEXT, activebackground=HOVER,
                    activeforeground=TEXT, bd=0, font=FONT["body"])
        m.add_command(label=f"Reset page {self.page + 1} to the factory look", command=self.reset_page)
        m.add_command(label="Reset everything to the factory look", command=self.reset_all)
        m.add_separator()
        m.add_command(label="Show what the deck has been doing", command=self.show_log)
        b = self.btn_more
        m.tk_popup(b.winfo_rootx() + b.winfo_width() - 300, b.winfo_rooty() + b.winfo_height() + 4)

    def reset_page(self):
        if not self.ask(f"Reset page {self.page + 1}?",
                        "This page goes back to the factory look. Nothing changes on your deck until you "
                        "press “Save to deck”.", "Reset this page", danger=True):
            return
        pages, _ = self.core.factory()
        fresh = pages[self.page] if self.page < len(pages) else {"8": copy.deepcopy(PAGE_KEY)}

        def change():
            self.work["pages"][self.page] = fresh
            self.sel = None
        self.edit("", change)
        self.render_editor()
        self.toast(f"Page {self.page + 1} is back to the factory look. Press “Save to deck” to keep it.")

    def reset_all(self):
        if not self.ask("Reset everything?",
                        "All pages and all settings go back to the factory look. Nothing changes on your deck "
                        "until you press “Save to deck”.", "Reset everything", danger=True):
            return
        pages, settings = self.core.factory()
        settings["saver_file"] = self.work["settings"].get("saver_file", "")   # that one is a file, kept
        self.edit("", lambda: self._set_work({"pages": pages, "settings": settings}, 0))
        self.render_editor()
        self.sync_settings()
        self.toast("Everything is back to the factory look. Press “Save to deck” to keep it.")

    def show_log(self):
        def show(lines):
            def body(box):
                t = tk.Text(box, width=78, height=16, bg=FIELD, fg="#c3c9d6", font=FONT["mono"], bd=0,
                            highlightthickness=1, highlightbackground=LINE, padx=10, pady=8, wrap="word")
                t.insert("1.0", "\n".join(str(x) for x in lines) or "Nothing yet.")
                t.configure(state="disabled")
                t.pack(fill="both", pady=(12, 0))
            self.dialog("What the deck has been doing", None, [("Close", "primary", None)], body)
        self.errand(lambda: list(self.host.log_tail(60)), show)

    def close(self):
        if self.dirty():
            answer = self.dialog("Save your changes?",
                                 "You changed some keys or settings but did not save them to the deck.",
                                 [("Cancel", "ghost", None), ("Don't save", "normal", "no"),
                                  ("Save to deck", "primary", "yes")])
            if answer is None or (answer == "yes" and not self.save()):
                return
        if self.stop_recording:
            self.stop_recording()
        self.painter.ask("stop")
        self.root.destroy()

    # ------------------------------------------------------------- views
    def set_view(self, view):
        self.view = view
        for name, (lab, bar) in self.view_tabs.items():
            on = name == view
            lab.configure(fg=TEXT if on else MUTED)
            bar.configure(bg=ACCENT if on else BG)
        if view == "keys":
            self.settings_view.pack_forget()
            self.keys_view.pack(fill="both", expand=True)
        else:
            if self.stop_recording:
                self.stop_recording()
            self.keys_view.pack_forget()
            self.settings_view.pack(fill="both", expand=True)
            self.sync_settings()
            self.schedule_themes()

    # ------------------------------------------------------------- pages and the deck
    def draw_tabs(self):
        n = len(self.work["pages"])
        if self.tabs_drawn == (n, self.page):
            return
        self.tabs_drawn = (n, self.page)
        for w in self.page_tabs.winfo_children():
            w.destroy()
        if n > 4:
            tk.Label(self.page_tabs, text="Page", bg=BG, fg=MUTED, font=FONT["body"]).pack(side="left", padx=(0, 6))
        for i in range(n):
            Btn(self.page_tabs, f"Page {i + 1}" if n <= 4 else str(i + 1), lambda i=i: self.go_page(i),
                kind="primary" if i == self.page else "normal", font=FONT["bold"]).pack(side="left", padx=(0, 6))
        if n < MAX_PAGES:
            Btn(self.page_tabs, "+  Add page", self.add_page, kind="ghost").pack(side="left", padx=(4, 0))
        self.btn_del_page.pack_forget()
        if n > 1:
            self.btn_del_page.pack(side="right")

    def go_page(self, i):
        if self.stop_recording:
            self.stop_recording()
        self.page, self.sel = i, None
        self.draw_tabs()
        self.draw_deck()
        self.render_editor()
        self.schedule_preview(now=True)

    def add_page(self):
        def change():
            self.work["pages"].append({"8": copy.deepcopy(PAGE_KEY)})
            self.page, self.sel = len(self.work["pages"]) - 1, None
        self.edit("", change)
        self.render_editor()
        self.toast(f"Added page {self.page + 1}. Its middle key flips to the next page.")

    def delete_page(self):
        if len(self.work["pages"]) < 2 or not self.ask(
                f"Delete page {self.page + 1}?", "All the buttons on this page will be removed.",
                "Delete page", danger=True):
            return

        def change():
            del self.work["pages"][self.page]
            self.page, self.sel = min(self.page, len(self.work["pages"]) - 1), None
        self.shown.clear()
        self.edit("", change)
        self.render_editor()

    def _resized(self):
        if self.resize_timer:
            self.root.after_cancel(self.resize_timer)
        self.resize_timer = self.root.after(150, self._size_keys)

    def _size_keys(self):
        """Make the keys as big as the window allows (between 64 and 136 pixels)."""
        self.resize_timer = None
        w, h = self.stage.winfo_width(), self.stage.winfo_height()
        if w < 50:
            w, h = 860, 700
        extra_h = self.pagebar.winfo_reqheight() + self.deck_hint.winfo_reqheight() + 100
        per_w = (w - 2 * 30 - 34 - 5 * 6) / 5 - 8
        per_h = (h - extra_h - 34 - 3 * 6) / 3 - 8
        k = int(max(64, min(136, per_w, per_h)))
        k -= k % 4
        if k != self.kpx or not self.key_canvases[1].winfo_reqwidth() > 10:
            self.kpx = k
            self.shown.clear()
            for c in self.key_canvases.values():
                c.configure(width=k + 8, height=k + 8)
            self.draw_deck()
            self.schedule_preview(now=True)

    def schedule_preview(self, now=False):
        if self.preview_timer:
            self.root.after_cancel(self.preview_timer)
        self.preview_timer = self.root.after(1 if now else 120, self._ask_preview)

    def _ask_preview(self):
        self.preview_timer = None
        self.painter.ask("deck", copy.deepcopy(self.work["pages"]), dict(self.work["settings"]),
                         self.page, self.kpx)

    def _got_deck(self, args, pictures):
        if len(self.photos) > 500:
            self.photos.clear()
        for pid, img in pictures.values():
            if pid not in self.photos:
                self.photos[pid] = ImageTk.PhotoImage(img)
        self.draw_deck()

    def photo_for(self, pos):
        """Newest picture of a key; the previous one stays until the new one has been drawn."""
        k = self.keys().get(str(pos))
        if not k:
            return None
        pid = Core.picture_id(k, self.work["settings"], self.page, len(self.work["pages"]), self.kpx)
        photo = self.photos.get(pid)
        if photo is not None:
            self.shown[(self.page, pos)] = photo
        return self.shown.get((self.page, pos))

    def draw_deck(self):
        for pos in self.key_canvases:
            self.draw_key(pos)
        stuck = len(self.work["pages"]) > 1 and not any(
            (k.get("action") or {}).get("type") == "page" for k in self.keys().values())
        if stuck and not self.stuck.winfo_ismapped():
            self.stuck.pack(before=self.deck_hint, pady=(12, 0))
        elif not stuck:
            self.stuck.pack_forget()
        if self.sel and getattr(self, "ed_pic", None) is not None and self.ed_pic.winfo_exists():
            photo = self.photo_for(int(self.sel))
            self.ed_pic.configure(image=photo or "", text="" if photo else "+")

    def draw_key(self, pos):
        c, n, m = self.key_canvases[pos], self.kpx, 4
        c.delete("all")
        k = self.keys().get(str(pos))
        selected = str(pos) == self.sel
        ring = GOOD if pos == self.drop else ACCENT if selected else "#596277" if pos == self.hover else None
        if k:
            photo = self.photo_for(pos)
            if photo is not None:
                c.create_image(m, m, image=photo, anchor="nw")
            else:
                round_rect(c, m, m, m + n, m + n, n * 0.16, fill=PANEL2, outline="")
            if pos == self.drag:
                c.create_rectangle(m, m, m + n, m + n, fill=DECK, outline="", stipple="gray50")
        else:
            fill = ACCENT_SOFT if selected or pos == self.hover else ""
            round_rect(c, m + 2, m + 2, m + n - 2, m + n - 2, n * 0.16, fill=fill,
                       outline=ACCENT if selected else "#3a4150", dash=(4, 4), width=2)
            c.create_text(m + n / 2, m + n / 2, text="+", font=FONT["plus"],
                          fill=ACCENT if selected or pos == self.hover else "#566075")
        if ring:
            round_rect(c, 2, 2, n + 2 * m - 2, n + 2 * m - 2, n * 0.2, fill="", outline=ring, width=3)

    # click, drag and drop on the keys
    def _hover(self, pos):
        old, self.hover = self.hover, pos
        for p in (old, pos):
            if p:
                self.draw_key(p)

    def _press(self, pos, e):
        self.press_at = (pos, e.x_root, e.y_root)

    def _motion(self, pos, e):
        start = getattr(self, "press_at", None)
        if not start or not self.keys().get(str(pos)):
            return
        if self.drag is None and abs(e.x_root - start[1]) + abs(e.y_root - start[2]) > 8:
            self.drag = pos
            self.root.configure(cursor="fleur")
        if self.drag is not None:
            target = self._key_at(e.x_root, e.y_root)
            target = target if target != self.drag else None
            if target != self.drop:
                old, self.drop = self.drop, target
                for p in (old, target, self.drag):
                    if p:
                        self.draw_key(p)

    def _key_at(self, x, y):
        w = self.root.winfo_containing(x, y)
        for pos, c in self.key_canvases.items():
            if w is c:
                return pos
        return None

    def _release(self, pos, e):
        drag, drop = self.drag, self.drop
        self.drag = self.drop = self.press_at = None
        self.root.configure(cursor="")
        if drag is None:
            self.select(str(pos))
        elif drop:
            self.swap(str(drag), str(drop))
        else:
            self.draw_deck()

    def select(self, pos):
        if self.stop_recording:
            self.stop_recording()
        self.sel = pos
        self.icon_tab, self.icon_search = None, ""
        self.draw_deck()
        self.render_editor()
        self.editor.top()

    def swap(self, a, b):
        def change():
            p = self.keys()
            ka, kb = p.get(a), p.get(b)
            p[b] = ka
            if kb:
                p[a] = kb
            else:
                p.pop(a, None)
            sa, sb = self.shown.pop((self.page, int(a)), None), self.shown.pop((self.page, int(b)), None)
            if sb:
                self.shown[(self.page, int(a))] = sb
            if sa:
                self.shown[(self.page, int(b))] = sa
            self.sel = b
        self.edit("", change)
        self.render_editor()

    def copy_key(self):
        k = self.sel_key()
        if k:
            self.clip = copy.deepcopy(k)
            self.toast("Copied. Click another key and press Paste.")
            self.render_editor()

    def paste_key(self):
        if self.clip and self.sel:
            self.edit("", lambda: self.keys().__setitem__(self.sel, copy.deepcopy(self.clip)))
            self.render_editor()

    def clear_key(self):
        if self.sel_key():
            self.edit("", lambda: self.keys().pop(self.sel, None))
            self.render_editor()

    # ------------------------------------------------------------- the editor panel
    def set_key(self, tag, change):
        """Change the selected key (the panel is not rebuilt, so typing keeps its place)."""
        self.edit(tag, lambda: change(self.keys()[self.sel]))

    def set_act(self, tag, name, value):
        self.set_key(tag, lambda k: k["action"].__setitem__(name, value))

    def set_kind(self, kind):
        if kind == self.kind_of(self.sel_key()):
            return

        def change():
            p = self.keys()
            k = p.get(self.sel) or {"color": (self.lists["colors"] or ["#1f6feb"])[0]}
            if kind == "empty":
                p.pop(self.sel, None)
                return
            if kind == "button":
                k.pop("widget", None)
                k.pop("hour24", None)
                if not k.get("label") and not k.get("icon"):
                    k["label"] = "New"
            else:
                for name in ("icon", "icon_file", "label"):
                    k.pop(name, None)
                k["widget"] = next(iter(self.lists["widgets"]))
            p[self.sel] = k
        self.edit("", change)
        self.render_editor()

    def render_editor(self):
        if self.stop_recording:
            self.stop_recording()
        box = self.editor.inner
        for w in box.winfo_children():
            w.destroy()
        self.ed_pic = None
        pad = tk.Frame(box, bg=PANEL, padx=22, pady=20)
        pad.pack(fill="both", expand=True)
        if not self.sel:
            return self._editor_empty(pad)
        k, pos = self.sel_key(), int(self.sel)
        kind = self.kind_of(k)

        head = tk.Frame(pad, bg=PANEL)
        head.pack(fill="x")
        self.ed_pic = tk.Label(head, bg=DECK, fg="#566075", font=FONT["plus"], bd=0, padx=5, pady=5)
        self.ed_pic.pack(side="left")
        if not k:
            self.ed_pic.configure(text="+", width=4, height=2)
        title = tk.Frame(head, bg=PANEL)
        title.pack(side="left", fill="x", expand=True, padx=(16, 0))
        tk.Label(title, text=f"Key {pos}", font=FONT["h1"], bg=PANEL, fg=TEXT, anchor="w").pack(fill="x")
        tk.Label(title, text=f"Row {(pos - 1) // 5 + 1}, column {(pos - 1) % 5 + 1}  ·  Page {self.page + 1}",
                 font=FONT["small"], bg=PANEL, fg=MUTED, anchor="w").pack(fill="x")
        row = tk.Frame(title, bg=PANEL)
        row.pack(fill="x", pady=(8, 0))
        for text, fn, on in (("Copy", self.copy_key, bool(k)), ("Paste", self.paste_key, bool(self.clip)),
                             ("Clear", self.clear_key, bool(k))):
            b = Btn(row, text, fn, small=True)
            b.set_enabled(on)
            b.pack(side="left", padx=(0, 6))

        self._caption(pad, "This key is")
        Seg(pad, [("button", "Button", "Icon and name"), ("live", "Live info", "Clock, music…"),
                  ("empty", "Empty", "Nothing here")], kind, self.set_kind).pack(fill="x")
        if kind == "button":
            self._button_parts(pad, k)
        elif kind == "live":
            self._live_parts(pad, k)
        else:
            self._hint(pad, "This key is empty. Choose “Button” or “Live info” above to put something here.")
        if kind != "empty":
            self._action_parts(pad, k)
        self.draw_deck()

    def _editor_empty(self, pad):
        art = tk.Canvas(pad, width=170, height=60, bg=PANEL, highlightthickness=0)
        art.pack(pady=(70, 18))
        for i in range(3):
            x = 10 + i * 54
            sel = i == 1
            round_rect(art, x, 8, x + 44, 52, 9, fill=ACCENT_SOFT if sel else "",
                       outline=ACCENT if sel else LINE2, width=2, dash=() if sel else (4, 4))
        tk.Label(pad, text="Pick a key", font=FONT["h1"], bg=PANEL, fg=TEXT).pack()
        tk.Label(pad, text="Click any key on the left to choose what it shows and what it does "
                           "when you press it.", font=FONT["body"], bg=PANEL, fg=MUTED,
                 wraplength=300, justify="center").pack(pady=(6, 0))

    def _caption(self, parent, text):
        tk.Label(parent, text=text.upper(), font=FONT["cap"], bg=parent.cget("bg"), fg=MUTED,
                 anchor="w").pack(fill="x", pady=(22, 8))

    def _hint(self, parent, text, top=8):
        tk.Label(parent, text=text, font=FONT["small"], bg=parent.cget("bg"), fg=MUTED, anchor="w",
                 justify="left", wraplength=330).pack(fill="x", pady=(top, 0))

    def _field(self, parent, label):
        tk.Label(parent, text=label, font=FONT["small"], bg=parent.cget("bg"), fg=MUTED,
                 anchor="w").pack(fill="x", pady=(14, 5))

    # --- Button: name, icon, colour
    def _button_parts(self, pad, k):
        self._field(pad, "Name")

        def named(text):
            self.set_key("label", lambda key: key.__setitem__("label", text) if text.strip()
                         else key.pop("label", None))
        frame, ent, _ = self._entry(pad, k.get("label", ""), named)
        frame.pack(fill="x")
        self._icon_picker(pad, k)
        self._color_field(pad, k)

    def _icon_picker(self, pad, k):
        head = tk.Frame(pad, bg=PANEL)
        head.pack(fill="x", pady=(14, 5))
        tk.Label(head, text="Icon", font=FONT["small"], bg=PANEL, fg=MUTED).pack(side="left")
        if k.get("icon") or k.get("icon_file"):
            Btn(head, "No icon", lambda: (self.set_key("", lambda key: (key.pop("icon", None),
                                                                        key.pop("icon_file", None))),
                                          self.render_editor()), kind="ghost", small=True).pack(side="right")
        built_in = k.get("icon") in self.lists["icons"]
        tab = self.icon_tab or ("picture" if k.get("icon_file") else
                                "emoji" if k.get("icon") and not built_in else "builtin")
        box = tk.Frame(pad, bg=CARD2, highlightthickness=1, highlightbackground=LINE, padx=8, pady=8)
        box.pack(fill="x")

        def set_tab(t):
            self.icon_tab = t
            self.render_editor()
        Seg(box, [("builtin", "Icons", None), ("emoji", "Emoji", None), ("picture", "My picture", None)],
            tab, set_tab).pack(fill="x")
        body = tk.Frame(box, bg=CARD2)
        body.pack(fill="x", pady=(8, 0))

        def use_icon(name):
            self.set_key("", lambda key: (key.__setitem__("icon", name), key.pop("icon_file", None)))
            self.render_editor()

        if tab == "builtin":
            grid = tk.Frame(body, bg=CARD2)

            def fill(_text=None):
                self.icon_search = search_var.get()
                q = self.icon_search.strip().lower()
                names = [n for n in self.lists["icons"]
                         if not q or q in n.lower() or q in str(self.lists["icons"][n]).lower()]
                self._icon_grid(grid, names[:72], k.get("icon") if not k.get("icon_file") else None, use_icon)
                if not names:
                    tk.Label(grid, text="No icon with that name. Try the Emoji tab.", bg=CARD2, fg=MUTED,
                             font=FONT["small"]).grid(row=0, column=0, columnspan=6, sticky="w")
                elif len(names) > 72:
                    tk.Label(grid, text=f"Type to find more ({len(names)} icons).", bg=CARD2, fg=MUTED,
                             font=FONT["small"]).grid(row=99, column=0, columnspan=6, sticky="w")
            frame, ent, search_var = self._entry(body, self.icon_search, fill)
            frame.pack(fill="x")
            tk.Label(frame, text="⌕", bg=FIELD, fg=MUTED, font=FONT["body"]).place(relx=1.0, x=-6, rely=0.5, anchor="e")
            grid.pack(fill="x", pady=(8, 0))
            fill()
        elif tab == "emoji":
            row = tk.Frame(body, bg=CARD2)
            row.pack(fill="x")

            def typed(text):
                text = text.strip()
                self.set_key("emoji", lambda key: (key.__setitem__("icon", text), key.pop("icon_file", None))
                             if text else key.pop("icon", None))
            frame, ent, _ = self._entry(row, k.get("icon", "") if not built_in else "", typed,
                                        font=FONT["emoji"], width=4)
            frame.pack(side="left")
            tk.Label(row, text="Type or paste an emoji (Windows key + . opens the emoji list), or pick one:",
                     bg=CARD2, fg=MUTED, font=FONT["small"], wraplength=230, justify="left").pack(
                side="left", padx=(10, 0))
            grid = tk.Frame(body, bg=CARD2)
            grid.pack(fill="x", pady=(8, 0))
            self._icon_grid(grid, EMOJI, k.get("icon"), use_icon)
        else:
            row = tk.Frame(body, bg=CARD2)
            row.pack(fill="x")
            Btn(row, "Choose a different picture…" if k.get("icon_file") else "Use my own picture…",
                self.pick_icon).pack(side="left")
            if k.get("icon_file"):
                Btn(row, "Remove", lambda: (self.set_key("", lambda key: key.pop("icon_file", None)),
                                            self.render_editor()), kind="ghost").pack(side="left", padx=6)
            self._hint(body, "Using: " + k["icon_file"].split("/")[-1] if k.get("icon_file") else
                       "Pick any picture from your PC (PNG, JPG…). Square pictures with a see-through "
                       "background look best.")

    def _icon_grid(self, grid, names, current, on_pick):
        for w in grid.winfo_children():
            w.destroy()
        for i, name in enumerate(names):
            photo = None
            if self.icon_pics and name in self.icon_pics:
                photo = self.icon_photos.get(name)
                if photo is None:
                    photo = self.icon_photos[name] = ImageTk.PhotoImage(self.icon_pics[name])
            on = name == current
            cell = tk.Label(grid, image=photo or "", text="" if photo else "…", width=44 if photo else 4,
                            height=44 if photo else 2, bg=ACCENT_SOFT if on else PANEL2, fg=MUTED,
                            highlightthickness=1, highlightbackground=ACCENT if on else PANEL2,
                            cursor="hand2", bd=0)
            cell.grid(row=i // 6, column=i % 6, padx=2, pady=2)
            cell.bind("<Button-1>", lambda e, n=name: on_pick(n))
            cell.bind("<Enter>", lambda e, c=cell, o=on: o or c.configure(highlightbackground=LINE2))
            cell.bind("<Leave>", lambda e, c=cell, o=on: o or c.configure(highlightbackground=PANEL2))

    def pick_icon(self):
        sel, page = self.sel, self.page
        path = filedialog.askopenfilename(parent=self.root, title="Choose a picture for this key",
                                          filetypes=[("Pictures", " ".join("*" + e for e in ICON_TYPES))])
        if not path:
            return
        try:
            rel = self.core.copy_icon(path)
        except Problem as e:
            return self.toast(str(e), "bad")
        if (sel, page) != (self.sel, self.page) or not self.sel_key():
            return
        self.set_key("", lambda key: (key.__setitem__("icon_file", rel), key.pop("icon", None)))
        self.render_editor()

    def _color_field(self, pad, k):
        self._field(pad, "Colour")
        now = k.get("color", "#333333").lower() if HEX.fullmatch(k.get("color", "")) else "#333333"
        swatches = tk.Frame(pad, bg=PANEL)
        swatches.pack(fill="x")
        rings = []

        def draw_rings(color):
            for c, col in rings:
                c.delete("ring")
                if col.lower() == color.lower():
                    c.create_oval(1, 1, 29, 29, outline="#ffffff", width=2, tags="ring")

        for i, col in enumerate(self.lists["colors"]):
            c = tk.Canvas(swatches, width=30, height=30, bg=PANEL, highlightthickness=0, cursor="hand2")
            c.create_oval(4, 4, 26, 26, fill=col, outline="")
            c.grid(row=i // 9, column=i % 9, padx=(0, 6), pady=(0, 6))
            c.bind("<Button-1>", lambda e, col=col: apply(col, ""))
            rings.append((c, col))
        row = tk.Frame(pad, bg=PANEL)
        row.pack(fill="x", pady=(4, 0))
        chip = tk.Frame(row, width=34, height=32, bg=now, highlightthickness=1, highlightbackground=LINE2)
        chip.pack(side="left")
        guard = {"busy": False}

        def typed(text):
            if not guard["busy"] and HEX.fullmatch(text.strip()):
                apply(text.strip(), "color", from_box=True)
        frame, ent, var = self._entry(row, now, typed, font=FONT["mono"], width=9)
        frame.pack(side="left", padx=8)

        def apply(color, tag, from_box=False):
            color = color.lower()
            self.set_key(tag, lambda key: key.__setitem__("color", color))
            chip.configure(bg=color)
            draw_rings(color)
            if not from_box:
                guard["busy"] = True
                var.set(color)
                guard["busy"] = False

        def wheel():
            got = colorchooser.askcolor(color=(self.sel_key() or {}).get("color", now), parent=self.root,
                                        title="Pick any colour")
            if got and got[1]:
                apply(got[1], "")
        Btn(row, "More colours…", wheel).pack(side="left")
        draw_rings(now)

    # --- Live info
    def _live_parts(self, pad, k):
        self._field(pad, "Show")
        grid = tk.Frame(pad, bg=PANEL)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1, uniform="w")
        grid.columnconfigure(1, weight=1, uniform="w")
        items = list(self.lists["widgets"].items())
        if k.get("widget") not in self.lists["widgets"]:
            items.insert(0, (k.get("widget"), {"label": str(k.get("widget")),
                                               "desc": "A special kind this app does not know. It is kept."}))

        def pick(name):
            def change(key):
                key["widget"] = name
                if name != "clock":
                    key.pop("hour24", None)
            self.set_key("", change)
            self.render_editor()
        for i, (name, w) in enumerate(items):
            on = name == k.get("widget")
            bg = ACCENT_SOFT if on else PANEL2
            cell = tk.Frame(grid, bg=bg, highlightthickness=1, highlightbackground=ACCENT if on else LINE,
                            padx=10, pady=7, cursor="hand2")
            cell.grid(row=i // 2, column=i % 2, sticky="nsew", padx=(0 if i % 2 else 0, 0 if i % 2 else 6), pady=(0, 6))
            a = tk.Label(cell, text=w["label"], font=FONT["bold"], bg=bg, fg=TEXT, anchor="w")
            a.pack(fill="x")
            b = tk.Label(cell, text=w["desc"], font=FONT["small"], bg=bg, fg=MUTED, anchor="w", justify="left",
                         wraplength=150)
            b.pack(fill="x")
            for x in (cell, a, b):
                x.bind("<Button-1>", lambda e, n=name: pick(n))
        if k.get("widget") == "clock":
            box = tk.Frame(pad, bg=PANEL)
            box.pack(fill="x", pady=(12, 0))
            sw = self._switch_row(box, "24-hour clock", "15:30 instead of 3:30 PM",
                                  lambda v: self.set_key("", lambda key: key.__setitem__("hour24", True) if v
                                                         else key.pop("hour24", None)), big=False)
            sw.set(bool(k.get("hour24")))
        self._color_field(pad, k)

    # --- When pressed
    def _action_parts(self, pad, k):
        self._caption(pad, "When pressed")
        a = k.get("action") or {}
        kind = a.get("type", "")
        ids = [i for i, _ in ACTIONS]
        names = [n for _, n in ACTIONS]
        if kind not in ids:
            ids.append(kind)
            names.append(f"Something special (“{kind}”)")

        def picked(index):
            t = ids[index]

            def change(key):
                if not t:
                    key.pop("action", None)
                    return
                key["action"] = {"type": t, **copy.deepcopy(NEW_ACTION.get(t, {}))}
                if t == "game":
                    key["action"]["name"] = next(iter(self.lists["games"]))
                if t == "toy":
                    key["action"]["name"] = next(iter(self.lists["toys"]))
            if t != kind:
                self.set_key("", change)
                self.render_editor()
        self._combo(pad, names, ids.index(kind), picked).pack(fill="x")
        box = tk.Frame(pad, bg=CARD2, highlightthickness=1, highlightbackground=LINE, padx=14, pady=4)
        if self._action_fields(box, a):
            box.pack(fill="x", pady=(10, 0))
            tk.Frame(box, bg=CARD2, height=10).pack()

    def _action_fields(self, box, a):
        """The boxes one kind of action needs. Returns False when it needs none."""
        t = a.get("type")

        def text_field(label, name, tag=None):
            self._field(box, label)
            frame, ent, var = self._entry(box, str(a.get(name, "")), lambda v: self.set_act(tag or name, name, v))
            frame.pack(fill="x")
            return var

        def browse_row(var, folder=True):
            row = tk.Frame(box, bg=CARD2)
            row.pack(fill="x", pady=(8, 0))

            def browse(kind):
                if kind == "folder":
                    path = filedialog.askdirectory(parent=self.root, title="Choose a folder to open")
                else:
                    path = filedialog.askopenfilename(
                        parent=self.root, title="Choose an app or file to open",
                        filetypes=[("Apps and shortcuts", "*.exe *.lnk *.url *.bat *.cmd"), ("All files", "*.*")])
                if path:
                    var.set(os.path.normpath(path))
            Btn(row, "Browse…", lambda: browse("file")).pack(side="left")
            if folder:
                Btn(row, "Choose a folder…", lambda: browse("folder")).pack(side="left", padx=6)

        if t == "launch":
            var = text_field("What to open (an app, a file, a folder or a link)", "target")
            browse_row(var)
            self._hint(box, "Press Browse to find the app or file on your PC. You can also type a link "
                            "such as spotify:")
        elif t == "launch_admin":
            var = text_field("App to open", "target")
            browse_row(var, folder=False)
            text_field("Extra options (you can leave this empty)", "args")
            self._hint(box, "For apps that need administrator rights. Windows may ask “Do you want to "
                            "allow this app…?” each time.")
        elif t == "url":
            text_field("Website address", "target")
        elif t == "hotkey":
            self._hotkey_fields(box, a)
        elif t == "text":
            self._field(box, "Text to type")
            txt = tk.Text(box, height=3, bg=FIELD, fg=TEXT, insertbackground=TEXT, font=FONT["body"], bd=0,
                          highlightthickness=1, highlightbackground=LINE2, highlightcolor=ACCENT, padx=8,
                          pady=6, wrap="word")
            txt.insert("1.0", str(a.get("target", "")))
            txt.pack(fill="x")
            txt.bind("<KeyRelease>", lambda e: self.set_act("target", "target", txt.get("1.0", "end-1c")))
            self._hint(box, "The deck types this for you, as if you typed it on the keyboard.")
        elif t == "command":
            text_field("Command", "target")
            self._hint(box, "For people who know what they are doing: the deck runs exactly what is written here.")
        elif t in ("volume", "brightness"):
            vol = t == "volume"
            delta = int(a.get("delta") or 0)
            up, amount = delta >= 0, abs(delta) or (4 if vol else 10)

            def direction(is_up):
                self.set_act("", "delta", (1 if is_up else -1) * abs(int(self.sel_key()["action"].get("delta") or amount)))
                self.render_editor()
            tk.Frame(box, bg=CARD2, height=10).pack()
            Seg(box, [(True, "Louder" if vol else "Brighter", None), (False, "Quieter" if vol else "Dimmer", None)],
                up, direction).pack(fill="x")
            self._field(box, "How much per press")
            self._slider_row(box, 1 if vol else 5, 20 if vol else 50, 1 if vol else 5, amount,
                             lambda v: self.set_act("delta", "delta", v if up else -v))
        elif t == "page":
            to = a.get("to", "next")
            mode = to if to in ("next", "prev") else "number"

            def go(value):
                self.set_act("", "to", 1 if value == "number" else value)
                self.render_editor()
            tk.Frame(box, bg=CARD2, height=10).pack()
            Seg(box, [("next", "Next page", None), ("prev", "Previous page", None), ("number", "A certain page", None)],
                mode, go).pack(fill="x")
            if mode == "number":
                self._field(box, "Which page")
                n = max(len(self.work["pages"]), int(to))
                labels = [f"Page {i + 1}" + ("  (does not exist yet)" if i >= len(self.work["pages"]) else "")
                          for i in range(n)]
                self._combo(box, labels, int(to) - 1, lambda i: self.set_act("", "to", i + 1)).pack(fill="x")
        elif t in ("game", "toy"):
            items = self.lists["games"] if t == "game" else self.lists["toys"]
            ids, labels = list(items), list(items.values())
            if a.get("name") not in items:
                ids.append(a.get("name", ""))
                labels.append(str(a.get("name", "")))
            self._field(box, "Which game" if t == "game" else "Which one")
            self._combo(box, labels, ids.index(a.get("name", "")), lambda i: self.set_act("", "name", ids[i])).pack(fill="x")
            if t == "game":
                self._hint(box, "In every game the bottom-right key is Exit.")
        elif t == "obsidian":
            ids = [i for i, _ in OBSIDIAN_DO]
            do = a.get("do") if a.get("do") in ids else "open"
            self._field(box, "What to do")
            self._combo(box, [n for _, n in OBSIDIAN_DO], ids.index(do),
                        lambda i: self.set_act("", "do", ids[i])).pack(fill="x")
            var = text_field("Vault folder (leave empty for the one Obsidian last had open)", "vault")
            row = tk.Frame(box, bg=CARD2)
            row.pack(fill="x", pady=(8, 0))

            def pick_vault():
                path = filedialog.askdirectory(parent=self.root, title="Choose your Obsidian vault folder")
                if path:
                    var.set(os.path.normpath(path))
            Btn(row, "Choose a folder…", pick_vault).pack(side="left")
            self._hint(box, "Daily notes go in a “Daily” folder and new notes in “Inbox”. "
                            "“Add the copied text” puts whatever you copied (Ctrl+C) at the end of Inbox.md "
                            "without opening Obsidian.")
        elif t in ("previous_song", "spotify_like"):
            self._hint(box, "Works with the Spotify app on this PC.")
        elif t == "sleep":
            self._hint(box, "Turns the key screens off. Press any key to wake the deck up again.")
        elif t == "quit":
            self._hint(box, "The deck asks for a second press, so you cannot quit by accident.")
        elif t == "game_mode":
            self._hint(box, "Press twice. Closes open apps using over 150 MB of memory, the same way as "
                            "clicking their X, so anything unsaved still asks first. Also fully quits "
                            "LM Studio (frees its AI model from the graphics card) and CurseForge. "
                            "Never closes Steam, Discord, Spotify, game launchers, OBS, Claude or Windows.")
        elif t == "stopwatch":
            self._hint(box, "Goes well with the “Stopwatch” live info key.")
        else:
            return False
        return True

    # --- keyboard shortcut recorder
    KEY_NAMES = {"Return": "enter", "Escape": "esc", "Tab": "tab", "space": "space", "BackSpace": "backspace",
                 "Delete": "delete", "Up": "up", "Down": "down", "Left": "left", "Right": "right",
                 "Home": "home", "End": "end", "Prior": "page_up", "Next": "page_down"}
    MODS = {"Control_L": "ctrl", "Control_R": "ctrl", "Shift_L": "shift", "Shift_R": "shift",
            "Alt_L": "alt", "Alt_R": "alt", "Win_L": "cmd", "Win_R": "cmd", "Meta_L": "cmd"}
    NICE = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "cmd": "Windows", "esc": "Esc",
            "page_up": "Page Up", "page_down": "Page Down"}

    def nice_shortcut(self, target):
        for mid, name in MEDIA_KEYS:
            if mid == target:
                return [name + " (music key)"]
        return [self.NICE.get(p, p.upper() if len(p) == 1 else p.capitalize())
                for p in str(target or "").split("+") if p]

    def _key_name(self, e):
        if e.keysym in self.KEY_NAMES:
            return self.KEY_NAMES[e.keysym]
        if re.fullmatch(r"F\d{1,2}", e.keysym):
            return e.keysym.lower()
        if 65 <= e.keycode <= 90 or 48 <= e.keycode <= 57:   # letters and digits, whatever Shift does
            return chr(e.keycode).lower()
        return e.char.lower() if len(e.char) == 1 and e.char.isprintable() else ""

    def _hotkey_fields(self, box, a):
        self._field(box, "Shortcut")
        chips = tk.Frame(box, bg=CARD2)
        chips.pack(fill="x", pady=(0, 8))
        row = tk.Frame(box, bg=CARD2)
        row.pack(fill="x")
        rec = Btn(row, "Record shortcut")
        rec.pack(side="left")
        media_ids = [""] + [m for m, _ in MEDIA_KEYS]
        guard = {"busy": False}

        def show(parts=None):
            for w in chips.winfo_children():
                w.destroy()
            parts = parts if parts is not None else self.nice_shortcut(var.get().strip().lower())
            if not parts:
                tk.Label(chips, text="No shortcut yet", bg=CARD2, fg=DIM, font=FONT["body"]).pack(side="left", pady=4)
            for i, p in enumerate(parts):
                if i:
                    tk.Label(chips, text="+", bg=CARD2, fg=MUTED).pack(side="left", padx=3)
                tk.Label(chips, text=p, bg=PANEL2, fg=TEXT, font=FONT["kbd"], padx=9, pady=4,
                         highlightthickness=1, highlightbackground=LINE2).pack(side="left")
            target = var.get().strip().lower()
            media.current(media_ids.index(target) if target in media_ids else 0)

        def typed(text):
            if not guard["busy"]:
                self.set_act("target", "target", text.strip().lower())
            show()

        def set_target(value):
            guard["busy"] = True
            var.set(value)
            guard["busy"] = False
            self.set_act("", "target", value)
            show()

        media = self._combo(row, ["Or pick a music key…"] + [n for _, n in MEDIA_KEYS], 0,
                            lambda i: i and set_target(media_ids[i]))
        media.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self._field(box, "Or type it (for example ctrl+shift+s)")
        frame, ent, var = self._entry(box, str(a.get("target", "")), typed, font=FONT["mono"])
        frame.pack(fill="x")
        self._hint(box, "Hold Ctrl, Shift, Alt or the Windows key and press one more key. Windows keeps some "
                        "Windows-key shortcuts for itself: type those instead, with “cmd” for the Windows key "
                        "(for example cmd+d).")
        self._hint(box, "If the shortcut belongs to another app, set the same shortcut inside that app too. "
                        "For Discord mute: in Discord open Settings › Keybinds and set “Toggle Mute” to these keys.")

        held = set()

        def key_down(e):
            mod = self.MODS.get(e.keysym)
            if mod:
                held.add(mod)
                show([self.NICE[m] for m in ("ctrl", "shift", "alt", "cmd") if m in held] + ["…"])
                return "break"
            name = self._key_name(e)
            if not name:
                return "break"
            mods = set(held)
            if e.state & 0x4:
                mods.add("ctrl")
            if e.state & 0x1 and (len(name) > 1 or mods):
                mods.add("shift")
            combo = "+".join([m for m in ("ctrl", "shift", "alt", "cmd") if m in mods] + [name])
            stop()
            set_target(combo)
            return "break"

        def key_up(e):
            held.discard(self.MODS.get(e.keysym))
            return "break"

        def stop():
            self.root.unbind("<KeyPress>")
            self.root.unbind("<KeyRelease>")
            self.stop_recording = None
            held.clear()
            if rec.winfo_exists():
                rec.set_kind("normal")
                rec.configure(text="Record shortcut")
                show()

        def start():
            if self.stop_recording:
                return stop()
            self.root.focus_set()
            self.root.bind("<KeyPress>", key_down)
            self.root.bind("<KeyRelease>", key_up)
            rec.set_kind("rec")
            rec.configure(text="Press the keys now…  (click to cancel)")
            self.stop_recording = stop
            show(["…"])
        rec.command = start
        show()

    # ------------------------------------------------------------- settings view
    def set_setting(self, tag, name, value):
        self.edit(tag, lambda: self.work["settings"].__setitem__(name, value))

    def sync_settings(self):
        """Put the current settings into the controls (without rebuilding them)."""
        s = self.work["settings"]
        self.s_bright.set(s["brightness"])
        self.s_bright_out.configure(text=f"{s['brightness']}%")
        self.s_saver_bright.set(s["saver_brightness"])
        self.s_saver_bright_out.configure(text=f"{s['saver_brightness']}%")
        self.s_sounds.set(s["sounds"])
        self.s_saver_on.set(s["saver_enabled"])
        self.s_startup.set(self.startup)
        if s["saver_enabled"]:
            self.saver_more.pack(fill="x")
        else:
            self.saver_more.pack_forget()
        styles = list(self.lists["savers"])
        if s["saver_style"] in styles:
            self.s_style.current(styles.index(s["saver_style"]))
        for name, (cell, pics, title, slots) in self.theme_cards.items():
            on = name == s["theme"]
            cell.configure(highlightbackground=ACCENT if on else LINE, bg=ACCENT_SOFT if on else CARD2)
            title.configure(bg=ACCENT_SOFT if on else CARD2)
            title.configure(text=self.lists["themes"][name] + ("   ✓" if on else ""))
        self._saver_picture()

    def _saver_picture(self):
        auto = self.lists["savers"].get("auto", "Automatic")
        pic = self.core.saver_thumb(self.work["settings"].get("saver_file", ""), (200, 112))
        if pic is not None:
            self._saver_photo = ImageTk.PhotoImage(pic)
            self.s_thumb.configure(image=self._saver_photo, text="", width=200, height=112)
            self.s_pick.configure(text="Choose a different picture or GIF…")
            self.s_remove.pack(anchor="w", pady=(6, 0))
            self.s_saver_hint.configure(text=f"Your picture is shown when the style is “{auto}”. "
                                             "It is kept straight away - no need to save.")
        else:
            self._saver_photo = None
            self.s_thumb.configure(image="", text="No picture yet", width=26, height=6)
            self.s_pick.configure(text="Use my own picture or GIF…")
            self.s_remove.pack_forget()
            self.s_saver_hint.configure(text=f"Any picture or animated GIF from your PC. It is shown when "
                                             f"the style is “{auto}”.")

    def pick_saver(self):
        path = filedialog.askopenfilename(parent=self.root, title="Choose a picture or GIF for the screen saver",
                                          filetypes=[("Pictures and GIFs", " ".join("*" + e for e in SAVER_TYPES))])
        if path:
            self._saver_done(lambda: self.core.set_saver(path), "Your picture is now the screen saver.")

    def pick_offscreen(self):
        path = filedialog.askopenfilename(parent=self.root, title="Choose a picture for when the PC is off",
                                          filetypes=[("Still pictures", "*.png *.jpg *.jpeg *.bmp")])
        if not path:
            return
        if not self.ask("Save this picture in the D6?",
                        "The keys go dark for about 10 seconds while it saves. Don't unplug the D6 "
                        "during that time. If it ever stays black afterwards, give it a couple of "
                        "minutes - it has come back on its own before.", "Save it"):
            return
        self.toast("Saving the picture in the D6… (about 10 seconds - don't unplug it)")

        def work():
            try:
                self.host.set_offscreen(path)
            except Exception as e:
                raise Problem(f"The D6 couldn't save that picture: {e}")
        self.errand(work, lambda _: self.toast("Saved in the D6. It shows when the PC is off.", "good"))

    def remove_saver(self):
        if self.ask("Remove your picture?", "The screen saver goes back to the built-in styles.", "Remove", True):
            self._saver_done(lambda: self.core.set_saver(None), "Your picture was removed.")

    def _saver_done(self, work, message):
        try:
            name = work()
        except Problem as e:
            return self.toast(str(e), "bad")
        # Kept on the deck straight away, so it is not an "unsaved change".
        self.work["settings"]["saver_file"] = name
        self.saved["settings"]["saver_file"] = name
        self.update_top()
        self.sync_settings()
        self.toast(message, "good")

    def schedule_themes(self):
        if self.theme_timer:
            self.root.after_cancel(self.theme_timer)
        self.theme_timer = self.root.after(200, self._ask_themes)

    def _ask_themes(self):
        self.theme_timer = None
        self.painter.ask("themes", copy.deepcopy(self.work["pages"]), dict(self.work["settings"]), 56)

    def _got_themes(self, out):
        self.theme_photos = []
        for name, (cell, pics, title, slots) in self.theme_cards.items():
            for slot, img in zip(slots, out.get(name, [])):
                photo = ImageTk.PhotoImage(img)
                self.theme_photos.append(photo)
                slot.configure(image=photo, width=56, height=56)


def run(host):
    """Show the settings window and return when it is closed."""
    w = Window(host)
    w.root.mainloop()


if __name__ == "__main__":
    if "--demo" in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))
        from fakes import DemoHost
        run(DemoHost())
    else:
        print("This is the settings window of D6Deck. Try it with:  py -3.14 d6ui.py --demo")
