"""CLI do ETL: importa o catálogo de `data/CONTROLE.xlsx` para o banco.

Uso:
    uv run python scripts/import_planilhas.py --dry-run
    uv run python scripts/import_planilhas.py
    uv run python scripts/import_planilhas.py --so-categoria CHAVEIROS
    uv run python scripts/import_planilhas.py --file outra.xlsx --dry-run

`--dry-run` valida e gera o relatório de inconsistências, mas NÃO grava no banco.
Sempre gera `relatorio_inconsistencias.xlsx` (aba / linha / código / problema).
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import openpyxl

from app.core.database import SessionLocal
from app.core.planilha import linha_segura
from app.importer import ABA_PARA_CATEGORIA, ABAS_CATALOGO
from app.importer.carga import carregar
from app.importer.parser import parse_blocos
from app.importer.staging import ler_staging
from app.importer.validador import validar
from app.models.enums import EstoqueModo

DEFAULT_FILE = pathlib.Path(__file__).resolve().parent.parent / "data" / "CONTROLE.xlsx"
RELATORIO = pathlib.Path("relatorio_inconsistencias.xlsx")


def _gerar_relatorio(inconsistencias, destino: pathlib.Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inconsistências"
    ws.append(["Aba", "Linha", "Código", "Problema"])
    for inc in inconsistencias:
        ws.append(linha_segura(list(inc.como_linha())))
    wb.save(destino)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Importador do catálogo CONTROLE.xlsx")
    parser.add_argument("--file", default=str(DEFAULT_FILE), help="Caminho da planilha")
    parser.add_argument(
        "--dry-run", action="store_true", help="Valida e gera relatório, mas não grava"
    )
    parser.add_argument(
        "--so-categoria", default=None, help="Importa só a aba/categoria informada (nome da aba)"
    )
    args = parser.parse_args(argv)

    abas = ABAS_CATALOGO
    if args.so_categoria:
        alvo = args.so_categoria.strip().upper()
        abas = [a for a in ABAS_CATALOGO if a.upper() == alvo]
        if not abas:
            print(f"Aba '{args.so_categoria}' não é uma aba de catálogo. Opções: {ABAS_CATALOGO}")
            return 2

    print(f"Lendo {args.file} (abas: {', '.join(abas)})...")
    staging = ler_staging(args.file, abas)
    produtos = parse_blocos(staging, ABA_PARA_CATEGORIA)
    inconsistencias = validar(produtos)

    # Estatísticas
    com_codigo = [p for p in produtos if p.codigo]
    sem_codigo = [p for p in produtos if not p.codigo]
    total_var = sum(len(p.variacoes) for p in com_codigo)
    var_exatas = sum(
        1 for p in com_codigo for v in p.variacoes if v.estoque_modo == EstoqueModo.EXATO
    )
    var_aprox = total_var - var_exatas

    _gerar_relatorio(inconsistencias, RELATORIO)

    if args.dry_run:
        print("\n[DRY-RUN] Nenhuma alteração gravada no banco.")
    else:
        db = SessionLocal()
        try:
            res = carregar(db, produtos, dry_run=False)
            print("\nCarga concluída:")
            print(f"  produtos criados:        {res.produtos_criados}")
            print(f"  produtos atualizados:    {res.produtos_atualizados}")
            print(f"  variações criadas:       {res.variacoes_criadas}")
            print(f"  variações atualizadas:   {res.variacoes_atualizadas}")
            print(f"  códigos alt. criados:    {res.codigos_alt_criados}")
            print(f"  movimentações criadas:   {res.movimentacoes_criadas}")
            print(f"  categorias criadas:      {res.categorias_criadas}")
            print(f"  ignorados (sem código):  {res.ignorados_sem_codigo}")
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    print("\n=== Resumo do parsing ===")
    print(f"  blocos lidos:            {len(produtos)}")
    print(f"  produtos com código:     {len(com_codigo)}")
    print(f"  blocos sem código:       {len(sem_codigo)}")
    print(f"  variações totais:        {total_var}")
    print(f"  variações EXATAS:        {var_exatas}")
    print(f"  variações APROXIMADAS:   {var_aprox}")
    print(f"  inconsistências:         {len(inconsistencias)}")
    print(f"  relatório gerado em:     {RELATORIO.resolve()}")

    # Top tipos de inconsistência
    if inconsistencias:
        from collections import Counter

        tipos = Counter(i.problema.split(" (")[0].split("'")[0].strip() for i in inconsistencias)
        print("\n  principais inconsistências:")
        for tipo, n in tipos.most_common(8):
            print(f"    - {tipo}: {n}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
