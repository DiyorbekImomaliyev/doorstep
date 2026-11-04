"""Fail if any acceptance ID in specs/ACCEPTANCE.md has no test.

A test covers an ID when its function name contains it, e.g. ``test_PO_06_...`` covers PO-06.
Tests under tests/ and evals/ count. Usage:

    python scripts/trace.py                    # every ID
    python scripts/trace.py --prefixes PP,WH   # only these modules (one phase)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ID_LINE = re.compile(r"^- ([A-Z0-9]+)-(\d{2})\b", re.MULTILINE)
TEST_DEF = re.compile(r"^\s*(?:async\s+)?def (test_([A-Z0-9]+)_(\d{2})_\w*)", re.MULTILINE)


def acceptance_ids(text: str) -> list[str]:
    return [f"{m.group(1)}-{m.group(2)}" for m in ID_LINE.finditer(text)]


def covered_ids(paths: list[Path]) -> set[str]:
    found: set[str] = set()
    for base in paths:
        for path in base.rglob("test_*.py"):
            for m in TEST_DEF.finditer(path.read_text(encoding="utf-8")):
                found.add(f"{m.group(2)}-{m.group(3)}")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefixes", default="", help="comma-separated module prefixes")
    args = parser.parse_args(argv)

    ids = acceptance_ids((ROOT / "specs" / "ACCEPTANCE.md").read_text(encoding="utf-8"))
    if args.prefixes:
        wanted = {p.strip() for p in args.prefixes.split(",") if p.strip()}
        ids = [i for i in ids if i.split("-")[0] in wanted]
    covered = covered_ids([ROOT / "tests", ROOT / "evals"])
    missing = [i for i in ids if i not in covered]
    print(f"trace: {len(ids) - len(missing)}/{len(ids)} acceptance IDs have a test")
    for i in missing:
        print(f"  missing: {i}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
