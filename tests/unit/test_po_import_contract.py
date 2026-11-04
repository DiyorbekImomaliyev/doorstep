"""PO-01 — only app.policy (and app.worker for order writes) import PayPal write modules."""

from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

from tests.conftest import ROOT

APP = ROOT / "app"


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _module_files(exclude: tuple[str, ...]) -> list[Path]:
    return [
        p for p in APP.rglob("*.py") if not any(p.is_relative_to(APP / part) for part in exclude)
    ]


def test_PO_01_import_linter_contracts_pass():
    lint_imports = Path(sys.executable).parent / "lint-imports"
    result = subprocess.run(
        [str(lint_imports)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_PO_01_import_linter_contracts_declared():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"]
    forbidden = {tuple(c["forbidden_modules"]) for c in config["contracts"]}
    assert ("app.paypal_client.writes",) in forbidden
    assert ("app.paypal_client.writes.money",) in forbidden


def test_PO_01_only_policy_and_worker_import_paypal_writes():
    offenders = [
        str(p.relative_to(ROOT))
        for p in _module_files(("policy", "worker", "paypal_client"))
        if any(name.startswith("app.paypal_client.writes") for name in _imports(p))
    ]
    assert offenders == []


def test_PO_01_only_policy_imports_refund_and_dispute_writes():
    offenders = [
        str(p.relative_to(ROOT))
        for p in _module_files(("policy", "paypal_client"))
        if any(name.startswith("app.paypal_client.writes.money") for name in _imports(p))
    ]
    assert offenders == []
