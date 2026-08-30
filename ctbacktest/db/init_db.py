"""Create all tables. Idempotent (checkfirst=True)."""

from __future__ import annotations

from ctbacktest.db.models import Base
from ctbacktest.db.session import get_engine


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine, checkfirst=True)


if __name__ == "__main__":
    init_db()
    print("Database schema created (or already up to date).")
