import asyncio
import os
import re

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, StreamEvent

CREDENTIALS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL")
VOICE_STYLE = (
    "You are being spoken aloud. Answer in short plain sentences, no markdown, no code blocks. "
    "When you pass the user's decision to another session, start with 'User says:' and quote his words."
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


def text_of(msg):
    delta = msg.event.get("delta") or {} if isinstance(msg, StreamEvent) else {}
    if isinstance(msg, StreamEvent) and msg.event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
        return delta["text"]
    return None


class ClaudeBrain:
    def __init__(self, cfg, approve):
        for name in CREDENTIALS:
            os.environ.pop(name, None)
        self.client = ClaudeSDKClient(
            options=ClaudeAgentOptions(
                include_partial_messages=True,
                setting_sources=["user", "project", "local"],
                system_prompt={"type": "preset", "preset": "claude_code", "append": VOICE_STYLE},
                can_use_tool=approve,
                model=cfg["brain_model"],
                cwd=os.path.expanduser("~"),
                extra_args={"name": NAME},
            )
        )
        self.connected = False
        self.cancelled = False
        self.busy = False
        self.unsolicited = False
        self.answers = asyncio.Queue()
        self.extras = asyncio.Queue()
        self.reader = None

    async def _read(self):
        async for msg in self.client.receive_messages():
            if not self.busy and not self.unsolicited:
                self.unsolicited = True
            to_extras = self.unsolicited
            if isinstance(msg, ResultMessage):
                self.unsolicited = False
            (self.extras if to_extras else self.answers).put_nowait(msg)

    async def _connect(self):
        await self.client.connect()
        self.connected = True
        self.reader = asyncio.create_task(self._read())

    async def ask(self, text):
        if not self.connected:
            await self._connect()
        self.cancelled = False
        self.busy = True
        finished = False
        try:
            await self.client.query(text)
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
            if not finished:
                await self.client.interrupt()
                while not isinstance(await self.answers.get(), ResultMessage):
                    pass
            self.busy = False

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

    async def interrupt(self):
        self.cancelled = True
        if self.connected:
            await self.client.interrupt()

    async def close(self):
        if self.connected:
            self.reader.cancel()
            await self.client.disconnect()
            self.connected = False
