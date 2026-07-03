from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy.orm import Session

from app.core.planilha import linha_segura
from app.repositories.relatorio_repo import relatorio_repo

# Cortes da curva ABC por valor acumulado (doc): A=80%, B=15% (até 95%), C=5%.
ABC_CORTE_A = Decimal("0.80")
ABC_CORTE_B = Decimal("0.95")


class RelatorioService:
    # ---------- Vendas ----------
    def vendas(
        self,
        db: Session,
        de: date | None = None,
        ate: date | None = None,
        vendedor_id: int | None = None,
        cliente_id: int | None = None,
    ) -> dict:
        linhas = relatorio_repo.vendas(db, de, ate, vendedor_id, cliente_id)
        total = sum((Decimal(linha["total"] or 0) for linha in linhas), Decimal("0"))
        return {
            "linhas": linhas,
            "total": total.quantize(Decimal("0.01")),
            "qtd_pedidos": len(linhas),
        }

    # ---------- Curva ABC ----------
    def curva_abc(self, db: Session, de: date | None = None, ate: date | None = None) -> dict:
        linhas = relatorio_repo.abc_produtos(db, de, ate)
        total = sum((Decimal(linha["valor"] or 0) for linha in linhas), Decimal("0"))

        acumulado = Decimal("0")
        resultado: list[dict] = []
        for linha in linhas:
            valor = Decimal(linha["valor"] or 0)
            # Classe pelo acumulado ANTES de incluir este item: o item que cruza o
            # corte ainda pertence à classe superior (convenção clássica da curva ABC).
            pct_anterior = (acumulado / total) if total > 0 else Decimal("0")
            acumulado += valor
            pct_acum = (acumulado / total) if total > 0 else Decimal("0")
            if pct_anterior < ABC_CORTE_A:
                classe = "A"
            elif pct_anterior < ABC_CORTE_B:
                classe = "B"
            else:
                classe = "C"
            resultado.append(
                {
                    **linha,
                    "valor": valor.quantize(Decimal("0.01")),
                    "pct_acumulado": (pct_acum * 100).quantize(Decimal("0.01")),
                    "classe": classe,
                }
            )
        return {"linhas": resultado, "total": total.quantize(Decimal("0.01"))}

    # ---------- Valorização de estoque (só admin/financeiro) ----------
    def valorizacao(self, db: Session) -> dict:
        linhas = relatorio_repo.valorizacao_estoque(db)
        total = sum((Decimal(linha["valor"] or 0) for linha in linhas), Decimal("0"))
        return {"linhas": linhas, "total": total.quantize(Decimal("0.01"))}

    # ---------- Export XLSX ----------
    def _to_xlsx(self, titulo: str, cabecalho: list[str], linhas: list[list]) -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.title = titulo[:31]
        ws.append(cabecalho)
        for celula in ws[1]:
            celula.font = Font(bold=True)
        for linha in linhas:
            ws.append(linha_segura(linha))
        buffer = io.BytesIO()
        wb.save(buffer)
        return buffer.getvalue()

    def vendas_xlsx(self, db: Session, **kwargs) -> bytes:
        dados = self.vendas(db, **kwargs)
        cabecalho = ["Pedido", "Data", "Cliente", "Vendedor", "Desconto", "Total"]
        linhas = [
            [
                linha.get("numero") or linha["id"],
                linha["criado_em"].strftime("%d/%m/%Y") if linha.get("criado_em") else "",
                linha["cliente"],
                linha["vendedor"],
                float(linha["desconto_total"] or 0),
                float(linha["total"] or 0),
            ]
            for linha in dados["linhas"]
        ]
        linhas.append(["", "", "", "", "TOTAL", float(dados["total"])])
        return self._to_xlsx("Vendas", cabecalho, linhas)

    def curva_abc_xlsx(self, db: Session, **kwargs) -> bytes:
        dados = self.curva_abc(db, **kwargs)
        cabecalho = ["Código", "Descrição", "Qtd", "Valor", "% Acumulado", "Classe"]
        linhas = [
            [
                linha["codigo"],
                linha["descricao"],
                int(linha["qtd"] or 0),
                float(linha["valor"]),
                float(linha["pct_acumulado"]),
                linha["classe"],
            ]
            for linha in dados["linhas"]
        ]
        return self._to_xlsx("Curva ABC", cabecalho, linhas)

    def valorizacao_xlsx(self, db: Session) -> bytes:
        dados = self.valorizacao(db)
        cabecalho = ["Código", "Descrição", "Estoque físico", "Custo unit.", "Valor"]
        linhas = [
            [
                linha["codigo"],
                linha["descricao"],
                int(linha["fisico"] or 0),
                float(linha["preco_custo"] or 0),
                float(linha["valor"]),
            ]
            for linha in dados["linhas"]
        ]
        linhas.append(["", "", "", "TOTAL", float(dados["total"])])
        return self._to_xlsx("Valorizacao", cabecalho, linhas)


relatorio_service = RelatorioService()
