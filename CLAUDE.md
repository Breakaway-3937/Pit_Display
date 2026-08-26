# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@BREAKAWAY_BRAND.md

## Running the app

```bash
uv run main.py
# or via the installed entry point:
uv run pit-display
```

There are no tests and no linter configured. The only runtime check is launching the app.

## Database

**[`DATABASE.md`](DATABASE.md) is the authoritative reference** — every table,
every column, every query, and the invariants that will bite you. Read it before
touching storage, and update it whenever you add a table, a migration, or a
query. It is the contract between the schema and everything that reads it.

The short version: two SQLite files. `data/pit_display.db` holds settings,
presets, the music library and all robot-log metadata; `data/pit_display_samples.db`
is ATTACHed as `samples` and holds only bulk telemetry. The second is disposable
by design and its schema is deliberately unversioned.

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

Nine module-level singletons share the `LazyProxy` helper in `app/lazy_proxy.py` — safe to import at module level, but raise if accessed before their `init_*()` function is called in `main()`:

| Import | Init call | Purpose |
|---|---|---|
| `from app.config import config` | `init_config()` | Active team, display mode, per-screen theme settings; emits Qt signals on change |
| `from app.rotation import rotation` | `init_rotation()` | 45-second timer that emits `advance` signal in standard mode |
| `from app.judges_slides import judges_slides` | `init_judges_slides()` | Loads images from `assets/judges_slides/`, tracks current slide index |
| `from app.cad_assets import cad_assets` | `init_cad_assets()` | Local HTTP server (port 8765) + subsystems config + CAD focus signals |
| `from app.db import db` | `init_db()` | SQLite connection + versioned migration runner (`data/pit_display.db`) |
| `from app.checklist import checklist` | `init_checklist()` | Pit checklists — lists, items, ticks (see [`DATABASE.md`](DATABASE.md)) |
| `from app.leds import leds` | `init_leds()` | USB-serial link to the LED controller + strip state |
| `from app.music import music` | `init_music()` | Playback, queue, local library, 10-band EQ |
| `from app.admin import admin` | `init_admin()` | Admin lock gating the LED/EQ controls |

`init_config()` must be called first; the others depend on `config` being ready.
`init_leds()` must come after `init_cad_assets()` (it subscribes to
`subsystem_focused`); `init_music()` and `init_admin()` after `init_db()` (they
seed EQ presets and the admin credential). `init_checklist()` also needs the DB,
and must come **before the presentation screens are constructed** — they read
their list while building.
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
- `config.set(screen, "slide_index", n)` → that presentation screen jumps to slide n;
  the screen writes the same key back as the rotation advances, so the control
  screen's picker stays in sync
- `admin.lock_state_changed` → `LEDPanel` / `MusicPanel` show or hide their gated blocks
- `config.set(screen, "content", "checklist"|"rotation")` → that presentation
  screen swaps between the slide rotation and the checklist
- `checklist.items_changed` / `item_toggled` → `ChecklistOverlay` rebuilds rows or
  repaints the one that changed; the control-screen editor mirrors it

### Touch input (`app/touch.py`)

Two panels in the pit take fingers at the same time — the operator's
**control screen** and the visitor-facing **project screen** — so simultaneous
touches on different screens are routine, not an edge case.

**Qt's default handling cannot survive that.** A plain QWidget never sees a
touch: unless it sets `WA_AcceptTouchEvents`, Qt *synthesizes* mouse events
from the touch stream, and that synthesis runs through the single
application-wide mouse state — one pressed button, one implicit grab, one
release. Press on control, press on project, lift one finger: the other widget
stays latched. A button drawn pressed forever, a slider still tracking a finger
that left the glass.

So `main.py` calls `touch.install(app, control, project)`, which flags exactly
those two top-levels and routes their touch per point. **Every point gets its
own target widget and its own press/move/release, keyed by `(device, point
id)`** — two panels are two devices, two fingers on one panel are two ids —
so no point can steal another's state.

Three things about that are load bearing:

- **Only the top-level windows get `WA_AcceptTouchEvents`, never their
  children.** `QWebEngineView`'s internal render widget sets the attribute on
  itself, so Qt targets *it* and the router never sees those points — Chromium
  gets the raw multi-touch stream for the CAD viewer's pinch and two-finger
  pan. Flagging children re-targets the touch at an ancestor and takes those
  gestures away from the web view.
- **Presentation screens and dialogs are deliberately not registered**, so they
  keep Qt's synthesis. Single touch is all an audience screen or a modal needs.
- **Mouse events arrive accepted; `QWidget`'s default handler calls
  `ignore()`.** The router follows that convention when it propagates a press
  up the parent chain. Clearing the flag first instead would deliver the same
  press to a widget *and* every ancestor above it.

**A drag over a scrollable area is a scroll, not a tap.** Past `_DRAG_SLOP`
the router unlatches the widget the finger landed on with a release *outside*
its rect (Qt buttons only emit `clicked` on a release inside) and drags the
scroll area instead. Sliders are exempt — they own their own drags, and a
scrollbar is itself a child of the area it drives, so `_scrollable_ancestor`
checks for a slider first.

**Anything that activates on `mousePressEvent` breaks under this.** The press
has already fired by the time the router decides the gesture was a scroll, so
activate on release-inside instead — `_Thumbnail` and `_StandardSlideRow` in
the control screen were converted for exactly this reason.

**A touchscreen sends no `leaveEvent`**, so anything that paints itself on
hover has to undo that itself. Synthesized events carry the real touch device,
so `touch.is_touch(event)` tells a tap from a click; `_CardButton` uses it to
drop its accent border, which would otherwise stay on every card ever tapped.

Web side, in `assets/cad_viewer/`: the canvas sets **`touch-action: none`** —
without it the browser claims two-finger gestures for page pan/zoom and fires
`pointercancel` mid-pinch — the viewport meta pins page zoom, and
`onCanvasTouch` ignores a `touchend` that ended a pinch (`multiTouch`) while
`onCanvasClick` ignores the compatibility click that follows every tap.

### Qt layout traps hit in this codebase

Three real bugs, all from the same root cause — **`addWidget(w, alignment=…)`
lays the widget out at its `sizeHint()`**, ignoring `heightForWidth` and any
expanding policy:

- A word-wrapped `QLabel` gets a one-line height, so long body text is clipped.
- A custom `QWidget` with no `sizeHint()` gets zero width. `Trace` had this —
  it silently never painted on any presentation slide.

Centre with a stretch row (`addStretch / addWidget / addStretch`) instead, and
give custom painted widgets a real `sizeHint()`. Related: `layout.setAlignment(
AlignCenter)` on a `QVBoxLayout` collapses it to minimum size, so wrapped labels
wrap at their narrowest — use stretches for vertical centring too.

Also: **`helpers.label()` does not word-wrap.** A long unwrapped label forces its
whole panel wider than the window and pushes table columns off-screen. Call
`setWordWrap(True)` on any prose.

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

### Standard slide picker

**Control Screen → Presentation A/B → Standard Slides** lists every slide in
that screen's rotation (authored + fun facts), highlights the live one, and
jumps on click. Prev / Next / First / Reload underneath.

Jumps travel through `config.set(screen_id, "slide_index", n)` — the control
screen never holds a reference to a presentation window, same as every other
cross-window command here.

**The feedback loop is closed by two idempotence guards**, and both are load
bearing: `config.set()` ignores an unchanged value, and `SlidePanel.set_slide()`
ignores a re-select. The round trip (picker → config → screen → config) settles
on the first pass. Verified: one click produces exactly one config write. If you
add another writer of this key, keep that property.

`PresentationScreen.rotation_slides()` is a **classmethod** so the picker can
list slides without a live window.

### Checklists (`app/checklist.py`)

The pit checklist as seen on an **overhead screen**, written and ticked from the
control panel. Storage, columns and the query traps are in
[`DATABASE.md`](DATABASE.md); this covers the code layout.

| File | Role |
|---|---|
| `app/checklist.py` | `_ChecklistService` singleton — lists, items, ordering, ticks |
| `app/widgets/checklist_overlay.py` | What the overhead screen shows |
| `app/widgets/checklist_panel.py` | The operator's editor, in the screen's settings |

**It is a per-screen content setting, not a fourth display mode.** Standard mode
has four faces, chosen by `config.get(screen, "content")` — `"rotation"` (the
default), `"checklist"`, `"diagnostics"` or `"robot_info"`. Per-screen because the useful arrangement in a pit is
one overhead screen holding the checklist while the other keeps rotating for
visitors, which a global mode cannot express. Judges and lunch still take over
both screens; those are whole-pit states. A screen also carries a
`checklist_id`, so the two can show different lists.

**The rotation timer is suppressed behind the checklist.** `_on_rotation_advance`
checks `_standard_page()` — without it the 45-second timer walks the hidden
slide panel forward while the crew works, and flipping back lands somewhere
random.

**Ticking happens on the control screen, never on the display.** The overhead
screens are audience-facing and out of reach — nobody walks over to a monitor
above the workbench to tap an item. Every tick travels through the singleton's
signals like every other cross-window command here.

**Done is green, not the accent.** `STATUS_ONLINE`, because on Breakaway the
team accent *is* red, and a red tick at ten feet reads "fault", not "finished".
That also keeps the brand's one-focal-red budget: red is the header and the
progress bar, green is status.

**Everything is sized from the live widget height** (`_apply_scale`), including
an explicit `setFixedHeight` per row — without that, six items huddle at the top
of a 55" panel with half the screen empty. A long list stops growing at the
floor and runs off the bottom: the fix is fewer items, not smaller type.

**Only deletion is admin-gated.** Writing and ticking items is data entry, like
naming a CAN id, and the crew needs it mid-match-cycle. Deleting a whole list
destroys every item on it, which is the same line this app draws for deleting an
imported log session.

**Content is the team's to write.** `_v8` seeds one empty list; the overlay's
empty state points at the control panel rather than showing an empty box to a
pit full of visitors. Do not pre-fill it with invented items.

### Robot diagnostics boards (`app/widgets/diagnostics_overlay.py`, `robot_info_overlay.py`)

Two live overhead boards built from the imported logs. **`fun_facts.py` talks to
visitors; these talk to the crew** — every tile is something you would act on in
the six minutes before the next match.

| File | Role |
|---|---|
| `app/robot/diagnostics.py` | Every query. No Qt, no widgets |
| `app/widgets/diagnostics_overlay.py` | Screen A's board — *is anything wrong?* |
| `app/widgets/robot_info_overlay.py` | Screen B's board — *what, on which motor?* |

**Split by question, not by column count.** A is glanceable from ten feet: a
headline that takes the worst status on the board, a grid of vitals, a subsystem
strip. B is the board you walk up to when A has gone amber: the fault list by
name, the per-motor table with CAN ids, and which log it all came from. Both
widgets exist on both screens; `BOARD_CONTENT` on each subclass says which one
joins that screen's rotation, and either can still be pinned on either screen.

**Three ways to get a board on screen**, all through the same widget:

1. **In the rotation.** It is the last stop in the cycle, at index *n* past the
   last slide, so visitors see it come round. It only joins when a log has been
   imported — an empty "no log" board every 45 seconds is worse than no board.
2. **Pinned.** `content = "diagnostics"` / `"robot_info"` parks it and the
   45-second timer stops touching that screen. Control → (screen) →
   Standard Content.
3. **Jumped to.** The control screen's slide picker lists it as the last entry,
   tagged `LIVE BOARD`, and clicking it writes the same `slide_index` key as any
   other slide.

`_BOARD_INDEX` is what makes the rotation and the picker agree. `rotation_entries()`
is the single source of that list — the picker and the presentation screen both
call it, and if they ever disagree a click lands on the wrong slide.

**Status is the robot's own judgment, not a threshold invented here.** See
[`DATABASE.md`](DATABASE.md) for the reasoning and the three published figures
that *are* used. **Red on these boards means a latched fault and nothing else** —
when the robot is clean there is no red on the board at all, which is where the
brand's one-focal-red budget goes.

**Neither board computes anything.** If a figure is wrong, it is wrong in
`diagnostics.py`.

**Layout is banded, not measured.** An earlier version measured where a list had
landed and hid whatever fell past the fold; it read geometry that had not
settled and hid rows there was room for — silently, which is the worst way for a
diagnostics screen to be wrong. Row counts now come from `_budget(height)` and
column counts from the width, both checked against a render at each panel size.
Lists are sorted worst-first upstream, so what a cap drops is always the healthy
end.

**They re-read on `config.logs_changed`**, emitted by the robot panel after an
import or a delete. That signal is also what finally wires `reload_slides()`,
which existed and was documented but had never been called — so importing a log
now regenerates the fun-fact slides too, with no power-cycle.

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
| `firmware/pit_leds/pit_leds.ino` | The controller. Pins, per-unit pixel counts, strip placement and the switch pins are all in the config block at the top |

**Three strips, one axis.** The pit is three units — LEFT, CENTRE, RIGHT — on
three pins, driven from one `leds[]` array. Every animation is a function of a
pixel's **distance from the true centre of the pit**, not from its own strip's
pixel 0, so a breathe blooms outward from the middle and reaches both far ends
together. Each strip declares `origin` (where its pixel 0 sits, in pixel-widths
from true centre) and `dir` (+1/-1) in the `strips[]` table; gaps between units,
unequal lengths and a backwards-wired strip are all just numbers there. Positions
are held in **half-pixel units** internally — an even-length strip has no pixel on
its own midpoint, and whole-pixel maths puts every "symmetric" effect half a pixel
off on one side.

`SET_COLOR`'s segment byte is now live: `0`/`1`/`2` are LEFT/CENTRE/RIGHT, `0xFF`
all three. `INFO` reports three segments; the app already parses that and needs no
change (it still sends `0xFF` everywhere).

**Manual three-way switch.** An SPDT on-off-on wired to two pull-up inputs
overrides the host: one throw forces all strips to Breakaway red, centre blanks
them, the other throw hands control back to the app. Serial keeps being read and
ACKed in **every** position, and commands still land in `state` — so flicking back
to host resumes on what the app has been asking for, with no round trip. The truth
table lives only in `switchPosition()`.

**SRAM ceiling:** the ATmega328P has 2KB and FastLED uses 3 bytes/pixel. The
number that matters is the **total across all three strips**: 150px = 450B (fine),
300px = 900B (the practical limit), 500px = 1500B (too tight — move to an ESP32).
If you change the counts, nothing in the app needs editing: `HELLO` reports the
geometry and the app adapts.

**`State` changed shape** when colour went per-strip, so `EEPROM_MAGIC` moved
`0xB9` → `0xBA`. Move it again on any further change: an old saved struct read
into a new layout garbles every field after it rather than failing.

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

Credential: PBKDF2-HMAC-SHA256, one row in `admin_credential` — see
[`DATABASE.md`](DATABASE.md). Default password is `Password`; the bar nags until it is changed.
**It is a UI lock, not a security boundary** — the DB is a local file. The
docstring in `app/admin.py` says so at length; keep that framing honest.

**Testing gated widgets:** use `w.isVisibleTo(panel)`, **not** `w.isVisible()`.
The latter is False whenever any ancestor is hidden (e.g. a non-selected settings
panel), which tells you nothing about the gate.

### Media

No explicit-content filter — the team pre-curates the music folder. Do not
re-add per-track filtering; it was removed in migration `_v5_drop_explicit`
(see [`DATABASE.md`](DATABASE.md)).
Operator instructions for every media type: [`MEDIA_GUIDE.md`](MEDIA_GUIDE.md).

### Robot logs (`app/robot/`)

The data pipeline: a raw file off the robot becomes rows the pit screens can
query. **Schema, queries and the traps are in [`DATABASE.md`](DATABASE.md)** —
this section covers only the code layout.

```
robot.hoot   ──owlet -f wpilog──▶  .wpilog ──┐
robot.wpilog ───────────────────────────────┼──▶ (t_ms, device, can id, signal, value)
…_detailed.txt ──parser.parse_line──────────┘                    │
                                                                 ▼
                     device · signal · series · sample · session_constant · fault_event
```

| File | Role |
|---|---|
| `owlet.py` | Runs CTRE's extractor to turn a `.hoot` into a `.wpilog` |
| `datalog.py` | WPILib's `.wpilog` reader, vendored — see its docstring for the three changes |
| `wpilog.py` | `.wpilog` → records, and entry name → `(device, can id, signal)` |
| `parser.py` | Text-export line grammar, filename grammar, `classify()`. Pure, no I/O |
| `ingest.py` | The pipeline. One streaming pass; own connection, own thread |
| `repository.py` | Every query the screens use |
| `distance.py` | Wheel odometry |
| `fun_facts.py` | Silly-but-true slides for the standard rotation |
| `app/widgets/robot_panel.py` | Import button + the CAN-id name table |

**Three input formats, one storage path.** Each is a `_Source` in `ingest.py`
yielding `(t_ms, device_type, can_id, signal, num, label)`, and the storage code
below them cannot tell which produced a row. Add a fourth format by writing a
`_Source`, never by touching the loop.

- **`.hoot`** — what the controller writes, and the one to reach for. It is the
  only source carrying the controller serial. `.hoot` is a closed CTRE format,
  so the first stage shells out to `owlet` (`tools/owlet/`, see its README) to
  extract it to a `.wpilog` in a scratch directory that is deleted afterwards.
  The scratch copy is the size of the hoot, so it goes **beside the source
  file**, not in the system temp dir.
- **`.wpilog`** — the roboRIO's own DataLogManager file. This is where the
  team's application signals live (robot states, PDH currents, shooter
  setpoints); none of them exist in a hoot.
- **`.txt`** — a Phoenix "detailed" export somebody converted by hand. Kept
  working because a season of them exists.

**A hoot-derived wpilog lands on the same `device` rows as the text export.**
`/Phoenix6/TalonFX-2/MotorVoltage` resolves to `("TalonFX", 2, "MotorVoltage")`,
which is exactly what the text grammar reads from
`('TalonFX', '2', 'MotorVoltage')` — so the CAN-id names the crew already typed
still apply, whichever way the log came in. `wpilog.entry_identity()` scans the
path **right to left** for a `<Type>-<digits>` segment, because a bus or
namespace above the device can itself contain a hyphen and digits.

**Application signals go to one pseudo-device: `Robot` at CAN id −1.** They have
no CAN address, and the signal name is the whole path
(`RealOutputs/Shooter/Setpoint`). It is created already named, and
`repository.devices()` filters it out with `can_id >= 0` — the CAN map answers
"which motor is CAN 11", and a row claiming CAN −1 is a lie in it. Every other
query joins `device` normally and sees it.

**`classify()` is the Phoenix catalogue and only that.** A signal name
containing `/` is application data written by the team's own robot code, and
CTRE's naming says nothing about it — a robot-code `Faults` bitfield folded into
intervals would throw the value away. Those are always `telemetry`; a constant
one still reaches `session_constant`, because the importer decides that from the
data rather than the name.

**Not everything in a wpilog is storable.** `sample.v` is a REAL, so string
arrays, msgpack and raw bytes are dropped and counted in `ImportResult.skipped`;
a string signal past `MAX_ENUM_LABELS` distinct values is abandoned rather than
allowed to grow an unbounded dictionary table. Numeric arrays are *not* dropped
— a `double[]` pose becomes `Pose[0]`, `Pose[1]`, `Pose[2]`. The import panel
reports both, because a log that is a third unstorable is one somebody needs to
look at, and silence would read as a clean import.

**`MAX_ARRAY_WIDTH` is 32 because the PDH has 24 channels.**
`/PowerDistribution/ChannelCurrent` arrives as one 24-wide `double[]`, and
per-channel current is something the pit actually looks at. It was 16 first, and
that silently dropped channels 16–23.

**A wpilog compresses ~1.1×, a hoot ~19×, and both are right.** WPILib's DataLog
already writes only on change, so the importer's change-only pass has nothing
left to remove. Read the ratio against `source_kind`; on its own it looks like a
broken import.

**`owlet.describe()` names the OS, the CPU and the binary chosen**, and the
panel shows it. `_PATTERNS` maps `platform.system()` × `platform.machine()` to
an ordered list of filename globs, so a Windows-on-ARM machine falls back to the
x86-64 build under emulation and Linux picks by architecture. "No owlet for your
platform" is the one import failure an operator can neither diagnose nor fix
from the error text alone — and the pit machine is Windows while nothing this is
developed on is.

**The conversion scratch directory does not go next to the log.** Order is
`$PIT_LOG_SCRATCH`, then the system temp dir, then — only if temp lacks the
space — beside the source. Logs arrive on USB sticks, shared drives and
read-only export folders; a crash mid-import must not strand a multi-gigabyte
`.pit_owlet_*` directory in somebody's log archive.

**The duplicate check runs before the source is opened.** Opening a `.hoot`
means running owlet, and spending four minutes extracting a log only to be told
it was already imported is the kind of thing that happens in a six-minute pit
cycle.

Measured on the real 3.85 GB export: 62,118,775 raw rows → 3,263,543 stored in
75 s; 83 MB of telemetry, main DB still ~270 KB. Summary queries under 0.5 ms.

**Before writing any query over this data**, read the Invariants section of
[`DATABASE.md`](DATABASE.md). Four of them produce plausible wrong answers
rather than errors: the non-unique `(series_id, t_ms)` key, millisecond (not
microsecond) timestamps, change-only samples needing zero-order-hold
integration, and motor-shaft (not wheel) units on the drive motors.

**Fun-fact slides.** `fun_facts.slides()` turns the imported log into
(title, body) pairs that `PresentationScreen._rotation_slides()` appends to each
screen's authored `SLIDES`. Two rules: **every number is real** (nothing invented
or rounded for effect — a visitor who asks "is that true?" gets a yes), and the
jokes are at our own expense. Returns `[]` with no import, so the rotation just
omits them. Facts are read at construction; `reload_slides()` picks up a new
import without a restart.

**The CAN-id name map is the manual step.** `device` rows appear automatically
on import with `label` NULL; names are typed in **Control Screen → Pit Systems →
Robot Logs** and persist across imports. Naming is intentionally **not**
admin-gated (it is data entry); deleting a session is, because it destroys
samples.

### Empty stubs

`app/db/repositories/` and `app/db/sync/` are empty stubs for the future
on-prem SQL Server sync.
