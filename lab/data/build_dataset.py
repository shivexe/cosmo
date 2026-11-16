import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "baseline"))

import jiwer
import mlx_whisper
from run_baseline import norm

ROOT = Path(__file__).parent / "wispr"
MODEL = "mlx-community/whisper-large-v3-turbo"
MAX_WER = 0.25
MIN_SECONDS, MAX_SECONDS, MIN_WORDS = 1.0, 30.0, 3


def wer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    if len(r.split()) < MIN_WORDS:
        return None
    return jiwer.wer(r, h) if h else 1.0


def save(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def main():
    meta = json.loads((ROOT / "meta.json").read_text())
    cache = ROOT / "turbo.json"
    turbo = json.loads(cache.read_text()) if cache.exists() else {}
    for i, m in enumerate(meta):
        if m["id"] in turbo or not MIN_SECONDS <= (m["duration"] or 0) <= MAX_SECONDS:
            continue
        turbo[m["id"]] = mlx_whisper.transcribe(m["wav"], path_or_hf_repo=MODEL, language="en")["text"].strip()
        if i % 50 == 0:
            save(cache, turbo)
            print(f"transcribed {i}/{len(meta)}", flush=True)
    save(cache, turbo)

    kept, used = [], {"edited": 0, "formatted": 0, "asr": 0}
    dropped = {"length": 0, "no_text": 0, "no_match": 0}
    for m in meta:
        d = m["duration"] or 0
        if not MIN_SECONDS <= d <= MAX_SECONDS:
            dropped["length"] += 1
            continue
        best, any_text = None, False
        for src in ("edited", "formatted", "asr"):
            text = (m[src] or "").strip()
            w = wer(text, turbo[m["id"]]) if text else None
            if w is None:
                continue
            any_text = True
            if w <= MAX_WER:
                best = (w, src, text)
                break
        if best is None:
            dropped["no_match" if any_text else "no_text"] += 1
        else:
            used[best[1]] += 1
            kept.append({"audio": m["wav"], "text": best[2], "source": best[1],
                         "wer_vs_turbo": round(best[0], 3), "duration": d, "timestamp": m["timestamp"]})

    kept.sort(key=lambda r: r["timestamp"])
    a, b = int(len(kept) * 0.8), int(len(kept) * 0.9)
    sets = {"train": kept[:a], "val": kept[a:b], "test": kept[b:]}
    for name, rows in sets.items():
        (ROOT / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        print(f"{name:5s} {len(rows):4d} clips  {sum(r['duration'] for r in rows) / 3600:.2f} h")
    print("label source", used)
    print("dropped", dropped)


if __name__ == "__main__":
    main()
