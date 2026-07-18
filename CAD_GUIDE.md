# CAD Viewer — Year-Over-Year Guide

## What this system does

- **Project screen** — interactive 3D CAD touch display. Free 360° tumble rotation that pivots around the point you grab (like a desktop CAD package), pinch/scroll zoom, two-finger or right-drag pan; tap any sub-assembly to isolate it with an animated transition and facts overlay.
- **Presentation A/B (judges mode)** — when the operator selects a subsystem from the control screen, both presentation screens switch to the CAD view and animate to that subsystem with cinematic timing and fact overlays.

---

## Start of season setup (one time, per machine)

```bash
# Download Three.js and GSAP to local lib/ — required for offline competition use
uv run scripts/setup_cad_assets.py
```

---

## Each season: export the robot model

1. Open the top-level assembly in **Onshape**
2. Right-click the assembly → **Export**
3. Format: **GLTF** → check **"Export as single file (.glb)"**
4. Check **"Export all configurations"** / **"Preserve hierarchy"** (exact option name varies by Onshape version — the goal is to keep sub-assembly names as named nodes in the output)
5. **Choose a coarse/medium tessellation.** Fine tessellation balloons the file (hundreds of MB) and makes loading and orbiting slow. The pit display doesn't need machining-grade surfaces — coarse looks identical from a metre away.
6. Upload the `.glb` in the app: **Control Screen → Project → CAD Viewer Config → Upload Model (.glb)**

### Robot appears tipped on its side?

CAD packages usually treat **Z** as up; the viewer (glTF standard) uses **Y**.
If the robot loads lying on its back or side, set **Model up axis** in
**CAD Viewer Config** (usually "Z up") and click **Save Config** — open viewers
re-orient instantly, no reload needed.

### Finding your sub-assembly node names

Node names in the exported GLB match the **Onshape sub-assembly names** exactly (case-sensitive). To find them:

1. In Onshape, expand the feature tree on the left
2. Note the names of each sub-assembly (e.g. `Intake`, `Shooter`, `Indexer`)
3. Those exact strings go into `node_name` in the subsystem config

---

## Defining subsystems

Go to **Control Screen → Project → CAD Viewer Config**.

### Adding a subsystem

1. Click **+ Add Subsystem**
2. Fill in the dialog:

| Field | Description |
|---|---|
| **Display name** | Shown on screen, e.g. `Intake` |
| **Onshape node name** | Must match the Onshape sub-assembly name exactly, e.g. `Intake` |
| **Accent color** | Hex color for the highlight ring and facts panel border, e.g. `#FF6B35` |
| **Facts** | One fact per line — shown in the overlay when this subsystem is focused |
| **Camera preset** | Optional: azimuth (°), elevation (°), distance factor. Controls where the camera flies to when focusing. |

3. Click **OK**, then **Save Config**

### Camera preset guide

- **Azimuth** (0–360°): horizontal rotation around the sub-assembly. `0` = front, `90` = right side, `180` = rear.
- **Elevation** (−90–90°): vertical angle. `25` = slightly above, `0` = level, `−15` = slightly below.
- **Distance factor** (multiplier): `2.2` = default. Larger = camera further away. `1.0` = very tight.

If no preset is set, the viewer auto-computes a reasonable angle.

### Editing / removing a subsystem

Use the **Edit** or **✕** buttons next to each subsystem row. Remember to **Save Config** after changes.

---

## Judges mode workflow

1. Set mode to **Judges** in the top bar
2. Open **Control Screen → Presentation A** (or B)
3. In the **Judges CAD** section, click a subsystem button
   - Both presentation screens switch to the CAD view
   - The camera animates to that sub-assembly with cinematic timing
   - Facts appear in an overlay panel
4. Click **↩ Full View** to return to the full robot view (still on CAD page)
5. Click **◀ Back to Slides** to return to the judges slides

---

## File locations

| File | Purpose |
|---|---|
| `assets/cad/robot.glb` | The exported robot model (replaced via app upload) |
| `assets/cad/subsystems.json` | Season config — subsystem names, facts, camera presets |
| `assets/cad_viewer/lib/` | Three.js + GSAP libraries (populated by setup script) |
| `assets/cad_viewer/index.html` | Three.js viewer HTML |
| `assets/cad_viewer/viewer.js` | Viewer logic — animations, subsystem isolation, API |

---

## Troubleshooting

**"No Model Loaded" on the viewer** — Upload a `.glb` file via the control screen, or check that `assets/cad/robot.glb` exists.

**"3D libraries not found" banner** — Run `uv run scripts/setup_cad_assets.py` once.

**Subsystem doesn't isolate / wrong mesh group** — The `node_name` in `subsystems.json` doesn't match the Onshape sub-assembly name. Check capitalization and spacing.

**Judges mode doesn't show CAD** — Make sure mode is set to `Judges` before clicking a subsystem in the Judges CAD panel, and that `cad_active` is True (clicking a subsystem button activates it automatically).
