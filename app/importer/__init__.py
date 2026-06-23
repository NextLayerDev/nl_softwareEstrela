"""Motor de importação da planilha real (`data/CONTROLE.xlsx`).

Pipeline:
    staging  -> lê o bruto preservando aba/linha/valor literal
    parser   -> agrupa em blocos e produz registros canônicos
    validador-> detecta inconsistências (preço ausente, cor sem qtd, etc.)
    carga    -> grava de forma idempotente + movimentação inicial de estoque

A CLI fica em `scripts/import_planilhas.py`.
"""

from app.importer.carga import ResultadoCarga, carregar
from app.importer.parser import ProdutoCanonico, VariacaoCanonica, parse_blocos
from app.importer.staging import CelulaBruta, ler_staging
from app.importer.validador import Inconsistencia, validar

__all__ = [
    "CelulaBruta",
    "Inconsistencia",
    "ProdutoCanonico",
    "ResultadoCarga",
    "VariacaoCanonica",
    "carregar",
    "ler_staging",
    "parse_blocos",
    "validar",
]

# Abas que contêm catálogo de produtos (importar). As demais são pedidos/notas/refs.
ABAS_CATALOGO = [
    "CANETAS PLÁSTICAS",
    "CANETAS METÁLICAS",
    "COPOS E GARRAFAS",
    "BLOCOS E CADERNOS",
    "CHAVEIROS",
    "ELETRÔNICOS",
    "OUTROS",
]

# Nome da aba -> nome da categoria (como gravado em `categorias` pelo seed).
ABA_PARA_CATEGORIA = {
    "CANETAS PLÁSTICAS": "Canetas Plásticas",
    "CANETAS METÁLICAS": "Canetas Metálicas",
    "COPOS E GARRAFAS": "Copos e Garrafas",
    "BLOCOS E CADERNOS": "Blocos e Cadernos",
    "CHAVEIROS": "Chaveiros",
    "ELETRÔNICOS": "Eletrônicos",
    "OUTROS": "Outros",
}
