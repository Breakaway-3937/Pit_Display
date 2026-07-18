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

`init_config()` must be called first; the others depend on `config` being ready.

### Signal flow

All cross-component communication uses Qt signals — no direct calls between windows:
- `config.team_changed` → all screens re-brand (colors, labels)
- `config.mode_changed` → presentation screens swap their `QStackedWidget` page
- `config.screen_setting_changed` → theme changes applied per-window via `app.theme.apply_theme()`
- `rotation.advance` → `SlidePanel.next_slide()` on each presentation screen
- `judges_slides.slide_changed` / `slides_reloaded` → `JudgesOverlay` and `_SlidePicker` in control screen

### Theming

Both stylesheets are rendered from one QSS template in `app/theme.py`, fed by the `DARK` / `LIGHT` palette bundles in `app/brand.py` — change a token there and both themes update. `theme.dark_qss()` is set app-wide at startup; light theme is applied per-window via `app.theme.apply_theme(window, "light")`. Clearing the window stylesheet (setting `""`) falls back to the app-level dark QSS.

Accent colors come from the active team's `primary_color` field and are applied inline via `setStyleSheet()` wherever the team color is needed dynamically.

### Adding a team

Edit `app/teams.py` — add an entry to the `TEAMS` dict. The control screen combo box picks it up automatically.

### Judges slides

Drop numbered PNG/JPG files into `assets/judges_slides/` (e.g. `01_intro.png`, `02_robot.png`). Files are sorted alphabetically. Click "Reload" in the control screen to rescan.

### Empty stubs

`app/db/` (repositories, sync) and `app/robot/` are empty stubs for future SQLite and robot-file features.
