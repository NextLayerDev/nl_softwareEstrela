"""Testes HTTP da colagem: as duas telas, o contrato OOB e a regressão do #bloco-itens.

Usam o mesmo truque do `client_vendedor` do `test_pedidos.py` — `get_db` sobrescrito
pela Session do fixture — para que o teste crie os próprios produtos e nada sobre no
banco de dev depois da rodada.
"""

from __future__ import annotations

import json
import re
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.models.cliente import Cliente
from app.models.enums import EstoqueModo
from app.models.produto import Produto, ProdutoVariacao


@pytest.fixture
def client_admin(db, usuario_admin):
    from app.deps.auth import get_current_user
    from app.deps.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: usuario_admin
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def produto(db):
    """Produto de uma cor só e sem piso: a colagem resolve sozinha, sem virar dúvida."""
    p = Produto(
        codigo=f"WEB{uuid.uuid4().hex[:8].upper()}",
        descricao="PRODUTO DE COLAGEM WEB",
        preco_pouca_qtd=Decimal("10.00"),
        preco_muita_qtd=Decimal("8.00"),
        preco_minimo=Decimal("0.00"),
    )
    db.add(p)
    db.flush()
    db.add(
        ProdutoVariacao(
            produto_id=p.id,
            cor="azul",
            estoque_modo=EstoqueModo.EXATO,
            estoque_fisico=1000,
            estoque_reservado=0,
        )
    )
    db.flush()
    return p


def _tsv(*linhas: str) -> str:
    return "\n".join(
        [
            "07/08/2026\t\t265550\t\t",
            "CODIGO\tDESCIRCAO\tQUANT.\tV. UNIT.\tSUB. TOTAL",
            *linhas,
            "\t\t\tTOTAL\tR$ 0,00",
        ]
    )


def _colar(client, texto: str):
    return client.post("/pedidos/colar", data={"texto": texto}, headers={"HX-Request": "true"})


# --------------------------------------------------------------------- telas
def test_lista_de_pedidos_mostra_o_painel_da_planilha_do_dia(client_admin) -> None:
    """O painel que cria VÁRIOS pedidos mora na lista, não na tela de novo pedido.

    As duas colagens fazem coisas diferentes: esta fatia a planilha do dia em um pedido
    por bloco (trabalho de fechamento), enquanto a aba "Colar itens" da tela de novo
    pedido enche o carrinho de UM pedido que está sendo montado. Juntas na mesma tela,
    o vendedor clicava em uma achando que era a outra.
    """
    t = client_admin.get("/pedidos").text
    assert 'id="painel-colagem"' in t
    assert 'hx-post="/pedidos/colar"' in t


def test_novo_pedido_mostra_a_aba_de_colar_no_carrinho(client_admin) -> None:
    t = client_admin.get("/pedidos/novo").text
    assert "Colar itens" in t
    assert 'name="itens_json"' in t  # o carrinho vai inteiro num POST só
    assert "pedido_novo.js" in t  # quem resolve a colagem no carrinho
    # o painel que cria vários pedidos NÃO aparece aqui
    assert 'id="painel-colagem"' not in t


def test_colagem_que_casa_tudo_manda_direto_para_o_pedido(client_admin, produto) -> None:
    r = _colar(client_admin, _tsv(f"{produto.codigo}\tqualquer\t2\tR$ 9,00\t"))

    assert r.status_code == 200  # nunca 4xx: o htmx descarta corpo de resposta de erro
    destino = r.headers.get("HX-Redirect", "")
    assert re.fullmatch(r"/pedidos/\d+", destino), r.text

    detalhe = client_admin.get(destino).text
    assert "R$ 18,00" in detalhe  # 2 × 9,00 — o preço colado é o que vale


def test_colagem_com_pendencia_para_e_mostra_o_que_faltou(client_admin, produto) -> None:
    r = _colar(
        client_admin,
        _tsv(f"{produto.codigo}\tx\t1\tR$ 9,00\t", "CODIGO-QUE-NAO-EXISTE\t\t3\tR$ 1,00\t"),
    )

    assert r.status_code == 200
    assert "HX-Redirect" not in r.headers  # tem o que conferir: não redireciona
    assert "1 linha não entrou" in r.text
    assert "código não encontrado" in r.text
    assert "Abrir pedido #" in r.text
    assert "1 linha virou item" in r.text


def test_colagem_vazia_nao_estoura_e_explica(client_admin) -> None:
    r = _colar(client_admin, "   ")
    assert r.status_code == 200
    assert "Não encontrei linhas de produto" in r.text


def test_cliente_digitado_ao_lado_vale_no_pedido_colado(client_admin, produto) -> None:
    """O `hx-include` do painel manda os campos de cliente junto com o texto."""
    r = client_admin.post(
        "/pedidos/colar",
        data={
            "texto": _tsv(f"{produto.codigo}\tx\t1\tR$ 9,00\t"),
            "cliente_nome": "Maria do Balcão",
            "cliente_telefone": "11 97777-6666",
        },
        headers={"HX-Request": "true"},
    )
    detalhe = client_admin.get(r.headers["HX-Redirect"]).text
    assert "Maria do Balcão" in detalhe


def test_colar_dentro_do_rascunho_responde_tudo_oob(client_admin, produto) -> None:
    criado = _colar(client_admin, _tsv(f"{produto.codigo}\tx\t1\tR$ 9,00\t"))
    pedido_url = criado.headers["HX-Redirect"]

    assert f'hx-post="{pedido_url}/colar"' in client_admin.get(pedido_url).text

    r = client_admin.post(
        f"{pedido_url}/colar",
        data={"texto": _tsv(f"{produto.codigo}\tx\t2\tR$ 9,00\t")},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    # o form usa hx-swap="none", então TUDO na resposta precisa vir OOB
    assert 'id="painel-colagem" hx-swap-oob="true"' in r.text
    assert 'id="bloco-itens" hx-swap-oob="true"' in r.text
    assert 'id="acoes-pedido"' in r.text
    assert "R$ 27,00" in r.text  # 1×9 + 2×9, somados na mesma linha


def test_painel_de_colagem_pode_ser_recarregado_vazio(client_admin, produto) -> None:
    criado = _colar(client_admin, _tsv(f"{produto.codigo}\tx\t1\tR$ 9,00\t"))
    pedido_id = criado.headers["HX-Redirect"].rsplit("/", 1)[1]

    r = client_admin.get(f"/pedidos/{pedido_id}/colagem", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert 'id="painel-colagem"' in r.text
    assert f'hx-post="/pedidos/{pedido_id}/colar"' in r.text


# --------------------------------------------------------------------- regressão
def test_rotas_antigas_de_item_nao_marcam_o_bloco_como_oob(client_admin, produto) -> None:
    """A flag `oob_bloco` é separada da `oob` por um motivo.

    As rotas de item/desconto trocam o #bloco-itens pelo hx-target (`outerHTML`), e o
    htmx REMOVE do conteúdo qualquer nó marcado como OOB antes do swap principal —
    marcá-lo ali apagaria a tabela da tela.
    """
    criado = _colar(client_admin, _tsv(f"{produto.codigo}\tx\t1\tR$ 9,00\t"))
    pedido_url = criado.headers["HX-Redirect"]

    r = client_admin.post(
        f"{pedido_url}/desconto", data={"desconto": "1,00"}, headers={"HX-Request": "true"}
    )
    assert r.status_code == 200
    assert 'id="bloco-itens"' in r.text
    assert 'id="bloco-itens" hx-swap-oob' not in r.text
    # As ações e o resumo continuam vindo OOB na mesma resposta.
    assert 'id="acoes-pedido" hx-swap-oob="true"' in r.text
    assert 'id="bloco-resumo" hx-swap-oob="true"' in r.text


# --------------------------------------------------------------------- planilha do dia
@pytest.fixture
def outro_produto(db):
    p = Produto(
        codigo=f"WEB{uuid.uuid4().hex[:8].upper()}",
        descricao="SEGUNDO PRODUTO DE COLAGEM WEB",
        preco_pouca_qtd=Decimal("10.00"),
        preco_muita_qtd=Decimal("8.00"),
        preco_minimo=Decimal("0.00"),
    )
    db.add(p)
    db.flush()
    db.add(
        ProdutoVariacao(
            produto_id=p.id,
            cor="preto",
            estoque_modo=EstoqueModo.EXATO,
            estoque_fisico=1000,
            estoque_reservado=0,
        )
    )
    db.flush()
    return p


def _bloco(*linhas: str, codigo_cliente: str, data: str = "07/08/2026") -> str:
    return "\n".join(
        [
            f"{data}\t\t{codigo_cliente}\t\t",
            "CODIGO\tDESCIRCAO\tQUANT.\tV. UNIT.\tSUB. TOTAL",
            *linhas,
            "\t\t\tTOTAL\tR$ 0,00",
        ]
    )


def test_planilha_do_dia_cria_um_pedido_por_bloco(client_admin, produto, outro_produto) -> None:
    r = _colar(
        client_admin,
        _bloco(f"{produto.codigo}\tx\t2\tR$ 9,00\t", codigo_cliente="111")
        + "\n"
        + _bloco(f"{outro_produto.codigo}\tx\t3\tR$ 4,00\t", codigo_cliente="222"),
    )

    assert r.status_code == 200
    # Não dá para redirecionar para dois pedidos: a tela de chegada é o resumo do lote.
    assert "HX-Redirect" not in r.headers
    assert "2 pedidos criados" in r.text
    assert "R$ 18,00" in r.text
    assert "R$ 12,00" in r.text
    assert r.text.count("Abrir pedido") == 2


def test_bloco_com_codigo_conhecido_mostra_o_cadastro(client_admin, db, produto, outro_produto):
    db.add(Cliente(nome="LOJA DO ZE", codigo="265550"))
    db.flush()

    r = _colar(
        client_admin,
        _bloco(f"{produto.codigo}\tx\t1\tR$ 9,00\t", codigo_cliente="265550")
        + "\n"
        + _bloco(f"{outro_produto.codigo}\tx\t1\tR$ 9,00\t", codigo_cliente="000000"),
    )

    assert "LOJA DO ZE" in r.text
    assert "cadastro" in r.text
    assert "código 000000 não achado" in r.text


def test_um_bloco_so_continua_redirecionando(client_admin, produto) -> None:
    """A planilha de um pedido só não pode ganhar uma tela de resumo no caminho."""
    r = _colar(client_admin, _bloco(f"{produto.codigo}\tx\t2\tR$ 9,00\t", codigo_cliente="111"))
    assert re.fullmatch(r"/pedidos/\d+", r.headers.get("HX-Redirect", ""))


# =================================================== tela única: POST /pedidos + carrinho
def _variacao_de(db, produto):
    from app.models.produto import ProdutoVariacao

    return db.query(ProdutoVariacao).filter_by(produto_id=produto.id).first()


def test_post_pedidos_cria_pedido_inteiro_com_catalogo_e_avulso(client_admin, db, produto):
    """O contrato da tela nova: carrinho em JSON num campo só, um POST, um pedido."""
    var = _variacao_de(db, produto)
    itens = json.dumps(
        [
            {"tipo": "catalogo", "variacao_id": var.id, "qtd": 2, "preco_unit": None},
            {
                "tipo": "avulso",
                "nome": "CANECA",
                "codigo": "AV-9",
                "detalhe": "",
                "qtd": 1,
                "preco_unit": "15.00",
            },
        ]
    )

    r = client_admin.post(
        "/pedidos",
        data={
            "cliente_id": "",
            "cliente_nome": "Joana",
            "cliente_telefone": "",
            "observacao": "entregar depois das 18h",
            "desconto_total": "5,00",
            "itens_json": itens,
        },
        follow_redirects=False,
    )

    assert r.status_code == 303
    destino = r.headers["location"]
    assert re.fullmatch(r"/pedidos/\d+", destino)

    detalhe = client_admin.get(destino).text
    assert "CANECA" in detalhe
    assert "avulso" in detalhe
    # 2×10 + 1×15 = 35, menos 5 de desconto
    assert "R$ 30,00" in detalhe


def test_post_pedidos_sem_itens_nao_cria_rascunho_vazio(client_admin):
    """A tela nova não abre mais rascunho vazio — pedido sem item não é pedido."""
    r = client_admin.post(
        "/pedidos",
        data={"cliente_nome": "Ninguém", "itens_json": "[]"},
        follow_redirects=False,
    )
    assert r.status_code != 303
    assert "ao menos um item" in r.text.lower()


def test_resolver_colagem_devolve_as_linhas_sem_gravar(client_admin, db, produto):
    """Casa com o catálogo e devolve JSON — o carrinho ainda vive no navegador."""
    from app.models.pedido import Pedido

    antes = db.query(Pedido).count()
    r = client_admin.post(
        "/pedidos/resolver-colagem",
        data={"texto": _tsv(f"{produto.codigo}\tqualquer\t4\tR$ 9,00\t")},
    )

    assert r.status_code == 200
    linhas = r.json()["linhas"]
    assert len(linhas) == 1
    assert linhas[0]["variacao_id"] == _variacao_de(db, produto).id
    assert linhas[0]["qtd"] == 4
    assert Decimal(linhas[0]["preco_unit"]) == Decimal("9.00")
    assert db.query(Pedido).count() == antes  # nada foi gravado


def test_resolver_colagem_devolve_linha_nao_casada_para_virar_avulso(client_admin):
    """O que não achou produto não some: volta sem variação, com código e descrição."""
    r = client_admin.post(
        "/pedidos/resolver-colagem",
        data={"texto": _tsv("CODIGOQUENAOEXISTE99\tCOISA ESTRANHA\t2\tR$ 4,00\t")},
    )

    assert r.status_code == 200
    linha = r.json()["linhas"][0]
    assert linha["variacao_id"] is None
    assert linha["codigo"] == "CODIGOQUENAOEXISTE99"
    assert linha["qtd"] == 2
    assert Decimal(linha["preco_unit"]) == Decimal("4.00")


def test_item_avulso_no_rascunho_aberto(client_admin, db, produto):
    """A mesma porta existe no detalhe, para os dois caminhos não divergirem."""
    var = _variacao_de(db, produto)
    r = client_admin.post(
        "/pedidos",
        data={
            "itens_json": json.dumps(
                [{"tipo": "catalogo", "variacao_id": var.id, "qtd": 1, "preco_unit": None}]
            )
        },
        follow_redirects=False,
    )
    pedido_id = r.headers["location"].rsplit("/", 1)[1]

    r = client_admin.post(
        f"/pedidos/{pedido_id}/itens-avulsos",
        data={"nome": "TAXA DE GRAVACAO", "qtd": "2", "preco_unit": "7,50"},
        headers={"HX-Request": "true"},
    )

    assert r.status_code == 200
    assert "TAXA DE GRAVACAO" in r.text
    assert "R$ 25,00" in r.text  # 1×10 + 2×7,50


# =================================================== detalhe do pedido (padrão omni)
def _pedido_com_item(client_admin, db, produto):
    var = _variacao_de(db, produto)
    r = client_admin.post(
        "/pedidos",
        data={
            "cliente_nome": "Maria",
            "itens_json": json.dumps(
                [{"tipo": "catalogo", "variacao_id": var.id, "qtd": 3, "preco_unit": "15.50"}]
            ),
        },
        follow_redirects=False,
    )
    return r.headers["location"]


def test_detalhe_mostra_o_selo_de_status_no_cabecalho(client_admin, db, produto):
    """O status é a primeira coisa que se quer saber ao abrir um pedido."""
    t = client_admin.get(_pedido_com_item(client_admin, db, produto)).text
    cabecalho = t.split("<h1", 1)[1].split("</h1>", 1)[0]
    assert 'id="status-pedido"' in cabecalho
    assert "selo-rascunho" in cabecalho


def test_detalhe_mostra_a_planilha_do_resumo_sempre(client_admin, db, produto):
    """No omni ela fica visível o tempo todo, sem clicar em "gerar"."""
    t = client_admin.get(_pedido_com_item(client_admin, db, produto)).text
    assert 'id="bloco-resumo"' in t
    assert "Resumo do pedido" in t
    assert "SUB. TOTAL" in t  # o cabeçalho amarelo da planilha
    # e os dados prontos para o <canvas>, em camelCase e centavos
    assert '"precoCentavos":1550' in t
    assert "resumo_pedido.js" in t


def test_detalhe_traz_os_tres_totais_e_a_observacao(client_admin, db, produto):
    t = client_admin.get(_pedido_com_item(client_admin, db, produto)).text
    assert "Subtotal dos itens" in t
    assert "Desconto total" in t
    # o desconto grava no blur, sem botão "Aplicar"
    assert 'hx-trigger="blur changed"' in t
    assert 'id="observacao-pedido"' in t


def test_detalhe_tem_as_tres_portas_de_adicionar_item(client_admin, db, produto):
    """As mesmas três abas de /pedidos/novo, para os dois caminhos não divergirem."""
    t = client_admin.get(_pedido_com_item(client_admin, db, produto)).text
    assert "Do catálogo" in t and "Item avulso" in t and "Colar itens" in t
    assert 'id="painel-colagem"' in t  # a colagem virou aba, não card solto


def test_adicionar_item_atualiza_o_resumo_na_mesma_resposta(client_admin, db, produto):
    """Uma planilha que só atualiza na próxima navegação é uma planilha que mente."""
    url = _pedido_com_item(client_admin, db, produto)
    pedido_id = url.rsplit("/", 1)[1]

    r = client_admin.post(
        f"/pedidos/{pedido_id}/itens-avulsos",
        data={"nome": "FRETE", "qtd": "1", "preco_unit": "20,00"},
        headers={"HX-Request": "true"},
    )

    assert r.status_code == 200
    assert 'id="bloco-resumo" hx-swap-oob="true"' in r.text
    assert "FRETE" in r.text
    assert '"totalCentavos":6650' in r.text  # 3×15,50 + 20,00


def test_observacao_grava_pelo_blur(client_admin, db, produto):
    url = _pedido_com_item(client_admin, db, produto)
    pedido_id = url.rsplit("/", 1)[1]

    r = client_admin.post(
        f"/pedidos/{pedido_id}/observacao",
        data={"observacao": "entregar após as 18h"},
        headers={"HX-Request": "true"},
    )

    assert r.status_code == 200
    assert "entregar após as 18h" in client_admin.get(url).text
