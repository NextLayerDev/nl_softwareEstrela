from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import NaoAutenticadoError
from app.core.rate_limit import limitador_login
from app.core.security import criar_token
from app.core.templates import templates
from app.deps.auth import COOKIE_NOME
from app.deps.db import get_db
from app.services.auth_service import auth_service

router = APIRouter()

logger = logging.getLogger("estrela.auth")


def _ip_cliente(request: Request) -> str:
    """IP real do cliente. Atrás do Caddy (proxy confiável), vem no X-Forwarded-For."""
    encaminhado = request.headers.get("x-forwarded-for")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return request.client.host if request.client else "desconhecido"


@router.get("/login", response_class=HTMLResponse)
def tela_login(request: Request):
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login", response_class=HTMLResponse)
def fazer_login(
    request: Request,
    email: str = Form(...),
    senha: str = Form(...),
    db: Session = Depends(get_db),
):
    ip = _ip_cliente(request)
    chave = f"{ip}|{email.lower().strip()}"

    if limitador_login.bloqueado(chave):
        logger.warning("Login bloqueado por excesso de tentativas: %s (IP %s)", email, ip)
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "erro": "Muitas tentativas. Aguarde alguns minutos e tente novamente.",
                "email": email,
            },
            status_code=429,
        )

    try:
        usuario = auth_service.autenticar(db, email, senha)
    except NaoAutenticadoError as exc:
        limitador_login.registrar_falha(chave)
        logger.warning("Falha de login: %s (IP %s)", email, ip)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"erro": exc.mensagem, "email": email},
            status_code=401,
        )

    limitador_login.limpar(chave)
    logger.info("Login OK: %s perfil=%s (IP %s)", usuario.email, usuario.perfil, ip)

    token = criar_token(usuario.id, usuario.perfil, extra={"tv": usuario.token_version})
    resposta = RedirectResponse(url="/", status_code=303)
    resposta.set_cookie(
        key=COOKIE_NOME,
        value=token,
        httponly=True,
        samesite="strict",
        # `Secure` só quando há HTTPS de verdade na frente — NÃO baseado em is_dev. Em prod
        # sobre HTTP (o caso do servidor da cliente hoje), `Secure` marcava o cookie e o
        # navegador nunca o reenviava: loop de login. Ver HTTPS_ENABLED em core/config.py.
        secure=settings.HTTPS_ENABLED,
        max_age=settings.JWT_EXPIRES_MIN * 60,
        path="/",
    )
    return resposta


@router.get("/logout")
def logout():
    resposta = RedirectResponse(url="/login", status_code=303)
    resposta.delete_cookie(COOKIE_NOME, path="/")
    return resposta
