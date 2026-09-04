"""
Microphone capture.

A rolling ring buffer holds the last N seconds of audio; the analysis thread
reads whatever is in it. Capture and analysis are decoupled so a slow analysis
pass can never drop audio frames.

On Windows, prefer a WASAPI device for the USB microphone. MME devices work
but often force 44100 Hz and add latency. Sample rate matters here: at 48 kHz
one sample is 20.8 microseconds, which is worth about 0.7 degrees of
amplitude resolution at 4 Hz. 96 kHz halves that if your interface offers it.
"""

from __future__ import annotations

import queue
import time
import threading
import wave
from typing import Optional

import numpy as np

try:
    import sounddevice as sd
    HAVE_SD = True
except Exception:                                  # pragma: no cover
    sd = None
    HAVE_SD = False


def list_input_devices():
    """Return [(index, name, max_channels, default_sr, hostapi_name)]."""
    if not HAVE_SD:
        return []
    out = []
    try:
        apis = sd.query_hostapis()
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0:
                api = apis[d["hostapi"]]["name"] if d.get("hostapi") is not None else "?"
                out.append((i, d["name"], d["max_input_channels"],
                            int(d.get("default_samplerate", 48000)), api))
    except Exception:
        pass
    return out


def default_input_device():
    if not HAVE_SD:
        return None
    try:
        return sd.default.device[0]
    except Exception:
        return None


class Recorder:
    """Continuous capture into a ring buffer, with optional WAV logging."""

    def __init__(self, device: Optional[int] = None, samplerate: int = 48000,
                 buffer_seconds: float = 40.0, channel: int = 0, blocksize: int = 2048):
        if not HAVE_SD:
            raise RuntimeError(
                "sounddevice is not installed. Run:  pip install sounddevice")
        self.device = device
        self.samplerate = int(samplerate)
        self.channel = channel
        self.blocksize = blocksize
        self.buffer_seconds = float(buffer_seconds)
        self.opened_note = ""            # anything worth telling the user after start()
        self.n = int(buffer_seconds * self.samplerate)
        self._buf = np.zeros(self.n, dtype=np.float32)
        self._write = 0
        self._filled = 0
        self._lock = threading.Lock()
        self._stream = None
        self.peak = 0.0          # raw input peak, EMA -- the level meter reads this
        self.overflows = 0
        self.frames = 0          # total samples ever received; monotonic, for a stall watchdog
        # The signal is used exactly as it arrives -- no digital makeup gain.
        # A quiet pickup is fixed at the interface, not by amplifying the noise
        # floor (which is how the analyzer used to be handed a "80,000 bph"
        # reading). `clips` just counts full-scale hits for the on-screen warning.
        self.clips = 0
        self._wav: Optional[wave.Wave_write] = None
        self._wav_path = ""
        self._wav_lock = threading.Lock()

    # -- stream lifecycle --------------------------------------------------
    def _callback(self, indata, frames, time_info, status):
        if status and status.input_overflow:
            self.overflows += 1
        data = indata[:, self.channel] if indata.ndim > 1 else indata
        data = np.asarray(data, dtype=np.float32)
        p = float(np.max(np.abs(data))) if data.size else 0.0
        self.peak = max(self.peak * 0.92, p)
        self.frames += len(data)
        if p >= 0.999:
            self.clips += 1

        with self._lock:
            k = len(data)
            if k >= self.n:
                self._buf[:] = data[-self.n:]
                self._write = 0
                self._filled = self.n
            else:
                end = self._write + k
                if end <= self.n:
                    self._buf[self._write:end] = data
                else:
                    split = self.n - self._write
                    self._buf[self._write:] = data[:split]
                    self._buf[: end - self.n] = data[split:]
                self._write = end % self.n
                self._filled = min(self.n, self._filled + k)

        with self._wav_lock:
            if self._wav is not None:
                self._wav.writeframes(
                    np.clip(data * 32767.0, -32768, 32767).astype("<i2").tobytes())

    def _device_info(self):
        try:
            info = sd.query_devices(self.device)
            api = sd.query_hostapis(info["hostapi"])["name"]
            return info, str(api)
        except Exception:
            return {}, ""

    def start(self):
        if self._stream is not None:
            return
        ch = max(1, self.channel + 1)
        info, api = self._device_info()

        # Many USB speakerphones (Jabra Speak, Poly, etc.) only expose their
        # microphone at 16 kHz on the WASAPI endpoint, and WASAPI shared mode
        # will not resample unless told to -- that is the -9996 / -9997 you get.
        extra = None
        if "wasapi" in api.lower():
            try:
                extra = sd.WasapiSettings(auto_convert=True)
            except Exception:
                extra = None

        want = self.samplerate
        tries = [want]
        dsr = int(info.get("default_samplerate") or 0)
        for r in (dsr, 48000, 44100, 32000, 16000):
            if r and r not in tries:
                tries.append(r)

        last_err = None
        for rate in tries:
            for ex in ([extra, None] if extra is not None else [None]):
                try:
                    s = sd.InputStream(
                        device=self.device, channels=ch, samplerate=rate,
                        blocksize=self.blocksize, dtype="float32",
                        callback=self._callback, extra_settings=ex)
                    s.start()
                except Exception as e:               # sd.PortAudioError et al.
                    last_err = e
                    continue
                self._stream = s
                if rate != want:
                    self.samplerate = rate
                    self.n = max(1, int(self.buffer_seconds * rate))
                    self._buf = np.zeros(self.n, dtype=np.float32)
                    self._write = self._filled = 0
                    self.opened_note = (
                        f"{(info.get('name') or 'device')} would not open at {want} Hz; "
                        f"running at {rate} Hz. Amplitude resolution is coarser -- for "
                        f"serious work use a dedicated pickup, or pick the MME / "
                        f"DirectSound copy of this device.")
                return

        name = info.get("name") or f"device {self.device}"
        supported = []
        for r in (16000, 32000, 44100, 48000, 96000):
            try:
                sd.check_input_settings(device=self.device, samplerate=r, channels=ch)
                supported.append(r)
            except Exception:
                pass
        hint = (f" It reports support for {', '.join(f'{r} Hz' for r in supported)}."
                if supported else
                " It did not accept any common sample rate as an input -- it may be an "
                "output-only endpoint, in use by another app, or need a different host "
                "API (try the MME or DirectSound copy in the Device list).")
        raise RuntimeError(f"Could not open '{name}' for recording.{hint}\n\n({last_err})")

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self.stop_recording()

    @property
    def running(self) -> bool:
        """True while the input stream is open and delivering audio."""
        s = self._stream
        try:
            return s is not None and s.active
        except Exception:
            return False

    # -- data access -------------------------------------------------------
    def read(self, seconds: float) -> np.ndarray:
        """Most recent `seconds` of audio, oldest first."""
        want = int(seconds * self.samplerate)
        with self._lock:
            avail = min(self._filled, want)
            if avail <= 0:
                return np.zeros(0, dtype=np.float32)
            start = (self._write - avail) % self.n
            if start + avail <= self.n:
                return self._buf[start:start + avail].copy()
            split = self.n - start
            return np.concatenate([self._buf[start:], self._buf[: avail - split]])

    @property
    def seconds_buffered(self) -> float:
        return self._filled / self.samplerate

    def clear(self):
        with self._lock:
            self._filled = 0
            self._write = 0

    # -- wav logging -------------------------------------------------------
    @property
    def is_recording(self) -> bool:
        return self._wav is not None

    def start_recording(self, path: str):
        with self._wav_lock:
            self.stop_recording_locked()
            w = wave.open(path, "wb")
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.samplerate)
            self._wav = w
            self._wav_path = path

    def stop_recording_locked(self):
        path = getattr(self, "_wav_path", "")
        if self._wav is not None:
            try:
                self._wav.close()
            except Exception:
                pass
            self._wav = None
        self._wav_path = ""
        return path

    def stop_recording(self) -> str:
        """Close the WAV; returns the path written, or '' if none."""
        with self._wav_lock:
            return self.stop_recording_locked()


def load_wav(path: str):
    """Read a WAV into (float array, samplerate). Mono-ised by taking ch 0."""
    with wave.open(path, "rb") as w:
        nch, sw, fr, nfr = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(nfr)
    dtype = {1: np.uint8, 2: "<i2", 4: "<i4"}.get(sw)
    if dtype is None:
        raise ValueError(f"Unsupported sample width: {sw * 8} bit")
    a = np.frombuffer(raw, dtype=dtype).astype(np.float64)
    if sw == 1:
        a = (a - 128.0) / 128.0
    else:
        a = a / float(2 ** (8 * sw - 1))
    if nch > 1:
        a = a.reshape(-1, nch)[:, 0]
    return a, fr


def _sweep_and_reference(seconds, samplerate, f0, f1):
    n = int(seconds * samplerate)
    t = np.arange(n) / samplerate
    k = np.log(f1 / f0)
    phase = 2 * np.pi * f0 * seconds / k * (np.exp(t / seconds * k) - 1.0)
    x = np.sin(phase).astype(np.float32)
    win = np.ones(n)
    edge = int(0.02 * samplerate)
    win[:edge] = np.linspace(0, 1, edge)
    win[-edge:] = np.linspace(1, 0, edge)
    return (x * win).astype(np.float32)


def mic_response_from_capture(rec, ref, samplerate, f0=80.0, f1=16000.0, bins=48):
    """
    Magnitude response of the record chain: captured spectrum / swept-sine
    reference spectrum, in log-frequency bins, normalised to 0 dB at its
    median. Returns (freqs_hz, level_db). This is speaker + room + mic, so it
    is only as flat a reference as the playback side is.
    """
    rec = np.asarray(rec, dtype=float)
    ref = np.asarray(ref, dtype=float)
    n = min(rec.size, ref.size)
    if n < samplerate // 2:
        return np.array([]), np.array([])
    rec, ref = rec[:n], ref[:n]
    w = np.hanning(n)
    R = np.abs(np.fft.rfft(rec * w))
    X = np.abs(np.fft.rfft(ref * w))
    fr = np.fft.rfftfreq(n, 1.0 / samplerate)
    keep = (fr >= f0) & (fr <= f1) & (X > X.max() * 1e-3)
    fr, ratio = fr[keep], R[keep] / X[keep]
    if fr.size < bins:
        return np.array([]), np.array([])
    edges = np.logspace(np.log10(fr[0]), np.log10(fr[-1]), bins + 1)
    idx = np.clip(np.digitize(fr, edges) - 1, 0, bins - 1)
    out_f, out_db = [], []
    for b in range(bins):
        sel = idx == b
        if sel.any():
            out_f.append(float(np.sqrt(edges[b] * edges[b + 1])))
            out_db.append(20.0 * np.log10(np.median(ratio[sel]) + 1e-9))
    db = np.asarray(out_db) - float(np.median(out_db))
    return np.asarray(out_f), db


# ==========================================================================
# Simulated watch -- no hardware required
# ==========================================================================

class SimulatedRecorder:
    """
    Drop-in replacement for Recorder that synthesises escapement audio in
    real time instead of capturing it.

    Exposes the same interface, so everything downstream -- ring buffer,
    analysis worker, trace, advice -- runs exactly as it does with a real
    microphone. That makes it a genuine end-to-end test: dial in a rate and
    an amplitude here, and the readouts should come back with those numbers.
    """

    def __init__(self, samplerate: int = 48000, buffer_seconds: float = 40.0,
                 bph: int = 28800, amplitude: float = 275.0, lift_angle: float = 52.0,
                 rate_spd: float = 0.0, beat_error_ms: float = 0.0, snr_db: float = 18.0):
        self.samplerate = int(samplerate)
        self.buffer_seconds = float(buffer_seconds)
        self.opened_note = ""
        self.n = int(buffer_seconds * self.samplerate)
        self._buf = np.zeros(self.n, dtype=np.float32)
        self._write = 0
        self._filled = 0
        self._lock = threading.Lock()
        self.peak = 0.0
        self.overflows = 0
        self.frames = 0
        self.clips = 0
        self._wav = None
        self._wav_path = ""
        self._wav_lock = threading.Lock()

        self._rng = np.random.default_rng()
        self._abs = 0            # absolute sample position of the next chunk
        self._k = 0              # beat index since the last phase anchor
        self._t0 = 0.0           # time of beat 0, in seconds
        self._tail = np.zeros(0)
        self._thread = None
        self._stop = threading.Event()
        self.set_params(bph, amplitude, lift_angle, rate_spd, beat_error_ms, snr_db)

    # -- parameters --------------------------------------------------------
    def set_params(self, bph=None, amplitude=None, lift_angle=None,
                   rate_spd=None, beat_error_ms=None, snr_db=None):
        if bph is not None:
            self.bph = int(bph)
        if amplitude is not None:
            self.amplitude = float(amplitude)
        if lift_angle is not None:
            self.lift_angle = float(lift_angle)
        if rate_spd is not None:
            self.rate_spd = float(rate_spd)
        if beat_error_ms is not None:
            self.beat_error_ms = float(beat_error_ms)
        if snr_db is not None:
            self.snr_db = float(snr_db)

        nominal = 3600.0 / self.bph
        self.period = nominal / (1.0 + self.rate_spd / 86400.0)
        t_osc = 2.0 * 3600.0 / self.bph
        # Same harmonic relation the analyzer inverts, run forwards.
        self.dt = (t_osc / np.pi) * np.arcsin(
            np.clip((self.lift_angle / 2.0) / max(self.amplitude, 1.0), -1, 1))
        self._c = (self.beat_error_ms / 1000.0) / 2.0

        # Re-anchor the beat phase at the current playhead. Without this, a
        # live parameter change leaves the old beat index multiplied by the
        # NEW period, which throws the next beat seconds into the past or the
        # future and corrupts any reading taken across the change.
        self._t0 = self._abs / self.samplerate + 0.01
        self._k = 0

    # -- synthesis ---------------------------------------------------------
    def _burst(self, freq, decay_ms, n):
        t = np.arange(n) / self.samplerate
        return np.sin(2 * np.pi * freq * t) * np.exp(-t / (decay_ms / 1000.0))

    def _generate(self, n: int) -> np.ndarray:
        fs = self.samplerate
        pad = int(fs * (self.dt + 0.006)) + 4
        out = np.zeros(n + pad)
        if self._tail.size:
            m = min(self._tail.size, out.size)
            out[:m] += self._tail[:m]

        # Emit every beat whose onset falls inside this chunk.
        while True:
            t_beat = self._t0 + self._k * self.period + ((-1) ** self._k) * self._c
            s_beat = int(round(t_beat * fs))
            if s_beat >= self._abs + n:
                break
            local = s_beat - self._abs
            if local < 0:
                self._k += 1
                continue
            tone = 4200.0 if self._k % 2 == 0 else 4700.0
            ln = int(fs * 0.004)
            for off, amp, f, dec in ((0.0, 0.35, tone * 1.15, 0.35),
                                     (self.dt * 0.55, 0.55, tone, 0.45),
                                     (self.dt, 1.00, tone * 0.92, 0.60)):
                s = local + int(off * fs)
                if 0 <= s < out.size - ln:
                    out[s:s + ln] += amp * self._burst(f, dec, ln)
            self._k += 1

        self._tail = out[n:].copy()
        chunk = out[:n]

        noise_rms = 0.09 / (10 ** (self.snr_db / 20.0)) * 3.0
        chunk = chunk + self._rng.normal(0, noise_rms, n)
        self._abs += n
        return (chunk * 0.7).astype(np.float32)

    # -- lifecycle ---------------------------------------------------------
    def _loop(self):
        block = max(256, self.samplerate // 20)      # ~50 ms
        interval = block / self.samplerate
        next_t = time.monotonic()
        while not self._stop.is_set():
            data = self._generate(block)
            self._push(data)
            next_t += interval
            time.sleep(max(0.0, next_t - time.monotonic()))

    def _push(self, data):
        p = float(np.max(np.abs(data))) if data.size else 0.0
        self.peak = max(self.peak * 0.92, p)
        self.frames += len(data)
        with self._lock:
            k = len(data)
            end = self._write + k
            if end <= self.n:
                self._buf[self._write:end] = data
            else:
                split = self.n - self._write
                self._buf[self._write:] = data[:split]
                self._buf[: end - self.n] = data[split:]
            self._write = end % self.n
            self._filled = min(self.n, self._filled + k)
        with self._wav_lock:
            if self._wav is not None:
                self._wav.writeframes(
                    np.clip(data * 32767.0, -32768, 32767).astype("<i2").tobytes())

    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self.stop_recording()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- shared interface --------------------------------------------------
    read = Recorder.read
    clear = Recorder.clear
    start_recording = Recorder.start_recording
    stop_recording = Recorder.stop_recording
    stop_recording_locked = Recorder.stop_recording_locked
    is_recording = Recorder.is_recording

    @property
    def seconds_buffered(self) -> float:
        return self._filled / self.samplerate
