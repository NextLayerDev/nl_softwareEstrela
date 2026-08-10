"""Testes da COLAGEM de planilha em pedido.

Três camadas, do mais barato ao mais caro:
  1. `core/numeros_br.py` e `core/colagem.py` — puros, sem banco.
  2. matching contra o catálogo (`colagem_service`), com banco.
  3. isolamento por linha: uma linha ruim não pode derrubar as outras nem a Session.

Criam seus próprios produtos (não assumem o banco vazio) e rodam dentro da transação
revertida do fixture `db`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.core.colagem import consolidar, normalizar_codigo, parse_blocos, parse_colagem
from app.core.numeros_br import parse_decimal_br, parse_int_br
from app.models.cliente import Cliente
from app.models.enums import EstoqueModo, StatusPedido
from app.models.produto import Produto, ProdutoCodigoAlt, ProdutoVariacao
from app.schemas.pedido import PedidoCreate
from app.services.colagem_service import colagem_service
from app.services.pedido_service import pedido_service


# --------------------------------------------------------------------- helpers
def _codigo() -> str:
    return f"CL{uuid.uuid4().hex[:8].upper()}"


def _produto(
    db,
    *,
    codigo=None,
    descricao=None,
    pouca=Decimal("10.00"),
    muita=Decimal("8.00"),
    corte=None,
    minimo=Decimal("0.00"),
    ativo=True,
    cores=("azul",),
):
    codigo = codigo or _codigo()
    p = Produto(
        codigo=codigo,
        descricao=descricao or f"PRODUTO DE COLAGEM {codigo}",
        preco_pouca_qtd=pouca,
        preco_muita_qtd=muita,
        qtd_corte_atacado=corte,
        preco_minimo=minimo,
        ativo=ativo,
    )
    db.add(p)
    db.flush()
    for cor in cores:
        db.add(
            ProdutoVariacao(
                produto_id=p.id,
                cor=cor,
                estoque_modo=EstoqueModo.EXATO,
                estoque_fisico=1000,
                estoque_reservado=0,
            )
        )
    db.flush()
    db.refresh(p)
    return p


def _colar(db, usuario, texto):
    pedido, resultado = colagem_service.criar_com_colagem(
        db, PedidoCreate(), texto, usuario.id, usuario.perfil
    )
    return pedido, resultado


def _tsv(*linhas: str, codigo_cliente: str = "265550", data: str = "07/08/2026") -> str:
    """Monta um bloco no formato exato da planilha da cliente (com o typo e tudo)."""
    return "\n".join(
        [
            f"{data}\t\t{codigo_cliente}\t\t",
            "CODIGO\tDESCIRCAO\tQUANT.\tV. UNIT.\tSUB. TOTAL",
            *linhas,
            "\t\t\tTOTAL\tR$ 0,00",
        ]
    )


def _cliente(db, *, nome="CLIENTE DA PLANILHA", codigo=None):
    c = Cliente(nome=nome, codigo=codigo)
    db.add(c)
    db.flush()
    return c


def _lote(db, usuario, texto, **campos):
    return colagem_service.criar_lote(db, PedidoCreate(**campos), texto, usuario.id, usuario.perfil)


# ============================================================ 1. números pt-BR
@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("R$ 1.234,50", Decimal("1234.50")),
        ("2,50", Decimal("2.50")),
        ("12.5", Decimal("12.5")),  # um ponto, 1 casa -> decimal
        ("12.50", Decimal("12.50")),
        ("1.234", Decimal("1234")),  # um ponto, 3 casas -> milhar
        ("1.234.567", Decimal("1234567")),
        ("-5,00", Decimal("-5.00")),
        ("0", Decimal("0")),
        ("", None),
        ("   ", None),
        ("abc", None),
        (None, None),
        ("12,34,56", None),  # ilegível vira None, nunca um chute
    ],
)
def test_parse_decimal_br(bruto, esperado):
    assert parse_decimal_br(bruto) == esperado


def test_parse_int_br_recusa_quantidade_fracionada():
    assert parse_int_br("1.000") == 1000
    assert parse_int_br("10,00") == 10
    assert parse_int_br("10,5") is None  # arredondar aqui só apareceria no faturamento


def test_milhar_nao_vira_zero_silencioso():
    """O bug que existia no `_to_decimal` da rota: "1.234,50" caía em Decimal("0")."""
    from app.web.routes.pedidos import _to_decimal

    assert _to_decimal("1.234,50") == Decimal("1234.50")
    assert _to_decimal("xis") == Decimal("0")


# ============================================================ 2. parser do texto
def test_parser_le_a_planilha_real():
    lidas, ignoradas = parse_colagem(
        _tsv("K-708\tCANETA AZUL\t10\tR$ 2,50\tR$ 25,00", "A12\tCADERNO 96F\t3\t\t")
    )
    assert [(linha.codigo, linha.qtd, linha.preco_unit) for linha in lidas] == [
        ("K-708", 10, Decimal("2.50")),
        ("A12", 3, None),  # preço vazio -> cai no preço de tabela
    ]
    motivos = {i.motivo for i in ignoradas}
    assert motivos == {"cabeçalho da planilha", "cabeçalho da tabela", "rodapé de total"}


def test_cabecalho_com_descricao_escrita_errado_ainda_mapeia():
    """A planilha da cliente escreve "DESCIRCAO"; exigir a grafia certa quebraria tudo."""
    lidas, _ = parse_colagem(
        "CODIGO\tDESCIRCAO\tQUANT.\tV. UNIT.\nK1\tCANETA\t4\t1,50",
    )
    assert (lidas[0].descricao, lidas[0].qtd) == ("CANETA", 4)


def test_colunas_fora_de_ordem_sao_lidas_pelo_nome():
    lidas, _ = parse_colagem("QUANT.\tCODIGO\tV. UNIT.\tDESCRICAO\n7\tK9\t3,00\tCANETA")
    assert (lidas[0].codigo, lidas[0].qtd, lidas[0].preco_unit) == ("K9", 7, Decimal("3.00"))


def test_sem_cabecalho_cai_no_posicional():
    lidas, _ = parse_colagem("K1\tCANETA AZUL\t5\t2,00")
    assert (lidas[0].codigo, lidas[0].qtd, lidas[0].preco_unit) == ("K1", 5, Decimal("2.00"))


def test_separado_por_espacos_e_por_token():
    por_espaco, _ = parse_colagem("K1    CANETA AZUL    5    2,00")
    assert (por_espaco[0].codigo, por_espaco[0].qtd) == ("K1", 5)

    por_token, _ = parse_colagem("K1 CANETA AZUL 5 2,00")
    assert (por_token[0].codigo, por_token[0].descricao, por_token[0].qtd) == (
        "K1",
        "CANETA AZUL",
        5,
    )


def test_token_solto_com_subtotal_so_conta_se_a_conta_fechar():
    """ "K1 CANETA 10 2,50 25,00" tem subtotal; "K1 CANETA 2 10 2,50" não tem."""
    com_subtotal, _ = parse_colagem("K1 CANETA 10 2,50 25,00")
    assert (com_subtotal[0].qtd, com_subtotal[0].preco_unit) == (10, Decimal("2.50"))

    sem_subtotal, _ = parse_colagem("K1 CANETA 2 10 2,50")
    assert (sem_subtotal[0].descricao, sem_subtotal[0].qtd) == ("CANETA 2", 10)


def test_preco_zerado_vira_none_e_nao_item_de_graca():
    lidas, _ = parse_colagem("K1\tCANETA\t5\tR$ 0,00")
    assert lidas[0].preco_unit is None


def test_limites_viram_pendencia_e_nao_erro():
    _, ignoradas = parse_colagem("linha\n" * 900)
    assert ignoradas and ignoradas[0].numero == 0 and "limite" in ignoradas[0].motivo


def test_consolidar_soma_o_mesmo_codigo_pelo_mesmo_preco():
    lidas, _ = parse_colagem("K1\tCANETA\t6\t2,00\nK1\tCANETA\t6\t2,00\nK1\tCANETA\t1\t9,00")
    juntas = consolidar(lidas)
    assert [(linha.qtd, linha.preco_unit) for linha in juntas] == [
        (12, Decimal("2.00")),
        (1, Decimal("9.00")),
    ]


def test_normalizar_codigo_tira_pontuacao_e_acento():
    assert normalizar_codigo("k-708") == normalizar_codigo("K708") == "K708"
    assert normalizar_codigo("Ç.1 ") == "C1"


# ============================================================ 3. matching
def test_casa_por_codigo_exato_e_usa_o_preco_colado(db, usuario_vendedor):
    p = _produto(db, pouca=Decimal("10.00"))
    _, r = _colar(db, usuario_vendedor, _tsv(f"{p.codigo}\tqualquer coisa\t4\tR$ 2,50\t"))

    assert r.tudo_casou
    assert (r.aplicados[0].qtd, r.aplicados[0].preco_unit) == (4, Decimal("2.50"))
    assert r.aplicados[0].tipo_match == "codigo_exato"


def test_sem_preco_colado_cai_na_faixa_do_sistema(db, usuario_vendedor):
    varejo = _produto(db, pouca=Decimal("10.00"), muita=Decimal("8.00"), corte=10)
    atacado = _produto(db, pouca=Decimal("10.00"), muita=Decimal("8.00"), corte=10)
    _, r = _colar(
        db, usuario_vendedor, _tsv(f"{varejo.codigo}\tx\t2\t\t", f"{atacado.codigo}\tx\t20\t\t")
    )

    assert sorted(a.preco_unit for a in r.aplicados) == [Decimal("8.00"), Decimal("10.00")]


def test_linhas_repetidas_somam_antes_de_precificar(db, usuario_vendedor):
    """Duas linhas de 6 com o corte em 10 são 12 unidades — o cliente merece o atacado.

    É por isso que a consolidação acontece ANTES do matching: olhando linha a linha, as
    duas sairiam a varejo e o pedido cobraria a mais.
    """
    p = _produto(db, pouca=Decimal("10.00"), muita=Decimal("8.00"), corte=10)
    _, r = _colar(db, usuario_vendedor, _tsv(f"{p.codigo}\tx\t6\t\t", f"{p.codigo}\tx\t6\t\t"))

    assert len(r.aplicados) == 1
    assert (r.aplicados[0].qtd, r.aplicados[0].preco_unit) == (12, Decimal("8.00"))


def test_codigo_normalizado_acha_o_cadastro(db, usuario_vendedor):
    p = _produto(db, codigo=f"K-{uuid.uuid4().hex[:6].upper()}")
    sujo = p.codigo.replace("-", "").lower()
    _, r = _colar(db, usuario_vendedor, _tsv(f"{sujo}\tx\t1\t5,00\t"))

    assert r.aplicados[0].tipo_match == "codigo_normalizado"


def test_codigo_alternativo_do_fornecedor_casa(db, usuario_vendedor):
    p = _produto(db)
    alt = _codigo()
    db.add(ProdutoCodigoAlt(produto_id=p.id, codigo_alt=alt))
    db.flush()

    _, r = _colar(db, usuario_vendedor, _tsv(f"{alt}\tx\t1\t5,00\t"))
    assert r.aplicados[0].tipo_match == "codigo_alt"


def test_codigo_desconhecido_sem_descricao_vira_pendencia(db, usuario_vendedor):
    _, r = _colar(db, usuario_vendedor, _tsv("NAOEXISTE-ZZZ\t\t3\t5,00\t"))

    assert not r.aplicados
    assert r.pendencias[0].motivo == "código não encontrado"
    assert r.pendencias[0].linha == 3  # a linha da planilha, para o vendedor achar


def test_descricao_forte_resolve_quando_o_codigo_nao_existe(db, usuario_vendedor):
    """A planilha antiga costuma trazer código de outra época — sobra a descrição."""
    marca = uuid.uuid4().hex[:8].upper()
    p = _produto(db, descricao=f"GUARDA CHUVA DOBRAVEL {marca}")

    _, r = _colar(
        db, usuario_vendedor, _tsv(f"CODIGO-VELHO-9\tGUARDA CHUVA DOBRAVEL {marca}\t2\tR$ 5,00\t")
    )

    assert r.tudo_casou
    assert r.aplicados[0].codigo == p.codigo
    assert r.aplicados[0].tipo_match.startswith("descricao:")


def test_duas_descricoes_iguais_viram_duvida_em_vez_de_chute(db, usuario_vendedor):
    """Empate na similaridade não pode virar escolha silenciosa num pedido de verdade."""
    marca = uuid.uuid4().hex[:8].upper()
    _produto(db, descricao=f"CANECA TERMICA {marca}")
    _produto(db, descricao=f"CANECA TERMICA {marca}")

    _, r = _colar(db, usuario_vendedor, _tsv(f"SEM-CODIGO\tCANECA TERMICA {marca}\t1\tR$ 5,00\t"))

    assert not r.aplicados
    assert "não bateu com um produto só" in r.pendencias[0].motivo
    assert len(r.pendencias[0].sugestoes) >= 2


def test_descricao_sem_nada_parecido_diz_que_nao_achou(db, usuario_vendedor):
    _, r = _colar(
        db, usuario_vendedor, _tsv(f"SEM-CODIGO\tXQZWKJ {uuid.uuid4().hex}\t1\tR$ 5,00\t")
    )

    assert not r.aplicados
    assert r.pendencias[0].motivo == "produto não encontrado"


def test_varias_cores_sem_dizer_qual_vira_duvida_com_candidatos(db, usuario_vendedor):
    p = _produto(db, cores=("azul", "preto", "vermelho"))
    _, r = _colar(db, usuario_vendedor, _tsv(f"{p.codigo}\tsem cor aqui\t2\t5,00\t"))

    assert not r.aplicados
    assert "mais de uma cor" in r.pendencias[0].motivo
    assert len(r.pendencias[0].sugestoes) == 3  # os três botões de um clique


def test_cor_citada_na_descricao_resolve_sozinha(db, usuario_vendedor):
    p = _produto(db, cores=("azul", "preto"))
    _, r = _colar(db, usuario_vendedor, _tsv(f"{p.codigo}\tCANETA PRETO GRANDE\t2\t5,00\t"))

    assert r.tudo_casou
    assert r.aplicados[0].cor == "preto"


def test_produto_inativo_vira_pendencia_dizendo_o_porque(db, usuario_vendedor):
    p = _produto(db, ativo=False)
    _, r = _colar(db, usuario_vendedor, _tsv(f"{p.codigo}\tx\t1\t5,00\t"))

    assert "inativo" in r.pendencias[0].motivo


def test_produto_sem_preco_nao_vira_item_de_graca(db, usuario_vendedor):
    """`preco_minimo` zerado é "sem piso", então nada barraria um item a R$ 0,00."""
    p = _produto(db, pouca=Decimal("0.00"), muita=Decimal("0.00"))
    _, r = _colar(db, usuario_vendedor, _tsv(f"{p.codigo}\tx\t1\t\t"))

    assert not r.aplicados
    assert "sem preço" in r.pendencias[0].motivo


def test_quantidade_ilegivel_ou_zerada_vira_pendencia(db, usuario_vendedor):
    p = _produto(db)
    _, r = _colar(
        db, usuario_vendedor, _tsv(f"{p.codigo}\tx\t0\t5,00\t", f"{p.codigo}\tx\txx\t5,00\t")
    )

    assert not r.aplicados
    assert {p.motivo for p in r.pendencias} == {
        "quantidade zerada ou negativa",
        "quantidade ilegível",
    }


def test_preco_abaixo_do_minimo_barra_o_vendedor_e_passa_o_admin(
    db, usuario_vendedor, usuario_admin
):
    p = _produto(db, minimo=Decimal("9.00"))
    texto = _tsv(f"{p.codigo}\tx\t2\tR$ 1,00\t")

    _, do_vendedor = _colar(db, usuario_vendedor, texto)
    assert not do_vendedor.aplicados
    assert "preço mínimo" in do_vendedor.pendencias[0].motivo

    _, do_admin = _colar(db, usuario_admin, texto)
    assert do_admin.tudo_casou  # é quem define o piso; pode fechar negócio difícil


# ============================================================ 4. isolamento por linha
def test_linha_ruim_no_meio_nao_derruba_o_lote(db, usuario_vendedor):
    """O teste-chave: 3 linhas, a do meio fura o piso. As outras duas gravam."""
    bom1 = _produto(db, pouca=Decimal("10.00"))
    ruim = _produto(db, minimo=Decimal("50.00"))
    bom2 = _produto(db, pouca=Decimal("10.00"))

    pedido, r = _colar(
        db,
        usuario_vendedor,
        _tsv(
            f"{bom1.codigo}\tx\t2\tR$ 5,00\t",
            f"{ruim.codigo}\tx\t3\tR$ 1,00\t",
            f"{bom2.codigo}\tx\t1\tR$ 7,00\t",
        ),
    )

    assert len(r.aplicados) == 2
    assert len(r.pendencias) == 1
    assert len(pedido.itens) == 2
    assert pedido.total == Decimal("17.00")  # 2×5 + 1×7 — sem resquício da linha ruim


def test_a_session_continua_utilizavel_depois_da_linha_ruim(db, usuario_vendedor):
    """Rollback de SAVEPOINT não pode envenenar a transação da request inteira."""
    ruim = _produto(db, minimo=Decimal("50.00"))
    bom = _produto(db, pouca=Decimal("10.00"))

    pedido, r = _colar(db, usuario_vendedor, _tsv(f"{ruim.codigo}\tx\t1\tR$ 1,00\t"))
    assert not r.aplicados

    # Uma segunda colagem no mesmo pedido, na mesma Session, tem que funcionar.
    segunda = colagem_service.aplicar(
        db, pedido.id, _tsv(f"{bom.codigo}\tx\t2\tR$ 4,00\t"), "vendedor", usuario_vendedor.id
    )
    assert segunda.tudo_casou
    assert pedido.total == Decimal("8.00")

    # E o caminho normal de item também.
    pedido_service.confirmar(db, pedido.id, usuario_vendedor.id)
    assert pedido.status == StatusPedido.CONFIRMADO


def test_pedido_fora_de_rascunho_recusa_a_colagem(db, usuario_vendedor):
    from app.core.errors import RegraNegocioError

    p = _produto(db)
    pedido, _ = _colar(db, usuario_vendedor, _tsv(f"{p.codigo}\tx\t1\t5,00\t"))
    pedido_service.confirmar(db, pedido.id, usuario_vendedor.id)

    with pytest.raises(RegraNegocioError):
        colagem_service.aplicar(db, pedido.id, _tsv(f"{p.codigo}\tx\t1\t5,00\t"), "vendedor", 1)


def test_colagem_registra_auditoria_com_hash_e_resumo(db, usuario_vendedor):
    from sqlalchemy import select

    from app.models.auditoria import Auditoria

    p = _produto(db)
    pedido, _ = _colar(db, usuario_vendedor, _tsv(f"{p.codigo}\tx\t2\tR$ 5,00\t"))

    registro = db.scalar(
        select(Auditoria).where(
            Auditoria.entidade == "pedidos",
            Auditoria.acao == "colar_itens",
            Auditoria.entidade_id == pedido.id,
        )
    )
    assert registro is not None
    assert registro.entidade_id == pedido.id
    assert len(registro.depois["texto_sha256"]) == 64
    assert registro.depois["aplicados"][0]["match"] == "codigo_exato"
    assert "texto" not in registro.depois  # o conteúdo colado não é guardado


# ============================================================ 5. planilha do dia (lote)
def test_parse_blocos_fatia_a_planilha_do_dia():
    """Cada cabeçalho abre um pedido; a data logo acima pertence ao bloco que vem."""
    blocos = parse_blocos(
        _tsv("K1\tCANETA\t10\tR$ 2,50\t", codigo_cliente="265550", data="07/08/2026")
        + "\n"
        + _tsv(
            "A12\tCADERNO\t3\t\t", "B33\tLAPIS\t7\t\t", codigo_cliente="998877", data="08/08/2026"
        )
    )

    assert len(blocos) == 2
    assert (blocos[0].data, blocos[0].codigo_cliente) == ("07/08/2026", "265550")
    assert [linha.codigo for linha in blocos[0].linhas] == ["K1"]
    assert (blocos[1].data, blocos[1].codigo_cliente) == ("08/08/2026", "998877")
    assert [linha.codigo for linha in blocos[1].linhas] == ["A12", "B33"]


def test_parse_blocos_sem_repetir_o_cabecalho():
    """Planilha que só repete a linha de data: ela sozinha abre o pedido seguinte."""
    blocos = parse_blocos(
        "07/08/2026\t\t111\t\t\n"
        "CODIGO\tDESCIRCAO\tQUANT.\tV. UNIT.\n"
        "K1\tCANETA\t2\t3,00\n"
        "08/08/2026\t\t222\t\t\n"
        "K2\tCADERNO\t5\t4,00"
    )
    assert [b.codigo_cliente for b in blocos] == ["111", "222"]
    assert [[linha.codigo for linha in b.linhas] for b in blocos] == [["K1"], ["K2"]]


def test_bloco_unico_continua_sendo_um_bloco_so():
    blocos = parse_blocos(_tsv("K1\tCANETA\t2\t3,00\t"))
    assert len(blocos) == 1 and blocos[0].codigo_cliente == "265550"


def test_parse_colagem_achata_todos_os_blocos():
    """É o que a colagem dentro de um rascunho usa: tudo entra no pedido aberto."""
    lidas, _ = parse_colagem(_tsv("K1\tCANETA\t1\t2,00\t") + "\n" + _tsv("K2\tCADERNO\t2\t3,00\t"))
    assert [linha.codigo for linha in lidas] == ["K1", "K2"]


def test_planilha_do_dia_cria_um_pedido_por_bloco(db, usuario_vendedor):
    p1 = _produto(db, pouca=Decimal("10.00"))
    p2 = _produto(db, pouca=Decimal("10.00"))

    lote = _lote(
        db,
        usuario_vendedor,
        _tsv(f"{p1.codigo}\tx\t2\tR$ 5,00\t", codigo_cliente="111")
        + "\n"
        + _tsv(f"{p2.codigo}\tx\t3\tR$ 4,00\t", codigo_cliente="222"),
    )

    assert len(lote.pedidos) == 2
    assert lote.tudo_casou
    assert [p.total for p in lote.pedidos] == [Decimal("10.00"), Decimal("12.00")]
    assert lote.total_aplicados == 2


def test_codigo_do_bloco_amarra_o_cadastro_do_cliente(db, usuario_vendedor):
    cliente = _cliente(db, nome="LOJA DO ZÉ", codigo="265550")
    p = _produto(db)

    lote = _lote(
        db, usuario_vendedor, _tsv(f"{p.codigo}\tx\t1\tR$ 5,00\t", codigo_cliente="265550")
    )

    assert lote.pedidos[0].cliente_vinculado is True
    assert lote.pedidos[0].cliente == cliente.nome


def test_codigo_repetido_no_cadastro_nao_chuta_cliente(db, usuario_vendedor):
    """Dois cadastros com o mesmo código: melhor CONSUMIDOR do que o cliente errado."""
    _cliente(db, nome="LOJA A", codigo="777777")
    _cliente(db, nome="LOJA B", codigo="777777")
    p = _produto(db)

    lote = _lote(
        db, usuario_vendedor, _tsv(f"{p.codigo}\tx\t1\tR$ 5,00\t", codigo_cliente="777777")
    )

    assert lote.pedidos[0].cliente_vinculado is False
    assert lote.pedidos[0].cliente == "CONSUMIDOR"


def test_codigo_sem_cadastro_cai_nos_campos_da_tela(db, usuario_vendedor):
    """Os campos digitados à esquerda são o padrão de quem o código não achou."""
    p = _produto(db)

    lote = _lote(
        db,
        usuario_vendedor,
        _tsv(f"{p.codigo}\tx\t1\tR$ 5,00\t", codigo_cliente="000000"),
        cliente_nome="Maria do Balcão",
        cliente_telefone="11 97777-6666",
    )

    assert lote.pedidos[0].cliente_vinculado is False
    assert lote.pedidos[0].cliente == "Maria do Balcão"


def test_cadastro_do_bloco_vence_os_campos_da_tela(db, usuario_vendedor):
    _cliente(db, nome="LOJA DO ZÉ", codigo="265550")
    p = _produto(db)

    lote = _lote(
        db,
        usuario_vendedor,
        _tsv(f"{p.codigo}\tx\t1\tR$ 5,00\t", codigo_cliente="265550"),
        cliente_nome="Maria do Balcão",
    )

    assert lote.pedidos[0].cliente == "LOJA DO ZÉ"


def test_pendencia_num_bloco_nao_afeta_o_outro(db, usuario_vendedor):
    bom = _produto(db, pouca=Decimal("10.00"))

    lote = _lote(
        db,
        usuario_vendedor,
        _tsv("NAOEXISTE-ZZZ\t\t5\tR$ 1,00\t", codigo_cliente="111")
        + "\n"
        + _tsv(f"{bom.codigo}\tx\t2\tR$ 5,00\t", codigo_cliente="222"),
    )

    assert len(lote.pedidos) == 2
    assert lote.pedidos[0].resultado.aplicados == []
    assert len(lote.pedidos[0].resultado.pendencias) == 1
    assert len(lote.pedidos[1].resultado.aplicados) == 1
    assert lote.pedidos[1].total == Decimal("10.00")


def test_colar_a_planilha_do_dia_num_rascunho_junta_tudo(db, usuario_vendedor):
    """No rascunho o vendedor escolheu UM pedido: blocos separados entram todos nele."""
    p1 = _produto(db, pouca=Decimal("10.00"))
    p2 = _produto(db, pouca=Decimal("10.00"))
    pedido, _ = _colar(db, usuario_vendedor, _tsv(f"{p1.codigo}\tx\t1\tR$ 5,00\t"))

    r = colagem_service.aplicar(
        db,
        pedido.id,
        _tsv(f"{p1.codigo}\tx\t1\tR$ 5,00\t", codigo_cliente="111")
        + "\n"
        + _tsv(f"{p2.codigo}\tx\t2\tR$ 3,00\t", codigo_cliente="222"),
        "vendedor",
        usuario_vendedor.id,
    )

    assert len(r.aplicados) == 2
    assert pedido.total == Decimal("16.00")  # 5 (antes) + 5 + 6
