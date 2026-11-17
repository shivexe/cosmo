import queue
import re
import threading

import numpy as np
import torch
from kokoro import KPipeline
from torch.nn.utils.weight_norm import WeightNorm

from . import config

SENTENCE = re.compile(r"(?<=[.!?])\s+")
LOOKAHEAD = 2
MARKDOWN = re.compile(r"[*`#>]+|^\s*[-•]\s+", re.M)


def load_pronunciations(path):
    pron = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return pron
    for line in lines:
        word, _, ipa = line.partition("\t")
        if word.strip() and ipa.strip():
            pron[word.strip()] = ipa.strip()
    return pron


def mark(text, pron):
    for word, ipa in pron.items():
        text = re.sub(rf"\b{re.escape(word)}\b", lambda m: f"[{m.group(0)}](/{ipa}/)", text, flags=re.I)
    return text


class Voice:
    def __init__(self, ears):
        self.ears = ears
        self.pipe = KPipeline(lang_code="a", device="cpu", repo_id="hexgrad/Kokoro-82M")
        for module in self.pipe.model.modules():
            if any(isinstance(hook, WeightNorm) for hook in module._forward_pre_hooks.values()):
                torch.nn.utils.remove_weight_norm(module)
        self.generation = 0
        self.todo = queue.Queue()
        self.ready = queue.Queue(maxsize=LOOKAHEAD)
        self.pending = 0
        self.idle = threading.Condition()
        self.speaking = threading.Event()
        for target in (self._synth_loop, self._play_loop):
            threading.Thread(target=target, daemon=True).start()

    def say(self, text):
        text = MARKDOWN.sub(" ", text)
        cfg = config.load()
        pron = load_pronunciations(config.path_of(cfg, "pronunciations_file"))
        for sentence in SENTENCE.split(text.strip()):
            if sentence.strip():
                with self.idle:
                    self.pending += 1
                self.todo.put((self.generation, mark(sentence, pron), cfg["voice"], cfg["speed"]))

    def stop(self):
        self.generation += 1
        for q in (self.todo, self.ready):
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
        with self.idle:
            self.pending = 0
            self.idle.notify_all()
        self.ears.clear()
        self.speaking.clear()

    def wait(self):
        with self.idle:
            self.idle.wait_for(lambda: self.pending <= 0)

    def synth(self, text, voice, speed):
        parts = [r.audio.numpy() for r in self.pipe(text, voice=voice, speed=speed) if r.audio is not None]
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)

    def _done(self):
        with self.idle:
            self.pending = max(0, self.pending - 1)
            self.idle.notify_all()

    def _synth_loop(self):
        try:
            self.synth("Hi.", config.load()["voice"], 1)
        except Exception:
            pass
        while True:
            gen, text, voice, speed = self.todo.get()
            if gen != self.generation:
                continue
            try:
                audio = self.synth(text, voice, speed)
            except Exception:
                if gen == self.generation:
                    self._done()
                continue
            self.ready.put((gen, audio))

    def _play_loop(self):
        while True:
            gen, audio = self.ready.get()
            if gen == self.generation:
                self.speaking.set()
                self.ears.play(audio, lambda: gen == self.generation)
                if gen == self.generation:
                    self._done()
                if self.todo.empty() and self.ready.empty():
                    self.speaking.clear()
