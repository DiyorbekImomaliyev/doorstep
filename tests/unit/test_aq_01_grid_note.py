"""AQ-01: Ask the grid's note is asked for in 12 words at most; a longer one stays valid."""

from __future__ import annotations

from app.ai import grid_agent
from app.ai.schemas import GridPlan


def test_AQ_01_grid_note_is_asked_for_in_twelve_words_and_long_notes_stay_valid() -> None:
    assert "at most 12 plain words" in grid_agent._SYSTEM
    field = GridPlan.model_json_schema()["properties"]["note"]
    assert "12" in field["description"]
    # A longer note is not a reason to drop the whole plan: the UI ends the line in an ellipsis.
    assert GridPlan(answerable=True, note="word " * 30).note
