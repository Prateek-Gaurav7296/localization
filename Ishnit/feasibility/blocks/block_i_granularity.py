#!/usr/bin/env python3
"""
Block I — rendering granularity: per line vs per utterance.

Per line, every call is a complete utterance to the model, so it applies
utterance-final prosody (falling pitch, final lengthening) and a cold start to
each fragment - which is why a one-akshara "ਨਾ!" took 0.94s in a 0.33s slot.

Per utterance, the lines are rendered in one call with explicit pause markers
sized to the ORIGINAL gaps, then the audio is split back at those silences.
Prosody stays continuous; the question is whether the pieces still land in
their slots.

  .venv/bin/python feasibility/blocks/block_i_granularity.py \
      --adaptation "feasibility/results/adapt_or_pa_*/adapt_anthropic*.json" --utterance 1
"""
from __future__ import annotations
import argparse, array, glob, json, statistics, sys, time, wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import block_d_tts as D

ROOT = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------ pause markup ---

def break_tag(provider_name: str, ms: int) -> str:
    if provider_name.startswith("camb"):
        return f'<break time="{int(ms)}ms"/>'
    # Gemini exposes three documented buckets, not arbitrary values
    return "[short pause]" if ms < 375 else "[medium pause]" if ms < 750 else "[long pause]"


# --------------------------------------------------------------- splitting ---

def frames(path, win_ms=20):
    with wave.open(str(path)) as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    a = array.array("h", raw[:len(raw) // 2 * 2])
    if ch == 2:
        a = array.array("h", a[0::2])
    n = max(1, int(sr * win_ms / 1000))
    out = []
    for i in range(0, len(a) - n, n):
        seg = a[i:i + n]
        out.append((i / sr, (sum(x * x for x in seg) / n) ** 0.5))
    return out, sr, len(a) / sr


def silence_runs(path, thresh_ratio=0.08, min_ms=120):
    fr, sr, dur = frames(path)
    if not fr:
        return [], 0.0
    peak = max(r for _, r in fr) or 1
    lim = peak * thresh_ratio
    runs, start = [], None
    for t, r in fr:
        if r < lim and start is None:
            start = t
        elif r >= lim and start is not None:
            if (t - start) * 1000 >= min_ms:
                runs.append((start, t))
            start = None
    if start is not None and (dur - start) * 1000 >= min_ms:
        runs.append((start, dur))
    return runs, dur


def split_at(path, n_pieces, out_dir, stem):
    """Split into n_pieces at the n-1 longest internal silences."""
    runs, dur = silence_runs(path)
    internal = [r for r in runs if r[0] > 0.05 and r[1] < dur - 0.05]
    internal.sort(key=lambda r: r[0] - r[1])          # longest first
    cuts = sorted((a + b) / 2 for a, b in internal[:n_pieces - 1])
    bounds = [0.0] + cuts + [dur]
    with wave.open(str(path)) as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    pieces = []
    for i in range(len(bounds) - 1):
        s = int(bounds[i] * sr) * ch * sw
        e = int(bounds[i + 1] * sr) * ch * sw
        p = out_dir / f"{stem}_piece{i}.wav"
        with wave.open(str(p), "wb") as w2:
            w2.setnchannels(ch); w2.setsampwidth(sw); w2.setframerate(sr)
            w2.writeframes(raw[s:e])
        pieces.append(p)
    return pieces, len(internal), dur


# -------------------------------------------------------------------- main ---

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adaptation", required=True)
    ap.add_argument("--utterance", type=int, default=1)
    ap.add_argument("--lang", default="pa")
    ap.add_argument("--models", default="camb:mars-instruct,gemini:gemini-3.8-flash-tts")
    ap.add_argument("--voice-id", type=int, default=165289)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    D.load_env()
    f = sorted(glob.glob(a.adaptation))
    if not f:
        sys.exit(f"no file matched {a.adaptation}")
    groups = json.loads(Path(f[0]).read_text(encoding="utf-8"))
    g = next((x for x in groups if x["index"] == a.utterance and x.get("chosen")), None)
    if not g:
        sys.exit(f"utterance {a.utterance} not found or has no chosen candidate")

    parts = [p["text"] for p in g["chosen"]["parts"]]
    budgets = [l["budget_s"] for l in g["lines"]]
    gaps = [round(1000 * (g["lines"][i + 1]["start"] - g["lines"][i]["end"]))
            for i in range(len(g["lines"]) - 1)]
    outdir = Path(a.out) if a.out else (ROOT / "feasibility" / "results" /
                                        f"block_i_{a.lang}_u{a.utterance}_{time.strftime('%H%M')}")
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"utterance {a.utterance}: {len(parts)} lines")
    for t, b in zip(parts, budgets):
        print(f"   {b:5.2f}s  {t}")
    print(f"   original gaps between them: {gaps} ms\n")

    for spec in [m.strip() for m in a.models.split(",") if m.strip()]:
        try:
            tts = D.build(spec, a.voice_id, a.lang)
        except Exception as e:
            print(f"!! {spec}: {str(e)[:120]}"); continue
        stem = tts.name.replace(":", "_")
        print(f"{'=' * 74}\n{tts.name}\n{'=' * 74}")

        # ---- A: per line
        per = []
        try:
            for i, (text, b) in enumerate(zip(parts, budgets)):
                p = outdir / f"{stem}_line{i}.wav"
                tts.say(text, p)
                per.append(D.trim_silence(p))
        except Exception as e:
            print(f"  per-line failed: {str(e)[:140]}\n"); continue

        # ---- B: one call, explicit breaks sized to the original gaps
        joined = parts[0]
        for i, nxt in enumerate(parts[1:]):
            joined += " " + break_tag(tts.name, gaps[i]) + " " + nxt
        comb = outdir / f"{stem}_combined.wav"
        try:
            tts.say(joined, comb)
        except Exception as e:
            print(f"  combined failed: {str(e)[:140]}\n"); continue
        pieces, n_sil, comb_dur = split_at(comb, len(parts), outdir, stem)
        comb_parts = [D.trim_silence(p) for p in pieces]

        print(f"{'#':>3} {'budget':>7} {'per-line':>9} {'Δ':>7} {'combined':>9} {'Δ':>7}")
        for i, b in enumerate(budgets):
            pl, cb = per[i], comb_parts[i] if i < len(comb_parts) else float("nan")
            print(f"{i:>3} {b:7.2f} {pl:9.2f} {100*(pl-b)/b:+6.0f}% "
                  f"{cb:9.2f} {100*(cb-b)/b:+6.0f}%")

        speech_pl = sum(per)
        total_pl = sum(per) + sum(gaps) / 1000          # placed on the timeline
        slot_total = (g["lines"][-1]["end"] - g["lines"][0]["start"])
        err_pl = statistics.mean(abs(p - b) / b for p, b in zip(per, budgets))
        err_cb = statistics.mean(abs(c - b) / b for c, b in zip(comb_parts, budgets)) \
            if len(comb_parts) == len(budgets) else float("nan")
        print(f"\n  speech only      per-line {speech_pl:6.2f}s   combined {sum(comb_parts):6.2f}s")
        print(f"  whole utterance  per-line {total_pl:6.2f}s   combined {comb_dur:6.2f}s"
              f"   slot {slot_total:6.2f}s")
        print(f"  mean |delta|     per-line {100*err_pl:5.1f}%     combined {100*err_cb:5.1f}%")
        print(f"  silences found in combined: {n_sil} (expected {len(parts)-1})"
              f"{'  <- split unreliable' if n_sil < len(parts)-1 else ''}")
        print(f"  verdict          : "
              f"{'combined' if err_cb < err_pl else 'per-line'} fits closer\n")

    print(f"saved -> {outdir}")


if __name__ == "__main__":
    main()
