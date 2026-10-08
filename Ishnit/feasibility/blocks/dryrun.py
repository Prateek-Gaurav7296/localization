#!/usr/bin/env python3
"""
Dry-run support — prove the code path before spending money on it.

Every expensive bug so far was visible without an API call:
  * `protected` was computed, checked, and never put in the prompt
  * the whole protected list was sent to every line, so the model inserted the
    brand name into lines that never said it
  * a quota error was retried for 30 minutes
All three would have been caught by printing one prompt and auditing it.

Two tools:
  audit_template()  - placeholders a template needs vs values actually passed,
                      AND values passed that the template never uses (the bug class above)
  FakeModel         - canned responses so the full scoring/report path executes offline
"""
from __future__ import annotations
import json, re
from string import Formatter


def template_fields(template: str) -> set[str]:
    return {f for _, f, _, _ in Formatter().parse(template) if f}


def audit_template(name: str, template: str, kwargs: dict) -> list[str]:
    """Returns human-readable problems; empty list means the template is wired up."""
    need = template_fields(template)
    have = set(kwargs)
    problems = []
    for missing in sorted(need - have):
        problems.append(f"{name}: template needs {{{missing}}} but nothing was passed "
                        f"-> KeyError at runtime")
    for unused in sorted(have - need):
        problems.append(f"{name}: '{unused}' was passed but the template never uses it "
                        f"-> the model will never see it")
    for k in sorted(need & have):
        v = kwargs[k]
        if v is None or (isinstance(v, (str, list, dict)) and len(v) == 0):
            problems.append(f"{name}: {{{k}}} is empty - check this is intended")
    return problems


class FakeModel:
    """Returns shape-correct canned JSON for every prompt the pipeline sends."""

    def __init__(self, n_parts=1, n_candidates=5, target_script="ਸ"):
        self.n_parts, self.n_candidates = n_parts, n_candidates
        self.script = target_script
        self.prompts = []
        self.calls = 0

    def __call__(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        if '"function"' in prompt and '"literal"' in prompt:
            return {"function": "hook", "beat": "test beat", "note": "test note",
                    "literal": "literal of the source", "entities": ["Brand"],
                    "tone": "warm"}
        if '"candidates"' in prompt:
            return {"candidates": [
                {"parts": [f"{self.script * (2 + i)} Brand" for _ in range(self.n_parts)]}
                for i in range(self.n_candidates)]}
        if '"gloss"' in prompt:
            return {"gloss": "literal english gloss Brand"}
        if "matches_brief" in prompt:
            return {"matches_brief": True, "matches_original": True, "drift": ""}
        return {}


def show_prompt(title: str, prompt: str, width: int = 100):
    print(f"\n{'=' * width}\n{title}\n{'=' * width}")
    print(prompt)


def estimate_llm_cost(utterances: int, candidates: int, score_top: int,
                      usd_in_per_mtok: float, usd_out_per_mtok: float,
                      tok_in=900, tok_out=600):
    """Calls per utterance: intent + candidates + score_top x (gloss + judge)."""
    calls = utterances * (2 + 2 * score_top)
    cost = calls * (tok_in * usd_in_per_mtok + tok_out * usd_out_per_mtok) / 1e6
    return calls, cost


def estimate_camb_credits(total_chars: int, credits_per_char=2.8125 / 10):
    """Measured: 2.8125 credits for a 10-character render."""
    return total_chars * credits_per_char
