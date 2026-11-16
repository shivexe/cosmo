import json
import sys
from pathlib import Path

import soundfile as sf
import torch
from peft import PeftModel
from transformers import WhisperForConditionalGeneration, WhisperProcessor

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import score_models as sm  # noqa: E402

PROMPT = ("Claude Code, Codex, Strapi, Postgres, Redis, Datadog, Linear, worktree, Whisper, Moonshine, "
          "Parakeet, LoRA, MLX, openWakeWord, Kokoro, Sarvam, Hinglish, Cosmo.")


def hf_prompted(model, processor, audios, prompt):
    kwargs = {}
    if prompt:
        kwargs["prompt_ids"] = processor.get_prompt_ids(prompt, return_tensors="pt").to(sm.DEVICE)
    out = []
    for i in range(0, len(audios), 8):
        feats = processor.feature_extractor(audios[i : i + 8], sampling_rate=16000, return_tensors="pt").input_features
        with torch.no_grad():
            seq = model.generate(input_features=feats.to(sm.DEVICE), max_new_tokens=200, use_cache=True, **kwargs)
        for t in processor.batch_decode(seq, skip_special_tokens=True):
            t = t.strip()
            if prompt and t.startswith(prompt):
                t = t[len(prompt):].strip()
            out.append(t)
    return out


def main():
    test = [json.loads(l) for l in (sm.DATA / "test.jsonl").read_text().splitlines()]
    test_refs = [r["text"] for r in test]
    test_audio = [sf.read(r["audio"], dtype="float32")[0] for r in test]
    base_audio = sf.read(sm.BASE / "me-baseline.wav", dtype="float32")[0]
    base_ref = (sm.BASE / "reading-script.txt").read_text()
    chunks = sm.split_audio(base_audio)
    rows = {}

    def record(name, test_hyps, base_hyp):
        rows[name] = {"test_wer": round(sm.wer(test_refs, test_hyps), 2),
                      "baseline_wer": round(sm.wer([base_ref], [base_hyp]), 2), "vocab_hits": sm.vocab(base_ref, base_hyp)}
        print(name, rows[name], flush=True)

    processor = WhisperProcessor.from_pretrained(sm.MODEL)
    base = WhisperForConditionalGeneration.from_pretrained(sm.MODEL, dtype=torch.float32)
    base.generation_config.forced_decoder_ids = None
    model = PeftModel.from_pretrained(base, str(sm.ADAPTER)).to(sm.DEVICE).eval()
    for label, plain in (("small.en plain", True), ("small.en + LoRA", False)):
        for prompt in (None, PROMPT):
            name = f"{label}{' + prompt' if prompt else ''}"

            def go(audios):
                if plain:
                    with model.disable_adapter():
                        return hf_prompted(model, processor, audios, prompt)
                return hf_prompted(model, processor, audios, prompt)

            record(name, go(test_audio), " ".join(go(chunks)))
            if prompt:
                print("  prompt leak check, first 60 chars of baseline output:", " ".join(go(chunks[:1]))[:60], flush=True)
    del model, base
    sm.free()

    import mlx_whisper

    for prompt in (None, PROMPT):
        name = "turbo" + (" + prompt" if prompt else "")
        hyps = [mlx_whisper.transcribe(r["audio"], path_or_hf_repo=sm.TURBO, language="en", initial_prompt=prompt)["text"].strip() for r in test]
        full = mlx_whisper.transcribe(str(sm.BASE / "me-baseline.wav"), path_or_hf_repo=sm.TURBO, language="en", initial_prompt=prompt)["text"].strip()
        record(name, hyps, full)

    (HERE / "runs" / "prompt_scores.json").write_text(json.dumps(rows, indent=2))
    print(f"\n{'model':26s} {'test WER%':>10s} {'baseline WER%':>14s} {'vocab hits':>11s}")
    for name, r in rows.items():
        print(f"{name:26s} {r['test_wer']:10.2f} {r['baseline_wer']:14.2f} {r['vocab_hits'][0]:>6d}/{r['vocab_hits'][1]}")


if __name__ == "__main__":
    main()
