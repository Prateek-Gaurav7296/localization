#!/usr/bin/env python3
"""
Block A / Q1 — Camb.ai STT probe.

  A1/A3  transcript + segment boundaries
  A2     word-level boundaries + the granularity they are snapped to
  A4     code-switching: transcribed or silently translated?
  A7     diarization -> more than one speaker means Stage 1 rejects the job

Needs CAMB_API_KEY (Studio -> Settings -> API Keys), read from env or
speech-engine/.env. Run with the repo venv:

  .venv/bin/python feasibility/blocks/block_a_camb_stt.py fixtures/ads/X.wav
"""
import argparse, json, os, sys, time, wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
import ledger

LANGS = {"hi-IN": 81, "en-IN": 38, "en-US": 1, "kn-IN": 93,
         "te-IN": 129, "ta-IN": 125, "ml-IN": 99, "mr-IN": 101,
         "bn-IN": 23, "gu-IN": 79, "pa-IN": 148}


def load_env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                if v.strip():                      # ignore blank entries
                    os.environ.setdefault(k.strip(), v.strip())


def wav_duration(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def dump(obj):
    for attr in ("model_dump", "dict"):
        if hasattr(obj, attr):
            try:
                return getattr(obj, attr)()
            except Exception:
                pass
    return obj if isinstance(obj, (dict, list)) else str(obj)


def granularity(vals):
    frac = [round(float(v) % 1, 6) for v in vals]
    if not frac:
        return "no offsets returned"
    if all(f == 0 for f in frac):
        return "WHOLE SECONDS - unusable"
    if all(abs(f * 10 - round(f * 10)) < 1e-9 for f in frac):
        return "100ms steps - too coarse for +-50ms boundaries"
    if all(abs(f * 100 - round(f * 100)) < 1e-9 for f in frac):
        return "10ms steps - fine enough, measure real error vs ground truth"
    return "sub-10ms - fine enough, measure real error vs ground truth"


def walk_words(obj, out, under=False):
    """Only collect entries under an explicit word-level key - a one-word
    SEGMENT is not a word timestamp, and counting it as one hides the fact
    that the provider returned no word timings at all."""
    WORD_KEYS = {"words", "word_timestamps", "word_level_timestamps", "tokens"}
    if isinstance(obj, dict):
        if under and {"start", "end"} <= {k.lower() for k in obj}:
            w = str(obj.get("word") or obj.get("text") or obj.get("w") or "")
            out.append((w, float(obj["start"]), float(obj["end"])))
        for k, v in obj.items():
            walk_words(v, out, under or k.lower() in WORD_KEYS)
    elif isinstance(obj, list):
        for v in obj:
            walk_words(v, out, under)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--language", default="hi-IN", help="|".join(LANGS))
    ap.add_argument("--mode", default="slow", choices=["fast", "slow"])
    ap.add_argument("--out", default=str(ROOT / "feasibility" / "results"))
    a = ap.parse_args()

    load_env()
    key = os.environ.get("CAMB_API_KEY")
    if not key:
        sys.exit("Set CAMB_API_KEY in speech-engine/.env or the environment")
    if a.language not in LANGS:
        sys.exit(f"language must be one of: {', '.join(LANGS)}")

    from camb.client import CambAI
    client = CambAI(api_key=key)

    p = Path(a.audio)
    dur = wav_duration(p) if p.suffix == ".wav" else None
    print(f"{p.name}  {dur:.3f}s  ->  camb.ai  lang={a.language}({LANGS[a.language]})  mode={a.mode}\n")

    with p.open("rb") as fh:
        task = client.transcription.create_transcription(
            language=LANGS[a.language], media_file=fh, transcription_mode=a.mode)
    t = dump(task)
    print(f"submitted: {json.dumps(t)[:200]}")
    task_id = t.get("task_id") if isinstance(t, dict) else None
    if not task_id:
        sys.exit(f"no task_id in response: {t}")

    run_id, status = None, None
    for i in range(120):
        s = dump(client.transcription.get_transcription_task_status(task_id=task_id))
        status = s.get("status") if isinstance(s, dict) else str(s)
        run_id = (s.get("run_id") if isinstance(s, dict) else None) or run_id
        print(f"  [{i * 3:>3}s] {status}", flush=True)
        if str(status).upper() in ("SUCCESS", "COMPLETED", "DONE"):
            break
        if str(status).upper() in ("FAILED", "ERROR"):
            sys.exit(f"transcription failed: {s}")
        time.sleep(3)

    res = dump(client.transcription.get_transcription_result(
        run_id, word_level_timestamps=True))
    ledger.record("camb", f"stt-{a.mode}", "stt", units=round(dur or 0, 1), unit="s",
                  note=f"{a.language}; credits not exposed on this endpoint")

    outdir = Path(a.out); outdir.mkdir(parents=True, exist_ok=True)
    f = outdir / f"camb_{a.mode}_{a.language}_{p.stem}.json"
    f.write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str))

    segs = res.get("transcript", []) if isinstance(res, dict) else []
    print(f"\n=== segments: {len(segs)}")
    speakers = sorted({str(s.get("speaker")) for s in segs if isinstance(s, dict)})
    for s in segs[:10]:
        print(f"  {float(s.get('start', 0)):7.3f} -> {float(s.get('end', 0)):7.3f} "
              f"[{s.get('speaker')}]  {s.get('text', '')}")
    if len(segs) > 10:
        print(f"  ... {len(segs) - 10} more")

    # A7 - speaker roles.
    # Rule: the speaker holding most of the speech time is the ON-SCREEN presenter;
    # everyone else is an off-screen announcer / VO. Off-screen voices are accepted:
    # they get dubbed, they just never drive lip-sync. Only a second SUBSTANTIAL
    # speaker (a real dialogue ad) is out of scope for V1.
    RUNNER_UP_LIMIT = 0.25

    talk = {}
    for seg in segs:
        if isinstance(seg, dict):
            sp = str(seg.get("speaker"))
            talk[sp] = talk.get(sp, 0.0) + (float(seg.get("end", 0)) - float(seg.get("start", 0)))
    total = sum(talk.values()) or 1.0
    ranked = sorted(talk.items(), key=lambda kv: -kv[1])

    print(f"\n=== speakers (A7): {len(ranked)}")
    for i, (sp, t) in enumerate(ranked):
        n = sum(1 for x in segs if str(x.get("speaker")) == sp)
        role = "ON-SCREEN presenter -> drives lip-sync" if i == 0 else \
               "off-screen VO -> dubbed, silent in the lip-sync track"
        print(f"    {sp:<12} {n:>2} seg  {t:6.2f}s  {100 * t / total:5.1f}%   {role}")

    runner_up = ranked[1][1] / total if len(ranked) > 1 else 0.0
    if runner_up > RUNNER_UP_LIMIT:
        print(f"    VERDICT: two substantial speakers ({runner_up:.0%}) - "
              "looks like a dialogue ad, out of scope for V1")
    else:
        print("    VERDICT: accepted - one on-screen speaker"
              + (f", {len(ranked) - 1} off-screen voice(s)" if len(ranked) > 1 else ""))

    onscreen = ranked[0][0] if ranked else None
    off = [s for s in segs if str(s.get("speaker")) != onscreen]
    if off:
        print(f"\n    lip-sync drive track must silence {len(off)} off-screen line(s):")
        for s in off:
            print(f"      {float(s['start']):7.3f} -> {float(s['end']):7.3f}  {s.get('text','')}")

    words = walk_words(res, [])
    print(f"\n=== words (A2): {len(words)}")
    if words:
        print(f"    granularity: {granularity([v for w in words for v in w[1:]])}")
        if dur:
            print(f"    last end   : {max(w[2] for w in words):.3f}s vs audio {dur:.3f}s")
        for w, s, e in words[:10]:
            print(f"      {s:7.3f} -> {e:7.3f}  {w}")
    else:
        print("    no word-level entries found - inspect the saved JSON")

    if segs:
        gaps = [(segs[i + 1]["start"] - segs[i]["end"]) for i in range(len(segs) - 1)]
        gaps = [g for g in gaps if g > 0]
        print(f"\n=== gaps between segments (A5): {len(gaps)}")
        if gaps:
            print("    " + ", ".join(f"{g:.3f}s" for g in gaps[:12]))
            print(f"    total gap {sum(gaps):.2f}s of {dur:.2f}s "
                  f"({100 * sum(gaps) / dur:.0f}% of the ad is pause)")

    print(f"\nsaved -> {f}")


if __name__ == "__main__":
    main()
