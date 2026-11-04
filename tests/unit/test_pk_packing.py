"""PK-01..04: the packing photo check. A model compares one photo with the order line; code
decides the verdict. It never auto-passes: low confidence or any failure gives "check"."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.ai.llm import Image, InvalidModelOutput, LLMAdapter
from app.ai.packing import PackingChecker, Verdict
from app.ai.schemas import PackingCheckResult
from app.cases.signals import EvidenceKind
from app.orders.enums import OrderState
from app.orders.models import Evidence
from app.shipping.packing import PackingError, PackingService
from tests.factories import make_order, make_seller
from tuning import engine_config

PHOTO = Image(media_type="image/jpeg", data=b"\xff\xd8fake-jpeg")


class Scripted:
    """A provider that returns or raises the scripted answers in order."""

    name = "scripted"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[dict] = []

    def generate(self, schema, **kw):
        self.calls.append(kw)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _result(**over) -> PackingCheckResult:
    base = {
        "matches": True,
        "item": "jacket",
        "colour": "black",
        "size_seen": "M",
        "reasons": ["black jacket, size label M"],
        "confidence": 0.95,
        "mismatch_attribute": "none",
    }
    base.update(over)
    return PackingCheckResult(**base)


def _checker(*answers) -> tuple[PackingChecker, Scripted]:
    provider = Scripted(*answers)
    return PackingChecker(LLMAdapter(provider)), provider


# ---------------------------------------------------------------- PK-01 schema


def test_PK_01_a_confident_match_is_a_match_with_the_schema_result():
    checker, provider = _checker(_result())
    verdict = checker.check("Black jacket, size M", PHOTO)
    assert verdict.verdict == Verdict.MATCH
    assert isinstance(verdict.result, PackingCheckResult)
    assert provider.calls[0]["temperature"] == 0
    assert provider.calls[0]["images"] == [PHOTO]


def test_PK_01_the_prompt_treats_photo_text_as_data():
    checker, provider = _checker(_result())
    checker.check("Black jacket, size M", PHOTO)
    system = provider.calls[0]["system"].lower()
    assert "data" in system and "instruction" in system


# ---------------------------------------------------------------- PK-02 threshold


def test_PK_02_low_confidence_match_is_check_never_match():
    low = engine_config.PACKING_CONFIDENCE_MIN - 0.01
    checker, _ = _checker(_result(confidence=low))
    assert checker.check("Black jacket, size M", PHOTO).verdict == Verdict.CHECK


def test_PK_02_low_confidence_mismatch_is_also_check():
    low = engine_config.PACKING_CONFIDENCE_MIN - 0.01
    checker, _ = _checker(
        _result(matches=False, colour="blue", mismatch_attribute="colour", confidence=low)
    )
    assert checker.check("Black jacket, size M", PHOTO).verdict == Verdict.CHECK


# ---------------------------------------------------------------- PK-03 attribute


def test_PK_03_mismatch_names_the_attribute():
    checker, _ = _checker(
        _result(
            matches=False, colour="blue", mismatch_attribute="colour", reasons=["jacket is blue"]
        )
    )
    verdict = checker.check("Black jacket, size M", PHOTO)
    assert verdict.verdict == Verdict.MISMATCH
    assert verdict.attribute == "colour"
    assert "colour" in verdict.message.lower()


def test_PK_03_a_mismatch_that_names_nothing_is_check():
    checker, _ = _checker(_result(matches=False, mismatch_attribute="none"))
    assert checker.check("Black jacket, size M", PHOTO).verdict == Verdict.CHECK


# ---------------------------------------------------------------- PK-04 fallback


def test_PK_04_invalid_output_retries_once_then_checks():
    bad = InvalidModelOutput("not json")
    checker, provider = _checker(bad, bad)
    verdict = checker.check("Black jacket, size M", PHOTO)
    assert verdict.verdict == Verdict.CHECK
    assert len(provider.calls) == 2  # one retry, then the fallback
    assert verdict.result is None


def test_PK_04_invalid_then_valid_uses_the_retry():
    checker, provider = _checker(InvalidModelOutput("bad"), _result())
    assert checker.check("Black jacket, size M", PHOTO).verdict == Verdict.MATCH
    assert len(provider.calls) == 2


def test_PK_04_no_model_configured_is_check():
    assert PackingChecker(None).check("Black jacket", PHOTO).verdict == Verdict.CHECK


# ---------------------------------------------------------------- service


@pytest.fixture
def packing(db, clock):
    def build(*answers) -> PackingService:
        return PackingService(db, clock, PackingChecker(LLMAdapter(Scripted(*answers))))

    return build


def _paid(db):
    order = make_order(db, make_seller(db), state=OrderState.PAID)
    order.item_description = "Black jacket, size M"
    db.flush()
    return order


def test_PK_01_match_stores_evidence_and_marks_packed(db, packing):
    order = _paid(db)
    verdict = packing(_result()).check(order, PHOTO)
    assert verdict.verdict == Verdict.MATCH
    assert order.state == OrderState.PACKED
    [ev] = db.scalars(select(Evidence).where(Evidence.kind == EvidenceKind.PACKING_PHOTO)).all()
    assert ev.ai_result["verdict"] == "match"
    assert ev.uri.startswith("sha256:")  # the photo's fingerprint, not the bytes


def test_PK_03_mismatch_keeps_the_order_paid_until_cleared(db, packing):
    order = _paid(db)
    service = packing(_result(matches=False, colour="blue", mismatch_attribute="colour"))
    assert service.check(order, PHOTO).verdict == Verdict.MISMATCH
    assert order.state == OrderState.PAID
    service.clear(order, actor="seller:maria")
    assert order.state == OrderState.PACKED
    ev = db.scalars(select(Evidence).where(Evidence.kind == EvidenceKind.PACKING_PHOTO)).one()
    assert ev.ai_result["cleared"] is True


def test_PK_02_clearing_without_a_flagged_check_is_refused(db, packing):
    order = _paid(db)
    with pytest.raises(PackingError):
        packing().clear(order, actor="seller:maria")


def test_PK_01_unpaid_order_cannot_be_checked(db, packing):
    order = make_order(db, make_seller(db), state=OrderState.CREATED)
    with pytest.raises(PackingError):
        packing(_result()).check(order, PHOTO)


def test_PK_01_the_model_sees_the_order_line_only(db, clock):
    order = _paid(db)
    provider = Scripted(_result())
    service = PackingService(db, clock, PackingChecker(LLMAdapter(provider)))
    service.check(order, PHOTO)
    user = provider.calls[0]["user"]
    assert "Black jacket, size M" in user
    assert order.shipping_address["line1"] not in user
