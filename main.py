import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

# Must be set before QApplication is created when using QtWebEngine
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

import app.db.migrations  # noqa: F401 — registers migrations before init_db()
from app.admin import init_admin
from app.cad_assets import init_cad_assets
from app.config import init_config
from app.db import init_db
from app.judges_slides import init_judges_slides
from app.leds import init_leds
from app.music import init_music
from app.rotation import init_rotation
from app.theme import dark_qss
from app.windows.control_screen import ControlScreen
from app.windows.presentation_a import PresentationScreenA
from app.windows.presentation_b import PresentationScreenB
from app.windows.project_screen import ProjectScreen


def _load_fonts(app: QApplication) -> None:
    from PyQt6.QtGui import QFont, QFontDatabase
    fonts_dir = Path(__file__).parent / "assets" / "fonts"
    for ttf in fonts_dir.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(ttf))
    # App default font — set here, NOT via a universal QSS rule, so widgets
    # that size their own type with setFont() (impact board, brand widgets)
    # aren't overridden by the stylesheet.
    default = QFont("Roboto")
    default.setPixelSize(13)
    app.setFont(default)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Pit Display")
    app.setOrganizationName("FRC Pit")
    _load_fonts(app)

    init_config()
    init_db()
    # Admin gates the LED/EQ controls; needs the DB for its credential row.
    init_admin()
    init_rotation()
    init_judges_slides()
    init_cad_assets()
    # LEDs after cad_assets — the service subscribes to subsystem_focused so the
    # strips can echo whichever subsystem the CAD viewer flies to.
    led_service = init_leds()
    music_service = init_music()
    app.setStyleSheet(dark_qss())

    # Create all windows — only control is shown on boot
    control = ControlScreen()
    pres_a  = PresentationScreenA()
    pres_b  = PresentationScreenB()
    project = ProjectScreen()

    # Hand the other windows to the control screen so it can show/hide them
    control.set_managed_windows({
        "presentation_a": pres_a,
        "presentation_b": pres_b,
        "project":        project,
    })

    # Leave the hardware in a known state on the way out: strips blanked (the
    # firmware watchdog takes over from there) and the audio device released.
    app.aboutToQuit.connect(led_service.shutdown)
    app.aboutToQuit.connect(music_service.shutdown)

    control.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
