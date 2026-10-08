#!/usr/bin/env python3
"""
Stage 5 adaptation probe — OpenRouter (Claude / GPT / Gemini / Qwen on one key).

  python3 block_g_adapt_openrouter.py --target pa --limit 4
  python3 block_g_adapt_openrouter.py --target pa --models anthropic/claude-sonnet-5,openai/gpt-5
  python3 block_g_adapt_openrouter.py --target pa --gloss-model qwen/qwen3-235b-a22b

Two things this does that a plain API call would not:
  * pins the upstream provider (allow_fallbacks=false) and records which one served
    the request - the same model id served by two providers at different
    quantisations is not the same model, and your doc requires version pinning
  * sets data_collection=deny, because unreleased client ad copy must not be
    trained on (risk R15)
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, urllib.request, urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import adapt_common as A
import adapt_group as G
import ledger
from dataclasses import asdict
import dryrun
from block_g_adapt_gemini import load_env

ROOT = Path(__file__).resolve().parents[2]
API = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_MODELS = "anthropic/claude-sonnet-5,openai/gpt-5,google/gemini-3.1-pro-preview"


class Router:
    """One OpenRouter model, with cost and provider accounting."""

    def __init__(self, model, key, temperature=0.9, deny_training=True):
        self.model, self.key, self.temperature = model, key, temperature
        self.deny_training = deny_training
        self.calls = self.failures = 0
        self.cost = 0.0
        self.providers = set()

    def __call__(self, prompt):
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "usage": {"include": True},
            "provider": {"allow_fallbacks": False,
                         **({"data_collection": "deny"} if self.deny_training else {})},
        }
        delay = 5
        for _ in range(4):
            req = urllib.request.Request(
                API, data=json.dumps(body).encode(),
                headers={"Authorization": f"Bearer {self.key}",
                         "Content-Type": "application/json",
                         "HTTP-Referer": "https://nextberry.local/vernacular",
                         "X-Title": "NextBerry Vernacular feasibility"})
            try:
                with urllib.request.urlopen(req, timeout=240) as r:
                    d = json.load(r)
            except urllib.error.HTTPError as e:
                msg = e.read().decode()[:220]
                if e.code in (429, 502, 503):
                    print(f"      [{self.model}] {e.code} busy, retry in {delay}s")
                    time.sleep(delay); delay *= 2; continue
                print(f"      [{self.model}] HTTP {e.code}: {msg}")
                self.failures += 1
                return None
            self.calls += 1
            _u = d.get("usage") or {}
            ledger.record("openrouter", self.model, "llm",
                          tokens_in=_u.get("prompt_tokens"),
                          tokens_out=_u.get("completion_tokens"),
                          cost_usd=_u.get("cost"),
                          note=d.get("provider", ""))
            if d.get("provider"):
                self.providers.add(d["provider"])
            usage = d.get("usage") or {}
            if usage.get("cost") is not None:
                self.cost += float(usage["cost"])
            try:
                text = d["choices"][0]["message"]["content"]
            except (KeyError, IndexError):
                self.failures += 1
                return None
            m = re.search(r"\{.*\}", text or "", re.S)
            if not m:
                self.failures += 1
                return None
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                self.failures += 1
                return None
        self.failures += 1
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", default=str(ROOT / "feasibility" / "results" /
                                              "camb_slow_hi-IN_IT0AfU3277I_48k_mono.json"))
    ap.add_argument("--target", default="pa", choices=list(A.SCRIPTS))
    ap.add_argument("--models", default=DEFAULT_MODELS)
    ap.add_argument("--gloss-model", default=None,
                    help="back-translation model; must be a DIFFERENT family "
                         "from the generator (default: qwen/qwen3-235b-a22b)")
    ap.add_argument("--candidates", type=int, default=5)
    ap.add_argument("--rate", type=float, default=7.0,
                    help="aksharas/sec; a prior until Block D4 measures it")
    ap.add_argument("--temperature", type=float, default=0.9)
    ap.add_argument("--allow-training", action="store_true",
                    help="permit providers that train on inputs (default: denied)")
    ap.add_argument("--context", default="30-second Indian TV commercial for Livpure Smart, "
                                         "a water purifier rented for a monthly fee. "
                                         "Presenter talks straight to camera, playful and direct.")
    ap.add_argument("--protected", default="Livpure,375,RO")
    ap.add_argument("--limit", type=int, default=0, help="first N utterances")
    ap.add_argument("--no-group", action="store_true",
                    help="adapt each segment alone (the old, broken behaviour)")
    ap.add_argument("--max-gap", type=float, default=0.6,
                    help="segments closer than this belong to one utterance")
    ap.add_argument("--score-top", type=int, default=3,
                    help="candidates gloss-checked and re-ranked on meaning (0 = fit only)")
    ap.add_argument("--allowlist", default=",".join(G.DEFAULT_ALLOWLIST),
                    help="borrowed words that must not be translated away")
    ap.add_argument("--dry-run", action="store_true",
                    help="build every prompt with a fake model, audit the wiring, "
                         "estimate cost - makes ZERO paid calls")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    load_env()
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("Set OPENROUTER_API_KEY in speech-engine/.env")

    segs = A.load_segments(a.segments)
    protected = [t for t in a.protected.split(",") if t]
    allowlist = [t.strip() for t in a.allowlist.split(",") if t.strip()]
    units = ([[s] for s in segs] if a.no_group
             else G.group_segments(segs, max_gap=a.max_gap))
    if a.limit:
        units = units[:a.limit]
    print(f"{len(segs)} segments -> {len(units)} "
          f"{'segments (ungrouped)' if a.no_group else 'utterances'}")
    outdir = Path(a.out) if a.out else (ROOT / "feasibility" / "results" /
                                        f"adapt_or_{a.target}_{time.strftime('%Y%m%d-%H%M')}")
    outdir.mkdir(parents=True, exist_ok=True)

    gloss_name = a.gloss_model or "qwen/qwen3-235b-a22b"

    if a.dry_run:
        fake = dryrun.FakeModel(n_parts=len(units[0]), n_candidates=a.candidates,
                                target_script=("ਸ" if a.target == "pa" else "ಸ"))
        G.AUDIT.clear()
        G.adapt_group(fake, 0, units[0], a.target, a.rate, a.candidates, a.context,
                      A.DEFAULT_RULES, protected, allowlist, gloss_with=fake,
                      score_top=a.score_top)
        for i, pr in enumerate(fake.prompts[:2]):
            dryrun.show_prompt(f"PROMPT {i + 1} as the model will receive it", pr)
        print(f"\n{'=' * 100}\nWIRING AUDIT\n{'=' * 100}")
        if G.AUDIT:
            for pb in G.AUDIT:
                print(f"  PROBLEM  {pb}")
        else:
            print("  no problems: every template placeholder is supplied and every "
                  "value passed is used")
        models = [m.strip() for m in a.models.split(",") if m.strip()]
        calls, _ = dryrun.estimate_llm_cost(len(units), a.candidates, a.score_top, 0, 0)
        print(f"\n{'=' * 100}\nCOST FORECAST\n{'=' * 100}")
        print(f"  {len(units)} utterances x (2 + 2x{a.score_top}) = {calls} calls per model")
        for m in models:
            rate_in, rate_out = (2.0, 10.0) if "sonnet" in m else \
                                (1.25, 10.0) if "gpt-5" in m else (2.0, 12.0)
            _, usd = dryrun.estimate_llm_cost(len(units), a.candidates, a.score_top,
                                              rate_in, rate_out)
            print(f"    {m:<42} ~${usd:.3f}")
        print(f"\n  DRY RUN - nothing was sent, nothing was charged.")
        print(f"  Check the prompts above actually contain your protected terms, "
              f"allowlist and slot budgets before running for real.")
        return

    summary = []

    for model in [m.strip() for m in a.models.split(",") if m.strip()]:
        gen = Router(model, key, a.temperature, not a.allow_training)
        gloss_name_here = gloss_name
        if gloss_name.split("/")[0] == model.split("/")[0]:
            print(f"!! {model}: gloss model {gloss_name} is the SAME family — "
                  f"verification would validate its own biases")
        gloss = Router(gloss_name_here, key, 0.0, not a.allow_training)

        print(f"\n{'=' * 78}\n{model}   ->   {A.SCRIPTS[a.target][0]}   "
              f"({len(units)} utterances, gloss by {gloss_name_here})\n{'=' * 78}")
        # Checkpoint each utterance as it completes. A run stopped halfway used to
        # lose everything it had paid for; now a re-run resumes and only pays for
        # what is missing.
        part_file = outdir / f"partial_{model.replace('/', '_')}_{a.target}.jsonl"
        done = {}
        if part_file.exists():
            for ln in part_file.read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    r = json.loads(ln)
                    done[r["index"]] = r
            if done:
                print(f"  resuming: {len(done)} utterance(s) already done, "
                      f"{len(units) - len(done)} to go")

        results = []
        for i, unit in enumerate(units):
            if i in done:
                results.append(G.GroupResult(**done[i]))
                continue
            print(f"  utterance {i + 1}/{len(units)} ({len(unit)} line"
                  f"{'s' if len(unit) > 1 else ''}) ...", flush=True)
            r = G.adapt_group(gen, i, unit, a.target, a.rate, a.candidates,
                              a.context, A.DEFAULT_RULES, protected,
                              allowlist, gloss_with=gloss, score_top=a.score_top)
            results.append(r)
            with part_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

        out = outdir / f"adapt_{model.replace('/', '_')}_{a.target}.json"
        G.report_groups(results, a.target, a.rate, model, out, allowlist)
        total = gen.cost + gloss.cost
        print(f"  calls {gen.calls + gloss.calls}  failures {gen.failures + gloss.failures}"
              f"  cost ${total:.4f}  provider(s) {', '.join(gen.providers) or 'n/a'}")
        summary.append((model, gen.calls + gloss.calls,
                        gen.failures + gloss.failures, total,
                        ", ".join(gen.providers) or "n/a"))

    print(f"\n{'=' * 78}\nRUN SUMMARY")
    print(f"{'model':<40}{'calls':>7}{'fails':>7}{'cost $':>10}  served by")
    for m, c, f, cost, prov in summary:
        print(f"{m:<40}{c:>7}{f:>7}{cost:>10.4f}  {prov}")
    print(f"{'TOTAL':<40}{sum(s[1] for s in summary):>7}"
          f"{sum(s[2] for s in summary):>7}{sum(s[3] for s in summary):>10.4f}")
    print(f"\nsaved -> {outdir}")


if __name__ == "__main__":
    main()
