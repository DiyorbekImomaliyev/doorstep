"""The case pipeline behind the buyer's two buttons (BC-02, BC-03).

"Got it" records possession proof and confirms the order. "Not here" (or "damaged", "empty box")
opens the order's single case and runs it straight through:

    open_case -> GATHERING_EVIDENCE -> assemble_signals -> scan audit -> engine.assess (code)
    -> PolicyService.decide (code; the only path to money) -> explainer (words only)
    -> signed receipt (PR-01)

The explainer runs after the decision and can never change it: AI suggests; code and people
decide money.
"""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.explainer import ExplainContext, Explainer, Explanation, template
from app.cases.counterfactual import counterfactual
from app.cases.engine import ClaimReason, Confidence, FailurePoint, assess
from app.cases.recovery import prepare_recovery
from app.cases.scan_audit import audit_order
from app.cases.signals import EvidenceKind, assemble_signals
from app.clock import Clock
from app.config import Settings
from app.money import Currency, Money
from app.orders.enums import CaseState, Fix, MoneyActionStatus, OrderState
from app.orders.models import Case, Decision, Evidence, MoneyAction, Order, Shipment
from app.orders.state import CaseAlreadyOpen, open_case, transition_case, transition_order
from app.policy.rules import RuleBook
from app.policy.service import CaseFacts, PolicyService, PolicyStateError
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
        *,
        ai_claims: bool = True,  # RC-08: False for the demo seed (fact-only claim packs)
    ) -> None:
        self.ai_claims = ai_claims
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

    def _store_explanation(self, decision: Decision, explanation: Explanation) -> None:
        decision.explanation = explanation.summary
        decision.model_outputs = {
            **(decision.model_outputs or {}),
            "explainer": explanation.model_dump(mode="json"),
        }
        self.session.flush()

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
        return self._decide(order, case, reason, note)

    def redecide(self, case: Case, *, actor: str) -> CaseReport:
        """A person asks for a fresh decision on a held case.

        HOLD_FOR_REVIEW -> GATHERING_EVIDENCE, then the same steps as a report,
        on the evidence as it is now. A refund the pre-refund re-check blocked, or one that
        failed, otherwise has no way forward. The new decision gets its own money action, and that
        action meets the same live PayPal re-check, so a refund that did land is never repeated.
        """
        if CaseState(case.state) != CaseState.HOLD_FOR_REVIEW:
            raise PolicyStateError(f"case {case.id} in {case.state} cannot be decided again")
        queued = self.session.scalar(
            select(MoneyAction.id).where(
                MoneyAction.case_id == case.id,
                MoneyAction.status == MoneyActionStatus.QUEUED.value,
            )
        )
        if queued is not None:  # it would land under a newer decision
            raise PolicyStateError(f"case {case.id}: a refund is on its way; wait for it to land")
        order = self.session.get(Order, case.order_id)
        assert order is not None
        transition_case(
            self.session,
            case,
            CaseState.GATHERING_EVIDENCE,
            actor=actor,
            reason="a person asked for a fresh decision",
        )
        return self._decide(order, case, ClaimReason(case.reason), "")

    def _decide(self, order: Order, case: Case, reason: ClaimReason, note: str) -> CaseReport:
        s = self.session
        now = self.clock.now()
        signals, proofs = assemble_signals(s, order, reason, now, self.settings)
        scan_audit = audit_order(s, order, proofs, now)  # SA-01: the carrier's own data
        result = assess(signals.model_copy(update={"scan_contradicted": scan_audit.contradicted}))
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
                scan_contradicted=scan_audit.contradicted,
            ),
        )
        decision = s.scalars(
            select(Decision)
            .where(Decision.case_id == case.id)
            .order_by(Decision.created_at.desc())
            .limit(1)
        ).one()
        what_if = counterfactual(  # CF-01: stored on the decision, replayable with its rule
            self.rulebook.select(order.buyer_country),
            proofs,
            scan_contradicted=scan_audit.contradicted,
        )
        decision.signals = {
            **decision.signals,
            "scan_audit": scan_audit.model_dump(mode="json"),
            "counterfactual": what_if.model_dump(mode="json"),
        }

        shipment = s.scalars(select(Shipment).where(Shipment.order_id == order.id)).first()
        rule = self.rulebook.select(order.buyer_country)
        liability = decision.signals.get("liability") or {}
        ctx = ExplainContext(
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
        # The decision commits with the template wording, so no model call runs
        # while the order, case and seller rows are locked; the model's words replace it after.
        explanation = template(ctx)
        self._store_explanation(decision, explanation)
        if self.rulebook.carriers is not None:  # RC-01: claim back from a carrier at fault
            prepare_recovery(
                s, case, decision, self.rulebook.carriers, self.clock, draft=self.ai_claims
            )

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
                "scan_audit": scan_audit.verdict.value,
            },
            at=now,
        )
        if self.explainer.llm is not None:
            s.commit()
            explanation = self.explainer.explain(ctx)
            self._store_explanation(decision, explanation)
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
