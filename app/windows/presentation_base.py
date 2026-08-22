"""
Shared base for the two audience-facing presentation screens.

Both presentation windows are identical in behaviour — a QStackedWidget that
swaps between rotating slides, the lunch overlay, the judges slide overlay, and
the judges CAD viewer based on the global display mode. They differ only in
their screen id and their default slide copy. This base owns all the wiring so
"follow screen commands" (theme + team + mode) lives in exactly one place.

Subclasses provide `SCREEN_ID` and `SLIDES`.
"""

from PyQt6.QtWidgets import QMainWindow, QStackedWidget

from app.cad_assets import cad_assets
from app.config import config, SCREEN_LABELS
from app.rotation import rotation
from app.theme import apply_theme
from app.widgets.cad_viewer import CADViewerWidget
from app.widgets.lunch_overlay import LunchOverlay
from app.widgets.judges_overlay import JudgesOverlay
from app.widgets.slide_panel import SlidePanel


class PresentationScreen(QMainWindow):
    """Base window; subclasses set SCREEN_ID / SLIDES / window title."""

    SCREEN_ID: str = ""
    SLIDES: list[tuple[str, str]] = []

    _PAGE_NORMAL = 0
    _PAGE_LUNCH  = 1
    _PAGE_JUDGES = 2
    _PAGE_CAD    = 3

    def __init__(self):
        super().__init__()
        self.setWindowTitle(
            f"Pit Display — {SCREEN_LABELS.get(self.SCREEN_ID, self.SCREEN_ID)}"
        )
        self.setMinimumSize(320, 240)
        self._build_ui()

        # Honour the operator's commands: team, mode, and per-screen settings.
        config.team_changed.connect(self._on_team_changed)
        config.mode_changed.connect(self._on_mode_changed)
        config.screen_setting_changed.connect(self._on_setting_changed)
        rotation.advance.connect(self._on_rotation_advance)
        cad_assets.cad_active_changed.connect(self._on_cad_active_changed)
        cad_assets.subsystem_focused.connect(self._on_subsystem_focused)
        cad_assets.model_changed.connect(self._cad_view.reload_model)

        # Apply whatever theme was already selected for this screen.
        apply_theme(self, config.screen_theme(self.SCREEN_ID))

    @classmethod
    def rotation_slides(cls) -> list[tuple[str, str]]:
        """
        The standard rotation: this screen's own slides, then any fun facts
        generated from imported robot logs.

        Facts are read once, at construction. Importing a new log while a
        presentation screen is open will not change what it shows until the
        screen is power-cycled from the control sidebar — the same way judges
        slides need a Reload. `reload_slides()` does it without a restart.
        """
        slides = list(cls.SLIDES)
        try:
            from app.robot.fun_facts import slides as fact_slides
            slides += fact_slides()
        except Exception:
            # A malformed or partially-imported log must never stop the
            # audience screens from coming up.
            pass
        return slides

    def reload_slides(self) -> None:
        """Rebuild the rotation, picking up newly imported robot logs."""
        new = SlidePanel(self.rotation_slides())
        old = self._slides
        self._stack.insertWidget(self._PAGE_NORMAL, new)
        self._stack.removeWidget(old)
        old.deleteLater()
        self._slides = new
        new.slide_changed.connect(self._on_slide_changed)
        new.apply_team(config.active_team)
        if self._stack.currentIndex() == self._PAGE_NORMAL:
            self._stack.setCurrentIndex(self._PAGE_NORMAL)

    def _build_ui(self):
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._slides = SlidePanel(self.rotation_slides())
        # Report every move so the control screen's picker can follow along.
        self._slides.slide_changed.connect(self._on_slide_changed)
        self._stack.addWidget(self._slides)                 # 0

        self._lunch = LunchOverlay(screen_id=self.SCREEN_ID)
        self._stack.addWidget(self._lunch)                  # 1

        self._judges = JudgesOverlay(screen_id=self.SCREEN_ID)
        self._stack.addWidget(self._judges)                 # 2

        self._cad_view = CADViewerWidget()
        self._stack.addWidget(self._cad_view)               # 3

        self._stack.setCurrentIndex(self._PAGE_NORMAL)

    # ── Mode / rotation ───────────────────────────────────────────────────

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

    # ── Team / theme commands ─────────────────────────────────────────────

    def _on_team_changed(self, team):
        # Slides re-brand here; overlays self-subscribe to team_changed.
        self._slides.apply_team(team)
        self._cad_view.set_accent(team.primary_color)

    def _on_slide_changed(self, index: int):
        """
        Publish the current slide so the control screen can highlight it.

        Safe against a feedback loop: config.set() ignores an unchanged value
        and SlidePanel.set_slide() ignores a re-select, so the round trip
        terminates on the first pass.
        """
        config.set(self.SCREEN_ID, "slide_index", index)

    def _on_setting_changed(self, screen: str, key: str, value):
        if screen != self.SCREEN_ID:
            return
        if key == "slide_index":
            self._slides.set_slide(int(value))
            return
        if key == "theme":
            apply_theme(self, value)
            self._cad_view.set_theme(value)
