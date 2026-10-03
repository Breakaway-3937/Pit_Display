"""
AppConfig — master config object.

`config` is a lazy proxy: safe to import at module level anywhere.
Call `init_config()` once after QApplication is created (in main.py);
all attribute access on `config` then forwards to the real object.

Usage anywhere:
    from app.config import config
    config.team_changed.connect(my_slot)
    config.set_team(3937)
"""

from PyQt6.QtCore import QObject, pyqtSignal

from app.lazy_proxy import LazyProxy
from app.teams import Team, get_team, all_teams

SCREENS = ["presentation_a", "presentation_b", "project", "control"]
MODES   = ["standard", "judges", "lunch"]

SCREEN_LABELS = {
    "presentation_a": "Presentation A",
    "presentation_b": "Presentation B",
    "project":        "Project",
    "control":        "Control",
}

MODE_LABELS = {
    "standard": "Standard",
    "judges":   "Judges",
    "lunch":    "Lunch",
}


class _AppConfig(QObject):

    # Emitted when the active team changes — all screens should re-brand
    team_changed = pyqtSignal(object)  # Team

    # Emitted when the display mode changes — screens adjust behavior accordingly
    mode_changed = pyqtSignal(str)     # "standard" | "judges" | "lunch"

    # Emitted when any per-screen setting changes
    # args: screen_id (str), key (str), value (object)
    screen_setting_changed = pyqtSignal(str, str, object)

    # Emitted when a robot log is imported or deleted. App-global state that
    # several screens react to — the slide rotation regenerates its fun facts,
    # the diagnostics boards re-read the database, the control screen's slide
    # picker rebuilds. It lives here, beside team and mode, rather than in a
    # tenth singleton created for one signal.
    logs_changed = pyqtSignal()
    # Admin-edited screen wording changed (app/wording.py): the pit-front panel
    # updates its labels in place, the overhead slides rebuild.
    wording_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._active_team: Team = get_team(3937) or all_teams()[0]
        self._mode: str = "standard"
        self._screen_settings: dict[str, dict] = {
            screen: {"theme": "dark"} for screen in SCREENS
        }

    # ── Team ─────────────────────────────────────────────────────────────

    @property
    def active_team(self) -> Team:
        return self._active_team

    def set_team(self, team_number: int) -> None:
        team = get_team(team_number)
        if team is None or team.number == self._active_team.number:
            return
        self._active_team = team
        self.team_changed.emit(team)

    # ── Mode ─────────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode not in MODES or mode == self._mode:
            return
        self._mode = mode
        self.mode_changed.emit(mode)

    # ── Per-screen settings ───────────────────────────────────────────────

    def get(self, screen: str, key: str, default=None):
        return self._screen_settings.get(screen, {}).get(key, default)

    def set(self, screen: str, key: str, value) -> None:
        if screen not in self._screen_settings:
            self._screen_settings[screen] = {}
        if self._screen_settings[screen].get(key) == value:
            return
        self._screen_settings[screen][key] = value
        self.screen_setting_changed.emit(screen, key, value)

    def screen_theme(self, screen: str) -> str:
        return self.get(screen, "theme", "dark")

    # ── Robot logs ────────────────────────────────────────────────────────

    def notify_logs_changed(self) -> None:
        """Tell every screen the imported-log set has changed."""
        self.logs_changed.emit()

    def notify_wording_changed(self) -> None:
        """Tell every screen an admin edited its wording (app/wording.py)."""
        self.wording_changed.emit()


config: _AppConfig = LazyProxy("config", "init_config")  # type: ignore[assignment]


def init_config() -> _AppConfig:
    """Call exactly once in main(), after QApplication is created."""
    real = _AppConfig()
    config._install(real)
    return real
