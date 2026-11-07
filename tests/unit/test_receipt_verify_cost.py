"""The public receipt check stays exact and gets cheaper on repeat calls.

Every call still recomputes every hash and link from the first receipt (PR-08). Checking an Ed25519
signature is a pure function of (key, hash, signature) and was 78% of the time (135 us each), so its
result is remembered: any changed byte is a different key and is checked afresh.
"""

from __future__ import annotations

from app.proof import receipts as rc
from app.proof.receipts import ReceiptSigner, issue_receipt, verify_chain
from tests.factories import make_case, make_order, make_seller


def _chain(db, signer, clock, n=4):
    case = make_case(db, make_order(db, make_seller(db)))
    rows = [
        issue_receipt(
            db, signer, case_id=case.id, kind="decision", payload={"i": i}, at=clock.now()
        )
        for i in range(n)
    ]
    db.flush()
    return case, rows


def test_a_repeat_check_verifies_no_signature_twice(db, clock, monkeypatch):
    signer = ReceiptSigner.generate()
    case, _ = _chain(db, signer, clock)
    assert verify_chain(db, signer.public_key_b64, case_id=case.id).status == "ok"
    calls = []
    real = rc._verify
    monkeypatch.setattr(rc, "_verify", lambda *a: calls.append(a) or real(*a))
    report = verify_chain(db, signer.public_key_b64, case_id=case.id)
    assert (report.status, report.checked, calls) == ("ok", 4, [])


def test_a_changed_signature_is_checked_afresh_after_a_good_run(db, clock):
    signer = ReceiptSigner.generate()
    case, rows = _chain(db, signer, clock)
    assert verify_chain(db, signer.public_key_b64, case_id=case.id).status == "ok"
    rows[1].signature = signer.sign("0" * 64)
    db.flush()
    report = verify_chain(db, signer.public_key_b64, case_id=case.id)
    assert (report.status, report.first_broken_seq) == ("broken", rows[1].seq)


def test_a_changed_payload_still_breaks_after_a_good_run(db, clock):
    signer = ReceiptSigner.generate()
    case, rows = _chain(db, signer, clock)
    assert verify_chain(db, signer.public_key_b64, case_id=case.id).status == "ok"
    rows[2].payload = {"i": 99}
    db.flush()
    report = verify_chain(db, signer.public_key_b64, case_id=case.id)
    assert (report.status, report.first_broken_seq) == ("broken", rows[2].seq)


def test_another_key_never_reuses_a_remembered_answer(db, clock):
    signer = ReceiptSigner.generate()
    case, _ = _chain(db, signer, clock)
    assert verify_chain(db, signer.public_key_b64, case_id=case.id).status == "ok"
    other = ReceiptSigner.generate()
    assert verify_chain(db, other.public_key_b64, case_id=case.id).status == "broken"
