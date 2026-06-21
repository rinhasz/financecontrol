# Modelo de Dados (Relacional)

Banco: SQLite (arquivo local). Cada mês é gerado do catálogo — não existe "copiar aba".

---

## categoria

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | INTEGER PK | |
| nome | TEXT UNIQUE | Ex.: Casa/Utilidades, Saúde, Cartões, Filhos/Educação, Funcionária, Financiamento, Outros |
| ativo | BOOLEAN | Permite desativar sem deletar |

---

## despesa (catálogo — cadastrado uma vez, não copiado por mês)

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | INTEGER PK | |
| nome | TEXT UNIQUE | Nome canônico normalizado |
| categoria_id | INTEGER FK → categoria | |
| dia_vencimento | INTEGER (1..31, nullable) | Dia esperado de débito |
| tipo_valor | TEXT | `fixo` \| `variavel` |
| padrao_variabilidade | TEXT | `fixa` \| `variavel_sazonal` \| `variavel_nao_sazonal` \| `reajuste_anual` \| `anual` |
| valor_padrao | REAL | Valor de referência |
| regras_match | JSON | `{ palavras_chave: [], faixa_valor: [min, max], janela_dias: N, banco: "" }` |
| ativo | BOOLEAN | Despesas inativas não geram lançamentos |

---

## lancamento (instância de uma despesa num mês)

Substitui a cópia de aba. Chave única por mês + despesa.

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | INTEGER PK | |
| mes_ref | TEXT | Formato `"AAAA-MM"` |
| despesa_id | INTEGER FK → despesa | |
| valor_esperado | REAL | Previsão do motor (doc 03) ou valor herdado do mês anterior; editável pelo usuário |
| status | TEXT | `pago` \| `agendado` \| `nao_encontrado` |
| transacao_id | INTEGER FK → transacao (nullable) | Preenchido após batimento |
| valor_real | REAL (nullable) | Valor efetivo após casamento |
| data_pagamento | DATE (nullable) | Data real do débito |

**UNIQUE(mes_ref, despesa_id)**

---

## transacao (linha do extrato importado)

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | INTEGER PK | |
| data | DATE | Data do débito/crédito |
| descricao | TEXT | Descrição original do extrato |
| valor | REAL | Negativo = débito |
| tipo | TEXT | `debito` \| `credito` |
| situacao | TEXT | `efetivada` \| `agendada` |
| banco_origem | TEXT | |
| classificacao | TEXT | `recorrente` \| `extra` \| `receita` \| `transferencia` \| `investimento` |
| despesa_id | INTEGER FK → despesa (nullable) | Preenchido após batimento |
| import_id | INTEGER FK → importacao | |

---

## importacao

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | INTEGER PK | |
| banco | TEXT | |
| formato | TEXT | `ofx` \| `csv` \| `pdf` |
| arquivo | TEXT | Nome original do arquivo |
| data_import | DATETIME | |
| periodo_ini | DATE | |
| periodo_fim | DATE | |

---

## receita (Função 4)

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | INTEGER PK | |
| mes_ref | TEXT | `"AAAA-MM"` |
| tipo | TEXT | `salario` \| `juros` \| `outro` |
| valor | REAL | |
| origem | TEXT | Descrição da fonte |

---

## posicao_investimento (Função 4 — snapshot mensal)

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | INTEGER PK | |
| mes_ref | TEXT | `"AAAA-MM"` |
| banco | TEXT | |
| produto | TEXT | |
| classe | TEXT | `RF` \| `RV` \| `Prev` \| `FGTS` |
| valor | REAL | Valor ao final do mês |
| valor_mes_anterior | REAL | Para calcular rentabilidade |
| rentabilidade | REAL | `valor / valor_mes_anterior - 1` |

---

## Notas de seed

- `Base Histórica` (1.219 linhas, Fev/23–Jun/26) → seed de `lancamento` (mapear `conta_norm` → `despesa_id`).
- `Catálogo Despesas` (45 itens) → seed de `despesa` + `categoria`.
- `Classificação Var` → seed de `despesa.padrao_variabilidade` e `despesa.valor_padrao`.
