"""Testes das proteções de segurança adicionadas no hardening.

Cobre: headers HTTP, rate limit de login, sanitização de fórmula em XLSX, validação de
upload de planilha, revogação de sessão (token_version) e o fail-fast de config em prod.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.planilha import linha_segura, sanitizar_celula
from app.core.rate_limit import LimitadorTentativas, limitador_login
from app.main import app


# ---------- Headers de segurança ----------
def test_headers_de_seguranca_presentes() -> None:
    resp = TestClient(app).get("/login")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "same-origin"
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
    # Em dev não há HSTS (usa http).
    assert "Strict-Transport-Security" not in resp.headers


# ---------- Rate limit de login ----------
def test_login_bloqueia_apos_muitas_tentativas() -> None:
    client = TestClient(app)
    email = "inexistente-bruteforce@estrela.local"
    dados = {"email": email, "senha": "errada"}
    # As 5 primeiras falham com 401; a 6ª é bloqueada com 429.
    for _ in range(5):
        r = client.post("/login", data=dados, follow_redirects=False)
        assert r.status_code == 401
    bloqueado = client.post("/login", data=dados, follow_redirects=False)
    assert bloqueado.status_code == 429
    # Limpa o estado do singleton para não afetar outros testes.
    for ip in ("testclient", "desconhecido"):
        limitador_login.limpar(f"{ip}|{email}")


def test_limitador_conta_por_chave() -> None:
    lim = LimitadorTentativas(max_tentativas=2, janela_seg=900)
    assert not lim.bloqueado("a")
    lim.registrar_falha("a")
    lim.registrar_falha("a")
    assert lim.bloqueado("a")
    assert not lim.bloqueado("b")  # outra chave não é afetada
    lim.limpar("a")
    assert not lim.bloqueado("a")


# ---------- Sanitização de fórmula em XLSX ----------
def test_sanitiza_gatilhos_de_formula() -> None:
    assert sanitizar_celula("=1+1") == "'=1+1"
    assert sanitizar_celula("+55") == "'+55"
    assert sanitizar_celula("-2") == "'-2"
    assert sanitizar_celula("@x") == "'@x"
    # Valores normais e não-string passam intactos.
    assert sanitizar_celula("Produto A") == "Produto A"
    assert sanitizar_celula(42) == 42
    assert linha_segura(["=cmd", "ok", 3]) == ["'=cmd", "ok", 3]


# ---------- Validação de upload de planilha ----------
def test_upload_planilha_nao_xlsx_rejeitado() -> None:
    client = TestClient(app)
    login = client.post(
        "/login",
        data={"email": "admin@estrela.local", "senha": "estrela123"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    r = client.post(
        "/importacao/preview",
        files={"arquivo": ("malicioso.txt", b"nao sou xlsx", "text/plain")},
    )
    assert r.status_code == 422


# ---------- Config: fail-fast em produção ----------
def test_config_prod_recusa_jwt_fraco() -> None:
    with pytest.raises(RuntimeError):
        Settings(
            ENV="prod",
            JWT_SECRET="troque-isto",
            DATABASE_URL="postgresql+psycopg://estrela:forte@db:5432/estrela_gestao",
        )


def test_config_prod_recusa_senha_padrao_do_banco() -> None:
    with pytest.raises(RuntimeError):
        Settings(
            ENV="prod",
            JWT_SECRET="x" * 40,
            DATABASE_URL="postgresql+psycopg://estrela:senha@db:5432/estrela_gestao",
        )


def test_config_prod_aceita_segredos_fortes() -> None:
    s = Settings(
        ENV="prod",
        JWT_SECRET="x" * 40,
        DATABASE_URL="postgresql+psycopg://estrela:umaSenhaForte@db:5432/estrela_gestao",
    )
    assert s.ENV == "prod"


# ---------- Revogação de sessão via token_version ----------
def test_reset_senha_invalida_token_antigo(db) -> None:
    """Depois de resetar a senha, o token emitido antes deixa de valer."""
    from app.deps.auth import get_current_user
    from app.models.usuario import Usuario
    from app.services.usuario_service import usuario_service

    u = Usuario(
        nome="Sessao",
        email="sessao-tv@teste.local",
        senha_hash="x",
        perfil="admin",
        token_version=0,
    )
    db.add(u)
    db.flush()

    from app.core.security import criar_token

    token_antigo = criar_token(u.id, u.perfil, extra={"tv": u.token_version})
    usuario_service.resetar_senha(db, u.id, "NovaSenha9!")

    # Simula um request trazendo o token antigo (tv=0) após o reset (tv=1).
    class _Req:
        cookies = {"estrela_token": token_antigo}

    from app.core.errors import NaoAutenticadoError

    with pytest.raises(NaoAutenticadoError):
        get_current_user(_Req(), db)
