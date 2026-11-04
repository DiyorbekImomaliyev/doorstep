"""Shippo track_updated handling: map carrier status, apply to the order via state.py."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.worker.models import WebhookInbox


def handle_shippo_track_updated(session: Session, row: WebhookInbox) -> None:
    raise NotImplementedError
