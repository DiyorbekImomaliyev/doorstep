"""SE-05: the routes that create data or spend model calls are capped per client and in total.

Found by the Nov 7 review: anyone could seed shops, start checkouts, file reports or ask questions
in a loop and stall or drain the live app. A per-client cap stops one caller; a total cap per route
bounds the damage even when the client address is faked.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.security.ratelimit import RateLimiter, client_key, limited


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_SE_05_one_client_is_capped_and_others_are_not():
    clock = Clock()
    limiter = RateLimiter(per_client=2, total=10, window_s=60, now=clock)
    assert limiter.allow("a") and limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")


def test_SE_05_the_total_cap_holds_whatever_the_client_says():
    limiter = RateLimiter(per_client=5, total=3, window_s=60, now=Clock())
    assert [limiter.allow(f"fake-{i}") for i in range(4)] == [True, True, True, False]


def test_SE_05_the_window_slides():
    clock = Clock()
    limiter = RateLimiter(per_client=1, total=10, window_s=60, now=clock)
    assert limiter.allow("a") and not limiter.allow("a")
    clock.t += 61
    assert limiter.allow("a")


def test_SE_05_a_refused_call_takes_no_slot():
    clock = Clock()
    limiter = RateLimiter(per_client=1, total=2, window_s=60, now=clock)
    assert limiter.allow("a")
    assert not limiter.allow("a")  # refused: must not count toward the total
    assert limiter.allow("b")


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"x-forwarded-for": "203.0.113.7, 10.0.0.1"}, "203.0.113.7"),
        ({"x-forwarded-for": " 198.51.100.2 "}, "198.51.100.2"),
        ({}, "testclient"),
    ],
)
def test_SE_05_the_client_is_the_first_forwarded_address(headers, expected):
    app = FastAPI()

    @app.get("/who")
    def who(key: str = Depends(client_key)) -> dict[str, str]:
        return {"key": key}

    assert TestClient(app).get("/who", headers=headers).json() == {"key": expected}


def test_SE_05_a_capped_route_answers_429_with_retry_after():
    app = FastAPI()
    limiter = RateLimiter(per_client=1, total=10, window_s=120, now=Clock())

    @app.post("/seed", dependencies=[Depends(limited(limiter))])
    def seed() -> dict[str, bool]:
        return {"ok": True}

    api = TestClient(app)
    assert api.post("/seed").status_code == 200
    refused = api.post("/seed")
    assert refused.status_code == 429
    assert refused.headers["retry-after"] == "120"
    assert "Try again" in refused.json()["detail"]
