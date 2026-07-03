"""Utilitários de geração segura de planilhas (XLSX)."""

from __future__ import annotations

from typing import Any

# Caracteres que, no início de uma célula de texto, fazem o Excel/LibreOffice interpretar
# o conteúdo como fórmula (CSV/Formula Injection). Prefixamos com apóstrofo para neutralizar.
_GATILHOS_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def sanitizar_celula(valor: Any) -> Any:
    """Neutraliza formula injection: prefixa com `'` texto que começa com gatilho de fórmula.

    Valores não-string (int/float/Decimal/date) passam intactos.
    """
    if isinstance(valor, str) and valor[:1] in _GATILHOS_FORMULA:
        return "'" + valor
    return valor


def linha_segura(valores: list[Any]) -> list[Any]:
    return [sanitizar_celula(v) for v in valores]
