from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from app.core.config import settings

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

_SENHA_MIN = 10


def senha_fraca(senha: str) -> str | None:
    """Devolve a mensagem de erro se a senha for fraca, ou None se estiver OK.

    Exige mínimo de 10 caracteres e ao menos 3 das 4 classes: minúscula, maiúscula,
    número e símbolo. Retornar mensagem (em vez de levantar) deixa cada chamador escolher
    a exceção certa (ValueError no schema, RegraNegocioError no service).
    """
    if len(senha) < _SENHA_MIN:
        return f"A senha deve ter ao menos {_SENHA_MIN} caracteres."
    classes = sum(
        [
            any(c.islower() for c in senha),
            any(c.isupper() for c in senha),
            any(c.isdigit() for c in senha),
            any(not c.isalnum() for c in senha),
        ]
    )
    if classes < 3:
        return "A senha deve combinar ao menos 3 de: minúscula, maiúscula, número e símbolo."
    return None


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
