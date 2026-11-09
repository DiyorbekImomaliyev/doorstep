"""DP-03/DP-04 over HTTP: Doorstep's public key set, a public proof check, and the buyer's proof.

The key set and the check are public and read-only; the buyer's proof needs the buyer's signed
link for that one order. Nothing here writes to the database.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import AppDeps, Services
from app.api.journey import Db
from app.orders.models import Order
from app.proof.attestation import (
    JWKS_PATH,
    MAX_PROOF_CHARS,
    InvalidProof,
    ProofClaims,
    Prover,
    jwks,
    verify_proof,
)
from app.security.links import InvalidLink

BUYER = "buyer"


class ProofIn(BaseModel):
    proof: str = Field(min_length=1, max_length=MAX_PROOF_CHARS)


class ProofCheck(BaseModel):
    valid: bool
    claims: ProofClaims | None
    reason: str | None


class BuyerProof(BaseModel):
    proof: str
    claims: ProofClaims
    check_url: str


def build_proof_router(deps: AppDeps, services: Services) -> APIRouter:
    router = APIRouter()
    prover = Prover(services.receipts, issuer=services.app_base_url, clock=deps.clock)

    @router.get(JWKS_PATH, include_in_schema=False)
    def keys() -> dict[str, list[dict[str, str]]]:
        return jwks(services.receipts)

    @router.post("/api/proof/verify", response_model=ProofCheck)
    def check(body: ProofIn) -> ProofCheck:
        try:
            claims = verify_proof(services.receipts.public_key_b64, body.proof.strip())
        except InvalidProof as exc:
            return ProofCheck(valid=False, claims=None, reason=str(exc))
        return ProofCheck(valid=True, claims=claims, reason=None)

    @router.get("/api/buyer/{token}/proof", response_model=BuyerProof)
    def buyer_proof(token: str, s: Db) -> BuyerProof:
        try:
            order = s.get(Order, services.links.verify(token, scope=BUYER))
        except InvalidLink:
            order = None
        if order is None:
            raise HTTPException(404, "not found")
        proof, claims = prover.prove(s, order)
        return BuyerProof(proof=proof, claims=claims, check_url=prover.check_url(proof))

    return router
