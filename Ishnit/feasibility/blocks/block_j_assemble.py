#!/usr/bin/env python3
"""
Block J — assemble the whole script into one voice track.

Renders every adapted line and places it at the ORIGINAL line's start offset on a
silent bed of exactly the source duration. Gaps stay gaps, so the track is the
same length as the source by construction - this is Track A's deliverable.

Optionally time-stretches each clip (capped, pitch preserved) to fit its slot, and
muxes the result over the source video so you can watch it.

  .venv/bin/python feasibility/blocks/block_j_assemble.py \
      --adaptation "feasibility/results/adapt_or_pa_*/adapt_anthropic*.json" \
      --video ~/fixtures/ads/IT0AfU3277I.mp4 --stretch 0.08
"""
from __future__ import annotations
import argparse, array, glob, json, math, shutil, subprocess, sys, time, wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import block_d_tts as D

ROOT = Path(__file__).resolve().parents[2]
SR = 48000


def ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args):
    r = subprocess.run(args, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode()[-400:])


def to_48k_mono(src: Path, dst: Path):
    run([ffmpeg(), "-y", "-v", "error", "-i", str(src), "-ac", "1",
         "-ar", str(SR), "-c:a", "pcm_s16le", str(dst)])
    return dst


def atempo(src: Path, dst: Path, tempo: float):
    """Pitch-preserving speed change; chain stages to stay inside ffmpeg's range."""
    stages, t = [], tempo
    while t > 2.0:
        stages.append(2.0); t /= 2.0
    while t < 0.5:
        stages.append(0.5); t /= 0.5
    stages.append(t)
    run([ffmpeg(), "-y", "-v", "error", "-i", str(src),
         "-filter:a", ",".join(f"atempo={s:.6f}" for s in stages), str(dst)])
    return dst


def read_samples(p: Path):
    with wave.open(str(p)) as w:
        raw = w.readframes(w.getnframes())
    return array.array("h", raw[:len(raw) // 2 * 2])


def trim(a: array.array, ratio=0.01):
    if not a:
        return a
    peak = max(1, max(abs(x) for x in a))
    lim = peak * ratio
    first = next((i for i, x in enumerate(a) if abs(x) > lim), 0)
    last = next((i for i in range(len(a) - 1, -1, -1) if abs(a[i]) > lim), len(a) - 1)
    return a[first:last + 1]


def write_wav(samples, path: Path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(samples.tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adaptation", required=True)
    ap.add_argument("--lang", default="pa")
    ap.add_argument("--model", default="camb:mars-instruct")
    ap.add_argument("--voice-id", type=int, default=165289)
    ap.add_argument("--duration", type=float, default=None,
                    help="source duration; default = last line end + 1.2s")
    ap.add_argument("--stretch", type=float, default=0.0,
                    help="max time-stretch to fit a slot, e.g. 0.08 for 8%%")
    ap.add_argument("--video", default=None, help="mux the track over this video")
    ap.add_argument("--pace", type=float, default=0.0,
                    help="seconds between TTS calls (free-tier quota)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    D.load_env()
    D.PACE_SECONDS = a.pace
    f = sorted(glob.glob(a.adaptation))
    if not f:
        sys.exit(f"no file matched {a.adaptation}")
    groups = json.loads(Path(f[0]).read_text(encoding="utf-8"))

    lines = []
    for g in groups:
        if not g.get("chosen"):
            continue
        for l, part in zip(g["lines"], g["chosen"]["parts"]):
            lines.append((l["start"], l["end"], l["budget_s"], part["text"]))
    lines.sort()
    if not lines:
        sys.exit("no adapted lines found")

    total = a.duration or (lines[-1][1] + 1.2)
    outdir = Path(a.out) if a.out else (ROOT / "feasibility" / "results" /
                                        f"block_j_{a.lang}_{time.strftime('%Y%m%d-%H%M')}")
    (outdir / "clips").mkdir(parents=True, exist_ok=True)
    tts = D.build(a.model, a.voice_id, a.lang)
    print(f"{len(lines)} lines -> {tts.name}, bed {total:.3f}s\n")

    bed = array.array("h", bytes(int(total * SR) * 2))
    report, collisions = [], 0
    prev_end = 0.0

    print(f"{'#':>3} {'start':>7} {'slot':>6} {'spoken':>7} {'tempo':>6} {'placed':>7} {'status':>10}")
    for i, (start, end, budget, text) in enumerate(lines):
        raw = outdir / "clips" / f"{i:02d}_raw.wav"
        try:
            tts.say(text, raw)
        except Exception as e:
            print(f"{i:>3} RENDER FAILED {str(e)[:70]}")
            report.append({"i": i, "error": str(e)[:200]}); continue
        norm = to_48k_mono(raw, outdir / "clips" / f"{i:02d}_48k.wav")
        samples = trim(read_samples(norm))
        spoken = len(samples) / SR

        tempo = 1.0
        if a.stretch > 0 and spoken > budget:
            tempo = min(spoken / budget, 1 + a.stretch)
            stretched = atempo(outdir / "clips" / f"{i:02d}_48k.wav",
                               outdir / "clips" / f"{i:02d}_fit.wav", tempo)
            samples = trim(read_samples(stretched))
        placed = len(samples) / SR

        off = int(start * SR)
        status = "ok"
        if start < prev_end - 0.01:
            status = "OVERLAP"; collisions += 1
        elif placed > budget * 1.02:
            status = "overflow"
        prev_end = start + placed

        for j, s in enumerate(samples):
            k = off + j
            if k >= len(bed):
                break
            v = bed[k] + s
            bed[k] = max(-32768, min(32767, v))

        print(f"{i:>3} {start:7.2f} {budget:6.2f} {spoken:7.2f} {tempo:6.3f} "
              f"{placed:7.2f} {status:>10}  {text[:34]}")
        report.append({"i": i, "start": start, "budget_s": round(budget, 3),
                       "spoken_s": round(spoken, 3), "tempo": round(tempo, 3),
                       "placed_s": round(placed, 3), "status": status, "text": text})

    track = outdir / "dub_vocals.wav"
    write_wav(bed, track)
    (outdir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))

    rendered = [r for r in report if "error" not in r]
    failed = [r for r in report if "error" in r]
    over = sum(1 for r in rendered if r.get("status") == "overflow")
    print(f"\n  track      : {track.name}  {len(bed)/SR:.3f}s "
          f"(source {total:.3f}s - {'MATCH' if abs(len(bed)/SR-total) < 0.01 else 'MISMATCH'})")
    print(f"  rendered   : {len(rendered)}/{len(lines)}"
          + (f"   FAILED: {[r['i'] for r in failed]}" if failed else ""))
    print(f"  overflowing: {over}/{len(rendered)} of rendered   "
          f"collisions into the next line: {collisions}")
    if failed:
        print("  NOTE: fit percentages below cover only the rendered lines")

    if a.video:
        out_mp4 = outdir / "preview.mp4"
        run([ffmpeg(), "-y", "-v", "error", "-i", str(Path(a.video).expanduser()),
             "-i", str(track), "-map", "0:v:0", "-map", "1:a:0",
             "-c:v", "copy", "-c:a", "aac", "-shortest", str(out_mp4)])
        print(f"  preview    : {out_mp4}")
    print(f"\nsaved -> {outdir}")


if __name__ == "__main__":
    main()
