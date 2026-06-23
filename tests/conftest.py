"""Fixtures de teste: banco isolado por teste (transação revertida), e usuários por perfil.

Cada teste roda dentro de uma transação SAVEPOINT revertida ao final, deixando o banco
limpo sem recriar schema. Requer o banco de dev já migrado (alembic upgrade head).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, engine
from app.core.security import hash_senha
from app.models.usuario import Usuario


@pytest.fixture
def db() -> Iterator[Session]:
    connection = engine.connect()
    trans = connection.begin()
    session = SessionLocal(bind=connection)
    # SAVEPOINT aninhado: reverte tudo o que o teste fizer.
    session.begin_nested()
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def _criar_usuario(db: Session, perfil: str) -> Usuario:
    u = Usuario(
        nome=perfil.capitalize(),
        email=f"{perfil}@teste.local",
        senha_hash=hash_senha("teste123"),
        perfil=perfil,
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def usuario_admin(db: Session) -> Usuario:
    return _criar_usuario(db, "admin")


@pytest.fixture
def usuario_vendedor(db: Session) -> Usuario:
    return _criar_usuario(db, "vendedor")


@pytest.fixture
def usuario_financeiro(db: Session) -> Usuario:
    return _criar_usuario(db, "financeiro")


@pytest.fixture
def usuario_funcionario(db: Session) -> Usuario:
    return _criar_usuario(db, "funcionario")
