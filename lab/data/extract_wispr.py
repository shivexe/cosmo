import json
import sqlite3
import subprocess
from pathlib import Path

DB = Path.home() / "Library/Application Support/Wispr Flow/flow.sqlite"
OUT = Path(__file__).parent / "wispr"
QUERY = """SELECT transcriptEntityId, timestamp, duration, speechDuration, app, status,
                  asrText, formattedText, editedText, audio
           FROM History WHERE audio IS NOT NULL ORDER BY timestamp"""


def main():
    wav_dir = OUT / "wav16k"
    wav_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    meta, failed = [], 0
    for id_, ts, dur, sdur, app, status, asr, fmt, edited, audio in con.execute(QUERY):
        wav = wav_dir / f"{id_}.wav"
        if not wav.exists():
            tmp = wav.with_suffix(".tmp.wav")
            p = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", "pipe:0",
                 "-ar", "16000", "-ac", "1", str(tmp)],
                input=audio, capture_output=True,
            )
            if p.returncode or not tmp.exists() or tmp.stat().st_size < 1000:
                tmp.unlink(missing_ok=True)
                failed += 1
                continue
            tmp.replace(wav)
        meta.append({
            "id": id_, "wav": str(wav), "timestamp": ts, "duration": dur, "speech_duration": sdur,
            "app": app, "status": status, "asr": asr, "formatted": fmt, "edited": edited,
        })
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1))
    print(f"extracted {len(meta)} clips, failed {failed}, hours {sum(m['duration'] or 0 for m in meta) / 3600:.2f}")


if __name__ == "__main__":
    main()
