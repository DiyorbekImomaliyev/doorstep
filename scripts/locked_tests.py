"""Locked tests stay as the human approved them (CLAUDE.md, `make locked-untouched`).

Compares tests/ with the newest tests-locked-* tag. New test files are allowed. A locked file
may change in two ways only, each listed below with the human's approval:

- a placeholder test the human approved for removal is gone, and the test file that replaced it
  exists;
- an exact text the human approved for replacement is replaced (for example a fixture path after
  a move), and nothing else.

The rest of the file must be the same code. tests/unit/test_unbuilt_xfail.py holds only
placeholders and may change.

This list lives in an unlocked file, so a change to it is a change to the lock: it is printed
on every run and must be approved like one. Once the human tags a new tests-locked-* baseline,
its entries are no longer needed and are removed.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys

PLACEHOLDERS = "tests/unit/test_unbuilt_xfail.py"
# locked file -> {removed test: (approval, the test file that replaced it)}
APPROVED_REMOVALS: dict[str, dict[str, tuple[str, str]]] = {
    "tests/unit/test_pp_paypal_client.py": {
        "test_PP_09_make_offer_not_built": (
            "approved by the human on Nov 7, 2026",
            "tests/unit/test_pp_09_make_offer.py",
        ),
    },
}
# locked file -> [(text in the locked version, its approved replacement, approval)]
APPROVED_EDITS: dict[str, list[tuple[str, str, str]]] = {
    "tests/integration/test_pp_recorded.py": [
        (
            'ROOT / "recordings" / "paypal" / "capture_declined.json"',
            'ROOT / "tests" / "fixtures" / "paypal" / "capture_declined.json"',
            "approved by the human on Nov 7, 2026: the recording moved to tests/fixtures/paypal/",
        ),
    ],
}
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


GIT = shutil.which("git") or "git"


def _git(*args: str) -> str:
    # Arguments are fixed strings from this file plus tag and path names git itself listed.
    return subprocess.run(  # noqa: S603
        [GIT, *args], check=True, capture_output=True, text=True
    ).stdout


def _without(source: str, names: set[str]) -> str:
    module = ast.parse(source)
    module.body = [
        node for node in module.body if not (isinstance(node, _FUNCTIONS) and node.name in names)
    ]
    return ast.dump(module)


def problems(tag: str) -> list[str]:
    changed = _git("diff", "--name-only", "--diff-filter=MDRT", tag, "--", "tests/").split()
    found: list[str] = []
    for path, removed in APPROVED_REMOVALS.items():
        for name, (_, replacement) in removed.items():
            try:
                open(replacement, encoding="utf-8").close()
            except FileNotFoundError:
                found.append(f"{path}::{name}: its replacement {replacement} does not exist")
    for path in changed:
        if path == PLACEHOLDERS:
            continue
        removals = set(APPROVED_REMOVALS.get(path, {}))
        edits = APPROVED_EDITS.get(path, [])
        if not removals and not edits:
            found.append(f"{path}: locked file changed")
            continue
        try:
            now = open(path, encoding="utf-8").read()
        except FileNotFoundError:
            found.append(f"{path}: locked file deleted")
            continue
        before = _git("show", f"{tag}:{path}")
        for old, new, _ in edits:
            if before.count(old) != 1:
                found.append(f"{path}: approved edit not found once in the locked file: {old}")
            before = before.replace(old, new)
        still_there = removals & {
            node.name for node in ast.parse(now).body if isinstance(node, _FUNCTIONS)
        }
        if still_there:
            found.append(f"{path}: approved removal not done: {sorted(still_there)}")
        if _without(before, removals) != _without(now, removals):
            found.append(f"{path}: changed beyond the approved removals and edits")
    return found


def main() -> int:
    tags = _git("tag", "--list", "tests-locked-*", "--sort=-creatordate").split()
    if not tags:
        print("no tests-locked-* tag")
        return 1
    for path, removed in APPROVED_REMOVALS.items():
        for name, (approval, replacement) in removed.items():
            print(f"approved removal: {path}::{name} ({approval}; replaced by {replacement})")
    for path, edits in APPROVED_EDITS.items():
        for old, new, approval in edits:
            print(f"approved edit: {path}: {old} -> {new} ({approval})")
    found = problems(tags[0])
    if found:
        print(f"locked tests edited since {tags[0]}:")
        print("\n".join(found))
        return 1
    print(f"locked tests untouched since {tags[0]} (new files and approved changes allowed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
