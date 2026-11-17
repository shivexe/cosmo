import asyncio
import json
import os
import re
import sys

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
)

from . import events, hosts, sessions
from .config import HOME

BRAIN_FILE = HOME / "brain.json"

CREDENTIALS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL")
SESSIONS = f"{sys.executable} -m cosmo.sessions"
FRONT_MODEL = "claude-sonnet-5-5"
VOICE_STYLE = (
    "You are Cosmo, USER's voice agent. You are spoken aloud. Don't yap: keep every reply as short as possible, "
    "one sentence when you can, plain words, no markdown or lists, and never repeat what you already said. "
    "Give details only when they ask for them. Your only job is to talk with them and stay free. Never do work yourself and never call tools "
    "yourself, except the Agent tool to start workers and TaskStop to stop a worker when USER asks. For anything that needs a tool (commands, files, their other Claude sessions, "
    "connectors like Slack, Linear or calendar, the web), start one Agent with run_in_background true and "
    "model opus, give it a complete task description, then tell them in one sentence what you started. "
    "When a worker finishes you are told; then tell them just the result in one short sentence. "
    "If they ask what you are doing, say in one sentence what each running worker is working on. "
    "For tasks about their other Claude Code sessions, put these instructions in the worker's task: "
    f"`{SESSIONS} list` lists live sessions and what each works on; "
    f"`{SESSIONS} find <words>` ranks sessions by a topic such as a ticket, PR number or feature; "
    f"`{SESSIONS} read \"<name>\" [n]` gives its last n replies; "
    f"`{SESSIONS} wait \"<name>\" --after <unix seconds>` waits up to 100 seconds for a reply newer than that time. "
    "The worker finds the right session itself, uses read for status questions, and passes their words exactly with "
    "SendMessage to that session, starting with 'USER says:' and their words. Before sending it runs `date +%s` "
    "and passes that number to wait."
)
END = re.compile(r"[.!?](?:\s)")
MIN_WORDS = 3
NAME = "cosmo"


def take_sentences(buffer):
    out = []
    while True:
        for m in END.finditer(buffer):
            if len(buffer[: m.end()].split()) >= MIN_WORDS:
                out.append(buffer[: m.end()].strip())
                buffer = buffer[m.end() :]
                break
        else:
            return out, buffer


def report_worker(msg):
    if isinstance(msg, TaskStartedMessage):
        events.emit("worker_started", {"id": msg.task_id, "title": msg.description})
    elif isinstance(msg, TaskProgressMessage):
        events.emit("worker_progress", {"id": msg.task_id, "step": msg.description, "tool": msg.last_tool_name or ""})
    elif isinstance(msg, TaskNotificationMessage):
        events.emit("worker_done", {"id": msg.task_id, "status": str(msg.status), "summary": msg.summary})
    elif isinstance(msg, TaskUpdatedMessage) and msg.status in ("completed", "failed", "killed", "stopped"):
        events.emit("worker_done", {"id": msg.task_id, "status": str(msg.status), "summary": ""})


def text_of(msg):
    delta = msg.event.get("delta") or {} if isinstance(msg, StreamEvent) else {}
    if isinstance(msg, StreamEvent) and msg.event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
        return delta["text"]
    if isinstance(msg, StreamEvent) and msg.event.get("type") == "content_block_stop":
        return "\n"
    return None


class ClaudeBrain:
    def __init__(self, cfg, approve, name=NAME):
        for variable in CREDENTIALS:
            os.environ.pop(variable, None)
        self.client = ClaudeSDKClient(
            options=ClaudeAgentOptions(
                include_partial_messages=True,
                setting_sources=["user", "project", "local"],
                system_prompt={"type": "preset", "preset": "claude_code", "append": (VOICE_STYLE + hosts.instructions()).replace("USER", cfg.get("user_name") or "the user")},
                can_use_tool=approve,
                model=cfg["brain_model"] or FRONT_MODEL,
                cwd=os.path.expanduser("~"),
                extra_args={"name": name},
            )
        )
        self.connected = False
        self.cancelled = False
        self.taken = False
        self.holder = None
        self.asks = 0
        self.busy = False
        self.answers = asyncio.Queue()
        self.extras = asyncio.Queue()
        self.reader = None
        self.session_id = None

    async def _read(self):
        async for msg in self.client.receive_messages():
            self._note_session(getattr(msg, "session_id", None))
            report_worker(msg)
            (self.answers if self.busy else self.extras).put_nowait(msg)

    def _note_session(self, sid):
        if not sid or sid == self.session_id:
            return
        self.session_id = sid
        path = sessions.transcript({"cwd": os.path.expanduser("~"), "sessionId": sid})
        temp = BRAIN_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps({"session": sid, "transcript": str(path)}), encoding="utf-8")
        temp.replace(BRAIN_FILE)

    async def _connect(self):
        await self.client.connect()
        self.connected = True
        self.reader = asyncio.create_task(self._read())

    async def ask(self, text):
        if not self.connected:
            await self._connect()
        self.cancelled = False
        self.busy = True
        self.asks += 1
        finished = sent = False
        try:
            await self.client.query(text)
            sent = True
            buffer = ""
            while True:
                msg = await self.answers.get()
                if isinstance(msg, ResultMessage):
                    finished = True
                    if buffer.strip() and not self.cancelled:
                        yield buffer.strip()
                    return
                piece = text_of(msg)
                if piece is not None and not self.cancelled:
                    sentences, buffer = take_sentences(buffer + piece)
                    for s in sentences:
                        yield s
        finally:
            if sent and not finished:
                try:
                    await asyncio.wait_for(self.drain(), 30)
                except Exception:
                    pass
            self.busy = False

    async def drain(self):
        await self.client.interrupt()
        while not isinstance(await self.answers.get(), ResultMessage):
            pass

    async def announcements(self):
        buffer = ""
        while True:
            msg = await self.extras.get()
            if isinstance(msg, ResultMessage):
                if buffer.strip():
                    yield buffer.strip()
                buffer = ""
                continue
            piece = text_of(msg)
            if piece is not None:
                sentences, buffer = take_sentences(buffer + piece)
                for s in sentences:
                    yield s

    async def interrupt(self, only_ask=None):
        if only_ask is not None and only_ask != self.asks:
            return
        self.cancelled = True
        if self.connected:
            await self.client.interrupt()

    async def close(self):
        if self.connected:
            self.reader.cancel()
            await self.client.disconnect()
            self.connected = False
