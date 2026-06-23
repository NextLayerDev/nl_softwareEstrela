"""Popula dados de desenvolvimento: usuários (1 por perfil), 7 categorias e alguns produtos
com variações/preços/localização espelhando a planilha real. Idempotente.

Uso: uv run python scripts/seed.py
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.core.security import hash_senha
from app.models.categoria import Categoria
from app.models.cliente import Cliente
from app.models.enums import EstoqueModo, Perfil, RotuloAprox
from app.models.produto import Produto, ProdutoVariacao
from app.models.usuario import Usuario

CATEGORIAS = [
    "Canetas Plásticas",
    "Canetas Metálicas",
    "Copos e Garrafas",
    "Blocos e Cadernos",
    "Chaveiros",
    "Eletrônicos",
    "Outros",
]

USUARIOS = [
    ("Admin", "admin@estrela.local", Perfil.ADMIN),
    ("Vendedor", "vendedor@estrela.local", Perfil.VENDEDOR),
    ("Financeiro", "financeiro@estrela.local", Perfil.FINANCEIRO),
    ("Funcionário", "funcionario@estrela.local", Perfil.FUNCIONARIO),
]


def _get_or_create_categoria(db, nome: str) -> Categoria:
    cat = db.scalar(select(Categoria).where(Categoria.nome == nome))
    if cat is None:
        cat = Categoria(nome=nome)
        db.add(cat)
        db.flush()
    return cat


def seed() -> None:
    db = SessionLocal()
    try:
        # Usuários (senha padrão de dev: "estrela123")
        for nome, email, perfil in USUARIOS:
            if not db.scalar(select(Usuario).where(Usuario.email == email)):
                db.add(
                    Usuario(
                        nome=nome,
                        email=email,
                        senha_hash=hash_senha("estrela123"),
                        perfil=perfil.value,
                    )
                )

        cats = {nome: _get_or_create_categoria(db, nome) for nome in CATEGORIAS}

        # Cliente de exemplo
        if not db.scalar(select(Cliente).where(Cliente.nome == "Claudemir")):
            db.add(Cliente(nome="Claudemir", condicao_pagto_padrao="30 dias"))

        # Produtos espelhando a planilha (catálogo por blocos)
        produtos = [
            {
                "codigo": "K708",
                "descricao": "CANETA ESFEROGRÁFICA COM SUPORTE",
                "categoria": "Canetas Plásticas",
                "localizacao": "1º andar, lado esquerdo",
                "unidades_por_caixa": 2000,
                "preco_pouca_qtd": "1.20",
                "preco_muita_qtd": "1.00",
                "variacoes": [
                    ("BRANCO", EstoqueModo.EXATO, 156, None),
                    ("PRETO", EstoqueModo.EXATO, 212, None),
                    ("VERDE", EstoqueModo.EXATO, 42, None),
                    ("VERMELHO", EstoqueModo.APROXIMADO, 0, RotuloAprox.TEM),
                ],
            },
            {
                "codigo": "KD33",
                "descricao": "CANETA METÁLICA COM TOUCH",
                "categoria": "Canetas Metálicas",
                "localizacao": "2º andar, lado direito",
                "preco_pouca_qtd": "1.80",
                "preco_muita_qtd": "1.60",
                "qtd_corte_atacado": 1000,
                "variacoes": [
                    ("PRETO", EstoqueModo.EXATO, 5, None),
                    ("AZUL ESCURO", EstoqueModo.EXATO, 106, None),
                    ("BRANCO", EstoqueModo.APROXIMADO, 0, RotuloAprox.ACABOU),
                ],
            },
            {
                "codigo": "JSC1140",
                "descricao": "COPO STANLEY COM ALÇA 500ml",
                "categoria": "Copos e Garrafas",
                "localizacao": "2º andar",
                "unidades_por_caixa": 5,
                "preco_pouca_qtd": "16.00",
                "preco_muita_qtd": "15.00",
                "variacoes": [
                    ("INOX", EstoqueModo.EXATO, 375, None),
                    ("AZUL MARINHO", EstoqueModo.APROXIMADO, 0, RotuloAprox.MUITO),
                    ("ROSA CLARO", EstoqueModo.APROXIMADO, 0, RotuloAprox.MUITO),
                ],
            },
            {
                "codigo": "A9003",
                "descricao": "CADERNO MOLESKINE GRANDE 14*21cm 80 folhas",
                "categoria": "Blocos e Cadernos",
                "localizacao": "3º e 4º andar à direita",
                "unidades_por_caixa": 1000,
                "preco_pouca_qtd": "9.50",
                "preco_muita_qtd": "8.50",
                "variacoes": [
                    ("AZUL", EstoqueModo.APROXIMADO, 0, RotuloAprox.MUITO),
                    ("PRETO", EstoqueModo.APROXIMADO, 0, RotuloAprox.ACABOU),
                    ("VERMELHO", EstoqueModo.APROXIMADO, 0, RotuloAprox.POUCO),
                ],
            },
            {
                "codigo": "FA12",
                "descricao": "KIT CHURRASCO 36*10cm fechado",
                "categoria": "Outros",
                "localizacao": "5º andar",
                "unidades_por_caixa": 60,
                "preco_pouca_qtd": "22.00",
                "preco_muita_qtd": "19.50",
                "variacoes": [
                    ("", EstoqueModo.APROXIMADO, 0, RotuloAprox.ACABOU),
                ],
            },
        ]

        for p in produtos:
            if db.scalar(select(Produto).where(Produto.codigo == p["codigo"])):
                continue
            produto = Produto(
                codigo=p["codigo"],
                descricao=p["descricao"],
                categoria_id=cats[p["categoria"]].id,
                localizacao=p.get("localizacao"),
                unidades_por_caixa=p.get("unidades_por_caixa"),
                preco_pouca_qtd=Decimal(p["preco_pouca_qtd"]),
                preco_muita_qtd=Decimal(p["preco_muita_qtd"]),
                qtd_corte_atacado=p.get("qtd_corte_atacado"),
            )
            for cor, modo, fisico, rotulo in p["variacoes"]:
                produto.variacoes.append(
                    ProdutoVariacao(
                        cor=cor,
                        estoque_modo=modo,
                        estoque_fisico=fisico,
                        rotulo_aprox=rotulo,
                        estoque_minimo=10,
                    )
                )
            db.add(produto)

        db.commit()
        print("Seed concluído:")
        print(f"  usuários:   {db.scalar(select(func.count(Usuario.id)))}")
        print(f"  categorias: {db.scalar(select(func.count(Categoria.id)))}")
        print(f"  produtos:   {db.scalar(select(func.count(Produto.id)))}")
        print(f"  variações:  {db.scalar(select(func.count(ProdutoVariacao.id)))}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
