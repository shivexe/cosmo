import queue
import threading
import time
import warnings
from collections import deque

import numpy as np
import sounddevice as sd
import torch
from aec_audio_processing import AudioProcessor
from scipy.signal import resample_poly
from silero_vad import VADIterator, load_silero_vad

from . import events

warnings.filterwarnings("ignore", category=DeprecationWarning, module="sounddevice")

RATE = 16000
FRAME = 160
OUT_RATE = 48000
OUT_BLOCK = FRAME * 3
VAD_FRAME = 512
PRE_ROLL = 10
WARMUP_SECONDS = 3.0
ECHO_TAIL = 1.2


class Ears:
    def __init__(self, end_of_speech_ms, wake=None):
        self.wake = wake
        self.on_wake = lambda: None
        self.aec = AudioProcessor(enable_aec=True, enable_ns=False, enable_agc=False, enable_vad=False)
        self.aec.set_stream_format(RATE, 1)
        self.aec.set_reverse_stream_format(RATE, 1)
        self.lock = threading.Lock()
        self.playing = []
        self.played = 0.0
        self.last_sound = 0.0
        self.glitches = 0
        self.held = None
        self.frames = queue.Queue()
        self.reverse = queue.Queue()
        self.refs = self.fed = 0
        self.captured = self.cleaned = 0
        self.recent = deque(maxlen=RATE // FRAME)
        self.recent_lock = threading.Lock()
        self.on_start = lambda: None
        self.on_utterance = lambda audio: None
        self.model = load_silero_vad()
        self.end_ms = end_of_speech_ms
        self.reset = threading.Event()
        self.resync = False
        self._open()
        threading.Thread(target=self._listen, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()

    def _open(self):
        self.mic = sd.InputStream(samplerate=RATE, blocksize=FRAME, channels=1, dtype="float32", latency="high", callback=self._mic)
        self.speaker = sd.OutputStream(samplerate=OUT_RATE, blocksize=OUT_BLOCK, channels=1, dtype="float32", latency="high", callback=self._speaker)
        self.aec.set_stream_delay(int((self.mic.latency + self.speaker.latency) * 1000))
        self.speaker.start()
        self.mic.start()

    def restart(self):
        self.reset.set()

    def _watch(self):
        seen, patience, failures = self.captured, 3.0, 0
        while True:
            asked = self.reset.wait(patience)
            stalled = not asked and self.captured == seen
            if not (asked or stalled):
                seen, patience = self.captured, 3.0
                continue
            time.sleep(0.5)
            self.reset.clear()
            try:
                for stream in (self.mic, self.speaker):
                    stream.abort()
                    stream.close()
                if sd._initialized:
                    sd._terminate()
                sd._initialize()
                self.resync = True
                self._open()
                failures = 0
                patience = min(patience * 2, 60) if stalled else 3.0
                events.emit("audio_reset", "stalled" if stalled else "")
            except Exception as error:
                failures += 1
                patience = min(5.0 * 2**failures, 60)
                if failures == 1:
                    events.emit("error", f"audio restart: {error!r}")
            seen = self.captured

    def is_playing(self):
        with self.lock:
            return bool(self.playing)

    def warm(self):
        return self.played >= WARMUP_SECONDS

    def play(self, audio, still_wanted=lambda: True):
        done = threading.Event()
        ref = resample_poly(audio, 2, 3).astype(np.float32)
        samples = np.zeros(len(ref) * 3, dtype=np.float32)
        up = resample_poly(audio, 2, 1)[: len(samples)]
        samples[: len(up)] = up
        with self.lock:
            if not still_wanted():
                return
            self.playing.append([samples, 0, done, ref])
        done.wait()

    def clear(self):
        with self.lock:
            for _, _, done, _ in self.playing:
                done.set()
            self.playing.clear()

    def hold_start(self):
        self.held = []

    def hold_stop(self):
        blocks, self.held = self.held, None
        target, deadline = self.captured, time.monotonic() + 0.5
        while self.cleaned < target and time.monotonic() < deadline:
            time.sleep(0.005)
        return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)

    def _speaker(self, outdata, frames, time_info, status):
        out = np.zeros(frames, dtype=np.float32)
        ref = np.zeros(frames // 3, dtype=np.float32)
        filled = 0
        with self.lock:
            while filled < frames and self.playing:
                samples, pos, done, r = self.playing[0]
                n = min(frames - filled, len(samples) - pos)
                out[filled : filled + n] = samples[pos : pos + n]
                ref[filled // 3 : (filled + n) // 3] = r[pos // 3 : (pos + n) // 3]
                filled += n
                self.playing[0][1] = pos + n
                if pos + n >= len(samples):
                    done.set()
                    self.playing.pop(0)
        self.played += filled / OUT_RATE
        if filled:
            self.last_sound = time.monotonic()
        outdata[:, 0] = out
        if status.output_underflow:
            self.glitches += 1
        if self.reverse.qsize() < RATE // FRAME:
            self.reverse.put(ref)
        self.refs += 1

    def _mic(self, indata, frames, time_info, status):
        if status.input_overflow:
            self.glitches += 1
        self.captured += 1
        quiet = time.monotonic() - self.last_sound > ECHO_TAIL
        self.frames.put((quiet, self.refs, self.held, indata[:, 0].copy()))

    def _clean(self, raw, refs, held):
        if self.resync:
            self.resync = False
            while not self.reverse.empty():
                self.reverse.get_nowait()
            self.fed = refs
        while self.fed < refs:
            try:
                ref = self.reverse.get_nowait()
            except queue.Empty:
                self.fed = refs
                break
            self.aec.process_reverse_stream((ref * 32767).astype(np.int16).tobytes())
            self.fed += 1
        processed = self.aec.process_stream((raw * 32767).astype(np.int16).tobytes())
        clean = np.frombuffer(processed, dtype=np.int16).astype(np.float32) / 32767
        if held is not None:
            held.append(clean)
        with self.recent_lock:
            self.recent.append(clean)
        self.cleaned += 1
        return clean

    def last_second(self):
        with self.recent_lock:
            return np.concatenate(self.recent) if self.recent else np.zeros(0, dtype=np.float32)

    def _listen(self):
        vad = VADIterator(self.model, threshold=0.5, sampling_rate=RATE, min_silence_duration_ms=self.end_ms)
        pending = np.zeros(0, dtype=np.float32)
        pre, utterance, speaking, guarded = deque(maxlen=PRE_ROLL), [], False, False
        while True:
            quiet, refs, held, raw = self.frames.get()
            incoming = self._clean(raw, refs, held)
            if self.wake is not None:
                try:
                    if self.wake.feed(incoming, quiet):
                        self.on_wake()
                except Exception as error:
                    events.emit("error", f"wake: {error!r}")
            pending = np.concatenate([pending, incoming])
            while len(pending) >= VAD_FRAME:
                frame, pending = pending[:VAD_FRAME], pending[VAD_FRAME:]
                try:
                    event = vad(torch.from_numpy(frame))
                    if speaking:
                        utterance.append(frame)
                    else:
                        pre.append(frame)
                    if event and "start" in event and not speaking:
                        speaking = True
                        guarded = self.is_playing() and not self.warm()
                        utterance = list(pre)
                        if not guarded:
                            self.on_start()
                    if event and "end" in event and speaking:
                        speaking = False
                        if not guarded:
                            self.on_utterance(np.concatenate(utterance))
                        utterance = []
                except Exception as error:
                    events.emit("error", f"listening: {error!r}")
                    vad.reset_states()
                    speaking, utterance = False, []
