# Modelo de Dados (Relacional)

Banco: SQLite (arquivo local `dev.sqlite`). Cada mês é gerado do catálogo —
não existe "copiar aba". O schema vive em `api/db.py` (`SCHEMA` + `init_db()`);
este documento é o espelho legível dele.

**Migrações:** o projeto não usa ferramenta de migração. `init_db()` roda o
`CREATE TABLE IF NOT EXISTS` e depois aplica `ALTER TABLE` condicionais
(checando `PRAGMA table_info`) para colunas adicionadas depois — é assim que
`linha_digitavel` e `tipo_codigo` entraram sem quebrar bancos existentes.
Ao acrescentar coluna nova, siga esse mesmo padrão.

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
| linha_digitavel | TEXT (nullable) | Código de pagamento achado por email — boleto **ou** Pix (ver doc 08) |
| tipo_codigo | TEXT (nullable) | `boleto` \| `pix` — qual código está em `linha_digitavel`; define o rótulo do botão em Mês Atual |

**UNIQUE(mes_ref, despesa_id)** — é o que permite `INSERT OR IGNORE` para
"garantir que o lançamento do mês existe" sem duplicar.

`linha_digitavel`/`tipo_codigo` são preenchidos pela busca de emails e **não
alteram status de pagamento** — servem só para copiar o código na hora de pagar.

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
| formato | TEXT | `ofx` \| `csv` \| `xls` \| `xlsx` \| `xlsm` |
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

## transacao_despesa_regra (aprendizado descrição → despesa)

O que o batimento aprendeu das confirmações anteriores. Antes disso as
correções iam só para `docs/07` (arquivo legível) e nada as lia de volta — o
usuário corrigia os mesmos erros todo mês.

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | INTEGER PK | |
| padrao | TEXT | Descrição reduzida ao que se repete (ver abaixo) |
| despesa_id | INTEGER FK → despesa | |
| acertos | INTEGER | Quantas vezes foi confirmada |
| criado_em | DATETIME | |

**UNIQUE(padrao, despesa_id)** — o mesmo padrão pode ter mais de uma despesa
(a mesma escola cobra mensalidade e material; o mesmo destinatário recebe
salário e vale transporte), e nesses casos quem decide é o valor.

`padrao` vem de `padrao_descricao()`: a descrição sem datas nem sequências
longas de dígitos, que mudam a cada mês. `PIX TRANSF MARIA J28/07` e
`PIX TRANSF MARIA J01/08` viram ambas `pix transf maria j`.

---

## email_despesa_regra (aprendizado remetente → despesa)

Gravada toda vez que o usuário confirma a associação de um boleto/Pix achado
por email. Na busca seguinte, o mesmo remetente já vem com a despesa
pré-preenchida — é o que sustenta o botão "Repetir mês anterior" (doc 08).

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | INTEGER PK | |
| remetente | TEXT UNIQUE | Endereço de email do emissor (ex.: `boleto@jaime.com.br`) |
| despesa_id | INTEGER FK → despesa | |
| criado_em | DATETIME | |

Espelho legível (só para consulta humana): [09-dicionario-emails.md](09-dicionario-emails.md).
A fonte de verdade é a tabela.

---

## config (chave/valor)

| Chave | Padrão | Uso |
|-------|--------|-----|
| `reserva_desejada` | `5000` | Colchão na calculadora de resgate (doc 04) |
| `saldo_conta` | `0` | Saldo atual informado pelo usuário |
| `dia_recebimento_salario` | `27` | Define o mês de competência (doc 10) — despesas de um mês começam a ser pagas quando o salário cai, no dia 27 do mês anterior |

---

## Notas de seed

- `Base Histórica` (1.219 linhas, Fev/23–Jun/26) → seed de `lancamento` (mapear `conta_norm` → `despesa_id`).
- `Catálogo Despesas` (45 itens) → seed de `despesa` + `categoria`.
- `Classificação Var` → seed de `despesa.padrao_variabilidade` e `despesa.valor_padrao`.
