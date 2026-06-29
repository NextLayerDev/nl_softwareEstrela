from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.controllers.estoque_controller import estoque_controller
from app.controllers.inventario_controller import inventario_controller
from app.core.errors import NaoEncontradoError
from app.core.templates import templates
from app.deps.auth import require_role
from app.deps.db import get_db
from app.models.usuario import Usuario
from app.repositories.estoque_repo import estoque_repo
from app.schemas.estoque import AjusteCreate, ContagemCreate, EntradaCreate, InventarioCreate

router = APIRouter()

_TODOS = ("admin", "vendedor", "financeiro", "funcionario")


# ============================================================ ESTOQUE (consulta)
@router.get("/estoque", response_class=HTMLResponse)
def index_estoque(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_TODOS)),
):
    variacoes = (
        estoque_repo.busca_localizacao(db, q) if q else estoque_repo.listar_variacoes_ativas(db)
    )
    contexto = {
        "user": usuario,
        "titulo": "Estoque",
        "variacoes": variacoes,
        "q": q,
        "pode_entrada": usuario.perfil in ("admin", "funcionario"),
        "pode_ajuste": usuario.perfil == "admin",
    }
    return templates.TemplateResponse(request, "estoque/index.html", contexto)


@router.get("/estoque/busca", response_class=HTMLResponse)
def busca_estoque(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_TODOS)),
):
    variacoes = estoque_repo.busca_localizacao(db, q) if q else []
    contexto = {
        "user": usuario,
        "variacoes": variacoes,
        "pode_entrada": usuario.perfil in ("admin", "funcionario"),
        "pode_ajuste": usuario.perfil == "admin",
    }
    return templates.TemplateResponse(request, "estoque/_linhas.html", contexto)


@router.get("/estoque/localizacao", response_class=HTMLResponse)
def localizacao(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_TODOS)),
):
    """Tela do tablet do estoque: busca dominante, cartões grandes com LOCALIZAÇÃO."""
    variacoes = estoque_repo.busca_localizacao(db, q) if q else []
    contexto = {"user": usuario, "titulo": "Localização", "variacoes": variacoes, "q": q}
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(request, "estoque/_cartoes_local.html", contexto)
    return templates.TemplateResponse(request, "estoque/localizacao.html", contexto)


@router.get("/estoque/{variacao_id}/movimentacoes", response_class=HTMLResponse)
def movimentacoes(
    request: Request,
    variacao_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_TODOS)),
):
    variacao = estoque_repo.get_variacao(db, variacao_id)
    if variacao is None:
        raise NaoEncontradoError("Variação de produto não encontrada.")
    movs = estoque_controller.historico(db, variacao_id)
    contexto = {
        "user": usuario,
        "titulo": "Movimentações",
        "variacao": variacao,
        "movimentacoes": movs,
    }
    return templates.TemplateResponse(request, "estoque/movimentacoes.html", contexto)


# ============================================================ ENTRADA / AJUSTE
@router.post("/estoque/entrada", response_class=HTMLResponse)
def post_entrada(
    request: Request,
    variacao_id: int = Form(...),
    qtd: int = Form(...),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin", "funcionario")),
):
    dados = EntradaCreate(variacao_id=variacao_id, qtd=qtd)
    variacao = estoque_controller.registrar_entrada(db, dados, usuario.id)
    contexto = {
        "user": usuario,
        "variacoes": [variacao],
        "pode_entrada": True,
        "pode_ajuste": usuario.perfil == "admin",
        "msg_ok": f"Entrada de {qtd} registrada.",
        "oob": True,
    }
    return templates.TemplateResponse(request, "estoque/_oob.html", contexto)


@router.post("/estoque/ajuste", response_class=HTMLResponse)
def post_ajuste(
    request: Request,
    variacao_id: int = Form(...),
    novo_saldo: int = Form(...),
    motivo: str = Form(...),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    dados = AjusteCreate(variacao_id=variacao_id, novo_saldo=novo_saldo, motivo=motivo)
    variacao = estoque_controller.registrar_ajuste(db, dados, usuario.id)
    contexto = {
        "user": usuario,
        "variacoes": [variacao],
        "pode_entrada": True,
        "pode_ajuste": True,
        "msg_ok": f"Ajuste para {novo_saldo} registrado.",
        "oob": True,
    }
    return templates.TemplateResponse(request, "estoque/_oob.html", contexto)


# ============================================================ INVENTÁRIO
@router.get("/inventario", response_class=HTMLResponse)
def index_inventario(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin", "funcionario")),
):
    inventarios = inventario_controller.listar(db)
    contexto = {
        "user": usuario,
        "titulo": "Inventário",
        "inventarios": inventarios,
        "pode_aplicar": usuario.perfil == "admin",
    }
    return templates.TemplateResponse(request, "estoque/inventario_index.html", contexto)


@router.post("/inventario")
def criar_inventario(
    descricao: str = Form(""),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin", "funcionario")),
):
    dados = InventarioCreate(descricao=descricao or None)
    inv = inventario_controller.criar(db, dados, usuario.id)
    return RedirectResponse(url=f"/inventario/{inv.id}", status_code=303)


@router.get("/inventario/{inventario_id}", response_class=HTMLResponse)
def detalhe_inventario(
    request: Request,
    inventario_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin", "funcionario")),
):
    inv = inventario_controller.get(db, inventario_id)
    if inv is None:
        raise NaoEncontradoError("Inventário não encontrado.")
    contexto = {
        "user": usuario,
        "titulo": f"Inventário #{inv.id}",
        "inventario": inv,
        "pode_aplicar": usuario.perfil == "admin",
    }
    return templates.TemplateResponse(request, "estoque/inventario_contagem.html", contexto)


@router.post("/inventario/{inventario_id}/contagem")
def post_contagem(
    inventario_id: int,
    item_id: int = Form(...),
    qtd_contada: int = Form(...),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin", "funcionario")),
):
    dados = ContagemCreate(item_id=item_id, qtd_contada=qtd_contada)
    inventario_controller.contar(db, inventario_id, dados)
    return RedirectResponse(url=f"/inventario/{inventario_id}", status_code=303)


@router.post("/inventario/{inventario_id}/aplicar")
def aplicar_inventario(
    inventario_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    inventario_controller.aplicar(db, inventario_id, usuario.id)
    return RedirectResponse(url=f"/inventario/{inventario_id}", status_code=303)
