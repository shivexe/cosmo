from pynput import keyboard


def watch(name, loop, on_press, on_release):
    key = getattr(keyboard.Key, name, None) or keyboard.KeyCode.from_char(name)
    held = False

    def down(k):
        nonlocal held
        if k == key and not held:
            held = True
            loop.call_soon_threadsafe(on_press)

    def up(k):
        nonlocal held
        if k == key and held:
            held = False
            loop.call_soon_threadsafe(on_release)

    listener = keyboard.Listener(on_press=down, on_release=up)
    listener.start()
    return listener
