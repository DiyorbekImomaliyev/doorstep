"""E2E-03 sandbox smoke: the backend half of the loop against real PayPal sandbox and Shippo.

`make smoke` (needs PAYPAL_* and SHIPPO_API_KEY in .env; sandbox/test keys only).

1. A sandbox order is paid with PayPal's public test card (no buyer login needed), so a real
   capture exists. The buyer-approval half (PayPal popup) is shown in the video instead.
2. Doorstep records the order, checks packing, and ships it: the outbox registers the tracking
   number with Shippo and on the PayPal payment (real calls, PayPal-Request-Id).
3. The carrier's "delivered" event is replayed from a fixture (Shippo cannot reach localhost;
   SPEC section 17 fallback), then the buyer taps "Not here".
4. The engine, rules and policy decide; under the seller's limit the policy queues the refund
   and the worker sends it to PayPal. The script prints the PayPal refund id and verifies the
   signed receipt chain.

Prints ids and statuses only, never tokens or keys. Uses its own SQLite file unless
SMOKE_DATABASE_URL is set, so it never touches the filmed demo data.
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _create_card_order(rt, run_id: str, currency: str, country: str, postcode: str) -> dict:
    """A sandbox order paid at once with PayPal's public test card (no buyer login)."""
    return rt.paypal.request(
        "POST",
        "/v2/checkout/orders",
        request_id=f"smoke:{run_id}:create",
        json={
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": f"smoke-{run_id}",
                    "description": "Black jacket, size M (Doorstep smoke test)",
                    "amount": {"currency_code": currency, "value": "40.00"},
                }
            ],
            "payment_source": {
                "card": {
                    "number": "4111111111111111",
                    "expiry": "2030-12",
                    "name": "Sandbox Buyer",
                    "billing_address": {"postal_code": postcode, "country_code": country},
                }
            },
        },
    )


def _approve_in_browser(rt, run_id: str, currency: str, wait_s: int = 600) -> dict | None:
    """PayPal wallet order: print the approval link, wait for the sandbox buyer, capture."""
    import time

    order = rt.paypal.request(
        "POST",
        "/v2/checkout/orders",
        request_id=f"smoke:{run_id}:create-wallet",
        json={
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": f"smoke-{run_id}",
                    "description": "Black jacket, size M (Doorstep smoke test)",
                    "amount": {"currency_code": currency, "value": "40.00"},
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "user_action": "PAY_NOW",
                        "shipping_preference": "NO_SHIPPING",
                        "return_url": "https://example.com/doorstep-smoke-approved",
                        "cancel_url": "https://example.com/doorstep-smoke-cancelled",
                    }
                }
            },
        },
    )
    link = next(
        (x["href"] for x in order.get("links", []) if x.get("rel") in ("payer-action", "approve")),
        None,
    )
    print(f"   Open this link and approve with your sandbox BUYER account:\n   {link}")
    print(f"   Waiting up to {wait_s // 60} minutes...", flush=True)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        status = rt.paypal.request("GET", f"/v2/checkout/orders/{order['id']}").get("status")
        if status == "APPROVED":
            return rt.paypal.request(
                "POST",
                f"/v2/checkout/orders/{order['id']}/capture",
                request_id=f"smoke:{run_id}:capture",
                json={},
            )
        time.sleep(3)
    print("   Not approved in time.")
    return None


def main() -> int:
    db = Path(tempfile.gettempdir()) / f"doorstep-smoke-{uuid.uuid4().hex[:8]}.db"
    os.environ["DATABASE_URL"] = os.environ.get("SMOKE_DATABASE_URL", f"sqlite:///{db}")
    os.environ.setdefault("APP_ENV", "development")

    from app.ai.explainer import Explainer
    from app.ai.packing import Verdict
    from app.cases.engine import ClaimReason
    from app.cases.pipeline import CasePipeline
    from app.cases.signals import EvidenceKind
    from app.orders.enums import OrderState
    from app.orders.models import Evidence, MoneyAction, Order, Seller
    from app.orders.state import transition_order
    from app.policy.rules import load_rules
    from app.proof.receipts import verify_chain
    from app.runtime import build_runtime
    from app.shipping.events import handle_shippo_track_updated
    from app.shipping.service import ShippingService
    from app.worker.main import build_worker
    from app.worker.models import WebhookInbox

    rt = build_runtime()
    if rt.paypal is None:
        print("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set: nothing to smoke-test.")
        return 2
    rulebook = load_rules(ROOT / "rules")
    inbox, runner = build_worker(rt)

    def drain(label: str) -> None:
        done = 0
        for _ in range(10):
            n = inbox.run_pending() + runner.run_due()
            done += n
            if not n:
                break
        print(f"  worker ({label}): {done} job(s) run")

    # 1. A real sandbox capture, paid with the public sandbox test card.
    from app.paypal_client.errors import PayPalClientError

    run_id = uuid.uuid4().hex[:10]
    currency = os.environ.get("SMOKE_CURRENCY", "USD")
    country, postcode, city = (
        ("GB", "N1 9GU", "London") if currency == "GBP" else ("US", "94107", "San Francisco")
    )
    try:
        created = _create_card_order(rt, run_id, currency, country, postcode)
    except PayPalClientError as exc:
        details = (getattr(exc, "body", None) or {}).get("details") or []
        issues = ", ".join(str(d.get("issue")) for d in details if isinstance(d, dict))
        print(f"1. Card payment not enabled on this sandbox app ({issues or exc}).")
        created = _approve_in_browser(rt, run_id, currency)
        if created is None:
            return 1
    captures = [
        c
        for unit in created.get("purchase_units", [])
        for c in (unit.get("payments") or {}).get("captures", [])
    ]
    if created.get("status") != "COMPLETED" or not captures:
        print(f"1. Sandbox order not captured (status {created.get('status')}).")
        return 1
    capture = captures[0]
    print(f"1. Sandbox order {created['id']} captured: {capture['id']} ({capture['status']})")

    with rt.session_factory() as s:
        seller = Seller(name="Maria's Vintage (smoke)", region=country)
        s.add(seller)
        s.flush()
        order = Order(
            seller_id=seller.id,
            paypal_order_id=created["id"],
            capture_id=capture["id"],
            capture_status=capture["status"],
            amount_minor=4000,
            currency=currency,
            buyer_country=country,
            payer_id="SMOKE-CARD",
            shipping_address={
                "name": "Sam Carter",
                "line1": "Flat 4, 12 Albion Road" if country == "GB" else "500 Howard St Apt 4",
                "city": city,
                "postal_code": postcode,
                "country": country,
            },
            address_check="valid",
            shipping_method="home",
            item_description="Black jacket, size M",
        )
        s.add(order)
        s.flush()
        transition_order(s, order, OrderState.PAID, actor="smoke", reason="sandbox card capture")
        s.add(
            Evidence(
                order_id=order.id,
                kind=EvidenceKind.PACKING_PHOTO.value,
                uri="sha256:smoke",
                ai_result={"verdict": Verdict.MATCH.value, "expected_weight_g": 900},
            )
        )
        # 2. Ship: the outbox registers tracking with Shippo and on the PayPal payment.
        ShippingService(s, rt.clock, rt.settings).ship(
            order, carrier="shippo", tracking_number="SHIPPO_DELIVERED", label_weight_g=900
        )
        s.commit()
        order_id = order.id
    print("2. Shipped with SHIPPO_DELIVERED")
    drain("tracking")

    # 3. Replay the carrier's delivered event (fixture), then the buyer taps "Not here".
    with rt.session_factory() as s:
        row = WebhookInbox(
            event_id=f"smoke-{run_id}-delivered",
            source="shippo",
            event_type="track_updated",
            payload={
                "event": "track_updated",
                "data": {
                    "tracking_number": "SHIPPO_DELIVERED",
                    "tracking_status": {"status": "DELIVERED"},
                },
            },
            status="pending",
            attempts=0,
        )
        handle_shippo_track_updated(s, row)
        order = s.get(Order, order_id)
        assert order is not None
        print(f"3. Carrier event replayed: order is {order.state}")
        pipeline = CasePipeline(
            s, rulebook, rt.clock, rt.settings, Explainer(), rt.services.receipts
        )
        report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
        s.commit()
        print(
            f"4. Case {report.case_id[:8]}: {report.failure_point.value}, "
            f"{report.confidence.value} confidence, rule {report.rule_id} -> {report.route.value}"
        )
        case_id = report.case_id

    # 4. The worker sends the refund the policy queued.
    drain("refund")
    with rt.session_factory() as s:
        money = s.query(MoneyAction).filter_by(case_id=case_id).all()
        for m in money:
            print(f"5. Refund {m.status}: PayPal refund id {m.paypal_refund_id or '-'}")
        report_chain = verify_chain(s, rt.services.receipts.public_key_b64, case_id=case_id)
        print(f"6. Receipt chain: {report_chain.status} ({report_chain.checked} checked)")
        ok = bool(money) and all(m.paypal_refund_id for m in money)
    print("SMOKE OK" if ok else "SMOKE INCOMPLETE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
