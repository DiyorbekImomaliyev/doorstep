"""TR-03: the delivery choice after a failed attempt reads as plain words in the history.

Found by the Nov 7 UI review: Priya's history said "Shipment choice", the audit action name, and her
headline still said "Choose what happens next" after she chose. The history now says what she
chose; the seller reads the same step about the buyer.
"""

from __future__ import annotations

from app.api.views import _shipment_title
from tests.unit.test_api_journey import (  # noqa: F401 - fixtures
    api,
    demo,
    paypal,
    services,
)


def test_TR_03_the_buyer_history_says_what_they_chose(api, demo):  # noqa: F811
    token = demo["buyers"]["failed-attempt"]
    view = api.post(f"/api/buyer/{token}/choice", json={"option": "locker"}).json()
    titles = [item["title"] for item in view["timeline"]]
    assert "You chose a locker" in titles
    assert not any("shipment" in t.lower() for t in titles)


def test_TR_03_the_seller_reads_the_same_step_about_the_buyer():
    assert _shipment_title("shipment.choice", {"choice": "rebook"}, for_buyer=False) == (
        "Buyer chose redelivery"
    )
    assert _shipment_title("shipment.choice", {"choice": "?"}, for_buyer=True) == (
        "You chose how to get the parcel"
    )
    assert _shipment_title("shipment.stale", {}, for_buyer=True) == "No courier update for a while"
    assert _shipment_title("shipment.new_step", {}, for_buyer=True) == "Shipment new step"
