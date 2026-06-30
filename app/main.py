from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.errors import DominioError, NaoAutenticadoError
from app.core.templates import templates

logger = logging.getLogger("estrela")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Em produção, sobe o agendador de jobs (ex.: marcar contas a receber atrasadas).
    scheduler = None
    if settings.ENV == "prod":
        from app.jobs import iniciar_scheduler

        scheduler = iniciar_scheduler()
        logger.info("Agendador de jobs iniciado.")
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


app = FastAPI(title="Estrela Gestão", docs_url=None, redoc_url=None, lifespan=lifespan)

_STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# Uploads do usuário (fotos de produto por cor) — servidos localmente, offline.
_UPLOADS_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"
_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_UPLOADS_DIR)), name="uploads")


def _quer_json(request: Request) -> bool:
    """Rotas de API (/api/*) e chamadas que aceitam JSON recebem JSON; o resto, HTML."""
    if request.url.path.startswith("/api"):
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


@app.exception_handler(DominioError)
async def dominio_error_handler(request: Request, exc: DominioError) -> Response:
    # Não autenticado em rota web → manda para o login.
    if isinstance(exc, NaoAutenticadoError) and not _quer_json(request):
        return RedirectResponse(url="/login", status_code=303)

    if _quer_json(request):
        return JSONResponse(status_code=exc.status_code, content={"erro": exc.mensagem})

    # Rota web: se for requisição HTMX, devolve só o fragmento de alerta.
    if request.headers.get("HX-Request") == "true":
        html = f'<div class="alerta alerta-erro" role="alert">{exc.mensagem}</div>'
        return HTMLResponse(content=html, status_code=exc.status_code)

    return templates.TemplateResponse(
        request,
        "erro.html",
        {"mensagem": exc.mensagem, "status": exc.status_code},
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def erro_inesperado_handler(request: Request, exc: Exception) -> Response:
    logger.exception("Erro inesperado em %s", request.url.path)
    if _quer_json(request):
        return JSONResponse(status_code=500, content={"erro": "Erro interno do servidor."})
    return templates.TemplateResponse(
        request,
        "erro.html",
        {"mensagem": "Ocorreu um erro inesperado.", "status": 500},
        status_code=500,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _registrar_routers() -> None:
    """Inclui os routers web/JSON que existirem. Cada módulo é opcional para que
    a aplicação suba mesmo durante o desenvolvimento incremental das frentes."""
    import importlib

    web_modulos = [
        "auth",
        "dashboard",
        "estoque",
        "produtos",
        "clientes",
        "usuarios",
        "pedidos",
        "separacao",
        "financeiro",
        "relatorios",
        "importacao",
        "guia",
    ]
    for nome in web_modulos:
        caminho = f"app.web.routes.{nome}"
        try:
            mod = importlib.import_module(caminho)
        except ModuleNotFoundError as exc:
            # Só ignora se for o próprio módulo de rota que ainda não existe.
            if exc.name == caminho:
                continue
            raise
        router = getattr(mod, "router", None)
        if router is not None:
            app.include_router(router)


_registrar_routers()
