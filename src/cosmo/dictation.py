import re
import threading
import time

import numpy as np
from AppKit import NSPasteboard, NSPasteboardItem, NSPasteboardTypeString
from pynput.keyboard import Controller, Key

from . import config

DICTIONARY = config.HOME / "dictionary.txt"
FILLERS = re.compile(r"(?i)(?<![\w'])(?:u+m+|u+h+|e+r+m+|uhm+|hm+|ah+)(?![\w'])[,.]?\s*")
DEFAULT_DICTIONARY = "# one per line: what Whisper writes => what you want\n"
RESTORE_AFTER = 1.0
LIVE_EVERY = 1.2
LIVE_SECONDS = 7
LIVE_GROWTH = 0.5
MARKS = {"question mark": "?", "exclamation mark": "!", "exclamation point": "!", "period": ".", "comma": ",", "colon": ":"}
BREAKS = {"new paragraph": "\n\n", "new line": "\n"}
ALWAYS = {"comma"}
pasting = threading.Lock()


def load_dictionary():
    if not DICTIONARY.exists():
        DICTIONARY.write_text(DEFAULT_DICTIONARY, encoding="utf-8")
    pairs = []
    for line in DICTIONARY.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        said, sep, written = line.partition("=>")
        if not sep:
            said, sep, written = line.partition("\t")
        if sep and said.strip() and written.strip():
            pairs.append((said.strip(), written.strip()))
    return pairs


def phrase(said):
    return r"(?<![\w'])" + said.replace(" ", r"\s+") + r"(?![\w'])"


def spoken(text):
    commands = "|".join(phrase(said) for said in [*MARKS, *BREAKS, "scratch that"])
    after = rf"(?=[ \t]*(?:$|[,.!?;:\n]|[A-Z]|(?i:{commands})))"
    for said, mark in MARKS.items():
        if said in ALWAYS:
            text = re.sub(rf"[\s,.!?;:]*(?i:{phrase(said)})[,.!?;:]*", mark, text)
        else:
            text = re.sub(rf"(^|[,.!?;:\n])[ \t]*(?i:{phrase(said)}){after}[,.!?;:]*",
                          lambda m: ("\n" if m[1] == "\n" else "") + mark, text)
    for said, mark in BREAKS.items():
        text = re.sub(rf"(^|[,.!?;:\n])[ \t]*(?i:{phrase(said)}){after}[,.!?;:]*[ \t]*",
                      lambda m: (m[1] if m[1] in ".!?:" else "") + mark, text)
    scratch = re.compile(rf"(^|[,.!?;:\n])[ \t]*(?i:{phrase('scratch that')}){after}[,.!?;:]*")
    while match := scratch.search(text):
        head = text[: match.start()].rstrip(" \t\n,.!?;:")
        head = head[: max(head.rfind(c) for c in ".!?\n,;:") + 1]
        text = head + " " + text[match.end() :]
    return text


def clean(text, pairs):
    text = FILLERS.sub("", text)
    for said, written in pairs:
        text = re.sub(rf"(?i)(?<!\w){re.escape(said)}(?!\w)", lambda _: written, text)
    text = spoken(text)
    text = re.sub(r"[ \t]+([,.!?;:])", r"\1", text)
    text = re.sub(r"([,.!?;:])\1+", r"\1", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip(" ,")
    text = re.sub(r"(?:[.!?][ \t]+|\n)[a-z](?![\w']*[A-Z])", lambda m: m[0].upper(), text)
    return text[:1].upper() + text[1:] if text else ""


def live_text(engine, held, words_file, going):
    blocks = list(held)
    if not blocks or not engine.lock.acquire(blocking=False):
        return ""
    try:
        if not going():
            return ""
        audio = np.concatenate(blocks[-int(LIVE_SECONDS * 16000 / len(blocks[0])) :])
        return clean(engine._transcribe(audio, words_file), load_dictionary())
    finally:
        engine.lock.release()


def snapshot(board):
    saved = []
    for item in board.pasteboardItems() or []:
        data = {t: item.dataForType_(t) for t in item.types()}
        saved.append({t: d for t, d in data.items() if d is not None})
    return saved


def restore(board, saved):
    board.clearContents()
    items = []
    for data in saved:
        item = NSPasteboardItem.alloc().init()
        for kind, value in data.items():
            item.setData_forType_(value, kind)
        items.append(item)
    if items:
        board.writeObjects_(items)


def paste(text):
    with pasting:
        board = NSPasteboard.generalPasteboard()
        saved = snapshot(board)
        board.clearContents()
        board.setString_forType_(text, NSPasteboardTypeString)
        mine = board.changeCount()
        keys = Controller()
        with keys.pressed(Key.cmd):
            keys.tap("v")
        time.sleep(RESTORE_AFTER)
        if board.changeCount() == mine:
            restore(board, saved)
