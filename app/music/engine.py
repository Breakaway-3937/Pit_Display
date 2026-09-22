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

**The VLC runtime ships inside the bundle on Windows**, and `_point_at_bundled
_vlc()` below is what makes python-vlc find it. Before that, a fresh pit
machine had music and the whole equaliser dead until somebody separately
downloaded VLC and picked the 64-bit build to match — an install step that is
invisible until an operator presses play, and the wrong-architecture version
fails exactly like no version at all.
"""

import os
import sys
from typing import Protocol, runtime_checkable


def _point_at_bundled_vlc() -> str:
    """
    Tell python-vlc about the libVLC we shipped, before it goes looking.

    python-vlc finds the native runtime at *import* time, so this has to run
    first — there is no second chance once `import vlc` has failed. Its Windows
    search order is `PYTHON_VLC_LIB_PATH`, then the registry key a VLC
    installer writes, then `PATH`; a machine with no VLC installed has none of
    the three. Setting the two variables puts the bundled copy at the front of
    that order without disturbing a machine where somebody *has* installed VLC
    and set them deliberately.

    `PYTHON_VLC_MODULE_PATH` is the one that is easy to forget and produces the
    strangest failure: `libvlc.dll` loads, `Instance()` returns, and every
    single `play()` is silent, because libVLC without its plugin directory has
    no audio output module to use. It is not an error — there is simply no
    codec and no sink.

    `add_dll_directory` is needed on top of both: `libvlc.dll` links against
    `libvlccore.dll` beside it, and since Python 3.8 Windows no longer searches
    the DLL's own folder for its dependencies.

    Returns a note for the log, or `""` when there was nothing to point at
    (every non-Windows machine, and any checkout).
    """
    if sys.platform != "win32":
        return ""                      # macOS/Linux resolve libvlc themselves
    try:
        from app import paths
        folder = paths.resource("vlc")
        lib = folder / "libvlc.dll"
        plugins = folder / "plugins"
        if not lib.exists():
            return ""                  # a checkout, or a build made without it
        os.environ.setdefault("PYTHON_VLC_LIB_PATH", str(lib))
        if plugins.is_dir():
            os.environ.setdefault("PYTHON_VLC_MODULE_PATH", str(plugins))
        os.add_dll_directory(str(folder))
        return f"using the bundled libVLC at {folder}"
    except (OSError, ImportError, AttributeError) as e:
        # A bundled runtime that will not load must never stop the app booting;
        # the import below then fails and NullEngine takes over, as it always did.
        return f"could not use the bundled libVLC ({type(e).__name__}: {e})"


VLC_RUNTIME_NOTE = _point_at_bundled_vlc()

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
