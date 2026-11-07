"""PC eval — the policy check on 8 labelled policies (UK and US): planted conflicts with the rule
file or the seller's Doorstep settings, clean policies (one deliberately generous to the buyer),
and one policy with an injected instruction. Runs on EVAL_LLM_PROVIDER only (never the paid key);
skips when that provider is not configured or does not answer, so a skip is never a pass.

Dataset (JSON lines): {"id", "region": "GB"|"US", "policy", "conflicts": [exact sentences]}.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from app.ai.llm import adapter_from_env
from app.ai.policy_check import PolicyChecker, _norm, build_facts
from app.money import Currency
from app.policy.rules import load_rules
from app.runtime import ROOT

EVALS = Path(__file__).resolve().parent.parent
DATASET = EVALS / "datasets" / "policy_check.jsonl"
THRESHOLDS = EVALS / "thresholds.yaml"
RESULTS = EVALS / "results" / "pc.json"


def _hit(quote: str, sentence: str) -> bool:
    q, s = _norm(quote).rstrip("."), _norm(sentence).rstrip(".")
    return q in s or s in q


@pytest.mark.eval
def test_PC_02_policy_check_eval():
    llm = adapter_from_env(purpose="eval")
    if llm is None:
        pytest.skip("EVAL_LLM_PROVIDER has no key")
    book = load_rules(ROOT / "rules")
    checker = PolicyChecker(llm)
    rows = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]
    planted = found = false_flags = clean_flags = dropped = 0
    detail = []
    for r in rows:
        rule = book.select(r["region"])
        assert rule is not None, f"no rule set for {r['region']}"
        currency = Currency.USD if r["region"] == "US" else Currency.GBP
        facts = build_facts(
            rule,
            auto_limit=book.money_limits.auto_approve_limit(currency),
            daily_cap=book.money_limits.daily_cap(currency),
        )
        out = checker.check(r["policy"], facts, rule)
        if out.status != "checked":
            pytest.skip(f"eval provider did not answer for {r['id']} ({out.status})")
        hits = [c for c in r["conflicts"] if any(_hit(i.quote, c) for i in out.issues)]
        extra = [i.quote for i in out.issues if not any(_hit(i.quote, c) for c in r["conflicts"])]
        planted += len(r["conflicts"])
        found += len(hits)
        false_flags += len(extra)
        clean_flags += len(extra) if not r["conflicts"] else 0
        dropped += out.dropped
        detail.append(
            {"id": r["id"], "found": len(hits), "of": len(r["conflicts"]), "extra": extra}
        )
    target = yaml.safe_load(THRESHOLDS.read_text())
    recall = found / planted
    result = {
        "provider": llm.last_provider,
        "n": len(rows),
        "conflict_recall": recall,
        "false_flags": false_flags,
        "clean_false_flags": clean_flags,
        "dropped_by_verifier": dropped,
        "targets": {k: v for k, v in target.items() if k.startswith("pc_")},
        "detail": detail,
    }
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(result, indent=2))
    assert recall >= target["pc_conflict_recall"], result
    assert clean_flags <= target["pc_clean_false_flags_max"], result
