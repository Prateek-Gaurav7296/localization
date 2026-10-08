#!/usr/bin/env python3
"""
Block H — end to end: adapted copy -> TTS -> does it land in the original slot?

Takes the chosen candidate from a Block G adaptation run and speaks every line,
then compares three numbers per line:

    budget_s   the slot the original line occupied   (the contract)
    est_s      what the akshara estimator predicted  (Stage 5 believed this)
    measured   what the voice engine actually produced

That gives the fit result AND the estimator error (test D5) from one run.

  .venv/bin/python feasibility/blocks/block_h_e2e.py \
      --adaptation feasibility/results/adapt_or_pa_*/adapt_anthropic_claude-sonnet-5_pa.json
"""
from __future__ import annotations
import argparse, csv, glob, json, statistics, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import adapt_common as A
import block_d_tts as D

ROOT = Path(__file__).resolve().parents[2]


def load_lines(path):
    """[(group_idx, line_idx, source, text, aksharas, est_s, budget_s)]"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for g in data:
        if not g.get("chosen"):
            continue
        for j, (l, part) in enumerate(zip(g["lines"], g["chosen"]["parts"])):
            out.append((g["index"], j, l["source"], part["text"],
                        part["aksharas"], part["est_s"], l["budget_s"]))
    return out


def fit_of(measured, budget):
    return A.fit_class(measured, budget)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adaptation", required=True, help="Block G json (globs allowed)")
    ap.add_argument("--lang", default="pa")
    ap.add_argument("--models", default="camb:mars-instruct,gemini:gemini-3.8-flash-tts")
    ap.add_argument("--voice-id", type=int, default=165289)
    ap.add_argument("--pace", type=float, default=0.0,
                    help="seconds between TTS calls (free-tier quota)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    D.load_env()
    D.PACE_SECONDS = a.pace
    matches = sorted(glob.glob(a.adaptation))
    if not matches:
        sys.exit(f"no adaptation file matched {a.adaptation}")
    lines = load_lines(matches[0])
    print(f"{Path(matches[0]).name}: {len(lines)} lines\n")

    outdir = Path(a.out) if a.out else (ROOT / "feasibility" / "results" /
                                        f"block_h_{a.lang}_{time.strftime('%Y%m%d-%H%M')}")
    outdir.mkdir(parents=True, exist_ok=True)
    csv_path = outdir / "e2e.csv"
    rows = []

    for spec in [m.strip() for m in a.models.split(",") if m.strip()]:
        try:
            tts = D.build(spec, a.voice_id, a.lang)
        except Exception as e:
            print(f"!! {spec}: {type(e).__name__}: {str(e)[:120]}"); continue
        ok, why, secs = D.capability_probe(tts, a.lang, outdir)
        print(f"=== {tts.name}   probe: {'OK' if ok else 'SKIP'} ({why})")
        if not ok:
            continue

        print(f"{'#':>5} {'budget':>7} {'est':>7} {'spoken':>7} {'vs slot':>9} "
              f"{'est err':>8}  text")
        per = []
        for gi, li, src, text, ak, est, budget in lines:
            tag = f"{gi}.{li}"
            p = outdir / f"{tts.name.replace(':','_')}_{gi}_{li}.wav"
            try:
                tts.say(text, p)
                measured = D.trim_silence(p)
            except Exception as e:
                print(f"{tag:>5} ERROR {str(e)[:90]}")
                continue
            cls, d = fit_of(measured, budget)
            est_err = (measured - est) / measured if measured else 0
            per.append((budget, est, measured, ak, cls, d, est_err))
            rows.append(dict(model=tts.name, group=gi, line=li, aksharas=ak,
                             budget_s=round(budget, 3), est_s=round(est, 3),
                             measured_s=round(measured, 3),
                             delta_pct=round(100 * d, 1), fit=cls,
                             est_err_pct=round(100 * est_err, 1), text=text))
            print(f"{tag:>5} {budget:7.2f} {est:7.2f} {measured:7.2f} "
                  f"{100*d:+8.1f}% {100*est_err:+7.1f}%  {text[:44]}")

        if not per:
            continue
        fits = {}
        for *_x, cls, _d, _e in per:
            fits[cls] = fits.get(cls, 0) + 1
        deltas = [abs(d) for *_x, _c, d, _e in per]
        errs = [abs(e) for *_x, _c, _d, e in per]
        aks = [(x[3], x[2]) for x in per]
        mx, my = statistics.mean(k for k, _ in aks), statistics.mean(v for _, v in aks)
        den = sum((k - mx) ** 2 for k, _ in aks) or 1e-9
        b = sum((k - mx) * (v - my) for k, v in aks) / den
        intercept = my - b * mx
        shippable = sum(v for k, v in fits.items() if k != "OVER")

        print(f"\n  fit            : " + "  ".join(f"{k}={v}" for k, v in sorted(fits.items())))
        print(f"  shippable      : {shippable}/{len(per)} "
              f"({100*shippable/len(per):.0f}%)")
        print(f"  median |delta| : {statistics.median(deltas)*100:.1f}%")
        print(f"  estimator MAE  : {statistics.mean(errs)*100:.1f}%  "
              f"(DoD target <=5%)")
        if b > 0:
            print(f"  measured rate  : {1/b:.2f} aksharas/sec, intercept {intercept:.2f}s "
                  f"(prior was 7.00 / 0.18)")
        print()

    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)
    print(f"saved -> {outdir}")


if __name__ == "__main__":
    main()
