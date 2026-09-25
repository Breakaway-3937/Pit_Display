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
# The pit-front panel's sponsor marks. Required: a bundle without them shows
# the public four empty plates, and --self-check's `sponsors` line names any
# that went missing.
datas += tree("assets/Sponsor Logos")
# The Nexus API's example payloads: the offline fake feed and what
# --self-check parses to prove the models still match the spec.
datas += tree("assets/nexus")

# The pit-LAN screen pages. Served to a browser across the pit, so a bundle
# that dropped them answers 500 to a panel and nothing else complains —
# `--self-check` looks for all three by name for exactly that reason.
datas += tree("assets/webcast")

# The owlet extractors. Without these a `.hoot` cannot be imported at all, and
# "no owlet for your platform" is the one failure an operator can neither
# diagnose nor fix from the error text — so both platforms' binaries ship,
# always, regardless of which one we are building on.
datas += tree("tools/owlet")

# The VLC runtime, on Windows only. Windows ships no libVLC and a pit machine
# has none, so without this the music player and the entire equaliser are dead
# on a fresh install until somebody downloads VLC *and* picks the 64-bit build
# to match — a separate install step whose failure only shows up when an
# operator presses play. macOS and Linux resolve libvlc through the system, so
# there is nothing to carry there.
# `tools/fetch_vlc.py` populates `vlc/`; it is optional so that a build on a
# machine that has not run it still succeeds and simply has no audio, which is
# the behaviour that existed before.
if sys.platform == "win32":
    datas += tree("vlc", optional=True)

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
    # The pit-LAN screens and the Nexus relay socket. Both are imported
    # normally and PyInstaller finds them; they are named here because the
    # relay's `wss://` also needs QtNetwork's `plugins/tls/` backend, which the
    # QtNetwork hook collects only when QtNetwork is in the graph. A build
    # without it starts fine and the relay never connects — `--self-check`'s
    # `network` result is what proves it made it in.
    "PyQt6.QtNetwork",
    "PyQt6.QtWebSockets",
    # Python-side HTTPS (updates, Nexus) verifies through the OS, not a
    # snapshot of its certificate store — see app/net.py. Imported lazily, so
    # named here; `--self-check`'s `network` line fails without it.
    "truststore",
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

# ── The launcher splash (Windows) ────────────────────────────────────────────
# Shown by the bootloader **before Python starts**, which on a pit machine is
# most of the wait: ~2,100 files load and Defender scans every new DLL on the
# first launch after an update. main.py swaps in the live boot screen
# (app/widgets/boot_splash.py) at the same size the moment Qt is up, and
# closes this one. `splash.png` is rendered from that widget by
# tools/make_splash.py; the text sits on the boot screen's status baseline.
#
# Windows only: PyInstaller's splash is unsupported on macOS. It needs Tcl/Tk
# in the build interpreter, so it is optional — a build that can't make one
# ships without it and says so, rather than failing a release over a splash.
# The updater's hidden self-check sets PYINSTALLER_SUPPRESS_SPLASH_SCREEN so
# it never flashes over a pit screen (app/update/install.py).
splash = None
splash_png = ROOT / "packaging" / "splash.png"
if sys.platform == "win32" and splash_png.exists():
    try:
        splash = Splash(
            str(splash_png),
            binaries=a.binaries,
            datas=a.datas,
            text_pos=(40, 297),          # bottom-left anchor, the status baseline
            text_size=11,
            text_font="Segoe UI",
            text_color="#F3F1F0",        # brand.INK_DARK
            text_default="Starting…",
            always_on_top=False,         # never over another app's window
        )
    except Exception as exc:             # no Tcl/Tk in this interpreter, etc.
        print(f"pit_display.spec: no launcher splash — {exc}")
        splash = None

icon = None
for candidate in ("packaging/icon.ico", "packaging/icon.icns"):
    if (ROOT / candidate).exists():
        icon = str(ROOT / candidate)
        break

exe = EXE(
    pyz,
    a.scripts,
    *([splash] if splash else []),
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
    *([splash.binaries] if splash else []),
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
