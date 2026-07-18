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
