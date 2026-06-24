"""Popula pedidos/contas de demonstração para as telas não ficarem vazias nas capturas.

Cria pedidos em estados variados (rascunho, confirmado, em separação, faturado) usando os
serviços reais. Idempotente: se já existirem pedidos marcados como DEMO, não recria.

Uso: uv run python scripts/demo_dados.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models.cliente import Cliente  # noqa: E402
from app.models.enums import EstoqueModo, StatusPedido  # noqa: E402
from app.models.pedido import Pedido  # noqa: E402
from app.models.produto import ProdutoVariacao  # noqa: E402
from app.models.usuario import Usuario  # noqa: E402
from app.schemas.pedido import ItemAdicionar  # noqa: E402
from app.services.pedido_service import pedido_service  # noqa: E402

CLIENTES = [
    ("Claudemir Atacados", "30 dias"),
    ("Luciano Distribuidora", "2x"),
    ("Leonardo Comércio", "à vista"),
]


def _garantir_clientes(db) -> list[Cliente]:
    clientes = []
    for nome, cond in CLIENTES:
        cli = db.scalar(select(Cliente).where(Cliente.nome == nome))
        if cli is None:
            cli = Cliente(nome=nome, condicao_pagto_padrao=cond, ativo=True)
            db.add(cli)
            db.flush()
        clientes.append(cli)
    return clientes


def _variacoes_vendaveis(db, n: int) -> list[ProdutoVariacao]:
    """Variações EXATO com saldo folgado e produto com preço definido."""
    stmt = (
        select(ProdutoVariacao)
        .join(ProdutoVariacao.produto)
        .where(
            ProdutoVariacao.estoque_modo == EstoqueModo.EXATO,
            ProdutoVariacao.estoque_fisico >= 60,
        )
        .limit(50)
    )
    candidatas = [
        v for v in db.scalars(stmt) if v.produto.preco_pouca_qtd and v.produto.preco_pouca_qtd > 0
    ]
    return candidatas[:n]


def seed_demo() -> None:
    db = SessionLocal()
    try:
        if db.scalar(select(Pedido).where(Pedido.observacao.like("DEMO%"))):
            print("Dados de demonstração já existem — nada a fazer.")
            return

        admin = db.scalar(select(Usuario).where(Usuario.perfil == "admin"))
        vendedor = db.scalar(select(Usuario).where(Usuario.perfil == "vendedor")) or admin
        clientes = _garantir_clientes(db)
        vrs = _variacoes_vendaveis(db, 8)
        if len(vrs) < 6:
            raise SystemExit("Sem variações EXATO suficientes — rode o seed/importação antes.")

        def novo(cliente, itens, obs):
            ped = pedido_service.criar(
                db, cliente_id=cliente.id, vendedor_id=vendedor.id, observacao=obs
            )
            db.flush()
            for v, qtd in itens:
                pedido_service.adicionar_item(
                    db, ped.id, ItemAdicionar(variacao_id=v.id, qtd=qtd), perfil="vendedor"
                )
            db.flush()
            return ped

        # 1) Rascunho
        novo(clientes[0], [(vrs[0], 24), (vrs[1], 12)], "DEMO rascunho")

        # 2) Confirmado (entra na fila de separação)
        p2 = novo(clientes[1], [(vrs[2], 30), (vrs[3], 18)], "DEMO confirmado")
        pedido_service.confirmar(db, p2.id, usuario_id=vendedor.id)

        # 3) Em separação (conferência parcial)
        p3 = novo(clientes[2], [(vrs[4], 40), (vrs[5], 25)], "DEMO separacao")
        pedido_service.confirmar(db, p3.id, usuario_id=vendedor.id)
        pedido_service.iniciar_separacao(db, p3.id)
        db.refresh(p3)
        pedido_service.marcar_item_separado(db, p3.id, p3.itens[0].id, True)

        # 4) Faturado (gera contas a receber pela condição "2x" do cliente Luciano)
        p4 = novo(clientes[1], [(vrs[6 % len(vrs)], 50), (vrs[7 % len(vrs)], 35)], "DEMO faturado")
        pedido_service.confirmar(db, p4.id, usuario_id=vendedor.id)
        pedido_service.faturar(db, p4.id, usuario_id=admin.id)

        db.commit()
        total = db.scalar(select(Pedido).where(Pedido.observacao.like("DEMO%")))
        print("Demonstração criada:")
        for st in StatusPedido:
            n = len(
                list(
                    db.scalars(
                        select(Pedido).where(Pedido.observacao.like("DEMO%"), Pedido.status == st)
                    )
                )
            )
            if n:
                print(f"  {st.value}: {n}")
        print("  (ok)" if total else "")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_demo()
