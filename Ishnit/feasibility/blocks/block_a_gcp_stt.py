#!/usr/bin/env python3
"""
Block A / Q1 — Google Cloud Speech-to-Text v2 probe.

The real word-timestamp test. Runs each (model x language) combination and
reports, per combination:
    - transcript
    - whether word offsets came back at all
    - the granularity those offsets are snapped to   <- decides Q1
    - last word end vs true audio duration

Auth: gcloud auth application-default login   (uses your gcloud access token)
Stdlib only.

  python3 block_a_gcp_stt.py fixtures/ads/X.wav
  python3 block_a_gcp_stt.py X.wav --models chirp_3,chirp_2,long --languages hi-IN,en-IN,hi-IN+en-IN
"""
import argparse, base64, json, re, subprocess, sys, urllib.request, urllib.error, wave
from pathlib import Path


def token():
    try:
        return subprocess.run(["gcloud", "auth", "print-access-token"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except subprocess.CalledProcessError as e:
        sys.exit(f"gcloud token failed: {e.stderr.strip()}\n"
                 "Run: gcloud auth application-default login")


def project():
    r = subprocess.run(["gcloud", "config", "get-value", "project"],
                       capture_output=True, text=True)
    p = r.stdout.strip()
    if not p or p == "(unset)":
        sys.exit("No project set. Run: gcloud config set project YOUR_PROJECT_ID")
    return p


def wav_duration(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def recognize(proj, region, model, langs, b64, tok):
    host = "speech.googleapis.com" if region == "global" else f"{region}-speech.googleapis.com"
    url = (f"https://{host}/v2/projects/{proj}/locations/{region}"
           f"/recognizers/_:recognize")
    body = {
        "config": {
            "model": model,
            "languageCodes": langs,
            "autoDecodingConfig": {},
            "features": {"enableWordTimeOffsets": True,
                         "enableWordConfidence": True,
                         "enableAutomaticPunctuation": True},
        },
        "content": b64,
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {tok}",
                                          "Content-Type": "application/json",
                                          "x-goog-user-project": proj})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()[:300]}"


def secs(v):
    """Google returns offsets as '1.100s' or {'seconds':1,'nanos':100000000}."""
    if isinstance(v, str):
        return float(v.rstrip("s") or 0)
    if isinstance(v, dict):
        return int(v.get("seconds", 0)) + int(v.get("nanos", 0)) / 1e9
    return 0.0


def granularity(vals):
    frac = [round(v % 1, 6) for v in vals]
    if not frac:
        return "no offsets returned"
    if all(f == 0 for f in frac):
        return "WHOLE SECONDS - unusable"
    if all(abs(f * 10 - round(f * 10)) < 1e-9 for f in frac):
        return "100ms steps - too coarse for +-50ms boundaries"
    if all(abs(f * 100 - round(f * 100)) < 1e-9 for f in frac):
        return "10ms steps - fine enough, measure real error vs ground truth"
    return "sub-10ms - fine enough, measure real error vs ground truth"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--project", default=None)
    ap.add_argument("--region", default="us-central1")
    ap.add_argument("--models", default="chirp_3,chirp_2,long,short")
    ap.add_argument("--languages", default="hi-IN,en-IN,hi-IN+en-IN")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "results"))
    a = ap.parse_args()

    p = Path(a.audio)
    dur = wav_duration(p)
    b64 = base64.b64encode(p.read_bytes()).decode()
    proj = a.project or project()
    tok = token()
    outdir = Path(a.out); outdir.mkdir(parents=True, exist_ok=True)

    print(f"{p.name}  {dur:.3f}s   project={proj}  region={a.region}\n")

    for model in [m.strip() for m in a.models.split(",") if m.strip()]:
        for lang in [l.strip() for l in a.languages.split(",") if l.strip()]:
            langs = lang.split("+")
            print(f"=== {model}  [{', '.join(langs)}] " + "=" * 24)
            res, err = recognize(proj, a.region, model, langs, b64, tok)
            if err:
                print(f"    {err}\n")
                continue

            (outdir / f"gcp_{model}_{lang.replace('+','-')}_{p.stem}.json").write_text(
                json.dumps(res, ensure_ascii=False, indent=2))

            words, text = [], []
            for r in res.get("results", []):
                alt = (r.get("alternatives") or [{}])[0]
                if alt.get("transcript"):
                    text.append(alt["transcript"])
                for w in alt.get("words", []):
                    words.append((w.get("word", ""),
                                  secs(w.get("startOffset", 0)),
                                  secs(w.get("endOffset", 0))))

            full = " ".join(t.strip() for t in text).strip()
            print(f"    transcript : {full[:220]}{'...' if len(full) > 220 else ''}")
            print(f"    words      : {len(words)}")
            bounds = [t for w in words for t in w[1:]]
            print(f"    granularity: {granularity(bounds)}")
            if words:
                print(f"    last end   : {max(w[2] for w in words):.3f}s "
                      f"vs audio {dur:.3f}s")
                for w, s, e in words[:8]:
                    print(f"      {s:7.3f} -> {e:7.3f}  {w}")
            print()

    print(f"saved -> {outdir}")


if __name__ == "__main__":
    main()
