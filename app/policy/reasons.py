"""Machine reason codes for policy decisions and blocked refunds.

Code decides on Reason values and tests assert them. The human sentences live only in MESSAGES,
so wording can change without touching logic or tests. The table is excluded from mutation
testing (`# pragma: no mutate`): a reworded sentence is not a logic change.
"""

from __future__ import annotations

from enum import StrEnum


class Reason(StrEnum):
    # Routes chosen by PolicyService.decide (PO-13 order of checks).
    OPEN_DISPUTE = "open_dispute"
    CONTRADICTION = "contradiction"
    LOW_CONFIDENCE = "low_confidence"
    REPEAT_CLAIMANT = "repeat_claimant"
    NO_COMPLETED_CAPTURE = "no_completed_capture"
    NO_RULE_SET = "no_rule_set"
    LIABILITY_RULE = "liability_rule"  # informational: which rule decided liability
    LIABILITY_UNDETERMINED = "liability_undetermined"
    POSSESSION_PROVEN = "possession_proven"
    WOULD_BLAME_BUYER = "would_blame_buyer"
    RESHIP = "reship"
    NOTHING_LEFT_TO_REFUND = "nothing_left_to_refund"
    DAILY_CAP_REACHED = "daily_cap_reached"
    OVER_AUTO_APPROVE_LIMIT = "over_auto_approve_limit"
    AUTO_REFUND = "auto_refund"
    SELLER_RULE = "seller_rule"
    SCAN_CONTRADICTED = "scan_contradicted"  # informational: SA-04 removed the delivered scan
    # Pre-refund re-check refusals (PO-12).
    CASE_FINISHED = "case_finished"
    DISPUTE_OPENED = "dispute_opened"
    DISPUTE_CHECK_UNAVAILABLE = "dispute_check_unavailable"
    UNMATCHED_DISPUTE = "unmatched_dispute"
    DISPUTE_CHECK_REFUSED = "dispute_check_refused"
    EVIDENCE_CHANGED = "evidence_changed"
    NO_PAYPAL_CAPTURE = "no_paypal_capture"
    CAPTURE_CURRENCY_MISMATCH = "capture_currency_mismatch"
    CAPTURE_NOT_REFUNDABLE = "capture_not_refundable"
    UNKNOWN_REFUND_AT_PAYPAL = "unknown_refund_at_paypal"
    WOULD_EXCEED_CAPTURE = "would_exceed_capture"


MESSAGES: dict[Reason, str] = {  # pragma: no mutate
    Reason.OPEN_DISPUTE: "open PayPal dispute: submit evidence instead of refunding",  # pragma: no mutate
    Reason.CONTRADICTION: "signals contradict the claim: human review",  # pragma: no mutate
    Reason.LOW_CONFIDENCE: "low confidence: human review",  # pragma: no mutate
    Reason.REPEAT_CLAIMANT: (  # pragma: no mutate
        "buyer has {threshold}+ claims with this seller in {days} days: human review"  # pragma: no mutate
    ),  # pragma: no mutate
    Reason.NO_COMPLETED_CAPTURE: "no completed PayPal capture to refund: human review",  # pragma: no mutate
    Reason.NO_RULE_SET: "no rule set matches this buyer: human review",  # pragma: no mutate
    Reason.LIABILITY_RULE: "{rule_id} ({rule_version}): liable party {liable_party}",  # pragma: no mutate
    Reason.LIABILITY_UNDETERMINED: "liability undetermined: human review",  # pragma: no mutate
    Reason.POSSESSION_PROVEN: (  # pragma: no mutate
        "possession proven: the seller decides; the buyer is never denied"  # pragma: no mutate
    ),  # pragma: no mutate
    Reason.WOULD_BLAME_BUYER: "outcome would blame the buyer: the seller decides",  # pragma: no mutate
    Reason.RESHIP: "reship: seller confirms, no money moves",  # pragma: no mutate
    Reason.NOTHING_LEFT_TO_REFUND: "nothing left to refund on this capture: human review",  # pragma: no mutate
    Reason.DAILY_CAP_REACHED: "daily refund cap {cap} reached: seller approves",  # pragma: no mutate
    Reason.OVER_AUTO_APPROVE_LIMIT: "over auto-approve limit {limit}: seller approves",  # pragma: no mutate
    Reason.AUTO_REFUND: "at or under auto-approve limit {limit}: automatic refund",  # pragma: no mutate
    Reason.SELLER_RULE: "over auto-approve limit {limit}, within your autopilot rule up to {rule_max}: automatic refund",  # pragma: no mutate
    Reason.SCAN_CONTRADICTED: (  # pragma: no mutate
        "the carrier's own data contradicts its delivered scan: not proof of delivery"  # pragma: no mutate
    ),  # pragma: no mutate
    Reason.CASE_FINISHED: "case is {state}: no money moves",  # pragma: no mutate
    Reason.DISPUTE_OPENED: "a PayPal dispute opened after the decision",  # pragma: no mutate
    Reason.DISPUTE_CHECK_UNAVAILABLE: (  # pragma: no mutate
        "the order is too old for PayPal to list its disputes: human review"  # pragma: no mutate
    ),  # pragma: no mutate
    Reason.UNMATCHED_DISPUTE: (  # pragma: no mutate
        "PayPal shows an open dispute it does not tie to a payment: human review"  # pragma: no mutate
    ),  # pragma: no mutate
    Reason.DISPUTE_CHECK_REFUSED: (  # pragma: no mutate
        "PayPal refused the dispute check (HTTP {status}): human review"  # pragma: no mutate
    ),  # pragma: no mutate
    Reason.EVIDENCE_CHANGED: "evidence changed since the decision",  # pragma: no mutate
    Reason.NO_PAYPAL_CAPTURE: "order has no PayPal capture",  # pragma: no mutate
    Reason.CAPTURE_CURRENCY_MISMATCH: "PayPal capture is in another currency",  # pragma: no mutate
    Reason.CAPTURE_NOT_REFUNDABLE: "capture is {status} at PayPal",  # pragma: no mutate
    Reason.UNKNOWN_REFUND_AT_PAYPAL: "PayPal shows a refund Doorstep cannot identify",  # pragma: no mutate
    Reason.WOULD_EXCEED_CAPTURE: "refund could exceed the captured amount",  # pragma: no mutate
}  # pragma: no mutate


def message(code: Reason, **values: object) -> str:
    """The human sentence for a code, with its placeholders filled in."""
    return MESSAGES[code].format(**values)
