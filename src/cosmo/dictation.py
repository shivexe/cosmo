import re
import threading
import time

from AppKit import NSPasteboard, NSPasteboardItem, NSPasteboardTypeString
from pynput.keyboard import Controller, Key

from . import config

DICTIONARY = config.HOME / "dictionary.txt"
FILLERS = re.compile(r"(?i)(?<![\w'])(?:u+m+|u+h+|e+r+m+|uhm+|hm+|ah+)(?![\w'])[,.]?\s*")
DEFAULT_DICTIONARY = "# one per line: what Whisper writes => what you want\n"
RESTORE_AFTER = 1.0
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


def clean(text, pairs):
    text = FILLERS.sub("", text)
    for said, written in pairs:
        text = re.sub(rf"(?i)(?<!\w){re.escape(said)}(?!\w)", lambda _: written, text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r"([,.!?;:])\1+", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,")
    return text[:1].upper() + text[1:] if text else ""


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
