"""SA-02 batch-scan finding in plain words (v2 UI review, Nov 5).

When the batch check supports the scan, the seller reads one plain sentence with the right
plural, not "0 other parcel(s)". The verdict logic is unchanged and pinned in test_sa_scan_audit.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.cases.scan_audit import (
    AuditInput,
    CarrierScan,
    CheckId,
    CheckResult,
    SellerDelivery,
    audit,
)
from tuning import engine_config

T0 = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
WINDOW = engine_config.SCAN_AUDIT_BATCH_WINDOW_S


def _batch_finding(seller_seconds: tuple[int, ...]) -> tuple[CheckResult, str]:
    inp = AuditInput(
        carrier="royal_mail",
        destination_area="N1",
        events=(
            CarrierScan(status="TRANSIT", occurred_at=T0 - timedelta(hours=2)),
            CarrierScan(status="DELIVERED", occurred_at=T0, area="N1"),
        ),
        # Same destination area as this parcel: never enough areas for a batch.
        seller_deliveries=tuple(
            SellerDelivery(occurred_at=T0 + timedelta(seconds=s), area="N1") for s in seller_seconds
        ),
        reported_at=T0 + timedelta(hours=1),
    )
    [check] = [c for c in audit(inp).checks if c.id == CheckId.BATCH]
    return check.result, check.finding


@pytest.mark.parametrize(
    ("nearby", "expected"),
    [
        ((), f"no other parcel of this seller was marked delivered within {WINDOW} s"),
        (
            (5,),
            f"1 other parcel of this seller marked delivered within {WINDOW} s, "
            "too few areas for a batch",
        ),
        (
            (5, 9),
            f"2 other parcels of this seller marked delivered within {WINDOW} s, "
            "too few areas for a batch",
        ),
    ],
)
def test_SA_02_batch_support_reads_as_plain_words(nearby, expected):
    result, finding = _batch_finding(nearby)
    assert result == CheckResult.SUPPORTS
    assert finding == expected
    assert "(s)" not in finding


def test_SA_02_a_parcel_outside_the_window_is_not_counted():
    _, finding = _batch_finding((WINDOW + 1,))
    assert finding.startswith("no other parcel")
