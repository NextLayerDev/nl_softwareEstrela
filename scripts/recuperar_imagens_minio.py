"""Recupera fotos de variação que ficaram órfãs no bucket MinIO após a migration
``b7e2c9f4a1d8`` (imagens no Postgres).

O que aconteceu: a migration tentava fazer backfill baixando cada foto do MinIO no
momento do ``alembic upgrade head``. Em produção o servidor **não alcança o MinIO**
(foi o timeout que motivou a mudança), então TODAS as fotos antigas caíram no ramo
"perdidas": ``imagem_url`` virou NULL e ``imagem_dados`` ficou vazio. Os objetos **não
foram apagados** — continuam no bucket ``estrela-uploads`` sob ``variacoes/``.

Este script repõe essas fotos a partir do bucket, sem depender do mapeamento perdido
(``imagem_url`` foi zerado): ele **lista** os objetos do MinIO e casa pelo ``variacao_id``
que está no nome da chave (convenção ``variacoes/{variacao_id}_{hex}.jpg``).

Fonte da verdade é o bucket: só recebem foto as variações que têm objeto no MinIO. Quem
nunca teve foto (sem objeto) segue sem foto — correto.

Idempotente: só preenche variações onde ``imagem_dados IS NULL`` (não sobrescreve quem
já tem foto, seja da migration bem-sucedida ou de re-upload manual).

Rode de uma máquina que alcance **o MinIO** e **o banco de destino** (prod). Aponte
``DATABASE_URL`` para o banco alvo (via Tailscale/SSH port-forward se precisar).

Uso:
    uv run python scripts/recuperar_imagens_minio.py --dry-run
    uv run python scripts/recuperar_imagens_minio.py
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3
from botocore.client import Config as BotoConfig
from sqlalchemy import select, text

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.imagens import caminho_foto_variacao, salvar_imagem_variacao
from app.models.produto import ProdutoVariacao

log = logging.getLogger("estrela.recuperar_imagens")

# Convenção da chave: "variacoes/{variacao_id}_{hex8}.jpg" — o id é o trecho numérico
# antes do primeiro "_", imediatamente após a barra.
_RE_CHAVE = re.compile(r"^variacoes/(\d+)_")

_BOTO_CONFIG = BotoConfig(
    signature_version="s3v4", connect_timeout=5, read_timeout=15, retries={"max_attempts": 1}
)


def _cliente_minio():
    if not settings.S3_ACCESS_KEY or not settings.S3_BUCKET:
        raise SystemExit(
            "S3_ACCESS_KEY / S3_BUCKET não configurados — preencha as vars S3_* no .env "
            "apontando para o MinIO antes de rodar."
        )
    kwargs = {"endpoint_url": settings.S3_ENDPOINT_URL} if settings.S3_ENDPOINT_URL else {}
    return boto3.client(
        "s3",
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name="us-east-1",
        config=_BOTO_CONFIG,
        **kwargs,
    )


def _listar_objetos(cliente) -> dict[int, dict]:
    """Lista ``variacoes/*`` e devolve {variacao_id: {key, last_modified}}.

    Se houver mais de um objeto por variacao_id (uploads antigos cujo delete falhou),
    fica com o mais recente (LastModified maior) — é a última foto enviada.
    """
    paginator = cliente.get_paginator("list_objects_v2")
    achados: dict[int, dict] = {}
    for page in paginator.paginate(Bucket=settings.S3_BUCKET, Prefix="variacoes/"):
        for obj in page.get("Contents", []):
            m = _RE_CHAVE.match(obj["Key"])
            if not m:
                continue
            vid = int(m.group(1))
            atual = achados.get(vid)
            if atual is None or obj["LastModified"] > atual["last_modified"]:
                achados[vid] = {"key": obj["Key"], "last_modified": obj["LastModified"]}
    return achados


def _baixar_e_normalizar(cliente, vid: int, key: str) -> bytes | None:
    """Baixa o objeto do MinIO e devolve os bytes JPEG normalizados (ou None em falha)."""
    try:
        obj = cliente.get_object(Bucket=settings.S3_BUCKET, Key=key)
        conteudo = obj["Body"].read()
    except Exception as exc:  # noqa: BLE001
        log.warning("  variacao %d: falha ao baixar '%s' — %s", vid, key, exc)
        return None
    try:
        # Re-encode para JPEG normalizado (mesmo pipeline de um upload novo).
        return salvar_imagem_variacao(vid, conteudo)
    except Exception as exc:  # noqa: BLE001
        log.warning("  variacao %d: '%s' não é imagem válida — %s", vid, key, exc)
        return None


def _exportar_sql(cliente, objetos: dict[int, dict], saida: Path) -> None:
    """Gera um .sql autocontido (bytes em hex via decode) para aplicar no banco de destino.

    Usa-se quando a máquina que alcança o MinIO NÃO alcança o banco de prod: rode aqui
    com --export, leve o arquivo ao servidor de prod e aplique com psql. Não precisa de
    MinIO nem de Python lá. Cada UPDATE só grava onde a variação existe E imagem_dados
    IS NULL (idempotente, não sobrescreve foto já reposta).
    """
    ok, falhas = 0, 0
    with saida.open("w", encoding="utf-8") as f:
        f.write("-- Recuperação de fotos de variação a partir do bucket MinIO.\n")
        f.write("-- Idempotente: só preenche onde imagem_dados IS NULL.\n")
        f.write('-- Aplique com: psql "$DATABASE_URL" -f <este_arquivo>\n\n')
        f.write("BEGIN;\n\n")
        for vid in sorted(objetos):
            key = objetos[vid]["key"]
            dados = _baixar_e_normalizar(cliente, vid, key)
            if dados is None:
                falhas += 1
                continue
            url = caminho_foto_variacao(vid)
            f.write(
                f"UPDATE produto_variacoes SET imagem_dados = decode('{dados.hex()}', 'hex'),\n"
                f"                            imagem_url = '{url}'\n"
                f"WHERE id = {vid} AND imagem_dados IS NULL;\n\n"
            )
            ok += 1
            log.info("  variacao %d: exportada de '%s'", vid, key)
        f.write("COMMIT;\n")
    log.info("\n%d foto(s) exportadas para %s, %d falha(s).", ok, saida, falhas)
    log.info('Aplique no banco de prod com:  psql "$DATABASE_URL" -f %s', saida)


def _exportar_arquivos(cliente, objetos: dict[int, dict], saida: Path) -> None:
    """Baixa cada foto e salva como <saida>/<variacao_id>.jpg (bytes já normalizados).

    Os arquivos vão junto no repo (commitados) e uma migration Alembic de dados os lê e
    grava em produto_variacoes.imagem_dados no próximo deploy (alembic upgrade head). Assim
    os bytes viajam pelo git — prod não precisa alcançar o MinIO.
    """
    saida.mkdir(parents=True, exist_ok=True)
    ok, falhas = 0, 0
    for vid in sorted(objetos):
        key = objetos[vid]["key"]
        dados = _baixar_e_normalizar(cliente, vid, key)
        if dados is None:
            falhas += 1
            continue
        (saida / f"{vid}.jpg").write_bytes(dados)
        ok += 1
        log.info("  variacao %d: %s (%d bytes)", vid, key, len(dados))
    log.info("\n%d foto(s) salvas em %s, %d falha(s).", ok, saida, falhas)


def _aplicar_direto(cliente, objetos: dict[int, dict], dry_run: bool) -> None:
    """Grava direto no banco configurado em DATABASE_URL (modo local/dev ou se o banco
    de destino é alcançável desta máquina). Filtra só variações existentes e sem foto."""
    db = SessionLocal()
    try:
        ids_existentes = set(db.scalars(select(ProdutoVariacao.id)).all())
        sem_foto = set(
            db.scalars(
                select(ProdutoVariacao.id).where(ProdutoVariacao.imagem_dados.is_(None))
            ).all()
        )

        recuperar = [vid for vid in objetos if vid in ids_existentes and vid in sem_foto]
        ignoradas_existente = [
            vid for vid in objetos if vid in ids_existentes and vid not in sem_foto
        ]
        orfas_db = [vid for vid in objetos if vid not in ids_existentes]

        log.info("  %d a recuperar (objeto no MinIO e imagem_dados NULL).", len(recuperar))
        log.info(
            "  %d ignoradas (já têm imagem_dados — não sobrescreve).", len(ignoradas_existente)
        )
        log.info(
            "  %d órfãs (objeto no bucket mas variação não existe mais no banco).", len(orfas_db)
        )
        if ignoradas_existente:
            log.info("    ignoradas: %s", sorted(ignoradas_existente))
        if orfas_db:
            log.info("    órfãs: %s", sorted(orfas_db))

        if dry_run:
            log.info("\n[dry-run] Nada gravado. Rode sem --dry-run para aplicar.")
            return

        ok, falhas = 0, 0
        for vid in recuperar:
            dados = _baixar_e_normalizar(cliente, vid, objetos[vid]["key"])
            if dados is None:
                falhas += 1
                continue
            db.execute(
                text(
                    "UPDATE produto_variacoes SET imagem_dados = :d, imagem_url = :u WHERE id = :id"
                ),
                {"d": dados, "u": caminho_foto_variacao(vid), "id": vid},
            )
            ok += 1
            log.info("  variacao %d: recuperada de '%s'", vid, objetos[vid]["key"])

        db.commit()
        log.info("\n%d foto(s) recuperada(s), %d falha(s).", ok, falhas)
    finally:
        db.close()


def main(dry_run: bool, exportar: Path | None, exportar_files: Path | None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cliente = _cliente_minio()
    log.info("Listando objetos do bucket %s (prefixo 'variacoes/')...", settings.S3_BUCKET)
    objetos = _listar_objetos(cliente)
    log.info(
        "  %d objeto(s) encontrado(s) no MinIO, %d variação(ões) distintas.",
        sum(1 for _ in objetos),
        len(objetos),
    )
    if not objetos:
        log.info("Nada no bucket — nada a recuperar.")
        return

    if exportar_files:
        log.info("Modo export-files: salvando .jpg em %s (sem tocar no banco).", exportar_files)
        _exportar_arquivos(cliente, objetos, exportar_files)
    elif exportar:
        log.info("Modo export: gerando SQL em %s (sem tocar no banco).", exportar)
        _exportar_sql(cliente, objetos, exportar)
    else:
        _aplicar_direto(cliente, objetos, dry_run)


def _parse_args() -> tuple[bool, Path | None, Path | None]:
    dry_run = "--dry-run" in sys.argv
    exportar: Path | None = None
    exportar_files: Path | None = None
    if "--export" in sys.argv:
        try:
            idx = sys.argv.index("--export")
            exportar = Path(sys.argv[idx + 1])
        except (ValueError, IndexError) as exc:
            raise SystemExit("Uso: --export <caminho_do_arquivo.sql>") from exc
    if "--export-files" in sys.argv:
        try:
            idx = sys.argv.index("--export-files")
            exportar_files = Path(sys.argv[idx + 1])
        except (ValueError, IndexError) as exc:
            raise SystemExit("Uso: --export-files <diretorio>") from exc
    return dry_run, exportar, exportar_files


if __name__ == "__main__":
    main(*_parse_args())
