"""RC-08 and AT-07 — AI where it is seen. A demo reset drafts no claim nobody opens; a claim pack is
drafted by the model when the seller opens it, once. The agent's automatic pre-check is planned by
the model, once per case, and the run says honestly who planned it."""

from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select

from app.agent.runner import FIXED_PLANNER
from app.ai.recovery import Fact, RecoveryDrafter, template
from app.ai.schemas import AgentStep, DraftSentence, RecoveryDraft
from app.api.deps import AppDeps, Services
from app.cases.recovery import DRAFT_RECOVERY, DRAFT_RETRY_S, ClaimDraftOnOpen
from app.db import Base, make_session_factory
from app.main import create_app
from app.orders.models import Case, Recovery
from app.proof.receipts import ReceiptSigner
from app.runtime import ROOT
from app.security.links import LinkSigner
from app.worker.models import Job
from tests.factories import make_case, make_order, make_seller

NOT_HERE = {"reason": "not_received", "note": ""}
STANDARD = [
    "get_order_details",
    "get_shipment_tracking",
    "list_disputes",
    "request_refund",
    "finish",
]


class EchoModel:
    """Drafts each fact back as a sentence citing it (passes the verifier); plans the standard
    agent steps in order. Counts every call: each one is a spend."""

    last_provider = "echo"

    def __init__(self, *, fail: bool = False, delay: float = 0.0):
        self.calls = 0
        self.fail = fail
        self.delay = delay
        self._lock = threading.Lock()

    def complete_structured(self, *, schema, system, user, **_):
        with self._lock:
            self.calls += 1
            n = self.calls
        time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("model down")
        if schema is AgentStep:
            return AgentStep(tool=STANDARD[min(n - 1, 4)], reason="standard check")  # type: ignore[arg-type]
        lines = [line.split(": ", 1) for line in user.splitlines()[1:] if ": " in line]
        return RecoveryDraft(sentences=[DraftSentence(text=t, fact_ids=[i]) for i, t in lines][:14])


def _api(session_factory, settings, clock, llm=None) -> TestClient:
    services = Services(
        links=LinkSigner("a" * 32, clock),
        receipts=ReceiptSigner.generate(),
        orders=None,
        approved=None,
        agent_llm=llm,
    )
    deps = AppDeps(
        settings=settings,
        session_factory=session_factory,
        paypal_verifier=None,
        clock=clock,
        rules_dir=ROOT / "rules",
        services=services,
    )
    return TestClient(create_app(deps))


def _drafts(session_factory) -> int:
    with session_factory() as s:
        return s.scalar(select(func.count()).select_from(Job).where(Job.kind == DRAFT_RECOVERY))


def _seeded_claim(api, session_factory):
    token = api.post("/demo/seed?scratch=1").json()["seller_token"]
    with session_factory() as s:
        r = s.scalars(
            select(Recovery).where(Recovery.status == "PREPARED").order_by(Recovery.case_id)
        ).first()
        assert r is not None
        return token, r.case_id


# ---------------------------------------------------------------- RC-08


def test_RC_08_the_demo_seed_queues_no_claim_drafts(session_factory, settings, clock):
    api = _api(session_factory, settings, clock)
    seed = api.post("/demo/seed?scratch=1").json()
    with session_factory() as s:
        packs = [r.pack["source"] for r in s.scalars(select(Recovery))]
    assert packs and set(packs) == {"template"}  # seeded cases still have a full claim pack
    assert _drafts(session_factory) == 0

    sam = api.post(f"/api/buyer/{seed['buyers']['sam-jacket']}/not-here", json=NOT_HERE)
    assert sam.status_code == 200, sam.text
    assert _drafts(session_factory) == 1  # a case reported during the demo is drafted ahead


def test_RC_08_opening_a_claim_pack_drafts_it_once(session_factory, settings, clock):
    model = EchoModel()
    api = _api(session_factory, settings, clock, llm=model)
    token, case_id = _seeded_claim(api, session_factory)
    base = f"/api/seller/{token}/cases/{case_id}/recovery"
    assert api.get(base).json()["pack_source"] == "template"

    first = api.post(f"{base}/draft")
    assert first.status_code == 200, first.text
    assert first.json()["pack_source"] == "model" and model.calls == 1
    assert api.post(f"{base}/draft").json()["pack_source"] == "model"
    assert api.get(base).json()["pack_source"] == "model"
    assert model.calls == 1  # saved: later opens reuse it


def test_RC_08_a_failed_draft_keeps_the_facts_and_waits(session_factory, settings, clock):
    model = EchoModel(fail=True)
    api = _api(session_factory, settings, clock, llm=model)
    token, case_id = _seeded_claim(api, session_factory)
    draft = f"/api/seller/{token}/cases/{case_id}/recovery/draft"
    assert api.post(draft).json()["pack_source"] == "template" and model.calls == 1
    assert api.post(draft).json()["pack_source"] == "template" and model.calls == 1
    clock.advance(DRAFT_RETRY_S + 1)
    api.post(draft)
    assert model.calls == 2


def test_RC_08_only_prepared_claims_are_drafted(session_factory, settings, clock):
    model = EchoModel()
    api = _api(session_factory, settings, clock, llm=model)
    token, case_id = _seeded_claim(api, session_factory)
    base = f"/api/seller/{token}/cases/{case_id}/recovery"
    assert api.post(f"{base}/submitted").status_code == 200
    assert api.post(f"{base}/draft").json()["pack_source"] == "template"
    assert model.calls == 0


def test_RC_08_a_case_without_a_claim_is_404(session_factory, settings, clock):
    api = _api(session_factory, settings, clock, llm=EchoModel())
    token = api.post("/demo/seed?scratch=1").json()["seller_token"]
    with session_factory() as s:
        claimed = select(Recovery.case_id)
        unclaimed = s.scalars(select(Case.id).where(Case.id.not_in(claimed))).first()
    assert unclaimed is not None
    assert api.post(f"/api/seller/{token}/cases/{unclaimed}/recovery/draft").status_code == 404


def test_RC_08_two_opens_at_once_make_one_call(tmp_path, clock):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'drafts.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    facts = [Fact(id=f"F{i}", kind="event", text=f"Fact number {i}.") for i in range(1, 5)]
    with factory() as s:
        order = make_order(s, make_seller(s))
        case = make_case(s, order)
        s.add(
            Recovery(
                case_id=case.id,
                order_id=order.id,
                seller_id=order.seller_id,
                decision_id="decision-1",
                carrier="royal_mail",
                carrier_id="royal_mail",
                failure_point="CARRIER_LOST",
                claim_minor=2000,
                currency="GBP",
                status="PREPARED",
                deadline_at=clock.now(),
                pack=template(facts).model_dump(mode="json"),
            )
        )
        s.commit()
        rid = s.scalars(select(Recovery.id)).one()
    model = EchoModel(delay=0.3)
    opener = ClaimDraftOnOpen(RecoveryDrafter(model), clock)

    def open_it():
        with factory() as s:
            opener.draft(s, s.get(Recovery, rid))

    threads = [threading.Thread(target=open_it) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert model.calls == 1
    with factory() as s:
        assert s.get(Recovery, rid).pack["source"] == "model"


# ---------------------------------------------------------------- AT-07


def _sam_case(api):
    seed = api.post("/demo/seed?scratch=1").json()
    sam = api.post(f"/api/buyer/{seed['buyers']['sam-jacket']}/not-here", json=NOT_HERE).json()
    return seed["seller_token"], sam["case_id"]


def test_AT_07_the_pre_check_is_planned_by_the_model_once_per_case(
    session_factory, settings, clock
):
    model = EchoModel()
    api = _api(session_factory, settings, clock, llm=model)
    token, case_id = _sam_case(api)
    run = api.post(f"/api/seller/{token}/agent/cases/{case_id}").json()
    assert run["planner"] == "model:echo" and run["outcome"]
    spent = model.calls
    assert spent >= 4  # one call per planned step
    again = api.post(f"/api/seller/{token}/agent/cases/{case_id}").json()  # opened again
    assert again == run and model.calls == spent


def test_AT_07_without_a_working_model_the_run_says_fixed_plan(session_factory, settings, clock):
    api = _api(session_factory, settings, clock, llm=EchoModel(fail=True))
    token, case_id = _sam_case(api)
    run = api.post(f"/api/seller/{token}/agent/cases/{case_id}").json()
    assert run["planner"] == FIXED_PLANNER and run["outcome"]


def test_RC_08_a_claim_sent_while_the_ai_writes_keeps_its_text(session_factory, settings, clock):
    """The seller taps "I submitted it" while the draft is being written: the text they sent
    stays; the late draft is not saved over it."""
    api = _api(session_factory, settings, clock)
    token, case_id = _seeded_claim(api, session_factory)
    base = f"/api/seller/{token}/cases/{case_id}/recovery"

    class MarksMeanwhile(EchoModel):
        def complete_structured(self, **kw):
            assert api.post(f"{base}/submitted").status_code == 200
            return super().complete_structured(**kw)

    model = MarksMeanwhile()
    opener = ClaimDraftOnOpen(RecoveryDrafter(model), clock)
    with session_factory() as s:
        r = opener.draft(s, s.scalars(select(Recovery).where(Recovery.case_id == case_id)).one())
        assert model.calls == 1 and r.status == "SUBMITTED" and r.pack["source"] == "template"
