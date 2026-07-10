"""Testes do CRUD de produtos: serviço (regras) e rotas (custo oculto + RBAC)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import RegraNegocioError
from app.main import app
from app.models.enums import EstoqueModo, TipoMov
from app.models.movimentacao import MovimentacaoEstoque
from app.models.produto import Produto, ProdutoVariacao
from app.schemas.produto import CodigoAltCreate, ProdutoCreate, ProdutoUpdate, VariacaoCreate
from app.services.estoque_service import estoque_service
from app.services.produto_service import produto_service

PRECO_CUSTO = "99.77"


def _login(client: TestClient, perfil: str) -> None:
    resp = client.post(
        "/login",
        data={"email": f"{perfil}@estrela.local", "senha": "estrela123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text


def _codigo() -> str:
    return f"TST-{uuid.uuid4().hex[:8].upper()}"


def test_criar_produto_codigo_duplicado_falha(db: Session) -> None:
    codigo = _codigo()
    produto_service.criar(db, ProdutoCreate(codigo=codigo, descricao="CANETA AZUL"))
    db.flush()
    with pytest.raises(RegraNegocioError):
        produto_service.criar(db, ProdutoCreate(codigo=codigo, descricao="OUTRA"))


def test_criar_produto_com_variacoes(db: Session) -> None:
    p = produto_service.criar(
        db,
        ProdutoCreate(
            codigo=_codigo(),
            descricao="LAPIS 6B",
            variacoes=[VariacaoCreate(cor="PRETO", estoque_fisico=10)],
        ),
    )
    db.flush()
    assert len(p.variacoes) == 1
    assert p.variacoes[0].cor == "PRETO"


def test_inativar_e_soft_delete(db: Session) -> None:
    p = produto_service.criar(db, ProdutoCreate(codigo=_codigo(), descricao="BORRACHA"))
    db.flush()
    produto_service.inativar(db, p.id)
    assert p.ativo is False


def test_custo_visivel_para_admin() -> None:
    client = TestClient(app)
    _login(client, "admin")
    codigo = _codigo()
    client.post(
        "/produtos",
        data={
            "codigo": codigo,
            "descricao": "PRODUTO CUSTO ADMIN",
            "preco_pouca_qtd": "10",
            "preco_muita_qtd": "8",
            "preco_custo": PRECO_CUSTO,
            "ativo": "on",
        },
        follow_redirects=False,
    )
    resp = client.get(f"/produtos?q={codigo}")
    assert resp.status_code == 200
    # admin vê a coluna de custo
    assert "Custo" in resp.text
    # limpeza: inativa o produto criado
    _remover(codigo)


def test_custo_oculto_para_vendedor() -> None:
    client = TestClient(app)
    _login(client, "admin")
    codigo = _codigo()
    client.post(
        "/produtos",
        data={
            "codigo": codigo,
            "descricao": "PRODUTO CUSTO OCULTO",
            "preco_pouca_qtd": "10",
            "preco_muita_qtd": "8",
            "preco_custo": PRECO_CUSTO,
            "ativo": "on",
        },
        follow_redirects=False,
    )
    vend = TestClient(app)
    _login(vend, "vendedor")
    resp = vend.get(f"/produtos?q={codigo}")
    assert resp.status_code == 200
    # vendedor NÃO vê o preço de custo no HTML
    assert "99,77" not in resp.text
    assert PRECO_CUSTO not in resp.text
    _remover(codigo)


def test_vendedor_nao_cria_produto() -> None:
    client = TestClient(app)
    _login(client, "vendedor")
    resp = client.post(
        "/produtos",
        data={"codigo": _codigo(), "descricao": "X", "ativo": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def _remover(codigo: str) -> None:
    """Remove fisicamente o produto de teste (criado via TestClient/commit real)."""
    from app.core.database import SessionLocal

    s = SessionLocal()
    try:
        p = s.query(Produto).filter(Produto.codigo == codigo).one_or_none()
        if p is not None:
            s.delete(p)
            s.commit()
    finally:
        s.close()


# --------------------------------------------------------------------------- #
# Variações: produto "sem cor" ganha padrão; adicionar/remover cor em edição. #
# --------------------------------------------------------------------------- #


def test_criar_produto_sem_variacoes_gera_padrao(db: Session) -> None:
    """Produto cadastrado sem variações recebe uma variação padrão (cor='')."""
    p = produto_service.criar(db, ProdutoCreate(codigo=_codigo(), descricao="SEM COR"))
    db.flush()
    assert len(p.variacoes) == 1
    assert p.variacoes[0].cor == ""


def test_adicionar_variacao_com_estoque_gera_entrada(db: Session, usuario_admin) -> None:
    p = produto_service.criar(db, ProdutoCreate(codigo=_codigo(), descricao="PROD A"))
    db.flush()
    v = produto_service.adicionar_variacao(
        db,
        p.id,
        VariacaoCreate(cor="Azul", estoque_modo=EstoqueModo.EXATO, estoque_fisico=10),
        usuario_id=usuario_admin.id,
    )
    db.flush()
    assert v.cor == "Azul"
    assert v.estoque_fisico == 10  # entrada via movimentação, nunca set direto
    movs = list(
        db.scalars(
            select(MovimentacaoEstoque).where(MovimentacaoEstoque.produto_variacao_id == v.id)
        )
    )
    assert any(m.tipo == TipoMov.ENTRADA and m.qtd == 10 for m in movs)


def test_adicionar_variacao_cor_duplicada_falha(db: Session, usuario_admin) -> None:
    p = produto_service.criar(
        db,
        ProdutoCreate(codigo=_codigo(), descricao="PROD B", variacoes=[VariacaoCreate(cor="Azul")]),
    )
    db.flush()
    with pytest.raises(RegraNegocioError):
        produto_service.adicionar_variacao(
            db, p.id, VariacaoCreate(cor="Azul"), usuario_id=usuario_admin.id
        )


def test_remover_variacao_limpa_deleta(db: Session, usuario_admin) -> None:
    p = produto_service.criar(db, ProdutoCreate(codigo=_codigo(), descricao="PROD C"))
    db.flush()
    v = produto_service.adicionar_variacao(
        db, p.id, VariacaoCreate(cor="Verde", estoque_fisico=0), usuario_id=usuario_admin.id
    )
    db.flush()
    vid = v.id
    variacao, acao = produto_service.remover_variacao(db, vid)
    assert acao == "deletada"
    assert db.get(ProdutoVariacao, vid) is None


def test_remover_variacao_com_saldo_bloqueia(db: Session, usuario_admin) -> None:
    p = produto_service.criar(db, ProdutoCreate(codigo=_codigo(), descricao="PROD D"))
    db.flush()
    v = produto_service.adicionar_variacao(
        db, p.id, VariacaoCreate(cor="Vermelho", estoque_fisico=5), usuario_id=usuario_admin.id
    )
    db.flush()
    with pytest.raises(RegraNegocioError):
        produto_service.remover_variacao(db, v.id)


def test_remover_variacao_com_historico_inativa(db: Session, usuario_admin) -> None:
    p = produto_service.criar(db, ProdutoCreate(codigo=_codigo(), descricao="PROD E"))
    db.flush()
    v = produto_service.adicionar_variacao(
        db, p.id, VariacaoCreate(cor="Preto", estoque_fisico=3), usuario_id=usuario_admin.id
    )
    db.flush()
    # Zera o saldo via ajuste (mantém histórico de entrada).
    estoque_service.ajustar(db, v, novo_saldo=0, usuario_id=usuario_admin.id, motivo="zerar")
    db.flush()
    variacao, acao = produto_service.remover_variacao(db, v.id)
    assert acao == "inativada"
    assert variacao.ativo is False
    assert db.get(ProdutoVariacao, v.id) is not None  # ainda existe (inativa)


def test_vendedor_nao_adiciona_variacao() -> None:
    client = TestClient(app)
    _login(client, "vendedor")
    # Produto 1 (seed) existe; vendedor deve tomar 403.
    resp = client.post("/produtos/1/variacao", data={"cor": "X"}, follow_redirects=False)
    assert resp.status_code == 403


def test_vendedor_nao_remove_variacao() -> None:
    client = TestClient(app)
    _login(client, "vendedor")
    resp = client.post("/produtos/variacao/1/remover", follow_redirects=False)
    assert resp.status_code == 403


def test_criar_produto_redireciona_para_edicao() -> None:
    client = TestClient(app)
    _login(client, "admin")
    codigo = _codigo()
    resp = client.post(
        "/produtos",
        data={"codigo": codigo, "descricao": "REDIRECT TEST", "ativo": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    location = resp.headers["location"]
    # Vai direto à edição (não para a lista /produtos?ok=...).
    assert "/editar" in location
    _remover(codigo)


# --------------------------------------------------------------------------- #
# Edição do código (e códigos alternativos) do produto já cadastrado.          #
# --------------------------------------------------------------------------- #


def test_atualizar_codigo_altera(db: Session) -> None:
    p = produto_service.criar(db, ProdutoCreate(codigo=_codigo(), descricao="EDIT COD"))
    db.flush()
    novo = _codigo()
    produto_service.atualizar(db, p.id, ProdutoUpdate(codigo=novo))
    db.flush()
    assert p.codigo == novo
    # Reconfirma pelo repositório (não é só o objeto em memória).
    assert produto_service.obter(db, p.id).codigo == novo


def test_atualizar_codigo_duplicado_falha(db: Session) -> None:
    codigo_outro = _codigo()
    produto_service.criar(db, ProdutoCreate(codigo=codigo_outro, descricao="OUTRO"))
    p = produto_service.criar(db, ProdutoCreate(codigo=_codigo(), descricao="EU"))
    db.flush()
    # Não pode assumir o código já usado por outro produto.
    with pytest.raises(RegraNegocioError):
        produto_service.atualizar(db, p.id, ProdutoUpdate(codigo=codigo_outro))


def test_atualizar_codigo_proprio_nao_conflita(db: Session) -> None:
    """Reenviar o próprio código (sem mudar) não pode disparar conflito de unicidade."""
    codigo = _codigo()
    p = produto_service.criar(db, ProdutoCreate(codigo=codigo, descricao="KEEP"))
    db.flush()
    produto_service.atualizar(db, p.id, ProdutoUpdate(codigo=codigo, descricao="KEEP ALT"))
    db.flush()
    assert p.codigo == codigo


def test_atualizar_codigo_vazio_rejeitado(db: Session) -> None:
    # Validator de ProdutoUpdate rejeita código vazio (rede de segurança).
    with pytest.raises(ValidationError):
        ProdutoUpdate(codigo="   ")


def test_atualizar_codigos_alt_reconcilia(db: Session) -> None:
    p = produto_service.criar(
        db,
        ProdutoCreate(
            codigo=_codigo(),
            descricao="CODS",
            codigos_alt=[CodigoAltCreate(codigo_alt="A"), CodigoAltCreate(codigo_alt="B")],
        ),
    )
    db.flush()
    # Mantém B, remove A, adiciona C.
    produto_service.atualizar(
        db,
        p.id,
        ProdutoUpdate(
            codigos_alt=[CodigoAltCreate(codigo_alt="B"), CodigoAltCreate(codigo_alt="C")],
        ),
    )
    db.flush()
    atual = produto_service.obter(db, p.id)
    codigos = {c.codigo_alt for c in atual.codigos_alt}
    assert codigos == {"B", "C"}


def test_atualizar_codigos_alt_remove_todos(db: Session) -> None:
    p = produto_service.criar(
        db,
        ProdutoCreate(
            codigo=_codigo(),
            descricao="ZERAR",
            codigos_alt=[CodigoAltCreate(codigo_alt="A")],
        ),
    )
    db.flush()
    produto_service.atualizar(db, p.id, ProdutoUpdate(codigos_alt=[]))
    db.flush()
    atual = produto_service.obter(db, p.id)
    assert [c.codigo_alt for c in atual.codigos_alt] == []


def test_editar_codigo_via_rota() -> None:
    """End-to-end: cadastra via rota, edita o código pela rota e confirma no banco real."""
    client = TestClient(app)
    _login(client, "admin")
    codigo_original = _codigo()
    resp_criar = client.post(
        "/produtos",
        data={"codigo": codigo_original, "descricao": "ROTA EDIT", "ativo": "on"},
        follow_redirects=False,
    )
    assert resp_criar.status_code == 303
    pid = resp_criar.headers["location"].rstrip("/").split("/")[-2]

    novo = _codigo()
    resp = client.post(
        f"/produtos/{pid}",
        data={"codigo": novo, "descricao": "ROTA EDIT ALT", "ativo": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    # Confirma no banco real (session do app) e limpa.
    from app.core.database import SessionLocal

    s = SessionLocal()
    try:
        prod = s.get(Produto, int(pid))
        assert prod is not None
        assert prod.codigo == novo
        s.delete(prod)
        s.commit()
    finally:
        s.close()
