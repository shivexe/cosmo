import asyncio
import time

from . import config, events
from .approvals import Approver
from .brain import ClaudeBrain
from .ears import Ears
from .hotkey import watch
from .stt import SpeechEngine
from .tts import Voice


STOP_WORDS = {"stop", "cancel", "quiet", "never mind", "nevermind", "be quiet", "shut up", "enough"}


class Cosmo:
    def __init__(self):
        self.cfg = config.load()
        self.words = config.path_of(self.cfg, "words_file")
        self.engine = SpeechEngine(self.cfg)
        self.ears = Ears(self.cfg["end_of_speech_ms"])
        self.voice = Voice(self.ears)
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
        self.ears.hold_start()

    def released(self):
        if self.mode is None:
            return
        audio, mode, at = self.ears.hold_stop(), self.mode, time.perf_counter()
        self.mode = None
        if mode == "answer":
            asyncio.create_task(self.finish_answer(audio))
        else:
            self.task = asyncio.create_task(self.turn(audio, at, self.task))

    def always(self):
        return self.cfg["listen_mode"] == "always" and self.mode is None

    def speech_started(self):
        if not self.always() or self.waiting is not None:
            return
        if self.state != "idle":
            self.interrupt()
        self.set("listening")

    def spoke(self, audio):
        if not self.always():
            return
        if self.waiting is not None and not self.waiting.done():
            asyncio.create_task(self.finish_answer(audio))
        else:
            self.task = asyncio.create_task(self.turn(audio, time.perf_counter(), self.task))

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
        if not text or text.lower().strip(" .,!?") in STOP_WORDS:
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
    loop = asyncio.get_running_loop()
    cosmo.ears.on_start = lambda: loop.call_soon_threadsafe(cosmo.speech_started)
    cosmo.ears.on_utterance = lambda audio: loop.call_soon_threadsafe(cosmo.spoke, audio)
    watch(cosmo.cfg["hotkey"], asyncio.get_running_loop(), cosmo.pressed, cosmo.released)
    events.emit("ready")
    how = "just talk" if cosmo.cfg["listen_mode"] == "always" else f"hold {cosmo.cfg['hotkey']} to talk, release to send"
    print(f"ready. {how}; talk over Cosmo or press the key to interrupt. ctrl-c quits.")
    try:
        await asyncio.Event().wait()
    finally:
        await cosmo.brain.close()
