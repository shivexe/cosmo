import json
import time

from .config import HOME

EVENTS = HOME / "events.jsonl"


def emit(state, detail=""):
    HOME.mkdir(exist_ok=True)
    with EVENTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": round(time.time(), 3), "state": state, "detail": detail}) + "\n")
