# Checklist de Go-Live — Estrela Gestão

> Passo a passo para colocar o sistema em produção no cliente (mini PC + 10 terminais).
> Marque cada item. Não avance de bloco sem concluir o anterior.

---

## Fase 1 — Pré-requisitos (antes de ir ao cliente)

- [ ] Tag de versão estável definida e testada em homologação.
- [ ] Imagem do app builda sem erro (`docker compose -f docker-compose.prod.yml build`).
- [ ] Planilhas reais do cliente coletadas e validadas no ETL (relatório de inconsistências aceito).
- [ ] Segredos gerados (`DB_PASSWORD`, `JWT_SECRET`) e guardados no cofre.
- [ ] Tailscale com conta/ACL prontos para o mini PC.
- [ ] HD externo para backup formatado e identificado.

---

## Fase 2 — Vistoria física (no cliente)

- [ ] **Vistoria elétrica**: tomada aterrada, circuito estável para o mini PC + nobreak.
- [ ] **Nobreak senoidal 1500 VA** instalado; mini PC e switch ligados nele.
- [ ] Cabo USB do nobreak conectado ao mini PC (para shutdown via NUT).
- [ ] **Rede**: switch gigabit posicionado; terminais cabeados ou Wi-Fi estável.
- [ ] Mini PC com **IP fixo** na rede interna.
- [ ] BIOS configurada: **"Power On After Power Loss" = On** (religa após queda).

---

## Fase 3 — Subir a stack

- [ ] Instalar Docker + Docker Compose no mini PC.
- [ ] `git clone` do projeto em `/opt/estrela` (tag de produção).
- [ ] Criar `/opt/estrela/.env.prod` a partir de `.env.prod.example` (preencher segredos).
- [ ] Subir: `docker compose -f docker-compose.prod.yml --env-file .env.prod up -d`.
- [ ] Conferir migrations no log do app: `... logs --tail=100 app` (alembic upgrade head OK).
- [ ] DNS/mDNS: `sistema.local` resolve para o IP fixo do mini PC (roteador ou Avahi).
- [ ] HTTPS interno: `https://sistema.local` abre com certificado interno do Caddy.
- [ ] Instalar/confiar na CA interna do Caddy nas estações (evita aviso de certificado).

---

## Fase 4 — Migração definitiva dos dados (ETL)

- [ ] Backup do estado vazio (referência).
- [ ] Rodar a carga final (idempotente) com as planilhas validadas:
  ```bash
  docker compose -f docker-compose.prod.yml exec app \
    uv run python scripts/import_planilhas.py --file /caminho/planilha.xlsx
  ```
  (rodar antes `--dry-run` para conferir o relatório de inconsistências)
- [ ] Conferir totais: nº de produtos, saldos de estoque iniciais (movimentação tipo importação), clientes.
- [ ] Backup imediato pós-carga: `./scripts/backup-estrela.sh`.

---

## Fase 5 — Terminais em modo aplicativo (10 estações)

Para cada terminal:

- [ ] Navegador instalado (Chrome ou Edge).
- [ ] Criar atalho em **modo aplicativo** apontando para o sistema:
  ```
  chrome --app=https://sistema.local
  ```
  (Edge: `msedge --app=https://sistema.local`)
- [ ] Ícone do atalho na área de trabalho com a marca **Estrela Gestão**.
- [ ] Testar: abre em tela cheia, sem barra de navegação ("cara de programa"), login funciona.
- [ ] PWA instalável: confirmar manifest/ícone e service worker (network-first) ativos.

### Tablet de localização (quiosque)

- [ ] Tablet posicionado no estoque, ligado à rede interna.
- [ ] Abrir a tela `/estoque/localizacao` em **modo quiosque** (tela cheia, sem sair do app).
- [ ] Bloquear navegação para fora; manter ligado/carregando.

---

## Fase 6 — Backup e acesso remoto

- [ ] Cron de backup diário ativo (`backup-estrela.sh`, madrugada) → HD externo.
- [ ] Cron de offsite (`backup-offsite.sh`) configurado (rclone crypt).
- [ ] **Teste de restore real** validado em máquina/instância de teste.
- [ ] Tailscale conectado no mini PC; ACL restrita; portas do roteador **fechadas** à internet.
- [ ] Monitoramento (Uptime Kuma ou ping + alerta) apontando para o mini PC.

---

## Fase 7 — Treinamento por perfil

- [ ] **Admin**: cadastros, importação, usuários, auditoria, relatórios completos.
- [ ] **Vendedor**: novo pedido, busca de estoque, clientes (sem ver preço de custo).
- [ ] **Financeiro**: faturar, contas a receber, baixas, relatórios financeiros.
- [ ] **Funcionário**: entradas de mercadoria, fila de separação, inventário (sem ver custo).
- [ ] Entregar guia rápido por perfil (1 página cada).

---

## Fase 8 — Operação assistida e aceite

- [ ] **1 semana de operação assistida** (presencial/remoto via Tailscale).
- [ ] Acompanhar volume real (80–100 pedidos/dia) sem bloqueios.
- [ ] **Critério de aceite**: 3 dias seguidos de operação real sem bloqueio
      **+** restore de backup validado no local.
- [ ] Termo de aceite assinado pelo cliente.

---

## Pós go-live

- [ ] Agendar teste de restore trimestral.
- [ ] Revisar logs/alertas na primeira semana e ao fim do mês.
- [ ] Atualizar contatos no `runbook-servidor.md`.
