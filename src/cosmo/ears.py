import queue
import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd
import torch
from aec_audio_processing import AudioProcessor
from scipy.signal import resample_poly
from silero_vad import VADIterator, load_silero_vad

from . import events

RATE = 16000
FRAME = 160
VAD_FRAME = 512
PRE_ROLL = 10
WARMUP_SECONDS = 3.0


class Ears:
    def __init__(self, end_of_speech_ms):
        self.aec = AudioProcessor(enable_aec=True, enable_ns=False, enable_agc=False, enable_vad=False)
        self.aec.set_stream_format(RATE, 1)
        self.aec.set_reverse_stream_format(RATE, 1)
        self.lock = threading.Lock()
        self.playing = []
        self.played = 0.0
        self.held = None
        self.frames = queue.Queue()
        self.on_start = lambda: None
        self.on_utterance = lambda audio: None
        self.model = load_silero_vad()
        self.end_ms = end_of_speech_ms
        self.stream = sd.Stream(samplerate=RATE, blocksize=FRAME, channels=1, dtype="float32", latency="low", callback=self._callback)
        self.aec.set_stream_delay(int(sum(self.stream.latency) * 1000))
        threading.Thread(target=self._listen, daemon=True).start()
        self.stream.start()

    def is_playing(self):
        with self.lock:
            return bool(self.playing)

    def warm(self):
        return self.played >= WARMUP_SECONDS

    def play(self, audio, still_wanted=lambda: True):
        done = threading.Event()
        samples = resample_poly(audio, 2, 3).astype(np.float32)
        with self.lock:
            if not still_wanted():
                return
            self.playing.append([samples, 0, done])
        done.wait()

    def clear(self):
        with self.lock:
            for _, _, done in self.playing:
                done.set()
            self.playing.clear()

    def hold_start(self):
        self.held = []

    def hold_stop(self):
        blocks, self.held = self.held, None
        return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)

    def _callback(self, indata, outdata, frames, time_info, status):
        out = np.zeros(frames, dtype=np.float32)
        filled = 0
        with self.lock:
            while filled < frames and self.playing:
                samples, pos, done = self.playing[0]
                n = min(frames - filled, len(samples) - pos)
                out[filled : filled + n] = samples[pos : pos + n]
                filled += n
                self.playing[0][1] = pos + n
                if pos + n >= len(samples):
                    done.set()
                    self.playing.pop(0)
        self.played += filled / RATE
        outdata[:, 0] = out
        self.aec.process_reverse_stream((out * 32767).astype(np.int16).tobytes())
        clean = np.frombuffer(self.aec.process_stream((indata[:, 0] * 32767).astype(np.int16).tobytes()), dtype=np.int16).astype(np.float32) / 32767
        held = self.held
        if held is not None:
            held.append(clean)
        self.frames.put(clean)

    def _listen(self):
        vad = VADIterator(self.model, threshold=0.5, sampling_rate=RATE, min_silence_duration_ms=self.end_ms)
        pending = np.zeros(0, dtype=np.float32)
        pre, utterance, speaking, guarded = deque(maxlen=PRE_ROLL), [], False, False
        while True:
            pending = np.concatenate([pending, self.frames.get()])
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
