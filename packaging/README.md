# packaging/

What turns the checkout into something you can hand someone.

| File | What it is |
|---|---|
| `pit_display.spec` | The PyInstaller recipe — what goes in the bundle |
| `installer.iss` | The Inno Setup script — the single `Setup.exe` a person downloads |
| `icon.ico` | **Not here yet.** Drop it in and both pick it up |

Neither is run directly. `tools/build_app.py` drives both:

```bash
uv run tools/build_app.py --zip --installer     # on Windows
```

## The icon

**Drop `icon.ico` in this folder and you are done.** Nothing needs editing:
`pit_display.spec` globs for it, and `installer.iss` guards its `SetupIconFile`
with `#if FileExists(...)`, so the build works with or without it and starts
using it the moment it exists.

It sets three things at once — the executable's icon in Explorer and the
taskbar, the installer's own icon, and the Add/Remove Programs entry.

**It has to be a real multi-resolution `.ico`**, not a renamed PNG. Windows
picks a different size for the taskbar, the desktop and Alt-Tab, and a
single-size file gets scaled into mush at the others. Include at least
16, 32, 48 and 256 px.

From a square PNG, with ImageMagick:

```bash
magick logo.png -define icon:auto-resize=256,128,64,48,32,16 packaging/icon.ico
```

Start from **1024×1024 or larger** with a transparent background. The mark needs
to survive being 16 px wide in a taskbar, so the full wordmark will not work —
use the shield/number lockup, not `BREAKAWAY 3937` set in a line.

Check with Tao for the current artwork, and see `breakaway_branding.md`: the
logo is never recoloured, stretched, outlined, boxed or redrawn, and never
sourced from a slide deck or an image search.

macOS builds look for `icon.icns` in the same way, if anyone ever wants one.
