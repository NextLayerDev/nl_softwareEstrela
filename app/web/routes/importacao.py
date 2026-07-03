from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.errors import RegraNegocioError
from app.core.templates import templates
from app.deps.auth import require_role
from app.deps.db import get_db
from app.importer import (
    ABA_PARA_CATEGORIA,
    ABAS_CATALOGO,
    carregar,
    ler_staging,
    parse_blocos,
    validar,
)
from app.models.usuario import Usuario

router = APIRouter()

# Diretório onde uploads ficam até a confirmação da carga.
_UPLOAD_DIR = Path(tempfile.gettempdir()) / "estrela_importacao"
_UPLOAD_DIR.mkdir(exist_ok=True)

# Limite de tamanho da planilha (defesa contra upload gigante / zip-bomb).
_MAX_XLSX_BYTES = 20 * 1024 * 1024  # 20 MB
# Content-types que navegadores mandam para .xlsx (alguns mandam octet-stream/vazio).
_XLSX_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",
    "application/zip",
    "",
}


async def _ler_xlsx_validado(arquivo: UploadFile) -> bytes:
    """Valida extensão, content-type e tamanho; devolve os bytes. Levanta RegraNegocioError."""
    nome = (arquivo.filename or "").lower()
    if not nome.endswith(".xlsx"):
        raise RegraNegocioError("Envie uma planilha no formato .xlsx.")
    ct = (arquivo.content_type or "").lower()
    if ct not in _XLSX_CONTENT_TYPES:
        raise RegraNegocioError("Tipo de arquivo inválido. Envie uma planilha .xlsx.")
    if arquivo.size is not None and arquivo.size > _MAX_XLSX_BYTES:
        raise RegraNegocioError("Planilha muito grande (máximo 20 MB).")
    conteudo = await arquivo.read(_MAX_XLSX_BYTES + 1)
    if len(conteudo) > _MAX_XLSX_BYTES:
        raise RegraNegocioError("Planilha muito grande (máximo 20 MB).")
    # Assinatura de arquivo ZIP (xlsx é um zip): "PK\x03\x04".
    if not conteudo.startswith(b"PK\x03\x04"):
        raise RegraNegocioError("Arquivo não é uma planilha .xlsx válida.")
    return conteudo


def _processar(caminho: Path):
    """Lê → parseia → valida. Devolve (produtos, inconsistencias)."""
    bruto = ler_staging(caminho, ABAS_CATALOGO)
    produtos = parse_blocos(bruto, ABA_PARA_CATEGORIA)
    inconsistencias = validar(produtos)
    return produtos, inconsistencias


@router.get("/importacao", response_class=HTMLResponse)
def tela_importacao(request: Request, usuario: Usuario = Depends(require_role("admin"))):
    return templates.TemplateResponse(request, "importacao/index.html", {"user": usuario})


@router.post("/importacao/preview", response_class=HTMLResponse)
async def preview_importacao(
    request: Request,
    arquivo: UploadFile,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    conteudo = await _ler_xlsx_validado(arquivo)
    destino = _UPLOAD_DIR / f"upload_{usuario.id}.xlsx"
    destino.write_bytes(conteudo)

    produtos, inconsistencias = _processar(destino)
    exatas = sum(1 for p in produtos for v in p.variacoes if v.estoque_modo == "EXATO")
    aprox = sum(len(p.variacoes) for p in produtos) - exatas
    # Prévia da carga sem gravar (dry-run) para mostrar quantos seriam criados/atualizados.
    resultado = carregar(db, produtos, dry_run=True)

    contexto = {
        "user": usuario,
        "nome_arquivo": arquivo.filename,
        "total_produtos": sum(1 for p in produtos if p.codigo),
        "total_variacoes": sum(len(p.variacoes) for p in produtos),
        "exatas": exatas,
        "aproximadas": aprox,
        "inconsistencias": inconsistencias,
        "resultado": resultado,
    }
    return templates.TemplateResponse(request, "importacao/_preview.html", contexto)


@router.post("/importacao/confirmar", response_class=HTMLResponse)
def confirmar_importacao(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    destino = _UPLOAD_DIR / f"upload_{usuario.id}.xlsx"
    if not destino.exists():
        return templates.TemplateResponse(
            request,
            "importacao/_preview.html",
            {"user": usuario, "erro": "Nenhum arquivo enviado para confirmar."},
            status_code=400,
        )
    produtos, _ = _processar(destino)
    resultado = carregar(db, produtos, dry_run=False)
    destino.unlink(missing_ok=True)
    return templates.TemplateResponse(
        request,
        "importacao/_resultado.html",
        {"user": usuario, "resultado": resultado},
    )
