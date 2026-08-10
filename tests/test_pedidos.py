"""Testes de PEDIDOS + SEPARAÇÃO.

Criam seus próprios produtos/variações/cliente (não assumem o banco vazio).
Rodam dentro da transação revertida do fixture `db`.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import NaoEncontradoError, PermissaoNegadaError, RegraNegocioError
from app.models.cliente import Cliente
from app.models.conta_receber import ContaReceber
from app.models.enums import EstoqueModo, StatusConta, StatusPedido
from app.models.produto import Produto, ProdutoVariacao
from app.schemas.pedido import ItemAdicionar
from app.services.pedido_service import pedido_service


# --------------------------------------------------------------------- helpers
def _produto(
    db,
    codigo: str,
    *,
    pouca=Decimal("10.00"),
    muita=Decimal("8.00"),
    corte=None,
    promo=None,
    unid_caixa=None,
):
    p = Produto(
        codigo=codigo,
        descricao=f"Produto {codigo}",
        preco_pouca_qtd=pouca,
        preco_muita_qtd=muita,
        preco_promocional=promo,
        qtd_corte_atacado=corte,
        unidades_por_caixa=unid_caixa,
        localizacao="A-01",
    )
    db.add(p)
    db.flush()
    return p


def _variacao(db, produto, *, modo=EstoqueModo.EXATO, fisico=100, cor="azul"):
    v = ProdutoVariacao(
        produto_id=produto.id,
        cor=cor,
        estoque_modo=modo,
        estoque_fisico=fisico,
        estoque_reservado=0,
    )
    db.add(v)
    db.flush()
    return v


def _cliente(db, condicao=None):
    c = Cliente(nome="Cliente Teste", condicao_pagto_padrao=condicao)
    db.add(c)
    db.flush()
    return c


def _novo_pedido(db, cliente, vendedor):
    return pedido_service.criar(db, cliente.id, vendedor.id)


def _add(db, pedido, variacao, perfil="vendedor", **kw):
    dados = ItemAdicionar(variacao_id=variacao.id, **kw)
    return pedido_service.adicionar_item(db, pedido.id, dados, perfil)


# --------------------------------------------------------------------- totais
def test_subtotal_total_e_desconto(db, usuario_vendedor):
    prod = _produto(db, "P1", pouca=Decimal("10.00"))
    var = _variacao(db, prod)
    cli = _cliente(db)
    ped = _novo_pedido(db, cli, usuario_vendedor)

    item = _add(db, ped, var, qtd=5, preco_unit=Decimal("10.00"), desconto=Decimal("5.00"))
    assert item.subtotal == Decimal("45.00")  # 5*10 - 5
    db.refresh(ped)
    assert ped.total == Decimal("45.00")

    # desconto total dentro do limite do vendedor (5% de 45)
    pedido_service.aplicar_desconto_total(db, ped.id, Decimal("2.00"), "vendedor")
    db.refresh(ped)
    assert ped.total == Decimal("43.00")


# --------------------------------------------------------- sugestão de preço
def test_sugestao_preco_por_faixa_corte(db):
    prod = _produto(db, "P2", pouca=Decimal("10.00"), muita=Decimal("8.00"), corte=12)
    s_varejo = pedido_service.sugerir_preco(prod, 5)
    s_atacado = pedido_service.sugerir_preco(prod, 20)
    assert s_varejo.faixa == "varejo" and s_varejo.preco_sugerido == Decimal("10.00")
    assert s_atacado.faixa == "atacado" and s_atacado.preco_sugerido == Decimal("8.00")


def test_item_usa_preco_sugerido_quando_omitido(db, usuario_vendedor):
    prod = _produto(db, "P3", pouca=Decimal("10.00"), muita=Decimal("8.00"), corte=10)
    var = _variacao(db, prod)
    cli = _cliente(db)
    ped = _novo_pedido(db, cli, usuario_vendedor)
    item = _add(db, ped, var, qtd=10)  # >= corte -> atacado
    assert item.preco_unit == Decimal("8.00")


# --------------------------------------------------------- conversão de caixa
def test_conversao_caixa_para_unidades(db, usuario_vendedor):
    prod = _produto(db, "P4", pouca=Decimal("2.00"), unid_caixa=12)
    var = _variacao(db, prod, fisico=1000)
    cli = _cliente(db)
    ped = _novo_pedido(db, cli, usuario_vendedor)
    item = _add(db, ped, var, qtd_caixas=3)
    assert item.qtd == 36 and item.qtd_caixas == 3


def test_caixa_sem_unidades_definidas_falha(db, usuario_vendedor):
    prod = _produto(db, "P5", unid_caixa=None)
    var = _variacao(db, prod)
    cli = _cliente(db)
    ped = _novo_pedido(db, cli, usuario_vendedor)
    with pytest.raises(RegraNegocioError):
        _add(db, ped, var, qtd_caixas=2)


# --------------------------------------------------------- numeração
def test_numeracao_via_sequence_sem_buraco(db, usuario_vendedor):
    cli = _cliente(db)
    numeros = []
    for i in range(3):
        prod = _produto(db, f"N{i}")
        var = _variacao(db, prod)
        ped = _novo_pedido(db, cli, usuario_vendedor)
        _add(db, ped, var, qtd=1, preco_unit=Decimal("1.00"))
        pedido_service.confirmar(db, ped.id, usuario_vendedor.id)
        db.refresh(ped)
        numeros.append(ped.numero)
    assert numeros[1] == numeros[0] + 1
    assert numeros[2] == numeros[1] + 1


# --------------------------------------------------------- reserva ao confirmar
def test_confirmar_reserva_estoque(db, usuario_vendedor):
    prod = _produto(db, "R1")
    var = _variacao(db, prod, fisico=50)
    cli = _cliente(db)
    ped = _novo_pedido(db, cli, usuario_vendedor)
    _add(db, ped, var, qtd=10, preco_unit=Decimal("1.00"))
    pedido_service.confirmar(db, ped.id, usuario_vendedor.id)
    db.refresh(var)
    assert var.estoque_reservado == 10
    assert var.disponivel == 40
    db.refresh(ped)
    assert ped.status == StatusPedido.CONFIRMADO
    assert ped.numero is not None


def test_confirmar_bloqueia_se_insuficiente_em_exato(db, usuario_vendedor):
    prod = _produto(db, "R2")
    var = _variacao(db, prod, modo=EstoqueModo.EXATO, fisico=5)
    cli = _cliente(db)
    ped = _novo_pedido(db, cli, usuario_vendedor)
    _add(db, ped, var, qtd=10, preco_unit=Decimal("1.00"))
    with pytest.raises(RegraNegocioError):
        pedido_service.confirmar(db, ped.id, usuario_vendedor.id)


def test_confirmar_aproximado_nao_bloqueia(db, usuario_vendedor):
    prod = _produto(db, "R3")
    var = _variacao(db, prod, modo=EstoqueModo.APROXIMADO, fisico=0)
    cli = _cliente(db)
    ped = _novo_pedido(db, cli, usuario_vendedor)
    _add(db, ped, var, qtd=10, preco_unit=Decimal("1.00"))
    pedido_service.confirmar(db, ped.id, usuario_vendedor.id)  # não levanta
    db.refresh(ped)
    assert ped.status == StatusPedido.CONFIRMADO


def test_pedido_sem_itens_nao_confirma(db, usuario_vendedor):
    cli = _cliente(db)
    ped = _novo_pedido(db, cli, usuario_vendedor)
    with pytest.raises(RegraNegocioError):
        pedido_service.confirmar(db, ped.id, usuario_vendedor.id)


# --------------------------------------------------------- cancelar estorna
def test_cancelar_estorna_reserva(db, usuario_vendedor):
    prod = _produto(db, "C1")
    var = _variacao(db, prod, fisico=50)
    cli = _cliente(db)
    ped = _novo_pedido(db, cli, usuario_vendedor)
    _add(db, ped, var, qtd=10, preco_unit=Decimal("1.00"))
    pedido_service.confirmar(db, ped.id, usuario_vendedor.id)
    db.refresh(var)
    assert var.estoque_reservado == 10
    pedido_service.cancelar(db, ped.id, usuario_vendedor.id)
    db.refresh(var)
    assert var.estoque_reservado == 0
    db.refresh(ped)
    assert ped.status == StatusPedido.CANCELADO


# --------------------------------------------------------- faturar
def test_faturar_baixa_estoque_e_gera_conta_a_vista(db, usuario_vendedor, usuario_admin):
    prod = _produto(db, "F1")
    var = _variacao(db, prod, fisico=50)
    cli = _cliente(db, condicao="à vista")
    ped = _novo_pedido(db, cli, usuario_vendedor)
    _add(db, ped, var, qtd=10, preco_unit=Decimal("10.00"))
    pedido_service.confirmar(db, ped.id, usuario_vendedor.id)
    pedido_service.faturar(db, ped.id, usuario_admin.id)

    db.refresh(var)
    assert var.estoque_fisico == 40
    assert var.estoque_reservado == 0
    db.refresh(ped)
    assert ped.status == StatusPedido.FATURADO
    assert ped.faturado_em is not None

    contas = list(db.scalars(select(ContaReceber).where(ContaReceber.pedido_id == ped.id)))
    assert len(contas) == 1
    assert contas[0].valor == Decimal("100.00")
    assert contas[0].vencimento == date.today()
    assert contas[0].status == StatusConta.PENDENTE


def test_faturar_conta_30_dias(db, usuario_vendedor, usuario_admin):
    prod = _produto(db, "F2")
    var = _variacao(db, prod, fisico=50)
    cli = _cliente(db, condicao="30 dias")
    ped = _novo_pedido(db, cli, usuario_vendedor)
    _add(db, ped, var, qtd=10, preco_unit=Decimal("10.00"))
    pedido_service.confirmar(db, ped.id, usuario_vendedor.id)
    pedido_service.faturar(db, ped.id, usuario_admin.id)
    contas = list(db.scalars(select(ContaReceber).where(ContaReceber.pedido_id == ped.id)))
    assert len(contas) == 1
    assert contas[0].vencimento == date.today() + timedelta(days=30)


def test_faturar_parcelado_3x_ajusta_centavos(db, usuario_vendedor, usuario_admin):
    prod = _produto(db, "F3")
    var = _variacao(db, prod, fisico=50)
    cli = _cliente(db, condicao="3x")
    ped = _novo_pedido(db, cli, usuario_vendedor)
    # total 100.00 / 3 -> 33.33, 33.33, 33.34
    _add(db, ped, var, qtd=10, preco_unit=Decimal("10.00"))
    pedido_service.confirmar(db, ped.id, usuario_vendedor.id)
    pedido_service.faturar(db, ped.id, usuario_admin.id)
    contas = sorted(
        db.scalars(select(ContaReceber).where(ContaReceber.pedido_id == ped.id)),
        key=lambda c: c.parcela,
    )
    assert len(contas) == 3
    assert [c.valor for c in contas] == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]
    assert sum(c.valor for c in contas) == Decimal("100.00")
    assert contas[0].vencimento == date.today()
    assert contas[1].vencimento == date.today() + timedelta(days=30)
    assert contas[2].vencimento == date.today() + timedelta(days=60)


# --------------------------------------------------------- limite de desconto
def test_vendedor_acima_do_limite_falha(db, usuario_vendedor):
    prod = _produto(db, "D1")
    var = _variacao(db, prod)
    cli = _cliente(db)
    ped = _novo_pedido(db, cli, usuario_vendedor)
    # bruto 100, desconto 20 -> 20% > 10%
    with pytest.raises(PermissaoNegadaError):
        _add(db, ped, var, qtd=10, preco_unit=Decimal("10.00"), desconto=Decimal("20.00"))


def test_vendedor_dentro_do_limite_ok(db, usuario_vendedor):
    prod = _produto(db, "D2")
    var = _variacao(db, prod)
    cli = _cliente(db)
    ped = _novo_pedido(db, cli, usuario_vendedor)
    item = _add(db, ped, var, qtd=10, preco_unit=Decimal("10.00"), desconto=Decimal("10.00"))
    assert item.subtotal == Decimal("90.00")


def test_admin_pode_desconto_acima_do_limite(db, usuario_admin):
    prod = _produto(db, "D3")
    var = _variacao(db, prod)
    cli = _cliente(db)
    ped = _novo_pedido(db, cli, usuario_admin)
    item = _add(
        db, ped, var, perfil="admin", qtd=10, preco_unit=Decimal("10.00"), desconto=Decimal("50.00")
    )
    assert item.subtotal == Decimal("50.00")


# --------------------------------------------------------- separação
def test_separacao_conclui_apos_conferencia(db, usuario_vendedor):
    prod1 = _produto(db, "S1")
    prod2 = _produto(db, "S2")
    var1 = _variacao(db, prod1, fisico=50, cor="a")
    var2 = _variacao(db, prod2, fisico=50, cor="b")
    cli = _cliente(db)
    ped = _novo_pedido(db, cli, usuario_vendedor)
    i1 = _add(db, ped, var1, qtd=2, preco_unit=Decimal("1.00"))
    i2 = _add(db, ped, var2, qtd=2, preco_unit=Decimal("1.00"))
    pedido_service.confirmar(db, ped.id, usuario_vendedor.id)

    # ainda não conferido -> não conclui
    with pytest.raises(RegraNegocioError):
        pedido_service.concluir_separacao(db, ped.id)

    pedido_service.marcar_item_separado(db, ped.id, i1.id, True)
    pedido_service.marcar_item_separado(db, ped.id, i2.id, True)
    db.refresh(ped)
    assert ped.status == StatusPedido.SEPARACAO
    pedido_service.concluir_separacao(db, ped.id)  # ok


# --------------------------------------------------------- RBAC via HTTP
@pytest.fixture
def client_vendedor(db, usuario_vendedor):
    """TestClient autenticado como vendedor (override de get_current_user e get_db)."""
    from fastapi.testclient import TestClient

    from app.deps.auth import get_current_user
    from app.deps.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: usuario_vendedor
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_rbac_vendedor_nao_fatura(client_vendedor, db, usuario_vendedor):
    """Faturar é a linha que o vendedor não cruza — mesmo no próprio pedido."""
    prod = _produto(db, "RB1")
    var = _variacao(db, prod, fisico=50)
    ped = _novo_pedido(db, _cliente(db), usuario_vendedor)
    _add(db, ped, var, qtd=1, preco_unit=Decimal("10.00"))
    pedido_service.confirmar(db, ped.id, usuario_vendedor.id)
    db.flush()

    r = client_vendedor.post(f"/pedidos/{ped.id}/faturar", follow_redirects=False)
    assert r.status_code == 403


# --------------------------------------------------------- guardas extras
def test_editar_item_fora_de_rascunho_falha(db, usuario_vendedor):
    prod = _produto(db, "E1")
    var = _variacao(db, prod, fisico=50)
    cli = _cliente(db)
    ped = _novo_pedido(db, cli, usuario_vendedor)
    _add(db, ped, var, qtd=2, preco_unit=Decimal("1.00"))
    pedido_service.confirmar(db, ped.id, usuario_vendedor.id)
    with pytest.raises(RegraNegocioError):
        _add(db, ped, var, qtd=1, preco_unit=Decimal("1.00"))


def test_get_pedido_inexistente(db):
    with pytest.raises(NaoEncontradoError):
        pedido_service.confirmar(db, 999999, 1)


# --------------------------------------------------------- cliente livre (balcão)
def test_pedido_sem_cliente_vira_consumidor(db, usuario_vendedor):
    """Venda de balcão: nenhum campo de cliente preenchido."""
    ped = pedido_service.criar(db, None, usuario_vendedor.id)
    assert ped.cliente_id is None
    assert ped.nome_cliente == "CONSUMIDOR"
    assert ped.telefone_cliente is None
    # Sem cadastro não há condição negociada: o faturamento trata como à vista.
    assert ped.condicao_pagto == "À VISTA"


def test_pedido_guarda_nome_e_telefone_livres(db, usuario_vendedor):
    ped = pedido_service.criar(
        db,
        None,
        usuario_vendedor.id,
        cliente_nome="Maria do Balcão",
        cliente_telefone="11 98888-7777",
    )
    assert ped.cliente_id is None
    assert ped.nome_cliente == "Maria do Balcão"
    assert ped.telefone_cliente == "11 98888-7777"


def test_telefone_conhecido_vincula_o_cadastro_sozinho(db, usuario_vendedor):
    """O vendedor digita só o telefone e o pedido cai na ficha certa."""
    cli = Cliente(nome="Cliente Fiel", telefone="(11) 98888-7777", condicao_pagto_padrao="30 dias")
    db.add(cli)
    db.flush()

    ped = pedido_service.criar(
        db, None, usuario_vendedor.id, cliente_nome="digitou errado", cliente_telefone="11988887777"
    )

    assert ped.cliente_id == cli.id
    # O nome que vale é o do cadastro, não o que foi digitado na pressa.
    assert ped.nome_cliente == "Cliente Fiel"
    assert ped.condicao_pagto == "30 dias"
    assert ped.cliente_nome is None


def test_telefone_desconhecido_nao_vincula(db, usuario_vendedor):
    ped = pedido_service.criar(db, None, usuario_vendedor.id, cliente_telefone="11 90000-0001")
    assert ped.cliente_id is None
    assert ped.cliente_telefone == "11 90000-0001"


def test_telefone_curto_nao_vincula(db, usuario_vendedor):
    """4 dígitos não podem casar com meia agenda."""
    cli = Cliente(nome="Curto", telefone="1234")
    db.add(cli)
    db.flush()
    ped = pedido_service.criar(db, None, usuario_vendedor.id, cliente_telefone="1234")
    assert ped.cliente_id is None


def test_cliente_id_invalido_falha(db, usuario_vendedor):
    with pytest.raises(NaoEncontradoError):
        pedido_service.criar(db, 999_999_999, usuario_vendedor.id)


def test_faturar_pedido_de_balcao_gera_conta_a_vista(db, usuario_vendedor, usuario_admin):
    """O faturamento não pode quebrar quando o pedido não tem cliente cadastrado."""
    prod = _produto(db, "BALC1")
    var = _variacao(db, prod, fisico=50)
    ped = pedido_service.criar(db, None, usuario_vendedor.id, cliente_nome="Passante")
    _add(db, ped, var, qtd=2, preco_unit=Decimal("10.00"))
    pedido_service.confirmar(db, ped.id, usuario_vendedor.id)
    pedido_service.faturar(db, ped.id, usuario_admin.id)

    contas = list(db.scalars(select(ContaReceber).where(ContaReceber.pedido_id == ped.id)))
    assert len(contas) == 1
    assert contas[0].valor == Decimal("20.00")
    assert contas[0].vencimento == date.today()


def test_busca_cliente_acha_por_telefone_formatado_diferente(db):
    from app.repositories.cliente_repo import cliente_repo

    cli = Cliente(nome=f"Busca {uuid.uuid4().hex[:6]}", telefone="(11) 97777-1234")
    db.add(cli)
    db.flush()

    achados = cliente_repo.busca_rapida(db, "11977771234")
    assert cli.id in [c.id for c in achados]
