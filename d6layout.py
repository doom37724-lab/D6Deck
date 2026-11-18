"""
d6layout - the factory-default buttons (what you get before you change anything).

Key positions: 1 = top-left ... 5 = top-right, 6-10 middle row, 11-15 bottom row.
Bump LAYOUT_VERSION when these defaults change in a way old buttons.json files must pick up:
an older buttons.json is then backed up (buttons.backup.json) and replaced automatically.
"""
LAYOUT_VERSION = 5

# Key 8 (the middle key) is the page key on every page: it shows which page you are on
# and flips to the next one.
PAGE_KEY = {"widget": "page", "color": "#3d4451", "action": {"type": "page", "to": "next"}}

# Page 1: music.
PAGE1 = {
    "1": {"widget": "play_pause", "label": "Play/Pause", "color": "#1f6feb",
          "action": {"type": "hotkey", "target": "media_play_pause"}},
    "2": {"widget": "spotify_cover", "color": "#121212",
          "action": {"type": "previous_song"}},
    "3": {"widget": "clock", "color": "#1c2430"},
    "4": {"widget": "spotify_title", "color": "#10231a",
          "action": {"type": "hotkey", "target": "media_next"}},
    "5": {"widget": "brightness", "color": "#7a5200"},
    "6": {"widget": "spotify_time", "color": "#10231a"},
    "7": {"widget": "volume", "color": "#17402a",
          "action": {"type": "mute"}},
    "8": PAGE_KEY,
    "9": {"label": "Next Song", "icon": "next", "color": "#146c37",
          "action": {"type": "hotkey", "target": "media_next"}},
    "10": {"label": "Like Song", "icon": "heart", "color": "#a3214a",
           "action": {"type": "spotify_like"}},
    "11": {"label": "Vol Up", "icon": "vol_up", "color": "#238636",
           "action": {"type": "volume", "delta": 4}},
    "12": {"label": "Vol Down", "icon": "vol_down", "color": "#8b2e2e",
           "action": {"type": "volume", "delta": -4}},
    "13": {"label": "Mod Manager", "icon": "gear", "color": "#5a3d1a",
           "action": {"type": "launch",
                      "target": r"%LOCALAPPDATA%\Deadlock Mod Manager\deadlock-mod-manager.exe"}},
    "14": {"label": "Spotify", "icon": "headphones", "color": "#146c37",
           "action": {"type": "launch", "target": "spotify:",
                      "fallbacks": [[r"%APPDATA%\Spotify\Spotify.exe"]]}},
    # Needs a matching keybind in Discord: Settings > Keybinds > Toggle Mute = Ctrl+Shift+Alt+M
    "15": {"label": "Discord Mute", "icon": "mic", "color": "#4752c4",
           "action": {"type": "hotkey", "target": "ctrl+shift+alt+m"}},
}

# Page 2: PC and app controls.
PAGE2 = {
    "1": {"widget": "status", "color": "#1c2430"},
    "2": {"label": "Task Mgr", "icon": "pulse", "color": "#3a4a5c",
          "action": {"type": "launch", "target": "taskmgr.exe"}},
    "3": {"widget": "clock", "color": "#1c2430"},
    "4": {"label": "Screenshot", "icon": "camera", "color": "#6e40c9",
          "action": {"type": "hotkey", "target": "cmd+shift+s"}},
    "5": {"widget": "brightness", "color": "#7a5200"},
    "6": {"label": "Streamlabs", "icon": "camera_video", "color": "#0f8a6f",
          "action": {"type": "launch_admin",
                     "target": r"%PROGRAMFILES%\Streamlabs OBS\Streamlabs OBS.exe"}},
    "7": {"label": "Files", "icon": "folder", "color": "#b08800",
          "action": {"type": "launch", "target": "explorer.exe"}},
    "8": PAGE_KEY,
    "9": {"label": "Bright Up", "icon": "bright_up", "color": "#9a6700",
          "action": {"type": "brightness", "delta": 10}},
    "10": {"label": "Bright Down", "icon": "bright_down", "color": "#6f4e00",
           "action": {"type": "brightness", "delta": -10}},
    "11": {"label": "Screen Off", "icon": "moon", "color": "#2d3a8c",
           "action": {"type": "sleep"}},
    "12": {"label": "Restart", "icon": "restart", "color": "#1f6feb",
           "action": {"type": "restart"}},
    "13": {"label": "Quit App", "icon": "power", "color": "#8b2e2e",
           "action": {"type": "quit"}},
    "14": {"label": "Desktop", "icon": "monitor", "color": "#1b6ac9",
           "action": {"type": "hotkey", "target": "cmd+d"}},
    "15": {"label": "Lock PC", "icon": "lock", "color": "#57606a",
           "action": {"type": "command", "target": "rundll32.exe user32.dll,LockWorkStation"}},
}

# Page 3: the fun page - PC meters, looks, games and toys.
PAGE3 = {
    "1": {"widget": "cpu", "color": "#0b4a6f"},
    "2": {"widget": "ram", "color": "#4a2a7a"},
    "3": {"widget": "stopwatch", "color": "#1c2430", "action": {"type": "stopwatch"}},
    "4": {"widget": "theme", "color": "#7a2a5c", "action": {"type": "theme"}},
    "5": {"widget": "saver", "color": "#2d3a8c", "action": {"type": "saver"}},
    "6": {"label": "Whack", "icon": "🐹", "color": "#8a5a1c",
          "action": {"type": "game", "name": "whack"}},
    "7": {"label": "Simon", "icon": "🧠", "color": "#8a2a6a",
          "action": {"type": "game", "name": "simon"}},
    "8": PAGE_KEY,
    "9": {"label": "Lights Out", "icon": "💡", "color": "#8a7a10",
          "action": {"type": "game", "name": "lights"}},
    "10": {"label": "Reaction", "icon": "⚡", "color": "#a35a00",
           "action": {"type": "game", "name": "reaction"}},
    "11": {"label": "Memory", "icon": "🃏", "color": "#1b6ac9",
           "action": {"type": "game", "name": "memory"}},
    "12": {"label": "Dice", "icon": "🎲", "color": "#8b2e2e",
           "action": {"type": "toy", "name": "dice"}},
    "13": {"label": "Coin", "icon": "🪙", "color": "#9a6700",
           "action": {"type": "toy", "name": "coin"}},
    "14": {"label": "8-Ball", "icon": "🎱", "color": "#3a3f8c",
           "action": {"type": "toy", "name": "8ball"}},
    "15": {"label": "Party", "icon": "🎉", "color": "#a3214a",
           "action": {"type": "game", "name": "party"}},
}

# Page 4: Obsidian notes. Uses the vault Obsidian last had open (set "vault" to pick another).
PAGE4 = {
    "1": {"label": "Obsidian", "icon": "library", "color": "#5b3fb5",
          "action": {"type": "obsidian", "do": "open"}},
    "2": {"label": "Today", "icon": "calendar", "color": "#7c5cdb",
          "action": {"type": "obsidian", "do": "daily"}},
    "3": {"widget": "clock", "color": "#1c2430"},
    "4": {"label": "New Note", "icon": "edit", "color": "#2f7d6d",
          "action": {"type": "obsidian", "do": "new"}},
    "5": {"label": "Search", "icon": "search", "color": "#1b6ac9",
          "action": {"type": "obsidian", "do": "search"}},
    "6": {"label": "Clip It", "icon": "paste", "color": "#a35a00",
          "action": {"type": "obsidian", "do": "clip"}},
    "7": {"label": "Inbox", "icon": "mail", "color": "#3a4a8c",
          "action": {"type": "obsidian", "do": "inbox"}},
    "8": PAGE_KEY,
    "9": {"label": "Game Mode", "icon": "gamepad", "color": "#b42318",
          "action": {"type": "game_mode"}},
}

PAGES = [PAGE1, PAGE2, PAGE3, PAGE4]

# Settings people can change from the settings app (and their starting values).
DEFAULT_SETTINGS = {
    "theme": "neon",          # which look (see d6look.THEMES)
    "brightness": 70,         # 10..100
    "saver_enabled": True,    # False = Screen Off goes fully dark
    "saver_style": "auto",    # see d6fun.SAVER_STYLES
    "saver_brightness": 35,   # 10..100
    "saver_file": "",         # your own picture/GIF for the screen saver ("" = none)
    "sounds": True,           # little sound effects in the games
}
