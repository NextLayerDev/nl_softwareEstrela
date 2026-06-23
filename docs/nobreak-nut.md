# Nobreak + NUT — Shutdown Gracioso — Estrela Gestão

> Configuração do **NUT (Network UPS Tools)** no mini PC para que, em queda de energia,
> o servidor desligue de forma **graciosa** antes da bateria do nobreak acabar — e religue
> sozinho quando a energia voltar. Protege a integridade do PostgreSQL.

---

## 1. Hardware esperado

- **Nobreak senoidal 1500 VA** (ex.: NHS / Intelbras) com porta **USB** de comunicação.
- Cabo USB do nobreak ligado ao mini PC.
- Mini PC, switch e roteador alimentados pelo nobreak.

---

## 2. Pré-requisitos no BIOS (crítico)

- **"Power On After Power Loss" = On** (ou "Restore on AC Power Loss" = Power On).
  Assim, quando a energia retorna, o mini PC **liga sozinho** e o Docker (`restart: always`) sobe a stack.
- Desabilitar suspensão automática / hibernação no SO.

---

## 3. Rede do servidor

- Mini PC com **IP fixo** na rede interna (ex.: `192.168.0.10`). Necessário para:
  - `sistema.local` resolver sempre para o mesmo IP.
  - O `upsmon` local falar com o `upsd` em `localhost`.

---

## 4. Instalar o NUT

```bash
sudo apt-get update
sudo apt-get install -y nut
```

Modo de operação: **standalone** (um único servidor monitora seu próprio nobreak).

---

## 5. Arquivos de configuração

> Caminhos típicos em Debian/Ubuntu: `/etc/nut/`. Ajuste o `driver`/`port` ao seu modelo
> (use `nut-scanner -U` para detectar o nobreak USB).

### 5.1 `/etc/nut/nut.conf`

```ini
MODE=standalone
```

### 5.2 `/etc/nut/ups.conf`

```ini
[estrela]
    driver = usbhid-ups
    port = auto
    desc = "Nobreak Estrela 1500VA"
    # Se o modelo não for reconhecido, testar driver/vendorid específicos:
    # vendorid = ....
    # productid = ....
```

### 5.3 `/etc/nut/upsd.conf`

```ini
# Apenas localhost — não expor o NUT na rede.
LISTEN 127.0.0.1 3493
```

### 5.4 `/etc/nut/upsd.users`

```ini
[monuser]
    password = TROQUE_ESTA_SENHA
    upsmon master
```

### 5.5 `/etc/nut/upsmon.conf`

```ini
# Monitora o nobreak local. "master" = este host desliga o nobreak no fim.
MONITOR estrela@localhost 1 monuser TROQUE_ESTA_SENHA master

# Mínimo de fontes de energia necessárias.
MINSUPPLIES 1

# Comando de desligamento do sistema (shutdown gracioso).
SHUTDOWNCMD "/sbin/shutdown -h +0"

# Tempo (s) que aguarda após bateria crítica antes de forçar o desligamento.
FINALDELAY 5

# Notificações (opcional): chamar upssched para avisos.
NOTIFYCMD /usr/sbin/upssched
```

> **Política de shutdown**: o NUT dispara o desligamento quando o nobreak sinaliza **bateria baixa**
> (evento `LB` — Low Battery), garantindo que o SO desligue antes da bateria zerar. Não esperar a
> bateria acabar: configurar o nobreak (ou o threshold) para um nível de segurança.

---

## 6. Habilitar e iniciar

```bash
sudo systemctl enable --now nut-server
sudo systemctl enable --now nut-monitor

# Conferir comunicação com o nobreak
upsc estrela@localhost
# Esperado: linhas como battery.charge, ups.status (OL = on line / OB = on battery)
```

---

## 7. Sequência em queda de energia

```
Energia cai
   │
   ▼
Nobreak entra em bateria (ups.status = OB)  ──► sistema segue rodando
   │
   ▼  (bateria atinge nível baixo: ups.status = OB LB)
upsmon dispara SHUTDOWNCMD
   │
   ▼
SO desliga graciosamente (Docker para os containers; Postgres encerra limpo)
   │
   ▼
Energia volta ──► BIOS "Power On After Power Loss" liga o mini PC
   │
   ▼
Docker (restart: always) sobe db + app + caddy; app roda migrations e atende
```

---

## 8. Teste de validação

1. Com o sistema rodando, **tirar o nobreak da tomada** (energia da rua).
2. Confirmar `ups.status = OB` em `upsc estrela@localhost`.
3. Aguardar até bateria baixa (ou simular) e observar o **shutdown automático**.
4. Religar a energia e confirmar que o mini PC **liga sozinho** e a stack sobe.
5. Validar acesso a `https://sistema.local` e integridade do banco.

> Fazer este teste no go-live e revisar anualmente (baterias degradam).

---

## 9. Observações

- Trocar as senhas placeholder (`TROQUE_ESTA_SENHA`) por valores reais e guardá-las no cofre.
- Não expor o NUT na rede (`LISTEN 127.0.0.1`): o monitoramento é local.
- Registrar o modelo do nobreak e o driver usado neste documento após a instalação.
