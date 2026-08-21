"""Testes do CRUD de produtos: serviço (regras) e rotas (custo oculto + RBAC)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import RegraNegocioError
from app.main import app
from app.models.categoria import Categoria
from app.models.enums import EstoqueModo, TipoMov
from app.models.movimentacao import MovimentacaoEstoque
from app.models.produto import Produto, ProdutoVariacao
from app.repositories.produto_repo import produto_repo
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


def test_reativar_produto(db: Session) -> None:
    p = produto_service.criar(db, ProdutoCreate(codigo=_codigo(), descricao="GIZ"))
    db.flush()
    produto_service.inativar(db, p.id)
    db.flush()
    produto_service.reativar(db, p.id)
    assert p.ativo is True
    assert produto_service.obter(db, p.id).ativo is True


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


def test_anonimo_nao_cria_produto() -> None:
    """Cadastro de produto é de admin e vendedor; sem sessão vai para o login."""
    client = TestClient(app)
    resp = client.post(
        "/produtos",
        data={"codigo": _codigo(), "descricao": "X", "ativo": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


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


def test_reativar_variacao(db: Session, usuario_admin) -> None:
    p = produto_service.criar(db, ProdutoCreate(codigo=_codigo(), descricao="PROD REAT V"))
    db.flush()
    v = produto_service.adicionar_variacao(
        db, p.id, VariacaoCreate(cor="Cinza", estoque_fisico=3), usuario_id=usuario_admin.id
    )
    db.flush()
    # Zera o saldo (mantém histórico) e remove -> inativa.
    estoque_service.ajustar(db, v, novo_saldo=0, usuario_id=usuario_admin.id, motivo="zerar")
    db.flush()
    produto_service.remover_variacao(db, v.id)
    assert v.ativo is False
    # Reativa.
    produto_service.reativar_variacao(db, v.id)
    assert v.ativo is True
    assert db.get(ProdutoVariacao, v.id).ativo is True


def test_reativar_variacao_bloqueia_duplicidade(db: Session, usuario_admin) -> None:
    p = produto_service.criar(
        db,
        ProdutoCreate(
            codigo=_codigo(),
            descricao="PROD DUP",
            variacoes=[VariacaoCreate(cor="Azul")],
        ),
    )
    db.flush()
    # Uma variação "Azul" ativa já existe; cria uma segunda "Azul" inativa direto no db
    # (adicionar_variacao bloquearia a duplicidade de cor ativa).
    inativa = ProdutoVariacao(cor="Azul", estoque_modo=EstoqueModo.APROXIMADO, ativo=False)
    p.variacoes.append(inativa)
    db.flush()
    with pytest.raises(RegraNegocioError):
        produto_service.reativar_variacao(db, inativa.id)


def test_anonimo_nao_mexe_em_variacao() -> None:
    """Cores também seguem o RBAC de edição de produto: sem sessão, login."""
    client = TestClient(app)
    for rota in ("/produtos/1/variacao", "/produtos/variacao/1/remover"):
        resp = client.post(rota, data={"cor": "X"}, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"


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


def test_busca_rapida_por_parte_descricao(db: Session) -> None:
    """Digitar só um pedaço (do meio/curto) da descrição deve encontrar o produto.

    Cobertura do fallback `descricao.ilike('%termo%')`: sem ele, o trigram sozinho
    pode não casar pedaços curtos/pouco parecidos (limiar de similaridade 0.3).
    """
    p = Produto(codigo=_codigo(), descricao="Boné Aba Reta Premium")
    db.add(p)
    db.flush()

    # Pedaço do meio e palavra isolada (curtos) — casam pelo fallback ilike.
    for termo in ("Aba Reta", "Premium", "Boné"):
        achou = produto_repo.busca_rapida(db, termo)
        assert any(x.id == p.id for x in achou), f"não achou com termo={termo!r}"


def test_busca_rapida_por_parte_codigo(db: Session) -> None:
    """Digitar só um pedaço do código (não desde o início) deve encontrar o produto.

    Ex: `708` casa com `K-708`. Cobertura do `codigo.ilike('%termo%')` (substring,
    não prefixo).
    """
    p = Produto(codigo="K-708", descricao="Produto Teste Parcial Codigo")
    db.add(p)
    db.flush()

    # "708" não é prefixo de "K-708" — só casa se a busca for por substring.
    achou = produto_repo.busca_rapida(db, "708")
    assert any(x.id == p.id for x in achou)


def test_listar_filtrar_por_categoria(db: Session) -> None:
    """O filtro por categoria restringe a listagem (sem termo) aos produtos daquela categoria."""
    cat_a = Categoria(nome=f"Canetas-{uuid.uuid4().hex[:6]}")
    cat_b = Categoria(nome=f"Bolas-{uuid.uuid4().hex[:6]}")
    db.add_all([cat_a, cat_b])
    db.flush()
    p_a = Produto(codigo=_codigo(), descricao="Caneta Azul", categoria_id=cat_a.id)
    p_b = Produto(codigo=_codigo(), descricao="Bola Vermelha", categoria_id=cat_b.id)
    db.add_all([p_a, p_b])
    db.flush()

    achou_a = produto_repo.listar(db, categoria_id=cat_a.id)
    ids_a = {x.id for x in achou_a}
    assert p_a.id in ids_a
    assert p_b.id not in ids_a


def test_busca_rapida_combina_categoria_e_texto(db: Session) -> None:
    """Categoria + texto agem junto: "Azul" em cat_a retorna só o produto certo."""
    cat_a = Categoria(nome=f"Canetas-{uuid.uuid4().hex[:6]}")
    cat_b = Categoria(nome=f"Bolas-{uuid.uuid4().hex[:6]}")
    db.add_all([cat_a, cat_b])
    db.flush()
    p_a_azul = Produto(codigo=_codigo(), descricao="Caneta Azul", categoria_id=cat_a.id)
    p_a_verde = Produto(codigo=_codigo(), descricao="Caneta Verde", categoria_id=cat_a.id)
    p_b_azul = Produto(codigo=_codigo(), descricao="Bola Azul", categoria_id=cat_b.id)
    db.add_all([p_a_azul, p_a_verde, p_b_azul])
    db.flush()

    achou = produto_repo.busca_rapida(db, "Azul", categoria_id=cat_a.id)
    ids = {x.id for x in achou}
    assert p_a_azul.id in ids
    assert p_a_verde.id not in ids  # não tem "Azul"
    assert p_b_azul.id not in ids  # é "Azul" mas é de outra categoria


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


def test_busca_produtos_categoria_vazia_nao_falha() -> None:
    """Selecionar 'Todas as categorias' envia categoria_id='' — não pode 422, deve listar tudo."""
    client = TestClient(app)
    _login(client, "admin")
    resp = client.get("/produtos/busca", params={"categoria_id": "", "q": ""})
    assert resp.status_code == 200, resp.text


def test_pagina_produtos_categoria_vazia_nao_falha() -> None:
    """A página inteira de produtos também aceita categoria_id='' (422 antes do fix)."""
    client = TestClient(app)
    _login(client, "admin")
    resp = client.get("/produtos", params={"categoria_id": ""})
    assert resp.status_code == 200, resp.text


# ---------------- preço mínimo e a trava de campos do admin ----------------
def test_vendedor_salvando_produto_nao_zera_custo_nem_piso() -> None:
    """O formulário do vendedor não tem esses campos — salvar não pode apagá-los.

    `_dec` devolve 0 para campo ausente, então mandar a chave sempre significaria
    zerar custo e piso a cada save de vendedor. É perda de dado silenciosa: ninguém
    percebe até a margem sumir do relatório.
    """
    client = TestClient(app)
    _login(client, "admin")
    codigo = _codigo()
    resp = client.post(
        "/produtos",
        data={
            "codigo": codigo,
            "descricao": "PRODUTO COM CUSTO",
            "preco_pouca_qtd": "10,00",
            "preco_custo": "4,00",
            "preco_minimo": "8,00",
            "ativo": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    from app.core.database import SessionLocal

    s = SessionLocal()
    try:
        produto = s.query(Produto).filter(Produto.codigo == codigo).one()
        assert produto.preco_custo == Decimal("4.00")
        assert produto.preco_minimo == Decimal("8.00")
        produto_id = produto.id
    finally:
        s.close()

    # Agora o vendedor edita: o form dele não manda preco_custo nem preco_minimo.
    vendedor = TestClient(app)
    _login(vendedor, "vendedor")
    resp = vendedor.post(
        f"/produtos/{produto_id}",
        data={
            "codigo": codigo,
            "descricao": "PRODUTO EDITADO PELO VENDEDOR",
            "preco_pouca_qtd": "12,00",
            "ativo": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    s = SessionLocal()
    try:
        produto = s.get(Produto, produto_id)
        assert produto.descricao == "PRODUTO EDITADO PELO VENDEDOR"
        assert produto.preco_pouca_qtd == Decimal("12.00")
        assert produto.preco_custo == Decimal("4.00"), "o vendedor zerou o custo"
        assert produto.preco_minimo == Decimal("8.00"), "o vendedor zerou o piso"
    finally:
        s.close()
    _remover(codigo)


def test_preco_minimo_some_do_dict_do_vendedor(db: Session) -> None:
    """O piso é onde a margem acaba: não vai no payload de quem não pode mexer nele."""
    from app.schemas.produto import produto_para_dict

    produto = produto_service.criar(
        db,
        ProdutoCreate(
            codigo=_codigo(),
            descricao="COM PISO",
            preco_custo=Decimal("1"),
            preco_minimo=Decimal("2"),
        ),
    )
    db.flush()
    dados_admin = produto_para_dict(produto, "admin")
    assert dados_admin["preco_minimo"] == Decimal("2")
    dados_vendedor = produto_para_dict(produto, "vendedor")
    assert "preco_minimo" not in dados_vendedor
    assert "preco_custo" not in dados_vendedor


# ------------------------------------------------------- busca de código versátil
def test_codigo_acha_sem_traco_e_em_minusculas(db):
    """O vendedor digita "kd31"; o cadastro tem "KD-31". Tem que achar."""
    from app.repositories.estoque_repo import estoque_repo
    from app.repositories.produto_repo import produto_repo

    p = produto_service.criar(
        db, ProdutoCreate(codigo="KD-31", descricao="TESTE BUSCA CODIGO VERSATIL")
    )
    produto_service.adicionar_variacao(db, p.id, VariacaoCreate(cor="azul"), 1)
    db.flush()

    for digitado in ("kd31", "KD31", "kd-31", "Kd 31", "KD-31"):
        assert produto_repo.get_by_codigo(db, digitado) is not None, digitado
        assert estoque_repo.por_codigo_exato(db, digitado) is not None, digitado
        assert p.codigo in [x.codigo for x in produto_repo.busca_rapida(db, digitado)], digitado
        assert p.codigo in [
            v.produto.codigo for v in estoque_repo.busca_localizacao(db, digitado)
        ], digitado
        assert p.codigo in [v.produto.codigo for v in estoque_repo.busca_orcamento(db, digitado)], (
            digitado
        )


def test_codigo_digitado_vem_na_frente_de_quem_casou_pela_descricao(db):
    """Digitar um código tem que pôr aquele produto na PRIMEIRA linha."""
    from app.repositories.estoque_repo import estoque_repo

    alvo = produto_service.criar(db, ProdutoCreate(codigo="ZQ-533", descricao="CANECA ZQ ALFA"))
    produto_service.adicionar_variacao(db, alvo.id, VariacaoCreate(cor="azul"), 1)
    # Descrição parecida, código diferente: casaria por trigrama e sairia antes na
    # ordem alfabética antiga.
    vizinho = produto_service.criar(
        db, ProdutoCreate(codigo="ZQ-545", descricao="CANECA ZQ ALFA B")
    )
    produto_service.adicionar_variacao(db, vizinho.id, VariacaoCreate(cor="azul"), 1)
    db.flush()

    achados = estoque_repo.busca_localizacao(db, "zq533")
    assert achados[0].produto.codigo == "ZQ-533"


def test_codigo_que_normaliza_igual_nao_pode_duplicar(db):
    """ "K-31" e "K31" seriam o mesmo produto para quem busca — o cadastro barra."""
    produto_service.criar(db, ProdutoCreate(codigo="QW-31", descricao="PRIMEIRO"))
    with pytest.raises(RegraNegocioError) as erro:
        produto_service.criar(db, ProdutoCreate(codigo="qw31", descricao="SEGUNDO"))
    assert "QW-31" in str(erro.value)  # nomeia o que JÁ existe, não o que foi digitado


def test_codigo_alternativo_do_fornecedor_tambem_e_versatil(db):
    from app.repositories.estoque_repo import estoque_repo
    from app.repositories.produto_repo import produto_repo

    p = produto_service.criar(
        db,
        ProdutoCreate(
            codigo="ZZ-900",
            descricao="PRODUTO COM CODIGO DE FORNECEDOR",
            codigos_alt=[CodigoAltCreate(codigo_alt="FORN-7788")],
        ),
    )
    produto_service.adicionar_variacao(db, p.id, VariacaoCreate(cor="azul"), 1)
    db.flush()

    assert estoque_repo.por_codigo_exato(db, "forn7788") is not None
    assert p.codigo in [x.codigo for x in produto_repo.busca_rapida(db, "forn7788")]
    assert p.codigo in [v.produto.codigo for v in estoque_repo.busca_localizacao(db, "forn7788")]


# ============================================================ faixas de preço
def test_salvar_sem_o_editor_de_faixas_nao_apaga_a_tabela() -> None:
    """A armadilha: `atualizar` monta o dict com TODAS as chaves setadas.

    O `exclude_unset` do service não protege nada nesse caminho, e lista vazia é um
    pedido legítimo ("apague a tabela") — então não dá para inferir pela ausência das
    linhas. Sem o sentinela `tem_editor_faixas`, qualquer save vindo de um formulário
    sem o editor apagaria a tabela de preço inteira, em silêncio e com os testes
    passando. Este teste é o que fecha esse buraco.
    """
    from app.core.database import SessionLocal

    client = TestClient(app)
    _login(client, "admin")
    codigo = _codigo()

    resp = client.post(
        "/produtos",
        data={
            "codigo": codigo,
            "descricao": "PRODUTO COM TABELA",
            "preco_pouca_qtd": "10,00",
            "ativo": "on",
            "tem_editor_faixas": "1",
            "faixa_min_qtd": ["1", "10", "50"],
            "faixa_preco": ["10,00", "8,00", "6,50"],
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    s = SessionLocal()
    try:
        produto = s.query(Produto).filter(Produto.codigo == codigo).one()
        assert [(f.min_qtd, f.preco) for f in produto.faixas] == [
            (1, Decimal("10.00")),
            (10, Decimal("8.00")),
            (50, Decimal("6.50")),
        ]
        produto_id = produto.id
    finally:
        s.close()

    # Agora salva de um formulário SEM o editor (nenhum campo de faixa).
    resp = client.post(
        f"/produtos/{produto_id}",
        data={
            "codigo": codigo,
            "descricao": "EDITADO SEM O EDITOR",
            "preco_pouca_qtd": "12,00",
            "ativo": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    s = SessionLocal()
    try:
        produto = s.get(Produto, produto_id)
        assert produto.descricao == "EDITADO SEM O EDITOR"
        assert len(produto.faixas) == 3, "salvar sem o editor apagou a tabela de preço"
    finally:
        s.close()


def test_editor_presente_e_vazio_apaga_a_tabela() -> None:
    """Tirar todas as linhas E salvar é um pedido de verdade: "esse produto não tem tabela"."""
    from app.core.database import SessionLocal

    client = TestClient(app)
    _login(client, "admin")
    codigo = _codigo()
    client.post(
        "/produtos",
        data={
            "codigo": codigo,
            "descricao": "PRODUTO COM TABELA 2",
            "preco_pouca_qtd": "10,00",
            "ativo": "on",
            "tem_editor_faixas": "1",
            "faixa_min_qtd": ["1"],
            "faixa_preco": ["10,00"],
        },
        follow_redirects=False,
    )
    s = SessionLocal()
    try:
        produto_id = s.query(Produto).filter(Produto.codigo == codigo).one().id
    finally:
        s.close()

    client.post(
        f"/produtos/{produto_id}",
        data={
            "codigo": codigo,
            "descricao": "PRODUTO COM TABELA 2",
            "preco_pouca_qtd": "10,00",
            "ativo": "on",
            "tem_editor_faixas": "1",
        },
        follow_redirects=False,
    )
    s = SessionLocal()
    try:
        assert s.get(Produto, produto_id).faixas == []
    finally:
        s.close()


def test_tabela_sem_a_faixa_de_uma_unidade_e_recusada(db: Session) -> None:
    from app.core.errors import RegraNegocioError
    from app.schemas.produto import FaixaPrecoCreate

    with pytest.raises(RegraNegocioError) as exc:
        produto_service.criar(
            db,
            ProdutoCreate(
                codigo=_codigo(),
                descricao="SEM FAIXA DE 1",
                faixas=[
                    FaixaPrecoCreate(min_qtd=10, preco=Decimal("8.00")),
                    FaixaPrecoCreate(min_qtd=50, preco=Decimal("6.50")),
                ],
            ),
        )
    assert "1 un" in str(exc.value)


def test_faixa_repetida_e_recusada(db: Session) -> None:
    from app.core.errors import RegraNegocioError
    from app.schemas.produto import FaixaPrecoCreate

    # `normalizar_faixas` derruba a repetida antes de validar, então o que sobra é uma
    # tabela sem a faixa de 1 un — a recusa vem por ali, e a tabela ambígua não passa.
    with pytest.raises(RegraNegocioError):
        produto_service.criar(
            db,
            ProdutoCreate(
                codigo=_codigo(),
                descricao="FAIXA REPETIDA",
                faixas=[
                    FaixaPrecoCreate(min_qtd=10, preco=Decimal("8.00")),
                    FaixaPrecoCreate(min_qtd=10, preco=Decimal("7.00")),
                ],
            ),
        )


# ============================================================ ficha técnica
def test_ficha_tecnica_guarda_a_ordem_e_renumera() -> None:
    """A ordem É o dado: as setas ↑↓ da tela só reordenam o array antes de enviar."""
    from app.core.database import SessionLocal

    client = TestClient(app)
    _login(client, "admin")
    codigo = _codigo()
    client.post(
        "/produtos",
        data={
            "codigo": codigo,
            "descricao": "PRODUTO COM FICHA",
            "ativo": "on",
            "tem_editor_especificacoes": "1",
            "espec_rotulo": ["Altura", "Largura", "Material"],
            "espec_valor": ["50 cm", "30 cm", "alumínio"],
        },
        follow_redirects=False,
    )
    s = SessionLocal()
    try:
        produto = s.query(Produto).filter(Produto.codigo == codigo).one()
        assert [(e.ordem, e.rotulo, e.valor) for e in produto.especificacoes] == [
            (0, "Altura", "50 cm"),
            (1, "Largura", "30 cm"),
            (2, "Material", "alumínio"),
        ]
        produto_id = produto.id
    finally:
        s.close()

    # Reordena (Material primeiro) e remove a Largura.
    client.post(
        f"/produtos/{produto_id}",
        data={
            "codigo": codigo,
            "descricao": "PRODUTO COM FICHA",
            "ativo": "on",
            "tem_editor_especificacoes": "1",
            "espec_rotulo": ["Material", "Altura"],
            "espec_valor": ["alumínio", "50 cm"],
        },
        follow_redirects=False,
    )
    s = SessionLocal()
    try:
        produto = s.get(Produto, produto_id)
        assert [(e.ordem, e.rotulo) for e in produto.especificacoes] == [
            (0, "Material"),
            (1, "Altura"),
        ]
    finally:
        s.close()


def test_salvar_sem_o_editor_de_ficha_nao_apaga(db: Session) -> None:
    """Mesma armadilha das faixas — `atualizar` seta todas as chaves do dict."""
    from app.core.database import SessionLocal

    client = TestClient(app)
    _login(client, "admin")
    codigo = _codigo()
    client.post(
        "/produtos",
        data={
            "codigo": codigo,
            "descricao": "FICHA PRESERVADA",
            "ativo": "on",
            "tem_editor_especificacoes": "1",
            "espec_rotulo": ["Peso aproximado"],
            "espec_valor": ["1,2 kg"],
        },
        follow_redirects=False,
    )
    s = SessionLocal()
    try:
        produto_id = s.query(Produto).filter(Produto.codigo == codigo).one().id
    finally:
        s.close()

    client.post(
        f"/produtos/{produto_id}",
        data={"codigo": codigo, "descricao": "EDITADO SEM O EDITOR", "ativo": "on"},
        follow_redirects=False,
    )
    s = SessionLocal()
    try:
        produto = s.get(Produto, produto_id)
        assert produto.descricao == "EDITADO SEM O EDITOR"
        assert len(produto.especificacoes) == 1, "salvar sem o editor apagou a ficha"
    finally:
        s.close()


def test_linha_incompleta_da_ficha_e_descartada(db: Session) -> None:
    """O editor deixa abrir uma linha e desistir dela."""
    from app.schemas.produto import EspecificacaoCreate

    produto = produto_service.criar(
        db,
        ProdutoCreate(
            codigo=_codigo(),
            descricao="FICHA COM BURACO",
            especificacoes=[EspecificacaoCreate(rotulo="Altura", valor="50 cm")],
        ),
    )
    # rótulo sem valor não sobrevive à limpeza do service
    produto_service.atualizar(
        db,
        produto.id,
        ProdutoUpdate(especificacoes=[EspecificacaoCreate(rotulo="Altura", valor="50 cm")]),
    )
    db.refresh(produto)
    assert [(e.rotulo, e.valor) for e in produto.especificacoes] == [("Altura", "50 cm")]


# ============================================================ Compre Junto
def _cria(db: Session, descricao: str = "PRODUTO") -> Produto:
    return produto_service.criar(db, ProdutoCreate(codigo=_codigo(), descricao=descricao))


def test_compre_junto_guarda_a_ordem(db: Session) -> None:
    principal = _cria(db, "GARRAFA")
    alca = _cria(db, "ALCA")
    tampa = _cria(db, "TAMPA")

    produto_service.atualizar(db, principal.id, ProdutoUpdate(relacionados=[tampa.id, alca.id]))
    db.refresh(principal)
    assert [(r.ordem, r.relacionado_id) for r in principal.relacionados] == [
        (0, tampa.id),
        (1, alca.id),
    ]


def test_compre_junto_e_de_mao_unica(db: Session) -> None:
    """Capa é acessório de celular; o contrário não. Criar a volta encheria o acessório."""
    principal = _cria(db, "CELULAR")
    capa = _cria(db, "CAPA")

    produto_service.atualizar(db, principal.id, ProdutoUpdate(relacionados=[capa.id]))
    db.refresh(capa)
    assert capa.relacionados == []


def test_compre_junto_descarta_auto_referencia_e_duplicata(db: Session) -> None:
    """Clicar no próprio produto é engano de dedo, não pedido — some, não recusa."""
    principal = _cria(db)
    outro = _cria(db)

    produto_service.atualizar(
        db, principal.id, ProdutoUpdate(relacionados=[principal.id, outro.id, outro.id])
    )
    db.refresh(principal)
    assert [r.relacionado_id for r in principal.relacionados] == [outro.id]


def test_compre_junto_descarta_id_que_nao_existe(db: Session) -> None:
    principal = _cria(db)
    outro = _cria(db)

    produto_service.atualizar(db, principal.id, ProdutoUpdate(relacionados=[10**9, outro.id]))
    db.refresh(principal)
    assert [r.relacionado_id for r in principal.relacionados] == [outro.id]


def test_compre_junto_respeita_o_teto_de_oito(db: Session) -> None:
    principal = _cria(db)
    alvos = [_cria(db).id for _ in range(10)]

    produto_service.atualizar(db, principal.id, ProdutoUpdate(relacionados=alvos))
    db.refresh(principal)
    assert len(principal.relacionados) == 8


def test_salvar_sem_o_editor_de_compre_junto_nao_apaga() -> None:
    """Terceira vez que a mesma armadilha aparece — e o terceiro sentinela."""
    from app.core.database import SessionLocal

    client = TestClient(app)
    _login(client, "admin")
    codigo, codigo_alvo = _codigo(), _codigo()
    client.post(
        "/produtos",
        data={"codigo": codigo_alvo, "descricao": "ACESSORIO", "ativo": "on"},
        follow_redirects=False,
    )
    s = SessionLocal()
    try:
        alvo_id = s.query(Produto).filter(Produto.codigo == codigo_alvo).one().id
    finally:
        s.close()

    client.post(
        "/produtos",
        data={
            "codigo": codigo,
            "descricao": "PRINCIPAL",
            "ativo": "on",
            "tem_editor_relacionados": "1",
            "rel_id": [str(alvo_id)],
        },
        follow_redirects=False,
    )
    s = SessionLocal()
    try:
        produto = s.query(Produto).filter(Produto.codigo == codigo).one()
        assert len(produto.relacionados) == 1
        produto_id = produto.id
    finally:
        s.close()

    client.post(
        f"/produtos/{produto_id}",
        data={"codigo": codigo, "descricao": "SEM O EDITOR", "ativo": "on"},
        follow_redirects=False,
    )
    s = SessionLocal()
    try:
        produto = s.get(Produto, produto_id)
        assert produto.descricao == "SEM O EDITOR"
        assert len(produto.relacionados) == 1, "salvar sem o editor apagou o Compre Junto"
    finally:
        s.close()
