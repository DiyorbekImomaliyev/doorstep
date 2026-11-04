"""PR-01..03: Ed25519-signed, hash-chained receipts and the public verify check."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.proof.models import Receipt
from app.proof.receipts import ReceiptSigner, issue_receipt, verify_chain
from tests.factories import make_case, make_order, make_seller


@pytest.fixture
def signer() -> ReceiptSigner:
    return ReceiptSigner.generate()


def _three(db, signer, clock):
    order = make_order(db, make_seller(db))
    case = make_case(db, order)
    receipts = [
        issue_receipt(db, signer, case_id=case.id, kind=kind, payload={"step": i}, at=clock.now())
        for i, kind in enumerate(["decision", "approval", "settlement"])
    ]
    db.flush()
    return case, receipts


def test_PR_01_each_receipt_is_signed_and_carries_previous_hash(db, signer, clock):
    _, (first, second, third) = _three(db, signer, clock)
    assert first.prev_hash == "0" * 64
    assert second.prev_hash == first.hash
    assert third.prev_hash == second.hash
    assert [r.seq for r in (first, second, third)] == [1, 2, 3]
    for r in (first, second, third):
        assert signer.verify(r.hash, r.signature)


def test_PR_01_chain_spans_cases(db, signer, clock):
    _, receipts = _three(db, signer, clock)
    other = make_case(db, make_order(db, make_seller(db)))
    nxt = issue_receipt(db, signer, case_id=other.id, kind="decision", payload={}, at=clock.now())
    assert nxt.prev_hash == receipts[-1].hash


def test_PR_02_verify_reports_ok_for_untouched_chain(db, signer, clock):
    case, _ = _three(db, signer, clock)
    report = verify_chain(db, signer.public_key_b64, case_id=case.id)
    assert report.status == "ok"
    assert report.checked == 3
    assert report.first_broken_seq is None


def test_PR_02_verify_exposes_no_personal_data(db, signer, clock):
    case, _ = _three(db, signer, clock)
    body = verify_chain(db, signer.public_key_b64, case_id=case.id).model_dump_json()
    for personal in ("Sam", "High Street", "N1 9GU", "PAYER-1"):
        assert personal not in body


@pytest.mark.parametrize("field", ["payload", "prev_hash", "signature"])
def test_PR_03_tampering_breaks_the_chain_at_that_receipt(db, signer, clock, field):
    case, (_, second, _) = _three(db, signer, clock)
    if field == "payload":
        second.payload = {"step": 99}
    elif field == "prev_hash":
        second.prev_hash = "f" * 64
    else:
        second.signature = signer.sign("0" * 64)
    db.flush()
    report = verify_chain(db, signer.public_key_b64, case_id=case.id)
    assert report.status == "broken"
    assert report.first_broken_seq == 2


def test_PR_03_receipt_signed_by_another_key_fails(db, signer, clock):
    case, _ = _three(db, signer, clock)
    other = ReceiptSigner.generate()
    assert verify_chain(db, other.public_key_b64, case_id=case.id).status == "broken"


def test_PR_02_case_without_receipts_reports_not_run(db, signer):
    case = make_case(db, make_order(db, make_seller(db)))
    report = verify_chain(db, signer.public_key_b64, case_id=case.id)
    assert report.status == "not_run"  # PR-08: never "ok" when nothing was checked
    assert db.scalars(select(Receipt)).all() == []


def test_PR_01_signer_round_trips_through_its_seed(signer):
    again = ReceiptSigner.from_seed_b64(signer.seed_b64)
    assert again.public_key_b64 == signer.public_key_b64
