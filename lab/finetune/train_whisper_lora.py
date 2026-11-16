import argparse
import gc
import json
import math
import os
import random
import sys
import time
from pathlib import Path

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import soundfile as sf
import torch
from peft import LoraConfig, get_peft_model
from transformers import WhisperForConditionalGeneration, WhisperProcessor

HERE = Path(__file__).parent
DATA = HERE.parent / "data" / "wispr"
MODEL = "openai/whisper-small.en"
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"


def rows_of(split):
    return [json.loads(l) for l in (DATA / f"{split}.jsonl").read_text().splitlines()]


def features(rows, processor):
    audio = [sf.read(r["audio"], dtype="float32")[0] for r in rows]
    return processor.feature_extractor(audio, sampling_rate=16000, return_tensors="pt").input_features


def labels(rows, processor, start_id):
    ids = [processor.tokenizer(r["text"]).input_ids for r in rows]
    ids = [i[1:] if i and i[0] == start_id else i for i in ids]
    width = -(-max(map(len, ids)) // 16) * 16
    out = torch.full((len(ids), width), -100)
    for k, i in enumerate(ids):
        out[k, : len(i)] = torch.tensor(i)
    return out


def sync():
    if DEVICE == "mps":
        torch.mps.synchronize()


def free():
    gc.collect()
    if DEVICE == "mps":
        torch.mps.empty_cache()


def mem_mb():
    return torch.mps.driver_allocated_memory() / 1e6 if DEVICE == "mps" else 0.0


def evaluate(model, processor, split):
    import jiwer

    sys.path.insert(0, str(HERE.parent / "baseline"))
    from run_baseline import norm

    model.eval()
    rows = rows_of(split)
    refs, hyps = [], []
    with torch.no_grad():
        for i in range(0, len(rows), 8):
            chunk = rows[i : i + 8]
            out = model.generate(input_features=features(chunk, processor).to(DEVICE), max_new_tokens=225, use_cache=True)
            hyps += processor.batch_decode(out, skip_special_tokens=True)
            refs += [r["text"] for r in chunk]
    pairs = [(norm(r), norm(h)) for r, h in zip(refs, hyps)]
    pairs = [(r, h) for r, h in pairs if r]
    score = jiwer.wer([r for r, _ in pairs], [h for _, h in pairs]) * 100
    model.train()
    free()
    return score


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=0, help="stop after this many steps (timing test)")
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--lr", type=float, default=5e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--alpha", type=int, default=16)
    ap.add_argument("--eval", action="store_true", help="score the validation set after training")
    ap.add_argument("--eval-before", action="store_true", help="also score it before training (uses extra memory)")
    ap.add_argument("--out")
    a = ap.parse_args()
    torch.manual_seed(0)
    rng = random.Random(0)

    processor = WhisperProcessor.from_pretrained(MODEL)
    model = WhisperForConditionalGeneration.from_pretrained(MODEL, dtype=torch.float32)
    model.generation_config.forced_decoder_ids = None
    model.config.use_cache = False
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model = get_peft_model(model, LoraConfig(
        r=a.rank, lora_alpha=a.alpha, target_modules=["q_proj", "v_proj"], lora_dropout=0.05, bias="none"))
    model.print_trainable_parameters()
    model.to(DEVICE)
    model.train()
    start_id = model.config.decoder_start_token_id

    train = rows_of("train")
    per_epoch = math.ceil(len(train) / a.batch)
    total = a.steps or int(per_epoch * a.epochs)
    warm = max(1, total // 10)
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=a.lr)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min((s + 1) / warm, 1.0) * max(0.0, 1 - s / total))

    if a.eval_before:
        print(f"validation WER before: {evaluate(model, processor, 'val'):.2f}%", flush=True)

    step, losses, t_data, t_step, peak = 0, [], [], [], 0.0
    while step < total:
        rng.shuffle(train)
        for i in range(0, len(train), a.batch):
            if step >= total:
                break
            rows = train[i : i + a.batch]
            t0 = time.perf_counter()
            x = features(rows, processor).to(DEVICE)
            y = labels(rows, processor, start_id).to(DEVICE)
            sync()
            t1 = time.perf_counter()
            loss = model(input_features=x, labels=y).loss
            loss.backward()
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            sync()
            t2 = time.perf_counter()
            losses.append(loss.item())
            t_data.append(t1 - t0)
            t_step.append(t2 - t1)
            peak = max(peak, mem_mb())
            step += 1
            if step % 25 == 0:
                free()
            print(f"step {step}/{total}  loss {losses[-1]:.3f}  data {t1 - t0:.2f}s  train {t2 - t1:.2f}s", flush=True)

    skip = 2 if len(t_step) > 4 else 0
    per = (sum(t_step[skip:]) + sum(t_data[skip:])) / len(t_step[skip:])
    print(f"\nbatch {a.batch}: {per:.2f} s/step (data {sum(t_data[skip:]) / len(t_data[skip:]):.2f}s, first {skip} steps skipped)")
    print(f"GPU memory held: {peak:.0f} MB   loss first {losses[0]:.3f} last {losses[-1]:.3f}")
    print(f"{per_epoch} steps/epoch  ->  one epoch about {per * per_epoch / 60:.1f} min, three epochs about {per * per_epoch * 3 / 60:.1f} min")

    if a.out:
        model.save_pretrained(a.out)
        print("adapter saved to", a.out)
    del opt, sched
    free()
    if a.eval:
        print(f"validation WER after: {evaluate(model, processor, 'val'):.2f}%")


if __name__ == "__main__":
    main()
