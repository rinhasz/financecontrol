# Função 1 — Controle de Contas Mensais

## Fluxo completo

```
Cadastro único        → Catálogo de despesas (seed: aba "Catálogo Despesas")
         ↓
Abertura do mês       → Gerar lancamentos do catálogo ativo
         ↓
Importação de extrato → OFX / CSV / PDF → tabela transacao
         ↓
Batimento automático  → Casa lancamento ↔ transacao (verde/azul/branco)
         ↓
Conferência           → Usuário revisa, resolve conflitos, edita valores
         ↓
Resgate do dia 1      → Calculadora (doc 04)
```

---

## Cores → Status (equivalência com a planilha)

| Cor | Status | Significado |
|-----|--------|-------------|
| Verde | `pago` | Casou com transação efetivada/já debitada |
| Azul | `agendado` | Casou com transação agendada/futura (extrato traz futuros) |
| Branco | `nao_encontrado` | Sem correspondência; mantém valor herdado/previsto |

Direção do batimento: **planilha → extrato** (cada lançamento previsto busca sua transação, não o contrário).

---

## Importação de extrato

Confiabilidade: **OFX > CSV > PDF**

### OFX
- Parsear `STMTTRN`: campos `DTPOSTED`, `TRNAMT`, `MEMO`/`NAME`, `FITID`.
- Futuros: `data > hoje` → `situacao = agendada`.

### CSV
- Mapear colunas (data, descrição, valor).
- Manter **perfil de importação por banco** (salvo em config/DB).
- `situacao`: flag explícita se houver; senão data futura ⇒ agendada.

### PDF
- Extração de texto linha a linha (pdfplumber).
- Exige revisão manual — menor confiabilidade.

---

## Motor de Batimento

Para cada `lancamento L` (status inicial = `nao_encontrado`):

**Passo 1 — Filtrar candidatas**
```
transacao WHERE
  mes == mes_ref(L)
  AND tipo == 'debito'
  AND despesa_id IS NULL  -- ainda não casada
```

**Passo 2 — Scoring (3 critérios)**

| Critério | Lógica |
|----------|--------|
| **Valor** | `tipo_valor=fixo` → tolerância ≤ 0,5%; `variavel` → tolerância ≤ ±15% (configurável por despesa) |
| **Data** | `\|dia(T) - dia_vencimento\|` ≤ janela (padrão ±5 dias, configurável por despesa) |
| **Texto** | `T.descricao` contém alguma `palavra_chave` da despesa (case/acento-insensível) |

**Passo 3 — Decisão**
- Melhor candidata **acima do limiar**: vincular `transacao_id`, setar `valor_real` e `data_pagamento`.
  - `situacao == efetivada` → `status = pago`
  - `situacao == agendada` → `status = agendado`
- **Conflito** (2+ plausíveis ou nenhuma): deixar `nao_encontrado`, marcar para revisão.

---

## Regras de valor herdado

- `valor_esperado` começa como previsão do motor (doc 03); na falta, herda do mês anterior.
- Editável pelo usuário a qualquer momento.
- Cada mês tem seu próprio conjunto de lançamentos — **não existe cópia de aba**.

---

## Resumo do mês (sem números mágicos)

| Campo | Cálculo |
|-------|---------|
| Total | `SUM(lancamento.valor_esperado)` onde status qualquer |
| Pago | `SUM(valor_real)` onde `status = pago` |
| Agendado | `SUM(valor_esperado)` onde `status = agendado` |
| A vencer | `SUM(valor_esperado)` onde `status = nao_encontrado` |
| Reserva | Colchão definido pelo usuário |
| A resgatar | Ver doc 04 |

A planilha atual tem fórmulas com números fixos embutidos (`=D2+D39+209,9+340*2+D23`). O app elimina isso.
