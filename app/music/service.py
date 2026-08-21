"""
Music service singleton — transport, queue, library and EQ in one place.

`music` is a lazy proxy: safe to import at module level anywhere. Call
`init_music()` once in main() after `init_db()` (it reads and seeds tables).

The service polls the engine on a QTimer rather than subscribing to libVLC
events, deliberately: libVLC fires its callbacks on its own threads, and
touching Qt widgets from there is a crash waiting for an audience. A 250ms
poll on the GUI thread is plenty for a progress bar and end-of-track.
"""

from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from app.config import config
from app.lazy_proxy import LazyProxy
from app.music import eq as eq_module
from app.music import library
from app.music.engine import make_engine
from app.music.library import Track
from app.music.sources import LocalSource, SpotifySource

_POLL_MS = 250

# Music can only get so loud regardless of where the slider is. Pit noise is
# capped at events, and an amp is easier to damage than to replace.
DEFAULT_VOLUME_CAP = 85


class _MusicService(QObject):

    now_playing_changed = pyqtSignal(object)   # Track | None
    state_changed = pyqtSignal()               # playing/paused/volume/duck
    queue_changed = pyqtSignal()
    library_changed = pyqtSignal()
    eq_changed = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._engine = make_engine()
        self._sources = {s.key: s for s in (LocalSource(), SpotifySource())}
        self._source_key = "local"

        self._queue: list[Track] = []
        self._index = -1
        self._current: Track | None = None

        self._volume = 60
        self._volume_cap = DEFAULT_VOLUME_CAP
        self._ducked = False
        self._duck_level = 20
        self._follow_mode = True
        self._repeat = False
        self._shuffle = False

        self._eq_preset = "Pit Default"
        self._eq_preamp = 0.0
        self._eq_gains = [0.0] * eq_module.N_BANDS

        eq_module.ensure_seeded()
        self.apply_eq_preset(self._eq_preset, manual=False)
        self._engine.set_volume(self._effective_volume())

        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

        config.mode_changed.connect(self._on_mode_changed)

    # ── Engine status ─────────────────────────────────────────────────────

    @property
    def engine_available(self) -> bool:
        return getattr(self._engine, "available", False)

    @property
    def engine_note(self) -> str:
        return getattr(self._engine, "reason", "")

    @property
    def source(self):
        return self._sources[self._source_key]

    def sources(self) -> list:
        return list(self._sources.values())

    # ── Transport ─────────────────────────────────────────────────────────

    @property
    def current(self) -> Track | None:
        return self._current

    @property
    def playing(self) -> bool:
        return self._engine.is_playing()

    def play_track(self, track: Track) -> None:
        path = self.source.resolve(track)
        if path is None:
            self.error.emit(f"File is missing: {Path(track.path).name}")
            return
        if not self.engine_available:
            self.error.emit(self.engine_note)
            return
        self._current = track
        self._engine.play(path)
        self._engine.set_volume(self._effective_volume())
        self.now_playing_changed.emit(track)
        self.state_changed.emit()

    def play_queue_at(self, index: int) -> None:
        if not (0 <= index < len(self._queue)):
            return
        self._index = index
        self.play_track(self._queue[index])
        self.queue_changed.emit()

    def toggle_play(self) -> None:
        if self._current is None:
            if self._queue:
                self.play_queue_at(max(0, self._index))
            return
        if self._engine.is_playing():
            self._engine.pause()
        else:
            self._engine.resume()
        self.state_changed.emit()

    def stop(self) -> None:
        self._engine.stop()
        self._current = None
        self.now_playing_changed.emit(None)
        self.state_changed.emit()

    def next_track(self) -> None:
        if not self._queue:
            return
        if self._shuffle and len(self._queue) > 1:
            import random
            choices = [i for i in range(len(self._queue)) if i != self._index]
            self.play_queue_at(random.choice(choices))
            return
        nxt = self._index + 1
        if nxt >= len(self._queue):
            if not self._repeat:
                self.stop()
                return
            nxt = 0
        self.play_queue_at(nxt)

    def previous_track(self) -> None:
        if not self._queue:
            return
        # Standard behaviour: restart the track unless we are near its start.
        if self._engine.position() > 3.0:
            self._engine.seek(0)
            return
        self.play_queue_at(self._index - 1 if self._index > 0 else len(self._queue) - 1)

    def seek(self, seconds: float) -> None:
        self._engine.seek(seconds)
        self.state_changed.emit()

    def position(self) -> float:
        return self._engine.position()

    def duration(self) -> float:
        d = self._engine.duration()
        if d <= 0 and self._current is not None:
            return self._current.duration
        return d

    # ── Volume, cap and duck ──────────────────────────────────────────────

    @property
    def volume(self) -> int:
        return self._volume

    @property
    def volume_cap(self) -> int:
        return self._volume_cap

    @property
    def ducked(self) -> bool:
        return self._ducked

    def _effective_volume(self) -> int:
        base = min(self._volume, self._volume_cap)
        return int(base * self._duck_level / 100) if self._ducked else base

    def set_volume(self, pct: int) -> None:
        self._volume = max(0, min(100, int(pct)))
        self._engine.set_volume(self._effective_volume())
        self.state_changed.emit()

    def set_volume_cap(self, pct: int) -> None:
        self._volume_cap = max(0, min(100, int(pct)))
        self._engine.set_volume(self._effective_volume())
        self.state_changed.emit()

    def set_ducked(self, on: bool) -> None:
        """One tap for when a judge or a queuer walks up mid-song."""
        if on == self._ducked:
            return
        self._ducked = on
        self._engine.set_volume(self._effective_volume())
        self.state_changed.emit()

    def toggle_duck(self) -> None:
        self.set_ducked(not self._ducked)

    # ── Queue ─────────────────────────────────────────────────────────────

    @property
    def queue(self) -> list[Track]:
        return list(self._queue)

    @property
    def queue_index(self) -> int:
        return self._index

    def set_queue(self, tracks: list[Track], start: int = 0) -> None:
        self._queue = list(tracks)
        self._index = -1
        self.queue_changed.emit()
        if self._queue:
            self.play_queue_at(max(0, min(start, len(self._queue) - 1)))

    def enqueue(self, track: Track) -> None:
        self._queue.append(track)
        self.queue_changed.emit()

    def remove_from_queue(self, index: int) -> None:
        if not (0 <= index < len(self._queue)):
            return
        self._queue.pop(index)
        if index < self._index:
            self._index -= 1
        elif index == self._index:
            self._index = min(self._index, len(self._queue) - 1)
        self.queue_changed.emit()

    def clear_queue(self) -> None:
        self._queue.clear()
        self._index = -1
        self.queue_changed.emit()

    @property
    def repeat(self) -> bool:
        return self._repeat

    def set_repeat(self, on: bool) -> None:
        self._repeat = on
        self.state_changed.emit()

    @property
    def shuffle(self) -> bool:
        return self._shuffle

    def set_shuffle(self, on: bool) -> None:
        self._shuffle = on
        self.state_changed.emit()

    # ── Library ───────────────────────────────────────────────────────────

    def tracks(self, search: str = "") -> list[Track]:
        return self.source.tracks(search=search)

    def scan_folder(self, folder: Path) -> tuple[int, int]:
        found, added = library.scan(Path(folder))
        self.library_changed.emit()
        return found, added

    # ── Equaliser ─────────────────────────────────────────────────────────

    @property
    def eq_preset(self) -> str:
        return self._eq_preset

    @property
    def eq_gains(self) -> list[float]:
        return list(self._eq_gains)

    @property
    def eq_preamp(self) -> float:
        return self._eq_preamp

    @property
    def follow_mode(self) -> bool:
        return self._follow_mode

    def set_follow_mode(self, on: bool) -> None:
        self._follow_mode = on
        if on:
            self.apply_eq_preset(
                eq_module.MODE_PRESETS.get(config.mode, "Pit Default"), manual=False
            )
        self.state_changed.emit()

    def apply_eq_preset(self, name: str, *, manual: bool = True) -> None:
        preset = eq_module.get_preset(name)
        if preset is None:
            return
        if manual:
            self._follow_mode = False
        self._eq_preset = preset.name
        self._eq_preamp = preset.preamp
        self._eq_gains = preset.clamped()
        self._push_eq()
        self.eq_changed.emit()

    def set_band(self, index: int, gain: float) -> None:
        if not (0 <= index < eq_module.N_BANDS):
            return
        self._eq_gains[index] = max(eq_module.GAIN_MIN,
                                    min(eq_module.GAIN_MAX, float(gain)))
        self._push_eq()
        self.eq_changed.emit()

    def set_preamp(self, value: float) -> None:
        self._eq_preamp = max(eq_module.GAIN_MIN,
                              min(eq_module.GAIN_MAX, float(value)))
        self._push_eq()
        self.eq_changed.emit()

    def save_eq_as(self, name: str) -> None:
        eq_module.save_preset(
            eq_module.EQPreset(name, self._eq_preamp, list(self._eq_gains))
        )
        self._eq_preset = name if name not in eq_module.BUILT_IN_NAMES \
            else f"{name} (edited)"
        self.eq_changed.emit()

    def _push_eq(self) -> bool:
        return self._engine.set_equalizer(self._eq_preamp, self._eq_gains)

    # ── Polling ───────────────────────────────────────────────────────────

    def _poll(self) -> None:
        if self._current is not None and self._engine.finished():
            self.next_track()
        elif self._current is not None:
            self.state_changed.emit()

    # ── App integration ───────────────────────────────────────────────────

    def _on_mode_changed(self, mode: str) -> None:
        """Judges mode ducks the pit automatically — nobody has to remember."""
        self.set_ducked(mode == "judges")
        if self._follow_mode:
            self.apply_eq_preset(eq_module.MODE_PRESETS.get(mode, "Pit Default"),
                                 manual=False)

    def shutdown(self) -> None:
        self._timer.stop()
        self._engine.release()


music: _MusicService = LazyProxy("music", "init_music")  # type: ignore[assignment]


def init_music() -> _MusicService:
    """Call once in main(), after init_config() and init_db()."""
    real = _MusicService()
    music._install(real)
    return real
