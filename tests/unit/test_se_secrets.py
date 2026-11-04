"""SE-01: no secrets in the repository, and none in the logs (CI also runs gitleaks)."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Real-looking credentials. Obvious placeholders in tests (e.g. "shippo_test_x") do not match.
PATTERNS = {
    "anthropic key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "google api key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "shippo key": re.compile(r"shippo_(?:live|test)_[0-9a-f]{32,}"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "paypal access token": re.compile(r"A21AA[0-9A-Za-z_-]{40,}"),
    "langfuse secret": re.compile(r"sk-lf-[0-9a-f-]{20,}"),
}


def _tracked_files() -> list[Path]:
    git = shutil.which("git")
    assert git is not None
    top = subprocess.run(
        [git, "rev-parse", "--show-toplevel"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    if not top or Path(top).resolve() != ROOT:
        # e.g. mutmut's `mutants/` copy: not the repository, so there is nothing to scan here.
        pytest.skip("not run from the repository work tree")
    out = subprocess.run(
        [git, "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode()
    return [ROOT / p for p in out.split("\0") if p and not p.endswith((".lock", ".png", ".jpg"))]


def test_SE_01_no_credentials_in_tracked_files():
    hits = []
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        hits += [
            f"{path.relative_to(ROOT)}: {name}" for name, p in PATTERNS.items() if p.search(text)
        ]
    assert hits == []


def test_SE_01_env_files_are_ignored():
    tracked = {p.name for p in _tracked_files()}
    assert ".env" not in tracked
    assert ".env.example" in tracked


SECRETS = {
    "PAYPAL_CLIENT_ID": "AbcSandboxClientIdValue1234567890",
    "PAYPAL_CLIENT_SECRET": "EFGsandboxSecretValueThatMustNeverBeLogged987",
    # Built at runtime so the literal never sits in a tracked file (the scan above reads them).
    "SHIPPO_API_KEY": "shippo_" + "test_" + "0123456789abcdef" * 2,
    "SIGNING_SECRET": "link-signing-secret-that-must-stay-private",
    "RECEIPT_SIGNING_KEY": "receipt-secret-that-must-stay-private-123",
    "ANTHROPIC_API_KEY": "sk-" + "ant-fake-but-long-enough-0123456789abcdef",
    "GEMINI_API_KEY": "AI" + "zaFakeButLongEnoughKey0123456789abcdefg",
}


def test_SE_01_startup_and_a_request_log_no_secrets(monkeypatch, tmp_path, caplog):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.runtime import build_runtime

    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'se.db'}")
    monkeypatch.setenv("APP_ENV", "development")
    caplog.set_level(logging.DEBUG)
    rt = build_runtime()
    client = TestClient(create_app(rt.deps))
    client.get("/healthz")
    client.post("/demo/seed?scratch=1")
    logged = caplog.text
    leaked = [name for name, value in SECRETS.items() if value in logged]
    assert leaked == []


@pytest.mark.parametrize("name", ["SIGNING_SECRET", "RECEIPT_SIGNING_KEY"])
def test_SE_01_missing_secret_errors_name_the_variable_not_a_value(monkeypatch, name):
    from app.runtime import ConfigError, _link_secret, _receipt_signer

    monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigError) as err:
        (_link_secret if name == "SIGNING_SECRET" else _receipt_signer)("production")
    assert name in str(err.value)
