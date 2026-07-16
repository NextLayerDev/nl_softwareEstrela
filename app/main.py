from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.errors import DominioError, NaoAutenticadoError
from app.core.templates import templates

logger = logging.getLogger("estrela")


# CSP pragmática: bloqueia script/estilo/imagem externos (offline-first, sem CDN) e clickjacking.
# 'unsafe-eval' é necessário para o Alpine.js; 'unsafe-inline' para os scripts/estilos inline dos
# templates. A app já tem autoescape do Jinja como defesa primária contra XSS.
# As fotos de produto são servidas pela própria app (rota /produtos/variacao/{id}/foto), então
# img-src 'self' cobre tudo — sem origem externa de MinIO/CDN.
_CSP = "; ".join(
    [
        "default-src 'self'",
        "img-src 'self' data:",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
        "style-src 'self' 'unsafe-inline'",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ]
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Em produção, sobe o agendador de jobs (ex.: marcar contas a receber atrasadas).
    scheduler = None
    if settings.ENV == "prod":
        from app.jobs import iniciar_scheduler

        scheduler = iniciar_scheduler()
        logger.info("Agendador de jobs iniciado.")

    # Listener de realtime: um por worker, escutando o canal do Postgres. Roda também em dev
    # (ao contrário do agendador), senão não dá para testar o realtime na LAN. Fica fora dos
    # testes: a suíte não sobe banco para o listener e não precisa dele.
    parar_listener: asyncio.Event | None = None
    task_listener: asyncio.Task | None = None
    if settings.REALTIME_ENABLED and "pytest" not in sys.modules:
        from app.realtime.listener import supervisionar

        parar_listener = asyncio.Event()
        task_listener = asyncio.create_task(supervisionar(parar_listener))
    try:
        yield
    finally:
        if task_listener is not None and parar_listener is not None:
            parar_listener.set()
            task_listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task_listener
        if scheduler is not None:
            scheduler.shutdown(wait=False)


app = FastAPI(title="Estrela Gestão", docs_url=None, redoc_url=None, lifespan=lifespan)

# Em produção, só aceita requisições com Host conhecido (barra Host-header spoofing).
# Em dev não aplica: o TestClient usa "testserver" e o dev acessa por localhost/IP variados.
# Se ALLOWED_HOSTS contiver "*", a checagem é desligada (proxy à frente já valida o Host).
if not settings.is_dev and "*" not in settings.allowed_hosts_list:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts_list)


@app.middleware("http")
async def cabecalhos_seguranca(request: Request, call_next):
    """Injeta headers de segurança em toda resposta."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = _CSP
    # HSTS só em produção (dev usa http; forçar HTTPS quebraria o fluxo local).
    if not settings.is_dev:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


_STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# Uploads do usuário (fotos de produto por cor) ficam num bucket PRIVADO do MinIO e são
# servidos via URL assinada (presigned) gerada a cada render — ver app/core/imagens.py.
# Não há mount local de /uploads.


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
        "empresa",
        "guia",
        "realtime",
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
