"""PR-02 (development): the receipt key survives a restart, so stored receipts keep verifying."""

from __future__ import annotations

import pytest

from app.runtime import ConfigError, _receipt_signer


def test_PR_02_dev_receipt_key_is_stable_across_restarts(tmp_path, monkeypatch):
    monkeypatch.delenv("RECEIPT_SIGNING_KEY", raising=False)
    url = f"sqlite:///{tmp_path / 'dev.db'}"
    first = _receipt_signer("development", url)
    second = _receipt_signer("development", url)
    assert first.public_key_b64 == second.public_key_b64
    assert (tmp_path / "dev.db.receipt-key").stat().st_mode & 0o077 == 0


def test_PR_02_in_memory_database_gets_a_throwaway_key(monkeypatch):
    monkeypatch.delenv("RECEIPT_SIGNING_KEY", raising=False)
    a = _receipt_signer("development", "sqlite:///:memory:")
    b = _receipt_signer("development", "sqlite:///:memory:")
    assert a.public_key_b64 != b.public_key_b64


def test_PR_02_production_still_requires_the_key(monkeypatch, tmp_path):
    monkeypatch.delenv("RECEIPT_SIGNING_KEY", raising=False)
    with pytest.raises(ConfigError):
        _receipt_signer("production", f"sqlite:///{tmp_path / 'p.db'}")
