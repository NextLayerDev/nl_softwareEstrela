"""Orçamento Inteligente — tela PÚBLICA de montagem rápida de orçamento.

É a releitura do "Lançamentos de Reserva" (P_RESE01) do sistema antigo, na paleta da
marca e com a digitação do PDV de balcão: um campo só, onde `2 caneta 10` vira duas
canetas a R$ 10,00.

DUAS DECISÕES QUE MOLDAM O ARQUIVO INTEIRO:

1. **Não exige login.** É a única tela do sistema, fora de `/login` e `/health`, sem
   `Depends`. Por isso ela só LÊ, nunca escreve, e o que devolve é o mínimo: código,
   descrição, cor e preço de venda. Nunca `preco_custo`, nunca `preco_minimo`, nunca
   saldo numérico, nunca dado de cliente, nunca foto. A busca exige 2 caracteres e
   devolve no máximo 10 linhas — sem isso a rota viraria um dump do catálogo.

2. **Não grava nada.** O orçamento vive no navegador e sai pela impressora. Não existe
   rascunho no servidor, não existe reserva de estoque, não existe numeração. Quem
   quiser transformar em venda abre um pedido em `/pedidos`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.templates import templates
from app.deps.db import get_db
from app.repositories.estoque_repo import estoque_repo
from app.services.empresa_service import empresa_service
from app.services.pedido_service import pedido_service

router = APIRouter()

# Teto de sugestões por consulta. A tela mostra uma lista curta que se escolhe pelas
# setas; passar disso vira rolagem, e rolagem no balcão é mais lento que redigitar.
_LIMITE_SUGESTOES = 10


def _centavos(valor) -> int:
    """Preço em centavos inteiros, como o JS da tela espera.

    A tela toda soma em inteiros de propósito: `0.1 + 0.2` em ponto flutuante não é
    `0.3`, e um orçamento que fecha um centavo diferente do pedido destrói a confiança
    na tela inteira.
    """
    return int((valor or 0) * 100)


@router.get("/orcamento", response_class=HTMLResponse)
def tela_orcamento(request: Request, db: Session = Depends(get_db)):
    """A tela. Pública: qualquer terminal da loja abre sem sessão."""
    contexto = {"titulo": "Orçamento", "empresa": empresa_service.obter(db)}
    return templates.TemplateResponse(request, "orcamento/index.html", contexto)


@router.get("/orcamento/busca", response_class=HTMLResponse)
def busca_orcamento(request: Request, q: str = "", db: Session = Depends(get_db)):
    """Sugestões de produto (fragmento HTMX).

    Aceita duas formas de entrada, na mesma rota, porque no balcão são o mesmo gesto:
    o pedaço de nome que a pessoa digita e o código que o leitor dispara. Quando o
    termo bate um código exato, aquele produto vem em primeiro lugar.
    """
    termo = q.strip()
    variacoes = estoque_repo.busca_orcamento(db, termo, limit=_LIMITE_SUGESTOES)

    exato = estoque_repo.por_codigo_exato(db, termo)
    if exato is not None:
        variacoes = [exato] + [v for v in variacoes if v.id != exato.id]
        variacoes = variacoes[:_LIMITE_SUGESTOES]

    sugestoes = [
        {
            "id": v.id,
            "codigo": v.produto.codigo,
            "descricao": v.produto.descricao,
            "cor": v.cor or "",
            # Preço de varejo: a tela não sabe a quantidade antes de a pessoa digitar,
            # e o desconto de atacado é conversa de vendedor, não de orçamento público.
            "preco_centavos": _centavos(pedido_service.sugerir_preco(v.produto, 1).preco_sugerido),
        }
        for v in variacoes
    ]
    return templates.TemplateResponse(
        request, "orcamento/_sugestoes.html", {"sugestoes": sugestoes, "termo": termo}
    )
