"""Train the Cosmo wake word on this Mac. Data and models go to ~/.cosmo/wake, never into git.

Steps: speak, features, train, eval. Each step saves to disk, so a rerun skips finished work.
"""

import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from scipy.signal import resample_poly

OUT = Path.home() / ".cosmo" / "wake"
LAB = Path.home() / "cosmo" / "lab"
WISPR = LAB / "data" / "wispr"
BASELINE = LAB / "baseline" / "me-baseline.wav"
RATE = 16000
CLIP = 2 * RATE
FRAMES = 16

VOICES = [
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole", "af_nova", "af_river",
    "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx",
    "am_puck", "am_santa", "bf_alice", "bf_emma", "bf_isabella", "bf_lily", "bm_daniel", "bm_fable", "bm_george",
    "bm_lewis",
]
HELD_OUT = {"af_nova", "am_echo", "bf_lily", "bm_fable"}
SPEEDS = [0.8, 0.9, 1.0, 1.1, 1.25]
HEY_COSMO = ["Hey Cosmo.", "Hey, Cosmo!", "Hey Cosmo?"]
COSMO = ["Cosmo.", "Cosmo!", "Cosmo?"]
LOOKALIKES = [
    "Cosmic.", "Cosmos.", "Cosmetic.", "Cosmopolitan.", "Cosmonaut.", "Costco.", "Kosovo.", "Hey Cosmic.",
    "Hey, cosmos.", "Commodore.", "Cosmology.", "Hey Jarvis.", "Hey Siri.", "Gizmo.", "Cosby.", "Cozy mode.",
    "Castle.", "Hey Cosima.", "Kismet.", "Hey Como.", "Cuz mo.", "Coastal.", "Hey Google.", "Okay so.",
]
GENERAL = [
    "Can you check the build logs for the payment service?", "I think the meeting moved to Thursday afternoon.",
    "Open the project folder and run the tests again.", "The cost of the new plan is too high for us.",
    "We should ship the fix before the weekend.", "Most of the users came from the campaign last month.",
    "Please summarize the last three pull requests.", "The cosmetics store downtown closed last year.",
    "Turn off the lights in the kitchen.", "What time does the train leave tomorrow?",
    "The comma in that sentence is in the wrong place.", "I'm going to grab some coffee, back in five.",
    "Let's compare the two models on the test set.", "My cousin moved to Boston in the spring.",
    "Send a message to the team about the outage.", "How many tickets are still open in Linear?",
    "The database migration finished without errors.", "Could you play some music while I work?",
    "We need more memory on the staging server.", "Remind me to call the bank at four.",
]
EVAL_GENERAL = [
    "The weather today is cloudy with a chance of rain later in the evening.",
    "He said the costume party starts at eight, so we should leave by seven thirty.",
    "Our company is hosting a small cosmology talk at the community center next week.",
    "Please make sure the commit message explains why the change was needed.",
    "She bought a cosmetic kit and a comb from the shop around the corner.",
    "If the deployment fails again, roll back to the last stable version and tell me.",
    "The football match went into extra time and ended in a penalty shootout.",
    "I would like a large coffee with oat milk and no sugar, please.",
    "Most people prefer to work from home on Fridays during the summer.",
    "The documentary about the cosmos was narrated by a famous scientist.",
]


def mentions_cosmo(text):
    return "cosm" in text.lower()


def save(path, audio):
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), (np.clip(audio, -1, 1) * 32767).astype(np.int16), RATE)


def load(path):
    audio, rate = sf.read(str(path), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(1)
    return audio if rate == RATE else resample_poly(audio, RATE, rate).astype(np.float32)


def speak():
    from kokoro import KPipeline

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    pipes = {code: KPipeline(lang_code=code, device=device, repo_id="hexgrad/Kokoro-82M") for code in "ab"}
    jobs = []
    for voice in VOICES:
        for i, text in enumerate(HEY_COSMO):
            jobs += [("hey_cosmo", voice, s, i, text) for s in SPEEDS]
        for i, text in enumerate(COSMO):
            jobs += [("cosmo", voice, s, i, text) for s in SPEEDS]
        for i, text in enumerate(LOOKALIKES):
            jobs += [("lookalike", voice, s, i, text) for s in (0.9, 1.0, 1.15)]
        for i, text in enumerate(GENERAL):
            jobs.append(("general", voice, 1.0, i, text))
        if voice in HELD_OUT:
            jobs += [("eval_general", voice, 1.0, i, text) for i, text in enumerate(EVAL_GENERAL)]
    start, made = time.time(), 0
    for kind, voice, speed, i, text in jobs:
        path = OUT / "tts" / kind / f"{voice}_{speed}_{i}.wav"
        if path.exists():
            continue
        parts = [r.audio.numpy() for r in pipes[voice[0]](text, voice=voice, speed=speed) if r.audio is not None]
        save(path, resample_poly(np.concatenate(parts), 2, 3))
        made += 1
        if made % 200 == 0:
            print(f"  {made} clips, {time.time() - start:.0f}s", flush=True)
    print(f"speak: {made} new clips of {len(jobs)} in {time.time() - start:.0f}s")


def wispr_clips():
    rows = {}
    for split in ("train", "val", "test"):
        for line in (WISPR / f"{split}.jsonl").read_text().splitlines():
            row = json.loads(line)
            if not mentions_cosmo(row["text"]):
                rows[row["audio"]] = split
    return rows


def noise_bank():
    bank = []
    for path, split in list(wispr_clips().items()):
        if split == "train":
            bank.append(load(Path(path)))
    return bank


def place(audio, rng, bank):
    out = np.zeros(CLIP, dtype=np.float32)
    end = CLIP - int(rng.integers(0, int(0.25 * RATE)))
    audio = audio[-end:] * rng.uniform(0.3, 1.0)
    out[end - len(audio) : end] = audio
    if rng.random() < 0.7:
        noise = bank[rng.integers(len(bank))]
        if len(noise) > CLIP:
            s = rng.integers(len(noise) - CLIP)
            out += noise[s : s + CLIP] * rng.uniform(0.05, 0.3)
    else:
        out += rng.normal(0, rng.uniform(0.001, 0.01), CLIP).astype(np.float32)
    return out


def augment(audio, rng):
    from scipy.signal import butter, fftconvolve, sosfilt

    if rng.random() < 0.5:
        factor = rng.uniform(0.92, 1.08)
        audio = resample_poly(audio, 100, int(100 * factor)).astype(np.float32)
    if rng.random() < 0.6:
        length = int(rng.uniform(0.05, 0.4) * RATE)
        rir = rng.normal(0, 1, length) * np.exp(-np.linspace(0, rng.uniform(4, 9), length))
        rir[0] = 1.0
        wet = fftconvolve(audio, rir)[: len(audio)]
        audio = (1 - (mix := rng.uniform(0.2, 0.6))) * audio + mix * wet / (np.abs(wet).max() + 1e-6) * np.abs(audio).max()
    if rng.random() < 0.5:
        low, high = rng.uniform(80, 300), rng.uniform(3000, 7500)
        audio = sosfilt(butter(2, [low, high], btype="band", fs=RATE, output="sos"), audio)
    return audio.astype(np.float32)


def his_cosmos(split):
    times = json.loads((OUT / "baseline_cosmo_times.json").read_text())
    picked = times[: len(times) // 2 + 1] if split == "train" else times[len(times) // 2 + 1 :]
    audio = load(BASELINE)
    return [audio[max(0, int((a - 0.6) * RATE)) : int((b + 0.1) * RATE)] for a, b, _ in picked]


def to_int16(x):
    return (np.clip(x, -1, 1) * 32767).astype(np.int16)


def features():
    from openwakeword.utils import AudioFeatures

    fe = AudioFeatures(inference_framework="onnx")
    rng = np.random.default_rng(0)
    bank = noise_bank()
    sets = {}
    for kind, copies in (("hey_cosmo", 4), ("cosmo", 4), ("lookalike", 2)):
        for split in ("train", "eval"):
            files = [p for p in sorted((OUT / "tts" / kind).glob("*.wav")) if (p.name.split("_")[0] + "_" + p.name.split("_")[1] in HELD_OUT) == (split == "eval")]
            n = copies if split == "train" else 1
            clips = np.stack([to_int16(place(augment(load(p), rng) if split == "train" else load(p), rng, bank)) for p in files for _ in range(n)])
            sets[f"{kind}_{split}"] = fe.embed_clips(clips, batch_size=64)[:, -FRAMES:, :]
            print(f"  {kind}_{split}: {sets[f'{kind}_{split}'].shape}", flush=True)
    mine = his_cosmos("train")
    sets["his_train"] = fe.embed_clips(np.stack([to_int16(place(augment(a, rng), rng, bank)) for a in mine for _ in range(40)]), batch_size=64)[:, -FRAMES:, :]
    for name, long_audio in (("tts_speech", [augment(load(p), rng) for p in sorted((OUT / "tts" / "general").glob("*.wav"))]), ("real_speech", bank)):
        windows = []
        for audio in long_audio:
            if len(audio) < 1.5 * RATE:
                continue
            emb = fe._get_embeddings(to_int16(audio))
            windows += [emb[i : i + FRAMES] for i in range(0, len(emb) - FRAMES + 1, 2)]
        sets[name] = np.stack(windows)
    for name in ("his_train", "tts_speech", "real_speech"):
        print(f"  {name}: {sets[name].shape}", flush=True)
    np.savez(OUT / "features.npz", **sets)


class Net(torch.nn.Module):
    def __init__(self, width=128):
        super().__init__()
        self.body = torch.nn.Sequential(
            torch.nn.Flatten(),
            torch.nn.Linear(FRAMES * 96, width),
            torch.nn.LayerNorm(width),
            torch.nn.ReLU(),
            torch.nn.Dropout(0.3),
            torch.nn.Linear(width, width),
            torch.nn.LayerNorm(width),
            torch.nn.ReLU(),
            torch.nn.Linear(width, 1),
            torch.nn.Sigmoid(),
        )

    def forward(self, x):
        return self.body(x)


def train():
    data = np.load(OUT / "features.npz")
    groups = [
        (np.concatenate([data["hey_cosmo_train"], data["cosmo_train"]]), 1.0, 1.0, 192),
        (data["his_train"], 1.0, 2.0, 64),
        (data["lookalike_train"], 0.0, 8.0, 256),
        (data["tts_speech"], 0.0, 6.0, 256),
        (data["real_speech"], 0.0, 4.0, 256),
    ]
    torch.manual_seed(0)
    net = Net()
    opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-2)
    tensors = [(torch.tensor(x, dtype=torch.float32), label, weight, n) for x, label, weight, n in groups]
    steps = 2500
    for step in range(steps):
        x = torch.cat([t[torch.randint(len(t), (n,))] for t, _, _, n in tensors])
        y = torch.cat([torch.full((n,), label) for _, label, _, n in tensors])
        w = torch.cat([torch.full((n,), weight) for _, _, weight, n in tensors])
        p = net(x).squeeze(-1).clamp(1e-6, 1 - 1e-6)
        loss = torch.nn.functional.binary_cross_entropy(p, y, weight=w)
        opt.zero_grad()
        loss.backward()
        opt.step()
        if step % 1000 == 0:
            print(f"  step {step} loss {loss.item():.4f}", flush=True)
    net.eval()
    torch.onnx.export(net, torch.rand(1, FRAMES, 96), str(OUT / "hey_cosmo.onnx"), output_names=["hey_cosmo"], dynamo=False)
    print("train: saved", OUT / "hey_cosmo.onnx")


def stream(oww, audio, threshold, refractory=2.0):
    oww.reset()
    pcm = to_int16(np.concatenate([np.zeros(RATE, np.float32), audio, np.zeros(RATE, np.float32)]))
    hits, last = [], -99.0
    for i in range(0, len(pcm) - 1280 + 1, 1280):
        score = oww.predict(pcm[i : i + 1280])["hey_cosmo"]
        t = i / RATE - 1.0
        if score >= threshold and t - last > refractory:
            hits.append(round(t, 2))
            last = t
    return hits


def evaluate():
    import openwakeword

    oww = openwakeword.Model(wakeword_models=[str(OUT / "hey_cosmo.onnx")], inference_framework="onnx")
    report = {}
    eval_speech = [load(Path(p)) for p, split in wispr_clips().items() if split != "train"]
    eval_speech += [load(p) for p in sorted((OUT / "tts" / "eval_general").glob("*.wav"))]
    hours = sum(len(a) for a in eval_speech) / RATE / 3600
    from cosmo import config, stt
    from cosmo.wake import said_wake_word

    cfg = config.load()
    engine, words = stt.SpeechEngine(cfg), config.path_of(cfg, "words_file")
    for threshold in (0.3, 0.5, 0.7):
        false, confirmed = 0, 0
        for audio in eval_speech:
            for t in stream(oww, audio, threshold):
                false += 1
                seg = audio[max(0, int((t - 1.5) * RATE)) : int((t + 1.5) * RATE)]
                confirmed += said_wake_word(engine.transcribe(seg, words))
        row = {"false_wakes": false, "speech_hours": round(hours, 2), "per_hour": round(false / hours, 2), "whisper_confirmed_false": confirmed}
        for kind in ("hey_cosmo", "cosmo", "lookalike"):
            files = [p for p in sorted((OUT / "tts" / kind).glob("*.wav")) if "_".join(p.name.split("_")[:2]) in HELD_OUT]
            fired = sum(bool(stream(oww, load(p), threshold)) for p in files)
            row[f"{kind}_held_out_voices"] = f"{fired}/{len(files)}"
        times = json.loads((OUT / "baseline_cosmo_times.json").read_text())
        hits = stream(oww, load(BASELINE), threshold)
        near = lambda a, b: any(a - 0.5 <= h <= b + 1.5 for h in hits)
        held = times[len(times) // 2 + 1 :]
        row["his_cosmo_trained_on"] = f"{sum(near(a, b) for a, b, _ in times[: len(times) // 2 + 1])}/{len(times) // 2 + 1}"
        row["his_cosmo_held_out"] = f"{sum(near(a, b) for a, b, _ in held)}/{len(held)}"
        row["his_recording_false"] = sum(not any(a - 0.5 <= h <= b + 1.5 for a, b, _ in times) for h in hits)
        report[str(threshold)] = row
        print(threshold, json.dumps(row), flush=True)
    (OUT / "eval.json").write_text(json.dumps(report, indent=2))


def verifier():
    import shutil

    from openwakeword.custom_verifier_model import train_custom_verifier

    folder = OUT / "verifier"
    shutil.rmtree(folder, ignore_errors=True)
    for i, audio in enumerate(his_cosmos("train")):
        save(folder / "pos" / f"his_{i}.wav", np.pad(audio, (8000, 8000)))
    others = [p for k in ("cosmo", "hey_cosmo") for p in sorted((OUT / "tts" / k).glob("*.wav")) if "_".join(p.name.split("_")[:2]) not in HELD_OUT]
    for p in np.random.default_rng(1).choice(others, 80, replace=False):
        save(folder / "neg" / p.name, np.pad(load(p), (8000, 8000)))
    train_custom_verifier(
        sorted(map(str, (folder / "pos").glob("*.wav"))),
        sorted(map(str, (folder / "neg").glob("*.wav"))),
        str(OUT / "verifier.pkl"),
        str(OUT / "hey_cosmo.onnx"),
        inference_framework="onnx",
    )


STEPS = {"speak": speak, "features": features, "train": train, "verifier": verifier, "eval": evaluate}

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name in sys.argv[1:] or list(STEPS):
        print(f"== {name}", flush=True)
        STEPS[name]()
