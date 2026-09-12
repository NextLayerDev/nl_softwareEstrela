"""Enfileira snapshots de produto para o catálogo externo (omni_respostaMax).

Este sistema é o estoque-chefe; o catálogo externo é só um espelho, atualizado via
webhook (fora do request, ver app/integracoes/catalogo_webhook.py e o job
`job_flush_sync_outbox` em app/jobs.py). Só produtos com `publicar_catalogo=True`
entram na fila — é o mesmo gate que a UI já usa para marcar "este produto vai pro
catálogo".

Cada evento é um snapshot COMPLETO do produto (não um delta): mais simples e
idempotente para o lado que recebe, que sempre faz um upsert do produto inteiro.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.produto import Produto, ProdutoVariacao
from app.repositories.sync_outbox_repo import sync_outbox_repo


def _dados_variacao(variacao: ProdutoVariacao) -> dict:
    return {
        "variacao_id": variacao.id,
        "cor": variacao.cor,
        "estoque_modo": str(variacao.estoque_modo),
        "estoque_disponivel": variacao.disponivel,
        "rotulo_aprox": str(variacao.rotulo_aprox) if variacao.rotulo_aprox else None,
        "ativo": variacao.ativo,
    }


def _snapshot_produto(produto: Produto) -> dict:
    """Payload do evento. Jamais inclui preco_custo nem margem (mesma regra de eventos.py)."""
    return {
        "codigo": produto.codigo,
        "descricao": produto.descricao,
        "categoria": produto.categoria.nome if produto.categoria else None,
        "preco_venda": float(produto.preco_pouca_qtd),
        "preco_atacado": float(produto.preco_muita_qtd),
        "preco_promocional": (
            float(produto.preco_promocional) if produto.preco_promocional is not None else None
        ),
        "ativo": produto.ativo,
        "publicar_catalogo": produto.publicar_catalogo,
        "variacoes": [_dados_variacao(v) for v in produto.variacoes if v.ativo],
    }


class CatalogoSyncService:
    def enfileirar_produto(self, db: Session, produto: Produto, *, forcar: bool = False) -> None:
        """Enfileira um snapshot, exceto quando o produto nunca foi (e continua não
        sendo) publicado no catálogo.

        `forcar=True` é usado só na transição publicar_catalogo True -> False: sem
        isso, desmarcar o produto simplesmente para de gerar eventos, e o catálogo
        externo fica com o dado antigo (ainda visível) parado pra sempre. Com
        `forcar`, um último snapshot é enviado com `publicar_catalogo: False`, e é
        esse valor que o webhook do outro lado usa pra esconder o produto da vitrine.
        """
        if not produto.publicar_catalogo and not forcar:
            return
        sync_outbox_repo.enfileirar(db, "produto", produto.id, _snapshot_produto(produto))


catalogo_sync_service = CatalogoSyncService()
