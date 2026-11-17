import re
import time

from pynput import keyboard


def parse(name):
    keys = []
    for token in str(name).lower().split("+"):
        token = token.strip()
        if not token:
            continue
        code = re.fullmatch(r"<(\d+)>", token)
        if code:
            keys.append(keyboard.KeyCode.from_vk(int(code.group(1))))
        elif hasattr(keyboard.Key, token):
            keys.append(getattr(keyboard.Key, token))
        else:
            keys.append(keyboard.KeyCode.from_char(token))
    return keys


def same(k, target):
    if isinstance(target, keyboard.Key):
        return k == target
    if target.vk is not None:
        return getattr(k, "vk", None) == target.vk
    return (getattr(k, "char", None) or "").lower() == target.char


MODIFIERS = {getattr(keyboard.Key, n) for n in ("cmd", "cmd_r", "alt", "alt_r", "ctrl", "ctrl_r", "shift", "shift_r")}


def overlaps(a, b):
    x, y = {str(k) for k in parse(a)}, {str(k) for k in parse(b)}
    return x <= y or y <= x


TAP = 0.3
DOUBLE = 0.45


def watch(name, loop, on_press, on_release, on_cancel=None, on_lock=None):
    keys = parse(name)
    held = [False] * len(keys)
    active = spoiled = locked = stopping = False
    pressed_at = last_tap = 0.0

    def member(k):
        return next((i for i, t in enumerate(keys) if same(k, t)), None)

    def down(k):
        nonlocal active, spoiled, locked, stopping, pressed_at
        i = member(k)
        if i is not None and held[i] and isinstance(keys[i], keyboard.Key) and keys[i] in MODIFIERS:
            return up(k)
        if i is None:
            if active and not locked:
                spoiled = True
            return
        held[i] = True
        if not all(held):
            return
        if locked:
            locked, stopping = False, True
            loop.call_soon_threadsafe(on_release)
        elif not active:
            active, spoiled, pressed_at = True, False, time.monotonic()
            loop.call_soon_threadsafe(on_press)

    def up(k):
        nonlocal active, locked, stopping, last_tap
        i = member(k)
        if i is None:
            return
        held[i] = False
        if stopping:
            stopping = active = False
            return
        if not active or locked:
            return
        now = time.monotonic()
        if on_lock and not spoiled and now - pressed_at < TAP:
            if now - last_tap < DOUBLE:
                locked, last_tap = True, 0.0
                loop.call_soon_threadsafe(on_lock)
                return
            last_tap = now
            active = False
            loop.call_soon_threadsafe(on_cancel or on_release)
            return
        active, last_tap = False, 0.0
        loop.call_soon_threadsafe(on_cancel if spoiled and on_cancel else on_release)

    def reset():
        nonlocal active, locked, stopping
        active = locked = stopping = False

    listener = keyboard.Listener(on_press=down, on_release=up)
    listener.reset = reset
    listener.start()
    return listener
