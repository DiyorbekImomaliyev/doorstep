"""R30 (review S11): every foreign key is indexed, on new databases and on old ones.

Postgres does not index foreign keys by itself, and every screen joins through them (orders by
seller, shipments by order, events by shipment). Base.metadata.create_all never alters a table that
already exists, so an index added to a model later would never reach Render's database: startup
creates any index that is missing.
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from app.db import Base, ensure_indexes, import_all_models


def _leading_columns(table) -> set[str]:
    """Columns that lead a primary key, an index or a unique constraint (all usable for lookups)."""
    lead = {table.primary_key.columns.values()[0].name}
    lead |= {next(iter(i.columns)).name for i in table.indexes}
    lead |= {
        next(iter(c.columns)).name
        for c in table.constraints
        if c.__class__.__name__ == "UniqueConstraint" and list(c.columns)
    }
    lead |= {c.name for c in table.columns if c.unique}
    return lead


def test_R30_every_foreign_key_leads_an_index():
    import_all_models()
    missing = [
        f"{t.name}.{fk.parent.name}"
        for t in Base.metadata.sorted_tables
        for fk in t.foreign_keys
        if fk.parent.name not in _leading_columns(t)
    ]
    assert missing == []


def test_R30_startup_adds_an_index_an_old_database_lacks(tmp_path):
    import_all_models()
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text("DROP INDEX ix_shipments_order_id"))
    assert "ix_shipments_order_id" not in {
        i["name"] for i in inspect(engine).get_indexes("shipments")
    }
    ensure_indexes(engine)
    ensure_indexes(engine)  # idempotent: a second process starting at once is harmless
    assert "ix_shipments_order_id" in {i["name"] for i in inspect(engine).get_indexes("shipments")}
