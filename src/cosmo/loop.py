import asyncio
import os
import time

from . import commands, config, events
from .approvals import Approver
from .brain import ClaudeBrain
from .ears import Ears
from .hotkey import watch
from .stt import SpeechEngine
from .tts import Voice
from .wake import Wake, said_wake_word, strip
from . import voiceid
from .voiceid import VoiceID


STOP_WORDS = {"stop", "cancel", "quiet", "never mind", "nevermind", "be quiet", "shut up", "enough"}


class Cosmo:
    def __init__(self):
        self.cfg = config.load()
        self.words = config.path_of(self.cfg, "words_file")
        self.engine = SpeechEngine(self.cfg)
        self.voice_id = VoiceID(self.cfg["voice_id_threshold"]) if self.cfg["listen_mode"] in ("wake", "always") and voiceid.PROFILE.exists() else None
        self.ears = Ears(self.cfg["end_of_speech_ms"], self.load_wake())
        self.voice = Voice(self.ears)
        self.approver = Approver(self.voice.say, self.hear_answer)
        self.brain = ClaudeBrain(self.cfg, self.approver)
        self.last_brain = None
        self.next_uid = 0
        self.cancelled_turns = set()
        self.speakers = {}
        self.state = "idle"
        self.turn_id = 0
        self.task = None
        self.mode = None
        self.waiting = None
        self.armed_at = None
        self.arm_reason = None
        self.gate = None
        self.barge = None
        self.background = set()

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        return task

    def set(self, state, detail=""):
        if self.waiting is not None and not self.waiting.done() and state != "waiting_for_approval":
            return
        self.state = state
        events.emit(state, detail)

    def pressed(self):
        if self.waiting is not None and not self.waiting.done():
            self.voice.stop()
            self.mode = "answer"
        else:
            if self.talking():
                self.talk_over()
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
        if self.voice_busy():
            self.barge = self.spawn(self.early_barge())
        elif self.armed() and self.state == "idle":
            self.set("listening")

    def can_barge(self):
        return self.always() and self.waiting is None

    async def early_barge(self):
        await asyncio.sleep(1.0)
        if not (self.voice_busy() and self.can_barge()):
            return
        if self.voice_id is None or await asyncio.to_thread(self.voice_id.is_me, self.ears.last_second()):
            if self.voice_busy() and self.can_barge():
                self.take_floor()

    def take_floor(self):
        if self.voice_busy():
            self.talk_over()
        self.armed_at = time.monotonic()
        self.arm_reason = "talk"
        self.set("listening")

    async def late_barge(self, audio):
        if await self.is_me(audio) and self.can_barge():
            self.take_floor()
            self.spoke(audio)

    def spoke(self, audio):
        if not self.always():
            return
        early, self.barge = self.barge, None
        if early is not None and not early.done():
            early.cancel()
        overlapped = time.monotonic() - len(audio) / 16000 < self.ears.last_sound
        if self.waiting is not None and not self.waiting.done():
            if time.monotonic() - len(audio) / 16000 < self.ears.last_sound + 0.3:
                return
            self.armed_at = self.arm_reason = None
            self.spawn(self.finish_answer(audio, check_voice=True))
        elif overlapped and not (self.arm_reason == "talk" and self.armed(len(audio) / 16000)):
            self.spawn(self.late_barge(audio))
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
                self.armed_at = self.arm_reason = None
            turn = self.turn(audio, time.perf_counter(), self.task, self.turn_id, need_word, gate_in, gate_out, check_voice=True)
            self.task = asyncio.create_task(turn)

    def voice_busy(self):
        return self.voice.pending > 0

    def talking(self):
        return bool(self.speakers) or self.voice_busy()

    def alive(self, epoch, uid):
        return epoch == self.turn_id and uid not in self.cancelled_turns

    def talk_over(self):
        self.voice.stop()
        for uid, (brain, task) in list(self.speakers.items()):
            self.cancelled_turns.add(uid)
            if brain.holder is task and (brain.busy or brain.taken):
                self.spawn(brain.interrupt(brain.asks))
        self.speakers.clear()
        events.emit("talked_over")

    def interrupt(self):
        self.turn_id += 1
        self.speakers.clear()
        self.voice.stop()
        for brain in (self.brain,):
            if brain.busy or brain.taken:
                self.spawn(brain.interrupt())
        events.emit("interrupted")

    async def hear_answer(self):
        self.set("waiting_for_approval")
        self.waiting = asyncio.get_running_loop().create_future()
        try:
            return await self.waiting
        finally:
            self.waiting = None
            self.settle()

    async def is_me(self, audio):
        if self.voice_id is None:
            return True
        if await asyncio.to_thread(self.voice_id.is_me, audio):
            return True
        events.emit("not_you", "ignored a voice that isn’t yours")
        return False

    async def finish_answer(self, audio, check_voice=False):
        if check_voice and not await self.is_me(audio):
            return
        text = await asyncio.to_thread(self.engine.transcribe, audio, self.words)
        events.emit("heard", text)
        if self.waiting is not None and not self.waiting.done():
            self.waiting.set_result(text)

    async def report(self, at, turn_id):
        if await asyncio.to_thread(self.voice.speaking.wait, 15) and turn_id == self.turn_id:
            seconds = time.perf_counter() - at
            events.emit("first_sound", f"{seconds:.2f}s")
            print(f"  first sound {seconds:.2f}s after you let go")

    async def turn(self, audio, at, previous, mine, need_word=False, gate_in=None, gate_out=None, typed=None, check_voice=False):
        brain = None
        self.next_uid += 1
        uid = self.next_uid
        try:
            if check_voice and not await self.is_me(audio):
                return self.settle()
            text = typed
            if text is None and gate_in is None and (self.busy() or (previous and not previous.done())):
                text = await asyncio.to_thread(self.engine.transcribe, audio, self.words)
                if (not need_word or said_wake_word(text)) and strip(text).lower().strip(" .,!?") in STOP_WORDS:
                    events.emit("heard", text)
                    self.interrupt()
                    return self.set("idle")
            if gate_in is not None:
                if previous:
                    await previous
                if not await gate_in:
                    if self.state == "listening" and self.alive(mine, uid) and self.mode is None:
                        self.settle()
                    return
                brain = await self.take(self.last_brain or self.brain)
            else:
                brain = self.take_free() or await self.take(self.brain)
            self.last_brain = brain
            if self.alive(mine, uid) and not self.voice_busy():
                self.set("thinking")
            await self.run_turn(audio, at, mine, need_word, text, gate_out, brain, uid)
        except Exception as error:
            events.emit("error", repr(error))
            print(f"  error: {error!r}")
            if self.alive(mine, uid):
                self.settle()
        finally:
            if gate_out is not None and not gate_out.done():
                gate_out.set_result(False)
            self.end_pending(gate_out)
            self.release(brain)
            self.speakers.pop(uid, None)
            self.cancelled_turns.discard(uid)

    def end_pending(self, gate):
        if gate is not None and self.gate is gate and self.arm_reason == "pending":
            self.armed_at = self.arm_reason = None

    def grab(self, brain):
        brain.taken, brain.holder = True, asyncio.current_task()
        return brain

    def take_free(self):
        return None if self.brain.taken else self.grab(self.brain)

    async def take(self, brain):
        while brain.taken and brain.holder is not None and not brain.holder.done():
            await asyncio.shield(brain.holder)
        return self.grab(brain)

    def release(self, brain):
        if brain is not None and brain.holder is asyncio.current_task():
            brain.taken = False

    def busy(self):
        me = asyncio.current_task()
        return (self.brain.taken or self.brain.busy) and self.brain.holder is not me

    async def run_turn(self, audio, at, mine, need_word=False, text=None, gate_out=None, brain=None, uid=0):
        brain = brain or self.brain
        if text is None:
            text = await asyncio.to_thread(self.engine.transcribe, audio, self.words)
        if not self.alive(mine, uid):
            return
        events.emit("heard", text)
        print(f"  you: {text or '(nothing heard)'}")
        if need_word and not said_wake_word(text):
            events.emit("wake_not_confirmed", text)
            self.end_pending(gate_out)
            return self.settle()
        if gate_out is not None:
            gate_out.set_result(True)
        command = strip(text)
        if text and not command:
            self.armed_at = time.monotonic()
            self.arm_reason = "confirmed"
            self.spawn(self.calm_down(self.armed_at))
            return self.set("listening") if self.cfg["listen_mode"] == "wake" else self.settle()
        self.end_pending(gate_out)
        if not command or command.lower().strip(" .,!?") in STOP_WORDS:
            return self.settle()
        text = command
        spoke = False
        stream = brain.ask(text)
        try:
            async for sentence in stream:
                if not self.alive(mine, uid):
                    continue
                if not spoke:
                    spoke = True
                    self.speakers[uid] = (brain, asyncio.current_task())
                    self.spawn(self.report(at, mine))
                print(f"  cosmo: {sentence}")
                events.emit("said", sentence)
                self.voice.say(sentence)
                self.settle()
        finally:
            await stream.aclose()
            self.release(brain)
        if self.alive(mine, uid):
            if spoke:
                await asyncio.to_thread(self.voice.wait)
            self.speakers.pop(uid, None)
            if self.alive(mine, uid):
                self.settle()

    def settle(self):
        if self.waiting is not None and not self.waiting.done():
            return
        self.set("speaking" if self.voice_busy() else "thinking" if self.busy() else "idle")

    async def announce(self, brain):
        async for sentence in brain.announcements():
            while self.talking() or self.waiting is not None or self.state == "listening":
                await asyncio.sleep(0.3)
            print(f"  cosmo (on its own): {sentence}")
            events.emit("said", sentence)
            self.voice.say(sentence)
            self.settle()
            await asyncio.to_thread(self.voice.wait)
            if self.state == "speaking":
                self.settle()


async def report_glitches(ears):
    seen = 0
    while True:
        await asyncio.sleep(10)
        if ears.glitches > seen:
            events.emit("audio_glitch", f"{ears.glitches - seen} audio dropouts in the last 10 s")
            seen = ears.glitches


async def run():
    cosmo = Cosmo()
    loop = asyncio.get_running_loop()
    cosmo.ears.on_start = lambda: loop.call_soon_threadsafe(cosmo.speech_started)
    cosmo.ears.on_utterance = lambda audio: loop.call_soon_threadsafe(cosmo.spoke, audio)
    cosmo.ears.on_wake = lambda: loop.call_soon_threadsafe(cosmo.woke)
    watch(cosmo.cfg["hotkey"], asyncio.get_running_loop(), cosmo.pressed, cosmo.released)
    cosmo.spawn(cosmo.announce(cosmo.brain))
    pid_file = config.HOME / "cosmo.pid"
    pid_file.write_text(str(os.getpid()))
    cosmo.spawn(commands.follow(cosmo))
    cosmo.spawn(commands.snapshot_sessions())
    cosmo.spawn(report_glitches(cosmo.ears))
    events.emit("ready")
    how = {"always": "just talk", "wake": "say hey cosmo"}.get(cosmo.cfg["listen_mode"], f"hold {cosmo.cfg['hotkey']} to talk, release to send")
    print(f"ready. {how}; talk over Cosmo or press the key to interrupt. ctrl-c quits.")
    try:
        await asyncio.Event().wait()
    finally:
        pid_file.unlink(missing_ok=True)
        await cosmo.brain.close()
