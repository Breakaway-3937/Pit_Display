# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@Breakaway_Branding.md

## Running the app

```bash
uv run main.py
# or via the installed entry point:
uv run pit-display
```

There are no tests and no linter configured. The only runtime check is launching the app.

## Architecture

Four-window PyQt6 desktop app for FRC pit displays. All windows are created at startup; only the control screen is shown on boot. The other three are shown/hidden by power toggles in the control screen sidebar.

**Window roles:**
- `ControlScreen` — operator panel (team selector, mode buttons, per-screen settings, judges slide picker)
- `PresentationScreenA` / `PresentationScreenB` — audience-facing rotating slides; switch to `LunchOverlay` or `JudgesOverlay` based on mode
- `ProjectScreen` — shell, currently a placeholder

**Three display modes** (set globally via `config.set_mode()`):
- `standard` — rotating `SlidePanel` with 45-second auto-advance driven by `RotationManager`
- `judges` — `JudgesOverlay` showing slides from `assets/judges_slides/`; controlled from the control screen thumbnail picker
- `lunch` — `LunchOverlay` painted entirely in `paintEvent` (bypasses Qt layout for guaranteed full-screen font sizing)

### Global singletons (lazy-proxy pattern)

Five module-level singletons share the `LazyProxy` helper in `app/lazy_proxy.py` — safe to import at module level, but raise if accessed before their `init_*()` function is called in `main()`:

| Import | Init call | Purpose |
|---|---|---|
| `from app.config import config` | `init_config()` | Active team, display mode, per-screen theme settings; emits Qt signals on change |
| `from app.rotation import rotation` | `init_rotation()` | 45-second timer that emits `advance` signal in standard mode |
| `from app.judges_slides import judges_slides` | `init_judges_slides()` | Loads images from `assets/judges_slides/`, tracks current slide index |
| `from app.cad_assets import cad_assets` | `init_cad_assets()` | Local HTTP server (port 8765) + subsystems config + CAD focus signals |
| `from app.db import db` | `init_db()` | SQLite connection + versioned migration runner (`data/pit_display.db`) |
| `from app.leds import leds` | `init_leds()` | USB-serial link to the LED controller + strip state |
| `from app.music import music` | `init_music()` | Playback, queue, local library, 10-band EQ |
| `from app.admin import admin` | `init_admin()` | Admin lock gating the LED/EQ controls |

`init_config()` must be called first; the others depend on `config` being ready.
`init_leds()` must come after `init_cad_assets()` (it subscribes to
`subsystem_focused`); `init_music()` and `init_admin()` after `init_db()` (they
seed EQ presets and the admin credential).
`app.db.migrations` must be imported before `init_db()` — it registers the
schema by side effect.

### Signal flow

All cross-component communication uses Qt signals — no direct calls between windows:
- `config.team_changed` → all screens re-brand (colors, labels)
- `config.mode_changed` → presentation screens swap their `QStackedWidget` page
- `config.screen_setting_changed` → theme changes applied per-window via `app.theme.apply_theme()`
- `rotation.advance` → `SlidePanel.next_slide()` on each presentation screen
- `judges_slides.slide_changed` / `slides_reloaded` → `JudgesOverlay` and `_SlidePicker` in control screen
- `config.team_changed` → LED strips wash to the team colour (when following)
- `config.mode_changed` → LED preset + EQ curve swap; judges mode auto-ducks audio
- `cad_assets.subsystem_focused` → strips echo that subsystem's `accent_color`
- `admin.lock_state_changed` → `LEDPanel` / `MusicPanel` show or hide their gated blocks

### Theming

Both stylesheets are rendered from one QSS template in `app/theme.py`, fed by the `DARK` / `LIGHT` palette bundles in `app/brand.py` — change a token there and both themes update. `theme.dark_qss()` is set app-wide at startup; light theme is applied per-window via `app.theme.apply_theme(window, "light")`. Clearing the window stylesheet (setting `""`) falls back to the app-level dark QSS.

Accent colors come from the active team's `primary_color` field and are applied inline via `setStyleSheet()` wherever the team color is needed dynamically.

### Fonts

`main.py:_load_fonts` globs `assets/fonts/*.ttf` at startup — dropping a TTF in
is all that is needed. Bundled: **Chakra Petch** (display), **Roboto** (body),
**JetBrains Mono** (data/numbers, added Aug 2026, OFL — license alongside it).

Two things that will bite:
- **`QFontDatabase.addApplicationFont()` silently returns `-1` for a relative
  path.** `_load_fonts` builds an absolute path, so it works; ad-hoc scripts
  using a relative path will appear to fail while the app is fine.
- **Never name a font family Qt cannot resolve.** It triggers a full font-alias
  sweep on every `QFont` construction (~40ms) plus a `qt.qpa.fonts` warning. For
  the mono role use `brand_widgets.mono_font()`, which sets the whole
  `brand.FONT_MONO_STACK` (JetBrains Mono → Menlo → Consolas → DejaVu → Courier)
  so it degrades to a real monospace anywhere.

Note Chakra Petch and Roboto also happen to be installed in `~/Library/Fonts/`
on the dev Mac, so a missing bundled copy can go unnoticed there — check on the
Windows pit machine.

### Adding a team

Edit `app/teams.py` — add an entry to the `TEAMS` dict. The control screen combo box picks it up automatically.

### Judges slides

Drop numbered PNG/JPG files into `assets/judges_slides/` (e.g. `01_intro.png`, `02_robot.png`). Files are sorted alphabetically. Click "Reload" in the control screen to rescan.

### LED strips (`app/leds/`)

USB serial to an Arduino Uno/Nano driving WS2812B. **The firmware owns the
animation loop** — the app sends short commands, never pixel frames. This is not
a style preference: on AVR, `FastLED.show()` disables interrupts for the whole
strip write (~30µs/pixel, so ~9ms at 300 LEDs) and drops incoming serial bytes.
Streaming corrupts, and worse the longer the strip. Commands also mean the pit
stays lit if this app crashes.

| File | Role |
|---|---|
| `protocol.py` | COBS framing, CRC-8, opcodes, payload builders. Pure Python — no Qt, no I/O, so it is testable without hardware |
| `link.py` | `SerialLink` QThread: VID/PID discovery, HELLO handshake, write queue, 1Hz heartbeat, reconnect w/ backoff. `MockLink` when `PIT_LEDS_FAKE=1` |
| `effects.py` | Named presets; `MODE_PRESETS` maps display mode → preset |
| `service.py` | `_LEDService` singleton — owns intent, replays it on reconnect |
| `firmware/pit_leds/pit_leds.ino` | The controller. Set `NUM_LEDS` and `LED_PIN` here |

**SRAM ceiling:** the ATmega328P has 2KB and FastLED uses 3 bytes/pixel.
150px = 450B (fine), 300px = 900B (the practical limit), 500px = 1500B (too
tight — move to an ESP32). If you change `NUM_LEDS`, nothing in the app needs
editing: `HELLO` reports the geometry and the app adapts.

Develop without hardware: `PIT_LEDS_FAKE=1 uv run main.py`.

If you edit the protocol, **change both sides** — `protocol.py` and the `.ino`
share the opcode table, the CRC and the COBS implementation.

### Music (`app/music/`)

Local-first media hub. Spotify is deliberately *not* the foundation: it needs
internet for every call (this app exists for venues without it), Dev Mode caps
at 5 users and requires the owner to hold Premium, and there is no PCM access so
it can never be equalised. `sources.py` has the full reasoning and a stub.

| File | Role |
|---|---|
| `engine.py` | `MusicEngine` protocol; `VLCEngine` (real) and `NullEngine` (no VLC runtime) |
| `library.py` | Folder scan, mutagen tags, SQLite index. Re-scan is idempotent; vanished files are flagged `missing`, not deleted |
| `eq.py` | Ten bands, pit-tuned presets, persistence |
| `sources.py` | `LocalSource` (the product) and `SpotifySource` (stub) |
| `service.py` | `_MusicService` singleton — transport, queue, volume cap, duck, EQ |

**libVLC is chosen for the EQ.** Qt Multimedia has no DSP hooks at all, so an
equaliser is impossible through it. `python-vlc` raises **`OSError`, not
`ImportError`**, when the native runtime is missing — catch both. On Windows the
VLC runtime must be installed or `libvlc.dll` + plugins bundled; without it the
app still boots and the panel explains why.

Swapping to a sounddevice+numpy pipeline later (for beat-to-LED sync) means
implementing `MusicEngine` — nothing above it changes.

### Admin lock (`app/admin.py`)

Gates the advanced LED controls and the whole equaliser. Operator guide:
[`ADMIN_GUIDE.md`](ADMIN_GUIDE.md).

**The Breakaway mark in the control screen's top-left is the door** — its
`mousePressEvent` is bound to `ControlScreen._on_brand_clicked`, which toggles
the inline `AdminBar` (`app/widgets/admin_bar.py`) that sits between the top bar
and the body. Not a separate screen and not a modal, deliberately: the operator
needs to watch the gated controls appear as they unlock.

**The bar is a 46px strip and must stay one.** It appears over the operator's
actual work, so it hugs its content: `QSizePolicy.Fixed` vertically on the bar
*and* the `QStackedWidget`, a pinned `_ROW_H` on the stack, and compact 30px
controls. Without the Fixed policies it expands to fill whatever vertical space
the parent layout has spare — which was most of the window. The status message
shares the row (replacing the hint text) rather than taking a second line; only
the change-password form grows it, to 85px, and only while open.

**The lock is session-only and re-locks on close.** `_close_admin()` calls
`admin.lock()` unconditionally, so hiding the bar always drops the unlock and
re-opening demands the password again. There is no persistence and no
stay-unlocked option — the display runs unattended for hours. Do not "improve"
this by remembering the session.

**What is gated:** LED brightness/speed/looks/colour/follow/save (but *never*
the on-off kill switch, which any operator may need), and the entire EQ block.
Gated widgets are **hidden, not disabled** — a greyed-out control invites
someone to go looking for the password.

To gate something new: subscribe to `admin.lock_state_changed(bool)` and call
`setVisible()`, following `LEDPanel._apply_lock` / `MusicPanel._apply_lock`. Put
the gated widgets in **one container** and toggle that, rather than tracking a
list of individual widgets.

Credential: PBKDF2-HMAC-SHA256, random per-credential salt, 200k iterations,
`hmac.compare_digest`, one row (`id = 1`) in `admin_credential` (migration
`_v6_admin`). Default password is `Password`; the bar nags until it is changed.
**It is a UI lock, not a security boundary** — the DB is a local file. The
docstring in `app/admin.py` says so at length; keep that framing honest.

**Testing gated widgets:** use `w.isVisibleTo(panel)`, **not** `w.isVisible()`.
The latter is False whenever any ancestor is hidden (e.g. a non-selected settings
panel), which tells you nothing about the gate.

### Media

No explicit-content filter — the team pre-curates the music folder. Do not
re-add per-track filtering; it was removed in migration `_v5_drop_explicit`.
Operator instructions for every media type: [`MEDIA_GUIDE.md`](MEDIA_GUIDE.md).

### Empty stubs

`app/db/` (repositories, sync) and `app/robot/` are empty stubs for future SQLite and robot-file features.
