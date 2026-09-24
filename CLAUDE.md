# CLAUDE.md

Guidance for working on this repository. **Every rule below exists because
breaking it has already caused a real bug.** The reasoning is kept short;
`git log` and the module docstrings hold the long versions.

@breakaway_branding.md

## Running and checking

```bash
uv run main.py                     # only the control screen opens
PIT_LEDS_FAKE=1 PIT_NEXUS_FAKE=1 uv run main.py
uv run main.py --self-check        # boot everything offscreen, exit 0/1
```

No unit tests and no linter. The checks run the real thing and exit 0/1:
`--self-check`, `tools/relay_check.py` (`--local` against `wrangler dev`),
`tools/webcast_check.py`, `tools/upgrade_check.py`, `tools/eq_check.py`. Run
the one that covers what you touched.

| Doc | Owns |
|---|---|
| [`DATABASE.md`](DATABASE.md) | **The contract** for every table, query and invariant. Read before touching storage; update it with any table, migration or query |
| [`NEXUS.md`](NEXUS.md) | The event feed, the relay, every Nexus field |
| [`nexus-relay/README.md`](nexus-relay/README.md) | The Cloudflare Worker (a separate npm/TypeScript subproject) |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | Build, release, install, update, the `--self-check` table |
| [`OPERATOR_GUIDE.md`](OPERATOR_GUIDE.md) | What the crew does. Operator-facing detail goes there, not here |

## Packaging rules that constrain code

- **Nothing outside `app/paths.py` builds a path from `__file__`.** A frozen
  app has a read-only resource tree and a separate writable data tree.
  `paths.resource()`, `paths.data()`, `paths.data_dir()` and `paths.find()` are
  the only correct ways to ask. From a checkout both are the repo.
- **`--self-check` is the gate an update must pass on the pit machine.**
  Packaging failures are invisible otherwise: the app starts, and only one
  screen ever finds out. It has caught PyInstaller dropping Chromium's helper
  and its `.pak`s; its `network` line guards QtNetwork's TLS plugin (without
  which the relay's `wss://` never connects). Add a check when you add
  something a bundle can silently lose. The shipped app is `console=False`, so
  `_attach_console()` borrows the parent console, and the report also goes to
  `selfcheck.log`.
- **The build is unsigned, and that's a decision, not an oversight**:
  Smart App Control accepts only Trusted-Root-Program signatures, so no free
  option exists. Pit machines run with SAC off; `--self-check`'s `appcontrol`
  line and the installer's `InitializeSetup` warn about Evaluation mode.
  **Only read** `VerifiedAndReputablePolicyState`, never write it.
- **Startup shows progress from the first second, in two stages.**
  PyInstaller's launcher splash (Windows, `packaging/splash.png`) covers the
  time before Python runs, which on a pit machine is most of it (2,100 files,
  Defender scanning new DLLs after an update). `BootSplash`
  (`app/widgets/boot_splash.py`) takes over at the same size once Qt is up
  and names each `_boot()` step in `main.py` *before* running it. So **keep
  `main.py`'s top-level imports minimal**: everything heavy loads inside the
  first step. Change the splash? Rerun `tools/make_splash.py` and commit the
  PNG. Every non-GUI path must close the launcher splash, and the updater's
  hidden self-check sets `PYINSTALLER_SUPPRESS_SPLASH_SCREEN`. A splash
  failure must never stop the app from starting.
- **`crash.log`** (`app/crash_log.py`, installed first thing in `main.py`)
  records unhandled exceptions and native crashes; `--self-check` prints the
  last one. The app has no console, so it's the only record of a failed start.
- **The version is stamped by CI from the tag, never edited.** `app/version.py`
  holds a sentinel that `version.is_release()` refuses.
- **Windows stdout is cp1252 when redirected.** Every entry point that prints
  calls `console.use_utf8()` first (`tools/make_manifest.py` inlines it). Fix
  the encoding, never the character.

## Architecture

Four PyQt6 windows. **Only the control screen is built at startup**; the
others come from factories (`set_window_factories`) behind power toggles and
are **destroyed when switched off**: no timers, no subscriptions, no Chromium.
A screen is rebuilt from scratch each time it's powered on.

- **Anything holding a window reference must let go.** `touch.py`'s router
  forgets a widget on `destroyed`.
- **Widgets on these screens connect app-wide signals to bound methods,
  never lambdas.** PyQt auto-disconnects a QObject's method when its C++ side
  dies; a lambda keeps firing and crashed the app with `wrapped C/C++ object …
  has been deleted`.

| Window | Role |
|---|---|
| `ControlScreen` | operator panel: top bar, 220px sidebar, settings column |
| `PresentationScreenA` / `B` | overhead audience screens (a `QStackedWidget` of faces) |
| `ProjectScreen` | pit-front portrait touch panel: `content="board"` or `"cad"` |

**Modes** (`config.set_mode()`), whole-pit: `standard` (slide rotation, 45 s,
`RotationManager`), `judges` (`JudgesOverlay`, full-bleed artwork, header
follows the chassis), `lunch` (`LunchOverlay`, one auto-fitted line, the Trace
is its red). **Per-screen content** in standard mode
(`config.get(screen, "content")`): `rotation`, `next_match`, `checklist`,
`diagnostics`, `robot_info`.

**`config`'s per-screen settings are memory-only and reset every launch**
(theme, content, slide index, `checklist_id`). Anything that must survive a
reboot goes in a JSON beside the database (`webcast.json`, `nexus.json`,
`update.json`) or in the database.

### Singletons (`app/lazy_proxy.py`)

Safe to import at module level; raise if used before their `init_*()`.

| Import | Init | Needs |
|---|---|---|
| `app.config.config` | `init_config()` | **first** |
| `app.update.update` | `init_update()` | nothing, deliberately: updating out of a broken config/DB must work |
| `app.db.db` | `init_db()` | import `app.db.migrations` first (registers by side effect) |
| `app.rotation.rotation` | `init_rotation()` | config |
| `app.judges_slides.judges_slides` | `init_judges_slides()` | config |
| `app.cad_assets.cad_assets` | `init_cad_assets()` | config; HTTP server on :8765 |
| `app.checklist.checklist` | `init_checklist()` | db; **before presentation screens are built** |
| `app.leds.leds` | `init_leds()` | after `cad_assets` (subscribes to `subsystem_focused`) |
| `app.music.music` | `init_music()` | db |
| `app.admin.admin` | `init_admin()` | db |
| `app.nexus.nexus` | `init_nexus()` | config only |
| `app.nexus.alerts.alerts` | `init_alerts()` | nexus, leds |
| `app.webcast.webcast` | `init_webcast()` | before the control screen (its Telemetry panel subscribes) |

**Upgrades keep what people typed, and that's tested:** `tools/upgrade_check.py`
fills every user-owned table, runs the shipped seed DB and a migration against
it, and checks every row survived. `paths.seed_user_data()` must only ever
*skip* an existing file.

### Signal flow

All cross-window communication is Qt signals; windows never call each other.
The control screen never holds a presentation window.

- `config.team_changed` → every screen re-brands; LEDs follow a colour look.
- `config.mode_changed` → stacks swap, LED preset + EQ curve swap, judges ducks
  audio. **Judges and lunch are LED overrides** (white / red) that ignore alerts.
- `config.set(screen, "slide_index", n)` → that screen jumps; it writes the key
  back as it rotates. **Two idempotence guards close the loop**:
  `config.set()` ignores an unchanged value and `SlidePanel.set_slide()`
  ignores a re-select. Keep both if you add a writer.
- `config.set(screen, "content", …)` → the screen swaps face.
- `config.logs_changed` → boards and fun-fact slides re-read after an import.
- `checklist.items_changed` / `item_toggled`, `judges_slides.*`,
  `cad_assets.subsystem_focused`, `admin.lock_state_changed`, `nexus.*` (see
  NEXUS.md).
- **Mode buttons follow `config.mode_changed`, not their own click.**

## Qt traps already hit

- **`addWidget(w, alignment=…)` lays out at `sizeHint()`**, ignoring
  height-for-width and expanding policies: wrapped labels clip, and a custom
  widget without `sizeHint()` gets zero width (the Trace silently never
  painted). Centre with `addStretch / addWidget / addStretch`. Likewise
  `layout.setAlignment(AlignCenter)` collapses a `QVBoxLayout`.
- **`helpers.label()` does not word-wrap.** Call `setWordWrap(True)` on prose,
  or one long label pushes the whole panel wider than the window.
- **A `QWidget` ignores a stylesheet border unless `WA_StyledBackground` is set.**
- **The app-wide `QPushButton` rule has 16px horizontal padding**; reset it in
  tight places (`ScreenCard`).
- **`R_PILL` is 999, a sentinel.** `drawRoundedRect` clamps x/y radii
  independently, so 999 makes an ellipse. `brand_widgets._radius()` clamps to
  half the height.
- **Derive geometry from `self.rect()`**, never a literal size, and **derive a
  resting visual state in `paintEvent`** from the model (`isChecked()`), never
  remember it from a signal that callers can block.
- **Never name a font family Qt can't resolve.** It costs a ~40 ms alias sweep
  per `QFont`. Use `brand_widgets.mono_font()` (the whole mono stack).
  `addApplicationFont()` silently returns -1 for a relative path.
- **Never `showFullScreen()` an audience screen.** Its trailing
  `activateWindow()` fights `WindowDoesNotAcceptFocus`;
  `display._show_fullscreen()` does it without. Move to the monitor *before*
  going full screen.
- **`default_fullscreen()` is `len(screens) > 1`.** With one monitor a
  full-screen audience window would bury the only way to turn it off.

## Touch (`app/touch.py`)

Two panels (control and project) take fingers at once. Qt's touch→mouse
synthesis is one application-wide mouse state, so a second panel latches the
first. `touch.install(app, control, project)` routes **each touch point** to its
own target with its own press/move/release, keyed by `(device, point id)`.

- **Only those two top-levels get `WA_AcceptTouchEvents`, never children.**
  `QWebEngineView`'s render widget flags itself, so Chromium gets raw
  multi-touch for pinch/pan. Presentation screens and dialogs keep Qt's
  synthesis.
- Propagate a press up the parent chain the way Qt does: events arrive
  accepted and `QWidget`'s default handler calls `ignore()`.
- **Past `_DRAG_SLOP` a drag is a scroll**: the router releases the pressed
  widget *outside* its rect and drags the scroll area. Sliders are exempt.
  **So activate on release-inside, never on `mousePressEvent`.**
- **A synthesized press moves no focus.** `_focus_on_press` activates the
  window and walks to the first `ClickFocus` widget. Presentation screens carry
  `WindowDoesNotAcceptFocus`; the project screen doesn't.
- **Touch sends no `leaveEvent`.** `touch.is_touch(event)` lets hover styling
  (e.g. `_CardButton`) skip a tap.
- CAD viewer: canvas `touch-action: none`; ignore a `touchend` that ended a
  pinch and the compatibility click after a tap.

## Painted surfaces

### The chassis (`app/widgets/chassis.py`)

Every full-screen audience surface subclasses `Chassis` (paints ground, plate,
header band, footer ledger; subclasses fill `paint_stage()` / `paint_footer()`),
or `PlatePanel` when it hosts a live layout. Two panels in one glance must not
look like two apps.

    ┌─ 40px inset ─────────────────────────────┐
    │  BREAKAWAY 3937      SCREEN A / STANDARD │  header, top 56, h 94; 2px rule at 150
    │  the stage                               │  216 → height−168
    │  01 / 07  ▔▔▔▔▔▔▔▔▔▔▔▔▔       ROTATION A │  footer ledger
    └──────────────────────────────────────────┘

- **Every number is a design measurement through `s()`**, a **contain fit**
  (`min(w/DESIGN_W, h/DESIGN_H)`), never one axis. `DESIGN_W/H` also drive
  `sizeHint()`, so a portrait surface opens portrait.
- The ground is cached (`_ground`); the 34 s ambient drift must not rebuild it.
  A window that's never mapped (`WA_DontShowOnScreen`) stops its own timers
  (`_stop_if_unseen()`).
- Light theme is a derivative (cast shadow, N200 rules), not a token swap.
- **`plate_tint()`** fills the plate with one colour and turns ink white (the
  Next Match board's red/blue card). Anything in the alliance colour on a tinted
  plate must reverse.
- Multi-line type goes through `Chassis.draw_wrapped()` (`QTextLayout`):
  `drawText()` uses the font's own loose leading and overflows at 140px.

### The rotation (`app/slides.py`, `slide_panel.py`)

Four archetypes chosen by content shape: `statement` (red = the Trace),
`figure` (red = the FROM LOG seal; numeral fitted by `_fit_figure()`, 260px is
a maximum), `roster` (no red, the marks bring colour), `board` (red only for a
latched fault). **Zero red is allowed; two never is.**

- The stage is painted, not laid out. Transition: 260 ms out, 80 ms empty,
  420 ms in; the Trace draws over 900 ms.
- **The dwell rail reads `rotation.progress()`**, never its own clock, and is a
  child `SmoothRail` on a 50 ms tick so the panel can keep a slow one.
- `rotation_entries()` is the single source for both the rotation and the
  control screen's picker; `_BOARD_INDEX` places the live board last (only when
  a log exists). `rotation_slides()` is a classmethod so the picker needs no
  window.
- The rotation timer is suppressed while a screen shows a pinned face
  (`_standard_page()`).

### Diagnostics boards (`diagnostics_overlay.py`, `robot_info_overlay.py`)

A: *is anything wrong?* (glanceable). B: *what, on which motor?* (walk-up).
Both on the chassis, in the rotation (last) or pinned.

- **Neither computes anything**; every figure comes from `app/robot/diagnostics.py`.
- **Status is the robot's own fault flags**, not an invented threshold (see
  DATABASE.md). Red means a latched fault and marks only the fault and its
  origin. Nothing flashes.
- Sparklines read `samples.sample_1s`, never `sample`.
- **Layout is banded** (`_budget(height)`), never measured after layout; lists
  are sorted worst-first so a cap drops the healthy end.

### Checklists (`app/checklist.py`)

A per-screen content setting, not a mode. Each screen has its own
`checklist_id`. **Ticking happens on the control screen**, never the display.
Done is green (`STATUS_ONLINE`), because red is the team accent and reads as a
fault. Rows are sized from the live height with an explicit `setFixedHeight`.
Only deleting a list is admin-gated. Content is the team's: ship it empty.

### The pit-front panel (`app/widgets/interactive_board.py`)

1080×1920 portrait, touched by strangers. One scrolling surface:
identity → CAD → Act 472 → reach → programs → sponsors (the footer, pinned).

- **There is one CAD viewer and it moves.** A `QWebEngineView` is a Chromium
  process; `ProjectScreen` lends it to the board (`attach_cad`) and takes it
  back for the full-screen face (`detach_cad`). Never build a second.
- Detail rises (`_DetailSheet`, 340 ms) over the lower two-thirds; the CAD stays
  visible. Every programme card stays reachable.
- **Every colour is a role** (`"ink"`, `"body"`, `"muted"`, `"faint"`,
  `self.tile`, `self.rule`) re-resolved by `apply_theme()`. The one red is the
  Act 472 plate.
- Type scales from the panel's width (`_apply_scale`).

## Theming and the red budget

Both stylesheets render from one QSS template (`app/theme.py`) over the
`DARK`/`LIGHT` bundles in `app/brand.py`. `dark_qss()` is app-wide; light is
per-window via `apply_theme(window, "light")`. The team accent is
`primary_color`, applied inline.

**One red thing per surface, and on a dark ground red is a filled shape,
never a letterform** (2.8:1). So: `RoundedButton` `secondary` is a neutral
outline and there's one `primary` per panel; sliders, progress and selection
fill white/carbon; `eyebrow()` and section headers are muted;
`SelectableChip` (white fill) is the shared "chosen" mark. **On the control
screen red means an exceptional state**: Judges or Lunch active
(`ModeButton`). A status colour goes in a `StatusDot`, never in type; a
disabled `primary` still paints red, so drop it to `secondary`.

**Fonts:** `main._load_fonts` loads `assets/fonts/*.ttf` (Chakra Petch,
Roboto, JetBrains Mono). The dev Mac also has the first two installed, so a
missing bundled copy only shows on Windows.

**Teams:** add an entry to `app/teams.py`.

## Control screen

Clarity under a six-minute clock. Settings are a ruled list (`SettingRow`,
64px floor), the EQ an instrument, the CAN table a form; `divider()` is the
2px section rule, rows close with 1px. `PanelHeader` opens every panel.
Touch targets ≥ 46px. **The body scrolls as one** (`_body_scroll`, sidebar and
settings together) under a fixed top bar.

**Pit Systems:** LED Strips, Music, **Telemetry** (sidebar id `network`), Event
Feed, Software Updates.

**Telemetry (`network_panel.py`) is the home of all telemetry.** It has the
event relay (this machine's socket *and* the relay's own counters), which route
each piece of event data came through, the overhead displays, the LED
controller, recent drops, and robot logs (`RobotLogPanel`, embedded whole).
It redraws on signals; a 5 s tick only keeps ages honest.

**Screen placement (`app/display.py`):** per-screen `display` (monitor index,
unplugged → primary) and `fullscreen`. `place()` runs on every freshly built
window. A screen published to the network is built with
`WA_DontShowOnScreen` and never placed.

## Admin lock (`app/admin.py`, `admin_bar.py`)

The Breakaway mark is the door: an inline 46px `AdminBar` (Fixed size
policies, or it eats the window). **Closing always re-locks; never remember
the session.** Gated widgets are **hidden, not disabled**: put them in one
container and toggle it on `admin.lock_state_changed`. The LED kill switch is
never gated. PBKDF2-HMAC-SHA256, one row. **It's a UI lock, not a security
boundary**; keep the docstring honest. Test with `w.isVisibleTo(panel)`, not
`isVisible()`.

## LED strips (`app/leds/`, `firmware/`)

USB serial to an Arduino driving **SK6812 RGBW** strips. **The firmware owns
the animation loop**; the app sends short COBS/CRC-8 commands, never pixel
frames (on AVR, `show()` disables interrupts and drops serial bytes). Change
`protocol.py` and the `.ino` together.

**Measured facts, don't re-guess (2026-09-03 / 09-08):**

- **RGBW, 4 bytes/pixel, order RGBW.** Wrong format shows a solid colour as a
  3-pixel green/white/blue pattern while black still works. FastLED's
  `setRgbw()` can't be used on AVR (RAM); `packAndShow()` packs by hand, the
  controllers are declared `RGB`, `showLeds(255)`, with no correction, dither
  or power limiting. Budget power with `MAX_BRIGHTNESS`.
- **Two channels:** pin 6 → LEFT and RIGHT through a Y-split (76 px each,
  always mirrored), pin 5 → CENTRE (93 px). `SET_COLOR` segment `0` = centre,
  `1` = sides, `0xFF` = both; an optional 5th byte is the W die (fw ≥ 2.2).
- Effects are functions of **distance from the pit's centre**, in half-pixels.
  **Pixel 0 of CENTRE is the pit's centre** (`origin 0`), and
  `SIDES_INDEX0_OUTER 0`. Only a *chase* reveals either being wrong.
- **Snap colours to saturated primaries (`palette.snap`)** before the wire;
  brand red came out pink. Never send brand hexes straight to the strips.
- **White is the W die**, never R+G+B (tinted, 3× current).
  `OVERRIDE_WHITE_BRIGHTNESS` is 160 (~2.1 A); raise only against a known
  supply.
- **Static frames must not re-clock the strips** (the `dirty` flag): with
  unconditional shows the HELLO handshake failed and the board looked dead.
- **Three-way switch: WHITE / app / RED**, hard overrides; serial keeps being
  read in every position. Up is D7 (`SW_PIN_UP 7`), down is D2
  (`SW_PIN_DOWN 2`); the input shield's labels are wrong. **Probe a pin
  before condemning it** (`firmware/pit_switch_probe`). `SW_ACTIVE_LOW` wrong
  reads as a jammed switch. A throw of `255` degrades to two positions.
- **Resting look = white work light** (`MODE_PRESETS`, brightness 160). No
  host → violet sparkle (fw 2.4). **No manual OFF position**, on purpose.
- **SRAM:** 169 px ≈ 1560 B of 2 KB; ~250 px is the ceiling, then use an ESP32.
- **Move `EEPROM_MAGIC` on any `State` change** (now `0xBE`).
- Alerts are overlays (`leds.start_alert/clear_alert`); intent keeps updating
  underneath; the flash is host-driven at 3 Hz.
- **Known bug:** `SET_PIXELS` is overwritten by SOLID's next render; nothing
  sends it.

Tools: `firmware/pit_probe`, `tools/led_probe.py`, `tools/led_color_check.py`.

## Music (`app/music/`, `eq_field.py`)

Local-first. Spotify is out: internet for every call, a 5-user dev cap, no PCM
so no EQ. **libVLC is the engine because it's the only one with an EQ.**

- **Windows bundles libVLC** (`tools/fetch_vlc.py` → `vlc/`, SHA-256 checked).
  `engine._point_at_bundled_vlc()` runs **before `import vlc`** and sets
  `PYTHON_VLC_LIB_PATH` / `PYTHON_VLC_MODULE_PATH` (a missing module path plays
  silently with no error) plus `os.add_dll_directory`. `python-vlc` raises
  `OSError`, not `ImportError`. Keep the licence files.
- **The EQ is drawn as a response curve**: the sum of ten peaking filters,
  ±20 dB. Press anywhere to move the nearest band, which stays locked for the
  drag. Presets are chips in authored order (Flat first).
- **The band display is a real measurement**: one band-pass biquad per band,
  Q 1.41, on an **output-less shadow decoder** carrying the same EQ (libVLC
  audio callbacks replace the output, so the pit's player is never touched).
  Floor −48 dBFS; decay 90 dB/s; repaint 33 ms (the decoder delivers every
  ~42 ms); resync at 2 s/350 ms. Colour is anchored to the field; red is the
  top eighth only. The readout paints last. **Off by default**, with no timer
  while off. `tools/eq_check.py` proves it.
- No explicit-content filter (removed in `_v5_drop_explicit`); the team curates.

## Robot logs (`app/robot/`)

`.hoot` (owlet → `.wpilog` in scratch space), `.wpilog`, and Phoenix `.txt`
are each a `_Source` yielding `(t_ms, device_type, can_id, signal, num, label)`;
storage can't tell them apart. **Add a format by writing a `_Source`.** Schema,
queries and the four invariants that give plausible wrong answers are in
DATABASE.md; read them before writing a query.

- `wpilog.entry_identity()` scans right to left for `<Type>-<digits>`, so hoot
  and text land on the same `device` rows and CAN names carry over.
- Application signals go to pseudo-device `Robot`, CAN id −1, hidden from the
  CAN map. `classify()` is the Phoenix catalogue only; anything with `/` is
  telemetry.
- Unstorable types are counted in `ImportResult.skipped`; numeric arrays
  expand up to `MAX_ARRAY_WIDTH` 32 (the PDH has 24 channels).
- Scratch goes to `$PIT_LOG_SCRATCH` → temp → beside the source, never a log
  archive. **The duplicate check runs before owlet.**
- `owlet.describe()` names OS, CPU and binary; `_PATTERNS` picks by platform.
- **Fun-fact slides: every number is real**, and jokes are at our own expense.
- CAN naming is data entry (not gated); deleting a session is gated.

## Pit LAN screens (`app/webcast/`)

The overhead screens can be published to Raspberry Pis over Ethernet. **The
browser draws; the pit machine sends state.** Rasterising frames cost 22–40% of
a core; state costs 0.1%.

| File | Role |
|---|---|
| `state.py` | a screen as JSON |
| `sockets.py` | `QWebSocketServer` on the Qt loop, :3939 |
| `server.py` | page, CSS/JS, fonts, judges artwork, :3938 |
| `service.py` | subscriptions and lifetime |
| `assets/webcast/` | the page |

- Protocol after Cheesy Arena: `{"type","data"}`, full state on connect, every
  `screen` message complete.
- **The dwell rail is never sent**, only its deadline plus `server_now_ms`;
  `screen.js` animates it.
- The headless window stays as the state engine (it owns the rotation).
- **`screen.css` ports `chassis.py` number for number**: `--u` is one design
  pixel. Use the plate's ink roles (`state.palette()`), not the control bundle.
- No `animation-fill-mode: both`: the resting state must be the visible one.
- **Two switches:** power (the sidebar) and Monitor/Network. `_reconcile()`
  resolves both; power has no signal, so `_on_power_toggled` calls
  `webcast.screen_power_changed()`. `_is_on()` reads window existence.
- **Off and unreachable show the same branded card.** Nothing on an audience
  screen names a port, socket, setting or the control panel; operator detail
  goes to the console. `webcast_check.py` enforces both.
- Assets are fetched by content hash (`asset_version()`), never cached stale.
- The pointer is never hidden on a networked display.
- **Own every socket:** `setParent(self)` on accept (PyQt hands them back
  unparented); disconnect by handle, never wildcard, inside `disconnected`;
  `stop()` aborts, then drains deferred deletes.
- `app/qt_log.py` writes Qt's warnings to `qt_warnings.log` (the app has no
  console).

## Nexus event feed (`app/nexus/`, `nexus-relay/`)

**NEXUS.md is the reference.** In short: frc.nexus → webhooks (all events) →
**the relay at `nexus.bh-stack.com`** (Cloudflare Worker + one Durable Object
per event) → `wss` push → `RelayLink` → `_offer_status()`. The relay also
pulls Nexus while a pit listens and mirrors `/api/v1/…` holding the API key.
A pit machine needs only `secrets/nexus_relay_token`. **No local webhook
server and no cloudflared, by design.**

- **Credentials live in `secrets/`, read only through `credentials.read()`**
  (`PIT_SECRET_<NAME>` overrides). Never in the DB, `nexus.json` or `app/`.
  The update token predates this and stays as `update_token`.
- **Newest `dataAsOfTime` wins, on both ends**: the relay's `offer()`, then
  `_offer_status()`.
- **`_tiered()`: relay first, direct second**, resolved on the event loop. A
  404 is an answer and stops there. **The live poll runs only while the socket
  has been down for `fallback_after_s`.**
- A 404 on a sub-resource means "nothing published"; only the event's own
  401/403/404 cancels other fetches.
- "Our" match is derived at call time from `config.active_team`;
  `match_changed` compares label, status *and* times.
- Team numbers are strings, timestamps are Unix ms (`api.team_str()`,
  `api.when()`).
- `provision.apply()` only writes; `auto_import()` applies `pit-setup.json`
  once and renames it. Retired keys are skipped with a note.
- Alerts are about state, brief (2 s flash + 1 s steady; banner 10 s);
  inspection fires only on a transition. `QueueBanner` is a child of the
  window, not a page.
- The Next Match board counts to the *next* step (`_NEXT_STEP`) and ticks only
  while shown.
- **Relay rules:** every webhook POST gets 200 (Nexus silently disables
  failing hooks); one storage key per room (the free plan allows 100k row
  writes a day across all events); broadcast before save; no state in DO class
  fields; no `setInterval` in the DO; `RelayLink.stop()` sets `off` *before*
  `abort()`.
- `PIT_NEXUS_FAKE=1` serves `assets/nexus/examples.json`; `PIT_NEXUS_QUIET=1`
  (set by `--self-check`) opens no socket and no timers. **Every surface
  showing this data carries `api.ATTRIBUTION`.**

## Self-update (`app/update/`)

Tag → CI → private Release → pit machines. Operator side is in DEPLOYMENT.md.
Windows-only CI (macOS bills 10×).

- **Versioned folders behind a directory junction** (`current\`). Never
  overwrite a running exe; never `shutil.rmtree` a junction (it deletes the
  target), use `os.rmdir`. The installer does the same in Pascal.
- **The staged build self-checks** with `PIT_DISPLAY_DATA`, `PIT_CAD_PORT` and
  `PIT_LEDS_FAKE` set, so it can't touch the live DB, port or serial.
- Prune at startup, not at swap. An install not of this shape is left alone.
- **Checking is automatic; downloading never is.**
- Private repo: fetch assets by id; **drop `Authorization` on the redirect to
  object storage** (`_Redirect`). A release without `manifest.json` is skipped.
- Preferences are JSON, not DB (reachable when the DB is broken).
- `installer.iss`: `[Code]` comments are `//`, never `{ }` (a brace comment
  ends at `{app}`), and the file is pure ASCII. `tools/check_installer.py`
  enforces both.

## Empty stubs

`app/db/repositories/` and `app/db/sync/` are placeholders for a future
on-prem SQL Server sync.
