import threading

import numpy as np
import torch

from .config import HOME

FOLDER = HOME / "voice"
PROFILE = FOLDER / "profile.npy"
MODEL = "speechbrain/spkrec-ecapa-voxceleb"
RATE = 16000
MIN_SECONDS = 0.6


def trim(audio, pad=0.15):
    hop = RATE // 100
    frames = len(audio) // hop
    if frames == 0:
        return audio
    level = np.sqrt((audio[: frames * hop].reshape(frames, hop) ** 2).mean(1))
    loud = np.where(level > max(level.max() * 0.1, 0.002))[0]
    if not len(loud):
        return audio[:0]
    start = max(0, loud[0] * hop - int(pad * RATE))
    end = min(len(audio), (loud[-1] + 1) * hop + int(pad * RATE))
    clip = audio[start:end]
    if len(clip) < MIN_SECONDS * RATE:
        extra = int(MIN_SECONDS * RATE) - len(clip)
        clip = np.pad(clip, (extra // 2, extra - extra // 2))
    return clip


class VoiceID:
    def __init__(self, threshold):
        from speechbrain.inference.speaker import EncoderClassifier

        self.model = EncoderClassifier.from_hparams(source=MODEL, savedir=str(FOLDER / "model"), run_opts={"device": "cpu"})
        self.threshold = threshold
        self.profile = np.load(PROFILE) if PROFILE.exists() else None
        self.lock = threading.Lock()

    def embed(self, audio):
        with self.lock, torch.no_grad():
            vec = self.model.encode_batch(torch.from_numpy(np.asarray(audio, dtype=np.float32))[None, :]).squeeze().numpy()
        return vec / (np.linalg.norm(vec) + 1e-9)

    def enroll(self, clips):
        vecs = [self.embed(c) for c in clips if len(c) >= MIN_SECONDS * RATE]
        mean = np.mean(vecs, axis=0)
        self.profile = mean / (np.linalg.norm(mean) + 1e-9)
        FOLDER.mkdir(parents=True, exist_ok=True)
        np.save(PROFILE, self.profile)
        return len(vecs)

    def score(self, audio):
        audio = trim(np.asarray(audio, dtype=np.float32))
        if self.profile is None or len(audio) < MIN_SECONDS * RATE:
            return None
        return float(np.dot(self.embed(audio), self.profile))

    def is_me(self, audio):
        s = self.score(audio)
        return self.profile is None or (s is not None and s >= self.threshold)
