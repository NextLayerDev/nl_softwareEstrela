"""Catálogo Inteligente: o documento A4 que a loja imprime ou manda em PDF."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models.enums import EstoqueModo
from app.models.produto import Produto, ProdutoFaixaPreco, ProdutoVariacao
from app.services.catalogo_service import descricao_curta


def _login(client: TestClient, perfil: str) -> None:
    r = client.post(
        "/login",
        data={"email": f"{perfil}@estrela.local", "senha": "estrela123"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


@pytest.fixture
def client_admin(db):
    """Sessão de admin com o `get_db` do teste — nada sobra no banco depois."""
    from app.deps.auth import get_current_user
    from app.deps.db import get_db
    from app.models.enums import Perfil
    from app.models.usuario import Usuario

    usuario = db.query(Usuario).filter_by(perfil=Perfil.ADMIN).first()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: usuario
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _produto(db: Session, *, publicado=True, ativo=True, cores=("azul",), **kw) -> Produto:
    p = Produto(
        codigo=f"CAT{uuid.uuid4().hex[:8].upper()}",
        descricao=kw.get("descricao", "PRODUTO DE CATALOGO"),
        preco_pouca_qtd=kw.get("pouca", Decimal("15.00")),
        preco_muita_qtd=Decimal("12.00"),
        publicar_catalogo=publicado,
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
                estoque_fisico=10,
                ativo=kw.get("variacao_ativa", True),
            )
        )
    db.flush()
    return p


# --------------------------------------------------------------- RBAC
def test_catalogo_exige_sessao() -> None:
    """CLAUDE.md §13: toda rota nova passa por RBAC."""
    r = TestClient(app).get("/catalogo/imprimir", follow_redirects=False)
    assert r.status_code in (302, 303, 401)


def test_vendedor_ve_o_catalogo() -> None:
    c = TestClient(app)
    _login(c, "vendedor")
    assert c.get("/catalogo/imprimir").status_code == 200


# --------------------------------------------------------------- conteúdo
def test_so_entra_produto_publicado_e_ativo(db: Session, client_admin) -> None:
    publicado = _produto(db, publicado=True)
    escondido = _produto(db, publicado=False)
    inativo = _produto(db, publicado=True, ativo=False)

    t = client_admin.get("/catalogo/imprimir").text
    assert publicado.codigo in t
    assert escondido.codigo not in t
    assert inativo.codigo not in t


def test_produto_sem_cor_ativa_nao_entra(db: Session, client_admin) -> None:
    """Cartão de produto que não dá para vender é propaganda enganosa."""
    sem_cor = _produto(db, variacao_ativa=False)
    assert sem_cor.codigo not in client_admin.get("/catalogo/imprimir").text


def test_documento_tem_o_formato_a4(client_admin) -> None:
    t = client_admin.get("/catalogo/imprimir").text
    assert "@page" in t and "size: A4" in t
    # grid sai em coluna única no WeasyPrint — o cartão tem que ser inline-block
    assert "inline-block" in t
    assert "display: grid" not in t


def test_preco_do_cartao_respeita_a_tabela_de_faixas(db: Session, client_admin) -> None:
    """Passa pelo `sugerir_preco`, então a tabela manda aqui como manda no pedido."""
    p = _produto(db, pouca=Decimal("15.00"))
    p.faixas.append(ProdutoFaixaPreco(min_qtd=1, preco=Decimal("9.90")))
    db.flush()

    t = client_admin.get("/catalogo/imprimir").text
    assert "R$ 9,90" in t
    assert "R$ 15,00" not in t


def test_produto_sem_preco_sai_sob_consulta(db: Session, client_admin) -> None:
    _produto(db, pouca=Decimal("0"))
    assert "Sob consulta" in client_admin.get("/catalogo/imprimir").text


def test_cores_limitadas_a_seis_com_mais_n(db: Session, client_admin) -> None:
    _produto(db, cores=[f"cor{i}" for i in range(9)])
    t = client_admin.get("/catalogo/imprimir").text
    assert "+3" in t


def test_estado_vazio_explica_o_porque(client_admin, db: Session) -> None:
    """`publicar_catalogo` nasce desmarcado — sem isto o recurso parece quebrado."""
    db.query(Produto).update({Produto.publicar_catalogo: False})
    db.flush()

    t = client_admin.get("/catalogo/imprimir").text
    assert "Nenhum produto publicado" in t
    assert "Publicar no catálogo" in t


# --------------------------------------------------------------- descrição curta
@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("", ""),
        # Primeira frase com corpo (>= 40 caracteres): ela basta.
        (
            "Garrafa térmica de 500 ml com tampa rosqueável. Mantém quente por 12 horas.",
            "Garrafa térmica de 500 ml com tampa rosqueável.",
        ),
        # Primeira frase curta demais não descreve nada: vale o texto inteiro.
        (
            "Azul. Garrafa térmica de 500 ml com tampa.",
            "Azul. Garrafa térmica de 500 ml com tampa.",
        ),
    ],
)
def test_descricao_curta(entrada, esperado) -> None:
    assert descricao_curta(entrada) == esperado


def test_descricao_curta_corta_na_palavra() -> None:
    longa = "palavra " * 40
    saida = descricao_curta(longa)
    assert len(saida) <= 121 and saida.endswith("…")
    assert not saida.rstrip("…").endswith("palavr")


# --------------------------------------------------------------- PDF
def test_pdf_sai_como_pdf(client_admin) -> None:
    """Pulado onde o WeasyPrint não tem as libs nativas (Pango/Cairo)."""
    r = client_admin.get("/catalogo/imprimir.pdf")
    if r.status_code == 503:
        # É o caminho protegido: sem Pango/Cairo a ROTA cai, o app não. Vale como
        # asserção — a mensagem tem que mandar a pessoa para o Imprimir.
        assert "Imprimir" in r.text
        pytest.skip("WeasyPrint sem bibliotecas nativas nesta máquina")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"
