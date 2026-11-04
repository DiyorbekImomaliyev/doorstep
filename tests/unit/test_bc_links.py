"""BC-01 / BC-04 (and MC-01 / MC-02 reuse): signed, order-scoped links."""

from __future__ import annotations

import pytest

from app.security.links import InvalidLink, LinkSigner


@pytest.fixture
def links(clock) -> LinkSigner:
    return LinkSigner(secret="test-secret-0123456789abcdef", clock=clock)


def test_BC_01_signed_link_round_trips(links):
    token = links.issue("order-1", scope="buyer")
    assert links.verify(token, scope="buyer") == "order-1"


def test_BC_01_tampered_link_is_rejected(links):
    token = links.issue("order-1", scope="buyer")
    body, sig = token.split(".")
    forged = links.issue("order-2", scope="buyer").split(".")[0] + "." + sig
    with pytest.raises(InvalidLink):
        links.verify(forged, scope="buyer")
    with pytest.raises(InvalidLink):
        links.verify(body + ".AAAA", scope="buyer")


@pytest.mark.parametrize("token", ["", "abc", "a.b.c", "!!!.???"])
def test_BC_01_garbage_link_is_rejected(links, token):
    with pytest.raises(InvalidLink):
        links.verify(token, scope="buyer")


def test_BC_01_expired_link_is_rejected(links, clock):
    token = links.issue("order-1", scope="buyer", ttl_s=60)
    clock.advance(61)
    with pytest.raises(InvalidLink):
        links.verify(token, scope="buyer")


def test_BC_04_link_for_one_scope_cannot_be_used_for_another(links):
    token = links.issue("order-1", scope="buyer")
    with pytest.raises(InvalidLink):
        links.verify(token, scope="mcp")


def test_BC_04_link_signed_with_another_secret_is_rejected(links, clock):
    other = LinkSigner(secret="another-secret-0123456789abcdef", clock=clock)
    with pytest.raises(InvalidLink):
        links.verify(other.issue("order-1", scope="buyer"), scope="buyer")


def test_BC_01_short_secret_is_refused(clock):
    with pytest.raises(ValueError):
        LinkSigner(secret="short", clock=clock)
