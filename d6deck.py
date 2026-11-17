#!/usr/bin/env python3
"""
d6deck - local, offline controller app for the Fifine AmpliGame D6
(USB VID 0x3142 / PID 0x0007).

No accounts, no internet, no cloud. Everything lives in this folder:
  d6deck.py     the app itself (this file): the D6, live info, what keys do
  d6look.py     how the keys look (themes, icons, live keys)
  d6fun.py      screen savers, mini-games, toys
  d6ui.py       the settings app (a normal desktop window)
  d6layout.py   the factory-default buttons
  buttons.json  your buttons (what each of the 15 keys shows and does, per page)
  config.json   device setup and options
  state.json    things it remembers (brightness, theme, best scores)
  d6deck.log    short log (useful when it runs in the background)

Commands:
  ui               Open the settings app (set up buttons by clicking).
  run              Run in this window (Ctrl+C to quit). Good for testing.
  start            Start it in the background right now (no window).
  stop             Stop the background copy.
  restart          Stop and start again.
  shortcuts        Put Settings / Start / Stop / Restart icons on your Desktop.
  install-startup  Make it start automatically when you log in to Windows.
  remove-startup   Undo install-startup.
  learn            Press each key in order so the app learns the key layout.
  test-screen      Draw position numbers 1-15 on the key screens.
  check            Show the volume and what Spotify is playing (no D6 needed).
  probe            Diagnostics. Add --active to wake the device first.
Add --debug to print raw packets.

"Screen Off" shows a screen saver; press any key to come back. Put your own picture or
GIF in this folder named screensaver.gif (or .png / .jpg / .webp) and it plays across all
15 keys like one big screen.

Spotify info (cover, title, time left) is read from Windows' own "now playing"
data for the Spotify desktop app. Nothing is sent over the internet.
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

try:
    import hid  # pip install hidapi
    from pynput.keyboard import Controller as KbController, Key  # pip install pynput
    import d6look as look  # needs: pip install pillow
except ImportError as e:
    sys.exit(f"Missing package: {e.name}\n"
             "Install with:  pip install hidapi pillow pynput")
import d6fun as fun
import d6layout

VID, PID = 0x3142, 0x0007
PKT = 512
COLS, ROWS, NKEYS = 5, 3, 15
MIN_BRIGHTNESS = 10

FROZEN = getattr(sys, "frozen", False)  # True when running as D6Deck.exe
SCRIPT = Path(sys.executable if FROZEN else __file__).resolve()
BASE = SCRIPT.parent  # the app folder: your buttons, settings and screen saver live here
if FROZEN and sys.stdout is None:  # the .exe has no console window
    sys.stdout = sys.stderr = open(os.devnull, "w")
CONFIG_PATH = BASE / "config.json"
BUTTONS_PATH = BASE / "buttons.json"
STATE_PATH = BASE / "state.json"
STOP_PATH = BASE / "d6deck.stop"      # "please stop" note left by the stop command
RELOAD_PATH = BASE / "d6deck.reload"  # "settings changed" note left by the settings app
LOG_PATH = BASE / "d6deck.log"
MUTEX_NAME = "Local\\d6deck_single_instance"

DEFAULT_CONFIG = {
    "brightness": 70,
    "image": {"size": 85, "rotate": 180, "flip": False},  # the D6 shows pictures upside down
    "key_map": {},       # raw key code -> position 1..15 (filled by `learn`)
    "display_map": {},   # optional manual override: position -> screen slot
    "flip_rows": True,   # D6 screens are numbered bottom row first; this fixes it
    "interface_index": None,
    # "Screen Off": a screen saver (style: see d6fun.SAVER_STYLES), or fully dark if not enabled.
    "screensaver": {"enabled": True, "brightness": 35, "file": "", "style": "auto"},
    "sounds": True,      # little sound effects in the games
    "theme": "neon",     # the look (see d6look.THEMES)
    # Send several key pictures, then show them together (about twice as fast).
    # Set to false if keys ever fail to update.
    "fast_draw": True,
}


class DeckNotFound(RuntimeError):
    pass


# ----------------------------------------------------------------- logging
def log(msg):
    """Print (if there is a console) and append to d6deck.log."""
    try:
        print(msg)
    except Exception:
        pass
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 200_000:
            LOG_PATH.write_text("")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass


look.log = log


# ----------------------------------------------------------------- config
def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_config():
    """Everything the app needs to know, from config.json + state.json + buttons.json."""
    if not CONFIG_PATH.exists():
        write_json(CONFIG_PATH, DEFAULT_CONFIG)
        print(f"Created {CONFIG_PATH}")
    cfg = read_json(CONFIG_PATH, {}) or {}
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    cfg["screensaver"] = {**DEFAULT_CONFIG["screensaver"], **(cfg.get("screensaver") or {})}
    state = read_json(STATE_PATH, {}) or {}  # what was last chosen on the deck wins
    cfg["state"] = state
    if "brightness" in state:
        cfg["brightness"] = int(state["brightness"])
    if state.get("theme"):
        cfg["theme"] = state["theme"]
    if cfg["theme"] not in look.THEMES:
        cfg["theme"] = next(iter(look.THEMES))
    if state.get("saver_style"):
        cfg["screensaver"]["style"] = state["saver_style"]
    cfg["pages"] = load_buttons()
    cfg["page"] = 0
    cfg["stopwatch"] = {"running": False, "elapsed": 0.0, "since": None}
    return cfg


def load_buttons():
    """Pages of keys come from buttons.json; it is created (or upgraded) from the defaults."""
    b = read_json(BUTTONS_PATH)
    ok = (isinstance(b, dict) and isinstance(b.get("pages"), list) and b["pages"]
          and b.get("version", 1) >= d6layout.LAYOUT_VERSION)
    if not ok:
        if b:
            write_json(BASE / "buttons.backup.json", b)
        elif BUTTONS_PATH.exists():  # a typo made it unreadable: keep your edits, don't wipe them
            try:
                (BASE / "buttons.broken.json").write_bytes(BUTTONS_PATH.read_bytes())
                log("buttons.json has a mistake in it and couldn't be read. Your version was "
                    "saved as buttons.broken.json and the default buttons were restored.")
            except OSError:
                pass
        b = {"version": d6layout.LAYOUT_VERSION, "pages": d6layout.PAGES}
        try:
            write_json(BUTTONS_PATH, b)
        except OSError:
            pass
    return b["pages"]


def all_keys(cfg):
    return [k for page in cfg["pages"] for k in page.values()]


def update_config_file(key, value):
    """Change one setting in config.json without touching the rest."""
    raw = read_json(CONFIG_PATH, {}) or {}
    raw[key] = value
    write_json(CONFIG_PATH, raw)


def save_state(cfg):
    """Remember what was chosen on the deck (brightness, theme, screen saver, best scores)."""
    state = cfg.setdefault("state", {})
    state.update(brightness=cfg["brightness"], theme=cfg["theme"],
                 saver_style=cfg["screensaver"]["style"])
    try:
        write_json(STATE_PATH, state)
    except OSError as e:
        log(f"Could not save state.json: {e}")


# ----------------------------------------------------------------- device
def list_interfaces():
    return hid.enumerate(VID, PID)


def pick_interface(cfg):
    ifs = list_interfaces()
    if not ifs:
        raise DeckNotFound("D6 not found (VID 3142 / PID 0007). Is it plugged in, "
                           "and is the Fifine app closed?")
    idx = cfg.get("interface_index")
    if idx is not None:
        return ifs[idx]
    for i in ifs:  # prefer the vendor-defined interface
        if i.get("usage_page", 0) >= 0xFF00:
            return i
    return ifs[0]


class Deck:
    def __init__(self, cfg, debug=False):
        self.cfg, self.debug = cfg, debug
        info = pick_interface(cfg)
        self.dev = hid.device()
        self.dev.open_path(info["path"])
        self.dev.set_nonblocking(True)  # read() with a timeout still waits; without, it doesn't
        self.presses = []  # key presses caught while drawing, not handled yet
        print(f"Opened D6 interface (usage_page=0x{info.get('usage_page', 0):04x}, "
              f"interface={info.get('interface_number')})")

    def _write(self, payload: bytes):
        data = b"\x00" + payload.ljust(PKT, b"\x00")  # report id 0 + 512 bytes
        if self.debug:
            print("TX", data[1:24].hex(" "), "...")
        if self.dev.write(data) < 0:
            raise OSError("write to D6 failed")

    def cmd(self, name: bytes, extra: bytes = b""):
        self._write(b"CRT\x00\x00" + name + extra)

    def read(self, timeout_ms=100):
        data = bytes(self.dev.read(PKT, timeout_ms))
        if data and self.debug:
            print("RX", data[:24].hex(" "), "...")
        return data

    def catch_presses(self):
        """Pick up key presses waiting right now, without waiting. Called between key
        pictures, so a press is never missed while a lot is being drawn (like a GIF)."""
        for _ in range(20):
            data = bytes(self.dev.read(PKT))  # instant: the device is in non-blocking mode
            if not data:
                return
            code = self.parse_event(data)
            if code is not None:
                self.presses.append(code)

    def next_press(self, timeout_ms):
        """The next key press (raw code), waiting up to timeout_ms for one; None if none."""
        self.catch_presses()
        if self.presses:
            return self.presses.pop(0)
        return self.parse_event(self.read(timeout_ms))

    # Commands confirmed working on a real D6 (Mirabox-style protocol)
    def wake(self):
        self.cmd(b"DIS")

    def brightness(self, pct):
        self.cmd(b"LIG", bytes([0, 0, max(0, min(100, pct))]))

    def clear(self, slot=0xFF):
        self.cmd(b"CLE", bytes([0, 0, 0, slot]))

    def set_image(self, slot, jpeg: bytes, show=True):
        """Send one key picture. With show=False it only appears at the next show(), so
        several keys can be sent in a row and change together (about twice as fast)."""
        size = len(jpeg)
        self.cmd(b"BAT", bytes([0, 0, size >> 8, size & 0xFF, slot]))
        for i in range(0, size, PKT):
            self._write(jpeg[i:i + PKT])
        if show or not self.cfg.get("fast_draw", True):
            self.show()
        self.catch_presses()

    def show(self):
        """Put every picture sent so far on the screens."""
        self.cmd(b"STP")

    @staticmethod
    def parse_event(data: bytes):
        """Return the raw key code on a key PRESS, else None.
        Confirmed on a real D6: b"ACK\\0\\0OK\\0\\0" + key(1-15) + state(1=press, 0=release)."""
        if len(data) > 10 and data[:3] == b"ACK" and data[10] == 1:
            return data[9]
        return None

    def close(self):
        try:
            self.dev.close()
        except Exception:
            pass


# ----------------------------------------------------------------- live data (Spotify, volume)
class MediaWatcher(threading.Thread):
    """Polls Windows' media session for the Spotify app, in the background."""

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.snap = None
        self.error = None
        self.stop_flag = False

    def get(self):
        with self.lock:
            return self.snap

    def run(self):
        try:
            import asyncio
            from winrt.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as Mgr,
                GlobalSystemMediaTransportControlsSessionPlaybackStatus as PS)
            from winrt.windows.storage.streams import Buffer, DataReader, InputStreamOptions
            import winrt.windows.foundation  # noqa: F401  (needed to await Windows calls)
            import winrt.windows.foundation.collections  # noqa: F401  (needed to list sessions)
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            log("Spotify widgets disabled (packages missing?): " + self.error)
            return
        try:
            asyncio.run(self._main(Mgr, PS, Buffer, DataReader, InputStreamOptions))
        except Exception:
            log("Spotify watcher stopped:\n" + traceback.format_exc())

    @staticmethod
    async def _read_cover(props, Buffer, DataReader, InputStreamOptions):
        try:
            th = props.thumbnail
            if th is None:
                return None
            stream = await th.open_read_async()
            size = int(stream.size)
            if size <= 0 or size > 8_000_000:
                return None
            buf = await stream.read_async(Buffer(size), size, InputStreamOptions.READ_AHEAD)
            try:
                data = bytes(memoryview(buf))[:buf.length]
            except TypeError:  # older pywinrt without buffer protocol
                reader = DataReader.from_buffer(buf)
                arr = bytearray(reader.unconsumed_buffer_length)
                reader.read_bytes(arr)
                data = bytes(arr)
            return data or None
        except Exception as e:
            log(f"Could not read Spotify cover: {e}")
            return None

    async def _main(self, Mgr, PS, Buffer, DataReader, InputStreamOptions):
        import asyncio
        mgr = await Mgr.request_async()
        cover_for, cover, last_err = None, None, None
        while not self.stop_flag:
            try:
                sess = None
                for s in mgr.get_sessions():
                    if "spotify" in (s.source_app_user_model_id or "").lower():
                        sess = s
                        break
                snap = None
                if sess is not None:
                    props = await sess.try_get_media_properties_async()
                    title, artist = props.title or "", props.artist or ""
                    key = f"{title}\x00{artist}\x00{props.album_title or ''}"
                    tl = sess.get_timeline_properties()
                    playing = sess.get_playback_info().playback_status == PS.PLAYING
                    start = tl.start_time.total_seconds()
                    dur = max(0.0, tl.end_time.total_seconds() - start)
                    pos = tl.position.total_seconds() - start
                    if playing:  # position is only as of last_updated_time
                        try:
                            lu = tl.last_updated_time
                            if lu.tzinfo is None:
                                lu = lu.replace(tzinfo=timezone.utc)
                            el = (datetime.now(timezone.utc) - lu).total_seconds()
                            if -1 <= el <= (dur or 36000) + 5:
                                pos += max(0.0, el)
                        except Exception:
                            pass
                    if dur:
                        pos = min(pos, dur)
                    if key != cover_for and title:
                        cover = await self._read_cover(props, Buffer, DataReader, InputStreamOptions)
                        cover_for = key
                        log(f"Now playing: {title} - {artist} "
                            f"(length {look.fmt_time(dur)}, at {look.fmt_time(pos)}, "
                            f"cover {'yes' if cover else 'no'})")
                    snap = {"key": key, "title": title, "artist": artist, "playing": playing,
                            "pos": pos, "dur": dur, "mono": time.monotonic(), "cover": cover}
                with self.lock:
                    self.snap = snap
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                if msg != last_err:
                    log("Spotify poll error: " + msg)
                    last_err = msg
            await asyncio.sleep(0.7)


class VolumeReader:
    """Reads and sets the Windows master volume (needs the pycaw package)."""

    def __init__(self):
        self.ep = None
        self.acquired = 0.0
        self.last_try = 0.0
        self.disabled = False
        self.cache = (0.0, None)

    def _acquire(self):
        from pycaw.pycaw import AudioUtilities
        dev = AudioUtilities.GetSpeakers()
        try:
            ep = dev.EndpointVolume  # newer pycaw
        except Exception:
            ep = None
        if ep is None:  # older pycaw
            from ctypes import POINTER, cast
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import IAudioEndpointVolume
            ep = cast(dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None),
                      POINTER(IAudioEndpointVolume))
        return ep

    def _endpoint(self):
        if self.disabled:
            return None
        now = time.monotonic()
        if self.ep is not None and now - self.acquired > 60:
            self.ep = None  # re-fetch now and then in case the output device changed
        if self.ep is None:
            if now - self.last_try < 3:
                return None
            self.last_try = now
            try:
                self.ep = self._acquire()
                self.acquired = now
            except ImportError as e:
                self.disabled = True
                log(f"Volume reading disabled (install pycaw): {e}")
            except Exception as e:
                log(f"Could not open the speakers: {type(e).__name__}: {e}")
        return self.ep

    def read(self, max_age=0.3):
        """(percent, muted) or None. Cached briefly so the loop stays light."""
        t, v = self.cache
        if time.monotonic() - t < max_age:
            return v
        v = None
        ep = self._endpoint()
        if ep is not None:
            try:
                v = (int(round(ep.GetMasterVolumeLevelScalar() * 100)), bool(ep.GetMute()))
            except Exception as e:
                log(f"Volume read failed: {type(e).__name__}: {e}")
                self.ep = None
        self.cache = (time.monotonic(), v)
        return v

    def change(self, delta):
        """Move volume by delta percent. False if pycaw isn't usable."""
        ep = self._endpoint()
        if ep is None:
            return False
        cur = int(round(ep.GetMasterVolumeLevelScalar() * 100))
        new = max(0, min(100, cur + int(delta)))
        ep.SetMasterVolumeLevelScalar(new / 100, None)
        if delta > 0 and ep.GetMute():
            ep.SetMute(0, None)
        self.cache = (0.0, None)
        return True

    def toggle_mute(self):
        ep = self._endpoint()
        if ep is None:
            return False
        ep.SetMute(0 if ep.GetMute() else 1, None)
        self.cache = (0.0, None)
        return True


class Live:
    """Everything that changes by itself: Spotify, system volume."""

    def __init__(self, keys, start_threads=True):
        widgets = {k.get("widget") for k in keys}
        self.media = None
        self.vol = VolumeReader()
        self.sys = SysStats()
        if start_threads and any(w and (w.startswith("spotify") or w == "play_pause") for w in widgets):
            self.media = MediaWatcher()
            self.media.start()

    def close(self):
        if self.media is not None:
            self.media.stop_flag = True

    def spotify(self):
        snap = self.media.get() if self.media is not None else None
        if not snap:
            return None
        pos, dur = snap["pos"], snap["dur"]
        if snap["playing"]:
            pos += time.monotonic() - snap["mono"]
        if dur:
            pos = min(pos, dur)
        rem = max(0, math.ceil(dur - pos)) if dur else None
        return {"key": snap["key"], "title": snap["title"], "artist": snap["artist"],
                "playing": snap["playing"], "pos": pos, "dur": dur, "rem": rem,
                "cover": snap["cover"]}

    def volume(self):
        return self.vol.read() if self.vol is not None else None

    def cpu(self):
        return self.sys.read()[0]

    def ram(self):
        return self.sys.read()[1]


class SysStats:
    """How busy the PC is (processor and memory), read from Windows about once a second."""

    def __init__(self):
        self.prev = None
        self.cache = (0.0, None, None)

    @staticmethod
    def _cpu_times():
        import ctypes
        from ctypes import wintypes
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel),
                                              ctypes.byref(user))

        def n(ft):
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime
        return n(idle), n(kernel) + n(user)  # "kernel" time already includes idle time

    @staticmethod
    def _memory_percent():
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + \
                       [(n, ctypes.c_ulonglong) for n in ("a", "b", "c", "d", "e", "f", "g")]
        m = MemoryStatus()
        m.dwLength = ctypes.sizeof(m)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return int(m.dwMemoryLoad)

    def read(self):
        """(cpu percent, memory percent); either can be None when not known (yet)."""
        t, cpu, ram = self.cache
        if time.monotonic() - t < 1.0:
            return cpu, ram
        if os.name == "nt":
            try:
                idle, total = self._cpu_times()
                if self.prev and total > self.prev[1]:
                    busy = 1 - (idle - self.prev[0]) / (total - self.prev[1])
                    cpu = max(0, min(100, round(busy * 100)))
                self.prev = (idle, total)
                ram = self._memory_percent()
            except Exception:
                pass
        self.cache = (time.monotonic(), cpu, ram)
        return cpu, ram


def slot_for(pos, cfg):
    """Screen slot that physically sits at position pos (1 = top-left, 15 = bottom-right)."""
    if str(pos) in cfg.get("display_map", {}):
        return int(cfg["display_map"][str(pos)])
    if cfg.get("flip_rows", True):  # this D6 numbers its screens bottom row first
        row, col = (pos - 1) // COLS, (pos - 1) % COLS
        return (ROWS - 1 - row) * COLS + col + 1
    return pos


class Screen:
    """The 15 key screens. It remembers what is on each one, so a picture that is already
    showing is never sent again (sending is slow: about 30 ms per key)."""

    def __init__(self, deck, cfg):
        self.deck, self.cfg = deck, cfg
        self.shown = {}    # pos -> JPEG on that key now
        self.pending = {}  # queued by put(), sent by flush()

    @property
    def state(self):
        return self.cfg["state"]

    def put(self, pos, jpeg):
        """Queue a picture for a key (dropped if that key already shows it)."""
        if self.shown.get(pos) == jpeg:
            self.pending.pop(pos, None)
        else:
            self.pending[pos] = jpeg

    def flush(self):
        """Send everything queued; the D6 then shows it all at the same moment."""
        if not self.pending:
            return
        for pos, jpeg in self.pending.items():
            self.deck.set_image(slot_for(pos, self.cfg), jpeg, show=False)
            self.shown[pos] = jpeg
        self.pending.clear()
        self.deck.show()

    def show(self, pos, jpeg):
        self.put(pos, jpeg)
        self.flush()

    def fill(self, jpegs):
        for pos, jpeg in jpegs.items():
            self.put(pos, jpeg)
        self.flush()

    def wiped(self):
        """Call after deck.clear(): every key is black now."""
        black = look.black_jpeg(self.cfg)
        self.shown = {pos: black for pos in range(1, NKEYS + 1)}
        self.pending.clear()

    def log(self, msg):
        log(msg)

    def save(self):
        save_state(self.cfg)


def fade(deck, start, end, dur=0.3, steps=6):
    """Smoothly move the screen brightness from start to end."""
    for i in range(1, steps + 1):
        deck.brightness(round(start + (end - start) * i / steps))
        time.sleep(dur / steps)


def drain(deck, seconds=0.15):
    """Throw away key events that piled up while we were busy drawing."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        deck.read(10)
    deck.presses.clear()


# ----------------------------------------------------------------- actions
_kb = KbController()


def _key(name):
    name = name.strip().lower()
    name = {"control": "ctrl", "win": "cmd", "windows": "cmd"}.get(name, name)
    if hasattr(Key, name):
        return getattr(Key, name)
    if len(name) == 1:
        return name
    raise ValueError(f"Unknown key name: {name}")


def send_hotkey(combo):
    keys = [_key(p) for p in combo.split("+")]
    for k in keys:
        _kb.press(k)
    for k in reversed(keys):
        _kb.release(k)


_found_apps = {}


def find_app(target):
    """Find a program even if it is not where the button says (another drive, another PC).

    Keeps the path if it exists. Otherwise looks the .exe name up in Windows' "App Paths"
    list, then in the usual install folders (a few levels deep), and remembers the answer.
    Links (spotify:, https://) and plain names like notepad.exe are left alone.
    """
    target = os.path.expandvars(target)
    p = Path(target)
    if not target.lower().endswith(".exe") or p.exists() or len(p.parts) < 2:
        return target
    name = p.name.lower()
    if name in _found_apps:
        return _found_apps[name]
    found = None
    try:
        import winreg
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{p.name}") as k:
                    hit = os.path.expandvars(winreg.QueryValue(k, None).strip('"'))
                    if Path(hit).exists():
                        found = hit
                        break
            except OSError:
                pass
    except ImportError:
        pass
    if not found:
        roots = [os.environ.get(v) for v in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
                                             "APPDATA", "PROGRAMDATA")]
        for root in filter(None, roots):
            for depth in ("*", "*/*", "*/*/*"):
                hits = list(Path(root).glob(f"{depth}/{p.name}"))
                if hits:
                    found = str(hits[0])
                    break
            if found:
                break
    if found:
        log(f"Found {p.name} at {found}")
    _found_apps[name] = found or target
    return _found_apps[name]


def launch(target, fallbacks=()):
    """Open a program, file or link (spotify:, discord://). Tries fallbacks if it fails."""
    target = find_app(target)
    try:
        if hasattr(os, "startfile"):
            os.startfile(target)  # also opens URI links like spotify: and discord://
        else:
            subprocess.Popen([target])
        return
    except OSError as first_error:
        for fb in fallbacks:
            argv = [os.path.expandvars(x) for x in ([fb] if isinstance(fb, str) else fb)]
            if Path(argv[0]).exists():
                subprocess.Popen(argv, cwd=str(Path(argv[0]).parent))
                return
        raise RuntimeError(f"could not open {target!r}: {first_error}")


def launch_admin(target, args=""):
    """Open a program as administrator. Windows shows its usual UAC "Yes/No" prompt."""
    import ctypes
    target = find_app(target)
    if not Path(target).exists():
        raise RuntimeError(f"not found: {target}")
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", target, args or None,
                                            str(Path(target).parent), 1)
    if r <= 32:  # 5 = you clicked No on the UAC prompt
        raise RuntimeError("admin launch cancelled" if r == 5 else f"ShellExecute error {r}")


# ------------------------------------------------------------------ Obsidian
# What an "obsidian" key can do: {"type": "obsidian", "do": <one of these>}.
# "vault" (optional) is the vault folder; empty = the vault Obsidian last had open.
OBSIDIAN_DO = {
    "open": "Open the vault",
    "daily": "Open today's daily note",
    "new": "Make a new note",
    "search": "Search the vault",
    "inbox": "Open the Inbox note",
    "clip": "Add the copied text to the Inbox",
}


def obsidian_vaults():
    """Vaults Obsidian knows about, as [(id, folder, was_open)], newest first."""
    try:
        data = json.loads(Path(os.path.expandvars(r"%APPDATA%\obsidian\obsidian.json")).read_text("utf-8"))
        vaults = data.get("vaults", {})
    except (OSError, ValueError, AttributeError):
        return []
    rows = [(vid, v.get("path", ""), bool(v.get("open")), v.get("ts", 0))
            for vid, v in vaults.items() if isinstance(v, dict) and v.get("path")]
    rows.sort(key=lambda r: (r[2], r[3]), reverse=True)
    return [(vid, path, was_open) for vid, path, was_open, _ in rows]


def obsidian_vault(folder=""):
    """(vault id or None, vault folder) to use."""
    vaults = obsidian_vaults()
    if folder:
        want = os.path.normcase(os.path.normpath(os.path.expandvars(folder)))
        for vid, path, _ in vaults:
            if os.path.normcase(os.path.normpath(path)) == want:
                return vid, path
        return None, os.path.expandvars(folder)
    if not vaults:
        raise RuntimeError("no Obsidian vault found - open Obsidian once first")
    return vaults[0][0], vaults[0][1]


def obsidian_uri(action, **params):
    from urllib.parse import quote
    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items() if v is not None)
    return f"obsidian://{action}" + (f"?{query}" if query else "")


def read_clipboard_text():
    """The text on the Windows clipboard ("" if there is none)."""
    import ctypes
    from ctypes import wintypes
    u, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    u.GetClipboardData.restype = wintypes.HANDLE
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = k32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    for _ in range(10):  # another app may be holding the clipboard for a moment
        if u.OpenClipboard(None):
            break
        time.sleep(0.03)
    else:
        raise RuntimeError("the clipboard is busy")
    try:
        h = u.GetClipboardData(13)  # CF_UNICODETEXT
        if not h:
            return ""
        p = k32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p) if p else ""
        finally:
            k32.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


def obsidian_note(folder, rel, first_text=""):
    """Make sure the note `rel` (inside the vault) exists. Returns its full path."""
    path = Path(folder) / rel
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(first_text, encoding="utf-8")
    return path


def obsidian(a):
    what = a.get("do", "open")
    vid, folder = obsidian_vault(a.get("vault", ""))
    vault = vid or Path(folder).name
    now = datetime.now()
    if what == "open":
        launch(obsidian_uri("open", vault=vault))
    elif what == "search":
        launch(obsidian_uri("search", vault=vault))
    elif what in ("daily", "new", "inbox"):
        if what == "daily":
            note = obsidian_note(folder, Path("Daily") / f"{now:%Y-%m-%d}.md",
                                 f"# {now:%A %d %B %Y}\n\n")
        elif what == "new":
            note = obsidian_note(folder, Path("Inbox") / f"Note {now:%Y-%m-%d %H%M%S}.md")
        else:
            note = obsidian_note(folder, "Inbox.md", "# Inbox\n\n")
        launch(obsidian_uri("open", path=str(note)))
    elif what == "clip":
        text = read_clipboard_text().strip()
        if not text:
            raise RuntimeError("nothing copied - copy some text first")
        note = obsidian_note(folder, "Inbox.md", "# Inbox\n\n")
        lines = text.splitlines()
        entry = f"- {now:%Y-%m-%d %H:%M} {lines[0]}\n" + "".join(f"  {ln}\n" for ln in lines[1:])
        old = note.read_text(encoding="utf-8")
        if old and not old.endswith("\n"):
            entry = "\n" + entry
        with open(note, "a", encoding="utf-8") as f:
            f.write(entry)
        log(f"Clipped {len(text)} characters to {note}")
    else:
        raise RuntimeError(f"unknown Obsidian action {what!r}")


def do_action(a):
    t, v = a.get("type"), a.get("target", "")
    if t == "obsidian":
        obsidian(a)
    elif t == "launch":
        launch(v, a.get("fallbacks", []))
    elif t == "launch_admin":
        launch_admin(v, a.get("args", ""))
    elif t == "url":
        webbrowser.open(v)
    elif t == "command":
        subprocess.Popen(v, shell=True)
    elif t == "hotkey":
        for _ in range(max(1, int(a.get("repeat", 1)))):
            send_hotkey(v)
            time.sleep(0.02)
    elif t == "text":
        _kb.type(v)
    else:
        print(f"Unknown action type: {t}")


def find_spotify_window():
    """Handle of the Spotify desktop window (even if minimized or hidden to the tray)."""
    import ctypes
    from ctypes import wintypes
    u, k32 = ctypes.windll.user32, ctypes.windll.kernel32

    def exe_of(pid):
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf, n = ctypes.create_unicode_buffer(520), wintypes.DWORD(520)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                return os.path.basename(buf.value).lower()
            return ""
        finally:
            k32.CloseHandle(h)

    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if u.GetWindowTextLengthW(hwnd) > 0 and not u.GetWindow(hwnd, 4):  # titled, not owned
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if exe_of(pid.value) == "spotify.exe":
                cls = ctypes.create_unicode_buffer(64)
                u.GetClassNameW(hwnd, cls, 64)
                score = (cls.value.startswith("Chrome_WidgetWin"), bool(u.IsWindowVisible(hwnd)))
                found.append((score, hwnd))
        return True

    u.EnumWindows(each, 0)
    return max(found)[1] if found else None


def focus_window(hwnd):
    """Bring a window to the front (Windows only lets us do this with a small trick)."""
    import ctypes
    u, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    fg = u.GetForegroundWindow()
    fg_thread = u.GetWindowThreadProcessId(fg, None) if fg else 0
    me = k32.GetCurrentThreadId()
    attached = bool(fg_thread and fg_thread != me and u.AttachThreadInput(fg_thread, me, True))
    try:
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
    finally:
        if attached:
            u.AttachThreadInput(fg_thread, me, False)
    time.sleep(0.15)
    if u.GetForegroundWindow() != hwnd:  # fallback: an Alt tap unlocks focus changes
        _kb.press(Key.alt)
        _kb.release(Key.alt)
        u.SetForegroundWindow(hwnd)
        time.sleep(0.15)
    return u.GetForegroundWindow() == hwnd


def spotify_like():
    """Like/unlike the current song: Spotify's own Alt+Shift+B shortcut, sent to its window,
    then the window you were using is put back in front."""
    if os.name != "nt":
        raise RuntimeError("only works on Windows")
    import ctypes
    u = ctypes.windll.user32
    hwnd = find_spotify_window()
    if not hwnd:
        raise RuntimeError("Spotify isn't open")
    prev = u.GetForegroundWindow()
    was_hidden, was_min = not u.IsWindowVisible(hwnd), bool(u.IsIconic(hwnd))
    if was_hidden:
        u.ShowWindow(hwnd, 5)  # SW_SHOW
    if was_min:
        u.ShowWindow(hwnd, 9)  # SW_RESTORE
    try:
        if not focus_window(hwnd):
            raise RuntimeError("Windows wouldn't let Spotify come to the front")
        send_hotkey("alt+shift+b")
        time.sleep(0.2)
    finally:
        if was_min:
            u.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        elif was_hidden:
            u.ShowWindow(hwnd, 0)  # SW_HIDE
        if prev and prev != hwnd:
            focus_window(prev)


def run_action(a, live):
    t = a.get("type")
    if t == "volume":
        if not live.vol.change(a.get("delta", 0)):  # no pycaw: fall back to media keys
            n = max(1, abs(int(a.get("delta", 0))) // 2)
            do_action({"type": "hotkey", "repeat": n,
                       "target": "media_volume_up" if a.get("delta", 0) > 0 else "media_volume_down"})
    elif t == "spotify_like":
        spotify_like()
    elif t == "previous_song":
        # Spotify's "previous" only restarts the song once it's past ~3 s, so press twice then.
        v = live.spotify()
        presses = 2 if v and v["pos"] > 3 else 1
        for i in range(presses):
            send_hotkey("media_previous")
            if i < presses - 1:
                time.sleep(0.15)
    elif t == "mute":
        if not live.vol.toggle_mute():
            do_action({"type": "hotkey", "target": "media_volume_mute"})
    else:
        do_action(a)


def change_brightness(deck, cfg, delta):
    old = int(cfg["brightness"])
    new = max(MIN_BRIGHTNESS, min(100, old + int(delta)))
    cfg["brightness"] = new
    fade(deck, old, new, 0.1, 3)
    save_state(cfg)


# ----------------------------------------------------------------- run loop
QUIT_CONFIRM_SECONDS = 3
CROSSFADE_WIDGETS = {"spotify_cover", "spotify_title", "clock"}  # fade smoothly when they change


def blank(deck):
    """Turn the key screens dark (used for Screen Off and when the app stops)."""
    deck.clear()
    deck.brightness(0)


class Session:
    """One connection to the D6: shows the pages, reacts to key presses, and hands the
    screens over to a screen saver or a game when asked. run() returns "stop", "quit" or
    "restart", and raises OSError when the device goes away."""

    def __init__(self, deck, cfg, live):
        self.deck, self.cfg, self.live = deck, cfg, live
        self.screen = Screen(deck, cfg)
        self.page = 0
        self.keys = {}     # pos -> key (from buttons.json) on the page showing now
        self.normal = {}   # pos -> that key's normal picture (animations start from it)
        self.sigs = {}     # pos -> signature of live keys, to notice when they change
        # pos -> [(due time, JPEG frame or None)], oldest first. None = the normal picture.
        self.anims = {}
        self.sleeping = False  # True after "Screen Off" (dark, or showing the screen saver)
        self.saver = None      # the screen saver while it is showing
        self.game = None       # the game while one is being played
        self.armed_pos, self.armed_until = None, 0.0  # Quit needs a second press in time
        self.last_press = 0.0

    # ------------------------------------------------------------- drawing the pages
    def picture(self, pos):
        jpeg = look.render_key(self.keys[pos], self.cfg, self.live)
        self.normal[pos] = jpeg
        return jpeg

    def draw_key(self, pos):
        self.screen.show(pos, self.picture(pos))
        if pos in self.sigs:
            self.sigs[pos] = look.signature(self.keys[pos], self.cfg, self.live)

    def show_page(self, direction=1, wipe=True):
        """Draw the current page. With wipe, it sweeps in one column at a time (left to
        right, or right to left for direction -1); without, all keys change together."""
        pages = self.cfg["pages"]
        self.page = max(0, min(self.page, len(pages) - 1))
        self.cfg["page"] = self.page
        self.anims.clear()
        self.armed_pos = None
        self.keys = {int(n): k for n, k in pages[self.page].items()
                     if str(n).isdigit() and 1 <= int(n) <= NKEYS}
        self.normal = {}
        black = look.black_jpeg(self.cfg)
        for c in (range(COLS) if direction >= 0 else range(COLS - 1, -1, -1)):
            for r in range(ROWS):
                pos = r * COLS + c + 1
                self.screen.put(pos, self.picture(pos) if pos in self.keys else black)
            if wipe:
                self.screen.flush()
        self.screen.flush()
        self.sigs = {pos: look.signature(k, self.cfg, self.live)
                     for pos, k in self.keys.items() if k.get("widget")}

    def light_up(self, first=False):
        """Draw the page while the screens are dark, then fade in: one clean refresh."""
        self.deck.brightness(0)
        if first:
            self.deck.clear()
            self.screen.wiped()
            time.sleep(0.2)
        self.show_page(wipe=False)
        fade(self.deck, 0, self.cfg["brightness"], 0.25, 5)
        drain(self.deck, 0.1)

    # ------------------------------------------------------------- small key animations
    def flash(self, pos, jpeg, seconds):
        """Show a temporary picture on a key, then put its normal picture back."""
        self.screen.show(pos, jpeg)
        self.anims[pos] = [(time.monotonic() + seconds, None)]

    def relabeled(self, k, label, color=None):
        """The key's picture with different words on it ("Failed", "Press again"...)."""
        k = {n: v for n, v in k.items() if n != "widget"}
        return look.render_key({**k, "label": label, "color": color or k.get("color", "#333333")},
                               self.cfg, self.live)

    def play_press(self, pos):
        sunk, glow = look.press_frames(self.normal.get(pos) or self.picture(pos))
        self.screen.show(pos, sunk)
        t0 = time.monotonic()
        self.anims[pos] = [(t0 + 0.07, glow), (t0 + 0.15, None)]

    def tick_page(self, now):
        """Play animation frames that are due, and redraw live keys that changed."""
        for pos, frames in list(self.anims.items()):
            due = [f for f in frames if f[0] <= now]
            if not due:
                continue
            rest = frames[len(due):]
            if rest:
                self.anims[pos] = rest
            else:
                del self.anims[pos]
            if pos not in self.keys:
                continue
            frame = due[-1][1]  # if we fell behind, skip straight to the newest frame
            if frame is None:
                self.draw_key(pos)
            else:
                self.screen.show(pos, frame)
        for pos in list(self.sigs):
            if pos in self.anims:
                continue  # finishes its animation first, then gets redrawn
            k = self.keys[pos]
            new = look.signature(k, self.cfg, self.live)
            if new == self.sigs[pos]:
                continue
            self.sigs[pos] = new
            old = self.normal.get(pos)
            if k.get("widget") in CROSSFADE_WIDGETS and old:
                mids = look.blend_frames(old, self.picture(pos))
                if mids:
                    self.screen.show(pos, mids[0])
                    self.anims[pos] = [(now + 0.05 * i, f) for i, f in enumerate(mids[1:], 1)]
                    self.anims[pos].append((now + 0.05 * len(mids), None))
                    continue
            self.draw_key(pos)

    # ------------------------------------------------------------- screen off / screen saver
    def go_to_sleep(self):
        self.sleeping = True
        self.anims.clear()
        time.sleep(0.08)  # let the press animation show for a moment
        fade(self.deck, self.cfg["brightness"], 0, 0.25, 5)
        saver = None
        if self.cfg["screensaver"].get("enabled", True):
            try:
                saver = fun.make_saver(self.screen)
                if saver is not None:
                    saver.start()
            except OSError:
                raise
            except Exception:
                log("Screen saver problem:\n" + traceback.format_exc())
                saver = None
        if saver is not None:
            self.saver = saver
            fade(self.deck, 0, saver.brightness(), 0.4, 6)
            log("Screen saver on")
        else:
            blank(self.deck)
            self.screen.wiped()
            log("Screens off")
        drain(self.deck, 0.3)  # so the Screen Off press itself can't wake it straight back up

    def wake_up(self):
        if self.saver is not None:
            fade(self.deck, self.saver.brightness(), 0, 0.2, 4)
            self.saver = None
        self.sleeping = False
        self.light_up()
        log("Screens on")

    # ------------------------------------------------------------- games
    def start_game(self, name):
        try:
            game = fun.make_game(name, self.screen)
        except Exception as e:
            log(f"Could not start game {name!r}: {e}")
            return
        self.anims.clear()
        time.sleep(0.05)
        self.game = game
        self.in_game(game.start)

    def in_game(self, step, *args):
        """Run one step of the game; a bug in a game must never take the deck down."""
        try:
            step(*args)
        except OSError:
            raise
        except Exception:
            log("Game problem:\n" + traceback.format_exc())
            self.game.done = True
        if self.game.done:
            self.game = None
            drain(self.deck, 0.1)
            self.show_page()

    # ------------------------------------------------------------- settings changed
    def reload(self):
        """The settings app saved something: pick it up without restarting."""
        RELOAD_PATH.unlink(missing_ok=True)
        new = load_config()
        new["stopwatch"] = self.cfg["stopwatch"]
        self.cfg.clear()
        self.cfg.update(new)
        log("Settings changed - reloaded")
        if self.sleeping or self.game is not None:
            return  # they show up when the buttons come back
        self.deck.brightness(self.cfg["brightness"])
        self.show_page()

    # ------------------------------------------------------------- key presses
    def press(self, pos):
        """A key was pressed. Returns "quit"/"restart" when the app should end, else None."""
        if self.game is not None:
            self.in_game(self.game.press, pos)
            return None
        if self.sleeping:  # any key wakes the screens, on the same page as before
            self.wake_up()
            return None
        k = self.keys.get(pos)
        if not k:
            return None
        a = k.get("action") or {}
        t = a.get("type")
        if t != "quit":
            self.play_press(pos)
        if not a:
            return None

        if t == "quit":
            if self.armed_pos == pos and time.monotonic() <= self.armed_until:
                log("Quit pressed on the deck")
                return "quit"
            self.armed_pos, self.armed_until = pos, time.monotonic() + QUIT_CONFIRM_SECONDS
            self.flash(pos, self.relabeled(k, "Press again", "#d1242f"), QUIT_CONFIRM_SECONDS)
            return None
        if self.armed_pos is not None:  # any other key cancels a pending Quit
            if self.armed_pos != pos:
                self.anims[self.armed_pos] = [(0.0, None)]  # normal picture back right away
            self.armed_pos = None

        if t == "page":
            self.turn_page(pos, a.get("to", "next"))
        elif t == "sleep":
            self.go_to_sleep()
        elif t == "restart":
            self.screen.show(pos, self.relabeled(k, "Restarting"))
            log("Restart pressed on the deck")
            return "restart"
        elif t == "brightness":
            change_brightness(self.deck, self.cfg, a.get("delta", 0))  # talks to the D6
        elif t == "theme":
            names = list(look.THEMES)
            self.cfg["theme"] = names[(names.index(self.cfg["theme"]) + 1) % len(names)]
            save_state(self.cfg)
            self.show_page()
        elif t == "saver":
            names = list(fun.SAVER_STYLES)
            cur = self.cfg["screensaver"].get("style", "auto")
            i = names.index(cur) if cur in names else -1
            self.cfg["screensaver"]["style"] = names[(i + 1) % len(names)]
            save_state(self.cfg)
        elif t == "stopwatch":
            self.tap_stopwatch()
        elif t == "game":
            self.start_game(a.get("name", ""))
        elif t == "toy":
            self.play_toy(pos, a.get("name", ""))
        else:
            self.do(pos, k, a)
        return None

    def turn_page(self, pos, to):
        pages, old = self.cfg["pages"], self.page
        if to == "next":
            self.page = (self.page + 1) % len(pages)
        elif to == "prev":
            self.page = (self.page - 1) % len(pages)
        else:
            try:
                self.page = max(0, min(len(pages) - 1, int(to) - 1))
            except (TypeError, ValueError):
                log(f'Key {pos}: page "to" must be "next", "prev" or a page number')
                return
        time.sleep(0.05)  # let the press animation show for a moment
        self.show_page(1 if to == "next" or (to != "prev" and self.page >= old) else -1)

    def tap_stopwatch(self):
        """First tap starts, second pauses, third clears."""
        sw = self.cfg["stopwatch"]
        if sw["running"]:
            sw["elapsed"] += time.monotonic() - sw["since"]
            sw.update(running=False, since=None)
        elif sw["elapsed"] > 0:
            sw["elapsed"] = 0.0
        else:
            sw.update(running=True, since=time.monotonic())

    def play_toy(self, pos, name):
        try:
            frames, hold = fun.toy(name, self.screen)
        except Exception as e:
            log(f"Toy {name!r} problem: {e}")
            return
        t0 = time.monotonic() + 0.1
        self.anims[pos] = [(t0 + at, jpeg) for at, jpeg in frames]
        self.anims[pos].append((t0 + (frames[-1][0] if frames else 0) + hold, None))

    def do(self, pos, k, a):
        """Everything else a key can do (open apps, shortcuts, volume, Spotify...)."""
        t = a.get("type")
        try:  # none of this talks to the D6, so a failure must not drop the connection
            run_action(a, self.live)
            ok = True
        except Exception as e:
            ok = False
            log(f"Key {pos} ({k.get('label') or k.get('widget')}) failed: {e}")
        if not ok:  # e.g. you said No to the admin prompt
            self.flash(pos, self.relabeled(k, "Failed", "#555555"), 1.5)
        elif t == "spotify_like" or (t == "obsidian" and a.get("do") == "clip"):
            self.flash(pos, self.relabeled(k, "Done!"), 1.5)
        elif t == "previous_song":  # a "previous" icon over the cover
            self.flash(pos, look.icon_over(self.normal.get(pos) or self.picture(pos), "prev",
                                           self.cfg), 0.6)

    # ------------------------------------------------------------- the loop
    def run(self):
        self.deck.wake()
        self.light_up(first=True)
        while True:
            if STOP_PATH.exists():
                return "stop"
            if RELOAD_PATH.exists():
                self.reload()
            if self.game is not None:
                wait = self.game.wait_ms
            elif self.saver is not None:
                wait = self.saver.wait_ms
            else:
                wait = 15 if self.anims else 100
            code = self.deck.next_press(max(1, int(wait)))
            if code is not None and time.time() - self.last_press >= 0.08:
                self.last_press = time.time()
                outcome = self.press(int(self.cfg["key_map"].get(str(code), code)))
                if outcome:
                    return outcome
            now = time.monotonic()
            if self.game is not None:
                self.in_game(self.game.tick, now)
            elif self.saver is not None:
                self.saver.tick(now)
            elif not self.sleeping:
                self.tick_page(now)


_mutex_handle = None


def single_instance(wait=0.0):
    """True if we are the only copy running (Windows named mutex).
    wait: seconds to keep trying (used when restarting)."""
    global _mutex_handle
    if os.name != "nt":
        return True
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # so the error code can't get lost
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    end = time.monotonic() + wait
    while True:
        h = k32.CreateMutexW(None, False, MUTEX_NAME)
        if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
            _mutex_handle = h
            return True
        k32.CloseHandle(h)
        if time.monotonic() >= end:
            return False
        time.sleep(0.5)


def release_instance():
    global _mutex_handle
    if os.name == "nt" and _mutex_handle:
        import ctypes
        k32 = ctypes.WinDLL("kernel32")
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.CloseHandle(_mutex_handle)
        _mutex_handle = None


def is_running():
    if os.name != "nt":
        return False
    import ctypes
    h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, MUTEX_NAME)  # SYNCHRONIZE
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    return False


def spawn_background(*extra):
    flags = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
    # The .exe unpacks itself to a temp folder; the background copy must get its own one,
    # because this copy's folder is deleted when it closes.
    env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    subprocess.Popen([*self_cmd(), "run", *extra], cwd=str(BASE), env=env,
                     creationflags=flags if os.name == "nt" else 0,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True)


def cmd_run(cfg, args):
    if not single_instance(wait=15 if args.wait else 0):
        print("d6deck is already running (probably in the background).\n"
              "Stop it first with:  py -3.14 d6deck.py stop")
        return
    STOP_PATH.unlink(missing_ok=True)
    RELOAD_PATH.unlink(missing_ok=True)
    log("d6deck started")
    live = Live(all_keys(cfg))
    announced_missing = False
    outcome = None
    try:
        while outcome is None:
            deck = None
            try:
                deck = Deck(cfg, args.debug)
                announced_missing = False
                log("Connected to D6")
                outcome = Session(deck, cfg, live).run()
                if outcome in ("stop", "quit"):
                    blank(deck)  # leave the screens dark so it's clear the app is off
            except DeckNotFound:
                if not announced_missing:
                    log("D6 not found, waiting for it...")
                    announced_missing = True
            except (OSError, ValueError) as e:
                log(f"Lost connection to D6 ({e}); will retry")
            except Exception:
                log("Unexpected error:\n" + traceback.format_exc())
            finally:
                if deck:
                    deck.close()
            if outcome is None:
                for _ in range(30):  # wait 3 s, but notice a stop request quickly
                    if STOP_PATH.exists():
                        outcome = "stop"
                        break
                    time.sleep(0.1)
    except KeyboardInterrupt:
        outcome = "stop"
    live.close()
    STOP_PATH.unlink(missing_ok=True)
    release_instance()
    if outcome == "restart":
        log("Restarting...")
        spawn_background("--wait")
    else:
        log("d6deck stopped")


# ----------------------------------------------------------------- background / startup
def pyw_path():
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return w if w.exists() else exe


def self_cmd():
    """How to start this app again without a console: D6Deck.exe, or pythonw d6deck.py."""
    return [str(SCRIPT)] if FROZEN else [str(pyw_path()), str(SCRIPT)]


def startup_vbs_path():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        sys.exit("This command only works on Windows.")
    return (Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            / "Startup" / "d6deck.vbs")


def cmd_install_startup(cfg, args):
    run = " ".join(f'""{c}""' for c in self_cmd())
    vbs = ('Set sh = CreateObject("WScript.Shell")\r\n'
           f'sh.CurrentDirectory = "{BASE}"\r\n'
           f'sh.Run "{run} run", 0, False\r\n')
    path = startup_vbs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(vbs, encoding="mbcs" if os.name == "nt" else "utf-8")
    print(f"Installed. d6deck will start automatically when you log in.\n  ({path})")
    print("Don't move or delete this folder, or run install-startup again after moving it.")


def cmd_remove_startup(cfg, args):
    path = startup_vbs_path()
    if path.exists():
        path.unlink()
        print("Removed from startup.")
    else:
        print("It wasn't set to start automatically.")


def cmd_start(cfg, args):
    if is_running():
        print("Already running in the background.")
        return
    spawn_background("--wait")
    print("Started in the background. You can close this window.\n"
          "Stop it any time with:  py -3.14 d6deck.py stop")


def cmd_stop(cfg, args):
    if not is_running():
        print("It isn't running.")
        return
    STOP_PATH.write_text("stop")
    for _ in range(100):
        if not is_running():
            print("Stopped.")
            return
        time.sleep(0.1)
    print("Stop signal sent. It should quit within a few seconds.")


def cmd_restart(cfg, args):
    if is_running():
        STOP_PATH.write_text("stop")
        for _ in range(100):
            if not is_running():
                break
            time.sleep(0.1)
    spawn_background("--wait")
    print("Restarted in the background.")


def desktop_path():
    """The real Desktop folder, even when OneDrive has moved it."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0:
            return Path(buf.value)
    return Path.home() / "Desktop"


ICON_PATH = BASE / "d6deck.ico"


def make_app_icon():
    """Draw the D6Deck icon (a little 5x3 deck of glowing keys) as d6deck.ico."""
    from PIL import Image, ImageDraw
    s = 256
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8, 40, s - 8, s - 40], radius=36, fill=(18, 20, 28), outline=(70, 90, 140),
                        width=6)
    colors = [(31, 111, 235), (35, 134, 54), (163, 33, 74), (110, 64, 201), (154, 103, 0)]
    for r in range(ROWS):
        for c in range(COLS):
            x, y = 28 + c * 42, 62 + r * 46
            d.rounded_rectangle([x, y, x + 34, y + 36], radius=8, fill=colors[(c + r) % len(colors)])
    img.save(ICON_PATH, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def make_shortcut(path, args, description):
    """A real Windows shortcut (.lnk) that runs this app without a console window."""
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:D6_LNK); "
          "$s.TargetPath = $env:D6_EXE; $s.Arguments = $env:D6_ARGS; "
          "$s.WorkingDirectory = $env:D6_DIR; $s.IconLocation = $env:D6_ICO; "
          "$s.Description = $env:D6_DESC; $s.Save()")
    exe, *pre = self_cmd()
    env = {**os.environ, "D6_LNK": str(path), "D6_EXE": exe,
           "D6_ARGS": " ".join([f'"{a}"' for a in pre] + [args]), "D6_DIR": str(BASE),
           "D6_ICO": f"{SCRIPT if FROZEN else ICON_PATH},0",
           "D6_DESC": description}
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env,
                   check=True, capture_output=True, creationflags=0x08000000)


def cmd_shortcuts(cfg, args):
    desk = desktop_path()
    make_app_icon()
    made = []
    for name, cmd, what in (("D6Deck", "ui", "Set up your D6 deck"),
                            ("D6Deck Start", "start", "Start the deck app"),
                            ("D6Deck Stop", "stop", "Stop the deck app"),
                            ("D6Deck Restart", "restart", "Restart the deck app")):
        (desk / f"{name}.vbs").unlink(missing_ok=True)  # old-style shortcut
        make_shortcut(desk / f"{name}.lnk", cmd, what)
        made.append(name)
    (desk / "D6Deck Settings.vbs").unlink(missing_ok=True)
    print(f"Made on your Desktop ({desk}):\n  " + "\n  ".join(made))
    print('Double-click "D6Deck" to set up your buttons. The others start, stop or restart it.')


# ----------------------------------------------------------------- diagnostics / setup
def cmd_probe(cfg, args):
    ifs = list_interfaces()
    if not ifs:
        sys.exit("D6 not found.")
    print(f"Found {len(ifs)} USB interface(s) for {VID:04x}:{PID:04x}:")
    for n, i in enumerate(ifs):
        print(f"  [{n}] interface={i.get('interface_number')} "
              f"usage_page=0x{i.get('usage_page', 0):04x} usage=0x{i.get('usage', 0):04x} "
              f"product={i.get('product_string')!r}")
    devs = []
    if args.active:
        deck = Deck(cfg, args.debug)
        print("ACTIVE mode: sending wake-up, brightness and test images...")
        deck.wake()
        deck.brightness(cfg["brightness"])
        deck.clear()
        time.sleep(0.2)
        for slot in range(1, NKEYS + 1):
            deck.set_image(slot, look.tile(cfg, color="#1f6feb", big=str(slot)))
            time.sleep(0.05)
        deck.dev.set_nonblocking(True)
        devs.append((0, deck.dev))
    else:
        for n, i in enumerate(ifs):
            try:
                d = hid.device()
                d.open_path(i["path"])
                d.set_nonblocking(True)
                devs.append((n, d))
            except OSError as e:
                print(f"      could not open interface {n}: {e}")
    print("\nPress keys on the D6 for the next 25 seconds...")
    end, got = time.time() + 25, 0
    while time.time() < end:
        for n, d in devs:
            data = bytes(d.read(PKT))
            if data:
                got += 1
                print(f"  iface[{n}] RX {data[:24].hex(' ')} ...")
        time.sleep(0.01)
    print(f"Done. Received {got} message(s) from the D6.")


def cmd_test_screen(cfg, args):
    deck = Deck(cfg, args.debug)
    deck.wake()
    deck.brightness(cfg["brightness"])
    deck.clear()
    time.sleep(0.2)
    for pos in range(1, NKEYS + 1):
        deck.set_image(slot_for(pos, cfg), look.tile(cfg, color="#1f6feb", big=str(pos)))
        time.sleep(0.05)
    print("Sent position numbers: 1-5 top row, 6-10 middle, 11-15 bottom.")
    deck.close()


def wait_press(deck, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        code = deck.parse_event(deck.read(100))
        if code is not None:
            return code
    return None


def cmd_learn(cfg, args):
    if is_running():
        sys.exit("d6deck is running in the background and has the D6 open.\n"
                 "Run  py -3.14 d6deck.py stop  first, then try again.")
    deck = Deck(cfg, args.debug)
    km = {}
    for pos in range(1, NKEYS + 1):
        r, c = (pos - 1) // COLS + 1, (pos - 1) % COLS + 1
        print(f"Press the key at row {r}, column {c} ...")
        code = wait_press(deck)
        if code is None:
            sys.exit("No key event received. Run `probe --active --debug` and send me the output.")
        km[str(code)] = pos
        time.sleep(0.4)
        while deck.read(50):  # drain release / repeats
            pass
    update_config_file("key_map", km)
    print("Saved key layout to config.json")
    deck.close()


def cmd_check(cfg, args):
    """Show what the live keys would display, without touching the D6."""
    live = Live(all_keys(cfg))
    print("Reading Spotify and volume for 10 seconds (play something in Spotify)...")
    try:
        for _ in range(10):
            time.sleep(1)
            v = live.spotify()
            vol = live.volume()
            song = (f"{v['title']} - {v['artist']} | left -{look.fmt_time(v['rem'] or 0)} "
                    f"of {look.fmt_time(v['dur'])} | {'playing' if v['playing'] else 'paused'} | "
                    f"cover {'yes' if v['cover'] else 'no'}") if v else "nothing from Spotify"
            print(f"  volume: {vol if vol is None else str(vol[0]) + '%' + (' (muted)' if vol[1] else '')}"
                  f"   spotify: {song}")
    except KeyboardInterrupt:
        pass
    if live.media is not None and live.media.error:
        print("Spotify reading is OFF because a package is missing:\n  " + live.media.error)
    live.close()


# ----------------------------------------------------------------- settings app
SAVER_FILE_TYPES = (".gif", ".webp", ".png", ".jpg", ".jpeg")


def own_saver_file():
    """Name of the picture/GIF in this folder used as the screen saver ('' if none)."""
    for ext in SAVER_FILE_TYPES:
        if (BASE / ("screensaver" + ext)).exists():
            return "screensaver" + ext
    return ""


class Host:
    """Everything the settings app (d6ui.py) is allowed to do. See SPEC notes in d6ui.py."""
    base = BASE

    @staticmethod
    def _settings(cfg):
        sv = cfg["screensaver"]
        return {"theme": cfg["theme"], "brightness": int(cfg["brightness"]),
                "saver_enabled": bool(sv.get("enabled", True)),
                "saver_style": sv.get("style", "auto"),
                "saver_brightness": int(sv.get("brightness", 35)),
                "saver_file": sv.get("file") or own_saver_file(),
                "sounds": bool(cfg.get("sounds", True))}

    def load(self):
        cfg = load_config()
        return {"pages": cfg["pages"], "settings": self._settings(cfg)}

    def save(self, pages, settings):
        """Write the buttons and settings, and tell a running deck to pick them up."""
        s = {**d6layout.DEFAULT_SETTINGS, **(settings or {})}
        write_json(BUTTONS_PATH, {"version": d6layout.LAYOUT_VERSION, "pages": pages})
        raw = read_json(CONFIG_PATH, {}) or {}
        raw["screensaver"] = {**DEFAULT_CONFIG["screensaver"], **(raw.get("screensaver") or {}),
                              "enabled": bool(s["saver_enabled"]),
                              "brightness": max(MIN_BRIGHTNESS, min(100, int(s["saver_brightness"])))}
        raw["sounds"] = bool(s["sounds"])
        write_json(CONFIG_PATH, raw)
        state = read_json(STATE_PATH, {}) or {}
        state.update(brightness=max(MIN_BRIGHTNESS, min(100, int(s["brightness"]))),
                     theme=s["theme"], saver_style=s["saver_style"])
        write_json(STATE_PATH, state)
        if is_running():
            RELOAD_PATH.write_text("reload")

    def defaults(self):
        return {"pages": json.loads(json.dumps(d6layout.PAGES)),
                "settings": dict(d6layout.DEFAULT_SETTINGS)}

    def preview_cfg(self, settings, pages, page=0, size=170):
        s = {**d6layout.DEFAULT_SETTINGS, **(settings or {})}
        cfg = load_config()
        cfg["image"] = {**cfg["image"], "size": int(size)}
        cfg.update(brightness=int(s["brightness"]), sounds=bool(s["sounds"]), pages=pages, page=page,
                   theme=s["theme"] if s["theme"] in look.THEMES else cfg["theme"])
        cfg["screensaver"].update(enabled=bool(s["saver_enabled"]), style=s["saver_style"],
                                  brightness=int(s["saver_brightness"]))
        return cfg

    def running(self):
        return is_running()

    def control(self, what):
        if what in ("stop", "restart") and is_running():
            STOP_PATH.write_text("stop")
            for _ in range(100):
                if not is_running():
                    break
                time.sleep(0.1)
        if what in ("start", "restart") and not is_running():
            spawn_background("--wait")

    def startup(self, enable=None):
        path = startup_vbs_path()
        if enable is True:
            cmd_install_startup(None, None)
        elif enable is False and path.exists():
            path.unlink()
        return path.exists()

    def set_saver_file(self, src):
        """Copy a picture/GIF in as the screen saver; None or '' removes it."""
        if src:
            src = Path(src)
            if src.suffix.lower() not in SAVER_FILE_TYPES or not src.is_file():
                raise ValueError("Pick a GIF or a picture (.gif, .png, .jpg, .webp)")
        for ext in SAVER_FILE_TYPES:
            (BASE / ("screensaver" + ext)).unlink(missing_ok=True)
        if not src:
            return ""
        dest = BASE / ("screensaver" + src.suffix.lower())
        shutil.copy2(src, dest)
        return dest.name

    def log_tail(self, n=50):
        try:
            return LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
        except OSError:
            return []


def cmd_ui(cfg, args):
    import d6ui
    d6ui.run(Host())


COMMANDS = {
    "ui": cmd_ui, "check": cmd_check, "restart": cmd_restart, "shortcuts": cmd_shortcuts,
    "run": cmd_run, "start": cmd_start, "stop": cmd_stop,
    "install-startup": cmd_install_startup, "remove-startup": cmd_remove_startup,
    "learn": cmd_learn, "test-screen": cmd_test_screen, "probe": cmd_probe,
}


def main():
    p = argparse.ArgumentParser(description="Local controller app for Fifine D6")
    p.add_argument("command", choices=list(COMMANDS), nargs="?", default="ui",
                   help="what to do (no command = open the settings window)")
    p.add_argument("--debug", action="store_true", help="print raw packets")
    p.add_argument("--active", action="store_true",
                   help="probe: send wake-up + test images first")
    p.add_argument("--wait", action="store_true",
                   help="run: wait for an old copy to finish (used by restart)")
    args = p.parse_args()
    cfg = load_config()
    try:
        COMMANDS[args.command](cfg, args)
    except DeckNotFound as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
