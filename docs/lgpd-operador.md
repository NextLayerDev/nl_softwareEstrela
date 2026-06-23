# LGPD — Responsabilidades do Operador — Estrela Gestão

> Documento de referência sobre o tratamento de dados pessoais no Estrela Gestão, conforme a
> **Lei nº 13.709/2018 (LGPD)**. Define papéis, princípios aplicados e medidas técnicas.
> **Não substitui parecer jurídico** — é a base operacional acordada entre as partes.

---

## 1. Papéis (art. 5º, VI e VII)

| Papel | Quem | Responsabilidade |
|---|---|---|
| **Controlador** | **Estrela** (empresa cliente) | Decide sobre as finalidades e os meios do tratamento. É a titular da relação com os dados de seus clientes/fornecedores. |
| **Operador** | **NextLayer** | Realiza o tratamento **em nome do controlador**, conforme instruções. Fornece e mantém o sistema, infraestrutura e backups. |

A NextLayer (operadora) **não usa os dados para finalidade própria**. Trata apenas o necessário para
operar, manter e dar suporte ao sistema, sob instrução da Estrela (controladora).

---

## 2. Dados pessoais tratados (minimização — art. 6º, III)

O sistema é de **estoque e pedidos** (B2B atacado). Dados pessoais são limitados ao necessário:

- **Clientes**: nome/razão social, documento (CNPJ/CPF), contato, condição de pagamento, endereço de entrega.
- **Usuários internos**: nome, login, perfil de acesso, senha (hash argon2).
- **Operações**: quem fez o quê (auditoria) — vinculado ao usuário interno.

**Não** são tratados dados sensíveis (art. 5º, II). Caso o cliente queira incluir, deve ser reavaliado.

> Princípio aplicado: coletar e reter **apenas o mínimo** para a finalidade de gestão comercial.

---

## 3. Medidas de segurança (art. 46)

- **Sem exposição à internet**: sistema roda em rede interna; acesso remoto de manutenção só via **Tailscale** (rede privada, ACL).
- **Controle de acesso (RBAC)**: 4 perfis (Admin, Vendedor, Financeiro, Funcionário); menor privilégio. Preço de custo oculto para Vendedor/Funcionário.
- **Autenticação**: senhas com **argon2**; sessão via **JWT em cookie httpOnly**.
- **HTTPS interno**: Caddy com `tls internal` (criptografia no tráfego da rede local).
- **Backup criptografado offsite**: rclone com remote **crypt**; chave guardada em cofre (sem ela o backup é irrecuperável).
- **Backup local** em HD externo, com rotação; restauração testada periodicamente.

---

## 4. Auditoria e rastreabilidade (art. 37 — registro das operações)

- Tabela de **auditoria** registra antes/depois (JSONB) em produtos, pedidos, estoque e financeiro,
  com **usuário, data/hora e origem**.
- **Estoque é append-only**: toda alteração de saldo gera movimentação imutável (não há `UPDATE` direto).
- Logs do servidor e do app disponíveis para investigação de incidentes.

---

## 5. Direitos dos titulares (art. 18)

A Estrela (controladora) é o ponto de contato dos titulares. A NextLayer (operadora) **apoia tecnicamente**:

- **Acesso / correção**: dados de clientes editáveis na tela de Clientes (perfil autorizado).
- **Eliminação / anonimização**: mediante solicitação formal da controladora; considerar obrigações
  legais de retenção fiscal antes de excluir (pedidos/financeiro).
- **Portabilidade**: exportação de dados (XLSX/relatórios) disponível no sistema.

---

## 6. Incidentes de segurança (art. 48)

- Em caso de incidente com risco a titulares, a **NextLayer comunica a Estrela imediatamente**.
- A Estrela (controladora) avalia a comunicação à **ANPD** e aos titulares.
- Coletar evidências (logs §4 do runbook), conter, restaurar de backup íntegro se necessário.

---

## 7. Retenção e término

- Dados retidos enquanto durar a relação comercial e as obrigações legais (fiscais/contábeis).
- Ao encerrar o contrato, a NextLayer **devolve ou elimina** os dados conforme instrução da Estrela,
  salvo guarda exigida por lei (art. 16).
- Backups offsite seguem a mesma política; a chave de criptografia é destruída no encerramento.

---

## 8. Resumo das obrigações por parte

| Tema | Estrela (Controladora) | NextLayer (Operadora) |
|---|---|---|
| Finalidade do tratamento | Define | Segue instruções |
| Atendimento ao titular | Responsável | Apoio técnico |
| Comunicação à ANPD | Responsável | Notifica a Estrela |
| Segurança técnica | Acompanha | Implementa e mantém |
| Backup/criptografia | Acompanha | Executa |
| Acesso de funcionários internos | Define perfis | Implementa RBAC |
