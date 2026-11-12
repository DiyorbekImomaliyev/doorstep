"""DI-01..DI-04: a dispute PayPal reports puts the order on the evidence route, whether the webhook
brings it or the worker's sweep finds it at PayPal; the seller can then send evidence in one tap."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from app.ai.explainer import Explainer
from app.cases.actions import CaseActions, seller_approver
from app.cases.dispute_intake import take_dispute
from app.cases.engine import ClaimReason
from app.cases.pipeline import CasePipeline
from app.orders.enums import CaseState, MoneyActionKind, OrderState
from app.orders.models import Case, Dispute, Order, TrackingEvent
from app.paypal_client.errors import PayPalClientError
from app.policy.evidence import DISPUTE_EVIDENCE_JOB
from app.proof.receipts import ReceiptSigner
from app.shipping.events import refresh_from_carrier
from app.shipping.shippo import ShippoClient, ShippoError
from app.worker.main import carrier_refresher, sweep_disputes
from app.worker.models import Job
from app.worker.paypal_events import paypal_inbox_handlers
from tests.factories import make_case, make_order, make_seller, make_shipment
from tests.unit.test_wh_paypal_events import _row

NOT_RECEIVED = "MERCHANDISE_OR_SERVICE_NOT_RECEIVED"


@pytest.fixture
def signer() -> ReceiptSigner:
    return ReceiptSigner.generate()


@pytest.fixture
def opener(rulebook, clock, settings, signer):
    def open_case(s, order: Order, reason: ClaimReason) -> object:
        return CasePipeline(s, rulebook, clock, settings, Explainer(), signer).report(
            order, reason, reported_by="paypal"
        )

    return open_case


def _webhook(db, rulebook, clock, order, *, opener=None, reason=NOT_RECEIVED, event="CREATED"):
    resource = {
        "dispute_id": "PP-D-DI",
        "status": "WAITING_FOR_SELLER_RESPONSE",
        "reason": reason,
        "disputed_transactions": [{"seller_transaction_id": order.capture_id}],
    }
    handlers = paypal_inbox_handlers(rulebook, clock, None, opener)
    handlers[("paypal", f"CUSTOMER.DISPUTE.{event}")](
        db, _row(f"CUSTOMER.DISPUTE.{event}", resource)
    )
    db.flush()


def _cases(db, order: Order) -> list[Case]:
    return list(db.scalars(select(Case).where(Case.order_id == order.id)))


def test_DI_01_a_dispute_moves_a_decided_case_to_the_evidence_route(db, rulebook, clock):
    order = make_order(db, make_seller(db))
    case = make_case(db, order, state=CaseState.AWAITING_SELLER)
    _webhook(db, rulebook, clock, order)
    assert case.state == CaseState.DISPUTE_EVIDENCE.value
    assert db.scalars(select(Dispute)).one().status == "open"


def test_DI_01_a_held_case_stays_held_for_a_person(db, rulebook, clock, opener):
    order = make_order(db, make_seller(db))
    case = make_case(db, order, state=CaseState.HOLD_FOR_REVIEW)
    _webhook(db, rulebook, clock, order, opener=opener)
    assert case.state == CaseState.HOLD_FOR_REVIEW.value
    assert _cases(db, order) == [case]  # no second case beside it


def test_DI_03_an_order_with_no_case_gets_one_and_the_seller_can_send_evidence(
    db, rulebook, clock, opener, signer
):
    seller = make_seller(db)
    order = make_order(db, seller, state=OrderState.CONFIRMED)
    make_shipment(db, order, tracking_number="SHIPPO_DELIVERED")
    _webhook(db, rulebook, clock, order, opener=opener)
    [case] = _cases(db, order)
    assert (case.state, case.reported_by, case.reason) == (
        CaseState.DISPUTE_EVIDENCE.value,
        "paypal",
        ClaimReason.NOT_RECEIVED.value,
    )
    ma = CaseActions(db, rulebook, clock, signer).act(
        case, "send-evidence", seller_approver(seller.id), verify_url="https://x/verify"
    )
    assert ma is not None and ma.kind == MoneyActionKind.EVIDENCE.value
    [job] = db.scalars(select(Job).where(Job.kind == DISPUTE_EVIDENCE_JOB)).all()
    assert job.payload["dispute_id"] == "PP-D-DI"
    assert job.payload["evidence"]["tracking_number"] == "SHIPPO_DELIVERED"


def test_DI_03_a_repeated_event_opens_no_second_case(db, rulebook, clock, opener):
    order = make_order(db, make_seller(db))
    _webhook(db, rulebook, clock, order, opener=opener)
    _webhook(db, rulebook, clock, order, opener=opener, event="UPDATED")
    assert len(_cases(db, order)) == 1


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (OrderState.DELIVERED, "UNAUTHORISED"),  # not a delivery problem
        (OrderState.SHIPPED, NOT_RECEIVED),  # no case can open before the parcel moves
    ],
)
def test_DI_03_other_disputes_are_recorded_for_a_person(db, rulebook, clock, opener, state, reason):
    order = make_order(db, make_seller(db), state=state)
    _webhook(db, rulebook, clock, order, opener=opener, reason=reason)
    assert _cases(db, order) == []
    assert db.scalars(select(Dispute)).one().status == "open"


def test_DI_03_an_order_whose_case_finished_is_only_recorded(db, rulebook, clock, opener):
    order = make_order(db, make_seller(db))
    case = make_case(db, order, state=CaseState.SETTLED)
    said = take_dispute(db, order, "PP-D-X", reason=NOT_RECEIVED, clock=clock, open_case=opener)
    assert "Disputes page" in said and _cases(db, order) == [case]


def test_DI_03_a_reopened_dispute_is_open_again(db, clock):
    order = make_order(db, make_seller(db))
    db.add(Dispute(order_id=order.id, paypal_dispute_id="PP-D-R", status="resolved"))
    db.flush()
    take_dispute(db, order, "PP-D-R", reason="", clock=clock)
    assert db.scalars(select(Dispute)).one().status == "open"


class _ListingPayPal:
    """GET /v1/customer/disputes answers with these items, and GET /v1/customer/disputes/{id}
    with these details (404 for any other); nothing else is called."""

    def __init__(
        self, items: list[dict[str, Any]], details: dict[str, dict[str, Any]] | None = None
    ) -> None:
        self.items = items
        self.details = details or {}
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, path: str, **_: Any) -> dict[str, Any]:
        self.calls.append((method, path))
        assert method == "GET" and path.startswith("/v1/customer/disputes")
        if path.startswith("/v1/customer/disputes?"):
            return {"items": self.items, "links": []}
        found = self.details.get(path.rsplit("/", 1)[-1])
        if found is None:
            raise PayPalClientError(404, "RESOURCE_NOT_FOUND")
        return found


def test_DI_02_the_sweep_finds_a_dispute_the_webhook_never_brought(
    db, session_factory, clock, settings, opener
):
    order = make_order(db, make_seller(db), state=OrderState.CONFIRMED)
    capture = order.capture_id
    db.commit()
    paypal = _ListingPayPal(
        [
            {
                "dispute_id": "PP-D-SW",
                "status": "WAITING_FOR_SELLER_RESPONSE",
                "reason": NOT_RECEIVED,
                "disputed_transactions": [{"seller_transaction_id": capture}],
            },
            {  # another shop's payment on the same PayPal account
                "dispute_id": "PP-D-OTHER",
                "status": "OPEN",
                "disputed_transactions": [{"seller_transaction_id": "NOT-OURS"}],
            },
            {"dispute_id": "", "status": "OPEN", "disputed_transactions": []},
        ]
    )
    rt = SimpleNamespace(session_factory=session_factory, clock=clock, settings=settings)
    assert sweep_disputes(SimpleNamespace(**vars(rt), paypal=paypal), opener) == 1
    assert sweep_disputes(SimpleNamespace(**vars(rt), paypal=paypal), opener) == 1  # repeatable
    db.expire_all()
    [case] = _cases(db, order)
    assert case.state == CaseState.DISPUTE_EVIDENCE.value
    assert [d.paypal_dispute_id for d in db.scalars(select(Dispute))] == ["PP-D-SW"]
    assert all(method == "GET" for method, _ in paypal.calls)  # the sweep only reads PayPal


def test_DI_02_the_sweep_asks_nothing_without_paypal(session_factory, clock, settings):
    rt = SimpleNamespace(
        session_factory=session_factory, clock=clock, settings=settings, paypal=None
    )
    assert sweep_disputes(rt) == 0


def test_DI_04_the_dispute_reason_is_read_from_the_paypal_list(clock):
    from app.paypal_client.reads import list_open_disputes_since

    paypal = _ListingPayPal(
        [{"dispute_id": "D", "status": "OPEN", "reason": NOT_RECEIVED, "disputed_transactions": []}]
    )
    [found] = list_open_disputes_since(paypal, clock.now())  # type: ignore[arg-type]
    assert found.reason == NOT_RECEIVED


def _runtime(session_factory, clock, settings, paypal):
    return SimpleNamespace(
        session_factory=session_factory, clock=clock, settings=settings, paypal=paypal
    )


def test_DI_05_the_sweep_closes_a_case_whose_dispute_paypal_decided(
    db, session_factory, clock, settings
):
    order = make_order(db, make_seller(db))
    case = make_case(db, order, state=CaseState.DISPUTE_EVIDENCE)
    db.add(Dispute(order_id=order.id, paypal_dispute_id="PP-D-RES", status="open"))
    db.add(Dispute(order_id=order.id, paypal_dispute_id="PP-D-GONE", status="open"))
    db.commit()
    resolved = {
        "dispute_id": "PP-D-RES",
        "status": "RESOLVED",
        "dispute_outcome": {"outcome_code": "RESOLVED_SELLER_FAVOUR"},
    }
    paypal = _ListingPayPal([], {"PP-D-RES": resolved})  # PP-D-GONE answers 404
    sweep_disputes(_runtime(session_factory, clock, settings, paypal))
    db.expire_all()
    rows = {d.paypal_dispute_id: d.status for d in db.scalars(select(Dispute))}
    assert rows == {"PP-D-RES": "resolved", "PP-D-GONE": "open"}  # an unreadable one stays open
    assert db.get(Case, case.id).state == CaseState.CLOSED.value  # type: ignore[union-attr]


def test_DI_05_a_dispute_paypal_still_lists_open_is_not_read_again(
    db, session_factory, clock, settings
):
    order = make_order(db, make_seller(db))
    make_case(db, order, state=CaseState.DISPUTE_EVIDENCE)
    db.add(Dispute(order_id=order.id, paypal_dispute_id="PP-D-OPEN", status="open"))
    db.commit()
    listed = {
        "dispute_id": "PP-D-OPEN",
        "status": "UNDER_REVIEW",
        "disputed_transactions": [{"seller_transaction_id": order.capture_id}],
    }
    paypal = _ListingPayPal([listed])
    sweep_disputes(_runtime(session_factory, clock, settings, paypal))
    assert [path for _, path in paypal.calls if not path.startswith("/v1/customer/disputes?")] == []


def test_DI_05_a_resolved_dispute_is_recorded_once(db, clock, rulebook):
    from app.worker.paypal_events import record_resolved

    order = make_order(db, make_seller(db))
    case = make_case(db, order, state=CaseState.SETTLED)
    row = Dispute(order_id=order.id, paypal_dispute_id="PP-D-1", status="resolved")
    db.add(row)
    db.flush()
    record_resolved(db, row, "ANY")
    assert (row.status, case.state) == ("resolved", CaseState.SETTLED.value)


class _Carrier:
    """Shippo's track read: the same track object its webhook carries."""

    def __init__(self, status: str = "DELIVERED", fail: bool = False) -> None:
        self.status, self.fail = status, fail
        self.reads: list[tuple[str, str]] = []

    def get_track(self, carrier: str, tracking_number: str) -> dict[str, Any]:
        self.reads.append((carrier, tracking_number))
        if self.fail:
            raise ShippoError("Shippo /tracks answered 503")
        return {
            "tracking_number": tracking_number,
            "tracking_status": {"status": self.status, "status_date": "2026-11-10T23:30:39Z"},
        }


def _refresh(carrier: _Carrier, clock):
    rt = SimpleNamespace(shippo=carrier, clock=clock)
    refresh = carrier_refresher(rt)  # type: ignore[arg-type]
    assert refresh is not None
    return refresh


def test_DI_06_a_shipped_order_is_brought_up_to_date_from_the_carrier_then_gets_its_case(
    db, rulebook, clock, opener
):
    order = make_order(db, make_seller(db), state=OrderState.SHIPPED)
    make_shipment(db, order, tracking_number="SHIPPO_DELIVERED")
    carrier = _Carrier("DELIVERED")
    said = take_dispute(
        db,
        order,
        "PP-D-6",
        reason=NOT_RECEIVED,
        clock=clock,
        open_case=opener,
        refresh=_refresh(carrier, clock),
    )
    assert carrier.reads == [("shippo", "SHIPPO_DELIVERED")]
    [case] = _cases(db, order)
    assert case.state == CaseState.DISPUTE_EVIDENCE.value and "opened a case" in said
    [event] = db.scalars(select(TrackingEvent)).all()
    assert (event.status, event.applied) == ("DELIVERED", True)


@pytest.mark.parametrize("carrier", [_Carrier("PRE_TRANSIT"), _Carrier(fail=True)])
def test_DI_06_with_no_carrier_scan_the_dispute_is_recorded_for_a_person(
    db, rulebook, clock, opener, carrier
):
    order = make_order(db, make_seller(db), state=OrderState.SHIPPED)
    make_shipment(db, order, tracking_number="SHIPPO_PRE_TRANSIT")
    take_dispute(
        db,
        order,
        "PP-D-7",
        reason=NOT_RECEIVED,
        clock=clock,
        open_case=opener,
        refresh=_refresh(carrier, clock),
    )
    assert _cases(db, order) == [] and order.state == OrderState.SHIPPED.value
    assert db.scalars(select(Dispute)).one().status == "open"


def test_DI_06_an_order_the_carrier_already_moved_is_not_read_again(db, clock, opener):
    order = make_order(db, make_seller(db), state=OrderState.DELIVERED)
    carrier = _Carrier()
    take_dispute(
        db,
        order,
        "PP-D-8",
        reason=NOT_RECEIVED,
        clock=clock,
        open_case=opener,
        refresh=_refresh(carrier, clock),
    )
    assert carrier.reads == [] and len(_cases(db, order)) == 1


def test_DI_06_a_shared_test_tracking_number_updates_only_the_disputed_order(db, clock):
    seller = make_seller(db)
    older = make_order(db, seller, state=OrderState.SHIPPED)
    make_shipment(db, older, tracking_number="SHIPPO_DELIVERED")
    newer = make_order(db, seller, state=OrderState.SHIPPED)
    make_shipment(db, newer, tracking_number="SHIPPO_DELIVERED")
    refresh_from_carrier(db, older, _Carrier(), now=clock.now())  # type: ignore[arg-type]
    assert (older.state, newer.state) == (OrderState.DELIVERED.value, OrderState.SHIPPED.value)


def test_DI_06_no_carrier_read_without_shippo_or_a_tracking_number(db, clock):
    assert carrier_refresher(SimpleNamespace(shippo=None, clock=clock)) is None  # type: ignore[arg-type]
    order = make_order(db, make_seller(db), state=OrderState.SHIPPED)
    carrier = _Carrier()
    refresh_from_carrier(db, order, carrier, now=clock.now())  # type: ignore[arg-type]
    assert carrier.reads == []


def test_DI_06_shippo_client_reads_a_track(httpserver):
    track = {"tracking_number": "SHIPPO_DELIVERED", "tracking_status": {"status": "DELIVERED"}}
    httpserver.expect_request("/tracks/shippo/SHIPPO_DELIVERED", method="GET").respond_with_json(
        track
    )
    httpserver.expect_request("/tracks/shippo/BAD", method="GET").respond_with_data(
        "no", status=404
    )
    client = ShippoClient(api_key="shippo_test_x", base_url=httpserver.url_for("").rstrip("/"))
    assert client.get_track("shippo", "SHIPPO_DELIVERED") == track
    with pytest.raises(ShippoError, match="404"):
        client.get_track("shippo", "BAD")
    assert httpserver.log[0][0].headers["Authorization"] == "ShippoToken shippo_test_x"
