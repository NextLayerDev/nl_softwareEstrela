"""Testes de relatórios: vendas, curva ABC, valorização e export XLSX."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models.cliente import Cliente
from app.models.enums import EstoqueModo, StatusPedido
from app.models.pedido import Pedido, PedidoItem
from app.models.produto import Produto, ProdutoVariacao
from app.models.usuario import Usuario
from app.services.relatorio_service import relatorio_service

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _login(perfil: str) -> TestClient:
    c = TestClient(app)
    resp = c.post(
        "/login",
        data={"email": f"{perfil}@estrela.local", "senha": "estrela123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    return c


def _cod() -> str:
    return f"R{uuid.uuid4().hex[:10].upper()}"


def _produto(db: Session, *, custo: str = "0", desc: str = "Produto Rel") -> Produto:
    p = Produto(codigo=_cod(), descricao=desc, preco_custo=Decimal(custo))
    db.add(p)
    db.flush()
    return p


def _variacao(db: Session, produto: Produto, *, fisico: int = 0) -> ProdutoVariacao:
    v = ProdutoVariacao(
        produto_id=produto.id,
        cor="Azul",
        estoque_modo=EstoqueModo.EXATO,
        estoque_fisico=fisico,
    )
    db.add(v)
    db.flush()
    return v


def _pedido_faturado(
    db: Session, vendedor: Usuario, variacao: ProdutoVariacao, *, qtd: int, preco: str
) -> Pedido:
    cliente = Cliente(nome=f"Cli {uuid.uuid4().hex[:6]}")
    db.add(cliente)
    db.flush()
    subtotal = Decimal(preco) * qtd
    pedido = Pedido(
        cliente_id=cliente.id,
        vendedor_id=vendedor.id,
        status=StatusPedido.FATURADO,
        total=subtotal,
        faturado_em=datetime.now(UTC),
    )
    db.add(pedido)
    db.flush()
    db.add(
        PedidoItem(
            pedido_id=pedido.id,
            produto_variacao_id=variacao.id,
            qtd=qtd,
            preco_unit=Decimal(preco),
            subtotal=subtotal,
        )
    )
    db.flush()
    return pedido


def test_vendas_soma_pedidos_faturados(db: Session, usuario_vendedor: Usuario) -> None:
    p = _produto(db)
    v = _variacao(db, p)
    _pedido_faturado(db, usuario_vendedor, v, qtd=2, preco="50.00")
    _pedido_faturado(db, usuario_vendedor, v, qtd=1, preco="30.00")
    dados = relatorio_service.vendas(db, vendedor_id=usuario_vendedor.id)
    assert dados["qtd_pedidos"] == 2
    assert dados["total"] == Decimal("130.00")


def test_curva_abc_classifica(db: Session, usuario_vendedor: Usuario) -> None:
    # Produto A domina o valor (deve cair em A); produtos pequenos viram B/C.
    grande = _produto(db, desc="Grande")
    vg = _variacao(db, grande)
    _pedido_faturado(db, usuario_vendedor, vg, qtd=1, preco="900.00")

    medio = _produto(db, desc="Medio")
    vm = _variacao(db, medio)
    _pedido_faturado(db, usuario_vendedor, vm, qtd=1, preco="80.00")

    pequeno = _produto(db, desc="Pequeno")
    vp = _variacao(db, pequeno)
    _pedido_faturado(db, usuario_vendedor, vp, qtd=1, preco="20.00")

    dados = relatorio_service.curva_abc(db)
    por_id = {linha["produto_id"]: linha for linha in dados["linhas"]}
    assert por_id[grande.id]["classe"] == "A"
    classes = {linha["classe"] for linha in dados["linhas"]}
    assert "A" in classes
    # Há ao menos uma classe diferente de A (B ou C).
    assert classes - {"A"}


def test_valorizacao_soma_fisico_x_custo(db: Session) -> None:
    p1 = _produto(db, custo="10.00")
    _variacao(db, p1, fisico=5)  # 50
    p2 = _produto(db, custo="2.50")
    _variacao(db, p2, fisico=4)  # 10
    dados = relatorio_service.valorizacao(db)
    por_id = {linha["produto_id"]: linha for linha in dados["linhas"]}
    assert por_id[p1.id]["valor"] == Decimal("50.00")
    assert por_id[p2.id]["valor"] == Decimal("10.00")
    assert dados["total"] >= Decimal("60.00")


def test_export_xlsx_retorna_bytes_validos(db: Session, usuario_vendedor: Usuario) -> None:
    p = _produto(db, custo="3.00")
    v = _variacao(db, p, fisico=10)
    _pedido_faturado(db, usuario_vendedor, v, qtd=2, preco="40.00")
    for conteudo in (
        relatorio_service.vendas_xlsx(db),
        relatorio_service.curva_abc_xlsx(db),
        relatorio_service.valorizacao_xlsx(db),
    ):
        assert isinstance(conteudo, bytes)
        assert conteudo[:2] == b"PK"  # assinatura zip do xlsx


# ---------------- RBAC ----------------
def test_export_endpoint_content_type() -> None:
    resp = _login("financeiro").get("/relatorios/valorizacao/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == XLSX_MEDIA
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content[:2] == b"PK"


def test_vendedor_nao_ve_valorizacao() -> None:
    resp = _login("vendedor").get("/relatorios/valorizacao", follow_redirects=False)
    assert resp.status_code == 403
    resp_exp = _login("vendedor").get("/relatorios/valorizacao/export", follow_redirects=False)
    assert resp_exp.status_code == 403


def test_funcionario_nao_acessa_relatorios() -> None:
    resp = _login("funcionario").get("/relatorios", follow_redirects=False)
    assert resp.status_code == 403


def test_vendedor_acessa_vendas() -> None:
    assert _login("vendedor").get("/relatorios/vendas").status_code == 200
