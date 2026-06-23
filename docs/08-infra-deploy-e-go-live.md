# 08 — Infraestrutura, Deploy e Go-Live · Estrela Gestão

> Responsável: DevOps / Tech Lead. Depende do doc `02` (fundação).
> O sistema roda **localmente no cliente**, em um **mini PC**, acessado por até 10 terminais via rede interna.

---

## 1. Hardware (cenário enxuto definido)

Para 10 usuários e 80–100 pedidos/dia, o workload é leve — o que importa é confiabilidade básica e backup.

| Item | Especificação | Faixa (jun/2026) |
|---|---|---|
| **Mini PC** | Ryzen 5/7, 16–32 GB RAM, SSD 512 GB | R$ 1.800–3.200 |
| **Nobreak senoidal 1500 VA** | NHS/Intelbras, USB p/ shutdown | R$ 1.300–1.800 |
| **HD externo 1 TB** | backup local (rotação) | R$ 300–450 |
| **Switch gigabit 8 portas** | rede dos terminais | R$ 150–280 |
| **Total** | | **≈ R$ 3.500–5.700** |

Trade-off aceito: sem RAID/ECC. Mitigação: **backup automático diário** + cópia offsite quando houver internet;
se o mini PC falhar, compra-se outro e restaura-se o backup em < 2 h.

---

## 2. Topologia

```
[10 terminais: navegador em modo app] ── switch gigabit ── [MINI PC / SERVIDOR]
                                                            Caddy (443, HTTPS interno)
                                                            ├─ App (Gunicorn+Uvicorn, FastAPI)
                                                            └─ PostgreSQL 16 (Docker)
                                                            Backup diário → HD externo + offsite
                                                            Tailscale → manutenção remota NextLayer
[Nobreak alimentando mini PC + switch + roteador]
```

- Terminais acessam `https://sistema.local` (DNS do roteador ou mDNS/Avahi). Zero instalação nas estações.
- Mini PC com **IP fixo**, BIOS para **religar após queda de energia**, nobreak com shutdown via USB (NUT).

---

## 3. Deploy com Docker Compose (produção)

`docker-compose.prod.yml` com três serviços: `db` (Postgres 16), `app` (imagem do projeto, Gunicorn +
UvicornWorker), `caddy` (proxy HTTPS interno).

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: estrela
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: estrela_gestao
    volumes: ["pgdata:/var/lib/postgresql/data"]
    restart: always

  app:
    build: .
    command: gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 3 -b 0.0.0.0:8000
    environment:
      DATABASE_URL: postgresql+psycopg://estrela:${DB_PASSWORD}@db:5432/estrela_gestao
      JWT_SECRET: ${JWT_SECRET}
      ENV: prod
    depends_on: [db]
    restart: always

  caddy:
    image: caddy:2
    ports: ["443:443", "80:80"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data
    depends_on: [app]
    restart: always

volumes: { pgdata: {}, caddy_data: {} }
```

`Caddyfile` (HTTPS interno automático para `sistema.local`):
```
sistema.local {
    reverse_proxy app:8000
    tls internal
}
```

**Dockerfile**: base `python:3.12-slim`, instalar dependências do sistema do WeasyPrint
(libpango, libcairo), copiar app, instalar via uv, expor 8000.

---

## 4. Atualizações

- Build/tag de versão → no servidor: `docker compose pull && docker compose up -d` (via Tailscale).
- **Rollback** imediato para a tag anterior se algo quebrar.
- Migrations: rodar `alembic upgrade head` no container do app após subir nova versão (entrypoint ou comando manual).
- Janela combinada com o cliente (fora do horário comercial).

---

## 5. Backup (regra 3-2-1 adaptada ao enxuto)

| Camada | O quê | Frequência | Como |
|---|---|---|---|
| 1. Diário local | `pg_dump` comprimido | diário (madrugada) | cron no host → HD externo |
| 2. Retenção | manter 14 dias | — | rotação de arquivos |
| 3. Mídia externa | cópia para HD externo (e segundo HD alternado, um fora do prédio) | semanal | script + rotina do cliente |
| 4. Offsite | cópia criptografada para nuvem | diário (quando há internet) | rclone (Backblaze B2/S3) |

- **RPO**: até 24 h (pior caso). **RTO**: < 2–4 h (restaurar em qualquer máquina via Docker + dump).
- **Testar restore** a cada trimestre (restore real, não só "backup verde").
- Nobreak + NUT: queda de energia → shutdown gracioso → religa sozinho.

```bash
# exemplo de backup (cron diário)
docker exec estrela-db pg_dump -U estrela estrela_gestao | gzip > /backup/estrela_$(date +%F).sql.gz
```

---

## 6. Segurança

- Sistema **não exposto à internet**: portas fechadas no roteador; acesso externo só via **Tailscale** (ACL).
- Senhas argon2; JWT em cookie httpOnly; HTTPS interno (Caddy `tls internal`).
- Princípio do menor privilégio (RBAC, doc 03/05/06).
- Backup offsite **criptografado**.
- LGPD: dados de clientes minimizados; auditoria de operações; contrato operador (NextLayer) × controlador (Estrela).

---

## 7. Go-live (no cliente)

Sequência:
1. Vistoria elétrica/rede; instalar mini PC, nobreak, switch.
2. Subir stack (Docker Compose prod) + Caddy + Tailscale + monitoramento (Uptime Kuma ou ping + alerta).
3. **Migração definitiva** dos dados (ETL, doc 04) — rodar carga final após validar inconsistências com o cliente.
4. Configurar **modo aplicativo nos 10 terminais** (atalho `--app`, ícone Estrela Gestão).
5. Posicionar o **tablet do estoque** com a tela de localização em modo quiosque.
6. **Treinamento por perfil** (Admin, Vendedor, Financeiro, Funcionário).
7. **1 semana de operação assistida**; testar restore de backup no local.

Critério de aceite: 3 dias seguidos de operação real sem bloqueio + restore de backup validado.

---

## 8. Definition of Done do marco 08

- [ ] `docker-compose.prod.yml` + `Caddyfile` + `Dockerfile` funcionando (app + db + caddy).
- [ ] HTTPS interno em `https://sistema.local`; terminais acessam por nome.
- [ ] Backup diário automatizado + rotina offsite documentada; restore testado.
- [ ] Tailscale para manutenção; portas fechadas à internet.
- [ ] Procedimento de atualização e rollback documentado (`runbook-servidor.md`).
- [ ] Mini PC com religamento automático e nobreak com shutdown gracioso.
- [ ] 10 terminais em modo aplicativo + tablet de localização em quiosque.
- [ ] Operação assistida concluída e aceite assinado.
