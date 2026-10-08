#!/usr/bin/env python3
"""
Minimal cost ledger — one append-only row per paid provider call.

The KT doc's `provider_calls` table is what prevents double-spending and makes
cost a queryable number rather than an estimate. The call sites are being written
now, so capture goes in now: retrofitting it means auditing every call site later.

Rows land in feasibility/results/ledger.jsonl.
"""
from __future__ import annotations
import json, os, time
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[1] / "results" / "ledger.jsonl"
RUN_ID = os.environ.get("RUN_ID") or time.strftime("%Y%m%d-%H%M%S")


def record(provider, model, op, *, units=None, unit="", cost_usd=None,
           credits=None, tokens_in=None, tokens_out=None, ok=True, note=""):
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "run": RUN_ID,
           "provider": provider, "model": model, "op": op,
           "units": units, "unit": unit, "cost_usd": cost_usd,
           "credits": credits, "tokens_in": tokens_in, "tokens_out": tokens_out,
           "ok": ok, "note": note}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def summarise(run=None, path=None):
    p = Path(path) if path else LEDGER
    if not p.exists():
        return "no ledger yet"
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    if run:
        rows = [r for r in rows if r["run"] == run]
    agg = {}
    for r in rows:
        k = (r["provider"], r["model"], r["op"])
        a = agg.setdefault(k, {"calls": 0, "usd": 0.0, "credits": 0.0,
                               "units": 0.0, "unit": r.get("unit", ""), "fail": 0})
        a["calls"] += 1
        a["usd"] += r.get("cost_usd") or 0
        a["credits"] += r.get("credits") or 0
        a["units"] += r.get("units") or 0
        if not r.get("ok", True):
            a["fail"] += 1
    out = [f"{'provider/model':<42}{'op':<12}{'calls':>6}{'fail':>5}"
           f"{'units':>10}{'credits':>9}{'USD':>9}", "-" * 93]
    tot_usd = tot_cred = 0.0
    for (prov, model, op), a in sorted(agg.items()):
        out.append(f"{prov + '/' + str(model):<42}{op:<12}{a['calls']:>6}{a['fail']:>5}"
                   f"{a['units']:>9.1f}{a['unit']:<1}{a['credits']:>9.1f}"
                   f"{a['usd']:>9.4f}")
        tot_usd += a["usd"]; tot_cred += a["credits"]
    out.append("-" * 93)
    out.append(f"{'TOTAL':<60}{'':>10}{tot_cred:>9.1f}{tot_usd:>9.4f}")
    if tot_cred:
        out.append("  (credits are Camb units; multiply by your plan's rate for USD)")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    print(summarise(run=sys.argv[1] if len(sys.argv) > 1 else None))
