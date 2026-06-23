from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.core.database import SessionLocal


def get_db() -> Iterator[Session]:
    """Uma Session por request. Commit no fim do fluxo bem-sucedido; rollback em erro."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
