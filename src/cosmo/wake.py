import re
import time
from pathlib import Path

import numpy as np
import openwakeword

CHUNK = 1280
REFRACTORY = 2.0
WAKE_WORDS = re.compile(r"^\W*(?:(?:hey|okay|ok)\W+)?co[sz]mo\b\W*", re.I)


def said_wake_word(text):
    return re.search(r"\bco[sz]mo\b", " ".join(text.split()[:6]), re.I) is not None


def strip(text):
    return WAKE_WORDS.sub("", text).strip()


class Wake:
    def __init__(self, model_path, threshold, verifier_path=None, verifier_threshold=0.3):
        self.name = Path(model_path).stem
        options = {}
        if verifier_path and Path(verifier_path).exists():
            options = {"custom_verifier_models": {self.name: str(verifier_path)}, "custom_verifier_threshold": verifier_threshold}
        self.model = openwakeword.Model(wakeword_models=[str(model_path)], inference_framework="onnx", **options)
        self.threshold = threshold
        self.pending = np.zeros(0, dtype=np.float32)
        self.last = 0.0

    def feed(self, frame, allowed=True):
        self.pending = np.concatenate([self.pending, frame])
        fired = False
        while len(self.pending) >= CHUNK:
            chunk, self.pending = self.pending[:CHUNK], self.pending[CHUNK:]
            score = self.model.predict((np.clip(chunk, -1, 1) * 32767).astype(np.int16))[self.name]
            now = time.monotonic()
            if allowed and score >= self.threshold and now - self.last > REFRACTORY:
                self.last = now
                fired = True
        return fired
