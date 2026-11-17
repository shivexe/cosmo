import base64
import ipaddress
import itertools
import json
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from . import config, events
from .mac import BLOCKED, OFF_LIMITS, Refused, check_stopped

ENDPOINT = config.HOME / "phone-adb.json"
APP = "dev.notsg.cosmo"
SHOT_WIDTH = 540
WAKE_SECONDS = 25
WAKE_RETRY = 60
KEYS = {"back": 4, "home": 3, "enter": 66, "return": 66, "recents": 187, "delete": 67, "backspace": 67, "tab": 61,
        "search": 84, "up": 19, "down": 20, "left": 21, "right": 22, "space": 62, "escape": 111, "menu": 82, "paste": 279}
BLOCKED_PHONE = re.compile(BLOCKED.pattern + r"|payzapp|paisa|upiapp|bhim|dreamplug|mobikwik|spay|onepassword|wallet|authenticator|\.pass\b|keepass|enpass|samsungpass", re.I)

elements = {}
last_shot_width = 0
counter = itertools.count(1)
lock = threading.Lock()
on_action = lambda app, action: None


def adb_path():
    return shutil.which("adb") or next((p for p in ("/opt/homebrew/bin/adb", "/usr/local/bin/adb",
                                                    str(Path.home() / "Library/Android/sdk/platform-tools/adb"))
                                        if Path(p).exists()), None)


def adb(*args, serial=None, timeout=20, binary=False):
    path = adb_path()
    if not path:
        raise Refused("adb is not installed on this Mac.")
    cmd = [path, *(["-s", serial] if serial else []), *args]
    out = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).decode("utf-8", "replace").strip()[-300:] or "adb failed")
    return out.stdout if binary else out.stdout.decode("utf-8", "replace")


def devices():
    try:
        return [line.split()[0] for line in adb("devices").splitlines()[1:] if line.split()[-1:] == ["device"]]
    except (Refused, RuntimeError, OSError, subprocess.SubprocessError):
        return []


def saved_endpoint():
    try:
        return json.loads(ENDPOINT.read_text(encoding="utf-8")).get("endpoint", "")
    except (OSError, ValueError):
        return ""


def valid_endpoint(endpoint):
    host, _, port = endpoint.rpartition(":")
    try:
        ip = ipaddress.ip_address(host)
        return ip.version == 4 and ip.is_private and not (ip.is_loopback or ip.is_unspecified or ip.is_link_local) and 0 < int(port) < 65536
    except ValueError:
        return False


def remember(endpoint):
    if not valid_endpoint(endpoint):
        return False
    if endpoint != saved_endpoint():
        temp = ENDPOINT.with_suffix(".tmp")
        temp.write_text(json.dumps({"endpoint": endpoint}), encoding="utf-8")
        temp.replace(ENDPOINT)
    return True


failed_at = 0.0


def ensure():
    global failed_at
    endpoint = saved_endpoint()
    listed = devices()
    if not endpoint or endpoint in listed or time.monotonic() - failed_at < 30:
        return bool(listed)
    try:
        adb("connect", endpoint, timeout=10)
    except (Refused, RuntimeError, OSError, subprocess.SubprocessError):
        pass
    if endpoint not in devices():
        failed_at = time.monotonic()
    return bool(devices())


def target():
    hosts = config.load().get("hosts") or {}
    return next((h.get("target") for h in hosts.values() if isinstance(h, dict) and h.get("kind") == "adb" and h.get("target")), "")


wake_failed_at = 0.0


def pick(listed):
    return next((c for c in (target(), saved_endpoint()) if c and c in listed), None)


def wake():
    global failed_at, wake_failed_at
    if time.monotonic() - wake_failed_at < WAKE_RETRY:
        return False
    events.emit("phone_adb", "on")
    deadline = time.monotonic() + WAKE_SECONDS
    while time.monotonic() < deadline:
        check_stopped()
        time.sleep(2)
        failed_at = 0.0
        ensure()
        if pick(devices()) or not (target() or saved_endpoint()) and devices():
            return True
    wake_failed_at = time.monotonic()
    return False


def serial():
    ensure()
    listed = devices()
    found = pick(listed)
    if not found and (target() or saved_endpoint() or not listed) and wake():
        listed = devices()
        found = pick(listed)
    if found:
        return found
    wanted = target() or saved_endpoint()
    if not listed or wanted:
        raise Refused("The phone did not come online. It must be on Wi-Fi with the Cosmo app linked to this Mac; "
                      "on a new Wi-Fi, allow wireless debugging on the phone once.")
    if len(listed) > 1:
        raise Refused(f"More than one Android device is connected ({', '.join(listed)}). Set the phone's adb serial in Settings, Devices.")
    return listed[0]


def shell(command, timeout=20, binary=False):
    return adb("shell", command, serial=serial(), timeout=timeout, binary=binary)


def front():
    out = shell("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'")
    match = re.search(r"([a-zA-Z0-9_.]+)/[a-zA-Z0-9_.$]+", out)
    return match.group(1) if match else ""


def guard(package):
    if package and BLOCKED_PHONE.search(package):
        raise Refused(OFF_LIMITS)


def launchable():
    out = shell("cmd package query-activities --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER")
    return sorted({line.strip().split("/")[0] for line in out.splitlines() if "/" in line})


def apps():
    current = front()
    rows = [p for p in launchable() if not BLOCKED_PHONE.search(p)]
    return f"In front: {current or 'unknown'}\nApps: " + ", ".join(rows)


def find_package(name):
    name = name.strip()
    packages = launchable()
    if name in packages:
        return name
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    hits = [p for p in packages if key and key in re.sub(r"[^a-z0-9.]", "", p.lower()).replace(".", "")]
    if not hits:
        raise Refused(f"No app on the phone matches {name!r}. Use apps to see the package names.")
    return min(hits, key=len)


def open_app(name):
    check_stopped()
    package = find_package(name)
    guard(package)
    on_action(package, "Opening")
    shell(f"monkey -p {shlex.quote(package)} -c android.intent.category.LAUNCHER 1")
    return f"Opened {package} on the phone."


def bounds(text):
    a, b, c, d = map(int, re.findall(r"-?\d+", text)[:4])
    return a, b, c, d


def dump():
    for _ in range(2):
        out = shell("f=/data/local/tmp/cosmo-ui.xml; rm -f $f; uiautomator dump $f >/dev/null 2>&1; cat $f 2>/dev/null; rm -f $f", timeout=25)
        start, end = out.find("<?xml"), out.rfind("</hierarchy>")
        if start >= 0 and end > start:
            return ET.fromstring(out[start:end + len("</hierarchy>")])
    raise RuntimeError("Could not read the phone screen. Try again, or use screenshot.")


def look(find=None):
    check_stopped()
    root = dump()
    package = next((n.get("package") for n in root.iter("node") if n.get("package")), "")
    guard(package)
    on_action(package, "Reading the screen")
    rows, found = [], {}
    for node in root.iter("node"):
        label = (node.get("text") or node.get("content-desc") or "").strip().replace("\n", " ")
        rid = (node.get("resource-id") or "").rsplit("/", 1)[-1]
        kind = (node.get("class") or "").rsplit(".", 1)[-1]
        clickable = node.get("clickable") == "true" or node.get("long-clickable") == "true"
        editable = "EditText" in kind
        if not (label or clickable or editable or node.get("scrollable") == "true"):
            continue
        if find and find.lower() not in f"{label} {rid}".lower():
            continue
        x1, y1, x2, y2 = bounds(node.get("bounds", "[0,0][0,0]"))
        if x2 <= x1 or y2 <= y1:
            continue
        eid = f"p{next(counter)}"
        found[eid] = ((x1 + x2) // 2, (y1 + y2) // 2, node.get("password") == "true", package)
        flags = [f for f, on in (("button", clickable), ("field", editable), ("scrolls", node.get("scrollable") == "true"),
                                 ("checked", node.get("checked") == "true"), ("focused", node.get("focused") == "true"),
                                 ("off", node.get("enabled") == "false")) if on]
        rows.append(f"{eid} {kind}{' ' + repr(label[:80]) if label else ''}{' #' + rid if rid else ''}"
                    f"{' (' + ', '.join(flags) + ')' if flags else ''}")
    with lock:
        if len(elements) > 4000:
            elements.clear()
        elements.update(found)
    head = f"Phone app in front: {package or 'unknown'}"
    return head + "\n" + ("\n".join(rows[:300]) if rows else "Nothing readable here. Use screenshot.")


def element(eid):
    with lock:
        if eid not in elements:
            raise Refused(f"No element {eid}. Run look again.")
        return elements[eid]


def tap(eid):
    check_stopped()
    x, y, _, package = element(eid)
    guard(front())
    on_action(package, f"Tapping {eid}")
    shell(f"input tap {x} {y}")
    return f"Tapped {eid}."


def tap_point(x, y):
    global last_shot_width
    check_stopped()
    guard(front())
    if not last_shot_width:
        raise Refused("Take a screenshot first.")
    scale = last_shot_width / SHOT_WIDTH
    last_shot_width = 0
    on_action(front(), "Tapping")
    shell(f"input tap {int(x * scale)} {int(y * scale)}")
    return "Tapped."


def type_text(text, eid=None):
    check_stopped()
    if eid:
        x, y, password, _ = element(eid)
        if password:
            raise Refused("Not done: that is a password field.")
        shell(f"input tap {x} {y}")
        time.sleep(0.5)
    package = front()
    guard(package)
    on_action(package, "Typing")
    if text.isascii() and "\n" not in text and "%" not in text:
        shell("input text " + shlex.quote(text.replace(" ", "%s")))
    else:
        encoded = base64.b64encode(text.encode()).decode()
        result = shell(f"am broadcast -f 0x20 -n {APP}/.TypeReceiver --es b64 {encoded}")
        if "result=1" not in result:
            raise RuntimeError("Could not hand the text to the phone. Open the Cosmo app on the phone once, then try again.")
        shell(f"input keyevent {KEYS['paste']}")
    return "Typed."


def key(name):
    check_stopped()
    code = KEYS.get(name.strip().lower())
    if code is None:
        raise Refused(f"Unknown key {name!r}. Use one of: {', '.join(KEYS)}.")
    package = front()
    guard(package)
    on_action(package, f"Pressing {name}")
    shell(f"input keyevent {code}")
    return f"Pressed {name}."


def screen_size():
    match = re.search(r"cur=(\d+)x(\d+)", shell("dumpsys window displays | grep -m1 -o 'cur=[0-9]*x[0-9]*'"))
    return (int(match.group(1)), int(match.group(2))) if match else (1080, 2400)


def swipe(direction):
    check_stopped()
    guard(front())
    width, height = screen_size()
    mid, top, bottom = width // 2, height // 4, height * 3 // 4
    moves = {"up": (mid, bottom, mid, top), "down": (mid, top, mid, bottom),
             "left": (width * 4 // 5, height // 2, width // 5, height // 2), "right": (width // 5, height // 2, width * 4 // 5, height // 2)}
    if direction not in moves:
        raise Refused("direction must be up, down, left or right")
    on_action(front(), f"Scrolling {direction}")
    shell("input swipe {} {} {} {} 300".format(*moves[direction]))
    return f"Swiped {direction} (finger moves {direction}, so swipe up shows content further down)."


def screenshot():
    check_stopped()
    package = front()
    guard(package)
    on_action(package, "Looking at the screen")
    global last_shot_width
    png = adb("exec-out", "screencap -p", serial=serial(), timeout=20, binary=True)
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("The phone did not return a screenshot.")
    last_shot_width = int.from_bytes(png[16:20], "big")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "phone.png"
        path.write_bytes(png)
        subprocess.run(["sips", "--resampleWidth", str(SHOT_WIDTH), str(path)], capture_output=True, timeout=20, check=True)
        data = base64.b64encode(path.read_bytes()).decode()
    return data, f"Phone screen, {SHOT_WIDTH} px wide. tap_point takes x and y in this image's pixels."
