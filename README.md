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
- **CAD viewer** — run `uv run scripts/setup_cad_assets.py` once to download
  the offline Three.js libraries, then upload a `.glb` from the Project
  settings panel. See `CAD_GUIDE.md`.
- **Teams** — add entries to `app/teams.py`; branding follows the selection
  automatically.

Architecture notes live in `CLAUDE.md`; brand tokens in
`Breakaway_Branding.md` and `app/brand.py`.
