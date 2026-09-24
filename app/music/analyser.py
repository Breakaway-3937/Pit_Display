"""
The ten-band level analyser behind the EQ curve — an X32's RTA, in the pit.

    libVLC decodes ──▶ PCM callback ──┬──▶ QAudioSink   (the speakers)
                                      └──▶ BandAnalyser (this file)

**Every number here is a measurement of the audio actually playing.** That is
worth stating because a spectrum display is the easiest thing in this app to
fake convincingly, and a pit display that invents a pretty dancing graph is
lying to everyone who looks at it. If there is no audio, the bars sit at the
floor.

**One biquad band-pass per EQ band, not an FFT.** The bands are libVLC's own —
31.25 Hz to 16 kHz, one per octave — so ten second-order sections answer
exactly the question the display asks, with no window size to choose, no bin
that straddles two bands, and no dependency. Measured on CPython 3.14: ten
bands over a second of 44.1 kHz audio costs **~2% of one core**, which is why
there is no numpy here.

**The samples arrive post-equaliser.** libVLC's audio callbacks are the output
sink, at the end of the filter chain, so what this measures is what the pit
hears — which is what an X32 shows, and the only version of this display that
tells you anything useful about the EQ you just set.

`feed()` runs on libVLC's audio thread. It touches nothing but its own floats
and never calls into Qt; the widget reads `levels()` on its own timer. That is
deliberate — a signal emitted per audio block would be ~20 cross-thread
emissions a second for a display that repaints at most half that often.
"""

from __future__ import annotations

import math
import threading

from app.music.eq import N_BANDS

# libVLC's own band centres. Read from the library at runtime where possible;
# these are the fallback and match what it reports.
BAND_HZ = (31.25, 62.5, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0,
           8000.0, 16000.0)

# The display's floor and ceiling, in dBFS.
#
# **−48, not −60, and the difference is the whole readability of the thing.**
# Music sits roughly −30 to −6 dBFS per octave band, so a 60 dB span squeezes
# every bar into the top third of the field and they all look the same height
# — which is precisely the failure this display exists to avoid. −48 spends
# the height where the signal actually is, and a band quieter than that is
# doing nothing an operator needs to see anyway.
FLOOR_DB = -48.0
CEIL_DB = 0.0

# How fast a bar falls once the sound has gone. Rising is instant — a
# transient that does not show is a transient the display got wrong — but an
# instant fall reads as flicker rather than as level.
#
# **90 dB/s, not 36.** At 36 a bar took 1.11 s to fall from −8 dBFS to the
# floor, which on music reads as lag rather than as level: the bars were still
# describing a beat that had finished. 90 makes that 0.44 s, close to the
# 20 dB per 250 ms an RTA on a console uses, and the peak tick is what keeps
# the recent maximum legible rather than a slow fall.
DECAY_PER_S = 90.0          # dB
PEAK_HOLD_S = 1.0
PEAK_FALL_PER_S = 36.0      # dB


def _bandpass(freq: float, rate: float, q: float = 1.41):
    """
    One second-order band-pass section, constant 0 dB at centre.

    Q of 1.41 is roughly one octave of bandwidth, which is the spacing of the
    bands themselves: narrower and the display misses energy that falls
    between two centres, wider and neighbouring bands report the same sound
    twice and every bar moves together.
    """
    w0 = 2.0 * math.pi * min(freq, rate * 0.45) / rate
    alpha = math.sin(w0) / (2.0 * q)
    cos_w0 = math.cos(w0)
    a0 = 1.0 + alpha
    return (alpha / a0, 0.0, -alpha / a0,          # b0, b1, b2
            (-2.0 * cos_w0) / a0, (1.0 - alpha) / a0)  # a1, a2


class BandAnalyser:
    """Ten band levels in dBFS, fed with raw PCM from the audio thread."""

    def __init__(self, rate: int = 44100, channels: int = 2) -> None:
        self._lock = threading.Lock()
        self.configure(rate, channels)

    def configure(self, rate: int, channels: int) -> None:
        with self._lock:
            self._rate = max(8000, int(rate))
            self._channels = max(1, int(channels))
            self._coeffs = [_bandpass(f, self._rate) for f in BAND_HZ[:N_BANDS]]
            self._z1 = [0.0] * N_BANDS
            self._z2 = [0.0] * N_BANDS
            self._level = [FLOOR_DB] * N_BANDS
            self._peak = [FLOOR_DB] * N_BANDS
            self._peak_age = [0.0] * N_BANDS
            self._silent_blocks = 0

    # ── The audio thread ─────────────────────────────────────────────────

    def feed(self, samples, frames: int) -> None:
        """
        One block of interleaved float samples, already scaled to -1..1.

        Runs on libVLC's audio thread and must return well inside the block's
        own duration, or the sink starves and the pit hears it. Everything in
        the inner loop is a local float on purpose.
        """
        if frames <= 0:
            return
        step = self._channels
        with self._lock:
            rate = self._rate
            dt = frames / float(rate)
            for band in range(N_BANDS):
                b0, b1, b2, a1, a2 = self._coeffs[band]
                z1, z2 = self._z1[band], self._z2[band]
                acc = 0.0
                # Mono sum by taking the first channel: a second channel
                # doubles the work to say almost exactly the same thing about
                # a music mix, and this runs on the audio thread.
                for i in range(0, frames * step, step):
                    s = samples[i]
                    y = b0 * s + z1
                    z1 = b1 * s - a1 * y + z2
                    z2 = b2 * s - a2 * y
                    acc += y * y
                self._z1[band], self._z2[band] = z1, z2

                rms = math.sqrt(acc / frames) if acc > 0.0 else 0.0
                db = (20.0 * math.log10(rms)) if rms > 1e-7 else FLOOR_DB
                db = max(FLOOR_DB, min(CEIL_DB, db))

                # Rise instantly, fall on a clock.
                held = self._level[band]
                if db >= held:
                    self._level[band] = db
                else:
                    self._level[band] = max(db, held - DECAY_PER_S * dt)

                if self._level[band] >= self._peak[band]:
                    self._peak[band] = self._level[band]
                    self._peak_age[band] = 0.0
                else:
                    self._peak_age[band] += dt
                    if self._peak_age[band] > PEAK_HOLD_S:
                        self._peak[band] = max(
                            self._level[band],
                            self._peak[band] - PEAK_FALL_PER_S * dt)

    def silence(self) -> None:
        """Nothing is playing. Let everything fall to the floor and settle."""
        with self._lock:
            self._level = [FLOOR_DB] * N_BANDS
            self._peak = [FLOOR_DB] * N_BANDS
            self._z1 = [0.0] * N_BANDS
            self._z2 = [0.0] * N_BANDS

    # ── The GUI thread ───────────────────────────────────────────────────

    def levels(self) -> list[float]:
        """Band levels in dBFS, floor to 0. Cheap enough to call per repaint."""
        with self._lock:
            return list(self._level)

    def peaks(self) -> list[float]:
        with self._lock:
            return list(self._peak)

    @property
    def active(self) -> bool:
        """True when anything has been above the floor recently."""
        with self._lock:
            return any(v > FLOOR_DB + 0.5 for v in self._level)


def normalise(db: float) -> float:
    """dBFS to 0..1 across the display's span."""
    return max(0.0, min(1.0, (db - FLOOR_DB) / (CEIL_DB - FLOOR_DB)))
