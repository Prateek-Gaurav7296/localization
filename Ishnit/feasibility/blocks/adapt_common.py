#!/usr/bin/env python3
"""
Stage 5 (script adaptation) probe — shared logic.

Provider-agnostic: block_g_adapt_gemini.py and block_g_adapt_claude.py each
supply a `complete_json(prompt) -> dict` callable and reuse everything here.

What it measures WITHOUT a native speaker:
  * length control   - do candidates actually vary in length when asked to?
  * fit              - akshara count / calibrated rate vs the line's real budget
  * script purity    - Devanagari leaking into Kannada output, etc.
  * protected terms  - brand name / price / CTA survived verbatim
  * JSON reliability - how often the model returns parseable output
Register, naturalness and cultural fit still need the language owner.
"""
from __future__ import annotations
import json, re, unicodedata
from dataclasses import dataclass, asdict, field

# ---------------------------------------------------------------- scripts ---

SCRIPTS = {           # target -> (name, unicode range, bcp47)
    "kn": ("Kannada",    (0x0C80, 0x0CFF), "kn-IN"),
    "te": ("Telugu",     (0x0C00, 0x0C7F), "te-IN"),
    "ta": ("Tamil",      (0x0B80, 0x0BFF), "ta-IN"),
    "ml": ("Malayalam",  (0x0D00, 0x0D7F), "ml-IN"),
    "mr": ("Marathi",    (0x0900, 0x097F), "mr-IN"),
    "hi": ("Hindi",      (0x0900, 0x097F), "hi-IN"),
    "bn": ("Bengali",    (0x0980, 0x09FF), "bn-IN"),
    "gu": ("Gujarati",   (0x0A80, 0x0AFF), "gu-IN"),
    "pa": ("Punjabi",    (0x0A00, 0x0A7F), "pa-IN"),
    "or": ("Odia",       (0x0B00, 0x0B7F), "or-IN"),
    "as": ("Assamese",   (0x0980, 0x09FF), "as-IN"),
}

INDIC_BLOCKS = [r for _, r, _ in SCRIPTS.values()]


def _is_indic(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in INDIC_BLOCKS)


def _is_virama(ch: str) -> bool:
    try:
        return "VIRAMA" in unicodedata.name(ch) or "SIGN HALANT" in unicodedata.name(ch)
    except ValueError:
        return False


def count_aksharas(text: str) -> int:
    """
    Orthographic syllables.

    Indic scripts are abugidas: the unit is the akshara - a consonant (or
    consonant cluster joined by virama) plus its vowel sign. Vowel signs,
    anusvara, visarga and nukta are combining marks and never count on their
    own. Latin words are counted by vowel groups, which is rough but only
    applies to the English words inside the copy.

    Approximation, not phonology: it does not model schwa deletion, so Hindi
    counts run slightly high. Calibrate against measured TTS output (D4) and
    this becomes the seed, not the answer.
    """
    n, prev_virama = 0, False
    latin = []
    for ch in text:
        if _is_indic(ch):
            cat = unicodedata.category(ch)
            if _is_virama(ch):
                prev_virama = True
                continue
            if cat in ("Mn", "Mc"):          # matra / anusvara / visarga / nukta
                continue
            if cat == "Lo":                  # consonant or independent vowel
                if not prev_virama:
                    n += 1
                prev_virama = False
        else:
            prev_virama = False
            latin.append(ch)
    for word in re.findall(r"[A-Za-z]+", "".join(latin)):
        groups = re.findall(r"[aeiouyAEIOUY]+", word)
        n += max(1, len(groups))
    for num in re.findall(r"\d+", text):     # digits get spoken; count generously
        n += max(1, len(num))
    return n


# Danda and double danda live in the Devanagari block but are shared punctuation
# across Indic scripts - Gurmukhi, Bengali and Odia all end sentences with them.
SHARED_PUNCT = {0x0964, 0x0965, 0x0970}


def script_purity(text: str, target: str) -> list[str]:
    """Any Indic character outside the target's own block is a defect."""
    lo, hi = SCRIPTS[target][1]
    bad = set()
    for ch in text:
        if ord(ch) in SHARED_PUNCT:
            continue
        if _is_indic(ch) and not (lo <= ord(ch) <= hi):
            try:
                bad.add(unicodedata.name(ch).split()[0])
            except ValueError:
                bad.add(hex(ord(ch)))
    return sorted(bad)


# ---------------------------------------------------------------- fitting ---

@dataclass
class RateModel:
    """duration = intercept + aksharas / rate.

    The intercept matters: short lines carry fixed onset/offset overhead, so a
    pure ratio underestimates them - and most ad lines are 1-2.5s.
    Defaults are the published 6-8 syl/sec prior for Indian languages; replace
    with measured values from Block D4 per (voice x speaking_rate).
    """
    rate: float = 7.0
    intercept: float = 0.18

    def seconds(self, aksharas: int) -> float:
        return self.intercept + aksharas / self.rate

    def aksharas_for(self, seconds: float) -> int:
        return max(1, round((seconds - self.intercept) * self.rate))


def fit_class(est_s: float, budget_s: float) -> tuple[str, float]:
    d = (est_s - budget_s) / budget_s
    if abs(d) <= 0.03:
        return "exact", d
    if d > 0:
        return ("tight", d) if d <= 0.08 else ("OVER", d)
    return ("tight", d) if d >= -0.05 else ("short", d)


# ----------------------------------------------------------------- prompts ---

INTENT_PROMPT = """You are preparing an ad line for localisation.

Write a LANGUAGE-NEUTRAL note describing what this line DOES. The note will be
the only input to a copywriter working in another language - the original words
will NOT be shown to them.

Ad context: {context}
Source line: {source}

Return JSON only:
{{"function":"hook|benefit|objection|cta|legal|endline|filler",
  "beat":"<the emotional beat in 3-6 words>",
  "note":"<what this line communicates, 1 sentence, no source wording>",
  "entities":["<prices, brand names, numbers that MUST appear>"],
  "tone":"<delivery in 2-3 words>"}}"""

CANDIDATE_PROMPT = """You are an advertising copywriter writing in {language}.

You are NOT translating. You have never seen the original line. Write fresh {language}
ad copy that performs the job described below, in the time available.

JOB
  function : {function}
  beat     : {beat}
  meaning  : {note}
  tone     : {tone}
  must appear verbatim : {entities}

TIME BUDGET
  This line occupies {budget:.2f} seconds on screen and cannot be longer.
  That is roughly {target_aksharas} aksharas (orthographic syllables) in {language}.

RULES
{rules}

Write {n} DIFFERENT versions at different lengths: aim for about
{aims} aksharas respectively. Vary the wording, not just the padding.
Write in {script} script. Keep any allowed English words in Latin script.

Return JSON only:
{{"candidates":[{{"text":"<{language} copy>","aim":<target aksharas>}}]}}"""

BACKTRANSLATE_PROMPT = """Gloss this {language} line into English, LITERALLY.

Do not improve it, do not fix errors, do not make it idiomatic. If it is clumsy
or wrong, the English must be clumsy or wrong in the same way. This gloss is
used to detect meaning drift, so repairing it defeats the purpose.

Line: {text}

Return JSON only: {{"gloss":"<literal English>"}}"""

DEFAULT_RULES = """- Write in the SPOKEN advertising register, never the literary or news form.
- Use the polite-but-warm address form normal in TV commercials.
- Keep naturally-used English words in English (offer, EMI, free, smart); do not
  invent new English words that the language would not normally borrow."""


# -------------------------------------------------------------- the probe ---

@dataclass
class LineResult:
    index: int
    speaker: str
    start: float
    budget_s: float
    source: str
    intent: dict = field(default_factory=dict)
    candidates: list = field(default_factory=list)
    chosen: dict | None = None
    gloss: str = ""
    errors: list = field(default_factory=list)


def adapt_line(complete_json, idx, seg, target, rate, n_cand, context,
               rules, protected, gloss_with=None) -> LineResult:
    lang, script = SCRIPTS[target][0], SCRIPTS[target][0]
    budget = float(seg["end"]) - float(seg["start"])
    r = LineResult(idx, str(seg.get("speaker", "")), float(seg["start"]),
                   budget, seg["text"])

    intent = complete_json(INTENT_PROMPT.format(context=context, source=seg["text"]))
    if not intent:
        r.errors.append("intent: unparseable"); return r
    r.intent = intent

    target_ak = RateModel(rate).aksharas_for(budget)
    aims = [max(1, round(target_ak * p)) for p in (0.85, 0.95, 1.0, 1.05, 1.12)][:n_cand]
    out = complete_json(CANDIDATE_PROMPT.format(
        language=lang, script=script, function=intent.get("function"),
        beat=intent.get("beat"), note=intent.get("note"), tone=intent.get("tone"),
        entities=", ".join(intent.get("entities") or []) or "(none)",
        budget=budget, target_aksharas=target_ak, n=n_cand,
        aims=", ".join(str(a) for a in aims), rules=rules))
    if not out or "candidates" not in out:
        r.errors.append("candidates: unparseable"); return r

    rm = RateModel(rate)
    for c in out["candidates"]:
        text = (c.get("text") or "").strip()
        ak = count_aksharas(text)
        est = rm.seconds(ak)
        cls, delta = fit_class(est, budget)
        r.candidates.append({
            "text": text, "aim": c.get("aim"), "aksharas": ak,
            "est_s": round(est, 3), "fit": cls, "delta_pct": round(100 * delta, 1),
            "foreign_script": script_purity(text, target),
            "missing_protected": [t for t in protected
                                  if t.lower() in seg["text"].lower()
                                  and t.lower() not in text.lower()],
        })

    ok = [c for c in r.candidates if c["fit"] != "OVER"] or r.candidates
    r.chosen = min(ok, key=lambda c: abs(c["delta_pct"]))

    if r.chosen["text"]:
        g = (gloss_with or complete_json)(
            BACKTRANSLATE_PROMPT.format(language=lang, text=r.chosen["text"]))
        r.gloss = (g or {}).get("gloss", "")
    return r


def load_segments(path):
    d = json.loads(open(path, encoding="utf-8").read())
    segs = d["transcript"] if isinstance(d, dict) and "transcript" in d else d
    return [s for s in segs if (s.get("text") or "").strip()]


def report(results, target, rate, model_name, out_path):
    lang = SCRIPTS[target][0]
    print(f"\n{'=' * 78}\n{model_name} -> {lang}   rate={rate} aksh/s\n{'=' * 78}")
    counts, purity_fail, protected_fail, spreads, errs = {}, 0, 0, [], 0
    for r in results:
        if r.errors:
            errs += 1
            print(f"\n[{r.index:2}] {r.budget_s:5.2f}s  ERROR: {'; '.join(r.errors)}")
            continue
        c = r.chosen
        counts[c["fit"]] = counts.get(c["fit"], 0) + 1
        if c["foreign_script"]:
            purity_fail += 1
        if c["missing_protected"]:
            protected_fail += 1
        aks = [x["aksharas"] for x in r.candidates]
        if len(aks) > 1:
            spreads.append(max(aks) - min(aks))
        print(f"\n[{r.index:2}] {r.budget_s:5.2f}s  {r.intent.get('function','?'):<8} "
              f"{r.intent.get('beat','')}")
        print(f"     src   : {r.source}")
        for x in r.candidates:
            mark = "*" if x is c else " "
            flags = ""
            if x["foreign_script"]:
                flags += f"  !script:{','.join(x['foreign_script'])}"
            if x["missing_protected"]:
                flags += f"  !lost:{','.join(x['missing_protected'])}"
            print(f"   {mark} {x['aksharas']:3} ak  {x['est_s']:5.2f}s  "
                  f"{x['fit']:<5} {x['delta_pct']:+6.1f}%  {x['text']}{flags}")
        print(f"     gloss : {r.gloss}")

    n = len(results)
    print(f"\n{'-' * 78}\nSUMMARY  {model_name}")
    print(f"  lines                : {n}   json failures: {errs}")
    print(f"  chosen fit           : " +
          "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if spreads:
        print(f"  length control       : median candidate spread "
              f"{sorted(spreads)[len(spreads)//2]} aksharas "
              f"({'OK' if sorted(spreads)[len(spreads)//2] >= 2 else 'POOR - candidates all same length'})")
    print(f"  script purity        : {n - errs - purity_fail}/{n - errs} clean")
    print(f"  protected terms kept : {n - errs - protected_fail}/{n - errs}")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in results], f, ensure_ascii=False, indent=2)
    print(f"\nsaved -> {out_path}")
