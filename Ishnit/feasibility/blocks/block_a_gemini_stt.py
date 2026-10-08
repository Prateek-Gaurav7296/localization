#!/usr/bin/env python3
"""
Block A / Q1 — Gemini STT probe.

Answers, for one audio fixture:
  A1  does it transcribe accurately?
  A4  does it TRANSCRIBE code-switched speech, or silently TRANSLATE it?
  ---  what timestamp granularity does it actually return?

Stdlib only. Needs GEMINI_API_KEY (https://aistudio.google.com -> Get API key).

  python3 block_a_gemini_stt.py --list-models
  python3 block_a_gemini_stt.py path/to.wav --model gemini-2.5-pro
"""
import argparse, base64, json, os, re, sys, time, urllib.request, urllib.error, wave
from pathlib import Path

API = "https://generativelanguage.googleapis.com/v1beta"
ROOT = Path(__file__).resolve().parents[2]


def load_env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                if v.strip():
                    os.environ.setdefault(k.strip(), v.strip())


class ModelUnavailable(Exception):
    pass


def call(path, payload=None, key="", retries=4):
    """Retries 429/503 with backoff; raises ModelUnavailable if the model is gone."""
    url = f"{API}/{path}{'?' if '?' not in path else '&'}key={key}"
    data = json.dumps(payload).encode() if payload is not None else None
    delay = 5
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"},
                                     method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code in (429, 503) and attempt < retries - 1:
                print(f"    [{e.code}] busy, retrying in {delay}s "
                      f"({attempt + 1}/{retries - 1})", flush=True)
                time.sleep(delay)
                delay *= 2
                continue
            if e.code in (404, 429, 503):
                raise ModelUnavailable(f"HTTP {e.code}: {body[:300]}")
            sys.exit(f"HTTP {e.code}: {body[:600]}")
    raise ModelUnavailable("exhausted retries")


def wav_info(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate(), w.getframerate(), w.getnchannels()


def ask(model, key, prompt, b64, mime):
    body = {"contents": [{"parts": [{"text": prompt},
                                    {"inline_data": {"mime_type": mime, "data": b64}}]}],
            "generationConfig": {"temperature": 0}}
    r = call(f"models/{model}:generateContent", body, key)
    text = ""
    try:
        parts = r["candidates"][0]["content"]["parts"]
        chunks = []
        for part in parts:
            if "text" in part:
                chunks.append(part["text"])
            elif "audioTranscription" in part:            # ASR-specialised models
                chunks.append(part["audioTranscription"].get("text", ""))
        text = "".join(chunks)
    except (KeyError, IndexError):
        pass
    if not text:
        cand = (r.get("candidates") or [{}])[0]
        print(f"    EMPTY RESPONSE  finishReason={cand.get('finishReason')} "
              f"keys={list(cand.keys())}")
        print(f"    raw: {json.dumps(r)[:500]}")
    return text, r


def as_json(text):
    m = re.search(r"\{.*\}|\[.*\]", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def granularity(values):
    """Smallest non-zero decimal step present -> reveals 1s / 100ms / 10ms snapping."""
    frac = [round(v % 1, 3) for v in values]
    if all(f == 0 for f in frac):
        return "WHOLE SECONDS - unusable for word timing"
    if all(abs(f * 10 - round(f * 10)) < 1e-6 for f in frac):
        return "100ms steps - too coarse for +-50ms boundaries"
    return "finer than 100ms - measurable against ground truth"


PROMPTS = {
    "verbatim": (
        "Transcribe this audio VERBATIM in the language actually spoken. "
        "Do NOT translate. Keep English words in Latin script and the regional "
        "language in its native script, exactly as spoken. "
        "Return JSON only: {\"language\":\"<bcp47>\",\"mixed_languages\":[...],"
        "\"text\":\"<full transcript>\"}"
    ),
    "segments": (
        "Transcribe this audio and split it into speech segments at natural pauses. "
        "Do NOT translate; keep the spoken language. Timestamps in seconds from the "
        "start, as precise as you can. Return JSON only: "
        "{\"segments\":[{\"start\":0.0,\"end\":0.0,\"text\":\"\"}]}"
    ),
    "words": (
        "Transcribe this audio with WORD-LEVEL timings. Do NOT translate. "
        "Timestamps in seconds from the start, as precise as you can. Return JSON only: "
        "{\"words\":[{\"w\":\"\",\"s\":0.0,\"e\":0.0}]}"
    ),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", nargs="?")
    ap.add_argument("--model", default="gemini-3.5-transcribe,gemini-3.8-flash,gemini-3.1-pro-preview")
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "results"))
    a = ap.parse_args()

    load_env()
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        sys.exit("Set GEMINI_API_KEY (get one at https://aistudio.google.com)")

    if a.list_models:
        for m in call("models?pageSize=200", key=key).get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                print(f"{m['name'].replace('models/',''):<44} {m.get('displayName','')}")
        return

    if not a.audio:
        sys.exit("give an audio path, or use --list-models")

    models = [m.strip() for m in a.model.split(",") if m.strip()]

    p = Path(a.audio)
    dur, sr, ch = wav_info(p) if p.suffix == ".wav" else (None, None, None)
    mime = {".wav": "audio/wav", ".mp3": "audio/mp3", ".m4a": "audio/mp4"}[p.suffix]
    b64 = base64.b64encode(p.read_bytes()).decode()
    print(f"{p.name}  {dur:.3f}s  {sr}Hz  {ch}ch")

    model = None
    for m in models:
        try:
            call(f"models/{m}", key=key)
            model = m
            break
        except ModelUnavailable as e:
            print(f"  skipping {m}: {e}")
    if not model:
        sys.exit("none of the requested models are available")
    print(f"using -> {model}\n")

    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    saved = {}

    for name, prompt in PROMPTS.items():
        print(f"=== {name} " + "=" * 40)
        try:
            text, usage = ask(model, key, prompt, b64, mime)
        except ModelUnavailable as e:
            print(f"  {e}\n"); continue
        parsed = as_json(text)
        saved[name] = parsed if parsed is not None else text
        (outdir / f"gemini_{model}_{p.stem}_{name}_raw.json").write_text(
            json.dumps(usage, ensure_ascii=False, indent=2))
        (outdir / f"gemini_{model}_{p.stem}_{name}.json").write_text(
            json.dumps(saved[name], ensure_ascii=False, indent=2))

        if parsed is None:
            print(text[:800]); continue

        if name == "verbatim":
            print(f"language   : {parsed.get('language')}   mixed: {parsed.get('mixed_languages')}")
            print(f"transcript : {parsed.get('text','')}")
        else:
            items = parsed.get("segments") or parsed.get("words") or []
            starts = [float(i.get("start", i.get("s", 0))) for i in items]
            ends = [float(i.get("end", i.get("e", 0))) for i in items]
            print(f"count      : {len(items)}")
            print(f"granularity: {granularity(starts + ends)}")
            if ends and dur:
                print(f"last end   : {max(ends):.3f}s vs audio {dur:.3f}s "
                      f"(delta {max(ends)-dur:+.3f}s)")
            for i in items[:6]:
                s = i.get("start", i.get("s")); e = i.get("end", i.get("e"))
                print(f"  {float(s):7.3f} -> {float(e):7.3f}  {i.get('text', i.get('w',''))}")
            if len(items) > 6:
                print(f"  ... {len(items)-6} more")
        print()

    print(f"saved -> {outdir}")


if __name__ == "__main__":
    main()
