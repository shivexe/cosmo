import asyncio
import time

from . import config, events
from .approvals import Approver
from .brain import ClaudeBrain
from .ears import Ears
from .hotkey import watch
from .stt import SpeechEngine
from .tts import Voice
from .wake import Wake, said_wake_word, strip


STOP_WORDS = {"stop", "cancel", "quiet", "never mind", "nevermind", "be quiet", "shut up", "enough"}


class Cosmo:
    def __init__(self):
        self.cfg = config.load()
        self.words = config.path_of(self.cfg, "words_file")
        self.engine = SpeechEngine(self.cfg)
        self.ears = Ears(self.cfg["end_of_speech_ms"], self.load_wake())
        self.voice = Voice(self.ears)
        self.brain = ClaudeBrain(self.cfg, Approver(self.voice.say, self.hear_answer))
        self.state = "idle"
        self.turn_id = 0
        self.task = None
        self.mode = None
        self.waiting = None
        self.armed_at = None
        self.arm_reason = None
        self.background = set()

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        return task

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
            self.spawn(self.finish_answer(audio))
        else:
            self.task = asyncio.create_task(self.turn(audio, at, self.task, self.turn_id))

    def load_wake(self):
        if self.cfg["listen_mode"] != "wake":
            return None
        model = config.path_of(self.cfg, "wake_model")
        if not model.exists():
            print(f"no wake word model at {model}; using the hotkey instead")
            self.cfg["listen_mode"] = "hotkey"
            return None
        return Wake(model, self.cfg["wake_threshold"], config.path_of(self.cfg, "wake_verifier"))

    def always(self):
        return self.cfg["listen_mode"] in ("always", "wake") and self.mode is None

    def armed(self, extra=0.0):
        if self.cfg["listen_mode"] != "wake":
            return True
        return self.armed_at is not None and time.monotonic() - self.armed_at < self.cfg["wake_window_seconds"] + extra

    def woke(self):
        if not self.always():
            return
        events.emit("wake")
        self.armed_at = time.monotonic()
        self.arm_reason = "wake"
        if self.waiting is None and self.state == "idle":
            self.set("listening")
            self.spawn(self.calm_down(self.armed_at))

    async def calm_down(self, stamp):
        await asyncio.sleep(self.cfg["wake_window_seconds"] + 2)
        if self.state == "listening" and self.armed_at == stamp and self.mode is None:
            self.set("idle")

    def speech_started(self):
        if not self.always() or self.waiting is not None:
            return
        if self.state == "speaking":
            self.interrupt()
            self.armed_at = time.monotonic()
            self.arm_reason = "talk"
            self.set("listening")
        elif self.armed() and self.state == "idle":
            self.set("listening")

    def spoke(self, audio):
        if not self.always():
            return
        if self.waiting is not None and not self.waiting.done():
            self.armed_at = self.arm_reason = None
            self.spawn(self.finish_answer(audio))
        elif not self.armed(len(audio) / 16000):
            if self.state == "listening":
                self.set("idle")
        else:
            need_word = self.cfg["listen_mode"] == "wake" and self.arm_reason == "wake"
            self.armed_at = None
            self.task = asyncio.create_task(self.turn(audio, time.perf_counter(), self.task, self.turn_id, need_word))

    def interrupt(self):
        self.turn_id += 1
        self.voice.stop()
        if self.task and not self.task.done():
            self.spawn(self.brain.interrupt())
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

    async def turn(self, audio, at, previous, mine, need_word=False):
        text = None
        if previous and not previous.done():
            text = await asyncio.to_thread(self.engine.transcribe, audio, self.words)
            if (not need_word or said_wake_word(text)) and strip(text).lower().strip(" .,!?") in STOP_WORDS:
                events.emit("heard", text)
                self.interrupt()
                await previous
                return self.set("idle")
        else:
            self.set("thinking")
        if previous:
            await previous
        self.set("thinking")
        try:
            await self.run_turn(audio, at, mine, need_word, text)
        except Exception as error:
            events.emit("error", repr(error))
            print(f"  error: {error!r}")
            self.voice.stop()
            if mine == self.turn_id:
                self.set("idle")

    async def run_turn(self, audio, at, mine, need_word=False, text=None):
        if text is None:
            text = await asyncio.to_thread(self.engine.transcribe, audio, self.words)
        if mine != self.turn_id:
            return
        events.emit("heard", text)
        print(f"  you: {text or '(nothing heard)'}")
        if need_word and not said_wake_word(text):
            events.emit("wake_not_confirmed", text)
            return self.set("idle")
        command = strip(text)
        if text and not command:
            self.armed_at = time.monotonic()
            self.arm_reason = "confirmed"
            self.spawn(self.calm_down(self.armed_at))
            return self.set("listening" if self.cfg["listen_mode"] == "wake" else "idle")
        if not command or command.lower().strip(" .,!?") in STOP_WORDS:
            return self.set("idle")
        text = command
        first = True
        async for sentence in self.brain.ask(text):
            if mine != self.turn_id:
                continue
            if first:
                first = False
                self.set("speaking")
                self.spawn(self.report(at, mine))
            print(f"  cosmo: {sentence}")
            self.voice.say(sentence)
        if mine == self.turn_id:
            await asyncio.to_thread(self.voice.wait)
            if mine == self.turn_id:
                self.set("idle")


    async def announce(self):
        async for sentence in self.brain.announcements():
            while self.state != "idle":
                await asyncio.sleep(0.3)
            print(f"  cosmo (on its own): {sentence}")
            self.set("speaking")
            self.voice.say(sentence)
            await asyncio.to_thread(self.voice.wait)
            if self.state == "speaking":
                self.set("idle")


async def run():
    cosmo = Cosmo()
    loop = asyncio.get_running_loop()
    cosmo.ears.on_start = lambda: loop.call_soon_threadsafe(cosmo.speech_started)
    cosmo.ears.on_utterance = lambda audio: loop.call_soon_threadsafe(cosmo.spoke, audio)
    cosmo.ears.on_wake = lambda: loop.call_soon_threadsafe(cosmo.woke)
    watch(cosmo.cfg["hotkey"], asyncio.get_running_loop(), cosmo.pressed, cosmo.released)
    cosmo.spawn(cosmo.announce())
    events.emit("ready")
    how = {"always": "just talk", "wake": "say hey cosmo"}.get(cosmo.cfg["listen_mode"], f"hold {cosmo.cfg['hotkey']} to talk, release to send")
    print(f"ready. {how}; talk over Cosmo or press the key to interrupt. ctrl-c quits.")
    try:
        await asyncio.Event().wait()
    finally:
        await cosmo.brain.close()
