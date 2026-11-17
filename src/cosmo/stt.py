import threading

import numpy as np
import torch
from peft import PeftModel
from silero_vad import get_speech_timestamps, load_silero_vad
from transformers import WhisperForConditionalGeneration, WhisperProcessor

from . import config

MODEL = "openai/whisper-small.en"
SAMPLE_RATE = 16000
MIN_SECONDS = 0.4
QUIET_RMS = 0.002
CHUNK_MIN, CHUNK_MAX = 18.0, 28.0
PROMPT_MAX = 223
MIN_SPEECH_MS = 100


def split(audio):
    hop = int(0.02 * SAMPLE_RATE)
    chunks, start = [], 0
    while len(audio) - start > CHUNK_MAX * SAMPLE_RATE:
        a, b = start + int(CHUNK_MIN * SAMPLE_RATE), start + int(CHUNK_MAX * SAMPLE_RATE)
        frames = (b - a) // hop
        energy = (audio[a : a + frames * hop].reshape(frames, hop) ** 2).mean(1)
        cut = a + int(np.argmin(np.convolve(energy, np.ones(5) / 5, mode="same"))) * hop
        chunks.append(audio[start:cut])
        start = cut
    chunks.append(audio[start:])
    return chunks


def loud_enough(audio):
    return len(audio) >= MIN_SECONDS * SAMPLE_RATE and np.sqrt(np.mean(audio**2)) >= QUIET_RMS


class SpeechEngine:
    def __init__(self, cfg):
        if cfg["speech_engine"] != "whisper-small-lora":
            raise ValueError(f"unknown speech_engine {cfg['speech_engine']!r}")
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.processor = WhisperProcessor.from_pretrained(MODEL)
        model = WhisperForConditionalGeneration.from_pretrained(MODEL, dtype=torch.float32)
        model.generation_config.forced_decoder_ids = None
        if cfg.get("adapter_path"):
            model = PeftModel.from_pretrained(model, str(config.path_of(cfg, "adapter_path"))).merge_and_unload()
        self.model = model.to(self.device).eval()
        self.vad = load_silero_vad()
        self.lock = threading.Lock()

    def has_speech(self, audio):
        return bool(get_speech_timestamps(torch.from_numpy(audio), self.vad, sampling_rate=SAMPLE_RATE, min_speech_duration_ms=MIN_SPEECH_MS))

    def hint(self, words_file):
        words = [w.strip() for w in words_file.read_text(encoding="utf-8").splitlines() if w.strip()]
        ids = self.processor.get_prompt_ids(", ".join(words) + ".", return_tensors="pt")
        if len(ids) > PROMPT_MAX:
            ids = torch.cat([ids[:1], ids[-(PROMPT_MAX - 1) :]])
        return ids.to(self.device)

    def transcribe(self, audio, words_file):
        with self.lock:
            return self._transcribe(audio, words_file)

    def _transcribe(self, audio, words_file):
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim != 1:
            raise ValueError("mono 1-D audio required")
        if not np.isfinite(audio).all() or not loud_enough(audio) or not self.has_speech(audio):
            return ""
        chunks = split(audio)
        if len(chunks) > 1:
            chunks = [c for c in chunks if loud_enough(c) and self.has_speech(c)]
        prompt_ids = self.hint(words_file)
        texts = []
        for chunk in chunks:
            feats = self.processor.feature_extractor(chunk, sampling_rate=SAMPLE_RATE, return_tensors="pt").input_features
            with torch.no_grad():
                ids = self.model.generate(input_features=feats.to(self.device), prompt_ids=prompt_ids, max_new_tokens=200, use_cache=True)
            texts.append(self.processor.batch_decode(ids, skip_special_tokens=True)[0].strip())
        return " ".join(t for t in texts if t)
