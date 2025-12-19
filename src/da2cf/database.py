from __future__ import annotations

from contextlib import contextmanager
import logging
from pathlib import Path
from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine


logger = logging.getLogger(__name__)


DB_PATH = Path("data")
DB_PATH.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH / 'app.db'}", echo=False, connect_args={"check_same_thread": False}
)


def init_db() -> None:
    from . import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _run_migrations()


def _run_migrations() -> None:
    """Lightweight schema migrations for existing SQLite databases."""
    with engine.connect() as conn:
        # Add acme_sync_interval_minutes to appsettings if it does not exist.
        try:
            conn.execute(
                "ALTER TABLE appsettings ADD COLUMN acme_sync_interval_minutes INTEGER"
            )
            logger.info("Added column appsettings.acme_sync_interval_minutes")
        except Exception:
            # Column already exists or table does not exist yet; ignore.
            pass


@contextmanager
def session_scope() -> Iterator[Session]:
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
