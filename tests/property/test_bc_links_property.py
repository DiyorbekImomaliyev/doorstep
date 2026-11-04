"""BC-01 / SE-04: any text given as a link token is either valid or InvalidLink, never a
crash (found by Schemathesis: a non-ASCII token raised a 500)."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from app.clock import SystemClock
from app.security.links import InvalidLink, LinkSigner

SIGNER = LinkSigner("p" * 32, SystemClock())


@given(st.text(max_size=200))
def test_BC_01_any_token_text_is_valid_or_invalid_link(token):
    try:
        SIGNER.verify(token, scope="buyer")
    except InvalidLink:
        pass


@given(st.text(min_size=1, max_size=40), st.text(min_size=1, max_size=40))
def test_BC_01_dotted_garbage_is_invalid_link(body, sig):
    try:
        SIGNER.verify(f"{body}.{sig}", scope="buyer")
    except InvalidLink:
        pass
