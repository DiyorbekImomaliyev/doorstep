"""AT-06: the agent's dispute check says what PayPal said, never more.

PayPal answers 400 to the transaction filter when nothing matches (checked in the sandbox on Nov 6
and Nov 9, 2026), and a failed read used to read as "No PayPal disputes". Now a failed filtered
read lists the account's disputes and matches the capture here; a read that fails both ways says so.
"""

from __future__ import annotations

import json

from app.agent.runner import read_disputes

CAPTURE = "3AB12345CD678901E"


def _dispute(dispute_id: str, capture: str | None, status: str = "OPEN") -> dict:
    item: dict = {"dispute_id": dispute_id, "status": status}
    if capture is not None:
        item["disputed_transactions"] = [{"seller_transaction_id": capture}]
    return item


class Tool:
    def __init__(self, filtered: str, unfiltered: str) -> None:
        self.answers = {True: filtered, False: unfiltered}
        self.calls: list[dict] = []

    def __call__(self, args: dict) -> str:
        self.calls.append(args)
        return self.answers["disputed_transaction_id" in args]


ERROR = "Error: 400 Client Error: Bad Request"


def test_AT_06_a_filtered_read_that_works_is_used_as_it_is():
    tool = Tool(json.dumps({"items": [_dispute("PP-D-1", CAPTURE)]}), "unused")
    summary, ids = read_disputes(tool, CAPTURE)
    assert tool.calls == [{"disputed_transaction_id": CAPTURE}]
    assert ids == {"PP-D-1"} and "1 PayPal dispute" in summary


def test_AT_06_a_failed_filter_falls_back_to_the_list_and_matches_the_capture():
    listed = {"items": [_dispute("PP-D-1", CAPTURE), _dispute("PP-D-2", "9ZZ99999ZZ999999Z")]}
    tool = Tool(ERROR, json.dumps(listed))
    summary, ids = read_disputes(tool, CAPTURE)
    assert tool.calls == [{"disputed_transaction_id": CAPTURE}, {"page_size": 50}]
    assert ids == {"PP-D-1"}
    assert "PP-D-1" in summary and "PP-D-2" not in summary


def test_AT_06_no_dispute_for_this_capture_says_so():
    tool = Tool(ERROR, json.dumps({"items": [_dispute("PP-D-2", "9ZZ99999ZZ999999Z")]}))
    summary, ids = read_disputes(tool, CAPTURE)
    assert ids == set() and summary == "No PayPal disputes on this payment."


def test_AT_06_a_dispute_the_list_does_not_tie_to_a_capture_may_be_read():
    tool = Tool(ERROR, json.dumps({"items": [_dispute("PP-D-3", None)]}))
    summary, ids = read_disputes(tool, CAPTURE)
    assert ids == {"PP-D-3"} and "not tied to a payment" in summary


def test_AT_06_a_read_that_fails_both_ways_never_reads_as_no_disputes():
    summary, ids = read_disputes(Tool(ERROR, ERROR), CAPTURE)
    assert ids == set()
    assert "could not read" in summary and "No PayPal disputes" not in summary
