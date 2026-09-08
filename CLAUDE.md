# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@breakaway_branding.md

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

## Packaging and deployment

**[`DEPLOYMENT.md`](DEPLOYMENT.md) is the reference** — what ships, what does
not, how to build for Windows, how a tag reaches the pit machines, and what the
data directory is. Three things belong here because they constrain how code is
written:

- **Nothing outside `app/paths.py` may build a path from `__file__`.** A frozen
  app has a read-only resource tree (inside the bundle; `Program Files` on
  Windows) and a writable data tree (per-user), and they are different
  directories. `paths.resource()`, `paths.data()` and `paths.find()` are the
  only correct ways to ask. From a checkout both are the repo, so the dev loop
  is unchanged.
- **`main.py --self-check` boots everything offscreen and reports**, exit 0/1.
  It exists because packaging failures are invisible: the app starts, and only
  the one screen that needed the missing file ever finds out. It has already
  caught PyInstaller dropping Chromium's helper process *and* its resource
  `.pak`s — both of which leave the CAD viewer dead and nothing else
  complaining. It is now also the **gate an update has to pass**: the staged
  build runs it on the pit machine before the launcher is pointed at it, so
  anything that breaks a bundle stops there rather than at an event.
  **Its output needs help to be readable on Windows**: the shipped app is
  `console=False`, and a GUI-subsystem executable is not attached to the
  console that launched it, so every `print()` here went nowhere at all.
  `_attach_console()` borrows the parent's console when there is one, and the
  report is written to `selfcheck.log` in the data directory either way.
- **The version is stamped at build time, never edited.** `app/version.py`
  carries a sentinel that `version.is_release()` refuses; CI rewrites it from
  the git tag. A file somebody has to remember to bump is a file that
  eventually lies about which build is on the pit laptop.

## Architecture

Four-window PyQt6 desktop app for FRC pit displays. **Only the control screen is
built at startup**; the other three are built on demand by the power toggles in
its sidebar and **destroyed when switched off** — the control screen holds
factories, not instances (`set_window_factories`). Off means off: no timers, no
signal subscriptions, and for the project screen no Chromium process. Two
consequences worth knowing:

- **A screen is rebuilt from scratch each time it is powered on**, so it re-reads
  slides, fun facts and boards. "Turn it off and on again" genuinely resets it.
- **Anything holding a window reference has to let go.** `touch.py`'s router
  connects to `destroyed` and forgets the widget; a stale entry there is a
  dangling pointer walked on every touch event.

**Window roles:**
- `ControlScreen` — operator panel (team selector, mode buttons, per-screen settings, judges slide picker)
- `PresentationScreenA` / `PresentationScreenB` — audience-facing rotating slides; switch to `LunchOverlay` or `JudgesOverlay` based on mode
- `ProjectScreen` — the pit-front touch panel: one interpretive surface with the CAD as its top band (`content="board"`), or the CAD alone (`content="cad"`)

**Three display modes** (set globally via `config.set_mode()`):
- `standard` — the painted `SlidePanel` with 45-second auto-advance driven by `RotationManager`
- `judges` — `JudgesOverlay` showing slides from `assets/judges_slides/`; controlled from the control screen thumbnail picker. The **artwork is full-bleed** — it is the team's own finished graphic and boxing it inside the plate would put two containers around one image — but the header band follows the chassis
- `lunch` — `LunchOverlay`, the holding card, on the chassis. One enormous auto-fitted line and nothing competing; the Trace is its one red

### Global singletons (lazy-proxy pattern)

Ten module-level singletons share the `LazyProxy` helper in `app/lazy_proxy.py` — safe to import at module level, but raise if accessed before their `init_*()` function is called in `main()`:

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
| `from app.update import update` | `init_update()` | Release feed, staged installs, channel and rollback |

`init_config()` must be called first; the others depend on `config` being ready.
`init_update()` depends on **neither** `config` nor the database — deliberately,
since the states worth updating out of are the ones where those are broken; it
is called early only because the control screen's panel reads it while building.
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

**A synthesized press does not move keyboard focus, and the router has to do
it by hand.** Qt focuses a widget on click inside its *native* mouse dispatch
(`QApplicationPrivate::giveFocusAccordingToFocusPolicy`), which an event built
and handed to `sendEvent()` never goes through — so a finger on a text field
blinked a caret that no keystroke ever reached. `_focus_on_press` does both
halves Qt would: activate the window (keystrokes go to the *active* window
first) and walk up from the tapped widget to the first one accepting
`ClickFocus`, before the press is delivered.

This was invisible while the control screen was the only window — whatever had
focus at startup still had it — and appeared the moment a second window existed
to hold the focus instead. **The symptom was "open any other screen and the
control panel stops accepting keyboard input."** Two more things belt-and-brace
it: `_on_power_toggled` hands activation back to the control screen after
`show()`, and the two presentation screens carry
`WindowDoesNotAcceptFocus` — they have no input widget on them, so they can
never take the keyboard in the first place. The **project** screen deliberately
does not, being a touch panel with a web view that wants ordinary focus.

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

### The Plate — the chassis (`app/widgets/chassis.py`)

**Every full-screen surface is built on one inset panel.** The slide rotation,
both robot boards and the pit-front panel all subclass `Chassis`, which paints
the ground, the plate, the header band and the footer ledger; subclasses fill in
`paint_stage()` and, where they want more than two mono strings, `paint_footer()`.
Two overhead panels are in the same glance all day — if they do not share a
chassis they read as two apps.

    ┌─ 40px inset ──────────────────────────────────┐
    │  BREAKAWAY 3937           SCREEN A / STANDARD │  header, top 56, h 94
    │ ───────────────────────────────────────────── │  2px rule at 150
    │  the stage                                    │  216 → height−168
    │ ───────────────────────────────────────────── │  2px rule
    │  01 / 07  ▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔     ROTATION A │  footer ledger
    └───────────────────────────────────────────────┘

Three things about it are load bearing:

- **Every number is a *design* measurement, converted by `s()`.** A pit panel
  is 1080 tall and the machine you are developing on is not, so a literal pixel
  count is always wrong somewhere.
- **The scale is a contain fit — `min(w/DESIGN_W, h/DESIGN_H)` — never one
  axis.** A single-axis rule is right until a window is the wrong shape: the
  pit-front panel is drawn at 1080×1920 and scaling it by width alone meant a
  window opened at 1920×1000 set every figure at **1.78×**, drawing a 1153px
  CAD stage into a 1000px window. That is not a clipped edge, it is a layout
  collapsing through itself. `DESIGN_W`/`DESIGN_H` also drive `sizeHint()`, so
  a portrait surface opens portrait — inheriting the landscape hint is what put
  that window at 1920 wide in the first place.
- **The background is cached** (`_ground`). The ambient light-fall drifts on a
  34s cycle, so the widget repaints on a timer; rebuilding the plate gradient
  and its cast shadow every tick would be pure waste.
- **The light theme is a derivative, not a token swap.** The same depth logic
  survives: the ambient wash becomes a cast shadow, rules go to N200, and the
  one red thing stays red.

`PlatePanel` is the container variant — same plate, but the inside is a Qt
layout instead of a `paint_stage()`. The pit-front panel needs it, because it
hosts a live `QWebEngineView` and a finger-dragged scroll area.

### Putting a screen on a monitor (`app/display.py`)

**A window that is not filling a monitor is not showing the design.** Every
audience surface is drawn at a fixed size and scaled by a contain fit against
it, so a half-size window is a proportionally smaller copy of the design rather
than the design. The power toggles used to call `show()` and nothing else, which
left every screen at a size hint on whichever display the window manager chose.

Two settings per screen, in `config`:

| key | meaning |
|---|---|
| `display` | monitor index; an unplugged one falls back to the primary rather than stranding the window off-canvas |
| `fullscreen` | fill that monitor, or float as a windowed 16:9 (portrait 3:4 for the pit-front panel) |

Powering a screen **off destroys the window**, so `place()` is called on a
freshly built one every time it comes back — there is no hidden window to
restore.

**`default_fullscreen()` is `len(screens) > 1`** — and that is load bearing. On
a single-display machine a full-screen audience window covers the control panel,
and the control panel is the only way to turn it off again. One monitor means
windowed, always.

`place()` moves the window onto the target monitor **before** going full-screen;
the other order makes Qt fill whichever monitor it was already on.

**Never call `showFullScreen()` on an audience screen.** Qt's version ends with
an unconditional `activateWindow()`, and those two windows carry
`WindowDoesNotAcceptFocus` on purpose (see the touch section), so every power-on
printed `requestActivate() called for QWidgetWindow(…) which has
Qt::WindowDoesNotAcceptFocus set.` Both halves are right — a panel must never
take the keyboard from the operator, and it must fill its monitor — so
`display._show_fullscreen()` does the window-state change and leaves the
activation out. `touch._focus_on_press` carries the same guard.

### Three painting traps this codebase has already hit

- **`R_PILL` is 999 — a sentinel, not a measurement.** `drawRoundedRect` clamps
  the x and y radii *independently* to half the width and half the height, so
  passing 999 for both turns a 200×56 chip into an **ellipse**. A pill is half
  the *height* on both axes; `brand_widgets._radius()` is the clamp, and every
  `RoundedFrame` / `RoundedButton` goes through it.
- **A widget may not carry another size's geometry.** `ToggleSwitch` painted a
  literal 52-wide track and a 24px thumb; the moment it was resized to fit the
  220px sidebar it drew both larger than itself and Qt clipped them. Everything
  is derived from `self.rect()` now.
- **A resting visual state must be derived, not remembered.** The same switch
  moved its thumb only from the `toggled` signal, and callers set it with
  `blockSignals()` — so it drew a green "on" track with the thumb still on the
  left. The animation is the *transition*; where the thumb sits is a function of
  `isChecked()`, resolved in `paintEvent`.

### Theming

Both stylesheets are rendered from one QSS template in `app/theme.py`, fed by the `DARK` / `LIGHT` palette bundles in `app/brand.py` — change a token there and both themes update. `theme.dark_qss()` is set app-wide at startup; light theme is applied per-window via `app.theme.apply_theme(window, "light")`. Clearing the window stylesheet (setting `""`) falls back to the app-level dark QSS.

Accent colors come from the active team's `primary_color` field and are applied inline via `setStyleSheet()` wherever the team color is needed dynamically.

**Where the red is allowed to go.** The playbook's budget is one red thing per
surface, and on a dark ground it is a *filled shape*, never a letterform — red
on carbon is 2.8:1 and forbidden for type. Four rules follow from that, and all
four were violations before:

- `RoundedButton`'s **`secondary` variant is a neutral outline**, not a red one.
  A panel with a dozen ordinary controls had a dozen red things. `primary` keeps
  the accent and there is one of those per panel.
- **Sliders, progress bars and list selection fill with white on dark / carbon
  on light**, not red.
- **`eyebrow()` and `QLabel#section_header` are muted**, not red.
- On the control screen, **red means the installation is in an exceptional
  state**: Judges or Lunch active. Standard active, the brand chip and the
  selected sidebar row are all **white**.

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

### The audience rotation (`app/slides.py`, `app/widgets/slide_panel.py`)

**One shell, four archetypes, chosen by the shape of the content** — never by
slide number. `Slide` in `app/slides.py` carries the kind:

| kind | what it is | where the one red goes |
|---|---|---|
| `statement` | authored headline + sentence | the **Trace** |
| `figure` | a real number out of a robot log | the **FROM LOG** seal |
| `roster` | a grid of sponsor marks | **nowhere** — the marks are the colour |
| `board` | the live diagnostics board | a latched fault, or nothing |

A surface is allowed zero red; it is never allowed two. That is why `roster`
spends none: a sponsor mark brings its own colour and would be the second.

**The stage is painted, not laid out.** The panel was a `QStackedWidget` of
`QLabel`s, which is why it could only ever be one template and why the
transition could only be an instant swap. Two things Qt's layout system cannot
express are load bearing here: a 140px headline sized from the live widget
height, and `line-height: 1.0` at that size — `QPainter.drawText()` spaces lines
by the font's own leading, which is far looser, so the headline that fits in the
design runs off the stage. Every multi-line block goes through
`Chassis.draw_wrapped()`, which uses `QTextLayout` for exactly that reason.

**The transition** is a `QVariantAnimation` over a painted opacity/offset pair:
260ms out (opacity→0, y −22, InCubic), 80ms of empty stage, 420ms in
(opacity→1, y +26→0, OutCubic). It runs 1240ms even though the slide has settled
at 760ms, because the Trace is still drawing on behind it over 900ms OutExpo.

**A and B share the chassis and differ by one thing:** the ledger reads
`ROTATION A` / `ROTATION B` and the rail sits left on A, right on B.

**The dwell rail reads `rotation.progress()`** — the live 45s timer — rather
than counting for itself. Two clocks for one dwell is exactly the kind of thing
that drifts apart and nobody notices.

**The rail is a child widget (`chassis.SmoothRail`) on its own 50ms tick.**
Repainting a full-screen painted stage — a 140px headline, its layout, the
plate — fast enough for a rail to look smooth is absurd, so the panel keeps its
slow tick and the rail keeps its own. At 45s across ~1500px the panel's 500ms
tick stepped the fill **16px at a time**, which reads as a stutter rather than
as time passing; 50ms makes it 1.7px. It has no background of its own, so it
asks the chassis for the plate beneath it (`paint_ground_under`) — the ground is
already a cached pixmap, and a flat fill would show a seam across the gradient.
Its geometry is set from `resizeEvent`, never from inside a paint event.

**`Slide.figure` is fitted, not fixed.** 260px is the design's *maximum*; a real
log yields figures from `14.2` to `62,118,775`, and a ten-glyph numeral set at
260 runs straight out of the plate. `_fit_figure()` scales the numeral and its
unit together so their relationship survives.

### Standard slide picker

**Control Screen → Presentation A/B → Standard Slides** lists every slide in
that screen's rotation (authored + fun facts), highlights the live one, and
jumps on click. Prev / Next / First / Reload underneath. The live row carries a
dwell rail reading the same `rotation.progress()` the audience screen shows, so
the operator can see where the rotation is without looking up at the panel.

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
150px status block, a 108px headline naming the mechanism, four vitals, a
subsystem strip. B is the board you walk up to when A has gone amber: the fault
list by name, the per-motor table with CAN ids, and which log it all came from.
Both widgets exist on both screens; `BOARD_CONTENT` on each subclass says which
one joins that screen's rotation, and either can still be pinned on either screen.

**Both sit on the shared chassis**, same as the slide rotation — the plate, the
header band, the footer ledger. Two overhead panels showing two different visual
languages in the same glance is the failure the chassis exists to prevent.

**Each vital carries its shape across the match**, bled to the tile edge
(`diagnostics.shape()`). A sag that dipped once reads differently from one that
sat low all match — that is the difference between "carry on" and "change the
battery", and a single number cannot say it. It reads `samples.sample_1s`, never
`sample`: the per-second rollup is what it is for, and a full-resolution read of
a nine-minute log to draw a 52-point line would be several hundred thousand rows
for something two centimetres wide. The aggregate matches the tile's own figure,
so the number and the line always agree.

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
brand's one-focal-red budget goes. When a fault does latch, red marks **the
fault and its origin only**: the count block, the offending tile's dot and
trace, and the offending subsystem. A second unrelated fault raises the count
to `2`; it does not paint a second region.

**Motion, almost none.** One 6s breathing dot in the header saying the feed is
live. A fault arriving does *not* flash. Latched means latched — the board never
animates to get attention twice.

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

### The pit-front panel (`app/widgets/interactive_board.py`)

1080×1920 portrait, 32" at standing height, touched by strangers. **The tabs are
gone.** Everything the board has to say is on one surface at one glance, stacked
as an interpretive panel rather than paged as an app:

    identity  →  CAD  →  Act 472  →  reach  →  programs  →  sponsors

each on a 2px rule. **The order is the argument:** this is the robot, this is
what the team changed, this is how far it reached. Tapping only ever *deepens*
what is already visible; it never navigates away from it.

- **The CAD is not a page.** It is the top 648px of the same panel — always
  live, always orbitable, subsystem chips on its own floor. Focusing a subsystem
  changes the caption under it and the pit LEDs; nothing else on the board moves.
- **There is one CAD viewer and it moves.** A `QWebEngineView` is a whole
  Chromium render process, so `ProjectScreen` *lends* its viewer to the board
  (`attach_cad`) and takes it back for the full-screen `content="cad"` face
  (`detach_cad`). Never build a second one.
- **The red, spent once:** the Act 472 plate, a filled field with white type.
  Chips, stats and cards stay carbon and white.
- **About, collapsed to a line.** The About tab was a paragraph nobody standing
  up will read; the five E's are now one mono rail under the wordmark.
- **Detail rises, never replaces.** A tapped card raises `_DetailSheet` over the
  lower two thirds — 340ms OutCubic — and **the CAD stays visible above it**. A
  visitor who tapped a card has not asked to stop looking at the robot.
- **Every card is still reachable.** The comp shows four programmes at rest; the
  grid holds all of them and scrolls, because they are real programmes the team
  runs and a kiosk that hides them is lying by omission.
- **Type scales from the panel's own width** (`_apply_scale`), not from a fixed
  px, and not from height — the design is drawn against the 1080 *width*.

### Control screen re-execution

Never seen by a visitor, so beauty here is **clarity under a six-minute clock**.
The architecture was already right — top bar, 220px sidebar, settings column —
so this is a re-execution, not a re-plan.

- **Three kinds of interface, same tokens.** Settings are a **ruled list**
  (`SettingRow`: label left, control right, 64px floor, 1px close). The EQ is an
  **instrument**. The CAN table is a **form**. `divider()` is the 2px *section*
  rule; a row closes with 1px. Two weights is what makes the grouping visible
  without reading.
- **`PanelHeader` opens every panel the same way** — eyebrow naming the kind,
  the name at 30px, one muted line of orientation, then the section rule.
- **The brand chip is the admin door and now says so**: a mono `HOLD` hairline
  under it, enough for an operator who has been told and invisible to a visitor.
- **Fingers, standing.** Mode buttons are 124×46, nav and setting rows 56–64px,
  and nothing is smaller than a thumb.
- **A `QWidget` ignores a stylesheet border unless `WA_StyledBackground` is
  set** — the rule simply never appears, silently. `SettingRow` sets it.
- **The app-wide `QPushButton` rule carries 16px of horizontal padding**, which
  ate the end of every screen name in the 220px sidebar. `ScreenCard` resets it.

### Judges slides

Drop numbered PNG/JPG files into `assets/judges_slides/` (e.g. `01_intro.png`, `02_robot.png`). Files are sorted alphabetically. Click "Reload" in the control screen to rescan.

### LED strips (`app/leds/`)

USB serial to an Arduino Uno/Nano driving **SK6812-class RGBW** strips.
**The firmware owns the animation loop** — the app sends short commands, never
pixel frames. This is not
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
| `palette.py` | Brand hex → the saturated primary actually sent to the strips |
| `firmware/pit_leds/pit_leds.ino` | The controller. Pins, per-unit pixel counts, strip placement and the switch pins are all in the config block at the top |
| `firmware/pit_probe/pit_probe.ino` | Diagnostic sketch: finds which pin a run is on, how long it is, and its pixel format. Not the controller — flash `pit_leds` back afterwards |
| `tools/led_probe.py` | Drives the probe. `id` / `ruler` / `solo` / `flood` / `rgbwraw` |
| `tools/led_color_check.py` | Self-paced colour check against the real controller |

**The strips are RGBW — four bytes per pixel, channel order RGBW.** This was
measured on the real pit (2026-09-03), and getting it wrong is the single
nastiest failure mode in this whole subsystem: drive an RGBW strip with 3-byte
pixels and your groups slide against its 4-byte ones, realigning only every 12
bytes, so a **solid colour comes back as a 3-pixel repeating green/white/blue
pattern**. Black still works perfectly (zero bytes are zero at any alignment),
so it reads as "the strip is half broken", not "wrong pixel format".

**FastLED's own `setRgbw()` cannot be used on AVR** — it allocates a 4/3-size
buffer on every show and there is no room. `packAndShow()` packs the bytes by
hand into a shared `wire` buffer instead: three RGBW pixels occupy exactly four
CRGB slots, so the stream is exact with no padding. The controllers are declared
`RGB` (not `GRB`) so those bytes are emitted verbatim, brightness is applied
during packing, and `showLeds(255)` is deliberate — any scaling FastLED did
would corrupt bytes it thinks are colours but the strip reads as pixel data.
For the same reason `setCorrection()`, `setDither()` and
`setMaxPowerInVoltsAndMilliamps()` are all off; budget power with
`MAX_BRIGHTNESS` instead.

**Colours are snapped to saturated primaries before they hit the wire**
(`palette.snap`). RGBW pixels render a mixed brand hex washed out — the team red
`#C82027` is only 13% green and 15% blue and came out visibly **pink**, while
pure `(200,0,0)` came out correctly red. Per-channel gain correction was tried
and abandoned; it needs re-tuning per strip, per batch, per colour. The brand
value is unchanged everywhere else — `leds.color` still reports the true hex and
every screen still uses it. **Do not send brand hexes straight to the wire.**

**Two channels, one axis.** The pit is three *units* but only **two electrical
channels**, and that is a fact about the wiring, not a simplification: **pin 6
feeds LEFT and RIGHT through a Y-split** (76 px each) and **pin 5 feeds CENTRE**
(93 px). `LEFT_PIN 6` / `RIGHT_PIN 6` in the original sketch was never a typo —
it described the splitter. Left and right therefore **always mirror and can
never show different content**, which is what the centre-out animations want
anyway: a left pixel and its mirrored right pixel are the same distance from the
middle of the pit and should be the same colour. The sides are addressed by
*distance* from centre (positive origin, `dir -1`) rather than a signed
position, because one channel is at `+d` and `-d` at once.

Every animation is a function of a pixel's
**distance from the true centre of the pit**, not from its own strip's
pixel 0, so a breathe blooms outward from the middle and reaches both far ends
together. Each strip declares `origin` (where its pixel 0 sits, in pixel-widths
from true centre) and `dir` (+1/-1) in the `strips[]` table; gaps between units,
unequal lengths and a backwards-wired strip are all just numbers there. Positions
are held in **half-pixel units** internally — an even-length strip has no pixel on
its own midpoint, and whole-pixel maths puts every "symmetric" effect half a pixel
off on one side.

**Measured orientation — do not re-guess these.** Both came out wrong on the
first build and only a *chase* reveals either; a breathe or a solid looks
identical either way, so "the breathe looks fine" proves nothing about them.

- **The pit's centre point is pixel 0 of the CENTRE run** (the back end), so
  that run has `origin 0`, not `-(count/2)`. It runs *away* from the middle of
  the pit rather than spanning it. With `-(count/2)` the chase started halfway
  along the strip and expanded both ways.
- **`SIDES_INDEX0_OUTER` is 0** — a side run's pixel 0 is at its *inner* end,
  nearest the centre. With this wrong the sides swept outside-in while the
  centre swept middle-out, and the two halves of the pit visibly disagreed.

`SET_COLOR`'s segment byte: `0` is CENTRE, `1` is SIDES, `0xFF` both. `INFO`
reports **two** segments and 169 px; the app reads the count rather than
assuming, so it needed no change (it still sends `0xFF` everywhere).

**The manual switch is compiled out** (`HAS_MANUAL_SWITCH 0`) because none is
wired. This matters: both inputs sit HIGH on their pull-ups when nothing is
attached, which is indistinguishable from the centre detent — so a board with
no switch reads OFF, boots dark, and ignores every host command. There is no
way to tell the two apart electrically; it has to be declared. Set it to 1 when
the SPDT goes in.

**How the switch behaves once fitted.** An SPDT on-off-on wired to two pull-up
inputs overrides the host: one throw forces all strips to red, centre blanks
them, the other throw hands control back to the app. Serial keeps being read and
ACKed in **every** position, and commands still land in `state` — so flicking back
to host resumes on what the app has been asking for, with no round trip. The truth
table lives only in `switchPosition()`.

**SRAM ceiling:** the ATmega328P has 2KB. RGBW costs a pixel buffer (3B/px) *and*
a wire buffer (4B/px for the longest channel), so the current 169 px build sits
at ~1560B with 488B free. Roughly 250 px total is the practical limit here;
past that, move to an ESP32. If you change the counts, nothing in the app needs
editing — `HELLO` reports the geometry and the app adapts.

**A static frame must not keep re-clocking the strips.** Each `show()` holds
interrupts off for milliseconds and the AVR's UART keeps only *two* bytes
without its ISR, so any frame arriving mid-write is destroyed. At ~7ms of
blackout per 16ms frame the HELLO handshake failed on almost every attempt —
the board looked dead, exactly like the no-firmware case. The `dirty` flag
fixes it: static modes (SOLID, OFF) draw once and then stop touching the
strips, and `showAll()` drains the port between the two channel writes. **Keep
that property** — anything that shows unconditionally every frame breaks the
link, silently and intermittently.

**`State` changed shape** twice — per-strip colour (`0xB9` → `0xBA`) and then
three strips down to two (`0xBA` → `0xBB`). Move `EEPROM_MAGIC` again on any
further change: an old saved struct read into a new layout garbles every field
after it rather than failing.

**Known bug, not yet fixed:** `SET_PIXELS` writes into `leds[]` and then
`render()` overwrites it on the very next frame, because the op sets
`state.mode = MODE_SOLID` and SOLID repaints every pixel from `state`. The op
has therefore never worked. Nothing in the app sends it.

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

### The equaliser (`app/widgets/eq_field.py`)

**Ten sliders in a row is a list of ten numbers, and nobody tuning a room
thinks in ten numbers.** They think in a shape — bottom pulled down, mud
scooped, presence lifted — so the ten bands are drawn as the response curve
they describe: a dashed 0 dB rule, a rail per band, one continuous white line
through ten hollow handles, then the gain readout and the Hz row. The numbers
are unchanged; only how they are shown is.

- **±20 dB spans the full field**, so 0 dB is the centre and the dashed rule is
  what the curve is read against.
- **Press anywhere and the nearest band's handle goes there**, then follows the
  finger and *stays on that band* — re-picking the band from x on every move
  smears a drag across its neighbours. This panel is touched, so the whole
  field is the target rather than ten 13px handles.
- **Presets are chips, not a combo**: there are five, they are what an operator
  reaches for, and a dropdown hides four of them behind a click mid-cycle.
  `eq.all_presets()` returns the built-ins in **authored** order — Flat to judge
  the room, then Pit Default, then the three exceptions — because sorting them
  by name put "Crowded" first, which is nobody's starting point.
- The preamp control lives **in the card header with its readout**, not in a row
  of its own; two "Preamp −2" labels is the same value said twice.

`SelectableChip` (`brand_widgets`) is the shared "one of many is chosen" mark —
white fill, carbon type. Mode buttons, CAD subsystem chips and EQ presets are
all the same object, and selected-is-red would put four red things on a panel
allowed one. `ModeButton` is the single exception: Judges and Lunch active *are*
red, because those are the exceptional states the budget exists for.

**Mode buttons follow `config.mode_changed`, not the click.** They used to be
updated only by the handler that set the mode, so anything else that moved it
left the top bar claiming a mode that was no longer live.

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

**Fun-fact slides.** `fun_facts.slides()` turns the imported log into `Slide`
objects that `PresentationScreen.rotation_slides()` appends to each screen's
authored `SLIDES`. Every one is the **Figure** archetype, carrying `figure` and
`unit` separately from its title rather than leaving the panel to parse a number
back out of a string, plus an eyebrow naming what was measured — a visitor
reading "187" needs "peak current draw" before the joke lands. Two rules: **every number is real** (nothing invented
or rounded for effect — a visitor who asks "is that true?" gets a yes), and the
jokes are at our own expense. Returns `[]` with no import, so the rotation just
omits them. Facts are read at construction; `reload_slides()` picks up a new
import without a restart.

**The CAN-id name map is the manual step.** `device` rows appear automatically
on import with `label` NULL; names are typed in **Control Screen → Pit Systems →
Robot Logs** and persist across imports. Naming is intentionally **not**
admin-gated (it is data entry); deleting a session is, because it destroys
samples.

### Self-updating (`app/update/`)

**A tag on the Mac becomes the app on the Windows pit machine, with nobody
copying a folder.** Operator side and the token setup: [`DEPLOYMENT.md`](DEPLOYMENT.md).

```
git tag v1.4.2 ─▶ Actions: stamp, build ×2, --self-check ×2 ─▶ Release + manifest.json
                                                                       │
                              pit machine, six-hour timer  ◀────────────┘
                                                                       │
                    download ─▶ sha256 ─▶ unpack ─▶ --self-check ─▶ repoint `current`
```

| File | Role |
|---|---|
| `app/version.py` | What this build is. Stamped by CI; a sentinel from a checkout |
| `app/update/release.py` | The GitHub feed. Token, manifest, verified download. No Qt |
| `app/update/install.py` | Versioned folders behind a link: stage, verify, activate, roll back, prune |
| `app/update/settings.py` | Channel and auto-check, in a JSON file rather than the DB |
| `app/update/service.py` | `_UpdateService` singleton — the state machine the panel draws |
| `app/widgets/update_panel.py` | Control → Pit Systems → Software Updates |
| `tools/stamp_version.py`, `tools/make_manifest.py` | What CI runs |
| `packaging/installer.iss` | The Inno Setup script — the one file a person is given |

**Windows only.** The Mac this is developed on runs from a checkout and never
installs a build, and a macOS CI job bills at *ten times* the Linux rate against
a private repo's monthly minutes — ~150 of the ~180 a two-platform release cost,
for an artefact nobody installed. The app's own code is still cross-platform
(`platform_key()`, symlinks instead of junctions on POSIX) because that costs
nothing and keeps the dev loop honest; only CI is single-platform.

**One build, two assets, two readers.** `…-Setup.exe` is the single file a
person downloads and double-clicks — Inno Setup wrapping exactly the `dist/`
tree PyInstaller just produced and CI just self-checked. `…-windows.zip` is what
the *app* fetches when it updates itself. Same bytes either way, so what a human
installs and what a machine updates to can never drift apart.

**Still one-folder, never one-file.** A PyInstaller `--onefile` build re-extracts
the whole 850 MB bundle to a temp directory on *every* launch — 30–60s of blank
screen before a pit display appears — and QtWebEngine is fragile in that mode.
"One file" is satisfied by the *installer* being one file, which is what an
application off the internet actually is.

**Windows will not overwrite a running `.exe`, so nothing ever tries to.** The
install is versioned folders behind a **directory junction** — every shortcut
points at `current\`, Windows resolves it at launch, so the running process
holds handles on `versions\1.4.2\` while the junction itself is locked by
nothing. Repointing it mid-session is safe and the new version is what the next
launch gets. Five things about that are load bearing:

- **A junction, not a symlink.** Junctions need no privilege on Windows;
  symlinks need Developer Mode. POSIX gets a symlink swapped with `os.replace`.
- **Never `shutil.rmtree` the link** — on a junction that descends into the
  target and deletes the version you are running. `os.rmdir` removes the
  reparse point and fails loudly on a real directory, which is also how an
  unmanaged install is refused rather than damaged.
- **The staged build is `--self-check`ed before the pointer moves**, with
  `PIT_DISPLAY_DATA` (a scratch tree, so the new build's migrations do not
  touch the live database before it is in charge), `PIT_CAD_PORT` (so it does
  not take :8765 from the CAD viewer a visitor is looking at) and
  `PIT_LEDS_FAKE` (so it does not open the controller's serial port). A build
  that fails there is deleted and never becomes the running app.
- **Old versions are pruned at startup, not at swap time.** The folder being
  replaced is still open by the running process; Windows will not delete it
  until that process is gone.
- **An install that is not this shape is left completely alone.**
  `install_root()` recognises the layout by structure, and `is_managed()` False
  means every write path here refuses.
- **The installer removes the junction with `rmdir` too**, in
  `CurUninstallStepChanged`, *before* Inno's own file deletion runs — the same
  trap, in Pascal.

**Checking is automatic; downloading never is.** A release is ~400 MB and a
download that starts itself is one that starts during a match cycle on event
wifi. The timer only ever asks, and says so on the control screen.

**Private repo, and it shapes `release.py`.** Assets are fetched by **id**
through `/releases/assets/{id}` — `browser_download_url` is a web-session URL
and 404s to a token — and **the `Authorization` header must be dropped on the
redirect** to object storage, which signs its own URL and rejects a request
carrying a bearer token as well. `_Redirect` strips it whenever the host
changes; without that, downloads 400 for no visible reason.

**A release with no `manifest.json` is skipped, not trusted.** The SHA-256 in
it is the only thing between a 400 MB download and the install folder, and "the
asset had the right name" is not a check.

**Update preferences are a JSON file, not the database** — the states worth
updating out of are the ones where the database will not open, and a preference
stored inside the broken thing is not reachable then.

**`_STATE_COLOR` puts the status colour in a dot, never in the type.** Red on
carbon is 2.8:1 and forbidden for letterforms, and a failed update next to a
red primary button would be two red things. The action button drops to
`secondary` when the machine cannot update at all — a *disabled* `primary`
still paints a full red block.

### Windows console encoding (`app/console.py`)

**Windows Python opens a redirected stdout as cp1252, not UTF-8.** Every arrow,
em-dash, ellipsis and `✗` this codebase prints is unencodable there, and one of
them raises `UnicodeEncodeError` and kills the process. It killed the first
Windows CI build outright — in `build_app.py`'s "here is the command I am
running" line, so a *progress message* took down the build.

`console.use_utf8()` is called first thing by every entry point that prints:
`main.py` (so `--self-check`, `--version` and `--rollback` are covered),
`tools/build_app.py` and `tools/stamp_version.py`. `tools/make_manifest.py`
carries the same two lines inline, because CI runs it with a bare `python3`
outside the venv and it cannot import `app`. The workflow also sets
`PYTHONUTF8=1` on both jobs, which covers everything they shell out to.

`errors="replace"` is deliberate: a console that cannot *render* a character
gets a `?`. This is diagnostic output, and diagnostics must never be the thing
that fails.

Do not "fix" a future occurrence by replacing the character. There are nine
distinct ones in the printed strings already and the next contributor will add
a tenth; the encoding assumption is the bug.

### Empty stubs

`app/db/repositories/` and `app/db/sync/` are empty stubs for the future
on-prem SQL Server sync.
