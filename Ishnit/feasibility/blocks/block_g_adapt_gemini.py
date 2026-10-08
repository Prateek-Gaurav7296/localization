#!/usr/bin/env python3
"""
Stage 5 adaptation probe — Gemini.

  python3 block_g_adapt_gemini.py --segments ../results/camb_slow_hi-IN_IT0AfU3277I_48k_mono.json
  python3 block_g_adapt_gemini.py --target te --model gemini-3.1-pro-preview
"""
import argparse, json, os, re, sys, time, urllib.request, urllib.error
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import adapt_common as A

ROOT = Path(__file__).resolve().parents[2]
API = "https://generativelanguage.googleapis.com/v1beta"


def load_env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                if v.strip():                      # ignore blank entries
                    os.environ.setdefault(k.strip(), v.strip())


def make_client(models, key, temperature):
    def complete_json(prompt):
        body = {"contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": temperature,
                                     "responseMimeType": "application/json"}}
        for model in models:
            delay = 5
            for _ in range(3):
                req = urllib.request.Request(
                    f"{API}/models/{model}:generateContent?key={key}",
                    data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=180) as r:
                        d = json.load(r)
                    parts = d["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts)
                    m = re.search(r"\{.*\}", text, re.S)
                    return json.loads(m.group(0)) if m else None
                except urllib.error.HTTPError as e:
                    if e.code in (429, 503):
                        time.sleep(delay); delay *= 2; continue
                    print(f"    [{model}] HTTP {e.code}: {e.read().decode()[:160]}")
                    break
                except (KeyError, IndexError, json.JSONDecodeError):
                    return None
            print(f"    [{model}] busy -> next model")
        return None
    return complete_json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", default=str(ROOT / "feasibility" / "results" /
                                              "camb_slow_hi-IN_IT0AfU3277I_48k_mono.json"))
    ap.add_argument("--target", default="kn", choices=list(A.SCRIPTS))
    ap.add_argument("--model", default="gemini-3.1-pro-preview,gemini-3.8-flash,gemini-3.5-flash")
    ap.add_argument("--candidates", type=int, default=5)
    ap.add_argument("--rate", type=float, default=7.0, help="aksharas/sec seed prior")
    ap.add_argument("--temperature", type=float, default=0.9)
    ap.add_argument("--context", default="30-second Indian TV commercial for Livpure Smart, "
                                         "a water purifier rented for a monthly fee. "
                                         "Presenter talks straight to camera, playful and direct.")
    ap.add_argument("--protected", default="Livpure,375,RO")
    ap.add_argument("--limit", type=int, default=0, help="only first N lines")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    load_env()
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        sys.exit("Set GEMINI_API_KEY in speech-engine/.env")

    models = [m.strip() for m in a.model.split(",") if m.strip()]
    client = make_client(models, key, a.temperature)
    protected = [t for t in a.protected.split(",") if t]

    segs = A.load_segments(a.segments)
    if a.limit:
        segs = segs[:a.limit]
    print(f"{len(segs)} lines -> {A.SCRIPTS[a.target][0]}   models: {', '.join(models)}")

    results = []
    for i, seg in enumerate(segs):
        print(f"  line {i + 1}/{len(segs)} ...", flush=True)
        results.append(A.adapt_line(client, i, seg, a.target, a.rate,
                                    a.candidates, a.context, A.DEFAULT_RULES, protected))

    out = Path(a.out) if a.out else (ROOT / "feasibility" / "results" /
                                     f"adapt_gemini_{a.target}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    A.report(results, a.target, a.rate, f"gemini({models[0]})", out)


if __name__ == "__main__":
    main()
