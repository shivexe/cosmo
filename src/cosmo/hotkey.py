from pynput import keyboard


def watch(name, loop, on_press, on_release, on_cancel=None):
    key = getattr(keyboard.Key, name, None) or keyboard.KeyCode.from_char(name)
    held = spoiled = False

    def down(k):
        nonlocal held, spoiled
        if k == key and not held:
            held, spoiled = True, False
            loop.call_soon_threadsafe(on_press)
        elif held and k != key:
            spoiled = True

    def up(k):
        nonlocal held
        if k == key and held:
            held = False
            loop.call_soon_threadsafe(on_cancel if spoiled and on_cancel else on_release)

    listener = keyboard.Listener(on_press=down, on_release=up)
    listener.start()
    return listener
