from __future__ import annotations

import os
import sys
from pathlib import Path

# First, before any other app import: an import-time failure in a
# console-less build must still land somewhere. See app/crash_log.py.
from app import crash_log
crash_log.install()

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

# Must be set before QApplication is created when using QtWebEngine
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

import app.db.migrations  # noqa: F401 — registers migrations before init_db()
from app import paths
from app import qt_log
from app.console import use_utf8
from app.admin import init_admin
from app.cad_assets import init_cad_assets
from app.checklist import init_checklist
from app.config import init_config
from app.db import init_db
from app.judges_slides import init_judges_slides
from app.leds import init_leds
from app.music import init_music
from app.nexus import init_nexus
from app.nexus.alerts import init_alerts
from app.rotation import init_rotation
from app.theme import dark_qss
from app.touch import install as install_touch
from app.update import init_update
from app.webcast import init_webcast
from app.windows.control_screen import ControlScreen
from app.windows.presentation_a import PresentationScreenA
from app.windows.presentation_b import PresentationScreenB
from app.windows.project_screen import ProjectScreen


def _load_fonts(app: QApplication) -> None:
    from PyQt6.QtGui import QFont, QFontDatabase
    fonts_dir = paths.resource("assets", "fonts")
    for ttf in fonts_dir.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(ttf))
    # App default font — set here, NOT via a universal QSS rule, so widgets
    # that size their own type with setFont() (impact board, brand widgets)
    # aren't overridden by the stylesheet.
    default = QFont("Roboto")
    default.setPixelSize(13)
    app.setFont(default)


def _cli(argv: list[str]) -> int | None:
    """
    The flags that answer without opening a window. None = start normally.

    All three exist for a machine nobody is standing at: an install script that
    wants an exit status, a pit laptop being asked what it is running over the
    phone, and — the one that matters — a display that has come up wrong and
    has to be put back the way it was without a working GUI to do it from.
    """
    if "--version" in argv:
        from app import version
        from app.update import install
        print(version.describe())
        root = install.install_root()
        if root is not None:
            print(f"installed at {root}, running {install.running_version()}")
        return 0

    if "--rollback" in argv:
        from app.update.install import rollback
        from app.update.release import UpdateError
        try:
            print(f"Rolled back to {rollback()} — it starts on the next launch.")
            return 0
        except UpdateError as exc:
            print(f"Could not roll back: {exc}", file=sys.stderr)
            return 1

    if "--provision" in argv:
        # Apply a pit setup file — keys and the event — from a script or a
        # prompt. The GUI build has no console of its own; borrow the caller's
        # so the report is seen. See app/provision.py.
        from app import provision
        from app.selfcheck import _attach_console
        _attach_console()
        i = argv.index("--provision")
        if i + 1 >= len(argv):
            print("usage: --provision <pit-setup.json>", file=sys.stderr)
            return 2
        try:
            for line in provision.apply_file(Path(argv[i + 1])):
                print(f"  {line}")
            print("Applied. Start the app normally.")
            return 0
        except provision.ProvisionError as exc:
            print(f"Not applied: {exc}", file=sys.stderr)
            return 1
    return None


def main():
    # Before anything prints: Windows opens a redirected stdout as cp1252, and
    # a single em-dash in a status line is then a crash. See app/console.py.
    use_utf8()

    status = _cli(sys.argv[1:])
    if status is not None:
        sys.exit(status)

    # `--self-check` boots everything offscreen and reports, so an install can
    # be verified on the pit machine without anyone watching a screen. See
    # app/selfcheck.py.
    if "--self-check" in sys.argv:
        from app.selfcheck import run as self_check
        status = self_check()
        # Exit *hard*. The check has already printed everything it has to say,
        # and a normal interpreter shutdown here tears down QtWebEngine's and
        # the asset server's threads in an order Qt aborts on — turning a clean
        # pass into exit code 134 and making the status useless to a script.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(status)

    app = QApplication(sys.argv)
    app.setApplicationName("Pit Display")

    # Qt's own warnings, to a file. The shipped app has no console behind the
    # screens, so anything Qt writes to stderr is lost — including the object
    # lifetime complaints that name a class and nothing else. See app/qt_log.py.
    qt_log.install()
    app.setOrganizationName("FRC Pit")
    _load_fonts(app)

    # A fresh install starts with the database, CAD model and judges slides
    # that shipped with it — copied out of the read-only bundle into the
    # writable tree, once, before anything opens them.
    seeded = paths.seed_user_data()
    if seeded:
        print(f"Seeded user data into {paths.data_root()}: "
              f"{', '.join(seeded)}", flush=True)
    # A `pit-setup.json` dropped beside the database carries the keys and the
    # event; apply it before any singleton goes looking for them.
    from app import provision
    for line in provision.auto_import():
        print(f"pit-setup: {line}", flush=True)

    init_config()
    init_db()
    # Updates need neither config nor the database — deliberately, since the
    # states worth updating out of are the ones where those are broken. It goes
    # here only because the control screen's panel reads it while building.
    init_update()
    # The Nexus event feed reads the active team from config and nothing else;
    # it starts polling on its own timer once the control screen is up.
    nexus_service = init_nexus()
    # Admin gates the LED/EQ controls; needs the DB for its credential row.
    init_admin()
    # Checklists are read while the presentation screens are being built.
    init_checklist()
    init_rotation()
    init_judges_slides()
    init_cad_assets()
    # LEDs after cad_assets — the service subscribes to subsystem_focused so the
    # strips can echo whichever subsystem the CAD viewer flies to.
    led_service = init_leds()
    music_service = init_music()
    # Queue and inspection alerts: reads the feed, drives the strips, and
    # hands the overhead screens their banner. After both of those.
    init_alerts()

    # Before the control screen, because its Pit Network panel subscribes to
    # this service while it is being built. The service itself depends on
    # nothing but its own settings file; what it needs from the control screen
    # — how to find a window — is handed over below, once there is one.
    webcast_service = init_webcast()

    app.setStyleSheet(dark_qss())

    control = ControlScreen()

    # The audience screens are **built on demand and destroyed when powered
    # off**, so the control screen gets factories rather than instances. A
    # screen that is off costs nothing: no timers, no subscriptions, and for
    # the project screen no Chromium process.
    def _project() -> ProjectScreen:
        window = ProjectScreen()
        # The two touch panels route touch per point rather than through Qt's
        # single synthesized mouse, so a finger on one screen can't latch a
        # widget on the other. The router has to be told about each new project
        # window; it forgets them again when they are destroyed. Presentation
        # screens are audience-facing and keep Qt's own synthesis.
        install_touch(app, window)
        return window

    control.set_window_factories({
        "presentation_a": PresentationScreenA,
        "presentation_b": PresentationScreenB,
        "project":        _project,
    })
    install_touch(app, control)

    # The overhead screens, optionally published to the pit LAN so a Pi on the
    # Ethernet switch can show one in a browser instead of a monitor on the end
    # of an HDMI run. It asks the control screen for windows rather than
    # building any — window lifetime has one owner — and the control screen
    # keeps a published screen's window alive (headless, never placed on a
    # monitor) even while its power toggle is off, which is what lets the pit
    # machine drive these with no video output at all.
    webcast_service.set_window_provider(control.managed_window)
    control.set_webcast(webcast_service)
    webcast_service.start()

    # Leave the hardware in a known state on the way out: strips blanked (the
    # firmware watchdog takes over from there) and the audio device released.
    app.aboutToQuit.connect(control.shutdown_managed)
    app.aboutToQuit.connect(led_service.shutdown)
    app.aboutToQuit.connect(music_service.shutdown)
    app.aboutToQuit.connect(nexus_service.shutdown)
    app.aboutToQuit.connect(webcast_service.stop)
    # Closing the control panel is quitting the app: it is the only window an
    # operator can reach, and the audience screens have no chrome to close.
    app.setQuitOnLastWindowClosed(True)

    control.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
