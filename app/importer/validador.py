"""Etapa de VALIDAÇÃO: detecta inconsistências nos produtos canônicos.

Detecta:
    - código ausente (bloco sem código identificável)
    - preço ausente
    - código duplicado (mesmo código em mais de um bloco)
    - cor sem quantidade
    - quantidade sem cor
    - estoque negativo
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.importer.parser import ProdutoCanonico
from app.models.enums import EstoqueModo


@dataclass
class Inconsistencia:
    aba: str
    linha: int
    codigo: str | None
    problema: str

    def como_linha(self) -> tuple[str, int, str, str]:
        return (self.aba, self.linha, self.codigo or "", self.problema)


def validar(produtos: list[ProdutoCanonico]) -> list[Inconsistencia]:
    inc: list[Inconsistencia] = []

    contagem = Counter(p.codigo for p in produtos if p.codigo)
    duplicados = {cod for cod, n in contagem.items() if n > 1}

    for p in produtos:
        if not p.codigo:
            inc.append(
                Inconsistencia(
                    p.aba, p.linha_inicio, None, "Bloco sem código identificável (produto ignorado)"
                )
            )

        if p.codigo and p.codigo in duplicados:
            inc.append(
                Inconsistencia(p.aba, p.linha_inicio, p.codigo, "Código duplicado na planilha")
            )

        if p.preco_pouca_qtd is None and p.preco_muita_qtd is None:
            inc.append(Inconsistencia(p.aba, p.linha_inicio, p.codigo, "Preço ausente"))

        cores = [v for v in p.variacoes if v.cor]
        sem_cor = [v for v in p.variacoes if not v.cor]

        for v in p.variacoes:
            # cor sem quantidade: variação com cor mas sem modo de estoque definido
            if v.cor and v.estoque_modo == EstoqueModo.APROXIMADO and v.rotulo_aprox is None:
                inc.append(
                    Inconsistencia(p.aba, v.linha, p.codigo, f"Cor '{v.cor}' sem quantidade")
                )
            if v.estoque_fisico < 0:
                inc.append(
                    Inconsistencia(
                        p.aba, v.linha, p.codigo, f"Estoque negativo na cor '{v.cor or '-'}'"
                    )
                )

        # quantidade sem cor: há variações sem cor mas o produto tem outras com cor
        if cores and sem_cor:
            for v in sem_cor:
                if v.estoque_modo == EstoqueModo.EXATO or v.rotulo_aprox is not None:
                    inc.append(
                        Inconsistencia(p.aba, v.linha, p.codigo, "Quantidade sem cor associada")
                    )

    return inc
