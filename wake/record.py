"""Record his wake phrases for the voice verifier: python wake/record.py [count]. Clips go to ~/.cosmo/wake/mine."""

import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

OUT = Path.home() / ".cosmo" / "wake" / "mine"
RATE = 16000
SECONDS = 2.5
PHRASES = {"hey_cosmo": "Hey Cosmo", "cosmo": "Cosmo"}

count = int(sys.argv[1]) if len(sys.argv) > 1 else 25
for key, phrase in PHRASES.items():
    folder = OUT / key
    folder.mkdir(parents=True, exist_ok=True)
    start = len(list(folder.glob("*.wav")))
    print(f"\nSay '{phrase}' once each time you see GO. Vary distance and tone; move around the room.")
    for i in range(count):
        print(f"  {i + 1}/{count} ... ", end="", flush=True)
        time.sleep(1.0)
        print("GO", flush=True)
        audio = sd.rec(int(SECONDS * RATE), samplerate=RATE, channels=1, dtype="float32")
        sd.wait()
        level = float(np.sqrt(np.mean(audio**2)))
        if level < 0.003:
            print("     too quiet, skipped")
            continue
        sf.write(str(folder / f"{start + i:03d}.wav"), audio[:, 0], RATE)
print(f"\nDone. Clips are in {OUT}")
