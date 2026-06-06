"""Presentation Screen B — shell. Switches to lunch overlay when mode is Lunch."""

from PyQt6.QtWidgets import QMainWindow, QStackedWidget
from PyQt6.QtCore import Qt

from app.config import config
from app.rotation import rotation
from app.widgets.lunch_overlay import LunchOverlay
from app.widgets.judges_overlay import JudgesOverlay
from app.widgets.slide_panel import SlidePanel


_SLIDES = [
    (
        "Our Design Process",
        "Every mechanism starts with student-led research, prototyping, and iteration.",
    ),
    (
        "Community Outreach",
        "Beyond the field — workshops, demos, and inspiring the next generation.",
    ),
    (
        "Thank You, Sponsors",
        "None of this is possible without the support of our incredible partners.",
    ),
]


class PresentationScreenB(QMainWindow):

    _PAGE_NORMAL  = 0
    _PAGE_LUNCH   = 1
    _PAGE_JUDGES  = 2

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pit Display — Presentation B")
        self.setMinimumSize(320, 240)
        self._build_ui()
        config.team_changed.connect(self._on_team_changed)
        config.mode_changed.connect(self._on_mode_changed)
        config.screen_setting_changed.connect(self._on_setting_changed)
        rotation.advance.connect(self._on_rotation_advance)

    def _build_ui(self):
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        # Page 0 — rotating slides
        self._slides = SlidePanel(_SLIDES)
        self._stack.addWidget(self._slides)

        # Page 1 — lunch overlay
        self._lunch = LunchOverlay(screen_id="presentation_b")
        self._stack.addWidget(self._lunch)

        # Page 2 — judges overlay
        self._judges = JudgesOverlay(screen_id="presentation_b")
        self._stack.addWidget(self._judges)

        self._stack.setCurrentIndex(self._PAGE_NORMAL)

    def _on_rotation_advance(self):
        if config.mode == "standard":
            self._slides.next_slide()

    def _on_mode_changed(self, mode: str):
        if mode == "lunch":
            self._stack.setCurrentIndex(self._PAGE_LUNCH)
            self._lunch.apply_fonts()
        elif mode == "judges":
            self._stack.setCurrentIndex(self._PAGE_JUDGES)
        else:
            self._slides.reset()
            self._stack.setCurrentIndex(self._PAGE_NORMAL)

    def _on_team_changed(self, team):
        pass

    def _on_setting_changed(self, screen: str, key: str, value):
        if screen != "presentation_b":
            return
