import json
from pathlib import Path

HOME = Path.home() / ".cosmo"
CONFIG = HOME / "config.json"
WORDS = HOME / "words.txt"
PRONUNCIATIONS = HOME / "pronunciations.txt"

DEFAULTS = {
    "voice": "af_heart",
    "speed": 1.0,
    "hotkey": "alt_r",
    "listen_mode": "hotkey",
    "wake_words": ["hey cosmo", "cosmo"],
    "wake_model": str(HOME / "wake" / "hey_cosmo.onnx"),
    "wake_verifier": str(HOME / "wake" / "verifier.pkl"),
    "wake_threshold": 0.5,
    "wake_window_seconds": 8,
    "voice_id_threshold": 0.25,
    "brain_model": None,
    "speech_engine": "whisper-small-lora",
    "adapter_path": "~/cosmo/lab/finetune/runs/small-r16-e3",
    "words_file": str(WORDS),
    "pronunciations_file": str(PRONUNCIATIONS),
    "end_of_speech_ms": 700,
    "hosts": {},
}

DEFAULT_WORDS = """Claude Code
Codex
Strapi
Postgres
Redis
Datadog
Linear
worktree
Whisper
Moonshine
Parakeet
LoRA
MLX
openWakeWord
Kokoro
Sarvam
Hinglish
Cosmo
"""

DEFAULT_PRONUNCIATIONS = "Redis\tɹˈɛdɪs\n"


def load():
    HOME.mkdir(exist_ok=True)
    for path, text in ((CONFIG, json.dumps(DEFAULTS, indent=2) + "\n"), (WORDS, DEFAULT_WORDS), (PRONUNCIATIONS, DEFAULT_PRONUNCIATIONS)):
        if not path.exists():
            path.write_text(text, encoding="utf-8")
    try:
        user = json.loads(CONFIG.read_text(encoding="utf-8"))
        readable = isinstance(user, dict)
    except (OSError, ValueError):
        user, readable = {}, False
    merged = {**DEFAULTS, **(user if readable else {})}
    if readable and merged.keys() != user.keys():
        temp = CONFIG.with_suffix(".tmp")
        temp.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        temp.replace(CONFIG)
    return merged


def path_of(cfg, key):
    return Path(cfg[key]).expanduser()
