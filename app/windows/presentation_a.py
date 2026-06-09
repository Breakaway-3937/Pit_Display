"""Presentation Screen A — rotating slides, lunch overlay, judges slides, judges CAD."""

from PyQt6.QtWidgets import QMainWindow, QStackedWidget
from PyQt6.QtCore import Qt

from app.cad_assets import cad_assets
from app.config import config
from app.rotation import rotation
from app.widgets.cad_viewer import CADViewerWidget
from app.widgets.lunch_overlay import LunchOverlay
from app.widgets.judges_overlay import JudgesOverlay
from app.widgets.slide_panel import SlidePanel


_SLIDES = [
    (
        "Welcome to Team 3937!",
        "Stop by and meet the team — we'd love to tell you about our season.",
    ),
    (
        "Our Robot This Year",
        "Designed and built from the ground up by our student members.",
    ),
    (
        "Awards & Milestones",
        "Celebrating the accomplishments that define our team's journey.",
    ),
    (
        "Come Ask Us Anything",
        "Questions about FRC, engineering, or our robot? We've got answers.",
    ),
]


class PresentationScreenA(QMainWindow):

    _PAGE_NORMAL  = 0
    _PAGE_LUNCH   = 1
    _PAGE_JUDGES  = 2
    _PAGE_CAD     = 3

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pit Display — Presentation A")
        self.setMinimumSize(320, 240)
        self._build_ui()
        config.team_changed.connect(self._on_team_changed)
        config.mode_changed.connect(self._on_mode_changed)
        config.screen_setting_changed.connect(self._on_setting_changed)
        rotation.advance.connect(self._on_rotation_advance)
        cad_assets.cad_active_changed.connect(self._on_cad_active_changed)
        cad_assets.subsystem_focused.connect(self._on_subsystem_focused)
        cad_assets.model_changed.connect(self._cad_view.reload_model)

    def _build_ui(self):
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._slides  = SlidePanel(_SLIDES)
        self._stack.addWidget(self._slides)                          # 0

        self._lunch   = LunchOverlay(screen_id="presentation_a")
        self._stack.addWidget(self._lunch)                           # 1

        self._judges  = JudgesOverlay(screen_id="presentation_a")
        self._stack.addWidget(self._judges)                          # 2

        self._cad_view = CADViewerWidget()
        self._stack.addWidget(self._cad_view)                        # 3

        self._stack.setCurrentIndex(self._PAGE_NORMAL)

    def _on_rotation_advance(self):
        if config.mode == "standard":
            self._slides.next_slide()

    def _on_mode_changed(self, mode: str):
        if mode == "lunch":
            self._stack.setCurrentIndex(self._PAGE_LUNCH)
            self._lunch.apply_fonts()
        elif mode == "judges":
            if cad_assets.cad_active:
                self._stack.setCurrentIndex(self._PAGE_CAD)
                self._cad_view.set_mode("judges")
                if cad_assets.focused_id:
                    self._cad_view.focus_subsystem(cad_assets.focused_id)
            else:
                self._stack.setCurrentIndex(self._PAGE_JUDGES)
        else:
            self._slides.reset()
            self._stack.setCurrentIndex(self._PAGE_NORMAL)

    def _on_cad_active_changed(self, active: bool):
        if config.mode != "judges":
            return
        if active:
            self._stack.setCurrentIndex(self._PAGE_CAD)
            self._cad_view.set_mode("judges")
        else:
            self._cad_view.reset_view()
            self._stack.setCurrentIndex(self._PAGE_JUDGES)

    def _on_subsystem_focused(self, sub_id: str):
        if config.mode != "judges" or not cad_assets.cad_active:
            return
        if sub_id:
            self._cad_view.focus_subsystem(sub_id)
        else:
            self._cad_view.reset_view()

    def _on_team_changed(self, team):
        pass

    def _on_setting_changed(self, screen: str, key: str, value):
        if screen != "presentation_a":
            return
