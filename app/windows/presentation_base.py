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

Subclasses provide `SCREEN_ID`, `SLIDES`, and `BOARD_CONTENT` — which of the two
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
    # Which board is this screen's own: the one that joins its rotation and the
    # one the picker lists. Either can still be pinned on either screen.
    BOARD_CONTENT: str = "diagnostics"

    _PAGE_NORMAL      = 0
    _PAGE_LUNCH       = 1
    _PAGE_JUDGES      = 2
    _PAGE_CAD         = 3
    _PAGE_CHECKLIST   = 4
    _PAGE_DIAGNOSTICS = 5
    _PAGE_ROBOT_INFO  = 6
    _PAGE_NEXT_MATCH  = 7
    _PAGE_ANALYSIS    = 8

    _CONTENT_PAGES = {
        "checklist":   _PAGE_CHECKLIST,
        "diagnostics": _PAGE_DIAGNOSTICS,
        "robot_info":  _PAGE_ROBOT_INFO,
        "next_match":  _PAGE_NEXT_MATCH,
        "analysis":    _PAGE_ANALYSIS,
    }

    BOARD_LABELS = {
        "diagnostics": Slide(
            kind=BOARD, eyebrow="Live board", title="Robot Diagnostics",
            body="Battery, current, temperature and latched faults from the "
                 "last imported log."),
        "robot_info": Slide(
            kind=BOARD, eyebrow="Live board", title="Robot Info",
            body="What tripped, on which motor, and which log it came from."),
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
        rotation.advance.connect(self._on_rotation_advance)
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
        The standard rotation: this screen's own slides, then any fun facts
        generated from imported robot logs.

        Facts are read once, at construction. Importing a new log while a
        presentation screen is open will not change what it shows until the
        screen is power-cycled from the control sidebar — the same way judges
        slides need a Reload. `reload_slides()` does it without a restart.
        """
        slides = coerce(cls.SLIDES)
        try:
            from app.robot.fun_facts import slides as fact_slides
            slides += coerce(fact_slides())
        except Exception:
            # A malformed or partially-imported log must never stop the
            # audience screens from coming up.
            pass
        return slides

    @classmethod
    def board_entry(cls) -> Slide | None:
        """
        This screen's board as the picker should list it, or None when there is
        nothing imported for it to show.
        """
        try:
            from app.robot.diagnostics import latest_session_id
            if latest_session_id() is None:
                return None
        except Exception:
            return None
        return cls.BOARD_LABELS.get(cls.BOARD_CONTENT)

    @classmethod
    def rotation_entries(cls) -> list[Slide]:
        """
        Every stop in the cycle, slides *and* the board, in order.

        The control screen's picker lists exactly this, so the index it writes
        to `slide_index` means the same thing on both sides.
        """
        entries = list(cls.rotation_slides())
        board = cls.board_entry()
        if board is not None:
            entries.append(board)
        return entries

    def _board_index(self) -> int | None:
        """Cycle position of the board, or None when it is not in the cycle."""
        return self._board_idx

    def reload_slides(self) -> None:
        """
        Rebuild the rotation, picking up newly imported robot logs.

        The two boards are not touched here — they subscribe to
        `config.logs_changed` themselves, so calling them would reload each of
        them twice.
        """
        self._board_idx = (len(self.rotation_slides())
                           if self.board_entry() is not None else None)
        new = SlidePanel(self.rotation_slides(), self.SCREEN_ID)
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
        # rotation is `BOARD_CONTENT`; either can still be pinned on either
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

        self._board_idx = (len(self.rotation_slides())
                           if self.board_entry() is not None else None)

        self._stack.setCurrentIndex(self._standard_page())

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

    def _board_page(self) -> int:
        """The stack page for this screen's own board."""
        return (self._PAGE_ROBOT_INFO if self.BOARD_CONTENT == "robot_info"
                else self._PAGE_DIAGNOSTICS)

    def _standard_page(self) -> int:
        """Which page standard mode means for this screen right now."""
        content = config.get(self.SCREEN_ID, "content", "rotation")
        return self._CONTENT_PAGES.get(content, self._PAGE_NORMAL)

    def _on_board(self) -> bool:
        """True when the cycle is currently parked on the board."""
        return (config.get(self.SCREEN_ID, "content", "rotation") == "rotation"
                and self._stack.currentIndex() == self._board_page())

    def _on_rotation_advance(self):
        # A pinned page — checklist or either board — is not a slide. The
        # 45-second timer must not walk off it while the crew is working.
        if config.mode != "standard" or self._standard_page() != self._PAGE_NORMAL:
            return

        board = self._board_index()
        if self._on_board():
            # The board is the last stop; the cycle wraps from it to slide 0.
            self._show_slide_page(0)
        elif board is not None and self._slides.current_index >= board - 1:
            self._show_board()
        else:
            self._slides.next_slide()

    def _show_board(self):
        """Put this screen's board on, as a stop in the cycle."""
        self._stack.setCurrentIndex(self._board_page())
        idx = self._board_index()
        if idx is not None:
            config.set(self.SCREEN_ID, "slide_index", idx)

    def _show_slide_page(self, index: int):
        self._slides.set_slide(index)
        self._stack.setCurrentIndex(self._PAGE_NORMAL)
        config.set(self.SCREEN_ID, "slide_index", index)

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
                self._slides.reset()
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
            # The board is the cycle position past the last slide, so a jump to
            # it arrives through the same key as any other slide.
            board = self._board_index()
            if board is not None and int(value) == board:
                if config.get(self.SCREEN_ID, "content", "rotation") == "rotation":
                    self._stack.setCurrentIndex(self._board_page())
                return
            self._slides.set_slide(int(value))
            if (config.mode == "standard" and self._on_board()):
                self._stack.setCurrentIndex(self._PAGE_NORMAL)
            return
        if key == "content":
            if config.mode == "standard":
                page = self._standard_page()
                if page == self._PAGE_NORMAL:
                    self._slides.reset()
                self._stack.setCurrentIndex(page)
            return
        if key == "checklist_id":
            self._checklist.set_list(self._configured_list_id())
            return
        if key == "theme":
            apply_theme(self, value)
            self._cad_view.set_theme(value)
