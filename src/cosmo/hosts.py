import asyncio
import json
import subprocess
import time

from . import android, config, events

HOSTS_FILE = config.HOME / "hosts.json"
CHECK_EVERY = 60


def configured():
    hosts = config.load().get("hosts") or {}
    return {name: h for name, h in hosts.items() if isinstance(h, dict)}


def online(host):
    try:
        if host.get("kind") == "adb":
            return bool(android.saved_endpoint() or android.devices())
        out = subprocess.run(["ssh", "-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", host["target"], "echo ok"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip().endswith("ok")
    except (OSError, subprocess.SubprocessError, KeyError):
        return False


def instructions():
    lines = []
    for name, host in configured().items():
        label = host.get("label") or name
        if host.get("kind") == "adb":
            serial = f"-s {host['target']} " if host.get("target") else ""
            lines.append(f"{label}: Android, control its apps with the phone tools when you have them; for shell commands `adb {serial}shell <command>`")
        elif host.get("target"):
            lines.append(f"{label}: `ssh -n {host['target']} \"<command>\"`, shell {host.get('shell') or 'bash'}")
    if not lines:
        return ""
    return " USER's other machines, for workers to run commands on when they ask: " + "; ".join(lines) + "."


async def watch():
    last = {}
    while True:
        rows = []
        for name, host in configured().items():
            up = await asyncio.to_thread(online, host)
            label = host.get("label") or name
            if name in last and last[name] != up:
                events.emit("host", f"{label} is {'online' if up else 'offline'}")
            last[name] = up
            rows.append({"name": name, "label": label, "kind": host.get("kind", "ssh"), "online": up, "checked": time.time()})
        temp = HOSTS_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(rows), encoding="utf-8")
        temp.replace(HOSTS_FILE)
        await asyncio.sleep(CHECK_EVERY)
