"""Testes de PEDIDOS + SEPARAÇÃO.

Criam seus próprios produtos/variações/cliente (não assumem o banco vazio).
Rodam dentro da transação revertida do fixture `db`.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.errors import NaoEncontradoError, RegraNegocioError
from app.models.cliente import Cliente
from app.models.conta_receber import ContaReceber
from app.models.enums import EstoqueModo, StatusConta, StatusPedido
from app.models.pedido import Pedido
from app.models.produto import Produto, ProdutoVariacao
from app.schemas.pedido import ItemAdicionar, PedidoCompletoCreate
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
def test_vendedor_acima_do_limite_grava_e_avisa(db, usuario_vendedor):
    """O limite de desconto informa, não barra.

    Travar a gravação empurrava a venda para fora do sistema: quem está no balcão fecha
    o negócio de qualquer jeito e depois o pedido não existe em lugar nenhum. O número
    continua valendo como aviso na tela.
    """
    prod = _produto(db, "D1")
    var = _variacao(db, prod)
    cli = _cliente(db)
    ped = _novo_pedido(db, cli, usuario_vendedor)
    # bruto 100, desconto 20 -> 20% > 10%
    item = _add(db, ped, var, qtd=10, preco_unit=Decimal("10.00"), desconto=Decimal("20.00"))
    assert item.subtotal == Decimal("80.00")

    aviso = pedido_service.aviso_de_preco("vendedor", prod, 10, Decimal("10.00"), Decimal("20.00"))
    assert aviso is not None and "limite" in aviso.lower()


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


# --------------------------------------------------------- preço mínimo
def test_preco_minimo_avisa_mas_nao_bloqueia(db, usuario_vendedor):
    prod = _produto(db, "MIN1", pouca=Decimal("10.00"))
    prod.preco_minimo = Decimal("8.00")
    db.flush()
    var = _variacao(db, prod)
    ped = _novo_pedido(db, _cliente(db), usuario_vendedor)

    item = _add(db, ped, var, qtd=1, preco_unit=Decimal("7.99"))
    assert item.preco_unit == Decimal("7.99")

    aviso = pedido_service.aviso_de_preco("vendedor", prod, 1, Decimal("7.99"))
    assert aviso is not None and "preço mínimo" in aviso.lower()


def test_preco_minimo_aceita_valor_igual_ao_piso(db, usuario_vendedor):
    prod = _produto(db, "MIN2", pouca=Decimal("10.00"))
    prod.preco_minimo = Decimal("8.00")
    db.flush()
    var = _variacao(db, prod)
    ped = _novo_pedido(db, _cliente(db), usuario_vendedor)
    item = _add(db, ped, var, qtd=1, preco_unit=Decimal("8.00"))
    assert item.preco_unit == Decimal("8.00")


def test_aviso_de_piso_olha_o_preco_efetivo(db, usuario_vendedor):
    """O piso é sobre o preço efetivo: 10,00 com 5% de desconto cai abaixo de 9,60."""
    prod = _produto(db, "MIN3", pouca=Decimal("10.00"))
    prod.preco_minimo = Decimal("9.60")
    db.flush()
    var = _variacao(db, prod)
    ped = _novo_pedido(db, _cliente(db), usuario_vendedor)

    # 10 un x 10,00 = 100,00; desconto de 5,00 (dentro do limite de 10%) -> 9,50/un
    _add(db, ped, var, qtd=10, preco_unit=Decimal("10.00"), desconto=Decimal("5.00"))

    # O aviso olha o preço EFETIVO: sem isso ele ficaria mudo justamente quando é o
    # desconto, e não o preço digitado, que derruba a margem.
    aviso = pedido_service.aviso_de_preco("vendedor", prod, 10, Decimal("10.00"), Decimal("5.00"))
    assert aviso is not None and "preço mínimo" in aviso.lower()


def test_admin_passa_por_cima_do_preco_minimo(db, usuario_admin):
    prod = _produto(db, "MIN4", pouca=Decimal("10.00"))
    prod.preco_minimo = Decimal("8.00")
    db.flush()
    var = _variacao(db, prod)
    ped = _novo_pedido(db, _cliente(db), usuario_admin)
    item = _add(db, ped, var, perfil="admin", qtd=1, preco_unit=Decimal("1.00"))
    assert item.preco_unit == Decimal("1.00")


def test_preco_minimo_zero_nao_trava(db, usuario_vendedor):
    """Produto ainda não revisado (piso 0) continua vendável a qualquer preço."""
    prod = _produto(db, "MIN5", pouca=Decimal("10.00"))
    var = _variacao(db, prod)
    ped = _novo_pedido(db, _cliente(db), usuario_vendedor)
    item = _add(db, ped, var, qtd=1, preco_unit=Decimal("0.50"))
    assert item.preco_unit == Decimal("0.50")


def test_desconto_total_abaixo_do_piso_passa(db, usuario_vendedor):
    prod = _produto(db, "MIN6", pouca=Decimal("10.00"))
    prod.preco_minimo = Decimal("9.80")
    db.flush()
    var = _variacao(db, prod)
    ped = _novo_pedido(db, _cliente(db), usuario_vendedor)
    _add(db, ped, var, qtd=10, preco_unit=Decimal("10.00"))

    # 5,00 sobre 100,00 = 5%, e derruba a unidade para 9,50 — passa, com o piso valendo
    # como informação e não como trava.
    pedido_service.aplicar_desconto_total(db, ped.id, Decimal("5.00"), "vendedor")
    db.refresh(ped)
    assert ped.total == Decimal("95.00")


# --------------------------------------------------------- item repetido
def test_mesmo_produto_mesmo_preco_soma_na_mesma_linha(db, usuario_vendedor):
    prod = _produto(db, "REP1")
    var = _variacao(db, prod, fisico=100)
    ped = _novo_pedido(db, _cliente(db), usuario_vendedor)

    _add(db, ped, var, qtd=3, preco_unit=Decimal("10.00"))
    item = _add(db, ped, var, qtd=2, preco_unit=Decimal("10.00"))

    db.refresh(ped)
    assert len(ped.itens) == 1
    assert item.qtd == 5
    assert item.subtotal == Decimal("50.00")
    assert ped.total == Decimal("50.00")


def test_mesmo_produto_preco_diferente_abre_linha_nova(db, usuario_vendedor):
    """Preço diferente é outra negociação — juntar esconderia isso do faturamento."""
    prod = _produto(db, "REP2")
    var = _variacao(db, prod, fisico=100)
    ped = _novo_pedido(db, _cliente(db), usuario_vendedor)

    _add(db, ped, var, qtd=1, preco_unit=Decimal("10.00"))
    _add(db, ped, var, qtd=1, preco_unit=Decimal("9.00"))

    db.refresh(ped)
    assert len(ped.itens) == 2
    assert ped.total == Decimal("19.00")


def test_somar_consolida_qtd_e_desconto_na_mesma_linha(db, usuario_vendedor):
    """Dois lançamentos do mesmo item pelo mesmo preço viram uma linha só."""
    prod = _produto(db, "REP3")
    var = _variacao(db, prod, fisico=100)
    ped = _novo_pedido(db, _cliente(db), usuario_vendedor)

    _add(db, ped, var, qtd=1, preco_unit=Decimal("100.00"), desconto=Decimal("9.00"))
    _add(db, ped, var, qtd=1, preco_unit=Decimal("100.00"), desconto=Decimal("40.00"))

    # Mesmo preço: uma linha só, com quantidade e desconto somados.
    db.refresh(ped)
    assert len(ped.itens) == 1
    assert ped.itens[0].qtd == 2
    assert ped.itens[0].desconto == Decimal("49.00")
    assert ped.total == Decimal("151.00")


# ======================================================= tela única (criar_completo)
def _completo(**kw):
    """PedidoCompletoCreate com os campos de cliente vazios, como o balcão manda."""
    base = {"cliente_id": None, "cliente_nome": None, "cliente_telefone": None}
    return PedidoCompletoCreate(**{**base, **kw})


def test_criar_completo_grava_catalogo_e_avulso_de_uma_vez(db, usuario_vendedor):
    """O pedido inteiro numa tacada: é o que a tela `/pedidos/novo` faz."""
    prod = _produto(db, "TU1", pouca=Decimal("10.00"))
    var = _variacao(db, prod)

    pedido = pedido_service.criar_completo(
        db,
        _completo(
            cliente_nome="Maria",
            desconto_total=Decimal("5.00"),
            itens=[
                {"tipo": "catalogo", "variacao_id": var.id, "qtd": 3},
                {
                    "tipo": "avulso",
                    "nome": "CANECA PERSONALIZADA",
                    "codigo": "AV-1",
                    "detalhe": "vermelha",
                    "qtd": 2,
                    "preco_unit": Decimal("20.00"),
                },
            ],
        ),
        usuario_vendedor.id,
        "vendedor",
    )

    db.refresh(pedido)
    assert pedido.status == StatusPedido.RASCUNHO
    assert pedido.nome_cliente == "Maria"
    assert len(pedido.itens) == 2
    # 3×10 + 2×20 = 70, menos 5 de desconto
    assert pedido.total == Decimal("65.00")

    catalogo = next(i for i in pedido.itens if not i.e_avulso)
    avulso = next(i for i in pedido.itens if i.e_avulso)
    # Preço não informado: quem resolveu foi o servidor, pelo catálogo.
    assert catalogo.preco_unit == Decimal("10.00")
    assert catalogo.descricao == prod.descricao  # snapshot do que foi vendido
    assert catalogo.codigo == prod.codigo
    assert avulso.produto_variacao_id is None
    assert avulso.descricao == "CANECA PERSONALIZADA"
    assert avulso.detalhe == "vermelha"


def test_criar_completo_respeita_o_preco_digitado_e_a_faixa_de_atacado(db, usuario_vendedor):
    prod = _produto(db, "TU2", pouca=Decimal("10.00"), muita=Decimal("8.00"), corte=10)
    var = _variacao(db, prod)

    pedido = pedido_service.criar_completo(
        db,
        _completo(
            itens=[
                # Sem preço: 12 un passa do corte, então vale a faixa de atacado.
                {"tipo": "catalogo", "variacao_id": var.id, "qtd": 12},
                # Com preço digitado: o valor do vendedor manda.
                {
                    "tipo": "catalogo",
                    "variacao_id": var.id,
                    "qtd": 1,
                    "preco_unit": Decimal("3.00"),
                },
            ]
        ),
        usuario_vendedor.id,
        "vendedor",
    )

    db.refresh(pedido)
    precos = sorted(i.preco_unit for i in pedido.itens)
    assert precos == [Decimal("3.00"), Decimal("8.00")]


def test_criar_completo_com_item_invalido_no_meio_nao_grava_nada(db, usuario_vendedor):
    """Tudo é resolvido ANTES da primeira escrita — não sobra rascunho órfão."""
    prod = _produto(db, "TU3")
    var = _variacao(db, prod)
    antes = db.scalar(select(func.count()).select_from(Pedido))

    with pytest.raises(NaoEncontradoError):
        pedido_service.criar_completo(
            db,
            _completo(
                itens=[
                    {"tipo": "catalogo", "variacao_id": var.id, "qtd": 1},
                    {"tipo": "catalogo", "variacao_id": 10**9, "qtd": 1},  # não existe
                ]
            ),
            usuario_vendedor.id,
            "vendedor",
        )

    assert db.scalar(select(func.count()).select_from(Pedido)) == antes


def test_criar_completo_recusa_produto_inativo(db, usuario_vendedor):
    prod = _produto(db, "TU4")
    prod.ativo = False
    var = _variacao(db, prod)
    db.flush()

    with pytest.raises(RegraNegocioError) as exc:
        pedido_service.criar_completo(
            db,
            _completo(itens=[{"tipo": "catalogo", "variacao_id": var.id, "qtd": 1}]),
            usuario_vendedor.id,
            "vendedor",
        )
    assert "inativo" in str(exc.value).lower()


def test_criar_completo_converte_caixas_em_unidades(db, usuario_vendedor):
    prod = _produto(db, "TU5", pouca=Decimal("2.00"), unid_caixa=12)
    var = _variacao(db, prod)

    pedido = pedido_service.criar_completo(
        db,
        _completo(itens=[{"tipo": "catalogo", "variacao_id": var.id, "qtd_caixas": 3}]),
        usuario_vendedor.id,
        "vendedor",
    )

    db.refresh(pedido)
    item = pedido.itens[0]
    assert item.qtd == 36
    assert item.qtd_caixas == 3
    assert pedido.total == Decimal("72.00")


# ======================================================= item avulso e estoque
def test_confirmar_reserva_estoque_so_dos_itens_de_catalogo(db, usuario_vendedor):
    """Item avulso não está no catálogo: não há saldo a reservar, e isso não pode travar."""
    prod = _produto(db, "AV1", pouca=Decimal("10.00"))
    var = _variacao(db, prod, fisico=50)

    pedido = pedido_service.criar_completo(
        db,
        _completo(
            itens=[
                {"tipo": "catalogo", "variacao_id": var.id, "qtd": 4},
                {
                    "tipo": "avulso",
                    "nome": "FRETE",
                    "qtd": 1,
                    "preco_unit": Decimal("30.00"),
                },
            ]
        ),
        usuario_vendedor.id,
        "vendedor",
    )
    pedido_service.confirmar(db, pedido.id, usuario_vendedor.id)

    db.refresh(var)
    assert var.estoque_reservado == 4  # só o item de catálogo mexeu no saldo
    assert pedido.numero is not None


def test_cancelar_estorna_so_o_que_foi_reservado(db, usuario_vendedor):
    prod = _produto(db, "AV2", pouca=Decimal("10.00"))
    var = _variacao(db, prod, fisico=50)

    pedido = pedido_service.criar_completo(
        db,
        _completo(
            itens=[
                {"tipo": "catalogo", "variacao_id": var.id, "qtd": 4},
                {"tipo": "avulso", "nome": "MONTAGEM", "qtd": 1, "preco_unit": Decimal("5.00")},
            ]
        ),
        usuario_vendedor.id,
        "vendedor",
    )
    pedido_service.confirmar(db, pedido.id, usuario_vendedor.id)
    pedido_service.cancelar(db, pedido.id, usuario_vendedor.id)

    db.refresh(var)
    assert var.estoque_reservado == 0


def test_item_avulso_nunca_funde_com_outra_linha(db, usuario_vendedor):
    """Sem chave de catálogo, dois nomes iguais podem ser negociações diferentes."""
    pedido = pedido_service.criar_completo(
        db,
        _completo(
            itens=[
                {"tipo": "avulso", "nome": "AJUSTE", "qtd": 1, "preco_unit": Decimal("10.00")},
                {"tipo": "avulso", "nome": "AJUSTE", "qtd": 1, "preco_unit": Decimal("10.00")},
            ]
        ),
        usuario_vendedor.id,
        "vendedor",
    )

    db.refresh(pedido)
    assert len(pedido.itens) == 2
    assert pedido.total == Decimal("20.00")


def test_pedido_so_de_item_avulso_confirma_e_fatura(db, usuario_vendedor, usuario_admin):
    """Nenhum item move estoque — o ciclo tem que andar mesmo assim."""
    pedido = pedido_service.criar_completo(
        db,
        _completo(
            itens=[{"tipo": "avulso", "nome": "SERVICO", "qtd": 1, "preco_unit": Decimal("99.00")}]
        ),
        usuario_vendedor.id,
        "vendedor",
    )
    pedido_service.confirmar(db, pedido.id, usuario_vendedor.id)
    pedido_service.faturar(db, pedido.id, usuario_admin.id)

    db.refresh(pedido)
    assert pedido.status == StatusPedido.FATURADO
    assert pedido.total == Decimal("99.00")


def test_snapshot_nao_muda_quando_o_produto_e_renomeado(db, usuario_vendedor):
    """O pedido é documento: renomear o produto não pode reescrever a venda de ontem."""
    prod = _produto(db, "SNAP1", pouca=Decimal("10.00"))
    var = _variacao(db, prod)
    pedido = pedido_service.criar_completo(
        db,
        _completo(itens=[{"tipo": "catalogo", "variacao_id": var.id, "qtd": 1}]),
        usuario_vendedor.id,
        "vendedor",
    )

    prod.descricao = "NOME COMPLETAMENTE DIFERENTE"
    prod.codigo = "OUTRO"
    db.flush()

    db.refresh(pedido)
    assert pedido.itens[0].descricao_exibida == "Produto SNAP1"
    assert pedido.itens[0].codigo_exibido == "SNAP1"


# ======================================================= lista: filtros e leitura rápida
def test_lista_filtra_por_status(client_vendedor, db, usuario_vendedor):
    """O filtro é do SERVIDOR: linha fora do filtro não vem no HTML, não é escondida."""
    prod = _produto(db, "FIL1")
    var = _variacao(db, prod)
    rascunho = _novo_pedido(db, _cliente(db), usuario_vendedor)
    _add(db, rascunho, var, qtd=1)
    confirmado = _novo_pedido(db, _cliente(db), usuario_vendedor)
    _add(db, confirmado, var, qtd=1)
    pedido_service.confirmar(db, confirmado.id, usuario_vendedor.id)

    so_rascunho = client_vendedor.get("/pedidos/lista?status=rascunho").text
    assert f'/pedidos/{rascunho.id}"' in so_rascunho
    assert f'/pedidos/{confirmado.id}"' not in so_rascunho

    so_confirmado = client_vendedor.get("/pedidos/lista?status=confirmado").text
    assert f'/pedidos/{confirmado.id}"' in so_confirmado
    assert f'/pedidos/{rascunho.id}"' not in so_confirmado


def test_lista_ignora_filtro_invalido_em_vez_de_estourar(client_vendedor, db, usuario_vendedor):
    """Link velho ou dedo no endereço mostra tudo — não é tela de erro."""
    ped = _novo_pedido(db, _cliente(db), usuario_vendedor)

    r = client_vendedor.get("/pedidos/lista?status=coisa-que-nao-existe&origem=nada")
    assert r.status_code == 200
    assert f'/pedidos/{ped.id}"' in r.text


def test_lista_mostra_selo_colorido_contagem_e_origem(client_vendedor, db, usuario_vendedor):
    """A lista é lida de relance: cor do status, quantos itens e de onde veio."""
    prod = _produto(db, "FIL2")
    var = _variacao(db, prod)
    ped = _novo_pedido(db, _cliente(db), usuario_vendedor)
    _add(db, ped, var, qtd=1)
    _add(db, ped, var, qtd=1, preco_unit=Decimal("3.00"))  # preço diferente = 2ª linha

    t = client_vendedor.get("/pedidos").text
    assert 'class="selo-rascunho' in t  # e não um cinza só para todo status
    assert "Balcão" in t  # origem por extenso, com ícone
    assert "Todos os status" in t and "Toda origem" in t  # os dois filtros na tela


# ======================================================= resumo e observação
def test_montar_resumo_traz_os_itens_em_centavos(db, usuario_vendedor):
    """O `<canvas>` desenha a partir daqui e não recalcula dinheiro."""
    prod = _produto(db, "RES1", pouca=Decimal("15.50"))
    var = _variacao(db, prod)
    pedido = pedido_service.criar_completo(
        db,
        _completo(
            cliente_nome="Maria",
            itens=[
                {"tipo": "catalogo", "variacao_id": var.id, "qtd": 3},
                {"tipo": "avulso", "nome": "FRETE", "qtd": 1, "preco_unit": Decimal("20.00")},
            ],
        ),
        usuario_vendedor.id,
        "vendedor",
    )
    db.refresh(pedido)

    resumo = pedido_service.montar_resumo(pedido)
    assert resumo.cliente == "Maria"
    assert resumo.numero == "RASCUNHO"  # ainda não confirmado
    assert [i.descricao for i in resumo.itens] == ["Produto RES1", "FRETE"]
    assert resumo.itens[0].preco_centavos == 1550
    assert resumo.itens[0].subtotal_centavos == 4650
    assert resumo.total_centavos == 6650

    pedido_service.confirmar(db, pedido.id, usuario_vendedor.id)
    assert pedido_service.montar_resumo(pedido).numero == f"#{pedido.numero}"


def test_resumo_sai_em_camel_case_para_o_javascript(db, usuario_vendedor):
    """O carrinho do navegador monta as chaves em camelCase; o servidor fala a mesma língua."""
    prod = _produto(db, "RES2")
    var = _variacao(db, prod)
    pedido = pedido_service.criar_completo(
        db,
        _completo(itens=[{"tipo": "catalogo", "variacao_id": var.id, "qtd": 1}]),
        usuario_vendedor.id,
        "vendedor",
    )
    db.refresh(pedido)

    bruto = pedido_service.montar_resumo(pedido).model_dump_json(by_alias=True)
    assert '"precoCentavos"' in bruto
    assert '"totalCentavos"' in bruto
    assert "preco_centavos" not in bruto


def test_observacao_editavel_depois_de_confirmar(db, usuario_vendedor):
    """O recado de entrega quase sempre chega DEPOIS que o pedido fechou."""
    prod = _produto(db, "OBS1")
    var = _variacao(db, prod, fisico=50)
    pedido = pedido_service.criar_completo(
        db,
        _completo(itens=[{"tipo": "catalogo", "variacao_id": var.id, "qtd": 1}]),
        usuario_vendedor.id,
        "vendedor",
    )
    pedido_service.confirmar(db, pedido.id, usuario_vendedor.id)

    pedido_service.definir_observacao(db, pedido.id, "  entregar após as 18h  ")
    db.refresh(pedido)
    assert pedido.observacao == "entregar após as 18h"

    # Vazio volta a ser None, não string vazia.
    pedido_service.definir_observacao(db, pedido.id, "   ")
    db.refresh(pedido)
    assert pedido.observacao is None


def test_observacao_recusada_no_pedido_finalizado(db, usuario_vendedor, usuario_admin):
    prod = _produto(db, "OBS2")
    var = _variacao(db, prod, fisico=50)
    pedido = pedido_service.criar_completo(
        db,
        _completo(itens=[{"tipo": "catalogo", "variacao_id": var.id, "qtd": 1}]),
        usuario_vendedor.id,
        "vendedor",
    )
    pedido_service.confirmar(db, pedido.id, usuario_vendedor.id)
    pedido_service.faturar(db, pedido.id, usuario_admin.id)
    pedido_service.entregar(db, pedido.id)

    with pytest.raises(RegraNegocioError):
        pedido_service.definir_observacao(db, pedido.id, "tarde demais")


# ======================================================= faixas de preço
def _com_faixas(db, codigo, faixas, **kw):
    """Produto com tabela de atacado, criado pelo service (passa pela validação)."""
    from app.schemas.produto import FaixaPrecoCreate, ProdutoCreate
    from app.services.produto_service import produto_service

    return produto_service.criar(
        db,
        ProdutoCreate(
            codigo=codigo,
            descricao=f"Produto {codigo}",
            preco_pouca_qtd=kw.get("pouca", Decimal("10.00")),
            preco_muita_qtd=kw.get("muita", Decimal("9.00")),
            qtd_corte_atacado=kw.get("corte"),
            faixas=[FaixaPrecoCreate(min_qtd=q, preco=Decimal(p)) for q, p in faixas],
        ),
    )


@pytest.mark.parametrize(
    ("qtd", "preco", "rotulo"),
    [
        (1, "10.00", "1 a 9 un"),
        (9, "10.00", "1 a 9 un"),
        (10, "8.00", "10 a 49 un"),
        (49, "8.00", "10 a 49 un"),
        (50, "6.50", "50+ un"),
        (900, "6.50", "50+ un"),
    ],
)
def test_a_tabela_manda_no_preco(db, qtd, preco, rotulo):
    """A regra em uma frase: se o produto tem tabela, a faixa da quantidade manda."""
    prod = _com_faixas(db, f"FX{qtd}", [(1, "10.00"), (10, "8.00"), (50, "6.50")], corte=5)

    s = pedido_service.sugerir_preco(prod, qtd)
    assert s.preco_sugerido == Decimal(preco)
    assert s.faixa == "tabela"
    assert s.faixa_rotulo == rotulo


def test_tabela_vence_varejo_e_atacado(db):
    """O corte em 5 diria "atacado" (R$ 9,00) para 10 un; a tabela diz R$ 8,00."""
    prod = _com_faixas(db, "FXV", [(1, "10.00"), (10, "8.00")], muita=Decimal("9.00"), corte=5)
    assert pedido_service.sugerir_preco(prod, 10).preco_sugerido == Decimal("8.00")


def test_sem_tabela_continua_na_regra_de_sempre(db):
    """Produto sem faixas é, byte a byte, o comportamento de antes da migration."""
    prod = _produto(db, "FXS", pouca=Decimal("10.00"), muita=Decimal("9.00"), corte=50)

    assert pedido_service.sugerir_preco(prod, 1).faixa == "varejo"
    assert pedido_service.sugerir_preco(prod, 50).faixa == "atacado"
    assert pedido_service.sugerir_preco(prod, 50).preco_sugerido == Decimal("9.00")
    assert pedido_service.sugerir_preco(prod, 1).faixas == []


def test_abaixo_da_primeira_faixa_cai_no_corte(db):
    """Tabela começando em 10: quem leva 3 volta para varejo/atacado.

    Só acontece com tabela escrita na mão ou vinda do ETL — o formulário exige a faixa
    de 1 un. Mas é melhor cair no preço antigo do que vender a R$ 0,00.
    """
    from app.models.produto import ProdutoFaixaPreco

    prod = _produto(db, "FXB", pouca=Decimal("10.00"), muita=Decimal("9.00"), corte=100)
    prod.faixas.append(ProdutoFaixaPreco(min_qtd=10, preco=Decimal("8.00")))
    db.flush()

    assert pedido_service.sugerir_preco(prod, 3).faixa == "varejo"
    assert pedido_service.sugerir_preco(prod, 3).preco_sugerido == Decimal("10.00")
    assert pedido_service.sugerir_preco(prod, 10).faixa == "tabela"


def test_proxima_faixa_e_o_empurraozinho(db):
    prod = _com_faixas(db, "FXP", [(1, "10.00"), (10, "8.00"), (50, "6.50")])

    assert pedido_service.sugerir_preco(prod, 5).proxima_faixa.min_qtd == 10
    assert pedido_service.sugerir_preco(prod, 50).proxima_faixa is None


def test_pedido_grava_o_preco_da_faixa(db, usuario_vendedor):
    """O caminho inteiro: carrinho sem preço digitado sai pela tabela."""
    prod = _com_faixas(db, "FXPED", [(1, "10.00"), (10, "8.00")])
    var = _variacao(db, prod, fisico=200)

    pedido = pedido_service.criar_completo(
        db,
        _completo(itens=[{"tipo": "catalogo", "variacao_id": var.id, "qtd": 12}]),
        usuario_vendedor.id,
        "vendedor",
    )
    db.refresh(pedido)
    assert pedido.itens[0].preco_unit == Decimal("8.00")
    assert pedido.total == Decimal("96.00")


def test_preco_digitado_ainda_vence_a_tabela(db, usuario_vendedor):
    """A tabela manda sobre varejo/atacado, não sobre o que o vendedor negociou."""
    prod = _com_faixas(db, "FXD", [(1, "10.00"), (10, "8.00")])
    var = _variacao(db, prod, fisico=200)

    pedido = pedido_service.criar_completo(
        db,
        _completo(
            itens=[
                {
                    "tipo": "catalogo",
                    "variacao_id": var.id,
                    "qtd": 12,
                    "preco_unit": Decimal("7.00"),
                }
            ]
        ),
        usuario_vendedor.id,
        "vendedor",
    )
    db.refresh(pedido)
    assert pedido.itens[0].preco_unit == Decimal("7.00")
