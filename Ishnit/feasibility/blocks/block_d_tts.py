#!/usr/bin/env python3
"""
Block D — TTS duration control, Punjabi (Phase 1).

Four mechanical tests, no native speaker needed. Every number is measured from
the returned audio, never from what we asked for.

  duration   does Camb's output_configuration.duration actually work?
             (the doc says no such parameter exists - the SDK says otherwise)
  rate       is speaking_rate linear and predictable?
  variance   same text 5x - how much does duration move run to run?
  calibrate  aksharas/sec per voice -> the rate model the timing engine needs

  .venv/bin/python feasibility/blocks/block_d_tts.py --test duration
  .venv/bin/python feasibility/blocks/block_d_tts.py --test all --models camb:mars-pro,camb:mars-instruct
"""
from __future__ import annotations
import argparse, base64, io, json, os, re, statistics, sys, time, urllib.request, urllib.error, wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import adapt_common as A
import ledger

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "feasibility" / "results"
FIXTURES = ROOT / "feasibility" / "fixtures"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"

# Free-tier TTS quota is per-minute, so a 12-line run collapses into connection
# resets halfway through. Pacing calls is enough to get a clean run; for a batch
# product the wall-clock cost is irrelevant.
PACE_SECONDS = 0.0
_last_call = [0.0]

CAMB_LANG = {"pa": "pa-in", "hi": "hi-in", "kn": "kn-in", "te": "te-in",
             "ta": "ta-in", "ml": "ml-in", "mr": "mr-in", "bn": "bn-in",
             "gu": "gu-in", "as": "as-in", "en": "en-in"}


def load_env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                if v.strip():
                    os.environ.setdefault(k.strip(), v.strip())


# --------------------------------------------------------------- measuring ---

def wav_seconds(path: Path) -> float:
    """Count DECODED samples, never the header's frame count.

    Camb streams audio, so joining the chunks yields a WAV whose header carries
    a streaming placeholder for nframes - one probe reported 44,739 seconds for
    a 132KB file. Only the bytes actually present can be trusted.
    """
    with wave.open(str(path)) as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = w.readframes(w.getnframes())
    frames = len(data) // max(1, ch * sw)
    return frames / sr if sr else 0.0


def pcm_to_wav(pcm: bytes, path: Path, rate=24000, channels=1, width=2):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels); w.setsampwidth(width); w.setframerate(rate)
        w.writeframes(pcm)


def trim_silence(path: Path, thresh=0.01) -> float:
    """Duration with leading/trailing silence removed - TTS pads, and the pad
    is not speech. Measuring the pad would corrupt every rate number."""
    import array
    with wave.open(str(path)) as w:
        n, sr, sw = w.getnframes(), w.getframerate(), w.getsampwidth()
        if sw != 2 or n == 0:
            return n / sr if n else 0.0
        a = array.array("h", w.readframes(n))
    peak = max(1, max(abs(x) for x in a))
    lim = peak * thresh
    first = next((i for i, x in enumerate(a) if abs(x) > lim), 0)
    last = next((i for i in range(len(a) - 1, -1, -1) if abs(a[i]) > lim), len(a) - 1)
    return max(0.0, (last - first) / sr)


# --------------------------------------------------------------- providers ---

class CambTTS:
    def __init__(self, model, voice_id, lang):
        from camb.client import CambAI
        self.client = CambAI(api_key=os.environ["CAMB_API_KEY"])
        self.model, self.voice_id, self.lang = model, voice_id, CAMB_LANG[lang]
        self.name = f"camb:{model}"
        self.supports_duration = True
        self.supports_rate = True

    CAMB_TTS_URL = "https://client.camb.ai/apis/tts-stream"

    def say_raw(self, body, out: Path):
        """Raw HTTP so the X-Credits-Required header can be read - the SDK's
        with_raw_response does not cover text_to_speech."""
        req = urllib.request.Request(
            self.CAMB_TTS_URL, data=json.dumps(body).encode(),
            headers={"x-api-key": os.environ["CAMB_API_KEY"],
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            data = r.read()
            credits = r.headers.get("X-Credits-Required")
        out.write_bytes(data)
        return len(data), (float(credits) if credits else None)

    def say(self, text, out: Path, rate=None, duration=None, instructions=None):
        oc = {"format": "wav", "sample_rate": 48000}
        if duration is not None:
            oc["duration"] = float(duration)
        kw = dict(text=text, language=self.lang, voice_id=self.voice_id,
                  speech_model=self.model, output_configuration=oc)
        if rate is not None:
            kw["voice_settings"] = {"speaking_rate": float(rate)}
        if instructions and self.model == "mars-instruct":
            kw["user_instructions"] = instructions
        body = {"text": text, "language": self.lang, "voice_id": self.voice_id,
                "speech_model": self.model, "output_configuration": oc}
        if rate is not None:
            body["voice_settings"] = {"speaking_rate": float(rate)}
        if instructions and self.model == "mars-instruct":
            body["user_instructions"] = instructions
        try:
            n, credits = self.say_raw(body, out)
            ledger.record("camb", self.model, "tts", units=len(text), unit="c",
                          credits=credits, note=f"{n}B")
            return out
        except Exception as e:                       # fall back to the SDK
            chunks = self.client.text_to_speech.tts(**kw)
            data = b"".join(chunks) if not isinstance(chunks, (bytes, bytearray)) else chunks
            out.write_bytes(data)
            ledger.record("camb", self.model, "tts", units=len(text), unit="c",
                          note=f"sdk fallback ({type(e).__name__}); credits unknown")
            return out


class GeminiTTS:
    def __init__(self, model, voice="Kore"):
        self.key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.model, self.voice = model, voice
        self.name = f"gemini:{model}"
        self.supports_duration = False      # no duration parameter exists
        self.supports_rate = False          # no numeric rate parameter either

    def say(self, text, out: Path, rate=None, duration=None, instructions=None):
        wait = PACE_SECONDS - (time.time() - _last_call[0])
        if PACE_SECONDS and wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        prompt = f"{instructions}\n\n{text}" if instructions else text
        body = {"contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["AUDIO"],
                                     "speechConfig": {"voiceConfig": {
                                         "prebuiltVoiceConfig": {"voiceName": self.voice}}}}}
        delay = 10
        for _ in range(6):
            req = urllib.request.Request(
                f"{GEMINI_API}/models/{self.model}:generateContent?key={self.key}",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    d = json.load(r)
                break
            except urllib.error.HTTPError as e:
                body = e.read().decode() if e.fp else ""
                # A quota exhaustion will not clear in seconds - retrying it just
                # burns the clock. Only transient busy signals are worth a backoff.
                if "RESOURCE_EXHAUSTED" in body or "exceeded your current quota" in body:
                    raise RuntimeError(f"quota exhausted for {self.model}: "
                                       f"{body[:140]}")
                if e.code in (429, 503):
                    print(f"      [gemini] {e.code}, waiting {delay}s", flush=True)
                    time.sleep(delay); delay = min(delay * 2, 120); continue
                raise RuntimeError(f"HTTP {e.code}: {body[:200]}")
            except OSError as e:                      # connection reset by peer
                print(f"      [gemini] {e}, waiting {delay}s", flush=True)
                time.sleep(delay); delay = min(delay * 2, 120); continue
        else:
            raise RuntimeError("busy after retries")
        u = d.get("usageMetadata") or {}
        ledger.record("gemini", self.model, "tts", units=len(text), unit="c",
                      tokens_in=u.get("promptTokenCount"),
                      tokens_out=u.get("candidatesTokenCount") or u.get("totalTokenCount"))
        part = d["candidates"][0]["content"]["parts"][0]
        inline = part.get("inlineData") or part.get("inline_data")
        if not inline:
            raise RuntimeError(f"no audio in response: {json.dumps(d)[:200]}")
        payload = base64.b64decode(inline["data"])
        if payload[:4] == b"RIFF":
            # Already a complete WAV. Writing it inside another header played the
            # inner header as audio AND appended Google's trailing C2PA/JUMBF
            # provenance box (~6KB = 0.125s of hiss) as if it were samples.
            # Written as-is, the wave reader stops at the data chunk and ignores it.
            out.write_bytes(payload)
        else:
            sr = 24000
            m = re.search(r"rate=(\d+)", inline.get("mimeType", ""))
            if m:
                sr = int(m.group(1))
            pcm_to_wav(payload, out, rate=sr)
        return out


def build(spec, voice_id, lang):
    kind, _, model = spec.partition(":")
    if kind == "camb":
        return CambTTS(model, voice_id, lang)
    if kind == "gemini":
        return GeminiTTS(model)
    sys.exit(f"unknown provider: {spec}")


# ----------------------------------------------------------------- corpus ---

CORPUS_PROMPT = """Write {n} short lines of {language} advertising copy for a TV commercial
about a water purifier that is rented monthly.

Spread them across lengths: some very short (2-4 orthographic syllables), some
medium, some long (30-40). Natural spoken ad register, not literary. Keep
English words that Indian ads normally use (free, offer, smart) in Latin script.

Return JSON only: {{"lines":["...","..."]}}"""


def get_corpus(lang_code, n, key, path: Path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["lines"]
    language = A.SCRIPTS[lang_code][0]
    body = {"contents": [{"parts": [{"text": CORPUS_PROMPT.format(n=n, language=language)}]}],
            "generationConfig": {"temperature": 1.0, "responseMimeType": "application/json"}}
    for model in ("gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.1-flash-lite"):
        try:
            req = urllib.request.Request(
                f"{GEMINI_API}/models/{model}:generateContent?key={key}",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.load(r)
            txt = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
            lines = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))["lines"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"lines": lines, "model": model},
                                       ensure_ascii=False, indent=2))
            print(f"corpus: {len(lines)} lines generated by {model} -> {path.name}")
            return lines
        except Exception as e:
            print(f"  corpus via {model} failed: {str(e)[:120]}")
    sys.exit("could not build a corpus; pass --corpus with a json file of lines")


# ------------------------------------------------------------------ tests ---

def row(csv, **kw):
    import csv as _csv
    new = not csv.exists()
    with csv.open("a", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=list(kw))
        if new:
            w.writeheader()
        w.writerow(kw)


def test_duration(tts, line, outdir, csv, targets=(1.0, 1.5, 2.0, 3.0, 4.0)):
    print(f"\n=== D-duration  {tts.name}")
    if not tts.supports_duration:
        print("    provider exposes no duration parameter - skipped")
        return
    base = tts.say(line, outdir / f"{tts.name.replace(':','_')}_dur_base.wav")
    b = wav_seconds(base)
    print(f"    no duration set : {b:6.3f}s   ({A.count_aksharas(line)} aksharas)")
    for t in targets:
        p = outdir / f"{tts.name.replace(':','_')}_dur_{t}.wav"
        try:
            tts.say(line, p, duration=t)
        except Exception as e:
            print(f"    duration={t:.1f}s -> ERROR {str(e)[:110]}")
            continue
        got, speech = wav_seconds(p), trim_silence(p)
        err = 100 * (got - t) / t
        print(f"    duration={t:4.1f}s -> file {got:6.3f}s  speech {speech:6.3f}s  "
              f"{err:+6.1f}%  {'HONOURED' if abs(err) < 5 else 'ignored/clamped'}")
        row(csv, test="duration", provider=tts.name, target=t, file_s=round(got, 3),
            speech_s=round(speech, 3), err_pct=round(err, 1), text=line)


def test_rate(tts, line, outdir, csv, rates=(0.8, 0.9, 1.0, 1.1, 1.2)):
    print(f"\n=== D2-rate  {tts.name}")
    if not tts.supports_rate:
        print("    provider exposes no speaking_rate parameter - skipped")
        return
    base = None
    for r in rates:
        p = outdir / f"{tts.name.replace(':','_')}_rate_{r}.wav"
        try:
            tts.say(line, p, rate=r)
        except Exception as e:
            print(f"    rate={r} -> ERROR {str(e)[:110]}"); continue
        d = trim_silence(p)
        if r == 1.0:
            base = d
        print(f"    rate={r:.1f} -> {d:6.3f}s" +
              (f"   {100*(d/base-1):+6.1f}% vs 1.0  (ideal {100*(1/r-1):+6.1f}%)"
               if base else ""))
        row(csv, test="rate", provider=tts.name, target=r, file_s="", speech_s=round(d, 3),
            err_pct="", text=line)


def test_variance(tts, line, outdir, csv, n=5):
    print(f"\n=== D1-variance  {tts.name}  ({n} identical renders)")
    ds = []
    for i in range(n):
        p = outdir / f"{tts.name.replace(':','_')}_var_{i}.wav"
        try:
            tts.say(line, p)
        except Exception as e:
            print(f"    run {i} ERROR {str(e)[:110]}"); continue
        d = trim_silence(p); ds.append(d)
        print(f"    run {i}: {d:6.3f}s")
        row(csv, test="variance", provider=tts.name, target=i, file_s="",
            speech_s=round(d, 3), err_pct="", text=line)
    if len(ds) > 1:
        m, sd = statistics.mean(ds), statistics.pstdev(ds)
        spread = 100 * (max(ds) - min(ds)) / m
        print(f"    mean {m:.3f}s  sd {sd:.3f}s  spread {spread:.1f}% "
              f"-> {'predict-then-render OK' if spread < 2 else 'MUST measure every line'}")


def test_calibrate(tts, lines, outdir, csv, lang):
    print(f"\n=== D4-calibrate  {tts.name}  ({len(lines)} lines)")
    pts = []
    for i, line in enumerate(lines):
        ak = A.count_aksharas(line)
        if ak < 2:
            continue
        p = outdir / f"{tts.name.replace(':','_')}_cal_{i}.wav"
        try:
            tts.say(line, p)
        except Exception as e:
            print(f"    [{i}] ERROR {str(e)[:100]}"); continue
        d = trim_silence(p)
        pts.append((ak, d))
        print(f"    {ak:3} ak -> {d:6.3f}s   {ak/d:5.2f} ak/s   {line[:46]}")
        row(csv, test="calibrate", provider=tts.name, target=ak, file_s="",
            speech_s=round(d, 3), err_pct="", text=line)
    if len(pts) >= 4:
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        denom = sum((x - mx) ** 2 for x in xs) or 1e-9
        b = sum((x - mx) * (y - my) for x, y in pts) / denom
        a = my - b * mx
        preds = [a + b * x for x in xs]
        mae = 100 * statistics.mean(abs(p - y) / y for p, y in zip(preds, ys))
        print(f"\n    affine fit : duration = {a:.3f}s + aksharas/{1/b:.2f}")
        print(f"    => rate {1/b:.2f} aksharas/sec, intercept {a:.3f}s")
        print(f"    ratio-only : {statistics.mean(x/y for x, y in pts):.2f} ak/s")
        print(f"    estimator MAE on its own fit: {mae:.1f}%  "
              f"(target <=5% per DoD T3)")


PROBE_TEXT = {"pa": "ਸਾਫ਼ ਪਾਣੀ।", "hi": "साफ़ पानी।", "kn": "ಶುದ್ಧ ನೀರು.",
              "te": "స్వచ్ఛమైన నీరు.", "mr": "स्वच्छ पाणी.", "as": "বিশুদ্ধ পানী।",
              "bn": "বিশুদ্ধ জল।", "gu": "સ્વચ્છ પાણી.", "ta": "சுத்தமான நீர்.",
              "ml": "ശുദ്ധമായ വെള്ളം.", "en": "Clean water."}


def capability_probe(tts, lang, outdir):
    """A model that does not support a locale returns HTTP 200 with ZERO bytes -
    no exception, no error body. Catch it here instead of three tests later."""
    p = outdir / f"probe_{tts.name.replace(':','_')}.wav"
    t0 = time.time()
    try:
        tts.say(PROBE_TEXT.get(lang, "test"), p)
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:60]}", time.time() - t0
    n = p.stat().st_size if p.exists() else 0
    if n == 0:
        return False, "EMPTY 200 - locale not supported by this model", time.time() - t0
    try:
        d = wav_seconds(p)
    except Exception:
        return False, f"{n} bytes but unreadable as wav", time.time() - t0
    if d < 0.15:
        return False, f"only {d:.2f}s of audio", time.time() - t0
    return True, f"{n} bytes, {d:.2f}s", time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="pa", choices=list(CAMB_LANG))
    ap.add_argument("--models", default="camb:mars-pro,camb:mars-instruct,gemini:gemini-3.8-flash-tts")
    ap.add_argument("--voice-id", type=int, default=None,
                    help="Camb voice id (default: first Hindi-native voice)")
    ap.add_argument("--test", default="all",
                    choices=["all", "duration", "rate", "variance", "calibrate"])
    ap.add_argument("--corpus", default=None, help="json file with {\"lines\":[...]}")
    ap.add_argument("--corpus-n", type=int, default=20)
    ap.add_argument("--line", default=None, help="single line for duration/rate/variance")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    load_env()
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    outdir = Path(a.out) if a.out else RESULTS / f"block_d_{a.lang}_{time.strftime('%Y%m%d-%H%M')}"
    outdir.mkdir(parents=True, exist_ok=True)
    csv = outdir / "results.csv"

    voice_id = a.voice_id
    if voice_id is None and any(m.startswith("camb") for m in a.models.split(",")):
        from camb.client import CambAI
        raw = CambAI(api_key=os.environ["CAMB_API_KEY"]).voice_cloning.list_voices()
        vs = [v if isinstance(v, dict) else v.model_dump() for v in raw]
        pick = [v for v in vs if v.get("language") == 81] or vs        # 81 = hi-IN
        voice_id = pick[0]["id"]
        print(f"voice: id={voice_id} '{pick[0].get('voice_name')}' "
              f"(lang id {pick[0].get('language')}) - no Punjabi-native voices exist, "
              f"so this is a cross-lingual render: watch for accent contamination")

    corpus_path = Path(a.corpus) if a.corpus else FIXTURES / f"corpus_{a.lang}.json"
    lines = get_corpus(a.lang, a.corpus_n, key, corpus_path)
    single = a.line or max(lines, key=lambda l: abs(A.count_aksharas(l) - 14))
    print(f"\nsingle-line tests use: {single}  ({A.count_aksharas(single)} aksharas)")

    print(f"\n=== capability probe ({a.lang})")
    usable = []
    for spec in [m.strip() for m in a.models.split(",") if m.strip()]:
        try:
            tts = build(spec, voice_id, a.lang)
        except Exception as e:
            print(f"    {spec:<34} BUILD FAILED {type(e).__name__}: {str(e)[:90]}")
            continue
        ok, why, secs = capability_probe(tts, a.lang, outdir)
        print(f"    {spec:<34} {'OK  ' if ok else 'SKIP'}  {secs:5.1f}s  {why}")
        row(csv, test="capability", provider=tts.name, target=a.lang,
            file_s=round(secs, 1), speech_s="", err_pct="", text=why)
        if ok:
            usable.append(tts)
    if not usable:
        sys.exit("no usable models for this language")

    for tts in usable:
        try:
            if a.test in ("all", "duration"):
                test_duration(tts, single, outdir, csv)
            if a.test in ("all", "rate"):
                test_rate(tts, single, outdir, csv)
            if a.test in ("all", "variance"):
                test_variance(tts, single, outdir, csv)
            if a.test in ("all", "calibrate"):
                test_calibrate(tts, lines, outdir, csv, a.lang)
        except Exception as e:
            body = getattr(e, "body", None)
            print(f"!! {tts.name} failed: {type(e).__name__}: {str(e)[:200]}"
                  + (f"\n   body: {str(body)[:300]}" if body else "")
                  + (f"\n   status: {getattr(e, 'status_code', '')}"))

    print(f"\nsaved -> {outdir}")


if __name__ == "__main__":
    main()
