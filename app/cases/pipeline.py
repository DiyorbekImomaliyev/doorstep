"""The case pipeline behind the buyer's two buttons (BC-02, BC-03).

"Got it" records possession proof and confirms the order. "Not here" (or "damaged", "empty box")
opens the order's single case and runs it straight through:

    open_case -> GATHERING_EVIDENCE -> assemble_signals -> engine.assess (code, no model)
    -> PolicyService.decide (code; the only path to money) -> explainer (words only)
    -> signed receipt (PR-01)

The explainer runs after the decision and can never change it (hard rule 5 of SPEC section 4).
"""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.explainer import ExplainContext, Explainer
from app.cases.engine import ClaimReason, Confidence, FailurePoint, assess
from app.cases.signals import EvidenceKind, assemble_signals
from app.clock import Clock
from app.config import Settings
from app.money import Currency, Money
from app.orders.enums import CaseState, Fix, OrderState
from app.orders.models import Decision, Evidence, Order, Shipment
from app.orders.state import CaseAlreadyOpen, open_case, transition_case, transition_order
from app.policy.rules import RuleBook
from app.policy.service import CaseFacts, PolicyService
from app.proof.receipts import ReceiptSigner, issue_receipt

# Orders a buyer can report on: the parcel has left the seller.
_REPORTABLE = frozenset(
    {
        OrderState.IN_TRANSIT,
        OrderState.EXCEPTION,
        OrderState.DELIVERED,
        OrderState.PICKED_UP,
        OrderState.CONFIRMED,
    }
)
_CONFIRMABLE = frozenset(
    {
        OrderState.SHIPPED,
        OrderState.IN_TRANSIT,
        OrderState.EXCEPTION,
        OrderState.DELIVERED,
        OrderState.PICKED_UP,
    }
)


class BuyerActionError(Exception):
    """The buyer asked for something the order's state does not allow."""


class CaseReport(BaseModel):
    case_id: str
    decision_id: str
    route: CaseState
    failure_point: FailurePoint
    confidence: Confidence
    rule_id: str | None
    rule_version: str | None
    refund_amount_minor: int | None
    currency: str
    explanation: str
    buyer_message: str
    claim_letter: str | None
    receipt_seq: int


class CasePipeline:
    def __init__(
        self,
        session: Session,
        rulebook: RuleBook,
        clock: Clock,
        settings: Settings,
        explainer: Explainer,
        signer: ReceiptSigner,
    ) -> None:
        self.session = session
        self.rulebook = rulebook
        self.clock = clock
        self.settings = settings
        self.explainer = explainer
        self.signer = signer

    # ---- BC-02 ---------------------------------------------------------------

    def confirm_received(self, order: Order, *, actor: str = "buyer") -> None:
        """Idempotent: a second tap on a confirmed order changes nothing."""
        s = self.session
        state = OrderState(order.state)
        if state == OrderState.CONFIRMED:
            return
        if state not in _CONFIRMABLE:
            raise BuyerActionError(f"order {order.id} cannot be confirmed in state {state}")
        if state in (OrderState.SHIPPED, OrderState.IN_TRANSIT, OrderState.EXCEPTION):
            # The buyer holds the parcel before the carrier's delivered scan arrived.
            transition_order(s, order, OrderState.DELIVERED, actor=actor, reason="buyer got it")
        transition_order(s, order, OrderState.CONFIRMED, actor=actor, reason="buyer got it")
        s.add(
            Evidence(
                order_id=order.id,
                kind=EvidenceKind.BUYER_CONFIRMATION.value,
                ai_result={"source": actor},
                created_at=self.clock.now(),
            )
        )
        s.flush()

    # ---- BC-03 ---------------------------------------------------------------

    def report(
        self, order: Order, reason: ClaimReason, *, reported_by: str, note: str = ""
    ) -> CaseReport:
        s = self.session
        if OrderState(order.state) not in _REPORTABLE:
            raise BuyerActionError(f"order {order.id} cannot be reported in state {order.state}")
        try:
            case = open_case(
                s, order, reported_by=reported_by, reason=reason.value, actor=reported_by
            )
        except CaseAlreadyOpen as exc:
            raise BuyerActionError(str(exc)) from exc
        transition_case(
            s, case, CaseState.GATHERING_EVIDENCE, actor="pipeline", reason="gathering evidence"
        )
        now = self.clock.now()
        signals, proofs = assemble_signals(s, order, reason, now, self.settings)
        result = assess(signals)
        amount = Money(amount_minor=order.amount_minor, currency=Currency(order.currency))
        fix = Fix.REFUND
        policy = PolicyService(s, self.rulebook, self.clock).decide(
            case,
            result,
            CaseFacts(
                buyer_country=order.buyer_country,
                proofs=proofs,
                claim_amount=amount,
                preferred_fix=fix,
            ),
        )
        decision = s.scalars(
            select(Decision)
            .where(Decision.case_id == case.id)
            .order_by(Decision.created_at.desc())
            .limit(1)
        ).one()

        shipment = s.scalars(select(Shipment).where(Shipment.order_id == order.id)).first()
        rule = self.rulebook.select(order.buyer_country)
        liability = decision.signals.get("liability") or {}
        explanation = self.explainer.explain(
            ExplainContext(
                order_ref=order.id[:8].upper(),
                item=order.item_description or "",
                failure_point=result.failure_point,
                confidence=result.confidence.value,
                route=policy.route,
                rule_id=policy.rule_id,
                rule_version=policy.rule_version,
                rule_source=rule.source if rule is not None else "",
                liable_party=liability.get("liable_party"),
                refund=policy.refund_amount,
                reship=policy.fix == Fix.RESHIP,
                reasons=policy.reasons,
                carrier=shipment.carrier if shipment else "",
                tracking_number=shipment.tracking_number if shipment else "",
                buyer_note=note,
                disclaimer=rule.disclaimer
                if rule is not None and rule.disclaimer
                else "Information, not legal advice. The seller decides.",
            )
        )
        decision.explanation = explanation.summary
        decision.model_outputs = {
            **(decision.model_outputs or {}),
            "explainer": explanation.model_dump(mode="json"),
        }
        s.flush()

        receipt = issue_receipt(
            s,
            self.signer,
            case_id=case.id,
            kind="decision",
            payload={
                "decision_id": decision.id,
                "route": policy.route.value,
                "failure_point": result.failure_point.value,
                "confidence": result.confidence.value,
                "rule_id": policy.rule_id,
                "rule_version": policy.rule_version,
                "refund_amount_minor": decision.refund_amount_minor,
                "currency": decision.currency,
                "reason_codes": list(decision.reason_codes),
                "evidence_fingerprint": decision.evidence_fingerprint,
            },
            at=now,
        )
        return CaseReport(
            case_id=case.id,
            decision_id=decision.id,
            route=policy.route,
            failure_point=result.failure_point,
            confidence=result.confidence,
            rule_id=policy.rule_id,
            rule_version=policy.rule_version,
            refund_amount_minor=decision.refund_amount_minor,
            currency=order.currency,
            explanation=explanation.summary,
            buyer_message=explanation.buyer_message,
            claim_letter=explanation.claim_letter,
            receipt_seq=receipt.seq,
        )
