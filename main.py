import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

# Must be set before QApplication is created when using QtWebEngine
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

import app.db.migrations  # noqa: F401 — registers migrations before init_db()
from app import paths
from app.admin import init_admin
from app.cad_assets import init_cad_assets
from app.checklist import init_checklist
from app.config import init_config
from app.db import init_db
from app.judges_slides import init_judges_slides
from app.leds import init_leds
from app.music import init_music
from app.rotation import init_rotation
from app.theme import dark_qss
from app.touch import install as install_touch
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


def main():
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
    app.setOrganizationName("FRC Pit")
    _load_fonts(app)

    # A fresh install starts with the database, CAD model and judges slides
    # that shipped with it — copied out of the read-only bundle into the
    # writable tree, once, before anything opens them.
    seeded = paths.seed_user_data()
    if seeded:
        print(f"Seeded user data into {paths.data_root()}: "
              f"{', '.join(seeded)}", flush=True)

    init_config()
    init_db()
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

    # Leave the hardware in a known state on the way out: strips blanked (the
    # firmware watchdog takes over from there) and the audio device released.
    app.aboutToQuit.connect(control.shutdown_managed)
    app.aboutToQuit.connect(led_service.shutdown)
    app.aboutToQuit.connect(music_service.shutdown)
    # Closing the control panel is quitting the app: it is the only window an
    # operator can reach, and the audience screens have no chrome to close.
    app.setQuitOnLastWindowClosed(True)

    control.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
