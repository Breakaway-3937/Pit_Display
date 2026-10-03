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

No unit tests and no linter. **`uv run tools/validate.py` runs every check
below, each isolated, and is the one command before a release**
(`--model` adds a real analysis, `--hardware` the LED controller;
logs in `.validation/logs/`). **`uv run tools/validate.py --app`** opens the
full app on a fresh test install: a private sync hub on this Mac (`tools/devhub.py`,
the real Worker in `wrangler dev`), a simulated home and second pit, the test
logs imported (`--setup-only` builds it and proves its first sync). **Checks
never read this Mac's own `data/`**: in a checkout `paths.seed_user_data()`
doesn't seed the database (it's the developer's, not a seed), so a scratch
tree starts as CI's seed does; `ai_check` imports `TEST_LOGS/fixture_faults_2026-07-29.pitlog.zst`.
The dev Mac's own app never syncs with the team (`sync.json` off).
`tools/import_check.py` pins every test log's complete import (the hoot ten
times: `--hoot-runs 10`).

The individual checks, each exit 0/1:
`--self-check`, `tools/relay_check.py` (`--local` against `wrangler dev`),
`tools/sync_check.py --local` (starts its own hub),
`tools/webcast_check.py`, `tools/upgrade_check.py`, `tools/eq_check.py`,
`tools/battery_check.py` (BFG decoder, simulated cart, panel),
`tools/ai_check.py` (analysis pipeline, panel, face, `--mcp`; `--engine llama`
for a real run on the bundled engine, `--model <tag>` on Ollama),
`tools/led_diag.py` (real LED controller). Run
the one that covers what you touched.

| Doc | Owns |
|---|---|
| [`DATABASE.md`](DATABASE.md) | **The contract** for every table, query and invariant. Read before touching storage; update it with any table, migration or query |
| [`NEXUS.md`](NEXUS.md) | The event feed, the relay, every Nexus field |
| [`nexus-relay/README.md`](nexus-relay/README.md) | The Cloudflare Worker (a separate npm/TypeScript subproject) |
| [`sync-hub/README.md`](sync-hub/README.md) | The sync hub Worker: API, deploy, budget |
| [`home/HANDOFF.md`](home/HANDOFF.md) | The home side (SQL Server master, MCP, Ollama), built by a session with access to that server; `home/OPERATIONS.md` runs it, `home/KICKOFF.md` starts it |
| [`home/REQUESTS.md`](home/REQUESTS.md) | **The channel to the home Claude session**: dated asks, each with a check. The home repo's `CLAUDE.md` imports it. The other direction is `../Database_Robotics_Pipeline/docs/INTEGRATION_GUIDE.md`. A change to a contract home implements gets a request here |
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
- **Python is pinned to 3.14** (`.python-version`, `requires-python`), so CI
  builds on it too: sync's bundles and uploads use the standard library's
  `compression.zstd`, which older Pythons lack (the self-check's `sync` line
  fails without it).
- **The version is stamped by CI from the tag, never edited.** `app/version.py`
  holds a sentinel that `version.is_release()` refuses.
- **Python HTTPS goes through `app/net.py`, verified by the OS (`truststore`),
  never `ssl.create_default_context()`.** Python's default reads a snapshot of
  the Windows certificate store and never triggers Windows' on-demand root
  download or trusts a school filter's re-signing CA the way Edge does. That
  broke updates with CERTIFICATE_VERIFY_FAILED on the first deploy machine
  while home worked. Any new HTTPS call uses `net.ssl_context()`; failures go
  through `net.describe_url_error()` so the operator gets the cause (clock,
  filter, missing root). `--net-check` diagnoses a machine; `--self-check`'s
  `network` line fails if `truststore` isn't bundled.
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
is its red). **The overhead screens are a matched set at all times** (`app/overhead.py`,
Brayden 2026-10-02): **one content choice for the pair**, each screen its half
(Next match: A the Nexus queue | B the event's schedule, ours marked, our
record; Robot: Diagnostics | Robot Info; Checklists: a list each, chosen per
screen; Did you know: Breakaway's | Arkansas's; Analysis: board | Robot Info).
The control screen's box sets both; `rotation.py` mirrors any per-screen
`content` write onto the partner, so no path leaves them mismatched. **Power,
theme, monitor, network publishing and the checklist stay per screen.**
Results and our record are TBA's (`tba_match`, `app/event_schedule.py`), the
schedule is Nexus's; "played" is play order, never the clock.

**The slide rotation is to be revised in its entirety (Brayden, 2026-10-02):
it'll be totally revamped.** `app/program.py` keeps only a few example stops
to prove the machinery works; don't extend it, redesign it.

**The overhead program** (`app/program.py`, driven by `app/rotation.py`):
**the two overhead screens move as one unit.** A stop names what A and B show
together (Diagnostics | Robot Info, Next match | Our schedule, a fact | a fact,
authored pairs), and the rotation timer sets **one** `slide_index` on both;
**a screen never advances itself** (two self-advancing lists drifted). A stop
whose data is missing (no log, no event, no facts) leaves both screens. A
picker jump on either screen moves both; a pinned screen keeps following the
position and rejoins in step. Stop sides are `Slide`s; `Slide.face` makes a
stop show a whole face, `Slide.credit` replaces "ROTATION A" with a data
credit. `STORY` and `_fact_pairs()` are **drafts** (pairings to be worked
through with Brayden). `tools/program_check.py` walks every stop.

**Per-screen content** in standard mode
(`config.get(screen, "content")`): `rotation`, `next_match`, `checklist`,
`diagnostics`, `robot_info`, `analysis`, `facts` ("Did you know?": home's
`tba_fact` sentences, `facts_overlay.py` + the webcast's `faceFacts`; pinned only,
pages a column that overflows, credits The Blue Alliance in the ledger).

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
| `app.batteries.batteries` | `init_batteries()` | nothing; before the control screen (its Batteries panel subscribes) |
| `app.webcast.webcast` | `init_webcast()` | before the control screen (its Telemetry panel subscribes) |
| `app.db.sync.service.sync` | `init_sync()` | db, and after every service it refreshes; before the control screen |
| `app.ai.service.analysis` | `init_analysis()` | db, leds; after sync (which signals it), before the control screen (its Analysis panel subscribes) |

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
- **Rebuild a list with `helpers.clear_layout()`**, which clears nested
  layouts too. It used to delete only direct widgets, so rows built as
  `QHBoxLayout`s left their labels and switches piled at the top-left (Home
  Datasets, 2026-10-03). Don't rebuild on a signal that fires every cycle
  (`sync.state_changed`); rebuild when what the rows show changed.
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

- **Sponsors are `_SPONSORS`**: files in `assets/Sponsor Logos/`, in display
  order, each with the ground its artwork was drawn for. A dark-ground mark
  gets a carbon plate; never recolour a mark to suit the panel. The spec ships
  the folder and `--self-check`'s `sponsors` line names any file that's lost.

- **There is one CAD viewer and it moves.** A `QWebEngineView` is a Chromium
  process; `ProjectScreen` lends it to the board (`attach_cad`) and takes it
  back for the full-screen face (`detach_cad`). Never build a second.
- Detail rises (`_DetailSheet`, 340 ms) over the lower two-thirds; the CAD stays
  visible. Every programme card stays reachable.
- **Every colour is a role** (`"ink"`, `"body"`, `"muted"`, `"faint"`,
  `self.tile`, `self.rule`) re-resolved by `apply_theme()`. The one red is the
  Act 472 plate.
- Type scales from the panel's width (`_apply_scale`).

### Screen wording (`app/wording.py`, `wording_panel.py`)

Admins edit the pit-front panel's words and the authored slides' eyebrow,
title and body in the app (Pit Systems → Screen Wording). **Every such text
is a `wording.field(key, default, …)`, registered by the module that shows
it**; the code's text is the default forever, an edit (team setting
`wording`, synced) overrides it, and an empty edit falls back. **Keys are
fixed once shipped**: board cards carry an `id`, slides a `Slide.key`, and
`program.STORY` pairs slides by key, never by (editable) title. The board
updates labels in place on `config.wording_changed` (never rebuild it: it
holds the CAD viewer); slides rebuild. Each field has a `max_len` that keeps
it inside its layout. New visitor-facing text on these surfaces should be a
field.

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
missing or broken bundled copy only shows on Windows: the Roboto files were
EOT web fonts named `.ttf` until 2026-10-01 and never loaded there. Check the
self-check's `fonts` line on a Windows build, not the Mac.

**Teams:** add an entry to `app/teams.py`.

## Control screen

Clarity under a six-minute clock. Settings are a ruled list (`SettingRow`,
64px floor), the EQ an instrument, the CAN table a form; `divider()` is the
2px section rule, rows close with 1px. `PanelHeader` opens every panel.
Touch targets ≥ 46px. **The body scrolls as one** (`_body_scroll`, sidebar and
settings together) under a fixed top bar.

**Pit Systems:** LED Strips, Music, Batteries (mock-up), **Telemetry** (sidebar
id `network`), **Analysis** (runs and the crew's ratings), Event Feed, Screen Wording (admin),
Software Updates.

**Telemetry (`network_panel.py`) is the home of all telemetry.** It has the
event relay (this machine's socket *and* the relay's own counters), which route
each piece of event data came through, the overhead displays, the LED
controller's serial link (heartbeat, round trip, rejected frames, switch), recent drops, and robot logs (`RobotLogPanel`, embedded whole).
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

- **Every strip write is a chance to flicker; write only what changed (fw
  2.9).** Flash test, 2026-09-24: steady white drawn once was clean, the same
  white re-sent ~10×/s (identical bytes, identical power) flickered. That is
  why the switch positions (one write per 10 s) never showed it and the app
  did. So `packAndShow()` skips a channel whose bytes match the last write
  (Fletcher-16, `sentSum`); only boot and the 10 s refresh (`forceShow`) write
  regardless. Don't add a redraw path that bypasses it.
- **`LED_TYPE SK6812Spec` (fw 2.9), a custom timing, never FastLED's
  `SK6812` or `WS2812B`.** FastLED 3.10.5 on AVR rounds its ns table to
  cycles: WS2812B gives a '1' 875 high / 375 low, SK6812 937 / 312. Both are
  under the SK6812's 450 ns low minimum (fw 2.7 made it worse, reading the
  unused legacy AVR table). `TimingSK6812Spec` 375/375/500 gives '0' 375/875,
  '1' 750/500, all in spec. Read the library, don't trust its comments.
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
  host → violet sparkle over the **whole pit**, centre included (fw 2.10,
  EEPROM magic 0xC0). The app takes over through it fine (HELLO resends,
  delivery). **No manual OFF position**, on purpose.
- **The animated-look glitch is the data line, not the code (video,
  2026-09-25, `IMG_5912.MOV`).** A glitch is one frame of white/green/red
  bands or an orange tint (bytes landing shifted: red in the green or W
  slot), clean again on the next frame, often starting partway down a run,
  and the two long strips break differently in the same instant. Roughly 1
  frame in 25. The firmware sends a fixed, cycle-counted waveform with
  interrupts off; it can't produce that. Fix it in the wiring (buffer each
  Y-split branch, e.g. 74AHCT125, data ground run with the data, 330 Ω at
  the source). Software can only trade smoothness for fewer frames: 30 fps
  default (fw 2.14; 15 read as choppy), live `OP_SET_FPS` 0x19, and
  `tools/led_diag.py --fps-test` steps 30/15/10/5. Animation speed follows
  the clock, so the rate never changes the speed.
- **Every animation runs on the whole pit, centre included (fw 2.12).** fw
  2.8 held the centre on steady white during animations to hide a flicker
  blamed on the amp's supply; fw 2.9 found the real causes, and a centre that
  never animates can't show its flicker to be fixed. The queue alert's steady
  white centre is its design, not that rule. Measured 2026-09-25: breathe,
  chase, wipe, sparkle, rainbow all hold 30.3 writes/s with or without
  10 commands/s of traffic. `tools/led_cap_sweep.py --only C S` holds a live
  power limit (OP_SET_CAP 0x18, RAM only) for tests.
- **Centre power budget (fw 2.7, `capCentrePower()`).** Violet at 180 drew
  ~1.7x the white work light, and with the audio amp's bass on the same supply
  the centre run (longest, fed from one end) flickered — it went away on
  white or with the music off. Each centre frame's channel sum is held to
  the white look's (`CENTER_POWER_BUDGET` = 93 x 160) and scaled down, colour
  kept, when over. White is never touched; solid violet 180 runs at ~59%.
  **Sides are not regulated yet**, so an over-budget look shows a dimmer
  centre than sides. STATUS format 2 reports the last scale and frames capped.
- **Redraw only when the picture changes (fw 2.7).** PING/HELLO/SAVE don't set
  `dirty`; a static frame is refreshed every 10 s (`REFRESH_MS`) to heal a
  glitch. Animations run at 30 fps (`ANIM_FRAME_MS` 33, `ANIM_STEP` 2 keeps the
  visible speed) — every strip write leaves the UART deaf ~8 ms, and at 60 fps
  that lost about half of all frames.
- **SRAM:** 169 px ≈ 1560 B of 2 KB; ~250 px is the ceiling, then use an ESP32.
- **Move `EEPROM_MAGIC` on any `State` change or new default** (now `0xC0`).
- **Clear `dirty` before `showAll()`, never after** (fw 2.6). `showAll()`
  reads serial between the centre and sides writes; a command handled there
  sets `dirty`, and clearing afterwards dropped it until the next redraw.
- **Flicker: `uv run tools/led_diag.py --flash-test`.** Confirms the white look
  landed (resending until STATUS agrees), then four 15 s stages, each changing
  one thing: no redraws at all (heartbeat paused via `SerialLink.heartbeat`),
  one redraw/s, ~10 commands/s, an alert. The stage it flickers in names the
  cause. On fw 2.8 it flickered in stage 3 (identical redraws). On fw 2.9
  (2026-09-25) no stage flickered: stages 1–3 at 0.1 redraws/s, the alert at
  2/s with a 5 ms write (sides only; the centre is never re-sent).
- Alerts are overlays (`leds.start_alert/clear_alert`); intent keeps updating
  underneath. **Every animation runs on the controller, the alert flash too
  (fw 2.11):** the app sends the colours once, then SET_MODE ALERT (sides
  flash ~264 ms on/off, centre steady with its W) and SOLID for the steady
  part. Never drive a visible animation with timed commands from the host:
  a lost or resent command shows as a stutter.
- **Animated looks draw only on their 30 fps tick (fw 2.11)**; a command
  waits at most one frame. An immediate extra frame broke the rhythm and
  double-faded chase/sparkle. A mode change restarts the animation.
- **Known bug:** `SET_PIXELS` is overwritten by SOLID's next render; nothing
  sends it.

**Delivery, not just sending (`app/leds/link.py`).** Output commands
(SET_MODE/COLOR/BRIGHT/PIXELS, SAVE, OFF) go one at a time, in order, and are
resent every 12–26 ms (jittered, so retries don't lock step with the frame
period) until ACKed; a newer command for the same setting replaces a waiting
one (`_key`/`_supersedes`: latest-wins is only safe for whole-value
registers). PING/STATUS are sent once. Reads are non-blocking and the loop
sleeps on an event `send()` sets. **The service self-heals** (`_check_in_sync`):
when STATUS disagrees with the intent (and no alert/override/switch owns the
strips) it replays the look, at most every 5 s — which also catches a
controller reboot. Anything that drives the link directly (a test) must
disable that, or it gets corrected back within ~2 s.

Measured on the real controller, 2026-09-24: before — ~60% of commands lost
on the animated default, the pit stuck sparkling, round trip ~54 ms, queued
~23 ms. After (host + fw 2.7) — 0 given up, delivered median ~5 ms (p95 ~6 ms
static), queued ~0.2 ms, 0 redraws/s while static.

**Link telemetry, two halves (Telemetry → LED controller).**
- **Host side, any firmware** (`LinkStats`, `app/leds/link.py`): round trip
  per command (ACK matched by sequence number; p50/p95/max over 200), **time
  spent in the host queue before the write**, commands/s, NAKs, unanswered
  (>2 s), queue sheds, heartbeat "last heard", reconnects, drops.
- **Controller side, fw 2.5+** (`OP_STATUS` 0x17 → `STATUS_REPLY` 0x82, 39
  bytes, polled every 2 s; `parse_status()`): uptime (backwards = a reboot,
  counted), free RAM, live switch position, frames/redraws per second, strip
  write time, longest unread-UART gap, command-received → drawn, CRC / COBS /
  overrun / unknown-op counts, watchdog fallbacks. **Timed on Timer1 (4 µs
  ticks), never `millis()`/`micros()`**, because those stop while a strip write
  holds interrupts off, which is exactly what's being measured. Maxima reset
  per reply; the host keeps the session worst. STATUS does not set `dirty`,
  so measuring doesn't cost a redraw.
- **Measured on the real controller (2026-09-24, fw 2.5, `tools/led_diag.py`).**
  The no-host violet sparkle animates at ~66 redraws/s with a **7.9 ms strip
  write** each, so the UART is deaf about half the time: ~60% of commands
  never ACKed, broken frames climbing, and the app's resting-look push lost,
  so **the pit keeps sparkling while the app believes it's white** (Telemetry
  flags this drift from `STATUS` mode/brightness). The host never resends. The
  handshake now resends HELLO every 150 ms (a single HELLO was answered
  ~50%; five, 5/5). **Round trip ~54 ms is host-side:** pyserial's `read(256)`
  blocks for the whole 50 ms timeout unless 256 bytes arrive.
- **Measured finding (2026-09-23, simulated port):** `_pump` blocks up to 50 ms
  in `read()` plus a 5 ms sleep, so a command waits **median 23 ms / p95 59 ms**
  in the host queue against a 1.3 ms round trip. A 2 ms read wait cut that to
  2.5 / 9 ms. Not changed yet: the operator is working on latency.
- PINGs no longer set `dirty` (fw 2.7), and since fw 2.9 an unchanged frame
  isn't written at all, so a static look makes no redraws between the 10 s
  refreshes. STATUS `shows` counts real writes.

Tools: `firmware/pit_probe`, `tools/led_probe.py`, `tools/led_color_check.py`.

## Battery cart (`app/batteries/`, `battery_panel.py`), a mock-up

Live charge state of the batteries on the charging cart, from each one's
Playing With Fusion **BFG** over CAN: BFGs → USB-CAN adapter → this machine.
**Nothing touches the robot.** Control → Pit Systems → Batteries.

- **`bfg.py` follows PWF's published spec** (BFG FRC User Manual rev
  2026-02-27, pp. 22-28): 29-bit ids `0x0A0B<api><dev>`, heartbeat
  `0x1F0B01<dev>`, little-endian. Current is **+ for discharge**; the panel
  shows charging as "A in". Known manual ambiguity: Battery Health's
  capacity/cycles bytes overlap at byte 6 (read as 4-5 / 6-7; verify on a
  real BFG). Device ID ships as **0**: two new BFGs collide until renumbered
  (`Battery.clash`, from their serials).
- **The only command sent is Identify.** Never unlock, calibrate, rename or
  reset statistics from here; that's PWF's tooling.
- **Transport is unverified on Windows.** `python-can` (not a dependency
  yet): a CANivore is SocketCAN on Linux (CTRE's `canivore-usb`), but CTRE
  documents no raw-frame access on Windows; a generic USB-CAN adapter
  (`gs_usb`, `slcan`) works there. Proven only against python-can's virtual
  bus. `PIT_BATTERY_CAN=<interface>:<channel>`.
- `PIT_BATTERIES_FAKE=1` or the panel's button: a simulated cart (30×
  speed) that emits real BFG frames through the same decoder. Every line it
  touches says "simulated". No source → no timer.
- Next battery is PWF's rule: most charge remaining, then most effective
  capacity. The bar is the BFG's own charge indicator (solid = remaining,
  outline = effective capacity, both against design capacity).

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

`.hoot` (owlet → **`.mcap`** in scratch space → `mcap.py` + `flatbuf.py`)
and `.wpilog` are each a `_Source` yielding `(t_ms, device_type, can_id, signal, num, label)`;
storage can't tell them apart. **Add a format by writing a `_Source`.**
**There is no text stage and no `.txt` input**, by decision: logs go from
`.hoot`/`.wpilog` straight to the database. Don't add one back. Schema,
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
- **Never read a hoot through owlet's `.wpilog`.** owlet 26.3.0's wpilog writer
  cut the end off 11 of 15 conversions of the same hoot while printing 100%
  and exiting 0, and wrote some samples twice (2026-10-02; ruled out: reading
  early, path reuse, the file, the licence check, Apple Silicon). Its `.mcap`
  is byte-identical every run and complete. `mcap.py` reads it (LZ4 chunks:
  `lz4`, named in the spec, guarded by the self-check's `owlet` line) and
  `flatbuf.py` decodes CTRE's FlatBuffers by the schema inside the file.
  owlet's mcap repeats every signal in every frame: a sample is new only when
  its own `timestampSec` moved. Rows match the wpilog path's identities, so
  CAN names carry over; `import_check` pins the result.
- **Fun-fact slides: every number is real**, and jokes are at our own expense.
- CAN naming is data entry (not gated); deleting a session is gated.
- **Import a folder** (`batch.py`, Brayden 2026-10-03: five minutes with the
  robot): every `.hoot`/`.wpilog` in every subfolder is **copied onto the
  machine first** (`robot_logs/`, `.part` then rename, an identical copy
  reused), then logs imported before (same name + size, any path or pit) are
  staged in a pop-up (skip / re-import; replacing needs the admin lock and a
  session imported here), then the rest import newest first. Skips `._` twins,
  hidden folders, empty files. `tools/batch_check.py`.

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

## Data-source credits (`app/attribution.py`), a condition of use

Nexus: "a link back to frc.nexus". The Blue Alliance: "Powered by The Blue
Alliance" with a link to thebluealliance.com, and **never TBA's name or lamp
logo in our name or brand identity** (text only, and we say we're not
official). Both sit, as links, in Control → Software Updates → Data sources
and the README. **Any surface showing a source's data carries its credit**:
Nexus on the Event Feed panel and the Next Match board (native footer and the
pit-network ledger, enforced by `webcast_check`); TBA on the Analysis panel,
and on any future surface showing `tba_*` rows (the Quality Awards when they
get a screen). Links are `helpers.LinkLabel`: the surface's ink, underlined,
never blue or red.

## Robot-log analysis (`app/ai/`)

A local model reads a session through **ten read-only tools** (the home MCP
server's nine plus `match_context`) and a board comes out: analyst (tool calls → findings, `insight.schema.json`) →
check → designer (board, `board.schema.json`) → check → publish into
`analysis_board`. The same `pipeline.analyse()` runs at home with a different
toolbox (its MCP) and sink (`analysis.run`, `sync.push_queue`). No Qt in
`app/ai/`, so home imports it from a checkout.

- **The tools are the home MCP server's contract**: names, arguments, result
  keys, `args` echoed, floats rounded to 4. Figures chosen as
  `diagnostics.py` chooses them. No free-form SQL tool, ever.
- **Models choose and word; `checks.py` decides.** A metric must equal a
  tool result exactly; prose may display-round; `fault` needs a latched
  fault cited; one red region. A failure goes back to the model (twice), then
  the run is rejected and logged (`analysis_run`). Never "fix" a figure.
- **Charts: the designer picks the kind and source, code fetches the data**
  (`line` = `series_1s`, `bars` = `series_stats`, `sessions` =
  `compare_sessions`), only from `chart_options()`: the signals the
  findings stand on. A model never writes a chart's data or a card's shape.
- **Code sets every colour**: a card names its finding, `assign_status()`
  gives it that finding's severity and the headline the worst; one card per
  finding. The designer only picks and words.
- **In the app** (`service.py`, `init_analysis()` after sync): a worker
  thread on its own connection (`tools.using(ThreadDB())`), one run at a
  time, on each new import (`auto_run`) or the panel's button. The model is
  unloaded `keep_alive` (1 min) after a run: **16 GB pit machines are the
  constraint** (qwen3:8b is 6.7 GB loaded; the 30B took ~20 GB and swapped
  a 24 GB Mac). Preferences in `ai.json`. `PIT_AI_QUIET=1` in `--self-check`.
- **A published board flashes the strips purple** (sides violet, centre
  white, 2 s + 1 s). Judges and lunch suppress it like every alert.
- **The crew's ratings are the reward signal** (`feedback.py`, Control →
  Analysis): per finding useful / not useful / wrong / acted on, a 1–5 rank
  per run. Never reward finding problems: a trusted all-clear is useful.
- **Runs, ratings and a pit's own boards sync** (`analysis_run`,
  `analysis_feedback`, `analysis_board`), so rating from a Mac anywhere
  counts everywhere. A transcript is packed (`Spec.packed`, zstd) under the
  hub's 256 KB row cap. Pit board uids are `<session>:<machine_id>-<run>`,
  never `<session>:<run>` (home's).
- **The engine is bundled** (`runtime.py`): llama.cpp's `llama-server`
  (Windows Vulkan build in `llama/`, `tools/fetch_llama.py`, pinned tag and
  SHA-256), started for a run on a free local port and stopped `keep_alive`
  after it. **The model is never in the build**: the panel downloads
  `Qwen3-8B-Q4_K_M.gguf` once into `models/` in the data tree, resumable,
  verified against its pinned SHA-256 (`PIT_AI_MODELS` overrides the folder).
  `-np 1 -cram 256 -fa on -ctk/-ctv q8_0` took it from 9.0 to **6.5 GB**;
  keep them. `engine: auto` falls back to Ollama (`ollama.py`); both clients
  return the same `Reply`, and a tool result carries its call's `id`.
- **The scoreboard** (`feedback.scoreboard()`, top of the panel): per model
  and `PROMPT_VERSION`, share of rated findings useful / wrong, acted on, mean
  rank. **Bump `PROMPT_VERSION`** when a prompt, check or tool result changes
  what a run can say.
- **The Analysis face** (`analysis_overlay.py`, content `"analysis"`, pinned
  only): the newest `analysis_board`, converted by `boards.to_dashboard()` and
  painted by the diagnostics painter, plus a charts band (line across the
  match, bars across devices; never red). The webcast serves it through
  `state.analysis_board_state()`.
- **`--mcp`** (`mcp_server.py`): MCP over stdio, written by hand (no SDK in
  the bundle), the ten tools plus `analysis_runs` / `analysis_scoreboard`,
  read-only, from this machine's synced copy. Proven against the official
  client (mcp 2.2.0). The windowed exe has no `sys.stdin`; `_stdio()` opens
  the host's pipe handles.
- **TBA data** (`tba_event`, `tba_match`, `_v13`): home-produced, pulled only;
  `match_context` names the match a log was (tagged, else nearest start time
  within 15 min). Home pushes them (`home/REQUESTS.md` R5).
- **What trials taught the checker** (keep the tests in `ai_check`): a count
  from `faults` must be the count of the fault (else device) the finding
  names; `fault` severity follows the named fault's own status; a card's value
  is only a figure; an evidence value may come from any of the finding's
  cited results; a retry says where a figure really is (two retries).
- **Not built:** syncing the scoreboard's prompt text (only its version),
  multi-session (match-to-match) runs, `breakdown_json` from TBA.

## Team sync (`app/db/sync/`, `sync-hub/`, `home/`)

Pit machines ⇄ **the hub at `sync.bh-stack.com`** (Worker + one Durable
Object + R2) ⇄ the **home SQL Server, the master**. DATABASE.md "Sync" is
the contract (what syncs, the bookkeeping tables, the rules); the home side
is not in this repo and is built from `home/HANDOFF.md`.

- **Triggers record every edit** (`_v10_sync`), so no writer can forget.
  The engine writes pulled changes with `sync_guard.applying = 1` from its own
  connection; nothing else may raise the guard.
- **Identity is `uid`, never an integer id.** Name-derived where the name is
  the identity, so stock rows seeded on two machines are one row.
  References travel as the parent's uid.
- **The hub decides conflicts** (row-level, on its own seq); only home may
  `force`. A pit never trusts its own clock.
- **Machine identity lives in `sync.json`, never the database** (a copied DB
  would clone it). The Telemetry panel's admin block renames, switches, takes
  the token and changes the id (`set_machine_id()` only, behind a warning).
- **Temp files come from `codec.temp_path()`, never a bare `mkstemp()`**: its
  open handle locks the file on Windows, and synced CAD models couldn't land.
- **R2 is a relay, not a store** (`home/REQUESTS.md` R8, DATABASE.md Sync
  rules 8–12). Team-file bytes (CAD, slides, every scanned song) move only in
  the night window (`transfer.py`, enforced by default; a file row goes with
  its bytes); a pit asks home (`blob_request`) for evicted bytes; a nightly
  `machine_manifest` gets home's `sync_verdict` back. Songs are deleted only
  as an admin's mark; home approves the real delete. **A new pulled table goes
  in `tables.APPLIED`**, and the catch-up fetches it after an upgrade.
- Logs travel as **bundles** (names, not ids; enum codes remapped on import;
  samples as columns on each series' exact quantum, zstd'd: 3.85 GB → 2.98 MB,
  bit-exact). Everything sent is zstd'd (`codec.py`). The original log goes
  only with `upload_raw` (off by default; the bundle is audited lossless).
- Off with no `secrets/sync_token`; `PIT_SYNC_QUIET=1` (set by
  `--self-check`) opens nothing. The self-check's `sync` line fails if a
  synced table lost a trigger.
- **Analysis boards** (`analysis_board`, contract
  `home/contracts/board.schema.json`) mirror `diagnostics.Dashboard`; the
  Analysis face paints them with the diagnostics painter (`app/ai/`).

## Self-update (`app/update/`)

Tag → CI → private Release → pit machines. Operator side is in DEPLOYMENT.md.
Windows-only CI (macOS bills 10×).

- **Versioned folders behind a directory junction** (`current\`). Never
  overwrite a running exe; never `shutil.rmtree` a junction (it deletes the
  target), use `os.rmdir`. The installer does the same in Pascal.
- **The staged build self-checks** with `PIT_DISPLAY_DATA`, `PIT_CAD_PORT` and
  `PIT_LEDS_FAKE` set, so it can't touch the live DB, port or serial.
- **Start another build only with `install.child_env()`**: no `_PYI_*`, plus
  `PYINSTALLER_RESET_ENVIRONMENT=1`. Inherited, they make the new build run
  the parent's files (beta.4's updater "checked" beta.5 against beta.4 and
  failed with a bare exit 1). The `paths` line fails if it ever happens. A
  windowed child prints nowhere, so `verify()` reads the scratch
  `selfcheck.log` / `crash.log` and keeps `update_check.log`.
- Prune at startup, not at swap. An install not of this shape is left alone.
- **Checking is automatic; downloading never is.**
- Private repo: fetch assets by id; **drop `Authorization` on the redirect to
  object storage** (`_Redirect`). A release without `manifest.json` is skipped.
- Preferences are JSON, not DB (reachable when the DB is broken).
- `installer.iss`: `[Code]` comments are `//`, never `{ }` (a brace comment
  ends at `{app}`), and the file is pure ASCII. `tools/check_installer.py`
  enforces both.

## Empty stubs

`app/db/repositories/` is a placeholder.
