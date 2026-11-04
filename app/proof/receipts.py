"""Signed receipts for money decisions (PR-01, PR-02, PR-03).

Each receipt stores `hash = sha256(canonical({seq, kind, case_id, payload, prev_hash, at}))` and an
Ed25519 signature of that hash. `prev_hash` is the previous receipt's hash, so editing any stored
receipt breaks verification at that receipt. Payloads hold ids, amounts and rule ids only, never
personal data, because `GET /cases/{id}/verify` is public.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.proof.models import Receipt

GENESIS = "0" * 64


class ReceiptSigner:
    def __init__(self, key: Ed25519PrivateKey) -> None:
        self._key = key
        self._public = key.public_key()

    @classmethod
    def generate(cls) -> ReceiptSigner:
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_seed_b64(cls, seed_b64: str) -> ReceiptSigner:
        return cls(Ed25519PrivateKey.from_private_bytes(base64.b64decode(seed_b64)))

    @property
    def seed_b64(self) -> str:
        raw = self._key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        return base64.b64encode(raw).decode("ascii")

    @property
    def public_key_b64(self) -> str:
        raw = self._public.public_bytes(Encoding.Raw, PublicFormat.Raw)
        return base64.b64encode(raw).decode("ascii")

    def sign(self, digest_hex: str) -> str:
        return base64.b64encode(self._key.sign(digest_hex.encode("ascii"))).decode("ascii")

    def verify(self, digest_hex: str, signature_b64: str) -> bool:
        return _verify(self._public, digest_hex, signature_b64)


def _verify(public: Ed25519PublicKey, digest_hex: str, signature_b64: str) -> bool:
    try:
        public.verify(base64.b64decode(signature_b64), digest_hex.encode("ascii"))
    except (InvalidSignature, ValueError):
        return False
    return True


def _digest(
    *, seq: int, kind: str, case_id: str, payload: dict[str, Any], prev_hash: str, at: datetime
) -> str:
    canonical = json.dumps(
        {
            "seq": seq,
            "kind": kind,
            "case_id": case_id,
            "payload": payload,
            "prev_hash": prev_hash,
            "at": at.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def issue_receipt(
    session: Session,
    signer: ReceiptSigner,
    *,
    case_id: str,
    kind: str,
    payload: dict[str, Any],
    at: datetime,
) -> Receipt:
    """Append one receipt to the chain. Callers hold the DB transaction; `seq` is unique, so two
    concurrent writers can never both extend the chain from the same receipt."""
    last = session.scalars(select(Receipt).order_by(Receipt.seq.desc()).limit(1)).first()
    seq = 1 if last is None else last.seq + 1
    prev_hash = GENESIS if last is None else last.hash
    digest = _digest(
        seq=seq, kind=kind, case_id=case_id, payload=payload, prev_hash=prev_hash, at=at
    )
    receipt = Receipt(
        seq=seq,
        case_id=case_id,
        kind=kind,
        payload=payload,
        prev_hash=prev_hash,
        hash=digest,
        signature=signer.sign(digest),
        created_at=at,
    )
    session.add(receipt)
    session.flush()
    return receipt


class ReceiptView(BaseModel):
    seq: int
    kind: str
    hash: str
    valid: bool


class VerifyReport(BaseModel):
    """`status` is ok only if every receipt up to the case's last one was checked (PR-08)."""

    status: Literal["ok", "broken", "not_run"]
    checked: int
    first_broken_seq: int | None = None
    reason: str | None = None
    receipts: list[ReceiptView] = []


def verify_chain(session: Session, public_key_b64: str, *, case_id: str) -> VerifyReport:
    """Recompute the chain from the first receipt up to this case's last one (PR-02)."""
    last_seq = session.scalar(select(func.max(Receipt.seq)).where(Receipt.case_id == case_id))
    if last_seq is None:
        return VerifyReport(status="not_run", checked=0, reason="no receipts for this case")
    public = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
    rows = session.scalars(
        select(Receipt).where(Receipt.seq <= last_seq).order_by(Receipt.seq)
    ).all()
    prev = GENESIS
    views: list[ReceiptView] = []
    broken: tuple[int, str] | None = None
    for expected_seq, r in enumerate(rows, start=1):
        digest = _digest(
            seq=r.seq,
            kind=r.kind,
            case_id=r.case_id,
            payload=r.payload,
            prev_hash=r.prev_hash,
            at=r.created_at,
        )
        problem = None
        if r.seq != expected_seq:
            problem = "missing receipt in the chain"
        elif r.prev_hash != prev:
            problem = "previous-hash link does not match"
        elif digest != r.hash:
            problem = "content does not match its hash"
        elif not _verify(public, r.hash, r.signature):
            problem = "signature does not verify"
        if problem and broken is None:
            broken = (r.seq, problem)
        if r.case_id == case_id:
            views.append(ReceiptView(seq=r.seq, kind=r.kind, hash=r.hash, valid=problem is None))
        prev = r.hash
    if broken:
        return VerifyReport(
            status="broken",
            checked=len(rows),
            first_broken_seq=broken[0],
            reason=broken[1],
            receipts=views,
        )
    return VerifyReport(status="ok", checked=len(rows), receipts=views)
