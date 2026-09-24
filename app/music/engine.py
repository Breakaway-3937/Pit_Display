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
import ctypes
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

        # ── The analyser's shadow decoder ─────────────────────────────────
        # A second player, on the same file, whose audio goes to a callback
        # instead of a sound card. **The real player above is untouched**, and
        # that is the entire reason it is built this way: libVLC's audio
        # callbacks *replace* the output, so feeding the analyser from the
        # player the pit is listening to would mean re-rendering the audio
        # ourselves — putting the one subsystem that simply has to work behind
        # new code. Decoding the file twice costs a percent or two of a core
        # and cannot make the music stop.
        self._analyser = None
        self._shadow = None
        self._shadow_path = ""
        self._shadow_cbs = ()      # kept alive: see _attach_shadow()
        self._current_path = ""

    # ── Transport ─────────────────────────────────────────────────────────

    def play(self, path: str) -> None:
        media = self._instance.media_new_path(str(path))
        self._player.set_media(media)
        self._player.play()
        self._player.audio_set_volume(self._volume)
        self._current_path = str(path)
        # An equaliser set while no media was loaded may not have stuck; some
        # libVLC builds only accept it against a live player. Reapply now.
        if self._pending_eq is not None:
            preamp, gains = self._pending_eq
            self._apply_eq(preamp, gains)
        self._shadow_play(str(path))

    def resume(self) -> None:
        self._player.set_pause(0)
        if self._shadow is not None:
            self._shadow.set_pause(0)

    def pause(self) -> None:
        self._player.set_pause(1)
        if self._shadow is not None:
            self._shadow.set_pause(1)
        if self._analyser is not None:
            self._analyser.silence()

    def stop(self) -> None:
        self._player.stop()
        self._current_path = ""
        self._shadow_stop()

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
        if self._shadow is not None:
            self._shadow.set_time(int(max(0.0, seconds) * 1000))

    # ── The analyser's shadow decoder ─────────────────────────────────────

    def set_analyser(self, analyser) -> None:
        """
        Attach the band analyser, or pass None to stop decoding for it.

        Turning it on starts a second decode of whatever is playing; turning
        it off stops that and nothing else. The real player never learns this
        happened, which is the point.
        """
        previous = self._analyser
        self._analyser = analyser
        if analyser is None:
            self._shadow_stop()
            # Silenced *after* the reference is dropped, and explicitly,
            # because `_shadow_stop()` can only reach the analyser it still
            # knows about. Without this the bars freeze at their last values
            # when the display is turned off — which reads as live audio that
            # has stopped moving, the one thing a level meter must never do.
            if previous is not None:
                previous.silence()
        elif self._current_path:
            self._shadow_play(self._current_path)

    def _attach_shadow(self, player) -> None:
        """Point a player's audio at the analyser instead of a sound card."""
        rate, channels = 44100, 2

        @ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_uint, ctypes.c_int64)
        def _play(_data, samples, count, _pts):
            analyser = self._analyser
            if analyser is None or count <= 0:
                return
            try:
                buf = ctypes.cast(samples, ctypes.POINTER(ctypes.c_int16))
                # Interleaved S16 to floats, first channel only — the analyser
                # sums to mono anyway and this halves the work on the audio
                # thread, where overrunning the block costs the pit a glitch.
                frames = int(count)
                mono = [buf[i * channels] / 32768.0 for i in range(frames)]
                analyser.feed(mono, frames)
            except Exception:
                pass          # never let a display fault reach the decoder

        @ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int64)
        def _two(_a, _b):
            pass

        @ctypes.CFUNCTYPE(None, ctypes.c_void_p)
        def _one(_a):
            pass

        # **Held on the instance on purpose.** ctypes callbacks are garbage
        # collected like anything else, and libVLC keeps only the raw pointer;
        # letting these fall out of scope is a crash inside the decoder at
        # some unpredictable later moment.
        self._shadow_cbs = (_play, _two, _one)
        vlc.libvlc_audio_set_format(player, b"S16N", rate, channels)
        vlc.libvlc_audio_set_callbacks(player, _play, _two, _two, _two, _one,
                                       None)
        if self._analyser is not None:
            self._analyser.configure(rate, 1)

    def _shadow_play(self, path: str) -> None:
        if self._analyser is None or not path:
            return
        try:
            self._shadow_stop()
            player = self._instance.media_player_new()
            self._attach_shadow(player)
            player.set_media(self._instance.media_new_path(path))
            # The same equaliser as the real player, so what the display
            # measures is what the pit is hearing rather than the raw file.
            if self._pending_eq is not None:
                preamp, gains = self._pending_eq
                self._apply_eq(preamp, gains, player)
            player.play()
            player.set_time(self._player.get_time() or 0)
            self._shadow = player
            self._shadow_path = path
        except Exception:
            # A shadow that will not start means no analyser, never no music.
            self._shadow = None

    def _shadow_stop(self) -> None:
        player, self._shadow = self._shadow, None
        self._shadow_path = ""
        if player is not None:
            try:
                player.stop()
                player.release()
            except Exception:
                pass
        if self._analyser is not None:
            self._analyser.silence()

    def resync_analyser(self, tolerance_ms: int = 350) -> None:
        """
        Nudge the shadow back into step if it has drifted.

        **Measured: two decoders of one file stay within ±200 ms unaided**,
        which is well below what anyone reads as lag on a level meter. So this
        is a safety net for a real divergence — a stall, a track change — and
        not a correction that has to keep up with steady drift.

        The threshold sits above that natural wander on purpose. Every
        correction is a seek, and a seek is a gap in the *shadow's* audio,
        which shows up as a hole in the display; at a 200 ms tolerance this
        seeked four times in fourteen seconds to buy about 48 ms of accuracy,
        which is a bad trade.
        """
        if self._shadow is None:
            return
        try:
            real, shadow = self._player.get_time(), self._shadow.get_time()
            if real is None or shadow is None or real < 0 or shadow < 0:
                return
            if abs(real - shadow) > tolerance_ms:
                self._shadow.set_time(real)
        except Exception:
            pass

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

    def _apply_eq(self, preamp: float, gains: list[float], player=None) -> bool:
        """
        Build a fresh equaliser and hand it to `player` (the real one by
        default, the shadow when it is being started).

        The shadow gets the same curve so the analyser measures what the pit
        is hearing rather than the raw file — a display of the signal *before*
        the EQ would say nothing about the EQ.
        """
        target = player if player is not None else self._player
        try:
            eq = vlc.AudioEqualizer()
            eq.set_preamp(float(preamp))
            for i, gain in enumerate(gains):
                eq.set_amp_at_index(float(gain), i)
            ok = target.set_equalizer(eq) == 0
            if player is None:
                self._eq = eq    # hold a reference; libVLC does not own it
                # Keep the shadow's curve in step with the real one.
                if self._shadow is not None:
                    try:
                        shadow_eq = vlc.AudioEqualizer()
                        shadow_eq.set_preamp(float(preamp))
                        for i, gain in enumerate(gains):
                            shadow_eq.set_amp_at_index(float(gain), i)
                        self._shadow.set_equalizer(shadow_eq)
                        self._shadow_eq = shadow_eq
                    except Exception:
                        pass
            return ok
        except Exception:
            return False

    def release(self) -> None:
        try:
            self._shadow_stop()
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
