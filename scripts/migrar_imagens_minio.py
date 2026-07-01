"""Migra fotos de variação que ainda estão como filename local (pré-MinIO) para o bucket
MinIO configurado em settings.S3_*, atualizando produto_variacoes.imagem_url para a URL
pública. Idempotente: só mexe em valores que não começam com "http" (já migrados são ignorados).
Lê os arquivos de data/uploads/variacoes/<filename>.

Uso: uv run python scripts/migrar_imagens_minio.py [--dry-run]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.imagens import _cliente_s3, _url_publica
from app.models.produto import ProdutoVariacao

UPLOADS_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads" / "variacoes"


def main(dry_run: bool) -> None:
    db = SessionLocal()
    variacoes = list(
        db.scalars(
            select(ProdutoVariacao).where(
                ProdutoVariacao.imagem_url.is_not(None),
                ~ProdutoVariacao.imagem_url.startswith("http"),
            )
        )
    )
    if not variacoes:
        print("Nada para migrar — todas as fotos já são URL do MinIO (ou não há fotos).")
        return

    print(f"{len(variacoes)} variação(ões) com foto local a migrar.")
    migradas, ausentes = 0, 0
    for v in variacoes:
        filename = v.imagem_url
        caminho = UPLOADS_DIR / filename
        if not caminho.exists():
            print(f"  [aviso] variacao {v.id}: arquivo '{filename}' não existe em disco — zerando.")
            ausentes += 1
            if not dry_run:
                v.imagem_url = None
            continue

        chave = f"variacoes/{filename}"
        url = _url_publica(chave)
        print(f"  variacao {v.id}: {filename} -> {url}")
        if not dry_run:
            _cliente_s3().put_object(
                Bucket=settings.S3_BUCKET,
                Key=chave,
                Body=caminho.read_bytes(),
                ContentType="image/jpeg",
            )
            v.imagem_url = url
        migradas += 1

    if dry_run:
        print(f"\n[dry-run] {migradas} seriam migradas, {ausentes} seriam zeradas. Nada gravado.")
    else:
        db.commit()
        print(f"\n{migradas} migradas, {ausentes} zeradas (arquivo ausente). Banco atualizado.")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
