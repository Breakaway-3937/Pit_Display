"""
Project Screen — 3D CAD interactive display (use case 1).

Shows the robot model in interactive mode: free orbit, tap/click a
sub-assembly to isolate it with an animated transition and facts overlay.
The subsystem button bar at the bottom is driven by subsystems.json.
"""

from PyQt6.QtWidgets import QMainWindow

from app.cad_assets import cad_assets
from app.config import config
from app.widgets.cad_viewer import CADViewerWidget


class ProjectScreen(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pit Display — Project")
        self.setMinimumSize(320, 240)
        self._build_ui()
        cad_assets.model_changed.connect(self._viewer.reload_model)
        config.team_changed.connect(self._on_team_changed)
        config.screen_setting_changed.connect(self._on_setting_changed)

    def _build_ui(self):
        self._viewer = CADViewerWidget()
        self.setCentralWidget(self._viewer)

    def showEvent(self, event):
        super().showEvent(event)
        self._viewer.set_mode("interactive")

    def _on_team_changed(self, team):
        pass

    def _on_setting_changed(self, screen: str, key: str, value):
        if screen != "project":
            return
