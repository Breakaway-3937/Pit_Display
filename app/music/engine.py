"""
Audio playback engines.

`MusicEngine` is the seam. `VLCEngine` is the real one; `NullEngine` stands in
when the VLC runtime is not present so the app still boots and the panel still
renders — it just says so instead of failing at import.

Why libVLC rather than Qt Multimedia: QMediaPlayer has no DSP hooks at all, so
an equaliser is impossible through it. libVLC ships a real ten-band equaliser
(31Hz–16kHz, +/-20dB, plus preamp) that can be rebuilt and reapplied live.

Why the interface: if the team later wants tight beat-to-LED sync, that needs a
sounddevice+numpy pipeline where the same buffer feeds the EQ, the meters and
the LED frames. Keeping playback behind this protocol makes that a contained
swap rather than a rewrite.

Packaging note: on Windows the VLC runtime must be present — either install
VLC, or ship libvlc.dll plus the plugins directory alongside the app.
"""

from typing import Protocol, runtime_checkable

# python-vlc raises OSError (not ImportError) when the native runtime is
# missing, which is the common case on a machine without VLC installed.
try:
    import vlc
    HAVE_VLC = True
    VLC_ERROR = ""
except (ImportError, OSError) as exc:
    vlc = None
    HAVE_VLC = False
    VLC_ERROR = str(exc)


@runtime_checkable
class MusicEngine(Protocol):
    """Everything the service needs from a playback backend."""

    def play(self, path: str) -> None: ...
    def resume(self) -> None: ...
    def pause(self) -> None: ...
    def stop(self) -> None: ...
    def set_volume(self, pct: int) -> None: ...
    def volume(self) -> int: ...
    def position(self) -> float: ...
    def duration(self) -> float: ...
    def seek(self, seconds: float) -> None: ...
    def is_playing(self) -> bool: ...
    def finished(self) -> bool: ...
    def set_equalizer(self, preamp: float, gains: list[float]) -> bool: ...
    def release(self) -> None: ...


class NullEngine:
    """
    No audio backend. Every call is a no-op that keeps the app coherent.

    `available` is False so the panel can explain the situation rather than
    silently doing nothing when someone presses play.
    """

    available = False

    def __init__(self, reason: str = "No audio backend available."):
        self.reason = reason
        self._volume = 70

    def play(self, path: str) -> None: pass
    def resume(self) -> None: pass
    def pause(self) -> None: pass
    def stop(self) -> None: pass
    def set_volume(self, pct: int) -> None: self._volume = max(0, min(100, pct))
    def volume(self) -> int: return self._volume
    def position(self) -> float: return 0.0
    def duration(self) -> float: return 0.0
    def seek(self, seconds: float) -> None: pass
    def is_playing(self) -> bool: return False
    def finished(self) -> bool: return False
    def set_equalizer(self, preamp: float, gains: list[float]) -> bool: return False
    def release(self) -> None: pass


class VLCEngine:
    """libVLC-backed playback with a live ten-band equaliser."""

    available = True

    def __init__(self):
        # --no-video keeps libVLC from ever opening a window for a file that
        # happens to carry cover art or a video stream.
        self._instance = vlc.Instance("--no-video", "--quiet")
        if self._instance is None:
            raise RuntimeError("libVLC failed to create an instance")
        self._player = self._instance.media_player_new()
        self._eq = None
        self._pending_eq: tuple[float, list[float]] | None = None
        self._volume = 70
        self._player.audio_set_volume(self._volume)

    # ── Transport ─────────────────────────────────────────────────────────

    def play(self, path: str) -> None:
        media = self._instance.media_new_path(str(path))
        self._player.set_media(media)
        self._player.play()
        self._player.audio_set_volume(self._volume)
        # An equaliser set while no media was loaded may not have stuck; some
        # libVLC builds only accept it against a live player. Reapply now.
        if self._pending_eq is not None:
            preamp, gains = self._pending_eq
            self._apply_eq(preamp, gains)

    def resume(self) -> None:
        self._player.set_pause(0)

    def pause(self) -> None:
        self._player.set_pause(1)

    def stop(self) -> None:
        self._player.stop()

    def is_playing(self) -> bool:
        return bool(self._player.is_playing())

    def finished(self) -> bool:
        return self._player.get_state() == vlc.State.Ended

    # ── Volume / position ─────────────────────────────────────────────────

    def set_volume(self, pct: int) -> None:
        self._volume = max(0, min(100, int(pct)))
        self._player.audio_set_volume(self._volume)

    def volume(self) -> int:
        return self._volume

    def position(self) -> float:
        ms = self._player.get_time()
        return max(0.0, ms / 1000.0) if ms is not None and ms >= 0 else 0.0

    def duration(self) -> float:
        ms = self._player.get_length()
        return max(0.0, ms / 1000.0) if ms is not None and ms >= 0 else 0.0

    def seek(self, seconds: float) -> None:
        self._player.set_time(int(max(0.0, seconds) * 1000))

    # ── Equaliser ─────────────────────────────────────────────────────────

    def set_equalizer(self, preamp: float, gains: list[float]) -> bool:
        """
        Build a fresh equaliser and hand it to the player.

        Always a new object: libVLC takes a snapshot when the equaliser is
        applied, so mutating the previous one has no effect on the running
        player. Remembered as pending too, because some builds ignore an
        equaliser applied before any media is loaded.
        """
        self._pending_eq = (preamp, list(gains))
        return self._apply_eq(preamp, gains)

    def _apply_eq(self, preamp: float, gains: list[float]) -> bool:
        try:
            eq = vlc.AudioEqualizer()
            eq.set_preamp(float(preamp))
            for i, gain in enumerate(gains):
                eq.set_amp_at_index(float(gain), i)
            ok = self._player.set_equalizer(eq) == 0
            self._eq = eq        # hold a reference; libVLC does not own it
            return ok
        except Exception:
            return False

    def release(self) -> None:
        try:
            self._player.stop()
            self._player.release()
            self._instance.release()
        except Exception:
            pass


def make_engine() -> "MusicEngine":
    """The real engine when libVLC is present, otherwise a working stand-in."""
    if not HAVE_VLC:
        return NullEngine(
            "VLC runtime not found. Install VLC (or bundle libvlc) to enable "
            "playback and the equaliser."
        )
    try:
        return VLCEngine()
    except Exception as exc:
        return NullEngine(f"libVLC failed to start: {exc}")


def band_frequencies() -> list[float]:
    """The ten ISO band centres libVLC exposes, in Hz."""
    if HAVE_VLC:
        try:
            n = vlc.libvlc_audio_equalizer_get_band_count()
            freqs = [vlc.libvlc_audio_equalizer_get_band_frequency(i) for i in range(n)]
            if freqs and all(f and f > 0 for f in freqs):
                return [float(f) for f in freqs]
        except Exception:
            pass
    # libVLC's fixed ten-band layout — used when we cannot ask it directly.
    return [31.25, 62.5, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0]
