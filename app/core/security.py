from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from app.core.config import settings

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_senha(senha: str) -> str:
    return _pwd_context.hash(senha)


def verificar_senha(senha: str, hash_armazenado: str) -> bool:
    return _pwd_context.verify(senha, hash_armazenado)


def criar_token(subject: str | int, perfil: str, extra: dict[str, Any] | None = None) -> str:
    agora = datetime.now(tz=UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "perfil": perfil,
        "iat": agora,
        "exp": agora + timedelta(minutes=settings.JWT_EXPIRES_MIN),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decodificar_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
