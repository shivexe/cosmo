import json
import random
import statistics
import sys
import time
from pathlib import Path

import soundfile as sf
import torch
from peft import PeftModel
from transformers import WhisperForConditionalGeneration, WhisperProcessor

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import score_models as sm  # noqa: E402
from prompt_test import PROMPT  # noqa: E402

N_CLIPS = 24
rows = [json.loads(l) for n in ("val", "test") for l in (sm.DATA / f"{n}.jsonl").read_text().splitlines()]
rows = [r for r in rows if 2.0 <= r["duration"] <= 10.0]
random.Random(0).shuffle(rows)
rows = rows[:N_CLIPS]
PATHS = [r["audio"] for r in rows]
AUDIO = [sf.read(p, dtype="float32")[0] for p in PATHS]
SECONDS = [len(a) / 16000 for a in AUDIO]
LISTS = {p: a.tolist() for a, p in zip(AUDIO, PATHS)}
results = {}


def sync():
    if sm.DEVICE == "mps":
        torch.mps.synchronize()


def bench(name, fn):
    for i in range(2):
        fn(AUDIO[i], PATHS[i])
    times = []
    for a, p in zip(AUDIO, PATHS):
        sync()
        t = time.perf_counter()
        fn(a, p)
        sync()
        times.append(time.perf_counter() - t)
    q = statistics.quantiles(times, n=10)
    results[name] = {"median_s": round(statistics.median(times), 3), "p90_s": round(q[8], 3),
                     "rtf": round(sum(times) / sum(SECONDS), 3)}
    print(name, results[name], flush=True)


def hf_fn(model, processor, prompt):
    kw = {}
    if prompt:
        kw["prompt_ids"] = processor.get_prompt_ids(prompt, return_tensors="pt").to(sm.DEVICE)

    def fn(a, _path):
        feats = processor.feature_extractor([a], sampling_rate=16000, return_tensors="pt").input_features.to(sm.DEVICE)
        with torch.no_grad():
            ids = model.generate(input_features=feats, max_new_tokens=200, use_cache=True, **kw)
        return processor.batch_decode(ids, skip_special_tokens=True)[0]

    return fn


def main():
    print(f"{N_CLIPS} clips, {min(SECONDS):.1f} to {max(SECONDS):.1f} s, mean {sum(SECONDS) / len(SECONDS):.1f} s", flush=True)
    processor = WhisperProcessor.from_pretrained(sm.MODEL)
    base = WhisperForConditionalGeneration.from_pretrained(sm.MODEL, dtype=torch.float32)
    base.generation_config.forced_decoder_ids = None
    model = PeftModel.from_pretrained(base, str(sm.ADAPTER)).to(sm.DEVICE).eval()
    with model.disable_adapter():
        bench("small.en plain (PyTorch, no hint)", hf_fn(model, processor, None))
    bench("small.en + LoRA + hint (PyTorch)", hf_fn(model, processor, PROMPT))
    model.merge_adapter()
    bench("small.en + LoRA merged + hint (PyTorch)", hf_fn(model, processor, PROMPT))
    del model, base
    sm.free()

    import mlx.core as mx
    import mlx_whisper
    from moonshine_voice import ModelArch, get_model_for_language
    from moonshine_voice.transcriber import Transcriber
    from parakeet_mlx import from_pretrained
    from parakeet_mlx.audio import get_logmel

    bench("small.en plain (MLX, no hint)", lambda a, p: mlx_whisper.transcribe(
        a, path_or_hf_repo="mlx-community/whisper-small.en-mlx", language="en")["text"])
    bench("turbo + hint (MLX)", lambda a, p: mlx_whisper.transcribe(
        a, path_or_hf_repo=sm.TURBO, language="en", initial_prompt=PROMPT)["text"])
    pk = from_pretrained(sm.PARAKEET)
    bench("parakeet 0.6B (MLX)", lambda a, p: pk.generate(get_logmel(mx.array(a), pk.preprocessor_config))[0].text)
    for label, arch in (("moonshine small streaming", ModelArch.SMALL_STREAMING),
                        ("moonshine medium streaming", ModelArch.MEDIUM_STREAMING)):
        path, arch = get_model_for_language("en", arch)
        tr = Transcriber(path, arch)
        bench(label + " (batch mode)", lambda a, p, tr=tr: tr.transcribe_without_streaming(LISTS[p], 16000))

    (HERE / "runs" / "latency.json").write_text(json.dumps({"clips": N_CLIPS, "mean_seconds": sum(SECONDS) / len(SECONDS), "results": results}, indent=2))
    print(f"\n{'model':44s} {'median s':>9s} {'p90 s':>7s} {'RTF':>6s}")
    for name, r in results.items():
        print(f"{name:44s} {r['median_s']:9.3f} {r['p90_s']:7.3f} {r['rtf']:6.3f}")


if __name__ == "__main__":
    main()
