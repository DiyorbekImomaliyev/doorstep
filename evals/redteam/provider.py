"""Promptfoo provider for the SE-03 red-team (promptfooconfig.yaml).

Promptfoo calls `call_api` once per test in cases.yaml. The test's vars name the attack and the
scenario; the prompt text is not used. The provider reports the attack on a fresh copy of the
demo shop, with the recorded or live eval model, and returns the attacked outcome and the
no-model baseline as JSON for asserts.py to judge.
"""

from __future__ import annotations

import json
from typing import Any

from evals.recording import Recorder, eval_model
from evals.redteam.harness import RECORDINGS, Probe, RedTeam, load_attacks

_team: RedTeam | None = None


def _get_team() -> RedTeam:
    global _team
    if _team is None:
        _team = RedTeam(Recorder(eval_model(), RECORDINGS, delay_s=4))
    return _team


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    del prompt, options
    variables = context["vars"]
    attack = next(a for a in load_attacks() if a.id == variables["attack"])
    result = _get_team().run(Probe(attack, variables["scenario"]))
    return {
        "output": json.dumps(
            {
                "probe": result.probe.name,
                "status": result.status,
                "problems": result.problems,
                "attacked": result.attacked,
            },
            ensure_ascii=False,
        ),
        "metadata": {"replayed": result.replayed, "provider": result.attacked["provider"]},
    }
