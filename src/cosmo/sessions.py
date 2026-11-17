import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SESSIONS = Path.home() / ".claude" / "sessions"
PROJECTS = Path.home() / ".claude" / "projects"
TAIL_BYTES = 2_000_000
SHORT = 160


STOP = {"what", "whats", "status", "this", "that", "with", "from", "have", "about", "task", "session", "working", "work", "the", "and", "for"}




def running(info):
    if not isinstance(info.get("pid"), int) or info["pid"] <= 0:
        return False
    try:
        os.kill(info["pid"], 0)
    except OSError:
        return False
    return True


def start_times(pids):
    if not pids:
        return {}
    out = subprocess.run(["ps", "-o", "pid=,lstart=", "-p", ",".join(map(str, pids))], capture_output=True, text=True, env={**os.environ, "TZ": "UTC"}).stdout
    return {int(line.split()[0]): " ".join(line.split()[1:]) for line in out.splitlines() if line.strip()}


def transcript(info):
    folder = re.sub(r"[^A-Za-z0-9]", "-", info.get("cwd", ""))
    return PROJECTS / folder / f"{info.get('sessionId')}.jsonl"


def live():
    out = []
    for path in SESSIONS.glob("*.json"):
        try:
            info = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if info.get("kind") == "interactive" and running(info):
            out.append(info)
    started = start_times([info["pid"] for info in out])
    return [info for info in out if started.get(info["pid"]) == " ".join(str(info.get("procStart", "")).split())]


def entries(info):
    path = transcript(info)
    if not path.exists():
        return []
    with path.open("rb") as f:
        f.seek(max(0, path.stat().st_size - TAIL_BYTES))
        lines = f.read().decode("utf-8", "ignore").splitlines()[1:]
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def texts(rows, role):
    out = []
    for row in rows:
        if row.get("type") != role or row.get("isMeta"):
            continue
        content = (row.get("message") or {}).get("content")
        if isinstance(content, str):
            text = content
        else:
            text = " ".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
        text = " ".join(text.split())
        if text and not text.startswith("<"):
            out.append(text)
    return out


def first_prompt(info):
    path = transcript(info)
    if not path.exists():
        return ""
    with path.open(encoding="utf-8", errors="ignore") as f:
        for _, line in zip(range(200), f):
            try:
                found = texts([json.loads(line)], "user")
            except ValueError:
                continue
            if found:
                return found[0]
    return ""


def describe(info, score=None):
    rows = entries(info)
    said = texts(rows, "assistant")
    asked = texts(rows, "user")
    line = f"{info['name']}  ({info.get('status', '?')}, folder {info.get('cwd', '?')})"
    if score is not None:
        line += f"  matches {score}"
    line += f"\n  started with: {first_prompt(info)[:SHORT]}"
    if asked:
        line += f"\n  last asked: {asked[-1][:SHORT]}"
    if said:
        line += f"\n  last said: {said[-1][:SHORT]}"
    return line


def find_one(name):
    for info in live():
        if info.get("name") == name or str(info.get("pid")) == name:
            return info
    raise SystemExit(f"no live session named {name}; run: list")


def cmd_list(_):
    for info in sorted(live(), key=lambda i: -i.get("updatedAt", 0)):
        print(describe(info))


def cmd_find(args):
    terms = [t for t in re.findall(r"[a-z0-9#-]+", " ".join(args).lower()) if len(t) > 2 and t not in STOP]
    scored = []
    for info in live():
        rows = entries(info)
        blob = " ".join(texts(rows, "user") + texts(rows, "assistant") + [first_prompt(info)]).lower()
        score = sum(blob.count(t) for t in terms)
        if score:
            scored.append((score, info))
    if not scored:
        print("no live session mentions that; run: list")
    for score, info in sorted(scored, key=lambda s: -s[0])[:3]:
        print(describe(info, score))


def cmd_read(args):
    info = find_one(args[0])
    said = texts(entries(info), "assistant")
    print(f"{info['name']} is {info.get('status', '?')}.")
    for text in said[-int(args[1]) if len(args) > 1 else -3 :]:
        print(f"- {text[:1500]}")


def cmd_wait(args):
    after = None
    if "--after" in args:
        i = args.index("--after")
        after = float(args[i + 1]) * 1000
        args = args[:i] + args[i + 2 :]
    info = find_one(args[0])
    deadline = time.time() + float(args[1] if len(args) > 1 else 100)
    path = SESSIONS / f"{info['pid']}.json"
    since = after if after is not None else info.get("statusUpdatedAt", 0)
    while time.time() < deadline:
        try:
            now = json.loads(path.read_text())
        except (OSError, ValueError):
            now = {}
        if now.get("status") == "idle" and now.get("statusUpdatedAt", 0) > since:
            return cmd_read([args[0], "1"])
        time.sleep(3)
    print(f"{info['name']} is still working; run wait again")


COMMANDS = {"list": cmd_list, "find": cmd_find, "read": cmd_read, "wait": cmd_wait}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        raise SystemExit("usage: list | find <words> | read <name> [n] | wait <name> [seconds]")
    COMMANDS[sys.argv[1]](sys.argv[2:])
