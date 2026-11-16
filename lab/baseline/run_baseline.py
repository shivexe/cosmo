import argparse
import json
import re
import resource
import subprocess
import sys
import time
import wave
from pathlib import Path

HERE = Path(__file__).parent
WAV = HERE / "me-baseline.wav"
REF = HERE / "reading-script.txt"
OUT = HERE / "results"

WHISPER = {
    "whisper-base.en": "mlx-community/whisper-base.en-mlx",
    "whisper-small.en": "mlx-community/whisper-small.en-mlx",
    "whisper-large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}
MODELS = [
    "whisper-base.en",
    "whisper-small.en",
    "whisper-large-v3-turbo",
    "parakeet-tdt-0.6b-v2",
    "moonshine-small-streaming",
    "moonshine-medium-streaming",
]


def audio_seconds():
    with wave.open(str(WAV)) as w:
        return w.getnframes() / w.getframerate()


def make_runner(key):
    if key in WHISPER:
        import mlx_whisper

        repo = WHISPER[key]
        return lambda: mlx_whisper.transcribe(str(WAV), path_or_hf_repo=repo, language="en")["text"]
    if key.startswith("parakeet"):
        from parakeet_mlx import from_pretrained

        model = from_pretrained("mlx-community/" + key)
        return lambda: model.transcribe(str(WAV), chunk_duration=120.0, overlap_duration=15.0).text
    if key.startswith("moonshine"):
        from moonshine_voice import ModelArch, get_model_for_language, load_wav_file
        from moonshine_voice.transcriber import Transcriber

        arch = {
            "moonshine-small-streaming": ModelArch.SMALL_STREAMING,
            "moonshine-medium-streaming": ModelArch.MEDIUM_STREAMING,
        }[key]
        path, arch = get_model_for_language("en", arch)
        transcriber = Transcriber(path, arch)
        audio, rate = load_wav_file(WAV)
        return lambda: " ".join(l.text for l in transcriber.transcribe_without_streaming(audio, rate).lines)
    raise SystemExit("unknown model " + key)


def child(key):
    run = make_runner(key)
    text = run()
    t = time.perf_counter()
    run()
    seconds_warm = time.perf_counter() - t
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024
    try:
        import mlx.core as mx

        mlx_peak_mb = round(mx.get_peak_memory() / 1e6)
    except Exception:
        mlx_peak_mb = None
    OUT.mkdir(exist_ok=True)
    (OUT / f"{key}.txt").write_text(text)
    print("RESULT " + json.dumps({
        "model": key,
        "seconds_warm": round(seconds_warm, 2),
        "peak_mb": round(peak_mb),
        "mlx_peak_mb": mlx_peak_mb,
    }))


def norm(text):
    from num2words import num2words

    t = text.lower().replace("’", "'")
    t = re.sub(r"(\d{1,2}):(\d{2})", lambda m: num2words(int(m[1])) + " " + num2words(int(m[2])), t)
    t = re.sub(r"(\d+)(st|nd|rd|th)\b", lambda m: num2words(int(m[1]), to="ordinal"), t)
    t = re.sub(r"\d+", lambda m: num2words(int(m[0])), t)
    t = t.replace("-", " ")
    t = t.replace("openwakeword", "open wake word")
    t = re.sub(r"\bmb\b", "megabytes", t)
    t = re.sub(r"\bgb\b", "gigabytes", t)
    t = re.sub(r"[^a-z' ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def score(key, info, duration):
    import jiwer

    ref = norm(REF.read_text())
    hyp = norm((OUT / f"{key}.txt").read_text())
    o = jiwer.process_words(ref, hyp)
    rw, hw = ref.split(), hyp.split()
    lines = []
    for c in o.alignments[0]:
        if c.type == "equal":
            continue
        lines.append(f"{c.type:11s} ref={' '.join(rw[c.ref_start_idx:c.ref_end_idx])!r} hyp={' '.join(hw[c.hyp_start_idx:c.hyp_end_idx])!r}")
    (OUT / f"{key}.errors.txt").write_text("\n".join(lines))
    info.update({
        "wer": round(o.wer * 100, 2),
        "substitutions": o.substitutions,
        "deletions": o.deletions,
        "insertions": o.insertions,
        "rtf_warm": round(info["seconds_warm"] / duration, 3),
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--only")
    args = ap.parse_args()
    if args.model:
        return child(args.model)
    duration = audio_seconds()
    keys = args.only.split(",") if args.only else MODELS
    rows = []
    for key in keys:
        print(f"running {key} ...", flush=True)
        try:
            p = subprocess.run([sys.executable, __file__, "--model", key], capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            print("  timed out")
            rows.append({"model": key, "error": "timeout"})
            continue
        line = next((l for l in p.stdout.splitlines() if l.startswith("RESULT ")), None)
        if p.returncode or not line:
            print(f"  failed: {p.stderr.strip().splitlines()[-1:] or p.stdout[-300:]}")
            rows.append({"model": key, "error": (p.stderr or p.stdout)[-500:]})
            continue
        info = json.loads(line[7:])
        try:
            score(key, info, duration)
        except Exception as e:
            print(f"  scoring failed: {e!r}")
            rows.append({"model": key, "error": repr(e)})
            continue
        rows.append(info)
        print(f"  WER {info['wer']}%  warm {info['seconds_warm']}s  peak {info['peak_mb']} MB", flush=True)
    OUT.mkdir(exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps({"audio_seconds": round(duration, 1), "results": rows}, indent=2))
    print(f"\naudio {duration:.0f}s")
    print(f"{'model':30s} {'WER%':>6s} {'sub':>4s} {'del':>4s} {'ins':>4s} {'warm s':>7s} {'RTF':>6s} {'RSS MB':>7s} {'MLX MB':>7s}")
    for r in rows:
        if "error" in r:
            print(f"{r['model']:30s} failed")
            continue
        print(f"{r['model']:30s} {r['wer']:6.2f} {r['substitutions']:4d} {r['deletions']:4d} {r['insertions']:4d} {r['seconds_warm']:7.2f} {r['rtf_warm']:6.3f} {r['peak_mb']:7d} {str(r['mlx_peak_mb'] if r['mlx_peak_mb'] is not None else '-'):>7s}")


if __name__ == "__main__":
    main()
