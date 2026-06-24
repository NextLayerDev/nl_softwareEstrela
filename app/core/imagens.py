"""Persistência de imagens de variação (foto por cor) — local, offline.

Salva em ``data/uploads/variacoes/`` redimensionando para um tamanho de tela razoável.
O nome do arquivo muda a cada upload (sufixo aleatório) para evitar cache antigo no navegador.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.core.errors import RegraNegocioError

# data/uploads/variacoes na raiz do projeto (app/core/ -> app/ -> raiz).
UPLOADS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"
VARIACOES_DIR = UPLOADS_DIR / "variacoes"
VARIACOES_DIR.mkdir(parents=True, exist_ok=True)

_MAX_BYTES = 8 * 1024 * 1024  # 8 MB
_MAX_LADO = 700  # px


def _caminho(filename: str) -> Path:
    return VARIACOES_DIR / filename


def remover_imagem(filename: str | None) -> None:
    if filename:
        _caminho(filename).unlink(missing_ok=True)


def salvar_imagem_variacao(variacao_id: int, conteudo: bytes, anterior: str | None = None) -> str:
    """Valida, redimensiona e grava a imagem; remove a anterior. Retorna o novo filename."""
    if not conteudo:
        raise RegraNegocioError("Arquivo de imagem vazio.")
    if len(conteudo) > _MAX_BYTES:
        raise RegraNegocioError("Imagem muito grande (máximo 8 MB).")
    try:
        img = Image.open(io.BytesIO(conteudo))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise RegraNegocioError("Arquivo não é uma imagem válida.") from exc

    img = img.convert("RGB")
    img.thumbnail((_MAX_LADO, _MAX_LADO))

    filename = f"{variacao_id}_{uuid.uuid4().hex[:8]}.jpg"
    img.save(_caminho(filename), format="JPEG", quality=82, optimize=True)

    if anterior and anterior != filename:
        remover_imagem(anterior)
    return filename
