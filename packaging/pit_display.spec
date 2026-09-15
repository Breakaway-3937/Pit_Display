# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Breakaway Pit Display.

Build it with `tools/build_app.py`, not with `pyinstaller` directly — that
script is what sets `PIT_BUILD_*` and prints the size warnings.

**One-folder, never one-file.** A one-file build extracts the whole bundle to a
temp directory on *every* launch; with a 340 MB CAD model in it, that is a
minute of disk churn before the pit screen appears, every time. One-folder
starts instantly and is still a single directory to copy or zip.

**PyInstaller does not cross-compile.** The Windows executable has to be built
on Windows — see `.github/workflows/build.yml`, which does exactly that.
"""

import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("PIT_BUILD_ROOT", os.getcwd())).resolve()
INCLUDE_MODEL = os.environ.get("PIT_BUILD_MODEL", "1") != "0"
APP_NAME = "Breakaway Pit Display"


def tree(rel: str, *, optional: bool = False) -> list[tuple[str, str]]:
    """Every file under `rel`, mapped to the same relative path in the bundle."""
    src = ROOT / rel
    if not src.exists():
        if optional:
            return []
        raise SystemExit(f"pit_display.spec: missing required tree {src}")
    out = []
    for path in src.rglob("*"):
        if path.is_file() and not path.name.startswith("."):
            out.append((str(path), str(path.parent.relative_to(ROOT))))
    return out


datas: list[tuple[str, str]] = []

# Read-only, always shipped.
datas += tree("assets/fonts")
datas += tree("assets/cad_viewer")
datas += tree("assets/logos", optional=True)
# The Nexus API's example payloads: the offline fake feed and what
# --self-check parses to prove the models still match the spec.
datas += tree("assets/nexus")

# The owlet extractors. Without these a `.hoot` cannot be imported at all, and
# "no owlet for your platform" is the one failure an operator can neither
# diagnose nor fix from the error text — so both platforms' binaries ship,
# always, regardless of which one we are building on.
datas += tree("tools/owlet")

# Seeds: copied into the writable data directory on first run (app/paths.py).
datas += [(str(ROOT / "data" / "pit_display.db"), "data")]
datas += [(str(ROOT / "assets" / "cad" / "subsystems.json"), "assets/cad")]
datas += tree("assets/judges_slides", optional=True)

# The season's CAD model — a third of a gigabyte, and the reason the bundle is
# the size it is. `--no-model` drops it; the app then shows its "no model"
# state until somebody uploads one from the control screen.
if INCLUDE_MODEL:
    model = ROOT / "assets" / "cad" / "robot.glb"
    if model.exists():
        datas += [(str(model), "assets/cad")]

# ── QtWebEngine's helper process and resources ───────────────────────────────
# **PyInstaller gets both of these wrong, and neither failure is visible until
# something opens the CAD viewer.** On macOS it copies the framework and its
# symlink chain but leaves `Versions/A/Helpers/` empty, and it drops the
# `.pak`/`icudtl.dat` resources into a bogus `Versions/Resources/Resources/`
# that the `Resources -> Versions/Current/Resources` symlink does not point at.
# The app starts fine and then the viewer dies with
#
#     The following paths were searched for Qt WebEngine Process … / resources
#     but could not find it.
#
# Nothing else notices, which is exactly why `--self-check` looks for these two
# by name before declaring an install healthy.
def _webengine_support() -> list[tuple[str, str]]:
    import PyQt6
    qt6 = Path(PyQt6.__file__).parent / "Qt6"
    out: list[tuple[str, str]] = []

    def add_tree(src: Path, dest: str) -> None:
        for f in src.rglob("*"):
            if f.is_file():
                rel = f.parent.relative_to(src).as_posix()
                out.append((str(f), f"{dest}/{rel}" if rel != "." else dest))

    if sys.platform == "darwin":
        fw = qt6 / "lib" / "QtWebEngineCore.framework"
        helper = fw / "Helpers" / "QtWebEngineProcess.app"
        resources = fw / "Resources"
        for required in (helper, resources):
            if not required.exists():
                raise SystemExit(f"pit_display.spec: no {required}")
        version = "PyQt6/Qt6/lib/QtWebEngineCore.framework/Versions/A"
        add_tree(helper, f"{version}/Helpers/QtWebEngineProcess.app")
        add_tree(resources, f"{version}/Resources")
        return out

    if sys.platform == "win32":
        exe = qt6 / "bin" / "QtWebEngineProcess.exe"
        if not exe.exists():
            raise SystemExit(f"pit_display.spec: no {exe}")
        out.append((str(exe), "PyQt6/Qt6/bin"))
        res = qt6 / "resources"
        if res.exists():
            add_tree(res, "PyQt6/Qt6/resources")
        locales = qt6 / "translations" / "qtwebengine_locales"
        if locales.exists():
            add_tree(locales, "PyQt6/Qt6/translations/qtwebengine_locales")
        return out

    helper = qt6 / "libexec" / "QtWebEngineProcess"
    if helper.exists():
        out.append((str(helper), "PyQt6/Qt6/libexec"))
    res = qt6 / "resources"
    if res.exists():
        add_tree(res, "PyQt6/Qt6/resources")
    return out


datas += _webengine_support()

hiddenimports = [
    "app.db.migrations",     # imported for its registration side effect only
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
]

excludes = [
    # Nothing here uses them, and they drag in a lot.
    "tkinter", "matplotlib", "numpy", "scipy", "pandas", "PIL",
    "PyQt6.QtBluetooth", "PyQt6.QtNfc", "PyQt6.QtQuick3D",
    "PyQt6.QtSensors", "PyQt6.QtTest",
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

icon = None
for candidate in ("packaging/icon.ico", "packaging/icon.icns"):
    if (ROOT / candidate).exists():
        icon = str(ROOT / candidate)
        break

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX corrupts Qt plugins often enough not to risk it
    console=False,      # a pit display must not open a terminal behind it
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=icon,
        bundle_identifier="org.frc3937.pitdisplay",
        info_plist={
            "NSHighResolutionCapable": True,
            # The pit display drives audience screens for hours; letting the
            # Mac sleep mid-event is the one thing it must never do.
            "LSUIElement": False,
            "CFBundleShortVersionString": os.environ.get("PIT_BUILD_VERSION",
                                                         "0.0.0"),
        },
    )
