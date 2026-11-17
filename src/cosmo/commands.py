import asyncio
import json
import time

from . import events, sessions
from .config import HOME

COMMANDS = HOME / "commands.jsonl"
SESSIONS_FILE = HOME / "sessions.json"
POLL = 0.2
SNAPSHOT_EVERY = 10
SHORT = 140


def handle(cosmo, command):
    kind = command.get("cmd")
    if kind == "approve":
        if cosmo.waiting is not None and not cosmo.waiting.done():
            cosmo.waiting.set_result("yes" if command.get("answer") == "yes" else "no")
    elif kind == "stop":
        cosmo.interrupt()
        cosmo.set("idle")
    elif kind == "ask" and str(command.get("text", "")).strip():
        text = str(command["text"]).strip()
        cosmo.task = asyncio.create_task(cosmo.turn(None, time.perf_counter(), cosmo.task, cosmo.turn_id, typed=text))


async def follow(cosmo):
    HOME.mkdir(exist_ok=True)
    COMMANDS.touch()
    offset = COMMANDS.stat().st_size
    while True:
        await asyncio.sleep(POLL)
        try:
            size = COMMANDS.stat().st_size
        except FileNotFoundError:
            COMMANDS.touch()
            offset = 0
            continue
        if size < offset:
            offset = 0
        if size == offset:
            continue
        with COMMANDS.open("rb") as f:
            f.seek(offset)
            chunk = f.read(size - offset)
        complete = chunk.rfind(b"\n") + 1
        offset += complete
        for line in chunk[:complete].decode("utf-8", "ignore").splitlines():
            try:
                handle(cosmo, json.loads(line))
            except Exception as error:
                events.emit("error", f"command: {error!r}")


def session_rows():
    rows = []
    for info in sorted(sessions.live(), key=lambda i: -i.get("updatedAt", 0)):
        said = sessions.texts(sessions.entries(info), "assistant")
        rows.append({
            "name": info.get("name", ""),
            "status": info.get("status", ""),
            "folder": info.get("cwd", ""),
            "topic": sessions.first_prompt(info)[:SHORT],
            "last": said[-1][:SHORT] if said else "",
            "updated": info.get("updatedAt", 0),
        })
    return rows


async def snapshot_sessions():
    while True:
        try:
            rows = await asyncio.to_thread(session_rows)
            temp = SESSIONS_FILE.with_suffix(".tmp")
            temp.write_text(json.dumps(rows), encoding="utf-8")
            temp.replace(SESSIONS_FILE)
        except Exception as error:
            events.emit("error", f"sessions snapshot: {error!r}")
        await asyncio.sleep(SNAPSHOT_EVERY)
