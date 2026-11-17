import asyncio
import time

import numpy as np
import sounddevice as sd

from . import config, events
from .approvals import Approver
from .brain import ClaudeBrain
from .hotkey import watch
from .stt import SAMPLE_RATE, SpeechEngine
from .tts import Voice


class Recorder:
    def __init__(self):
        self.blocks = None
        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=self._take)
        self.stream.start()

    def _take(self, data, frames, time_info, status):
        if self.blocks is not None:
            self.blocks.append(data[:, 0].copy())

    def start(self):
        self.blocks = []

    def stop(self):
        blocks, self.blocks = self.blocks, None
        return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)


class Cosmo:
    def __init__(self):
        self.cfg = config.load()
        self.words = config.path_of(self.cfg, "words_file")
        self.engine = SpeechEngine(self.cfg)
        self.voice = Voice()
        self.mic = Recorder()
        self.brain = ClaudeBrain(self.cfg, Approver(self.voice.say, self.hear_answer))
        self.state = "idle"
        self.turn_id = 0
        self.task = None
        self.mode = None
        self.waiting = None

    def set(self, state, detail=""):
        self.state = state
        events.emit(state, detail)

    def pressed(self):
        if self.waiting is not None and not self.waiting.done():
            self.voice.stop()
            self.mode = "answer"
        else:
            if self.state != "idle":
                self.interrupt()
            self.mode = "turn"
            self.set("listening")
        self.mic.start()

    def released(self):
        if self.mode is None:
            return
        audio, mode, at = self.mic.stop(), self.mode, time.perf_counter()
        self.mode = None
        if mode == "answer":
            asyncio.create_task(self.finish_answer(audio))
        else:
            self.task = asyncio.create_task(self.turn(audio, at, self.task))

    def interrupt(self):
        self.turn_id += 1
        self.voice.stop()
        if self.task and not self.task.done():
            asyncio.create_task(self.brain.interrupt())
        events.emit("interrupted")

    async def hear_answer(self):
        self.set("waiting_for_approval")
        self.waiting = asyncio.get_running_loop().create_future()
        try:
            return await self.waiting
        finally:
            self.waiting = None
            self.set("thinking")

    async def finish_answer(self, audio):
        text = await asyncio.to_thread(self.engine.transcribe, audio, self.words)
        events.emit("heard", text)
        if self.waiting is not None and not self.waiting.done():
            self.waiting.set_result(text)

    async def report(self, at, turn_id):
        if await asyncio.to_thread(self.voice.speaking.wait, 15) and turn_id == self.turn_id:
            seconds = time.perf_counter() - at
            events.emit("first_sound", f"{seconds:.2f}s")
            print(f"  first sound {seconds:.2f}s after you let go")

    async def turn(self, audio, at, previous):
        self.set("thinking")
        if previous:
            await previous
        mine = self.turn_id
        text = await asyncio.to_thread(self.engine.transcribe, audio, self.words)
        if mine != self.turn_id:
            return
        events.emit("heard", text)
        print(f"  you: {text or '(nothing heard)'}")
        if not text:
            return self.set("idle")
        first = True
        async for sentence in self.brain.ask(text):
            if mine != self.turn_id:
                continue
            if first:
                first = False
                self.set("speaking")
                asyncio.create_task(self.report(at, mine))
            print(f"  cosmo: {sentence}")
            self.voice.say(sentence)
        if mine == self.turn_id:
            await asyncio.to_thread(self.voice.wait)
            if mine == self.turn_id:
                self.set("idle")


async def run():
    cosmo = Cosmo()
    watch(cosmo.cfg["hotkey"], asyncio.get_running_loop(), cosmo.pressed, cosmo.released)
    events.emit("ready")
    print(f"ready. hold {cosmo.cfg['hotkey']} to talk, release to send, press again to interrupt. ctrl-c quits.")
    try:
        await asyncio.Event().wait()
    finally:
        await cosmo.brain.close()
