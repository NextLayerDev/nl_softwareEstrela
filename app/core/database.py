from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    """Base declarativa de todos os models."""


@contextmanager
def uow(db: Session) -> Iterator[Session]:
    """Unit of Work: confirma no fim ou faz rollback em erro.

    Os services NÃO fazem commit; quem fecha a transação é a rota/controller
    via este helper (ou o get_db, que também faz commit no fim do request).
    """
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
