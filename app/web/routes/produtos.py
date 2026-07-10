from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.controllers.produto_controller import produto_controller
from app.core.errors import NaoEncontradoError, RegraNegocioError
from app.core.imagens import remover_imagem, salvar_imagem_variacao
from app.core.templates import templates
from app.deps.auth import get_current_user, require_role
from app.deps.db import get_db
from app.models.categoria import Categoria
from app.models.produto import ProdutoVariacao
from app.models.usuario import Usuario
from app.schemas.produto import pode_ver_custo
from app.web.routes._flash import redirect_ok

router = APIRouter()

# Tamanho do bloco no scroll infinito da listagem de produtos.
_BLOCO = 50


def _ctx_paginacao(produtos: list, q: str, offset: int) -> dict:
    """Contexto de paginação para o fragmento de linhas (scroll infinito)."""
    tem_mais = (not q) and (len(produtos) == _BLOCO)
    return {"q": q, "offset": offset, "tem_mais": tem_mais, "proximo_offset": offset + _BLOCO}


def _categorias(db: Session) -> list[Categoria]:
    return list(db.scalars(select(Categoria).order_by(Categoria.nome)))


def _get_variacao(db: Session, variacao_id: int) -> ProdutoVariacao:
    variacao = db.get(ProdutoVariacao, variacao_id)
    if variacao is None:
        raise NaoEncontradoError("Variação não encontrada.")
    return variacao


@router.get("/produtos", response_class=HTMLResponse)
def listar_produtos(
    request: Request,
    q: str = "",
    ok: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    produtos = produto_controller.listar(db, q or None, limit=_BLOCO, offset=0)
    contexto = {
        "user": usuario,
        "titulo": "Produtos",
        "produtos": produtos,
        "pode_editar": usuario.perfil == "admin",
        "ver_custo": pode_ver_custo(usuario.perfil),
        "mensagem_ok": ok or None,
        **_ctx_paginacao(produtos, q, 0),
    }
    return templates.TemplateResponse(request, "produtos/index.html", contexto)


@router.get("/produtos/busca", response_class=HTMLResponse)
def busca_produtos(
    request: Request,
    q: str = "",
    offset: int = 0,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    produtos = produto_controller.listar(db, q or None, limit=_BLOCO, offset=offset)
    contexto = {
        "user": usuario,
        "produtos": produtos,
        "pode_editar": usuario.perfil == "admin",
        "ver_custo": pode_ver_custo(usuario.perfil),
        **_ctx_paginacao(produtos, q, offset),
    }
    return templates.TemplateResponse(request, "produtos/_linhas.html", contexto)


@router.get("/produtos/novo", response_class=HTMLResponse)
def form_novo_produto(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    contexto = {
        "user": usuario,
        "titulo": "Novo produto",
        "produto": None,
        "categorias": _categorias(db),
        "ver_custo": pode_ver_custo(usuario.perfil),
    }
    return templates.TemplateResponse(request, "produtos/form.html", contexto)


@router.get("/produtos/{produto_id}/editar", response_class=HTMLResponse)
def form_editar_produto(
    request: Request,
    produto_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    produto = produto_controller.obter(db, produto_id)
    contexto = {
        "user": usuario,
        "titulo": f"Editar {produto.codigo}",
        "produto": produto,
        "categorias": _categorias(db),
        "ver_custo": pode_ver_custo(usuario.perfil),
    }
    return templates.TemplateResponse(request, "produtos/form.html", contexto)


@router.post("/produtos", response_class=HTMLResponse)
async def criar_produto(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    raw = await request.form()
    form = dict(raw)
    # listas paralelas para variações/códigos
    form["var_cor"] = raw.getlist("var_cor")
    form["var_modo"] = raw.getlist("var_modo")
    form["var_estoque"] = raw.getlist("var_estoque")
    form["var_minimo"] = raw.getlist("var_minimo")
    form["var_rotulo"] = raw.getlist("var_rotulo")
    form["cod_alt"] = raw.getlist("cod_alt")
    produto = produto_controller.criar(db, form)
    # Vai direto à edição para enviar as fotos por cor (não precisa reabrir o produto).
    return redirect_ok(f"/produtos/{produto.id}/editar", "Produto cadastrado com sucesso.")


@router.post("/produtos/{produto_id}", response_class=HTMLResponse)
async def atualizar_produto(
    request: Request,
    produto_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    form = dict(await request.form())
    produto_controller.atualizar(db, produto_id, form)
    return redirect_ok("/produtos", "Produto atualizado com sucesso.")


@router.post("/produtos/{produto_id}/inativar", response_class=HTMLResponse)
async def inativar_produto(
    request: Request,
    produto_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    produto_controller.inativar(db, produto_id)
    return redirect_ok("/produtos", "Produto inativado.")


@router.post("/produtos/variacao/{variacao_id}/cor", response_class=HTMLResponse)
async def renomear_cor_variacao(
    request: Request,
    variacao_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    form = dict(await request.form())
    variacao = produto_controller.renomear_variacao(db, variacao_id, form)
    db.flush()
    return templates.TemplateResponse(
        request, "produtos/_thumb_variacao.html", {"variacao": variacao}
    )


@router.post("/produtos/variacao/{variacao_id}/imagem", response_class=HTMLResponse)
async def enviar_imagem_variacao(
    request: Request,
    variacao_id: int,
    imagem: UploadFile = File(...),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    variacao = _get_variacao(db, variacao_id)
    # Rejeita cedo (antes de carregar tudo em memória) se o tamanho já vier grande.
    if imagem.size is not None and imagem.size > 8 * 1024 * 1024:
        raise RegraNegocioError("Imagem muito grande (máximo 8 MB).")
    conteudo = await imagem.read(8 * 1024 * 1024 + 1)
    variacao.imagem_url = salvar_imagem_variacao(
        variacao.id, conteudo, anterior=variacao.imagem_url
    )
    db.flush()
    return templates.TemplateResponse(
        request, "produtos/_thumb_variacao.html", {"variacao": variacao}
    )


@router.post("/produtos/variacao/{variacao_id}/imagem/remover", response_class=HTMLResponse)
async def remover_imagem_variacao(
    request: Request,
    variacao_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    variacao = _get_variacao(db, variacao_id)
    remover_imagem(variacao.imagem_url)
    variacao.imagem_url = None
    db.flush()
    return templates.TemplateResponse(
        request, "produtos/_thumb_variacao.html", {"variacao": variacao}
    )


@router.post("/produtos/{produto_id}/variacao", response_class=HTMLResponse)
async def adicionar_variacao_produto(
    request: Request,
    produto_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    form = dict(await request.form())
    variacao = produto_controller.criar_variacao(db, produto_id, form, usuario.id)
    return templates.TemplateResponse(
        request, "produtos/_thumb_variacao.html", {"variacao": variacao}
    )


@router.post("/produtos/variacao/{variacao_id}/remover", response_class=HTMLResponse)
async def remover_variacao_produto(
    request: Request,
    variacao_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    variacao, acao = produto_controller.remover_variacao(db, variacao_id)
    if acao == "deletada":
        # Card removido do DOM (HTMX troca o card por um span vazio).
        return HTMLResponse("<span></span>")
    # Inativada: re-renderiza o card com selo de inativa.
    return templates.TemplateResponse(
        request, "produtos/_thumb_variacao.html", {"variacao": variacao}
    )
