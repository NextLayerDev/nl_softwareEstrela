from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import NaoAutenticadoError
from app.core.security import criar_token
from app.core.templates import templates
from app.deps.auth import COOKIE_NOME
from app.deps.db import get_db
from app.services.auth_service import auth_service

router = APIRouter()


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
    try:
        usuario = auth_service.autenticar(db, email, senha)
    except NaoAutenticadoError as exc:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"erro": exc.mensagem, "email": email},
            status_code=401,
        )

    token = criar_token(usuario.id, usuario.perfil)
    resposta = RedirectResponse(url="/", status_code=303)
    resposta.set_cookie(
        key=COOKIE_NOME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=not settings.is_dev,
        max_age=settings.JWT_EXPIRES_MIN * 60,
    )
    return resposta


@router.get("/logout")
def logout():
    resposta = RedirectResponse(url="/login", status_code=303)
    resposta.delete_cookie(COOKIE_NOME)
    return resposta
