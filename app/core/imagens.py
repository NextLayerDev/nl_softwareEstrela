"""Persistência de imagens de variação (foto por cor) — armazenadas no próprio Postgres.

A foto é redimensionada para um tamanho de tela razoável e guardada como bytes JPEG na
coluna ``produto_variacoes.imagem_dados`` (bytea). A exibição é feita por uma rota da própria
aplicação (``GET /produtos/variacao/{id}/foto``), que devolve os bytes com o content-type certo.

**Por que no Postgres e não no MinIO/S3:** o sistema é offline-first (CLAUDE.md §16) — nada na
Fase 1 pode depender de internet. Guardar no banco elimina o bucket externo, a dependência de
rede e a complexidade de URLs assinadas, mantendo as fotos privadas (só autenticado serve).

**Por que bytes e não base64 inline:** ``data:`` URIs inflariam o HTML das listagens (várias
thumbs por página). A rota devolve a imagem sob demanda, mesma origem, cacheável pelo navegador.
"""

from __future__ import annotations

import io
import uuid

from PIL import Image, UnidentifiedImageError

from app.core.errors import RegraNegocioError

_MAX_BYTES = 8 * 1024 * 1024  # 8 MB (tamanho do arquivo enviado, antes de redimensionar)
_MAX_LADO = 700  # px


def _url_foto(variacao_id: int) -> str:
    """Caminho da rota que serve a foto, com sufixo aleatório para invalidar cache do navegador."""
    return f"/produtos/variacao/{variacao_id}/foto?v={uuid.uuid4().hex[:8]}"


def salvar_imagem_variacao(variacao_id: int, conteudo: bytes) -> bytes:
    """Valida, redimensiona e devolve os bytes JPEG prontos para guardar no Postgres.

    Quem chama persiste o retorno em ``ProdutoVariacao.imagem_dados`` e atualiza
    ``imagem_url`` com :func:`caminho_foto_variacao`.
    """
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

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82, optimize=True)
    return buf.getvalue()


def caminho_foto_variacao(variacao_id: int) -> str:
    """Caminho (mesma origem) da rota de foto — para guardar em ``ProdutoVariacao.imagem_url``."""
    return _url_foto(variacao_id)


def url_para_exibicao(valor: str | None) -> str:
    """Recebe o caminho guardado em ``imagem_url`` e devolve-o (mesma origem, pronto p/ <img>)."""
    return valor if isinstance(valor, str) and valor else ""
