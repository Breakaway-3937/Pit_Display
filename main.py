import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from app.config import init_config
from app.judges_slides import init_judges_slides
from app.rotation import init_rotation
from app.windows.control_screen import ControlScreen
from app.windows.presentation_a import PresentationScreenA
from app.windows.presentation_b import PresentationScreenB
from app.windows.project_screen import ProjectScreen


def _load_stylesheet(app: QApplication) -> None:
    qss = Path(__file__).parent / "assets" / "styles.qss"
    if qss.exists():
        app.setStyleSheet(qss.read_text())


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Pit Display")
    app.setOrganizationName("FRC Pit")

    init_config()
    init_rotation()
    init_judges_slides()
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
