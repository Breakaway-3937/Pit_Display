"""
Shared base for the two audience-facing presentation screens.

Both presentation windows are identical in behaviour — a QStackedWidget that
swaps between rotating slides, the lunch overlay, the judges slide overlay, and
the judges CAD viewer based on the global display mode. They differ only in
their screen id and their default slide copy. This base owns all the wiring so
"follow screen commands" (theme + team + mode) lives in exactly one place.

**An alert banner lies along the bottom of every face** (`QueueBanner`): the
first and second queue calls in the alliance colour, and inspection passed in
green, driven by `app/nexus/alerts.py`. It is a child of the window rather
than a page, so it needs no cooperation from whatever is showing.

**Standard mode has five faces**, chosen per screen by the `content` setting
rather than by the global mode:

| `content` | shows |
|---|---|
| `"rotation"` (default) | the slide cycle, and the diagnostics board as its last stop |
| `"checklist"` | the pit checklist, pinned |
| `"diagnostics"` | the diagnostics board, pinned |
| `"robot_info"` | the robot-info board, pinned |
| `"analysis"` | the newest analysis board (app/ai/), pinned |
| `"next_match"` | the next-match board off the Nexus feed, pinned |

Per-screen and not more global modes, on purpose — the useful arrangement in a
pit is one overhead screen pinned to diagnostics while the other keeps rotating
for visitors, which a global mode cannot express. Judges and lunch still take
over both screens, because those are whole-pit states.

**The board is both a rotation stop and a pin.** It sits at the end of the
cycle as slide *n*, so visitors see it come round; pinning it with `content`
parks it there and the 45-second timer stops touching the screen. One widget
serves both — `_BOARD_INDEX` is what makes the two agree, and `slide_index`
carries the board position like any other slide so the control-screen picker
can jump to it.

**It only joins the cycle when there is a log to show.** An empty "no log
imported" board appearing in front of visitors every 45 seconds is worse than
no board, so `_cycle_len()` asks the database first.

Subclasses provide `SCREEN_ID` and `SLIDES` (authored slides; `app/program.py` pairs them). Formerly also `BOARD_CONTENT` — which of the two
boards is this screen's own.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMainWindow, QStackedWidget

from app.cad_assets import cad_assets
from app.checklist import checklist
from app.config import config, SCREEN_LABELS
from app.rotation import rotation
from app.slides import Slide, BOARD, coerce
from app.theme import apply_theme
from app.widgets.cad_viewer import CADViewerWidget
from app.widgets.checklist_overlay import ChecklistOverlay
from app.widgets.analysis_overlay import AnalysisOverlay
from app.widgets.facts_overlay import FactsOverlay
from app.widgets.schedule_overlay import ScheduleOverlay
from app.widgets.quality_overlay import QualityOverlay
from app.widgets.seasons_overlay import SeasonsOverlay
from app.widgets.dataset_overlay import DatasetOverlay
from app.widgets.diagnostics_overlay import DiagnosticsOverlay
from app.widgets.robot_info_overlay import RobotInfoOverlay
from app.widgets.lunch_overlay import LunchOverlay
from app.widgets.next_match_overlay import NextMatchOverlay
from app.widgets.judges_overlay import JudgesOverlay
from app.widgets.queue_banner import QueueBanner
from app.widgets.slide_panel import SlidePanel


class PresentationScreen(QMainWindow):
    """Base window; subclasses set SCREEN_ID / SLIDES / window title."""

    SCREEN_ID: str = ""
    SLIDES: list[Slide] = []

    _PAGE_NORMAL      = 0
    _PAGE_LUNCH       = 1
    _PAGE_JUDGES      = 2
    _PAGE_CAD         = 3
    _PAGE_CHECKLIST   = 4
    _PAGE_DIAGNOSTICS = 5
    _PAGE_ROBOT_INFO  = 6
    _PAGE_NEXT_MATCH  = 7
    _PAGE_ANALYSIS    = 8
    _PAGE_FACTS       = 9
    _PAGE_SCHEDULE    = 10
    _PAGE_QUALITY     = 11
    _PAGE_SEASONS     = 12
    _PAGE_DATASET     = 13

    _CONTENT_PAGES = {
        "checklist":   _PAGE_CHECKLIST,
        "diagnostics": _PAGE_DIAGNOSTICS,
        "robot_info":  _PAGE_ROBOT_INFO,
        "next_match":  _PAGE_NEXT_MATCH,
        "analysis":    _PAGE_ANALYSIS,
        "facts":       _PAGE_FACTS,
        "schedule":    _PAGE_SCHEDULE,
        "quality":     _PAGE_QUALITY,
        "bk_seasons":  _PAGE_SEASONS,
        "dataset":     _PAGE_DATASET,
    }


    def __init__(self):
        super().__init__()
        self.setWindowTitle(
            f"Pit Display — {SCREEN_LABELS.get(self.SCREEN_ID, self.SCREEN_ID)}"
        )
        self.setMinimumSize(320, 240)
        # An audience screen has no input widget on it — no field, no button,
        # nothing to type into. Saying so to the window manager means it can
        # never take the keyboard from the operator's panel, which is stronger
        # than handing activation back after the fact: there is no window in
        # which the control screen has lost it.
        #
        # The **project** screen deliberately does not get this. It is a touch
        # panel visitors drive, and the CAD web view wants ordinary focus.
        self.setWindowFlag(
            Qt.WindowType.WindowDoesNotAcceptFocus, True)
        self._build_ui()

        # Honour the operator's commands: team, mode, and per-screen settings.
        config.team_changed.connect(self._on_team_changed)
        config.mode_changed.connect(self._on_mode_changed)
        config.screen_setting_changed.connect(self._on_setting_changed)
        # The program (app/program.py) moves both screens together through
        # `slide_index`; a screen never advances itself. When its stops change
        # (a log, an event, facts arriving), rebuild.
        rotation.program_changed.connect(self.reload_slides)
        # Importing a log mid-day regenerates the fun-fact slides and re-reads
        # both boards, with no power-cycle. This is what `reload_slides()` was
        # written for.
        config.logs_changed.connect(self.reload_slides)
        cad_assets.cad_active_changed.connect(self._on_cad_active_changed)
        cad_assets.subsystem_focused.connect(self._on_subsystem_focused)
        cad_assets.model_changed.connect(self._cad_view.reload_model)

        # Apply whatever theme was already selected for this screen.
        apply_theme(self, config.screen_theme(self.SCREEN_ID))

    @classmethod
    def rotation_slides(cls) -> list[Slide]:
        """
        This screen's half of the overhead program (`app/program.py`), in
        order: slides, and stops that show a whole face (`Slide.face`). The
        same length on both screens, which is what keeps them paired.
        """
        try:
            from app import program
            return program.side(cls.SCREEN_ID)
        except Exception:
            # Bad data must never stop the audience screens from coming up.
            return coerce(cls.SLIDES)

    @classmethod
    def rotation_entries(cls) -> list[Slide]:
        """Every stop, as the control screen's picker lists it."""
        return cls.rotation_slides()

    def reload_slides(self) -> None:
        """
        Rebuild the rotation, picking up newly imported robot logs.

        The two boards are not touched here — they subscribe to
        `config.logs_changed` themselves, so calling them would reload each of
        them twice.
        """
        new = SlidePanel(self.rotation_slides(), self.SCREEN_ID)
        old = self._slides
        self._stack.insertWidget(self._PAGE_NORMAL, new)
        self._stack.removeWidget(old)
        old.deleteLater()
        self._slides = new
        new.slide_changed.connect(self._on_slide_changed)
        new.apply_team(config.active_team)
        new.set_slide(int(config.get(self.SCREEN_ID, "slide_index", 0) or 0))
        if config.mode == "standard" and self._standard_page() == self._PAGE_NORMAL:
            self._sync_page()

    def _build_ui(self):
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._slides = SlidePanel(self.rotation_slides(), self.SCREEN_ID)
        # Report every move so the control screen's picker can follow along.
        self._slides.slide_changed.connect(self._on_slide_changed)
        self._stack.addWidget(self._slides)                 # 0

        self._lunch = LunchOverlay(screen_id=self.SCREEN_ID)
        self._stack.addWidget(self._lunch)                  # 1

        self._judges = JudgesOverlay(screen_id=self.SCREEN_ID)
        self._stack.addWidget(self._judges)                 # 2

        self._cad_view = CADViewerWidget()
        self._stack.addWidget(self._cad_view)               # 3

        self._checklist = ChecklistOverlay(screen_id=self.SCREEN_ID)
        self._checklist.set_list(self._configured_list_id())
        self._stack.addWidget(self._checklist)              # 4

        # Both boards exist on both screens. Which one joins *this* screen's
        # program pairs (app/program.py); either can still be pinned on either
        # screen, because a crew mid-debug should not have to care which panel
        # was configured for what.
        self._diagnostics = DiagnosticsOverlay(screen_id=self.SCREEN_ID)
        self._stack.addWidget(self._diagnostics)            # 5

        self._robot_info = RobotInfoOverlay(screen_id=self.SCREEN_ID)
        self._stack.addWidget(self._robot_info)             # 6

        # *When do we go?* — the next match off the Nexus feed, with a live
        # countdown to the next queue call. Pinned only; never in the rotation.
        self._next_match = NextMatchOverlay(screen_id=self.SCREEN_ID)
        self._stack.addWidget(self._next_match)             # 7

        # The newest analysis board (app/ai/): the local model's read of the
        # last log, on the diagnostics painter. Pinned only.
        self._analysis = AnalysisOverlay(screen_id=self.SCREEN_ID)
        self._stack.addWidget(self._analysis)               # 8

        # "Did you know?": home's fun facts from TBA's award history
        # (tba_fact). Pinned only, like the analysis board.
        self._facts = FactsOverlay(screen_id=self.SCREEN_ID)
        self._stack.addWidget(self._facts)                  # 9

        # Our schedule today (Nexus): Next Match's partner in the program.
        self._schedule = ScheduleOverlay(screen_id=self.SCREEN_ID)
        self._stack.addWidget(self._schedule)               # 10

        # Home's datasets (R10/R11): the Quality Award leaderboard, the
        # season-by-season chart, and every other dataset, generically.
        self._quality = QualityOverlay(screen_id=self.SCREEN_ID)
        self._stack.addWidget(self._quality)                # 11
        self._seasons = SeasonsOverlay(screen_id=self.SCREEN_ID)
        self._stack.addWidget(self._seasons)                # 12
        self._dataset = DatasetOverlay(screen_id=self.SCREEN_ID)
        self._stack.addWidget(self._dataset)                # 13

        self._stack.setCurrentIndex(self._standard_page())
        if self._standard_page() == self._PAGE_NORMAL:
            self._sync_page()

        # The queue / inspection banner lies over every page along the bottom
        # — a child of the window, not a page, so no face has to know. It is
        # placed by resizeEvent and shows itself when an alert is up.
        self._banner = QueueBanner(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._banner.place(self.width(), self.height())

    # ── Mode / rotation ───────────────────────────────────────────────────

    def _configured_list_id(self):
        """Which checklist this screen shows; the first one until told otherwise."""
        chosen = config.get(self.SCREEN_ID, "checklist_id")
        return int(chosen) if chosen else checklist.default_list_id()

    def _standard_page(self) -> int:
        """Which page standard mode means for this screen right now."""
        content = config.get(self.SCREEN_ID, "content", "rotation")
        return self._CONTENT_PAGES.get(content, self._PAGE_NORMAL)

    def _sync_page(self) -> None:
        """Show the current stop: a slide on the slide page, or the face the
        stop names (`Slide.face`) on its own page."""
        entry = self._slides.current_slide
        face = entry.face if entry is not None else ""
        self._stack.setCurrentIndex(self._CONTENT_PAGES.get(face, self._PAGE_NORMAL)
                                    if face else self._PAGE_NORMAL)

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
            # Coming back from judges or lunch restarts the cycle at slide 0
            # rather than resuming on the board it happened to be parked on.
            page = self._standard_page()
            if page == self._PAGE_NORMAL:
                self._sync_page()
            else:
                self._stack.setCurrentIndex(page)

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
            # The program's position (app/rotation.py sets both screens). A
            # pinned screen keeps following it, so it rejoins in step.
            self._slides.set_slide(int(value))
            if config.mode == "standard" and self._standard_page() == self._PAGE_NORMAL:
                self._sync_page()
            return
        if key == "content":
            if config.mode == "standard":
                page = self._standard_page()
                if page == self._PAGE_NORMAL:
                    self._sync_page()
                else:
                    self._stack.setCurrentIndex(page)
            return
        if key == "checklist_id":
            self._checklist.set_list(self._configured_list_id())
            return
        if key == "theme":
            apply_theme(self, value)
            self._cad_view.set_theme(value)
