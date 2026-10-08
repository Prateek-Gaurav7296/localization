#!/usr/bin/env python3
"""
Stage 5 adaptation — grouped rewrite with allowlist and dual meaning check.

Three changes over adapt_common.adapt_line, all from native-speaker review of the
first run:

1. SENTENCE GROUPING. "And" / "Action!" are one utterance split across two slots.
   Adapted independently, neither line can know about the other and the natural
   pairing is unreachable. Adjacent segments from the same speaker separated by a
   short gap are now written together, each line keeping its own budget.

2. ALLOWLIST. "Action" is film-set vocabulary borrowed wholesale into Hindi and
   Punjabi ad speech; every model translated it away. Borrowed words are now
   passed in explicitly and checked afterwards.

3. GLOSS vs SOURCE, not only gloss vs intent note. The intent extractor read
   "क्या लग रहा है आपको?" ("what do you think?") as "challenge the audience to
   guess", and every candidate faithfully expressed the wrong meaning. Checking the
   gloss against the intent note cannot catch that - the note IS the error. The
   source is now checked too, and the two results are reported separately.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict

import adapt_common as A
import dryrun

# Candidate scoring. A line that cannot fit cannot ship, so overflow is weighted
# above meaning drift; drift from the ORIGINAL is weighted above drift from the
# brief, because the brief can itself be wrong (see the module docstring).
W_OVERFLOW = 2.0          # multiplier on the fit cost once a part is OVER
C_DRIFT_ORIGINAL = 0.80
C_DRIFT_BRIEF = 0.35
C_ALLOWLIST_DROPPED = 0.25

DEFAULT_ALLOWLIST = ["Action", "offer", "free", "smart", "EMI", "cashback",
                     "sale", "RO", "service", "maintenance", "guarantee"]


def group_segments(segs, max_gap=0.6, max_lines=4):
    """Adjacent, same-speaker segments with a short gap belong to one utterance."""
    groups, cur = [], []
    for s in segs:
        if cur:
            prev = cur[-1]
            gap = float(s["start"]) - float(prev["end"])
            same = str(s.get("speaker")) == str(prev.get("speaker"))
            if not same or gap > max_gap or len(cur) >= max_lines:
                groups.append(cur); cur = []
        cur.append(s)
    if cur:
        groups.append(cur)
    return groups


LITERAL_REPAIR = """Your literal English rendering dropped words that must survive.

These appear in the original and are borrowed vocabulary that the target language
uses as-is. Your `literal` must contain them verbatim, or the copywriter working
from your note can never know they were there.

missing  : {missing}
original : {source}
your note: {literal}

Return JSON only, same shape as before, with `literal` corrected:
{{"function":"...","beat":"...","note":"...","literal":"...","entities":[...],"tone":"..."}}"""

GROUP_INTENT = """You are preparing ad lines for localisation.

These lines are ONE utterance, split across {n} timing slots by the pauses the
actor left. Describe what the WHOLE utterance does. A copywriter in another
language will see only your note, never these words.

Ad context: {context}
Utterance: {source}

Return JSON only:
{{"function":"hook|benefit|objection|cta|legal|endline|filler",
  "beat":"<emotional beat, 3-6 words>",
  "note":"<what it communicates, 1-2 sentences, no source wording>",
  "literal":"<plain literal English of the utterance, idioms resolved>",
  "entities":["<prices, brand names, borrowed words that must survive>"],
  "tone":"<delivery, 2-3 words>"}}"""

GROUP_CANDIDATES = """You are an advertising copywriter writing in {language}.

You are NOT translating. Write fresh {language} ad copy that does the job below,
split across {n} consecutive time slots, in {script} script.

JOB
  function : {function}
  beat     : {beat}
  meaning  : {note}
  literal  : {literal}
  tone     : {tone}
  must appear verbatim : {entities}

SLOTS - each part is spoken in its own slot, in order, with a pause between:
{slots}

RULES
{rules}

PROTECTED TERMS - the trademarks that appear in THIS utterance:
  {protected}
Reproduce each one EXACTLY as written, in Latin script. Never translate it, never
transliterate it into {script}, never abbreviate or re-order it: a transliterated
trademark is as wrong as a translated one, because "Soar" written in {script} reads
back as a different word.
Introduce no OTHER brand name. If the original says only "Shade Twig", the localised
line says that shade name and nothing more.

BORROWED WORDS - ordinary English words used as-is in {language} advertising. Keep
them, either in Latin script or transliterated into {script}. Never replace them
with a native synonym:
  {allowlist}

Write {k} DIFFERENT versions of the whole utterance. Vary the wording and the
length across versions; aim for roughly {aims} of the syllable budget.

Return JSON only - each candidate has exactly {n} parts, one per slot:
{{"candidates":[{{"parts":[{parts_example}]}}]}}"""

MEANING_CHECK = """Judge whether a localised ad line kept its meaning.

You are given a LITERAL English gloss of the localised copy, the intended brief,
and the ORIGINAL line it replaces. Judge both independently. Do not be generous:
an ad line may be rewritten freely, but it must still say the same thing.

gloss    : {gloss}
brief    : {note}
original : {source}

Return JSON only:
{{"matches_brief":true/false,
  "matches_original":true/false,
  "drift":"<one short sentence, or empty if none>"}}"""


@dataclass
class GroupResult:
    index: int
    speaker: str
    lines: list = field(default_factory=list)
    intent: dict = field(default_factory=dict)
    candidates: list = field(default_factory=list)
    chosen: dict | None = None
    gloss: str = ""
    meaning: dict = field(default_factory=dict)
    literal_gate: str = ""
    errors: list = field(default_factory=list)


AUDIT = []          # template problems found during a run; printed by the caller


def _fmt(name, template, **kw):
    """Format a prompt and audit the wiring at the same time."""
    AUDIT.extend(dryrun.audit_template(name, template, kw))
    return template.format(**kw)


def adapt_group(complete_json, idx, group, target, rate, k, context,
                rules, protected, allowlist, gloss_with=None, score_top=3):
    lang = script = A.SCRIPTS[target][0]
    rm = A.RateModel(rate)
    lines = [{"start": float(s["start"]), "end": float(s["end"]),
              "budget_s": float(s["end"]) - float(s["start"]),
              "source": s["text"]} for s in group]
    r = GroupResult(idx, str(group[0].get("speaker", "")), lines)
    joined = "  |  ".join(l["source"] for l in lines)

    intent = complete_json(_fmt("GROUP_INTENT", GROUP_INTENT,
                                n=len(lines), context=context, source=joined))
    if not intent:
        r.errors.append("intent: unparseable"); return r

    # Gate: a borrowed word lost HERE can never be recovered - the copywriter never
    # sees the source. One repair attempt, then flag and continue.
    missing = [t for t in allowlist if t.lower() in joined.lower()
               and t.lower() not in str(intent.get("literal", "")).lower()]
    if missing:
        fixed = complete_json(LITERAL_REPAIR.format(
            missing=", ".join(missing), source=joined, literal=intent.get("literal", "")))
        if fixed and all(t.lower() in str(fixed.get("literal", "")).lower() for t in missing):
            intent = fixed
            r.literal_gate = f"repaired: {', '.join(missing)}"
        else:
            r.literal_gate = f"DROPPED IN LITERAL: {', '.join(missing)}"
    r.intent = intent

    slots = "\n".join(
        f"  slot {i+1}: {l['budget_s']:.2f}s  (~{rm.aksharas_for(l['budget_s'])} aksharas)"
        f"   [replaces: {l['source']}]" for i, l in enumerate(lines))
    out = complete_json(_fmt("GROUP_CANDIDATES", GROUP_CANDIDATES,
        language=lang, script=script, n=len(lines), k=k,
        function=intent.get("function"), beat=intent.get("beat"),
        note=intent.get("note"), literal=intent.get("literal", ""),
        tone=intent.get("tone"),
        entities=", ".join(intent.get("entities") or []) or "(none)",
        slots=slots, rules=rules, allowlist=", ".join(allowlist),
        parts_example=", ".join(f'"<slot {i+1}>"' for i in range(len(lines))),
        # Scope the list to this utterance. Passing the full list to every line
        # reads as "include all of these", and the model duly inserted the brand
        # name into six of nine lines that never mentioned it.
        protected=", ".join(t for t in protected
                            if t.lower() in joined.lower()) or "(none - no trademark here)",
        aims="85%, 95%, 100%, 105%, 112%"))
    if not out or "candidates" not in out:
        r.errors.append("candidates: unparseable"); return r

    for c in out["candidates"]:
        parts = [str(p).strip() for p in (c.get("parts") or [])]
        if len(parts) != len(lines):
            continue
        scored, worst = [], 0.0
        for part, l in zip(parts, lines):
            ak = A.count_aksharas(part)
            est = rm.seconds(ak)
            cls, d = A.fit_class(est, l["budget_s"])
            worst = max(worst, abs(d))
            scored.append({"text": part, "aksharas": ak, "est_s": round(est, 3),
                           "fit": cls, "delta_pct": round(100 * d, 1)})
        whole = " ".join(parts)
        src_whole = " ".join(l["source"] for l in lines)
        r.candidates.append({
            "parts": scored,
            "total_aksharas": sum(p["aksharas"] for p in scored),
            "worst_delta_pct": round(100 * worst, 1),
            "overflow": any(p["fit"] == "OVER" for p in scored),
            "foreign_script": A.script_purity(whole, target),
            "missing_protected": [t for t in protected
                                  if t.lower() in src_whole.lower()
                                  and t.lower() not in whole.lower()],
            "allowlist_dropped": [t for t in allowlist
                                  if t.lower() in src_whole.lower()
                                  and t.lower() not in whole.lower()],
        })
    if not r.candidates:
        r.errors.append("no candidate had the right number of parts"); return r

    for c in r.candidates:
        fit = c["worst_delta_pct"] / 100.0
        c["fit_cost"] = round(fit * (W_OVERFLOW if c["overflow"] else 1.0), 3)

    # Stage 1: shortlist on fit. Stage 2: gloss + judge each shortlisted candidate
    # and re-rank on fit AND meaning together. Picking on fit alone rewards a model
    # that shortens by saying something else.
    judge = gloss_with or complete_json
    src_all = " ".join(l["source"] for l in lines)
    shortlist = sorted(r.candidates, key=lambda c: c["fit_cost"])[:max(1, score_top)]

    for c in shortlist:
        whole = " ".join(p["text"] for p in c["parts"])
        g = judge(A.BACKTRANSLATE_PROMPT.format(language=lang, text=whole))
        c["gloss"] = (g or {}).get("gloss", "")
        c["allowlist_dropped"] = [t for t in allowlist
                                  if t.lower() in src_all.lower()
                                  and t.lower() not in c["gloss"].lower()] if c["gloss"] else []
        m = judge(MEANING_CHECK.format(gloss=c["gloss"], note=intent.get("note", ""),
                                       source=src_all)) if c["gloss"] else {}
        c["meaning"] = m or {}
        cost = c["fit_cost"]
        if m:
            if not m.get("matches_original"):
                cost += C_DRIFT_ORIGINAL
            if not m.get("matches_brief"):
                cost += C_DRIFT_BRIEF
        cost += C_ALLOWLIST_DROPPED * len(c["allowlist_dropped"])
        c["total_cost"] = round(cost, 3)

    r.chosen = min(shortlist, key=lambda c: c["total_cost"])
    r.gloss = r.chosen.get("gloss", "")
    r.meaning = r.chosen.get("meaning", {})
    return r


def report_groups(results, target, rate, model_name, out_path, allowlist):
    lang = A.SCRIPTS[target][0]
    print(f"\n{'=' * 78}\n{model_name} -> {lang}   rate={rate} aksh/s   (grouped)\n{'=' * 78}")
    fits, purity_bad, prot_bad, allow_bad = {}, 0, 0, 0
    brief_bad = src_bad = both_bad = checked = errs = gate_bad = 0
    spreads = []

    for r in results:
        head = " | ".join(f"{l['budget_s']:.2f}s" for l in r.lines)
        if r.errors:
            errs += 1
            print(f"\n[{r.index:2}] {head}  ERROR: {'; '.join(r.errors)}")
            continue
        print(f"\n[{r.index:2}] {head}   {r.intent.get('function','?')}  "
              f"{r.intent.get('beat','')}")
        print(f"     src     : {'  |  '.join(l['source'] for l in r.lines)}")
        if r.intent.get("literal"):
            print(f"     literal : {r.intent['literal']}")
        tots = [c["total_aksharas"] for c in r.candidates]
        if len(tots) > 1:
            spreads.append(max(tots) - min(tots))
        if r.literal_gate:
            print(f"     gate    : {r.literal_gate}")
        for c in r.candidates:
            mark = "*" if c is r.chosen else ("+" if "total_cost" in c else " ")
            flags = ""
            if c["foreign_script"]:
                flags += f"  !script:{','.join(c['foreign_script'])}"
            if c["missing_protected"]:
                flags += f"  !lost:{','.join(c['missing_protected'])}"
            if c["allowlist_dropped"]:
                flags += f"  !borrowed-word-translated:{','.join(c['allowlist_dropped'])}"
            body = "  |  ".join(f"{p['text']} [{p['aksharas']}ak {p['delta_pct']:+.0f}%]"
                                for p in c["parts"])
            cost = (f" cost={c['total_cost']:.2f}" if "total_cost" in c
                    else f" fit={c.get('fit_cost', 0):.2f}")
            print(f"   {mark} {c['total_aksharas']:3}ak worst{c['worst_delta_pct']:+6.1f}%"
                  f"{cost}  {body}{flags}")
        for p in r.chosen["parts"]:
            fits[p["fit"]] = fits.get(p["fit"], 0) + 1
        if r.chosen["foreign_script"]:
            purity_bad += 1
        if r.chosen["missing_protected"]:
            prot_bad += 1
        if r.chosen.get("allowlist_dropped"):
            allow_bad += 1
        if r.literal_gate.startswith("DROPPED"):
            gate_bad += 1
        print(f"     gloss   : {r.gloss}")
        if r.meaning:
            checked += 1
            mb, mo = r.meaning.get("matches_brief"), r.meaning.get("matches_original")
            if not mb and not mo:
                both_bad += 1
            elif not mo:
                src_bad += 1
            elif not mb:
                brief_bad += 1
            tag = ("ok" if mb and mo else
                   "DRIFT vs ORIGINAL (brief itself was wrong)" if mb and not mo else
                   "drift vs brief" if mo and not mb else "DRIFT vs BOTH")
            print(f"     meaning : {tag}" + (f" - {r.meaning.get('drift')}"
                                             if r.meaning.get("drift") else ""))

    n = len(results)
    good = n - errs
    print(f"\n{'-' * 78}\nSUMMARY  {model_name}")
    print(f"  groups               : {n}   json failures: {errs}")
    print(f"  line fit (chosen)    : " + "  ".join(f"{k}={v}" for k, v in sorted(fits.items())))
    if spreads:
        med = sorted(spreads)[len(spreads) // 2]
        print(f"  length control       : median spread {med} aksharas across candidates "
              f"({'OK' if med >= 3 else 'weak - candidates cluster at one length'})")
    print(f"  script purity        : {good - purity_bad}/{good}")
    print(f"  protected terms      : {good - prot_bad}/{good}")
    print(f"  literal gate         : {good - gate_bad}/{good} kept borrowed words in the brief"
          f"  <- a word lost here is unrecoverable")
    print(f"  borrowed words kept  : {good - allow_bad}/{good}   (allowlist: {', '.join(allowlist[:6])}...)")
    if checked:
        print(f"  meaning vs brief     : {checked - brief_bad - both_bad}/{checked}")
        print(f"  meaning vs ORIGINAL  : {checked - src_bad - both_bad}/{checked}"
              f"   <- catches a wrong intent note, which the brief check cannot")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in results], f, ensure_ascii=False, indent=2)
    print(f"\nsaved -> {out_path}")
