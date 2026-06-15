import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

# Must be set before QApplication is created when using QtWebEngine
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

from app.cad_assets import init_cad_assets
from app.config import init_config
from app.db import init_db
from app.judges_slides import init_judges_slides
from app.rotation import init_rotation
from app.windows.control_screen import ControlScreen
from app.windows.presentation_a import PresentationScreenA
from app.windows.presentation_b import PresentationScreenB
from app.windows.project_screen import ProjectScreen


def _load_fonts() -> None:
    from PyQt6.QtGui import QFontDatabase
    fonts_dir = Path(__file__).parent / "assets" / "fonts"
    for ttf in fonts_dir.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(ttf))


def _load_stylesheet(app: QApplication) -> None:
    qss = Path(__file__).parent / "assets" / "styles.qss"
    if qss.exists():
        app.setStyleSheet(qss.read_text())


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Pit Display")
    app.setOrganizationName("FRC Pit")
    _load_fonts()

    init_config()
    init_db()
    init_rotation()
    init_judges_slides()
    init_cad_assets()
    _load_stylesheet(app)

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

    control.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
