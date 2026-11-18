# What's new in D6Deck

Every update that goes on GitHub is listed here, newest first.
Downloads are on the [Releases page](https://github.com/doom37724-lab/D6Deck/releases).

## 1.2.0 – 18 November 2026

### Added
- **Game Mode key** (page 4): press it twice to close the apps eating your memory before you play.
  It closes open apps using over 150 MB the polite way (like clicking their X, so anything
  unsaved still asks first), and fully quits tray apps that hog memory: LM Studio (whose AI
  model sits in your graphics card) and CurseForge. It never closes Windows, D6Deck, Claude,
  Steam and other game launchers, Discord, Spotify or OBS. Shows "Closed 3" (or "All clear")
  when done.
- **No more frozen deck when the PC turns off:** D6Deck now hears Windows shutting down or
  going to sleep and puts the D6 to sleep first. It wakes again when the PC wakes, or when you
  press any key.
- **Picture for when the PC is off:** save one of your own still pictures inside the D6 itself
  (Settings → screen saver section). It shows when the D6 starts up without D6Deck running.
  Still pictures only (PNG, JPG or BMP). The keys go dark for about 10 seconds while it saves,
  so don't unplug the D6 then.
- New commands: `offscreen <picture>` and `pretend-off` (tests the shutdown handling without
  shutting down).
- `--version` shows which version you have, and the log says it on every start.

### Fixed
- **Blue line on the edge of every key.** The D6 smears colour on pictures that aren't a whole
  number of 16-pixel blocks, and keys are 85 pixels. Key pictures are now padded to 96 pixels
  behind the scenes. (A very faint line may remain; that one comes from the D6's screen itself.)

### Removed
- Nothing.

## 1.1.0 – 17 November 2026

Not released on its own; it's included in the 1.2.0 download.

### Added
- **Page 4 – Obsidian notes:** open your vault, today's daily note, a new note, search, the
  Inbox note, and "Clip It", which adds whatever text you copied to the Inbox without opening
  Obsidian.
- Security policy (SECURITY.md) with private vulnerability reporting.

### Changed
- Tidier README with tips and a file list.

## 1.0.0 – 16 November 2026

The first release.

- Three pages of keys: music (Spotify cover, song, time, volume knob, Discord mute), PC (stats,
  apps, screenshot, brightness, screen off, lock) and fun (meters, stopwatch, mini-games and toys).
- Smooth animations, 4 looks, screen savers (including your own picture or GIF across all keys).
- Settings window, smart app finding, waits for the deck to be plugged in, live reload.
- One-file `D6Deck.exe`, no Python needed.
