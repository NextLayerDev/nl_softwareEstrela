"""Testes da tela PÚBLICA de orçamento.

O que estes testes protegem é a decisão que torna a tela pública aceitável: ela lê
pouco e não escreve nada. Se alguém um dia acrescentar preço de custo, preço mínimo,
saldo ou POST nessa rota, é aqui que quebra.

Os produtos são criados numa sessão PRÓPRIA e commitados: o TestClient abre a sessão
dele pelo `get_db`, então dado preso na transação do fixture `db` não existiria para
a rota. Cada teste limpa o que criou.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.enums import EstoqueModo
from app.models.produto import Produto, ProdutoVariacao

# Valores plantados para servirem de sonda: o de venda TEM que aparecer, os outros dois
# não podem aparecer de jeito nenhum.
PRECO_VENDA = Decimal("12.34")
PRECO_CUSTO = Decimal("5.55")
PRECO_MINIMO = Decimal("7.77")


@contextmanager
def produto_publicado(*, ativo: bool = True, variacao_ativa: bool = True) -> Iterator[Produto]:
    """Cria produto + variação commitados e remove no final."""
    db = SessionLocal()
    produto = Produto(
        codigo=f"ORC{uuid.uuid4().hex[:8].upper()}",
        descricao=f"TESTEORCAMENTO {uuid.uuid4().hex[:6].upper()}",
        preco_pouca_qtd=PRECO_VENDA,
        preco_muita_qtd=Decimal("9.00"),
        preco_custo=PRECO_CUSTO,
        preco_minimo=PRECO_MINIMO,
        ativo=ativo,
    )
    db.add(produto)
    db.flush()
    variacao = ProdutoVariacao(
        produto_id=produto.id,
        cor="VERDE",
        estoque_modo=EstoqueModo.EXATO,
        estoque_fisico=42,
        ativo=variacao_ativa,
    )
    db.add(variacao)
    db.commit()
    db.refresh(produto)
    try:
        yield produto
    finally:
        limpeza = SessionLocal()
        try:
            alvo = limpeza.get(Produto, produto.id)
            if alvo is not None:
                limpeza.delete(alvo)
                limpeza.commit()
        finally:
            limpeza.close()
        db.close()


def _sem_ids(html: str) -> str:
    """Tira `data-id` e `data-indice` antes de procurar números sensíveis no HTML.

    Sem isso um id de linha como 5551 faria a sonda do preço de custo (555) acusar
    vazamento que não existe — e, pior, esconderia um vazamento real atrás do ruído.
    """
    return re.sub(r'data-(id|indice)="\d+"', "", html)


def test_tela_abre_sem_sessao() -> None:
    """É a única tela de operação sem login — se isso mudar, é decisão de produto."""
    resp = TestClient(app).get("/orcamento", follow_redirects=False)
    assert resp.status_code == 200
    assert "Orçamento" in resp.text


def test_busca_abre_sem_sessao() -> None:
    resp = TestClient(app).get("/orcamento/busca?q=ca", follow_redirects=False)
    assert resp.status_code == 200


def test_busca_encontra_por_codigo_e_por_descricao() -> None:
    with produto_publicado() as produto:
        client = TestClient(app)
        por_codigo = client.get(f"/orcamento/busca?q={produto.codigo}").text
        assert f'data-codigo="{produto.codigo}"' in por_codigo

        por_descricao = client.get(f"/orcamento/busca?q={produto.descricao}").text
        assert f'data-codigo="{produto.codigo}"' in por_descricao


def test_busca_devolve_o_preco_em_centavos() -> None:
    """A tela soma em inteiros; texto formatado obrigaria a converter de volta."""
    with produto_publicado() as produto:
        html = TestClient(app).get(f"/orcamento/busca?q={produto.codigo}").text
        assert 'data-preco="1234"' in html


def test_busca_nao_vaza_custo_nem_piso() -> None:
    """O piso é onde a margem acaba: numa tela pública ele não pode aparecer."""
    with produto_publicado() as produto:
        html = _sem_ids(TestClient(app).get(f"/orcamento/busca?q={produto.codigo}").text)
        assert "555" not in html, "preço de custo vazou"
        assert "777" not in html, "preço mínimo vazou"


def test_busca_ignora_produto_inativo() -> None:
    with produto_publicado(ativo=False) as produto:
        html = TestClient(app).get(f"/orcamento/busca?q={produto.codigo}").text
        assert "data-codigo=" not in html


def test_busca_ignora_variacao_inativa() -> None:
    with produto_publicado(variacao_ativa=False) as produto:
        html = TestClient(app).get(f"/orcamento/busca?q={produto.codigo}").text
        assert "data-codigo=" not in html


def test_termo_curto_nao_devolve_nada() -> None:
    """Um caractere devolveria o catálogo inteiro a cada tecla."""
    html = TestClient(app).get("/orcamento/busca?q=a").text
    assert "data-codigo=" not in html


def test_busca_respeita_o_teto_de_sugestoes() -> None:
    """Lista curta é requisito de uso, não de performance: no balcão não se rola."""
    html = TestClient(app).get("/orcamento/busca?q=ca").text
    assert html.count("data-codigo=") <= 10


def test_rota_nao_aceita_escrita() -> None:
    """A tela é somente-leitura. Um POST aqui seria escrita anônima no sistema."""
    client = TestClient(app)
    assert client.post("/orcamento", follow_redirects=False).status_code == 405
    assert client.post("/orcamento/busca", follow_redirects=False).status_code == 405


def test_busca_publica_respeita_a_tabela_de_faixas() -> None:
    """A tela de orçamento é PÚBLICA e chama `sugerir_preco` — as faixas valem aqui também.

    É o consumidor que quase ninguém lista quando mexe na regra de preço: não tem
    `Depends` de autenticação, e cota pela faixa de 1 un (a tela é por unidade). Cotar
    pelo `preco_pouca_qtd` enquanto o pedido cobra pela tabela seria o balcão prometer
    um preço e o sistema cobrar outro.
    """
    from app.models.produto import ProdutoFaixaPreco

    with produto_publicado() as produto:
        db = SessionLocal()
        try:
            alvo = db.get(Produto, produto.id)
            alvo.faixas.append(ProdutoFaixaPreco(min_qtd=1, preco=Decimal("7.77")))
            alvo.faixas.append(ProdutoFaixaPreco(min_qtd=10, preco=Decimal("5.00")))
            db.commit()
        finally:
            db.close()

        html = TestClient(app).get(f"/orcamento/busca?q={produto.codigo}").text
        assert 'data-preco="777"' in html, "a tela pública ignorou a tabela"
