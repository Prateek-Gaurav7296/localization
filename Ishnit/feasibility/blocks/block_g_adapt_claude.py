#!/usr/bin/env python3
"""
Stage 5 adaptation probe — Claude (Anthropic Messages API).

  python3 block_g_adapt_claude.py --segments ../results/camb_slow_hi-IN_IT0AfU3277I_48k_mono.json
  python3 block_g_adapt_claude.py --target kn --model claude-opus-5

Cross-family verification (what your doc requires - the back-translation must
come from a different model family than the generator):

  python3 block_g_adapt_claude.py --gloss-with gemini
"""
import argparse, json, os, re, sys, time, urllib.request, urllib.error
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import adapt_common as A
from block_g_adapt_gemini import make_client as gemini_client, load_env

ROOT = Path(__file__).resolve().parents[2]
API = "https://api.anthropic.com/v1/messages"


def make_client(model, key, temperature, max_tokens=2000):
    def complete_json(prompt):
        body = {"model": model, "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}]}
        delay = 5
        for _ in range(4):
            req = urllib.request.Request(
                API, data=json.dumps(body).encode(),
                headers={"x-api-key": key,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    d = json.load(r)
                text = "".join(b.get("text", "") for b in d.get("content", []))
                m = re.search(r"\{.*\}", text, re.S)
                return json.loads(m.group(0)) if m else None
            except urllib.error.HTTPError as e:
                msg = e.read().decode()[:200]
                if e.code in (429, 529, 503):
                    print(f"    [{model}] {e.code} busy, retry in {delay}s")
                    time.sleep(delay); delay *= 2; continue
                print(f"    [{model}] HTTP {e.code}: {msg}")
                return None
            except json.JSONDecodeError:
                return None
        return None
    return complete_json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", default=str(ROOT / "feasibility" / "results" /
                                              "camb_slow_hi-IN_IT0AfU3277I_48k_mono.json"))
    ap.add_argument("--target", default="kn", choices=list(A.SCRIPTS))
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--candidates", type=int, default=5)
    ap.add_argument("--rate", type=float, default=7.0, help="aksharas/sec seed prior")
    ap.add_argument("--temperature", type=float, default=0.9)
    ap.add_argument("--gloss-with", default=None, choices=["gemini"],
                    help="use a different model family for back-translation")
    ap.add_argument("--context", default="30-second Indian TV commercial for Livpure Smart, "
                                         "a water purifier rented for a monthly fee. "
                                         "Presenter talks straight to camera, playful and direct.")
    ap.add_argument("--protected", default="Livpure,375,RO")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    load_env()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("Set ANTHROPIC_API_KEY in speech-engine/.env "
                 "(https://console.anthropic.com -> API keys)")

    client = make_client(a.model, key, a.temperature)

    gloss = None
    if a.gloss_with == "gemini":
        gkey = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not gkey:
            sys.exit("--gloss-with gemini needs GEMINI_API_KEY")
        gloss = gemini_client(["gemini-3.8-flash", "gemini-3.5-flash"], gkey, 0.0)
        print("back-translation: gemini (different family from the generator)")

    protected = [t for t in a.protected.split(",") if t]
    segs = A.load_segments(a.segments)
    if a.limit:
        segs = segs[:a.limit]
    print(f"{len(segs)} lines -> {A.SCRIPTS[a.target][0]}   model: {a.model}")

    results = []
    for i, seg in enumerate(segs):
        print(f"  line {i + 1}/{len(segs)} ...", flush=True)
        results.append(A.adapt_line(client, i, seg, a.target, a.rate, a.candidates,
                                    a.context, A.DEFAULT_RULES, protected,
                                    gloss_with=gloss))

    out = Path(a.out) if a.out else (ROOT / "feasibility" / "results" /
                                     f"adapt_claude_{a.target}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    A.report(results, a.target, a.rate, f"claude({a.model})", out)


if __name__ == "__main__":
    main()
