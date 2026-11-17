import asyncio
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


async def deliver(to, text, sender):
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, ToolResultBlock, ToolUseBlock, UserMessage

    for variable in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        os.environ.pop(variable, None)
    denied = []

    async def check(hook, _, __):
        data = hook.get("tool_input") or {}
        if data.get("to") == to and data.get("message") == text:
            return {}
        denied.append(data)
        reason = f"Not sent. Call SendMessage again with exactly {json.dumps({'to': to, 'message': text})}."
        return {"continue_": len(denied) < 2, "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}

    options = ClaudeAgentOptions(
        tools=["SendMessage"], allowed_tools=["SendMessage"], setting_sources=[], model="haiku", max_turns=3,
        hooks={"PreToolUse": [HookMatcher(matcher="SendMessage", hooks=[check])]},
        cwd=str(Path.home()), extra_args={"name": sender},
        system_prompt="You relay one message. Call SendMessage exactly once with the given to and message, copied character for character, then reply done.",
    )
    sent, results = None, []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(json.dumps({"to": to, "message": text}))
        async for msg in client.receive_response():
            for block in getattr(msg, "content", None) or []:
                if isinstance(msg, AssistantMessage) and isinstance(block, ToolUseBlock) and block.name == "SendMessage":
                    sent = block
                elif isinstance(msg, UserMessage) and isinstance(block, ToolResultBlock) and sent and block.tool_use_id == sent.id:
                    content = block.content if isinstance(block.content, str) else " ".join(c.get("text", "") for c in block.content or [])
                    results.append((block.is_error, content))
    delivered = [content for failed, content in results if not failed]
    if not delivered:
        raise SystemExit(f"not sent: {json.dumps(denied[-1]) if denied else results[-1][1] if results else 'SendMessage was not called'}")
    print(delivered[-1])


def cmd_send(args):
    sender = "cosmo"
    if "--as" in args:
        i = args.index("--as")
        sender = args[i + 1]
        args = args[:i] + args[i + 2 :]
    info = find_one(args[0])
    asyncio.run(deliver(info["name"], " ".join(args[1:]) or sys.stdin.read().strip(), sender))


COMMANDS = {"list": cmd_list, "find": cmd_find, "read": cmd_read, "wait": cmd_wait, "send": cmd_send}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        raise SystemExit("usage: list | find <words> | read <name> [n] | wait <name> [seconds] | send <name> [text, or stdin]")
    COMMANDS[sys.argv[1]](sys.argv[2:])
