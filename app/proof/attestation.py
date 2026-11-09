"""Signed delivery proof (DP-01..05): delivery facts a buyer's AI assistant, or anyone, can check.

Agent-payment protocols sign what the buyer asked for and paid; none signs what happened at the
door. Doorstep signs the delivery facts it holds as a compact JWS (RFC 7515) with EdDSA
(RFC 8037), using the Ed25519 key that signs its receipts, and publishes the public key as a JWK
set at /.well-known/jwks.json. Anyone can check a proof without trusting whoever passed it on.

Facts only, never a conclusion: the carrier's delivered scan, the possession seal scanned after
it (SL-08), the delivered-scan audit verdict, the case route and rule, and the head of the case's
receipt chain, which ties the proof to Doorstep's signed decisions. It never says the buyer
received the parcel, and it holds no address, name, postcode, tracking number or buyer words
(hard rules 7-9). It reads and signs; it moves no money and changes no state.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.orders.models import Case, Decision, Order, Shipment, TrackingEvent
from app.proof.models import Receipt
from app.proof.receipts import ReceiptSigner
from app.proof.seal import proof_scan

TYP = "delivery-proof+jwt"
ALG = "EdDSA"
JWKS_PATH = "/.well-known/jwks.json"
CHECK_PATH = "/proof"
MAX_PROOF_CHARS = 8000  # a proof is about 1 KB; anything far longer is not one


class InvalidProof(ValueError):
    """The text is not a proof Doorstep signed, or it was changed after signing."""


class ProofClaims(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    iss: str  # Doorstep's address
    sub: str  # the order reference, as on the buyer's page and the receipts
    iat: int  # when it was signed, seconds since 1970 (UTC)
    carrier: str | None
    delivered_scan_at: str | None  # the carrier's first "delivered" scan
    seal_scanned_after_delivery_at: str | None  # SL-08: only a scan after that delivered scan
    scan_audit: str | None  # Doorstep's audit of the delivered scan: consistent, contradicted…
    case_state: str | None
    rule: str | None  # "uk_possession_v1@2026-11-04"
    receipt_head: str | None  # hash of the case's latest signed receipt
    statement: str  # the facts above, in plain words


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64url(text: str) -> bytes:
    if not text or "=" in text:
        raise InvalidProof("not base64url")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise InvalidProof("not base64url") from None


def _json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _jwk(public_key_b64: str) -> dict[str, str]:
    x = _b64url(base64.b64decode(public_key_b64))
    # RFC 7638: the thumbprint hashes the required members only, in lexical order, no spaces.
    kid = _b64url(hashlib.sha256(_json({"crv": "Ed25519", "kty": "OKP", "x": x})).digest())
    return {"kty": "OKP", "crv": "Ed25519", "x": x, "kid": kid, "alg": ALG, "use": "sig"}


def jwks(signer: ReceiptSigner) -> dict[str, list[dict[str, str]]]:
    return {"keys": [_jwk(signer.public_key_b64)]}


def _statement(delivered_at: datetime | None, sealed: bool, audit: str | None) -> str:
    if delivered_at is None:
        parts = ["The carrier has not scanned the parcel as delivered."]
    else:
        parts = [
            f"The carrier scanned the parcel as delivered at {delivered_at:%H:%M on %d %b %Y} UTC."
        ]
    if sealed:
        parts.append("The seal packed inside the parcel was scanned after that delivered scan.")
    if audit == "consistent":
        parts.append("Doorstep's audit found the carrier's delivered scan consistent.")
    elif audit == "contradicted":
        parts.append(
            "Doorstep's audit found the carrier's own data contradicts its delivered scan."
        )
    return " ".join(parts)


def build_claims(session: Session, order: Order, *, issuer: str, now: datetime) -> ProofClaims:
    shipments = select(Shipment.id).where(Shipment.order_id == order.id)
    carrier = session.scalar(
        select(Shipment.carrier).where(Shipment.order_id == order.id).order_by(Shipment.created_at)
    )
    delivered_at = session.scalar(
        select(func.min(TrackingEvent.occurred_at))
        .where(TrackingEvent.shipment_id.in_(shipments))
        .where(TrackingEvent.status == "DELIVERED")
    )
    seal = proof_scan(session, order.id)
    case = session.scalars(
        select(Case).where(Case.order_id == order.id).order_by(Case.opened_at.desc())
    ).first()
    decision = head = None
    if case is not None:
        decision = session.scalars(
            select(Decision).where(Decision.case_id == case.id).order_by(Decision.created_at.desc())
        ).first()
        head = session.scalar(
            select(Receipt.hash).where(Receipt.case_id == case.id).order_by(Receipt.seq.desc())
        )
    audit = ((decision.signals or {}).get("scan_audit") or {}).get("verdict") if decision else None
    rule = f"{decision.rule_id}@{decision.rule_version}" if decision and decision.rule_id else None
    return ProofClaims(
        iss=issuer,
        sub=order.id[:8].upper(),
        iat=int(now.timestamp()),
        carrier=carrier,
        delivered_scan_at=delivered_at.isoformat() if delivered_at else None,
        seal_scanned_after_delivery_at=seal.created_at.isoformat() if seal else None,
        scan_audit=audit,
        case_state=case.state if case else None,
        rule=rule,
        receipt_head=head,
        statement=_statement(delivered_at, seal is not None, audit),
    )


def sign_proof(signer: ReceiptSigner, claims: ProofClaims) -> str:
    header = {"alg": ALG, "kid": _jwk(signer.public_key_b64)["kid"], "typ": TYP}
    signing_input = f"{_b64url(_json(header))}.{_b64url(_json(claims.model_dump()))}"
    return f"{signing_input}.{_b64url(signer.sign_bytes(signing_input.encode('ascii')))}"


def verify_proof(public_key_b64: str, token: str) -> ProofClaims:
    """The claims of a proof this key signed, or InvalidProof. Only EdDSA is accepted."""
    parts = token.split(".")
    if len(token) > MAX_PROOF_CHARS or len(parts) != 3:
        raise InvalidProof("not a signed proof")
    head_b64, body_b64, sig_b64 = parts
    try:
        header = json.loads(_unb64url(head_b64))
    except ValueError:
        raise InvalidProof("the header is not readable") from None
    if not isinstance(header, dict) or header.get("alg") != ALG or header.get("typ") != TYP:
        raise InvalidProof("not a Doorstep delivery proof")
    public = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
    try:
        public.verify(_unb64url(sig_b64), f"{head_b64}.{body_b64}".encode("ascii"))
    except InvalidSignature:
        raise InvalidProof(
            "the signature does not match: it was changed or not signed by Doorstep"
        ) from None
    try:
        return ProofClaims.model_validate_json(_unb64url(body_b64))
    except ValidationError:
        raise InvalidProof("the signed facts are not readable") from None


class Prover:
    """Builds and signs the proof for one order; injected where a proof is offered."""

    def __init__(self, signer: ReceiptSigner, *, issuer: str, clock: Clock) -> None:
        self.signer = signer
        self.issuer = issuer.rstrip("/")
        self.clock = clock

    @property
    def keys_url(self) -> str:
        return f"{self.issuer}{JWKS_PATH}"

    def check_url(self, proof: str) -> str:
        # In the fragment, so the proof is never sent to a server just by opening the link.
        return f"{self.issuer}{CHECK_PATH}#{proof}"

    def prove(self, session: Session, order: Order) -> tuple[str, ProofClaims]:
        claims = build_claims(session, order, issuer=self.issuer, now=self.clock.now())
        return sign_proof(self.signer, claims), claims
