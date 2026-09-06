"""
`--self-check` — does this install actually work on this machine?

Deployment failures here are not code bugs; they are *machine* facts. The
Windows pit laptop has no VLC runtime, or the CAD viewer's Chromium helper
never made it into the bundle, or `%LOCALAPPDATA%` is redirected somewhere the
app cannot write, or nobody put an owlet binary in the build. Every one of
those looks identical from the outside — an app that starts and then quietly
cannot do one thing — and every one of them is found in about two seconds if
something goes looking.

So the shipped app can be asked:

    "Breakaway Pit Display" --self-check

It builds all four windows offscreen, renders each one, tears them down, and
reports. Exit status is 0 when everything critical passed, 1 otherwise, so it
can be the last line of an install script.

**Warnings are not failures.** No VLC means no music and a working pit display;
no owlet means no `.hoot` import and a working pit display. Only the things
that would leave an operator with nothing — an unwritable data directory, a
database that will not open, a window that cannot be built — fail the run.
"""

from __future__ import annotations

import os
import platform
import sys
import traceback
from dataclasses import dataclass, field


# The QApplication is held here rather than in a local. A local goes out of
# scope when `run()` returns, Qt tears the application down on the way out, and
# the asset-server and QtWebEngine threads abort the process — turning a clean
# pass into exit code 134 before `main` ever reaches its hard exit.
_APP = None


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    critical: bool = True
    warnings: list[str] = field(default_factory=list)


def _check_paths() -> Result:
    from app import paths
    lines = [
        f"frozen      {paths.is_frozen()}",
        f"resources   {paths.resource_root()}",
        f"data        {paths.data_root()}",
    ]
    try:
        probe = paths.data("data", ".writetest")
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return Result("paths", False,
                      "\n".join(lines) + f"\ndata directory is NOT writable: {e}")
    seeded = paths.seed_user_data()
    if seeded:
        lines.append(f"seeded      {', '.join(seeded)}")
    return Result("paths", True, "\n".join(lines))


def _check_database() -> Result:
    try:
        import app.db.migrations  # noqa: F401
        from app.db import db, init_db
        init_db()
        version = db.fetchone("PRAGMA user_version")[0]
        tables = db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        samples = db.fetchone("SELECT COUNT(*) c FROM samples.sample")["c"]
        return Result("database", True,
                      f"schema v{version}, {len(tables)} tables, "
                      f"{samples:,} telemetry samples\n"
                      f"file        {db.path if hasattr(db, 'path') else '—'}")
    except Exception as e:
        return Result("database", False, f"{type(e).__name__}: {e}")


def _check_fonts() -> Result:
    from PyQt6.QtGui import QFontDatabase
    from app import brand, paths
    families = set(QFontDatabase.families())
    missing = [f for f in (brand.FONT_DISPLAY, brand.FONT_BODY, brand.FONT_MONO)
               if f not in families]
    ttfs = list(paths.resource("assets", "fonts").glob("*.ttf"))
    detail = f"{len(ttfs)} ttf files bundled"
    if missing:
        return Result("fonts", True, f"{detail}; NOT loaded: {', '.join(missing)} "
                      "— the app will fall back and look wrong",
                      critical=False)
    return Result("fonts", True, f"{detail}; all three families present")


def _check_owlet() -> Result:
    try:
        from app.robot import owlet
        described = owlet.describe()
        binary = owlet.find_owlet()
        if binary is None:
            return Result("owlet", True,
                          f"{described}\nNo binary for this platform — .hoot "
                          "import will not work here (.wpilog and .txt still will)",
                          critical=False)
        return Result("owlet", True, f"{described}")
    except Exception as e:
        return Result("owlet", True, f"unavailable: {type(e).__name__}: {e}",
                      critical=False)


def _check_audio() -> Result:
    try:
        import vlc
        _ = vlc.Instance
    except (ImportError, OSError) as e:
        # python-vlc raises OSError, not ImportError, when the native runtime
        # is missing — catching only ImportError is the classic mistake here.
        return Result("audio", True,
                      f"libVLC not available ({type(e).__name__}) — music and "
                      "the equaliser are disabled; everything else works.\n"
                      "Install VLC on this machine to enable them.",
                      critical=False)
    return Result("audio", True, "libVLC present")


def _check_webengine() -> Result:
    """
    Is Chromium's helper process and resource set actually in this build?

    Both are things PyInstaller gets wrong silently — the app starts, and only
    a screen showing the CAD viewer ever finds out. Naming the missing file
    here turns a mystifying blank panel at an event into one line of output.
    """
    import PyQt6
    from pathlib import Path
    qt6 = Path(PyQt6.__file__).parent / "Qt6"

    if sys.platform == "darwin":
        fw = qt6 / "lib" / "QtWebEngineCore.framework"
        wanted = {
            "helper process": fw / "Helpers" / "QtWebEngineProcess.app"
                              / "Contents" / "MacOS" / "QtWebEngineProcess",
            "resources":      fw / "Resources" / "qtwebengine_resources.pak",
            "icu data":       fw / "Resources" / "icudtl.dat",
        }
    elif sys.platform == "win32":
        wanted = {
            "helper process": qt6 / "bin" / "QtWebEngineProcess.exe",
            "resources":      qt6 / "resources" / "qtwebengine_resources.pak",
            "icu data":       qt6 / "resources" / "icudtl.dat",
        }
    else:
        wanted = {
            "helper process": qt6 / "libexec" / "QtWebEngineProcess",
            "resources":      qt6 / "resources" / "qtwebengine_resources.pak",
        }

    missing = [name for name, path in wanted.items() if not path.exists()]
    if missing:
        return Result("webengine", False,
                      f"missing: {', '.join(missing)}\n"
                      "The CAD viewer cannot run. This is a packaging fault — "
                      "see _webengine_support() in packaging/pit_display.spec.")
    return Result("webengine", True,
                  f"helper process and resources present under {qt6}")


def _check_cad(app) -> Result:
    import urllib.error
    import urllib.request
    from app.cad_assets import cad_assets, init_cad_assets
    try:
        init_cad_assets()
    except RuntimeError:
        pass                       # already initialised by an earlier check
    warnings: list[str] = []
    try:
        cad_assets.start_server()
        with urllib.request.urlopen(cad_assets.viewer_url, timeout=10) as r:
            if r.status != 200:
                return Result("cad", False, f"viewer page returned {r.status}")
            size = len(r.read())
    except (urllib.error.URLError, OSError) as e:
        return Result("cad", False, f"local asset server failed: {e}")

    model = "present" if cad_assets.model_exists else "NOT bundled"
    if not cad_assets.model_exists:
        warnings.append("no robot.glb — upload one from Control → Project → "
                        "CAD Viewer Config")
    return Result("cad", True,
                  f"asset server on :{cad_assets.port}, viewer page {size} bytes, "
                  f"model {model}", warnings=warnings)


def _check_windows(app) -> Result:
    """Build, render and destroy every window — the real smoke test."""
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtGui import QPixmap
    from app.windows.control_screen import ControlScreen
    from app.windows.presentation_a import PresentationScreenA
    from app.windows.presentation_b import PresentationScreenB
    from app.windows.project_screen import ProjectScreen

    built = []
    try:
        for cls, size in ((ControlScreen, (1600, 1000)),
                          (PresentationScreenA, (1920, 1080)),
                          (PresentationScreenB, (1920, 1080)),
                          (ProjectScreen, (1080, 1920))):
            w = cls()
            w.resize(*size)
            w.show()
            for _ in range(4):
                app.processEvents()
            pm = QPixmap(*size)
            w.render(pm)
            if pm.isNull():
                return Result("windows", False, f"{cls.__name__} rendered nothing")
            built.append(cls.__name__)
            w.close()
            w.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
    except Exception:
        return Result("windows", False,
                      f"built {built} then: {traceback.format_exc(limit=6)}")
    return Result("windows", True, f"built and rendered {', '.join(built)}")


def run() -> int:
    """Run every check. Returns a process exit status."""
    # Unbuffered: this is diagnostic output that has to survive whatever
    # happens next, and Qt teardown can and does abort the process.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("PIT_LEDS_FAKE", "1")

    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    print("Breakaway Pit Display — self-check")
    print(f"  {platform.system()} {platform.release()} / {platform.machine()}")
    print(f"  Python {sys.version.split()[0]}\n")

    results = [_check_paths()]
    if not results[0].ok:
        _report(results)
        return 1

    global _APP
    _APP = QApplication(sys.argv[:1])
    app = _APP
    from main import _load_fonts
    _load_fonts(app)

    from app.admin import init_admin
    from app.checklist import init_checklist
    from app.config import init_config
    from app.judges_slides import init_judges_slides
    from app.leds import init_leds
    from app.music import init_music
    from app.rotation import init_rotation
    from app.theme import dark_qss

    results.append(_check_database())
    if results[-1].ok:
        init_config()
        init_admin()
        init_checklist()
        init_rotation()
        init_judges_slides()
        results.append(_check_webengine())
        results.append(_check_cad(app))
        init_leds()
        init_music()
        app.setStyleSheet(dark_qss())
        results.append(_check_fonts())
        results.append(_check_owlet())
        results.append(_check_audio())
        results.append(_check_windows(app))

    return _report(results)


def _report(results: list[Result]) -> int:
    failed = [r for r in results if not r.ok and r.critical]
    warned = [r for r in results if r.ok and (not r.critical or r.warnings)]

    for r in results:
        mark = "PASS" if r.ok else ("FAIL" if r.critical else "WARN")
        print(f"[{mark}] {r.name}")
        for line in (r.detail or "").splitlines():
            print(f"       {line}")
        for w in r.warnings:
            print(f"       ! {w}")
    print()

    if failed:
        print(f"FAILED — {', '.join(r.name for r in failed)}")
        return 1
    if warned:
        print(f"OK, with notes on: {', '.join(r.name for r in warned)}")
    else:
        print("OK — everything checks out.")
    return 0
