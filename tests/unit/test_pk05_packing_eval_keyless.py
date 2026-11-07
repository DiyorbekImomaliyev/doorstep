"""PK-05 harness without a model key: the manifest checks and the recall arithmetic.

The live eval (evals/vision/test_pk05_packing_photos.py) needs the labelled photo set.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from pydantic import BaseModel

from app.ai.schemas import PackingCheckResult
from evals.recording import Recorder
from evals.vision.packing_eval import ManifestError, check_row, load_rows, summarise

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class Sees:
    """A stand-in model that sees the colour named in the photo's file name."""

    last_provider = "stand-in"

    def complete_structured(
        self, *, schema: type[BaseModel], system: str, user: str, **kwargs: Any
    ) -> BaseModel:
        photo = kwargs["images"][0].data.decode("latin-1").rsplit(":", 1)[-1]
        matches = photo in user.lower()
        return PackingCheckResult(
            matches=matches,
            item="mug",
            colour=photo,
            size_seen="",
            confidence=0.9,
            mismatch_attribute="none" if matches else "colour",
        )


def _set(tmp_path, rows: list[dict[str, Any]], photos: dict[str, bytes]):
    folder = tmp_path / "photos"
    folder.mkdir()
    for name, data in photos.items():
        (folder / name).write_bytes(data)
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(yaml.safe_dump({"rows": rows}))
    return manifest, folder


def test_PK_05_mismatch_recall_counts_check_as_caught_and_reports_false_alarms(tmp_path):
    photos = {"red.png": PNG + b":red", "blue.png": PNG + b":blue"}
    rows = [
        {"photo": "red.png", "order_line": "Red mug", "label": "match"},
        {"photo": "red.png", "order_line": "Blue mug", "label": "mismatch", "attribute": "colour"},
        {"photo": "blue.png", "order_line": "Blue mug", "label": "match"},
        {
            "photo": "blue.png",
            "order_line": "Green mug",
            "label": "mismatch",
            "attribute": "colour",
        },
    ]
    manifest, folder = _set(tmp_path, rows, photos)
    loaded = load_rows(manifest, folder)
    out = summarise([check_row(r, Sees(), folder) for r in loaded])
    assert (out["scored"], out["not_run"]) == (4, 0)
    assert out["mismatch_recall"] == 1.0 and out["named_as_mismatch"] == 1.0
    assert out["attribute_correct"] == 1.0 and out["false_alarms"] == 0.0


def test_PK_05_a_photo_the_model_did_not_answer_is_not_run(tmp_path):
    manifest, folder = _set(
        tmp_path,
        [{"photo": "a.png", "order_line": "Red mug", "label": "match"}],
        {"a.png": PNG},
    )
    [row] = load_rows(manifest, folder)
    out = summarise([check_row(row, Recorder(None, tmp_path / "rec.jsonl"), folder)])
    assert (out["scored"], out["not_run"], out["mismatch_recall"]) == (0, 1, None)


def test_PK_05_two_photos_with_one_order_line_never_share_a_recorded_answer(tmp_path):
    photos = {"red.png": PNG + b":red", "blue.png": PNG + b":blue"}
    rows = [
        {"photo": "red.png", "order_line": "Red mug", "label": "match"},
        {"photo": "blue.png", "order_line": "Red mug", "label": "mismatch", "attribute": "colour"},
    ]
    manifest, folder = _set(tmp_path, rows, photos)
    recorder = Recorder(Sees(), tmp_path / "rec.jsonl")
    verdicts = [check_row(r, recorder, folder)["verdict"] for r in load_rows(manifest, folder)]
    assert verdicts == ["match", "mismatch"] and recorder.asked == 2


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"photo": "a.png", "order_line": "Mug", "label": "maybe"}, "label must be"),
        ({"photo": "a.png", "order_line": "Mug", "label": "mismatch"}, "names its attribute"),
        ({"photo": "a.png", "order_line": " ", "label": "match"}, "order_line is empty"),
        ({"photo": "nope.png", "order_line": "Mug", "label": "match"}, "photo missing"),
        ({"photo": "a.gif", "order_line": "Mug", "label": "match"}, "photo missing"),
    ],
)
def test_PK_05_a_malformed_manifest_row_is_refused(tmp_path, row, message):
    manifest, folder = _set(tmp_path, [row], {"a.png": PNG, "a.gif": PNG})
    with pytest.raises(ManifestError, match=message):
        load_rows(manifest, folder)


def test_PK_05_the_shipped_manifest_is_valid():
    load_rows()  # empty until the human adds the photos; must never be malformed
