import base64
import itertools
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import Quartz
from AppKit import NSRunningApplication
from ApplicationServices import (
    AXUIElementCopyActionNames,
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXUIElementPerformAction,
    AXUIElementSetAttributeValue,
    AXValueGetValue,
    kAXValueCGPointType,
    kAXValueCGSizeType,
)

USEFUL = {
    "AXButton", "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXMenuButton", "AXTextField", "AXTextArea",
    "AXSearchField", "AXComboBox", "AXLink", "AXMenuItem", "AXTab", "AXRow", "AXCell", "AXStaticText", "AXSlider",
    "AXDisclosureTriangle", "AXIncrementor", "AXHeading", "AXImage", "AXSwitch", "AXSegment",
}
ACTIONABLE = USEFUL - {"AXStaticText", "AXHeading", "AXImage"}
MAX_DEPTH = 40
MAX_NODES = 6000
BLOCKED = re.compile(r"passwords|keychain|1password|agilebits|bitwarden|dashlane|lastpass|bank|wallet|coinbase|binance|robinhood|"
                     r"zerodha|kite|groww|upstox|metamask|ledger|trading|crypto|paypal|venmo|revolut|chase|hdfc|icici|sbi|kotak|"
                     r"axis|phonepe|paytm|exodus|imobile", re.I)
BROWSERS = re.compile(r"chrome|chromium|helium|brave|edge|arc|vivaldi|opera", re.I)
OFF_LIMITS = "Not done: this app is off limits for Cosmo (password, banking and crypto apps)."

elements = {}
counter = itertools.count(1)
elements_lock = threading.Lock()
on_action = lambda app, action, box: None
generation = 0
stopped_at = 0.0
STOP_GRACE = 5


def stop():
    global generation, stopped_at
    generation += 1
    stopped_at = time.monotonic()


def check_stopped():
    if time.monotonic() - stopped_at < STOP_GRACE:
        raise Refused("Stopped by the user. Do not continue this task.")


class Refused(Exception):
    pass


def attr(el, name):
    err, value = AXUIElementCopyAttributeValue(el, name, None)
    return value if err == 0 else None


def text_of(el):
    for name in ("AXTitle", "AXDescription", "AXValue", "AXPlaceholderValue", "AXHelp"):
        value = attr(el, name)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


def inner_text(el, depth=0):
    if depth > 4:
        return ""
    for child in attr(el, "AXChildren") or []:
        label = text_of(child) or inner_text(child, depth + 1)
        if label:
            return label
    return ""


def frame(el):
    if el is None:
        return None
    pos, size = attr(el, "AXPosition"), attr(el, "AXSize")
    if pos is None or size is None:
        return None
    _, p = AXValueGetValue(pos, kAXValueCGPointType, None)
    _, s = AXValueGetValue(size, kAXValueCGSizeType, None)
    return (p.x, p.y, s.width, s.height)


def gui_apps():
    out = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True).stdout
    apps = []
    for line in out.splitlines():
        pid, _, path = line.strip().partition(" ")
        if ".app/Contents/MacOS/" not in path:
            continue
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
        if app is not None and app.activationPolicy() == 0:
            apps.append(app)
    return apps


def front_pid():
    for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID):
        if w.get("kCGWindowLayer") == 0:
            return w.get("kCGWindowOwnerPID")
    return None


def is_blocked(app):
    return bool(BLOCKED.search(f"{app.localizedName() or ''} {app.bundleIdentifier() or ''}"))


def running():
    out, front = [], front_pid()
    for app in gui_apps():
        if is_blocked(app):
            continue
        root = AXUIElementCreateApplication(app.processIdentifier())
        windows = [attr(w, "AXTitle") or "" for w in attr(root, "AXWindows") or []]
        out.append({"app": app.localizedName(), "frontmost": app.processIdentifier() == front,
                    "windows": [w for w in windows if w][:8], "hidden": bool(app.isHidden())})
    return out


def find_app(name):
    name = name.lower().strip()
    apps = gui_apps()
    for match in (lambda a: (a.localizedName() or "").lower() == name,
                  lambda a: name in (a.localizedName() or "").lower(),
                  lambda a: name in (a.bundleIdentifier() or "").lower()):
        found = [a for a in apps if match(a)]
        names = sorted({a.localizedName() for a in found})
        if len(names) > 1:
            raise Refused(f"'{name}' matches several apps: {', '.join(names)}. Use the full name.")
        if found:
            if is_blocked(found[0]):
                raise Refused(OFF_LIMITS)
            return found[0]
    if BLOCKED.search(name):
        raise Refused(OFF_LIMITS)
    return None


def need_app(name):
    app = find_app(name)
    if app is None:
        raise Refused(f"{name} is not running. Use open first.")
    return app


def open_app(name):
    app = find_app(name)
    if app is None:
        result = subprocess.run(["open", "-a", name], capture_output=True, text=True)
        if result.returncode:
            return f"Could not open {name}: {result.stderr.strip()}"
        time.sleep(1.5)
        try:
            app = find_app(name)
        except Refused:
            for candidate in gui_apps():
                if is_blocked(candidate) and name.lower() in (candidate.localizedName() or "").lower():
                    candidate.terminate()
            raise
        if app is None:
            return f"Opened {name}, but it has not finished starting yet."
    app.activateWithOptions_(0)
    on_action(app.localizedName(), "Opening", None)
    return f"{app.localizedName()} is open and in front."


def root_of(app):
    root = AXUIElementCreateApplication(app.processIdentifier())
    AXUIElementSetAttributeValue(root, "AXManualAccessibility", True)
    if BROWSERS.search(app.bundleIdentifier() or ""):
        AXUIElementSetAttributeValue(root, "AXEnhancedUserInterface", True)
    return root


def window_of(root, title=None):
    windows = attr(root, "AXWindows") or []
    if title:
        for w in windows:
            if title.lower() in (attr(w, "AXTitle") or "").lower():
                return w
    return attr(root, "AXFocusedWindow") or attr(root, "AXMainWindow") or (windows[0] if windows else None)


def look(app_name, find=None, window=None, limit=150):
    app = need_app(app_name)
    win = window_of(root_of(app), window)
    if win is None:
        return f"{app.localizedName()} has no open window."
    name = app.localizedName()
    on_action(name, "Reading the window", frame(win))
    rows, seen, found = [], [0], {}
    needle = (find or "").lower()

    def walk(el, depth):
        if depth > MAX_DEPTH or seen[0] > MAX_NODES:
            return
        seen[0] += 1
        role = attr(el, "AXRole") or ""
        if role in USEFUL:
            label = text_of(el) or (inner_text(el) if role in ("AXRow", "AXCell", "AXButton", "AXLink", "AXMenuButton") else "")
            parent = (attr(attr(el, "AXParent"), "AXRole") or "") if depth > 0 else ""
            nested = (role == "AXStaticText" and parent in ("AXRow", "AXCell", "AXButton", "AXLink")) or (role == "AXCell" and parent == "AXRow")
            if not nested and (label or role in ACTIONABLE) and (not needle or needle in label.lower() or needle in role.lower()):
                eid = f"e{next(counter)}"
                found[eid] = (app.processIdentifier(), el)
                bits = [eid, role[2:], repr(label[:80]) if label else "(no label)"]
                if attr(el, "AXEnabled") is False:
                    bits.append("disabled")
                if role not in ("AXRow", "AXCell") and attr(el, "AXFocused"):
                    bits.append("focused")
                if attr(el, "AXSelected"):
                    bits.append("selected")
                checked = attr(el, "AXValue") if role in ("AXCheckBox", "AXRadioButton", "AXSwitch") else None
                if checked is not None:
                    bits.append("on" if checked else "off")
                rows.append(" ".join(bits))
        for child in attr(el, "AXChildren") or []:
            walk(child, depth + 1)

    walk(win, 0)
    with elements_lock:
        elements.update(found)
        while len(elements) > 20000:
            elements.pop(next(iter(elements)))
    head = f"{name}, window '{attr(win, 'AXTitle') or ''}': {len(rows)} elements" + (f" matching '{find}'" if find else "")
    if not rows:
        return head + ". Nothing readable here; this app may draw its own UI. Use screenshot, then click by position."
    more = f"\n...and {len(rows) - limit} more; pass find to narrow it." if len(rows) > limit else ""
    return head + "\n" + "\n".join(rows[:limit]) + more


def element(app_name, eid):
    app = need_app(app_name)
    with elements_lock:
        pid, el = elements.get(eid, (None, None))
    if el is None or pid != app.processIdentifier():
        raise Refused(f"No element {eid} in {app.localizedName()}. Run look again.")
    return app, el


def label_of(el):
    return text_of(el) or (attr(el, "AXRole") or "")[2:].lower()


def secure(el):
    return el is not None and (attr(el, "AXSubrole") or "") == "AXSecureTextField"


def focused_element(app):
    return attr(AXUIElementCreateApplication(app.processIdentifier()), "AXFocusedUIElement")


def press(app_name, eid):
    app, el = element(app_name, eid)
    name = label_of(el)
    on_action(app.localizedName(), f"Pressing {name}", frame(el))
    actions = list(AXUIElementCopyActionNames(el, None)[1] or [])
    for action in ("AXPress", "AXConfirm", "AXPick", "AXOpen", "AXShowMenu"):
        if action in actions and AXUIElementPerformAction(el, action) == 0:
            return f"Pressed {name} in {app.localizedName()}."
    return f"{name} cannot be pressed (actions: {actions}). Try click with its position."


def set_text(app_name, eid, text):
    app, el = element(app_name, eid)
    if secure(el):
        raise Refused("That is a password field. Cosmo never types passwords; ask the user to type it.")
    name = label_of(el)
    on_action(app.localizedName(), f"Typing into {name}", frame(el))
    AXUIElementSetAttributeValue(el, "AXFocused", True)
    if AXUIElementSetAttributeValue(el, "AXValue", text) == 0:
        return f"Set {name} in {app.localizedName()}. Look again to check it."
    if attr(el, "AXFocused") is not True:
        raise Refused(f"Could not focus {name}. Press it first, then use type.")
    key(app_name, "cmd+a")
    return type_text(app_name, text)


KEYCODES = {
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51, "forwarddelete": 117, "escape": 53,
    "esc": 53, "left": 123, "right": 124, "down": 125, "up": 126, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    **dict(zip("asdfhgzxcvbqweryt123465=97-80]ou[ip", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
                                                       21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35])),
    "l": 37, "j": 38, "k": 40, "n": 45, "m": 46, ",": 43, ".": 47, "/": 44, ";": 41, "'": 39, "\\": 42, "`": 50,
    "minus": 27, "plus": 24, "equals": 24,
    **{f"f{i}": k for i, k in enumerate([122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111], 1)},
}
FLAGS = {"cmd": Quartz.kCGEventFlagMaskCommand, "command": Quartz.kCGEventFlagMaskCommand, "shift": Quartz.kCGEventFlagMaskShift,
         "alt": Quartz.kCGEventFlagMaskAlternate, "option": Quartz.kCGEventFlagMaskAlternate, "opt": Quartz.kCGEventFlagMaskAlternate,
         "ctrl": Quartz.kCGEventFlagMaskControl, "control": Quartz.kCGEventFlagMaskControl}


def post(pid, event):
    Quartz.CGEventPostToPid(pid, event)
    time.sleep(0.004)


def type_text(app_name, text):
    app = need_app(app_name)
    target = focused_element(app)
    if secure(target):
        raise Refused("The focused field is a password field. Cosmo never types passwords.")
    on_action(app.localizedName(), "Typing", frame(target))
    start = generation
    for ch in text:
        if generation != start:
            raise Refused("Stopped by the user.")
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
            Quartz.CGEventKeyboardSetUnicodeString(event, len(ch.encode("utf-16-le")) // 2, ch)
            post(app.processIdentifier(), event)
    return f"Typed {len(text)} characters into {app.localizedName()}."


def key(app_name, combo):
    app = need_app(app_name)
    parts = [p.strip().lower() for p in combo.split("+")]
    if len(parts) > 1 and parts[-1] == "" and combo.rstrip().endswith("+"):
        parts = parts[:-2] + ["plus"] if parts[-2] == "" else parts[:-1] + ["plus"]
    parts = [p for p in parts if p]
    if not parts:
        return f"Unknown key {combo}."
    unknown = [p for p in parts[:-1] if p not in FLAGS]
    if unknown:
        return f"Unknown modifier {unknown[0]}. Use cmd, shift, alt or ctrl."
    flags = 0
    for p in parts[:-1]:
        flags |= FLAGS[p]
    code = KEYCODES.get("minus" if parts[-1] == "-" else parts[-1])
    if code is None:
        return f"Unknown key {parts[-1]}."
    on_action(app.localizedName(), f"Pressing {combo}", None)
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, code, down)
        Quartz.CGEventSetFlags(event, flags)
        post(app.processIdentifier(), event)
    return f"Pressed {combo} in {app.localizedName()}."


def menu(app_name, path):
    app = need_app(app_name)
    node = attr(root_of(app), "AXMenuBar")
    names = [p.strip() for p in path.split(">") if p.strip()]
    for i, name in enumerate(names):
        children = attr(node, "AXChildren") or []
        if i > 0 and children and attr(children[0], "AXRole") == "AXMenu":
            children = attr(children[0], "AXChildren") or []
        match = next((c for c in children if (attr(c, "AXTitle") or "").lower() == name.lower()), None) or \
            next((c for c in children if name.lower() in (attr(c, "AXTitle") or "").lower()), None)
        if match is None:
            options = [attr(c, "AXTitle") for c in children if attr(c, "AXTitle")]
            return f"No menu item '{name}'. Options here: {options[:30]}"
        node = match
    on_action(app.localizedName(), f"Choosing {' > '.join(names)}", None)
    if AXUIElementPerformAction(node, "AXPress") == 0:
        return f"Chose {' > '.join(names)} in {app.localizedName()}."
    return f"Could not choose {' > '.join(names)}."


def window_id(app):
    for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID):
        if w.get("kCGWindowOwnerPID") == app.processIdentifier() and w.get("kCGWindowLayer") == 0 and w["kCGWindowBounds"]["Height"] > 100:
            return w["kCGWindowNumber"], w["kCGWindowBounds"]
    return None, None


def screenshot(app_name):
    app = need_app(app_name)
    wid, bounds = window_id(app)
    if wid is None:
        return None, f"{app.localizedName()} has no visible window to capture."
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "shot.png"
        subprocess.run(["screencapture", "-x", "-o", "-t", "png", "-l", str(wid), str(path)], capture_output=True)
        if not path.exists() or path.stat().st_size == 0:
            return None, "Screen capture failed. Cosmo needs Screen Recording permission."
        subprocess.run(["sips", "-Z", "1400", str(path)], capture_output=True)
        info = subprocess.run(["sips", "-g", "pixelWidth", str(path)], capture_output=True, text=True).stdout
        data = base64.b64encode(path.read_bytes()).decode()
    match = re.search(r"pixelWidth: (\d+)", info)
    scale = bounds["Width"] / int(match[1]) if match else 1
    on_action(app.localizedName(), "Looking at the window", (bounds["X"], bounds["Y"], bounds["Width"], bounds["Height"]))
    note = (f"{app.localizedName()} window at x={bounds['X']:.0f} y={bounds['Y']:.0f}, {bounds['Width']:.0f}x{bounds['Height']:.0f} points. "
            f"Image pixel (px, py) is screen point ({bounds['X']:.0f} + px*{scale:.3f}, {bounds['Y']:.0f} + py*{scale:.3f}).")
    return data, note


def owner_at(x, y):
    for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID):
        b = w["kCGWindowBounds"]
        if w.get("kCGWindowAlpha", 1) > 0 and b["X"] <= x < b["X"] + b["Width"] and b["Y"] <= y < b["Y"] + b["Height"]:
            return w.get("kCGWindowOwnerPID")
    return None


def click(app_name, x, y):
    app = need_app(app_name)
    app.activateWithOptions_(0)
    time.sleep(0.3)
    if front_pid() != app.processIdentifier():
        return f"Could not bring {app.localizedName()} to the front, so I did not click."
    on_action(app.localizedName(), "Clicking", (x - 12, y - 12, 24, 24))
    time.sleep(0.35)
    if owner_at(x, y) != app.processIdentifier():
        return f"That point is not on {app.localizedName()}'s window, or something covers it, so I did not click."
    old = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, kind, (x, y), Quartz.kCGMouseButtonLeft))
        time.sleep(0.03)
    Quartz.CGWarpMouseCursorPosition(old)
    return f"Clicked at {x:.0f},{y:.0f} in {app.localizedName()}."
