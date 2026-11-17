import asyncio
import collections
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import quote

from . import android, commands, events
from .config import HOME

PHONE = HOME / "phone.json"
QR = HOME / "phone-pair.png"
PORT = 8765
POLL = 0.2
SEED_BYTES = 512 * 1024
MAX_BODY = 8192
KEEPALIVE = 15
WRITE_TIMEOUT = 10
QUEUE = 200
SKEW = 60
REBIND = 30
STATES = {"idle", "listening", "thinking", "speaking", "waiting_for_approval"}
FORWARD = STATES | {"said", "heard", "approval", "worker_started", "worker_progress", "worker_done", "interrupted", "error", "app_action", "phone_adb"}
TAILSCALE = ipaddress.ip_network("100.64.0.0/10")


def addresses():
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    lan, tail = [], []
    for name, block in re.findall(r"^(\S+?):(.*?)(?=^\S|\Z)", out, re.M | re.S):
        for raw in re.findall(r"inet (\d+\.\d+\.\d+\.\d+)", block):
            ip = ipaddress.ip_address(raw)
            if ip in TAILSCALE:
                tail.append(raw)
            elif name.startswith("en") and ip.is_private and not ip.is_link_local:
                lan.append(raw)
    return lan + tail


def private_write(path, data):
    temp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    temp.unlink(missing_ok=True)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    temp.replace(path)


def pairing(port=PORT, hosts=None):
    try:
        saved = json.loads(PHONE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    token = saved.get("token") or secrets.token_urlsafe(32)
    hosts = (addresses() if hosts is None else hosts) or saved.get("hosts") or []
    link = f"cosmo://pair?host={quote(','.join(hosts), safe=',')}&port={port}&token={token}"
    HOME.mkdir(exist_ok=True)
    private_write(PHONE, (json.dumps({"token": token, "port": port, "hosts": hosts, "link": link}, indent=2) + "\n").encode())
    return token, link


def sign(token, method, path, stamp, nonce, body):
    message = "\n".join((method, path, stamp, nonce, hashlib.sha256(body).hexdigest()))
    return hmac.new(token.encode(), message.encode(), hashlib.sha256).hexdigest()


def seal(token, *parts):
    return hmac.new(token.encode(), "\n".join(parts).encode(), hashlib.sha256).hexdigest()


def drop_dead_peers(writer):
    sock = writer.get_extra_info("socket")
    if sys.platform != "darwin" or sock is None:
        return
    try:
        sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, "TCP_RXT_CONNDROPTIME", 0x80), WRITE_TIMEOUT * 2)
    except (OSError, AttributeError):
        pass


class Bridge:
    def __init__(self, cosmo, token):
        self.cosmo, self.token = cosmo, token
        self.state = "idle"
        self.lines = collections.deque(maxlen=30)
        self.tasks = {}
        self.question = None
        self.approval_id = None
        self.clients = set()
        self.seen = {}
        self.offset = 0

    def apply(self, event):
        kind, detail = event.get("state"), event.get("detail")
        if kind in STATES:
            self.state = kind
        if kind == "ready":
            self.tasks.clear()
        if kind in ("said", "heard") and detail:
            self.lines.append({"t": event.get("t"), "who": "cosmo" if kind == "said" else "you", "text": detail})
        elif kind == "waiting_for_approval":
            self.question = detail or self.question or "Cosmo needs your approval"
            self.approval_id = self.approval_id or str(event.get("t"))
            event["id"] = self.approval_id
        elif kind == "approval":
            self.question = self.approval_id = None
        elif kind == "worker_started" and isinstance(detail, dict):
            self.tasks[detail.get("id")] = {"id": detail.get("id"), "title": detail.get("title", "")}
        elif kind == "worker_done" and isinstance(detail, dict):
            self.tasks.pop(detail.get("id"), None)

    def snapshot(self):
        return {"state": self.state, "conversation": list(self.lines), "tasks": list(self.tasks.values()), "approval": self.question, "approval_id": self.approval_id}

    def seed(self):
        try:
            with events.EVENTS.open("rb") as f:
                size = f.seek(0, 2)
                f.seek(max(0, size - SEED_BYTES))
                chunk = f.read()
        except FileNotFoundError:
            return 0
        complete = chunk.rfind(b"\n") + 1
        for line in chunk[:complete].decode("utf-8", "ignore").splitlines():
            try:
                self.apply(json.loads(line))
            except ValueError:
                pass
        self.question = self.approval_id = None
        self.state = "idle" if self.state == "waiting_for_approval" else self.state
        return size - len(chunk) + complete

    async def tail(self):
        self.offset = self.seed()
        while True:
            await asyncio.sleep(POLL)
            self.pump()

    def pump(self):
        try:
            size = events.EVENTS.stat().st_size
        except FileNotFoundError:
            self.offset = 0
            return
        if size < self.offset:
            self.offset = 0
        if size == self.offset:
            return
        with events.EVENTS.open("rb") as f:
            f.seek(self.offset)
            chunk = f.read(size - self.offset)
        complete = chunk.rfind(b"\n") + 1
        self.offset += complete
        for line in chunk[:complete].decode("utf-8", "ignore").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("state") not in FORWARD:
                continue
            if event.get("state") == "app_action" and (event.get("detail") or {}).get("device") != "phone":
                continue
            self.apply(event)
            for queue in list(self.clients):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    self.clients.discard(queue)

    def authorized(self, method, path, headers, body):
        stamp, nonce = headers.get("x-cosmo-time", ""), headers.get("x-cosmo-nonce", "")
        given = headers.get("x-cosmo-signature", "")
        if not 16 <= len(nonce) <= 64:
            return False
        if not hmac.compare_digest(given.encode(), sign(self.token, method, path, stamp, nonce, body).encode()):
            return False
        try:
            age = abs(time.time() - int(stamp))
        except ValueError:
            return False
        if age > SKEW:
            return "clock"
        now = time.time()
        self.seen = {n: t for n, t in self.seen.items() if t > now}
        if nonce in self.seen:
            return False
        self.seen[nonce] = now + SKEW * 2
        return True

    def command(self, path, body):
        if path == "/ask":
            text = str(body.get("text", "")).strip()
            if not text:
                return 400, {"error": "text is empty"}
            commands.handle(self.cosmo, {"cmd": "ask", "text": text[:4000]})
        elif path == "/approve":
            answer = body.get("answer")
            if answer not in ("yes", "no"):
                return 400, {"error": "answer must be yes or no"}
            self.pump()
            if self.cosmo.waiting is None or self.cosmo.waiting.done():
                return 409, {"error": "nothing is waiting for approval"}
            if self.approval_id is None or str(body.get("id")) != self.approval_id:
                return 409, {"error": "that approval is no longer waiting"}
            commands.handle(self.cosmo, {"cmd": "approve", "answer": answer})
        elif path == "/adb":
            if not android.remember(str(body.get("endpoint", ""))):
                return 400, {"error": "endpoint must be a private ip:port"}
            threading.Thread(target=android.ensure, daemon=True).start()
        elif path == "/stop":
            commands.handle(self.cosmo, {"cmd": "stop"})
        else:
            return 404, {"error": "not found"}
        return 200, {"ok": True}

    async def handle(self, reader, writer):
        try:
            method, path, headers, body = await asyncio.wait_for(read_request(reader), 10)
            auth = self.authorized(method, path, headers, body)
            if not auth:
                return await respond(writer, 401, {"error": "unauthorized"})
            key = (self.token, headers["x-cosmo-nonce"])
            if auth == "clock":
                return await respond(writer, 401, {"error": "clock", "now": int(time.time())}, key)
            path = path.split("?", 1)[0]
            if method == "GET" and path == "/state":
                return await respond(writer, 200, self.snapshot(), key)
            if method == "GET" and path == "/events":
                return await self.stream(writer, key)
            if method != "POST":
                return await respond(writer, 404, {"error": "not found"}, key)
            try:
                parsed = json.loads(body or b"{}")
            except ValueError:
                return await respond(writer, 400, {"error": "body must be json"}, key)
            await respond(writer, *self.command(path, parsed if isinstance(parsed, dict) else {}), key)
        except (asyncio.TimeoutError, ValueError, ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        except Exception as error:
            events.emit("error", f"phone bridge: {error!r}")
        finally:
            writer.close()

    async def stream(self, writer, key):
        queue = asyncio.Queue(QUEUE)
        self.clients.add(queue)
        drop_dead_peers(writer)
        if len(self.clients) == 1:
            events.emit("phone", "connected")
        seq = 0
        try:
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n")
            writer.write(sse(key, seq, {"state": "snapshot", "t": round(time.time(), 3), "detail": self.snapshot()}))
            await asyncio.wait_for(writer.drain(), WRITE_TIMEOUT)
            while queue in self.clients:
                try:
                    event = await asyncio.wait_for(queue.get(), KEEPALIVE)
                    seq += 1
                    writer.write(sse(key, seq, event))
                except asyncio.TimeoutError:
                    writer.write(b": ping\n\n")
                await asyncio.wait_for(writer.drain(), WRITE_TIMEOUT)
        finally:
            self.clients.discard(queue)
            if not self.clients:
                events.emit("phone", "disconnected")


def sse(key, seq, event):
    raw = json.dumps({**event, "seq": seq}, sort_keys=True, separators=(",", ":"))
    return f'data: {{"sig":"{seal(key[0], "event", key[1], raw)}","event":{raw}}}\n\n'.encode()


async def read_request(reader):
    head = await reader.readuntil(b"\r\n\r\n")
    if len(head) > MAX_BODY:
        raise ValueError("headers too large")
    lines = head.decode("latin-1").split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length") or 0)
    if length > MAX_BODY:
        raise ValueError("body too large")
    body = await reader.readexactly(length) if length else b""
    return method, path, headers, body


async def respond(writer, status, payload, key=None):
    reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 409: "Conflict"}[status]
    data = json.dumps(payload).encode()
    signed = f"X-Cosmo-Signature: {seal(key[0], 'response', key[1], str(status), hashlib.sha256(data).hexdigest())}\r\n" if key else ""
    writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n{signed}Connection: close\r\n\r\n".encode() + data)
    await writer.drain()


async def serve(cosmo, port=PORT):
    if not cosmo.cfg.get("phone_bridge", True):
        return
    token, _ = await asyncio.to_thread(pairing, port)
    bridge = Bridge(cosmo, token)
    tail = asyncio.ensure_future(bridge.tail())
    server, bound, warned = None, None, False
    try:
        while True:
            hosts = await asyncio.to_thread(addresses) or bound or []
            if server is None or hosts != bound:
                if server:
                    server.close()
                    server = None
                try:
                    server = await asyncio.start_server(bridge.handle, ["127.0.0.1", *hosts], port, limit=MAX_BODY * 2)
                    bound, warned = hosts, False
                    await asyncio.to_thread(pairing, port, hosts)
                except OSError as error:
                    if not warned:
                        events.emit("error", f"phone bridge could not listen on {port}: {error}")
                    warned = True
            done, _ = await asyncio.wait([tail], timeout=REBIND)
            if done:
                return tail.result()
    finally:
        tail.cancel()
        if server:
            server.close()


def show():
    _, link = pairing()
    print(link)
    try:
        import Quartz
        from AppKit import NSBitmapImageRep, NSPNGFileType
        qr = Quartz.CIFilter.filterWithName_("CIQRCodeGenerator")
        qr.setValue_forKey_(link.encode(), "inputMessage")
        code = qr.valueForKey_("outputImage").imageByApplyingTransform_(Quartz.CGAffineTransformMakeScale(12, 12))
        side = code.extent().size.width + 96
        code = code.imageByApplyingTransform_(Quartz.CGAffineTransformMakeTranslation(48, 48))
        white = Quartz.CIImage.imageWithColor_(Quartz.CIColor.colorWithRed_green_blue_(1, 1, 1)).imageByCroppingToRect_(((0, 0), (side, side)))
        image = code.imageByCompositingOverImage_(white)
        private_write(QR, bytes(NSBitmapImageRep.alloc().initWithCIImage_(image).representationUsingType_properties_(NSPNGFileType, None)))
        subprocess.run(["open", str(QR)])
    except Exception as error:
        print(f"could not draw the QR code: {error!r}", file=sys.stderr)


if __name__ == "__main__":
    show()
