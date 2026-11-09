"""SE-06: the seal page's "Something's wrong" shares the report cap.

From the Nov 9 gap audit: POST /api/seal/{token}/report opens a case and can spend a model call,
like the buyer page's "Not here", but it had no rate limit. It now draws on the same per-client
budget (20 an hour), so the two report routes together cannot be looped.
"""

from __future__ import annotations

from tests.unit.test_dp_delivery_proof import signer, web  # noqa: F401 - fixtures


def test_SE_06_the_seal_report_is_capped_with_not_here(web):  # noqa: F811
    api, _ = web
    answers = [
        api.post("/api/seal/not-a-token/report", json={"reason": "damaged"}).status_code
        for _ in range(10)
    ] + [
        api.post("/api/buyer/not-a-token/not-here", json={"reason": "not_received"}).status_code
        for _ in range(10)
    ]
    assert 429 not in answers  # 20 reports in the hour, across both routes
    refused = api.post("/api/seal/not-a-token/report", json={"reason": "damaged"})
    assert refused.status_code == 429 and refused.headers["retry-after"]
