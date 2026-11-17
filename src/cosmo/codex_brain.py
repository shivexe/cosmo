import asyncio
import json
import os
import signal
import uuid
from datetime import datetime, timezone

from claude_agent_sdk import PermissionResultAllow

from . import events, hosts
from .brain import BRAIN_FILE, SESSIONS, ClaudeBrain, take_sentences
from .config import HOME

HOME_DIR = os.path.expanduser("~")
TRANSCRIPTS = HOME / "codex"
FRONT_MODEL = "gpt-5.6-luna"
LEAN = {"features": {"shell_tool": False, "unified_exec": False, "multi_agent": False, "apps": False, "plugins": False, "memories": False, "hooks": False}}
FRONT_STYLE = (
    "You are Cosmo, USER's voice agent. You are spoken aloud. Don't yap: keep every reply as short as possible, "
    "one sentence when you can, plain words, no markdown or lists, and never repeat what you already said. "
    "Give details only when they ask for them. Your only job is to talk with them and stay free. Never do work yourself, "
    "but remember what they tell you yourself without a worker. "
    "For anything that needs a tool (commands, files, their Claude or Codex sessions, connectors like Slack, Linear or "
    "calendar, the web), call start_worker with a complete task description, then say in a few words what you are doing "
    "as if you do it yourself, like 'Opening TextEdit.' Never mention workers, agents, background tasks or tools. Call stop_workers only when USER asks to stop them. A finished worker's result is read to USER directly and "
    "reaches you later as a background note, so never repeat it unless they ask. "
    "If they ask what you are doing, say what you are working on."
)
TOOLS = [
    {"type": "function", "name": "start_worker", "description": "Start a background worker that can run commands, read and write files, use connectors and the web on this Mac.",
     "inputSchema": {"type": "object", "properties": {"task": {"type": "string", "description": "Complete task description."}}, "required": ["task"]}},
    {"type": "function", "name": "stop_workers", "description": "Stop every running worker.", "inputSchema": {"type": "object", "properties": {}}},
]
WORKER_STYLE = (
    "You are a background worker for Cosmo, USER's voice agent. Do the task completely, then end with the result in one "
    "or two short plain sentences, no markdown, because it is read aloud. Commands run in a read-only sandbox without "
    "network; when you need to write files or use the network, ask for escalated permissions so USER can approve. "
    f"For USER's Claude Code sessions: `{SESSIONS} list` lists live sessions and what each works on; "
    f"`{SESSIONS} find <words>` ranks sessions by a topic; `{SESSIONS} read \"<name>\" [n]` gives its last n replies; "
    f"`{SESSIONS} wait \"<name>\" --after <unix seconds>` waits up to 100 seconds for a reply newer than that time; "
    f"`{SESSIONS} send \"<name>\" <<'EOF'` followed by the text on its own lines and a final `EOF` line delivers that "
    "text into that live session and needs escalated permissions; always pass the text through that quoted heredoc, never as an argument. "
    "To pass USER's words to a session, find the right session yourself, run `date +%s`, send 'USER says:' followed by "
    "their words exactly, then wait with that number."
)


def make_brain(cfg, approve):
    return CodexBrain(cfg, approve) if cfg.get("brain") == "codex" else ClaudeBrain(cfg, approve)


def turn_of(params):
    return params.get("turnId") or (params.get("turn") or {}).get("id")


class CodexBrain:
    def __init__(self, cfg, approve):
        self.approve = approve
        self.user = cfg.get("user_name") or "the user"
        self.model = cfg.get("brain_model") or FRONT_MODEL
        self.connected = False
        self.cancelled = False
        self.taken = False
        self.holder = None
        self.asks = 0
        self.busy = False
        self.proc = None
        self.reader = None
        self.thread = None
        self.turn = None
        self.calls = {}
        self.next_id = 0
        self.front = asyncio.Queue()
        self.extras = asyncio.Queue()
        self.lock = asyncio.Lock()
        self.workers = {}
        self.files = {}
        self.count = 0
        self.notes = []
        self.tasks = set()
        self.log = None

    def _named(self, text):
        return text.replace("USER", self.user)

    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def _send(self, message):
        self.proc.stdin.write((json.dumps(message) + "\n").encode())

    async def _call(self, method, params):
        if self.reader is None or self.reader.done():
            raise RuntimeError("codex app-server is not running")
        self.next_id += 1
        future = self.calls[self.next_id] = asyncio.get_running_loop().create_future()
        self._send({"id": self.next_id, "method": method, "params": params})
        reply = await future
        if "error" in reply:
            raise RuntimeError(f"codex {method}: {reply['error'].get('message')}")
        return reply["result"]

    async def _connect(self):
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        self.proc = await asyncio.create_subprocess_exec(
            "codex", "app-server", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=2**24, env=env, start_new_session=True,
        )
        self.reader = asyncio.create_task(self._read())
        await self._call("initialize", {"clientInfo": {"name": "cosmo", "version": "1"}, "capabilities": {"experimentalApi": True}})
        self._send({"method": "initialized"})
        front = {"model": self.model, "cwd": HOME_DIR, "sandbox": "read-only", "approvalPolicy": "never", "config": LEAN,
                 "baseInstructions": self._named(FRONT_STYLE + hosts.instructions())}
        if self.thread:
            await self._call("thread/resume", {"threadId": self.thread, **front})
        else:
            self.thread = (await self._call("thread/start", {**front, "dynamicTools": TOOLS}))["thread"]["id"]
        self.connected = True
        self._point()

    def _point(self):
        TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
        self.log = TRANSCRIPTS / f"{self.thread}.jsonl"
        self.log.touch()
        temp = BRAIN_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps({"session": self.thread, "transcript": str(self.log)}), encoding="utf-8")
        temp.replace(BRAIN_FILE)

    def _log(self, role, content):
        if not self.log:
            return
        stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        row = {"type": role, "uuid": str(uuid.uuid4()), "timestamp": stamp, "sessionId": self.thread, "message": {"role": role, "content": content}}
        with self.log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    async def _read(self):
        try:
            async for line in self.proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if "method" not in msg:
                    future = self.calls.pop(msg.get("id"), None)
                    if future and not future.done():
                        future.set_result(msg)
                elif "id" in msg:
                    self._spawn(self._answer(msg, self.proc))
                elif (msg.get("params") or {}).get("threadId") == self.thread:
                    self.front.put_nowait(msg)
                else:
                    self._worker_event(msg)
        finally:
            self._lost()

    def _lost(self):
        self.connected = False
        for future in self.calls.values():
            if not future.done():
                future.set_result({"error": {"message": "app-server exited"}})
        self.calls.clear()
        self.front.put_nowait({"method": "turn/completed", "params": {"turn": {"id": self.turn}}})
        for wid in list(self.workers):
            self._finish(self.workers.pop(wid), "failed")

    async def _answer(self, msg, proc):
        method, params = msg["method"], msg.get("params") or {}
        if method == "item/tool/call":
            return self._send({"id": msg["id"], "result": self._tool(params.get("tool"), params.get("arguments") or {})})
        if method == "item/commandExecution/requestApproval":
            name, data = "Bash", {"command": params.get("command") or params.get("reason") or ""}
        elif method == "item/fileChange/requestApproval":
            name, data = "Edit", {"file_path": ", ".join(self.files.get(params.get("itemId"), [])) or params.get("grantRoot") or "files"}
        elif method == "mcpServer/elicitation/request":
            name, data = f"{params.get('serverName') or 'a connector'} to {params.get('message') or 'continue'}", {}
        else:
            return self._send({"id": msg["id"], "error": {"code": -32601, "message": f"cosmo does not handle {method}"}})
        try:
            allowed = isinstance(await self.approve(name, data, None), PermissionResultAllow)
        except Exception:
            allowed = False
        if method == "mcpServer/elicitation/request":
            result = {"action": "accept", "content": {}} if allowed else {"action": "decline"}
        else:
            result = {"decision": "accept" if allowed else "decline"}
        if self.connected and self.proc is proc:
            self._send({"id": msg["id"], "result": result})

    def _worker_event(self, msg):
        method, params = msg["method"], msg.get("params") or {}
        wid = params.get("threadId")
        worker = self.workers.get(wid)
        if worker is None:
            return
        item = params.get("item") or {}
        if method == "item/started" and item.get("type") == "fileChange":
            self.files[item["id"]] = [c.get("path", "") for c in item.get("changes") or []]
        if method == "item/started" and item.get("type") in ("commandExecution", "mcpToolCall", "fileChange", "webSearch"):
            step = item.get("command") or item.get("tool") or item.get("query") or ", ".join(self.files.get(item["id"], []))
            events.emit("worker_progress", {"id": worker["id"], "step": step, "tool": item.get("server") or item["type"]})
        elif method == "item/completed" and item.get("type") == "agentMessage":
            worker["text"] = item.get("text") or worker["text"]
        elif method == "turn/completed":
            self._finish(self.workers.pop(wid), (params.get("turn") or {}).get("status") or "completed")

    def _finish(self, worker, status):
        text = worker["text"] or f"A worker {status} without a result."
        events.emit("worker_done", {"id": worker["id"], "status": status, "summary": worker["text"]})
        self.notes.append(f"[Background note, not from {self.user}: worker {worker['id']} {status}. Task: {worker['task']} Result: {text}]")
        self._log("assistant", [{"type": "text", "text": text}])
        sentences, rest = take_sentences(text + " ")
        for sentence in sentences + ([rest.strip()] if rest.strip() else []):
            self.extras.put_nowait(sentence)

    def _tool(self, name, arguments):
        if name == "start_worker" and arguments.get("task"):
            self.count += 1
            self._spawn(self._start_worker(f"codex-{self.count}", arguments["task"]))
            text = f"Worker codex-{self.count} started."
        elif name == "stop_workers":
            self._spawn(self._stop_workers())
            text = f"Stopping {len(self.workers)} workers."
        else:
            return {"contentItems": [{"type": "inputText", "text": f"Unknown tool {name}."}], "success": False}
        detail = {"description": arguments["task"]} if name == "start_worker" else {}
        self._log("assistant", [{"type": "tool_use", "id": f"call_{uuid.uuid4().hex}", "name": name, "input": detail}])
        return {"contentItems": [{"type": "inputText", "text": text}], "success": True}

    async def _start_worker(self, wid, task):
        events.emit("worker_started", {"id": wid, "title": task})
        worker = {"id": wid, "task": task, "text": "", "turn": None}
        try:
            thread = (await self._call("thread/start", {"cwd": HOME_DIR, "sandbox": "read-only", "approvalPolicy": "on-request",
                                                         "developerInstructions": self._named(WORKER_STYLE + hosts.instructions())}))["thread"]["id"]
            self.workers[thread] = worker
            worker["turn"] = (await self._call("turn/start", {"threadId": thread, "input": [{"type": "text", "text": task}], "effort": "medium"}))["turn"]["id"]
        except Exception as error:
            worker["text"] = str(error)
            self.workers = {k: w for k, w in self.workers.items() if w is not worker}
            self._finish(worker, "failed")

    async def _stop_workers(self):
        for thread, worker in list(self.workers.items()):
            if worker["turn"]:
                try:
                    await self._call("turn/interrupt", {"threadId": thread, "turnId": worker["turn"]})
                except RuntimeError:
                    pass

    async def _turn(self, text):
        notes = len(self.notes)
        joined = "\n".join(self.notes + [text])
        self.turn = (await self._call("turn/start", {"threadId": self.thread, "input": [{"type": "text", "text": joined}], "effort": "low"}))["turn"]["id"]
        del self.notes[:notes]
        self._log("user", text)
        if self.cancelled:
            await self._call("turn/interrupt", {"threadId": self.thread, "turnId": self.turn})
        buffer = ""
        while True:
            msg = await self.front.get()
            params = msg.get("params") or {}
            if turn_of(params) != self.turn:
                continue
            if msg["method"] == "item/agentMessage/delta":
                piece = params.get("delta", "")
            elif msg["method"] == "item/completed" and (params.get("item") or {}).get("type") == "agentMessage":
                piece = "\n"
                self._log("assistant", [{"type": "text", "text": params["item"].get("text") or ""}])
            elif msg["method"] == "turn/completed":
                self.turn = None
                if buffer.strip() and not self.cancelled:
                    yield buffer.strip()
                return
            else:
                continue
            sentences, buffer = take_sentences(buffer + piece)
            for sentence in [] if self.cancelled else sentences:
                yield sentence

    async def ask(self, text):
        self.cancelled = False
        self.busy = True
        self.asks += 1
        try:
            async with self.lock:
                if not self.connected:
                    await self._connect()
                if self.cancelled:
                    return
                try:
                    async for sentence in self._turn(text):
                        yield sentence
                finally:
                    if self.turn:
                        try:
                            await asyncio.wait_for(self.drain(), 30)
                        except Exception:
                            pass
        finally:
            self.busy = False

    async def drain(self):
        turn = self.turn
        if not turn or not self.connected:
            return
        await self._call("turn/interrupt", {"threadId": self.thread, "turnId": turn})
        while True:
            msg = await self.front.get()
            if msg["method"] == "turn/completed" and turn_of(msg.get("params") or {}) == turn:
                self.turn = None
                return

    async def announcements(self):
        while True:
            yield await self.extras.get()

    async def interrupt(self, only_ask=None):
        if only_ask is not None and only_ask != self.asks:
            return
        self.cancelled = True
        if self.connected and self.turn:
            try:
                await self._call("turn/interrupt", {"threadId": self.thread, "turnId": self.turn})
            except Exception:
                pass

    async def close(self):
        if self.proc and self.proc.returncode is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await self.proc.wait()
        if self.reader:
            self.reader.cancel()
        self.connected = False
