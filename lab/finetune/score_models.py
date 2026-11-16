import gc
import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import jiwer
import numpy as np
import soundfile as sf
import torch
from peft import PeftModel
from transformers import WhisperForConditionalGeneration, WhisperProcessor

HERE = Path(__file__).parent
BASE = HERE.parent / "baseline"
DATA = HERE.parent / "data" / "wispr"
sys.path.insert(0, str(BASE))
from run_baseline import norm  # noqa: E402

ADAPTER = HERE / "runs" / "small-r16-e3"
MODEL = "openai/whisper-small.en"
TURBO = "mlx-community/whisper-large-v3-turbo"
PARAKEET = "mlx-community/parakeet-tdt-0.6b-v2"
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
TERMS = ["claude", "codex", "strapi", "postgres", "redis", "cloud run", "datadog", "linear", "worktree",
         "whisper", "moonshine", "parakeet", "lora", "mlx", "wake word", "kokoro", "sarvam", "hinglish",
         "cosmo", "npm", "mac"]


def free():
    gc.collect()
    if DEVICE == "mps":
        torch.mps.empty_cache()


def split_audio(x, sr=16000, lo=18.0, hi=28.0):
    hop = int(0.02 * sr)
    chunks, start = [], 0
    while len(x) - start > hi * sr:
        a, b = start + int(lo * sr), start + int(hi * sr)
        frames = (b - a) // hop
        energy = (x[a : a + frames * hop].reshape(frames, hop) ** 2).mean(1)
        cut = a + int(np.argmin(np.convolve(energy, np.ones(5) / 5, mode="same"))) * hop
        chunks.append(x[start:cut])
        start = cut
    chunks.append(x[start:])
    return chunks


def hf_transcribe(model, processor, audios):
    out = []
    for i in range(0, len(audios), 8):
        feats = processor.feature_extractor(audios[i : i + 8], sampling_rate=16000, return_tensors="pt").input_features
        with torch.no_grad():
            ids = model.generate(input_features=feats.to(DEVICE), max_new_tokens=225, use_cache=True)
        out += [t.strip() for t in processor.batch_decode(ids, skip_special_tokens=True)]
    return out


def wer(refs, hyps):
    pairs = [(norm(r), norm(h)) for r, h in zip(refs, hyps)]
    pairs = [(r, h) for r, h in pairs if r]
    return jiwer.wer([r for r, _ in pairs], [h for _, h in pairs]) * 100


def vocab(ref, hyp):
    r, h = norm(ref), norm(hyp)
    total = hit = 0
    for t in TERMS:
        pat = re.compile(r"\b" + re.escape(t) + r"\b")
        n = len(pat.findall(r))
        total += n
        hit += min(n, len(pat.findall(h)))
    return hit, total


def main():
    test = [json.loads(l) for l in (DATA / "test.jsonl").read_text().splitlines()]
    test_refs = [r["text"] for r in test]
    test_audio = [sf.read(r["audio"], dtype="float32")[0] for r in test]
    base_audio = sf.read(BASE / "me-baseline.wav", dtype="float32")[0]
    base_ref = (BASE / "reading-script.txt").read_text()
    chunks = split_audio(base_audio)
    print(f"test clips {len(test)}, baseline split into {len(chunks)} chunks, longest {max(len(c) for c in chunks) / 16000:.1f}s", flush=True)

    rows = {}

    def record(name, test_hyps, base_hyp):
        rows[name] = {"test_wer": round(wer(test_refs, test_hyps), 2), "baseline_wer": round(wer([base_ref], [base_hyp]), 2),
                      "vocab_hits": vocab(base_ref, base_hyp)}
        print(name, rows[name], flush=True)

    processor = WhisperProcessor.from_pretrained(MODEL)
    base = WhisperForConditionalGeneration.from_pretrained(MODEL, dtype=torch.float32)
    base.generation_config.forced_decoder_ids = None
    model = PeftModel.from_pretrained(base, str(ADAPTER)).to(DEVICE).eval()
    with model.disable_adapter():
        record("small.en plain", hf_transcribe(model, processor, test_audio), " ".join(hf_transcribe(model, processor, chunks)))
    record("small.en + your LoRA", hf_transcribe(model, processor, test_audio), " ".join(hf_transcribe(model, processor, chunks)))
    del model, base
    free()

    import mlx_whisper
    from parakeet_mlx import from_pretrained

    saved = lambda key: (BASE / "results" / f"{key}.txt").read_text()
    record("whisper large-v3-turbo", [mlx_whisper.transcribe(r["audio"], path_or_hf_repo=TURBO, language="en")["text"].strip() for r in test],
           saved("whisper-large-v3-turbo"))
    pk = from_pretrained(PARAKEET)
    record("parakeet 0.6B", [pk.transcribe(r["audio"]).text.strip() for r in test], saved("parakeet-tdt-0.6b-v2"))

    (HERE / "runs").mkdir(exist_ok=True)
    (HERE / "runs" / "ft_scores.json").write_text(json.dumps(rows, indent=2))
    print(f"\n{'model':26s} {'test WER%':>10s} {'baseline WER%':>14s} {'vocab hits':>11s}")
    for name, r in rows.items():
        print(f"{name:26s} {r['test_wer']:10.2f} {r['baseline_wer']:14.2f} {r['vocab_hits'][0]:>6d}/{r['vocab_hits'][1]}")


if __name__ == "__main__":
    main()
