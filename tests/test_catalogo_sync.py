from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integracoes import catalogo_webhook
from app.models.produto import Produto, ProdutoVariacao
from app.models.sync_outbox import SyncOutbox
from app.repositories.sync_outbox_repo import sync_outbox_repo
from app.schemas.produto import ProdutoUpdate
from app.services.catalogo_sync_service import catalogo_sync_service
from app.services.produto_service import produto_service


def _cod() -> str:
    return f"T{uuid.uuid4().hex[:10].upper()}"


def _produto(db: Session, *, publicar_catalogo: bool = True) -> Produto:
    p = Produto(
        codigo=_cod(),
        descricao="Camiseta Teste",
        preco_pouca_qtd="19.90",
        preco_muita_qtd="15.00",
        preco_custo="8.00",
        publicar_catalogo=publicar_catalogo,
    )
    db.add(p)
    db.flush()
    db.add(ProdutoVariacao(produto_id=p.id, cor="Azul", estoque_fisico=10))
    db.flush()
    db.refresh(p)
    return p


def _pendentes(db: Session, produto_id: int) -> list[SyncOutbox]:
    return list(
        db.scalars(
            select(SyncOutbox).where(
                SyncOutbox.entidade == "produto",
                SyncOutbox.entidade_id == produto_id,
                SyncOutbox.status == "pendente",
            )
        )
    )


def test_produto_sem_publicar_catalogo_nao_enfileira(db: Session):
    p = _produto(db, publicar_catalogo=False)
    catalogo_sync_service.enfileirar_produto(db, p)
    assert _pendentes(db, p.id) == []


def test_produto_publicado_enfileira_snapshot_sem_preco_custo(db: Session):
    p = _produto(db, publicar_catalogo=True)
    catalogo_sync_service.enfileirar_produto(db, p)
    rows = _pendentes(db, p.id)
    assert len(rows) == 1
    payload = rows[0].payload
    assert payload["codigo"] == p.codigo
    assert "preco_custo" not in payload
    assert payload["variacoes"][0]["cor"] == "Azul"
    assert payload["variacoes"][0]["estoque_disponivel"] == 10


def test_enfileirar_de_novo_substitui_pendente_nao_duplica(db: Session):
    p = _produto(db, publicar_catalogo=True)
    catalogo_sync_service.enfileirar_produto(db, p)
    catalogo_sync_service.enfileirar_produto(db, p)
    assert len(_pendentes(db, p.id)) == 1


def test_despublicar_sem_forcar_nao_enfileira(db: Session):
    """Sem forcar=True, desmarcar publicar_catalogo simplesmente para de sincronizar
    (não é o suficiente para avisar o catálogo externo de que deve esconder o produto)."""
    p = _produto(db, publicar_catalogo=False)
    catalogo_sync_service.enfileirar_produto(db, p)
    assert _pendentes(db, p.id) == []


def test_despublicar_com_forcar_manda_ultimo_snapshot(db: Session):
    p = _produto(db, publicar_catalogo=False)
    catalogo_sync_service.enfileirar_produto(db, p, forcar=True)
    rows = _pendentes(db, p.id)
    assert len(rows) == 1
    assert rows[0].payload["publicar_catalogo"] is False


def test_marcar_enviado_limpa_erro(db: Session):
    p = _produto(db, publicar_catalogo=True)
    row = sync_outbox_repo.enfileirar(db, "produto", p.id, {"codigo": p.codigo})
    sync_outbox_repo.marcar_erro(db, row, "falhou uma vez")
    assert row.status == "pendente"
    assert row.tentativas == 1
    sync_outbox_repo.marcar_enviado(db, row)
    assert row.status == "enviado"
    assert row.erro is None


def test_marcar_erro_falha_apos_maximo_de_tentativas(db: Session):
    p = _produto(db, publicar_catalogo=True)
    row = sync_outbox_repo.enfileirar(db, "produto", p.id, {"codigo": p.codigo})
    for _ in range(10):
        sync_outbox_repo.marcar_erro(db, row, "erro de rede")
    assert row.status == "falhou"
    assert row.tentativas == 10


def test_produto_service_atualizar_despublicar_forca_ultimo_snapshot(db: Session):
    """Fim a fim via produto_service.atualizar: True->False manda o snapshot final
    (com publicar_catalogo=False); ficar em False->False não gera evento nenhum."""
    p = _produto(db, publicar_catalogo=True)
    produto_service.atualizar(db, p.id, ProdutoUpdate(publicar_catalogo=False))
    rows = _pendentes(db, p.id)
    assert len(rows) == 1
    assert rows[0].payload["publicar_catalogo"] is False

    sync_outbox_repo.marcar_enviado(db, rows[0])
    produto_service.atualizar(db, p.id, ProdutoUpdate(descricao="Outra descrição"))
    assert _pendentes(db, p.id) == []


def test_enviar_evento_sem_config_nao_tenta_rede(monkeypatch):
    monkeypatch.setattr(catalogo_webhook.settings, "CATALOGO_WEBHOOK_URL", "")
    monkeypatch.setattr(catalogo_webhook.settings, "CATALOGO_WEBHOOK_SECRET", "")
    ok, erro = catalogo_webhook.enviar_evento({"codigo": "X"})
    assert ok is False
    assert erro is not None
