# Pit Display

Four-window PyQt6 desktop app for the FRC Team 3937 "Breakaway" pit: an
operator control screen, two audience-facing presentation screens, and an
interactive project touchscreen (3D CAD viewer / Impact board).

## Running

```bash
uv run main.py
```

Only the control screen opens on boot — the other windows are switched on with
the power toggles in its sidebar.

## Quick reference

- **Modes** — Standard (rotating slides), Judges (slides from
  `assets/judges_slides/` + CAD subsystem focus), Lunch (full-screen break
  overlay). Switched from the control screen top bar.
- **Judges slides** — drop numbered PNG/JPG files into `assets/judges_slides/`
  and click Reload on the control screen.
- **Teams** — add entries to `app/teams.py`; branding follows the selection
  automatically.
- **Pit systems** — LED strips and music sit below the screens in the control
  sidebar. Both follow the team colour and the display mode.
- **Admin lock** — click the Breakaway mark (top-left) to unlock LED tuning and
  the equaliser. Closing it re-locks. See [`ADMIN_GUIDE.md`](ADMIN_GUIDE.md).
- **Adding media** — music, judges slides, CAD, robot logs: [`MEDIA_GUIDE.md`](MEDIA_GUIDE.md).
- **Shipping a new version, or setting up a pit machine** — the checklist is
  [`RELEASING.md`](RELEASING.md); the reference is [`DEPLOYMENT.md`](DEPLOYMENT.md).
- **Database** — every table and query: [`DATABASE.md`](DATABASE.md).

## LED strips

USB serial to an Arduino Uno/Nano driving WS2812B. The **firmware owns the
animation loop** — the app only sends short commands, so the strips keep running
if this app closes, and long strips never suffer the dropped-serial problem that
pixel streaming hits on AVR.

### Setup

1. Open `firmware/pit_leds/pit_leds.ino`, set `NUM_LEDS` and `LED_PIN`, flash it
   (needs the **FastLED** library).
2. Plug the board in. The app finds it by USB ID and handshakes automatically —
   no port to configure, and replugging is fine.
3. Control it from **Control Screen → Pit Systems → LED Strips**.

Brightness, speed, looks and colour are **admin only** — click the Breakaway
mark in the top-left to unlock. The **on/off kill switch stays available to any
operator**, because "turn those off please" should never need a password.

**Save as boot default** writes the current look to the controller's EEPROM,
which is also what the watchdog falls back to if the app stops talking.

**Wiring:** power the strip from its own 5V supply (USB runs about eight pixels),
tie all grounds together, 330Ω in series on the data line, 1000µF across the
strip's 5V/GND.

**Strip length:** stay at or below ~300 pixels on an Uno/Nano — the ATmega328P
has 2KB of SRAM and FastLED uses 3 bytes per pixel. Beyond that, use an ESP32.

### No hardware? 

```bash
PIT_LEDS_FAKE=1 uv run main.py
```

The panel connects to a simulated controller so the whole UI works on a machine
with nothing plugged in.

## Music

A local media hub for the overhead speakers, with a ten-band equaliser.
**Control Screen → Pit Systems → Music.**

Point it at a folder with **Add folder…** and it indexes everything it finds
(re-scanning is safe — it will not duplicate or lose your queue). Double-click to
play. It works with no internet, no account, and no subscription.

- **Volume cap** is the real ceiling; the volume slider cannot exceed it.
- **Duck for a visitor** drops to 20% in one click. Judges mode does it on its own.
- **The music folder is pre-curated by the team** — there is no explicit filter
  in the app, so listen through the folder before an event.

### Equaliser

**Admin only** — click the Breakaway mark in the top-left to unlock.

Ten bands, 31Hz to 16kHz, plus a preamp. Presets: *Pit Default*, *Crowded*,
*Judges Visiting*, *Lunch*, *Flat*.

Before touching a slider: **aim the speakers down into the pit**, not across the
aisle. That beats any curve here. Then the two moves that matter most are a
high-pass around 100–120Hz (pit boom is low end bouncing off concrete — cutting
it makes you clearer *and* less annoying) and a broad cut at 200–400Hz for mud.
Every preset does both.

**Requires the VLC runtime.** Install VLC on the pit machine, or bundle
`libvlc.dll` and its plugins folder. Without it the app still runs and the panel
tells you what is missing — it just cannot play or equalise anything.

### Spotify

Not built in, on purpose. It needs internet for every command, Dev Mode caps an
app at five users and requires the owner to keep an active Premium subscription,
and there is no way to apply the equaliser to it. If you want Spotify at an
event, run the desktop client on the pit machine alongside this app.

## CAD viewer

Interactive 3D robot model, served to the project touchscreen and (in judges
mode) both presentation screens. The season-by-season workflow — exporting
from Onshape, naming sub-assemblies, camera presets — is in `CAD_GUIDE.md`.

### Commands

```bash
# One-time per machine: download Three.js + GSAP into assets/cad_viewer/lib/
# so the viewer works fully offline at competition venues
uv run scripts/setup_cad_assets.py

# Then run the app as normal
uv run main.py
```

Everything else is done from **Control Screen → Project → CAD Viewer Config**:

| Action | Where |
|---|---|
| Upload / replace the robot model | **Upload Model (.glb)** — copies the file to `assets/cad/robot.glb` |
| Set the season year | **Season year** field |
| Fix a robot lying on its side | **Model up axis** dropdown (usually "Z up" for CAD exports), then **Save Config** — applies to open viewers instantly, no reload |
| Define a subsystem | **+ Add Subsystem** — display name, exact Onshape node name (case-sensitive), accent color, one fact per line, optional camera preset (azimuth / elevation / distance) |
| Edit / remove a subsystem | **Edit** / **✕** on its row |
| Persist changes | **Save Config** — writes `assets/cad/subsystems.json` and pushes it to open viewers live |
| Force viewers to re-load the model | **↺ Reload Viewer** |

Judges-mode driving lives under each presentation screen's settings
(**Judges CAD** section): pick a subsystem to fly both presentation screens to
it, **↩ Full View** to return to the whole robot, **◀ Back to Slides** to
leave the CAD page.

### Viewer controls (project touchscreen)

| Input | Action |
|---|---|
| Drag (mouse-left / one finger) | Free 360° tumble — no pole locks, rotates across all axes like a desktop CAD package. The rotation pivots around the exact point on the model you grab; grabbing empty space rotates around the view center |
| Scroll wheel / pinch | Zoom |
| Right-drag / two-finger drag | Pan |
| Tap a part | Isolate its subsystem — everything else fades, the camera flies to its preset, and the facts panel slides in |
| Tap empty space or **↩ Full View** | Restore the full robot, re-level and re-frame the camera |
| Bottom subsystem bar | Tap any subsystem button to jump straight to it |

### Features

- **Color-accurate rendering** — appearance colors set in Onshape carry
  through faithfully: straight sRGB pipeline (no filmic tone mapping),
  neutral lighting, and a camera headlight so no side ever goes dark.
  Metalness/roughness are clamped so metal parts read as their real color
  instead of black mirrors or blown highlights.
- **Click-point rotation** — the tumble pivot is the geometry under your
  cursor at drag start, CAD-style; rotation stops the instant you release.
- **Up-axis correction** — Z-up/X-up CAD exports are re-oriented upright from
  a config dropdown, applied live.
- **Subsystem isolation** — tap or button-driven; non-selected geometry fades,
  a facts overlay presents the talking points, and each subsystem can carry
  its own accent color and camera angle.
- **Judges mode** — the operator drives both presentation screens from the
  control panel with slower, cinematic camera moves; touch input is locked on
  the audience screens.
- **Large-model performance** — renders only when something changes (idle
  costs ~nothing), capped pixel ratio, frozen scene-graph matrices, cached
  picking, and material-level fade animations. Files over ~150 MB still load
  slowly — export with coarser tessellation (see `CAD_GUIDE.md`).
- **Fully offline** — libraries and model are served from a local HTTP server
  (port 8765); no internet needed at the venue after the one-time setup
  script.
- **Theme + team aware** — follows each screen's dark/light setting and the
  active team's accent color like every other screen.

Architecture notes live in `CLAUDE.md`; brand tokens in
`Breakaway_Branding.md` and `app/brand.py`.
