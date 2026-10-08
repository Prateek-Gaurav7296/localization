#!/usr/bin/env python3
"""
Block J-refit — re-assemble an existing run, squeezing every overflowing line
into its slot exactly. No new TTS calls: it reuses the clips already rendered.

Each line is time-stretched (pitch preserved) by exactly spoken/budget, so the
placed clip equals the original slot and nothing overlaps. The spec's 8% rule
says speech past that starts sounding artificial - this prints the tempo applied
per line and flags the ones beyond it, so you can hear where the limit really is.

  .venv/bin/python feasibility/blocks/block_j_refit.py \
      --run feasibility/results/block_j_pa_20261005-2237 \
      --video ~/fixtures/ads/IT0AfU3277I.mp4
"""
from __future__ import annotations
import argparse, array, json, sys, wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import block_j_assemble as J

SR = J.SR
QUALITY_CAP = 1.08          # the spec's "past 8% it sounds artificial"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="an existing block_j_* directory")
    ap.add_argument("--duration", type=float, default=30.047)
    ap.add_argument("--max-tempo", type=float, default=0.0,
                    help="cap the squeeze (0 = no cap, fit every line exactly)")
    ap.add_argument("--mode", default="squeeze", choices=["squeeze", "borrow"],
                    help="squeeze: stretch the line into its own slot. "
                         "borrow: let it run on into the silence that follows, "
                         "stretching only if it would hit the next line.")
    ap.add_argument("--video", default=None)
    a = ap.parse_args()

    run = Path(a.run)
    report = json.loads((run / "report.json").read_text(encoding="utf-8"))
    lines = [r for r in report if "error" not in r]
    tag = a.mode + ("" if not a.max_tempo else f"_cap{a.max_tempo:.2f}")
    outdir = run / f"refit_{tag}"
    (outdir / "clips").mkdir(parents=True, exist_ok=True)

    bed = array.array("h", bytes(int(a.duration * SR) * 2))
    lines.sort(key=lambda r: r["start"])
    # How much room each line really has: its own slot plus the silence after it,
    # up to the moment the next line starts.
    for i, r in enumerate(lines):
        nxt = lines[i + 1]["start"] if i + 1 < len(lines) else a.duration
        r["available_s"] = max(r["budget_s"], nxt - r["start"])
    print(f"{len(lines)} clips, bed {a.duration:.3f}s, mode={a.mode}"
          + (f", tempo capped at {a.max_tempo:.2f}" if a.max_tempo else ""))
    print(f"\n{'#':>3} {'slot':>6} {'avail':>6} {'spoken':>7} {'tempo':>6} {'placed':>7}  note")

    beyond = borrowed = 0
    for r in lines:
        src = run / "clips" / f"{r['i']:02d}_48k.wav"
        if not src.exists():
            print(f"{r['i']:>3}  missing clip {src.name}"); continue
        budget, spoken = r["budget_s"], r["spoken_s"]
        target = budget if a.mode == "squeeze" else r["available_s"]
        tempo = 1.0
        if spoken > target * 1.005:
            tempo = spoken / target
            if a.max_tempo:
                tempo = min(tempo, a.max_tempo)
        if tempo > 1.0005:
            dst = outdir / "clips" / f"{r['i']:02d}_fit.wav"
            J.atempo(src, dst, tempo)
        else:
            dst = src
        samples = J.trim(J.read_samples(dst))
        placed = len(samples) / SR
        flag = ""
        if tempo > QUALITY_CAP:
            flag = f"squeezed {100*(tempo-1):.0f}% - BEYOND 8% CAP"
            beyond += 1
        elif tempo > 1.0005:
            flag = f"squeezed {100*(tempo-1):.0f}% - within cap"
        elif a.mode == "borrow" and placed > budget * 1.02:
            flag = (f"borrowed {placed - budget:.2f}s of the "
                    f"{r['available_s'] - budget:.2f}s gap")
            borrowed += 1
        off = int(r["start"] * SR)
        for j, s in enumerate(samples):
            k = off + j
            if k >= len(bed):
                break
            bed[k] = max(-32768, min(32767, bed[k] + s))
        print(f"{r['i']:>3} {budget:6.2f} {r['available_s']:6.2f} {spoken:7.2f} "
              f"{tempo:6.3f} {placed:7.2f}  {flag}")

    track = outdir / f"dub_vocals_{a.mode}.wav"
    J.write_wav(bed, track)
    print(f"\n  track   : {track}  {len(bed)/SR:.3f}s")
    print(f"  squeezed beyond the 8% cap : {beyond}/{len(lines)} lines")
    if a.mode == "borrow":
        print(f"  ran on into the gap        : {borrowed}/{len(lines)} lines "
              f"(no stretching needed)")

    if a.video:
        out_mp4 = outdir / f"preview_{a.mode}.mp4"
        J.run([J.ffmpeg(), "-y", "-v", "error", "-i", str(Path(a.video).expanduser()),
               "-i", str(track), "-map", "0:v:0", "-map", "1:a:0",
               "-c:v", "copy", "-c:a", "aac", "-shortest", str(out_mp4)])
        print(f"  preview : {out_mp4}")


if __name__ == "__main__":
    main()
