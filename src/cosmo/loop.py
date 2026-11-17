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
        self.approver = Approver(self.voice.say, self.hear_answer)
        self.brain = ClaudeBrain(self.cfg, self.approver)
        self.helper = None
        self.last_brain = None
        self.state = "idle"
        self.turn_id = 0
        self.task = None
        self.mode = None
        self.waiting = None
        self.armed_at = None
        self.arm_reason = None
        self.gate = None
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
            if self.state == "speaking":
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
            need_word, gate_in, gate_out = False, None, None
            if self.cfg["listen_mode"] == "wake" and self.arm_reason == "wake":
                need_word = True
                gate_out = self.gate = asyncio.get_running_loop().create_future()
                self.arm_reason, self.armed_at = "pending", time.monotonic()
            elif self.cfg["listen_mode"] == "wake" and self.arm_reason == "pending":
                gate_in, self.armed_at = self.gate, None
            else:
                self.armed_at = None
            turn = self.turn(audio, time.perf_counter(), self.task, self.turn_id, need_word, gate_in, gate_out)
            self.task = asyncio.create_task(turn)

    def interrupt(self):
        self.turn_id += 1
        self.voice.stop()
        for brain in (self.brain, self.helper):
            if brain is not None and brain.busy:
                self.spawn(brain.interrupt())
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

    async def turn(self, audio, at, previous, mine, need_word=False, gate_in=None, gate_out=None):
        brain = None
        try:
            text = None
            if gate_in is None and (self.busy() or (previous and not previous.done())):
                text = await asyncio.to_thread(self.engine.transcribe, audio, self.words)
                if (not need_word or said_wake_word(text)) and strip(text).lower().strip(" .,!?") in STOP_WORDS:
                    events.emit("heard", text)
                    self.interrupt()
                    return self.set("idle")
            if gate_in is not None:
                if previous:
                    await previous
                if not await gate_in:
                    if self.state == "listening" and mine == self.turn_id and self.mode is None:
                        self.set("idle")
                    return
                brain = await self.take(self.last_brain or self.brain)
            else:
                brain = self.take_free() or await self.take(self.brain)
            self.last_brain = brain
            if mine == self.turn_id:
                self.set("thinking")
            await self.run_turn(audio, at, mine, need_word, text, gate_out, brain)
        except Exception as error:
            events.emit("error", repr(error))
            print(f"  error: {error!r}")
            self.voice.stop()
            if mine == self.turn_id:
                self.set("idle")
        finally:
            if gate_out is not None and not gate_out.done():
                gate_out.set_result(False)
            self.end_pending(gate_out)
            self.release(brain)

    def end_pending(self, gate):
        if gate is not None and self.gate is gate and self.arm_reason == "pending":
            self.armed_at = self.arm_reason = None

    def grab(self, brain):
        brain.taken, brain.holder = True, asyncio.current_task()
        return brain

    def take_free(self):
        if self.helper is None:
            self.helper = ClaudeBrain(self.cfg, self.approver, name="cosmo-helper")
            self.spawn(self.announce(self.helper))
        if self.brain.taken and self.brain.cancelled:
            return None
        for brain in (self.brain, self.helper):
            if not brain.taken:
                return self.grab(brain)
        return None

    async def take(self, brain):
        while brain.taken and brain.holder is not None and not brain.holder.done():
            await asyncio.shield(brain.holder)
        return self.grab(brain)

    def release(self, brain):
        if brain is not None and brain.holder is asyncio.current_task():
            brain.taken = False

    def busy(self):
        return any(b is not None and (b.taken or b.busy) for b in (self.brain, self.helper))

    async def run_turn(self, audio, at, mine, need_word=False, text=None, gate_out=None, brain=None):
        brain = brain or self.brain
        if text is None:
            text = await asyncio.to_thread(self.engine.transcribe, audio, self.words)
        if mine != self.turn_id:
            return
        events.emit("heard", text)
        print(f"  you: {text or '(nothing heard)'}")
        if need_word and not said_wake_word(text):
            events.emit("wake_not_confirmed", text)
            self.end_pending(gate_out)
            return self.set("idle")
        if gate_out is not None:
            gate_out.set_result(True)
        command = strip(text)
        if text and not command:
            self.armed_at = time.monotonic()
            self.arm_reason = "confirmed"
            self.spawn(self.calm_down(self.armed_at))
            return self.set("listening" if self.cfg["listen_mode"] == "wake" else "idle")
        self.end_pending(gate_out)
        if not command or command.lower().strip(" .,!?") in STOP_WORDS:
            return self.set("idle")
        text = command
        spoke = False
        stream = brain.ask(text)
        try:
            async for sentence in stream:
                if mine != self.turn_id:
                    continue
                if not spoke:
                    spoke = True
                    self.set("speaking")
                    self.spawn(self.report(at, mine))
                print(f"  cosmo: {sentence}")
                self.voice.say(sentence)
        finally:
            await stream.aclose()
            self.release(brain)
        if mine == self.turn_id:
            if spoke:
                await asyncio.to_thread(self.voice.wait)
            if mine == self.turn_id:
                self.settle()

    def settle(self):
        self.set("thinking" if self.busy() else "idle")

    async def announce(self, brain):
        async for sentence in brain.announcements():
            while self.state not in ("idle", "thinking"):
                await asyncio.sleep(0.3)
            print(f"  cosmo (on its own): {sentence}")
            self.set("speaking")
            self.voice.say(sentence)
            await asyncio.to_thread(self.voice.wait)
            if self.state == "speaking":
                self.settle()


async def run():
    cosmo = Cosmo()
    loop = asyncio.get_running_loop()
    cosmo.ears.on_start = lambda: loop.call_soon_threadsafe(cosmo.speech_started)
    cosmo.ears.on_utterance = lambda audio: loop.call_soon_threadsafe(cosmo.spoke, audio)
    cosmo.ears.on_wake = lambda: loop.call_soon_threadsafe(cosmo.woke)
    watch(cosmo.cfg["hotkey"], asyncio.get_running_loop(), cosmo.pressed, cosmo.released)
    cosmo.spawn(cosmo.announce(cosmo.brain))
    events.emit("ready")
    how = {"always": "just talk", "wake": "say hey cosmo"}.get(cosmo.cfg["listen_mode"], f"hold {cosmo.cfg['hotkey']} to talk, release to send")
    print(f"ready. {how}; talk over Cosmo or press the key to interrupt. ctrl-c quits.")
    try:
        await asyncio.Event().wait()
    finally:
        await cosmo.brain.close()
        if cosmo.helper is not None:
            await cosmo.helper.close()
