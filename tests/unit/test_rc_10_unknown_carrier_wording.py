"""RC-10: a claim pack for a carrier with no confirmed terms reads as plain sentences: "the carrier"
mid-sentence, and one line that its terms and cover are not confirmed. A known carrier keeps its
name, service and cover."""

from __future__ import annotations

from tests.unit.test_rc_recovery import _recovery, _reported, pipeline  # noqa: F401  (fixture)


def _facts(db, clock, pipeline, carrier: str) -> dict[str, str]:  # noqa: F811
    *_, report = _reported(db, clock, pipeline, carrier=carrier)
    recovery = _recovery(db, report.case_id)
    assert recovery is not None
    return {f["kind"]: f["text"] for f in recovery.pack["facts"]}


def test_RC_10_unknown_carrier_reads_as_the_carrier(db, clock, pipeline):  # noqa: F811
    facts = _facts(db, clock, pipeline, "shippo")
    assert facts["shipment"].startswith("The parcel was handed to the carrier with tracking")
    assert facts["terms"] == "The carrier's claim terms and standard cover are not confirmed."
    assert "Your carrier" not in " ".join(facts.values())


def test_RC_10_known_carrier_keeps_its_name_and_cover(db, clock, pipeline):  # noqa: F811
    facts = _facts(db, clock, pipeline, "royal_mail")
    assert facts["shipment"].startswith("The parcel was handed to Royal Mail with tracking")
    assert facts["terms"].startswith("Royal Mail (") and "standard cover is £" in facts["terms"]
