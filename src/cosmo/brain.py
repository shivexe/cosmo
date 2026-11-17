import os
import re

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, StreamEvent

CREDENTIALS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL")
VOICE_STYLE = "You are being spoken aloud. Answer in short plain sentences, no markdown, no code blocks."
END = re.compile(r"[.!?](?:\s)")
MIN_WORDS = 3


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
            )
        )
        self.connected = False
        self.cancelled = False

    async def ask(self, text):
        if not self.connected:
            await self.client.connect()
            self.connected = True
        self.cancelled = False
        await self.client.query(text)
        buffer = ""
        async for msg in self.client.receive_response():
            if self.cancelled:
                continue
            if isinstance(msg, StreamEvent):
                delta = msg.event.get("delta") or {}
                if msg.event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    sentences, buffer = take_sentences(buffer + delta["text"])
                    for s in sentences:
                        yield s
            elif isinstance(msg, ResultMessage) and buffer.strip():
                yield buffer.strip()

    async def interrupt(self):
        self.cancelled = True
        if self.connected:
            await self.client.interrupt()

    async def close(self):
        if self.connected:
            await self.client.disconnect()
            self.connected = False
