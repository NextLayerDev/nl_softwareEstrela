from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.controllers.produto_controller import produto_controller
from app.core.errors import NaoEncontradoError, RegraNegocioError
from app.core.imagens import caminho_foto_variacao, salvar_imagem_variacao
from app.core.templates import templates
from app.deps.auth import get_current_user, require_role
from app.deps.db import get_db
from app.models.categoria import Categoria
from app.models.enums import tem_perfil
from app.models.produto import ProdutoVariacao
from app.models.usuario import Usuario
from app.schemas.produto import pode_definir_minimo, pode_ver_custo
from app.web.routes._flash import redirect_ok

router = APIRouter()

# Quem cadastra e edita produto. O vendedor entrou aqui junto com a redução para dois
# perfis: quem vende é quem descobre que o cadastro está errado. O que continua sendo
# só do admin são os dois campos de dinheiro sensível — `preco_custo` e `preco_minimo`
# —, filtrados pelas flags `ver_custo` e `pode_definir_minimo` do contexto.
_EDITA = ("admin", "vendedor")

# Tamanho do bloco no scroll infinito da listagem de produtos.
_BLOCO = 50


def _ctx_paginacao(produtos: list, q: str, offset: int, categoria_id: int | None = None) -> dict:
    """Contexto de paginação para o fragmento de linhas (scroll infinito)."""
    tem_mais = (not q) and (len(produtos) == _BLOCO)
    return {
        "q": q,
        "offset": offset,
        "tem_mais": tem_mais,
        "proximo_offset": offset + _BLOCO,
        "categoria_id": categoria_id,
    }


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
    categoria_id: str | None = None,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    # "Todas as categorias" envia categoria_id="" (string vazia); o FastAPI rejeita
    # "" em `int | None` (422), então recebemos como str e coerciamos vazio → None.
    cat = int(categoria_id) if categoria_id else None
    produtos = produto_controller.listar(db, q or None, limit=_BLOCO, offset=0, categoria_id=cat)
    contexto = {
        "user": usuario,
        "titulo": "Produtos",
        "produtos": produtos,
        "pode_editar": tem_perfil(usuario.perfil, *_EDITA),
        "ver_custo": pode_ver_custo(usuario.perfil),
        "mensagem_ok": ok or None,
        "categorias": _categorias(db),
        "categoria_id": cat,
        **_ctx_paginacao(produtos, q, 0, cat),
    }
    return templates.TemplateResponse(request, "produtos/index.html", contexto)


@router.get("/produtos/busca", response_class=HTMLResponse)
def busca_produtos(
    request: Request,
    q: str = "",
    offset: int = 0,
    categoria_id: str | None = None,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    cat = int(categoria_id) if categoria_id else None
    produtos = produto_controller.listar(
        db, q or None, limit=_BLOCO, offset=offset, categoria_id=cat
    )
    contexto = {
        "user": usuario,
        "produtos": produtos,
        "pode_editar": tem_perfil(usuario.perfil, *_EDITA),
        "ver_custo": pode_ver_custo(usuario.perfil),
        **_ctx_paginacao(produtos, q, offset, cat),
    }
    return templates.TemplateResponse(request, "produtos/_linhas.html", contexto)


# Antes de qualquer rota /produtos/{produto_id}: senão o FastAPI casaria "linha" como id (422).
@router.get("/produtos/linha/{produto_id}", response_class=HTMLResponse)
def fragmento_linha_produto(
    request: Request,
    produto_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Uma linha da listagem, re-buscada pelo realtime quando aquele produto muda.

    Troca cirúrgica: refazer a lista chamaria /produtos/busca e resetaria o scroll infinito,
    jogando o operador de volta ao primeiro bloco.
    """
    produto = produto_controller.obter(db, produto_id)
    contexto = {
        "user": usuario,
        "produtos": [produto],
        "pode_editar": tem_perfil(usuario.perfil, *_EDITA),
        "ver_custo": pode_ver_custo(usuario.perfil),
    }
    return templates.TemplateResponse(request, "produtos/_linhas.html", contexto)


@router.get("/produtos/novo", response_class=HTMLResponse)
def form_novo_produto(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_EDITA)),
):
    contexto = {
        "user": usuario,
        "titulo": "Novo produto",
        "produto": None,
        "categorias": _categorias(db),
        "ver_custo": pode_ver_custo(usuario.perfil),
        "pode_definir_minimo": pode_definir_minimo(usuario.perfil),
    }
    return templates.TemplateResponse(request, "produtos/form.html", contexto)


@router.get("/produtos/{produto_id}/editar", response_class=HTMLResponse)
def form_editar_produto(
    request: Request,
    produto_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_EDITA)),
):
    produto = produto_controller.obter(db, produto_id)
    contexto = {
        "user": usuario,
        "titulo": f"Editar {produto.codigo}",
        "produto": produto,
        "categorias": _categorias(db),
        "ver_custo": pode_ver_custo(usuario.perfil),
        "pode_definir_minimo": pode_definir_minimo(usuario.perfil),
    }
    return templates.TemplateResponse(request, "produtos/form.html", contexto)


@router.post("/produtos", response_class=HTMLResponse)
async def criar_produto(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_EDITA)),
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
    form["faixa_min_qtd"] = raw.getlist("faixa_min_qtd")
    form["faixa_preco"] = raw.getlist("faixa_preco")
    form["espec_rotulo"] = raw.getlist("espec_rotulo")
    form["espec_valor"] = raw.getlist("espec_valor")
    produto = produto_controller.criar(db, form)
    # Vai direto à edição para enviar as fotos por cor (não precisa reabrir o produto).
    return redirect_ok(f"/produtos/{produto.id}/editar", "Produto cadastrado com sucesso.")


@router.post("/produtos/{produto_id}", response_class=HTMLResponse)
async def atualizar_produto(
    request: Request,
    produto_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_EDITA)),
):
    raw = await request.form()
    form = dict(raw)
    # Lista paralela para os códigos alternativos (edição pode adicionar/remover).
    form["cod_alt"] = raw.getlist("cod_alt")
    # Faixas de preço. Quem decide se elas entram é o sentinela `tem_editor_faixas`,
    # que só existe quando o formulário renderizou o editor — ver o controller.
    form["faixa_min_qtd"] = raw.getlist("faixa_min_qtd")
    form["faixa_preco"] = raw.getlist("faixa_preco")
    form["espec_rotulo"] = raw.getlist("espec_rotulo")
    form["espec_valor"] = raw.getlist("espec_valor")
    produto_controller.atualizar(db, produto_id, form)
    return redirect_ok("/produtos", "Produto atualizado com sucesso.")


@router.post("/produtos/{produto_id}/inativar", response_class=HTMLResponse)
async def inativar_produto(
    request: Request,
    produto_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_EDITA)),
):
    produto_controller.inativar(db, produto_id)
    return redirect_ok("/produtos", "Produto inativado.")


@router.post("/produtos/{produto_id}/reativar", response_class=HTMLResponse)
async def reativar_produto(
    request: Request,
    produto_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_EDITA)),
):
    produto_controller.reativar(db, produto_id)
    return redirect_ok(f"/produtos/{produto_id}/editar", "Produto reativado.")


@router.post("/produtos/variacao/{variacao_id}/cor", response_class=HTMLResponse)
async def renomear_cor_variacao(
    request: Request,
    variacao_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_EDITA)),
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
    usuario: Usuario = Depends(require_role(*_EDITA)),
):
    variacao = _get_variacao(db, variacao_id)
    # Rejeita cedo (antes de carregar tudo em memória) se o tamanho já vier grande.
    if imagem.size is not None and imagem.size > 8 * 1024 * 1024:
        raise RegraNegocioError("Imagem muito grande (máximo 8 MB).")
    conteudo = await imagem.read(8 * 1024 * 1024 + 1)
    if len(conteudo) > 8 * 1024 * 1024:
        raise RegraNegocioError("Imagem muito grande (máximo 8 MB).")
    variacao.imagem_dados = salvar_imagem_variacao(variacao.id, conteudo)
    variacao.imagem_url = caminho_foto_variacao(variacao.id)
    db.flush()
    return templates.TemplateResponse(
        request, "produtos/_thumb_variacao.html", {"variacao": variacao}
    )


@router.post("/produtos/variacao/{variacao_id}/imagem/remover", response_class=HTMLResponse)
async def remover_imagem_variacao(
    request: Request,
    variacao_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_EDITA)),
):
    variacao = _get_variacao(db, variacao_id)
    variacao.imagem_dados = None
    variacao.imagem_url = None
    db.flush()
    return templates.TemplateResponse(
        request, "produtos/_thumb_variacao.html", {"variacao": variacao}
    )


@router.get("/produtos/variacao/{variacao_id}/foto")
def foto_variacao(
    variacao_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Serve os bytes da foto da variação (mesma origem, exige login). Offline-first."""
    variacao = _get_variacao(db, variacao_id)
    if not variacao.imagem_dados:
        raise NaoEncontradoError("Esta variação não tem foto.")
    return Response(
        content=variacao.imagem_dados,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.post("/produtos/{produto_id}/variacao", response_class=HTMLResponse)
async def adicionar_variacao_produto(
    request: Request,
    produto_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_EDITA)),
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
    usuario: Usuario = Depends(require_role(*_EDITA)),
):
    variacao, acao = produto_controller.remover_variacao(db, variacao_id)
    if acao == "deletada":
        # Card removido do DOM (HTMX troca o card por um span vazio).
        return HTMLResponse("<span></span>")
    # Inativada: re-renderiza o card com selo de inativa.
    return templates.TemplateResponse(
        request, "produtos/_thumb_variacao.html", {"variacao": variacao}
    )


@router.post("/produtos/variacao/{variacao_id}/reativar", response_class=HTMLResponse)
async def reativar_variacao_produto(
    request: Request,
    variacao_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_EDITA)),
):
    variacao = produto_controller.reativar_variacao(db, variacao_id)
    # Re-renderiza o card já ativo (sem o selo de inativa).
    return templates.TemplateResponse(
        request, "produtos/_thumb_variacao.html", {"variacao": variacao}
    )
