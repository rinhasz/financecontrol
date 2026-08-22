# Receitas, Esporádicas e Resgates — Especificação

**Status:** especificado, **não implementado**. Este documento é o contrato a
ser seguido; nada em `api/` ou `src/` reflete isto ainda.

Até aqui o app só enxerga **saídas recorrentes**. Este documento estende o
modelo em três eixos, pedidos juntos porque são o mesmo problema visto de
ângulos diferentes:

1. **Entradas** — salário, juros, transferências recebidas.
2. **Esporádicas** — o que acontece de vez em quando (uma consulta médica, um
   PIX recebido de alguém) e hoje só existe como "transação sobrando".
3. **Resgates** — dinheiro trazido da aplicação, que **não é renda** e precisa
   ser distinguido entre rotina do mês e objetivo específico.

---

## 1. O modelo conceitual

### Dois eixos independentes

|  | **Fixa** (catalogada) | **Esporádica** |
|---|---|---|
| **Saída** | aluguel, escola, cartão | consulta médica, conserto |
| **Entrada** | salário, juros | PIX recebido, reembolso |

**Natureza** (entrada/saída) diz o *sinal* do dinheiro.
**Recorrência** (fixa/esporádica) diz se o item **gera previsão mensal**.

Essa é a distinção operacional, não uma etiqueta:

- **Fixa** → o mês é aberto com um lançamento previsto. Se o débito/crédito não
  aparecer no extrato, o item entra na seção *"não encontrei"* cobrando
  atenção.
- **Esporádica** → **não gera lançamento nenhum**. Só passa a existir no mês em
  que uma transação for associada a ela. Nunca aparece em *"não encontrei"*,
  porque não ter acontecido é o estado normal.

> **Efeito colateral desejado:** hoje toda despesa ativa gera lançamento todo
> mês, então uma despesa ocasional polui a lista de "não encontrei"
> eternamente. Marcar como esporádica resolve isso, e é uma alternativa mais
> honesta ao `padrao_variabilidade='anual'`, que hoje contorna o sintoma
> devolvendo previsto 0,00.

### Recorrência **não** é o mesmo que `tipo_valor`

Já existem dois campos sobre previsibilidade, e é fácil confundir:

| Campo | Pergunta que responde |
|---|---|
| `tipo_valor` (`fixo`/`variavel`) | O **valor** é sempre o mesmo? |
| `padrao_variabilidade` | **Como** prever o valor do mês? |
| `recorrencia` (**novo**) | **Acontece todo mês?** |

Conta de luz é `variavel` + `fixa`: acontece sempre, com valor diferente.
Consulta médica é `variavel` + `esporadica`.

---

## 2. Créditos: nem todo dinheiro que entra é renda

Levantamento real da competência 08/2026 (21 créditos):

| Descrição | Valor | O que é |
|---|---:|---|
| `FOLHA PAGAMENTO MENSAL` | 14.404,90 | receita fixa |
| `COR JSCP ITUB4` | 411,59 | receita (juros) |
| `TED 237.0001.BRADESCO S` | 700,00 | receita esporádica |
| `PIX TRANSF LUIZ CA03/08` | 5.000,00 | receita esporádica |
| `RESGATE CDB DI` / `LCI` / `LCA` / `COFRINHOS` | ~91.400 | **resgate** |
| `CREDITO CARTAO ITAU` | 5.709,27 | **estorno** |
| `DEV PIX ZIG TECNOLO25/07` | 40,00 | **estorno** |

Somar tudo como receita inflaria a renda de agosto em mais de 6×, e destruiria
o sentido da calculadora de resgate (doc 04) — que existe justamente para dizer
*quanto* resgatar.

### Classificação de crédito

Todo crédito recebe exatamente uma classificação:

| Classificação | Entra no total de renda? | Observação |
|---|:---:|---|
| `receita` | **sim** | única que conta como renda |
| `resgate_mensal` | não | dinheiro trazido para cobrir as contas do mês |
| `resgate_esporadico` | não | resgate com objetivo próprio; exige rótulo |
| `estorno` | não | anula um débito (ver §5) |
| `transferencia` | não | entre contas próprias |

### Os dois resgates

- **`resgate_mensal`** — a rotina: o salário não cobre tudo, você resgata a
  diferença. É a **execução** do número que a calculadora de resgate calcula.
  Fecha o ciclo: a tela passa a mostrar *"precisa resgatar R$ X · já resgatou
  R$ Y · falta R$ Z"*.
- **`resgate_esporadico`** — tem objetivo próprio (carro, eletrodoméstico) e
  **não** deve ser lido como "faltou dinheiro no mês". Carrega um campo
  `objetivo` em texto livre, exibido junto do valor.

A distinção é do usuário, não inferida: mesma descrição (`RESGATE CDB DI`)
serve aos dois casos, e só ele sabe para que resgatou. **O padrão sugerido é
`resgate_mensal`**, por ser o caso comum; o esporádico é uma troca explícita.

---

## 3. Modelo de dados

### Princípio: espelho, não cópia

O lado das receitas é o **espelho exato** do lado das despesas:

| Saídas (existe hoje) | Entradas (novo) |
|---|---|
| `despesa` | `receita` |
| `lancamento` | `lancamento_receita` |
| `transacao.despesa_id` | `transacao.receita_id` |
| `transacao_despesa_regra` | `transacao_receita_regra` |

**O schema é espelhado, mas o código não é duplicado**: o motor de batimento
vira uma função genérica, parametrizada pela natureza (§4).

> **Nota para quem for reconstruir isto do zero:** uma tabela única
> `item_orcamento` com uma coluna `natureza` (`entrada`/`saida`) e uma única
> `lancamento` seria mais enxuta, e é o que eu recomendaria começando hoje. O
> espelho foi escolhido aqui porque renomear `despesa` num banco em produção
> com anos de histórico — tocando 6 módulos, todas as telas e todos os docs —
> é risco desproporcional ao ganho. Se estiver reescrevendo, colapse as duas.

### `receita` (catálogo) — nova

Espelha `despesa` campo a campo.

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | INTEGER PK | |
| nome | TEXT UNIQUE | Nome canônico ("salário", "juros poupança") |
| categoria_id | INTEGER FK → categoria | Categorias de receita convivem com as de despesa na mesma tabela |
| dia_recebimento | INTEGER (1..31, nullable) | Espelho de `dia_vencimento` |
| tipo_valor | TEXT | `fixo` \| `variavel` |
| padrao_variabilidade | TEXT | Mesmos valores de `despesa` |
| valor_padrao | REAL | |
| regras_match | JSON | Mesmo formato de `despesa` |
| recorrencia | TEXT | `fixa` \| `esporadica` |
| ativo | BOOLEAN | |

### `lancamento_receita` — nova

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | INTEGER PK | |
| mes_ref | TEXT | `"AAAA-MM"` |
| receita_id | INTEGER FK → receita | |
| valor_esperado | REAL | Previsto pelo mesmo motor de `_valor_previsto` |
| status | TEXT | `recebido` \| `previsto` \| `nao_encontrado` |
| transacao_id | INTEGER FK → transacao (nullable) | |
| valor_real | REAL (nullable) | |
| data_recebimento | TEXT (nullable) | |

**UNIQUE(mes_ref, receita_id)**.

> `status` usa vocabulário próprio (`recebido`/`previsto`) em vez de
> `pago`/`agendado`. É o mesmo mecanismo — a situação da transação — com o
> nome certo para o lado da entrada.

### Alterações em tabelas existentes

**`despesa`** ganha:

| Campo | Tipo | Padrão | Por quê |
|---|---|---|---|
| recorrencia | TEXT | `'fixa'` | O padrão preserva o comportamento atual de todas as 58 despesas já cadastradas |

**`transacao`** ganha:

| Campo | Tipo | Por quê |
|---|---|---|
| receita_id | INTEGER FK → receita | Espelho de `despesa_id` |
| objetivo | TEXT | Rótulo livre do `resgate_esporadico` ("compra do carro") |
| estorna_transacao_id | INTEGER FK → transacao | Qual débito este crédito anula (§5) |

`transacao.classificacao` passa a aceitar, além dos valores atuais:
`resgate_mensal`, `resgate_esporadico`, `estorno`.

**`transacao_receita_regra`** — espelho exato de `transacao_despesa_regra`,
mesma normalização `padrao_descricao()`, mesmo bônus/penalidade.

### Migração

Padrão já usado no projeto (`init_db()` + `ALTER TABLE` condicional checando
`PRAGMA table_info`, ver doc 01):

1. `CREATE TABLE IF NOT EXISTS` para `receita_catalogo`… — **atenção**: a
   tabela `receita` **já existe** com outro formato (`mes_ref`, `tipo`,
   `valor`, `origem`), sobra do desenho antigo da Função 4 e **está vazia**
   (0 linhas verificadas). Ela é **descartada e recriada** com o formato acima.
   Como está vazia, não há dado a preservar — mas a operação deve conferir
   `SELECT COUNT(*)` antes e abortar se houver linhas, para não destruir dado
   de um banco diferente do que foi inspecionado.
2. `ALTER TABLE despesa ADD COLUMN recorrencia TEXT NOT NULL DEFAULT 'fixa'`.
3. `ALTER TABLE transacao ADD COLUMN receita_id INTEGER REFERENCES receita(id)`,
   idem `objetivo` e `estorna_transacao_id`.
4. Criar `lancamento_receita` e `transacao_receita_regra`.

Nenhum passo altera dado existente de despesa. **Backup do `dev.sqlite` antes**
(o banco tem anos de histórico real).

---

## 4. Batimento de entradas

### Motor único

O batimento de hoje (doc 10) já é genérico na essência: pega lançamentos com
previsão, pega transações sem dono, pontua pares, resolve por score. A única
coisa específica de despesa é **o sinal da transação** e **de qual tabela vêm
os lançamentos**.

A implementação extrai o núcleo para uma função que recebe:

- a lista de lançamentos candidatos (`id`, `nome`, `valor_esperado`,
  `tipo_valor`, `regras_match`, `dia`);
- a lista de transações candidatas;
- o mapa de regras aprendidas.

E devolve os pares. Despesas passam `tipo='debito'`; receitas, `tipo='credito'`.

**Todo o comportamento do doc 10 se aplica igual ao lado das receitas**:
scoring, resolução de conflito por score decrescente, regras aprendidas
(+8/−4), desaprender ao corrigir, casamento por palavra inteira.

### Quem entra

```sql
-- lançamentos de receita candidatos
WHERE l.mes_ref = ? AND l.status = 'nao_encontrado'
  AND r.ativo = 1 AND r.recorrencia = 'fixa'

-- transações candidatas
WHERE data BETWEEN <competência> AND tipo = 'credito'
  AND receita_id IS NULL
  AND classificacao NOT IN ('resgate_mensal','resgate_esporadico','estorno','transferencia')
```

O filtro por `classificacao` é o que impede o motor de tentar casar o salário
com um `RESGATE CDB DI` de valor parecido.

### Efeito de `recorrencia` no lado das despesas

A query de despesas ganha `AND d.recorrencia = 'fixa'` no mesmo lugar onde já
tem `AND d.ativo = 1`, e `_garantir_lancamentos()` deixa de abrir lançamento
para esporádicas. Uma despesa esporádica **ainda pode receber** uma transação
pela seção 3 — o lançamento é criado naquele momento, como já acontece hoje em
`_persistir_par`.

### Tela

A revisão ganha um seletor **Saídas | Entradas** no topo. Cada lado mantém as
**mesmas três seções** do doc 10, com os papéis espelhados:

| # | Saídas | Entradas |
|---|---|---|
| 1 | despesas casadas | receitas casadas |
| 2 | despesas não encontradas → combo de débitos | receitas não encontradas → combo de créditos |
| 3 | débitos sem despesa → combo de despesas | créditos sem receita → combo de receitas |

Na seção 3 das entradas, cada crédito também pode ser **classificado** em vez
de associado: `resgate mensal`, `resgate esporádico` (+ objetivo), `estorno`
(+ qual débito anula) ou `transferência`. Classificar tira o crédito da lista
sem inventar uma receita para ele.

Tudo continua **preview → confirmar**: nada grava antes do "Confirmar tudo".

---

## 5. Estorno (resolve um caso real)

Em 03/08 o extrato traz três linhas de R$ 5.709,27: um débito
`INT PERS BLACK`, um crédito `CREDITO CARTAO ITAU` e outro débito
`PAG BOLETO ITAU UNIBANCO HOLDING S.A.`. É uma cobrança estornada e refeita.
Hoje o app mostra o primeiro débito como despesa disponível para associar, o
que já gerou confusão real.

Classificar o crédito como `estorno` **apontando para o débito que ele anula**
(`estorna_transacao_id`) faz os dois saírem das listas de associação, sem
apagar nada do extrato.

**Sugestão automática, nunca decisão automática:** ao ver um crédito com
valor idêntico ao de um débito não associado, em janela de ±3 dias, a tela
propõe o estorno já preenchido. O usuário confirma. Detectar sozinho é
arriscado — um reembolso legítimo de valor redondo se parece com estorno.

---

## 6. Mês Atual

Uma tela só, dois blocos, fechando em saldo.

```
RECEITAS
  Fixas          salário            previsto 14.404,90   recebido 14.404,90  ✓
                 juros                       ~400,00     recebido    411,59  ✓
  Esporádicas    PIX Luiz                                          5.000,00
  ─────────────────────────────────────────────────────────────────────────
  Total recebido                                                  19.816,49

DESPESAS
  (bloco atual, sem mudança)
  ─────────────────────────────────────────────────────────────────────────
  Total pago / previsto

SALDO DO MÊS       recebido − pago
MOVIMENTAÇÃO       resgate mensal 8.000,00 · esporádico 12.000,00 (carro)
```

Regras de exibição, herdadas do que já vale para despesas (doc 10):

- receita inativa não aparece — **exceto** se teve movimento real no mês
  (esconder falsearia o total);
- `/api/resumo` aplica **o mesmo filtro** da lista, senão o total não bate com
  as linhas exibidas;
- esporádicas aparecem só nos meses em que aconteceram.

### Calculadora de resgate

Passa a ser um ciclo fechado:

```
resgate_necessario = total_despesas + reserva_desejada − saldo_conta − receitas
resgate_ja_feito   = Σ transações classificadas 'resgate_mensal' no mês
falta_resgatar     = max(0, resgate_necessario − resgate_ja_feito)
```

`resgate_esporadico` **não** entra nessa conta — ele tem objetivo próprio e não
é resposta ao déficit do mês.

---

## 7. Catálogo

A tela ganha um seletor **Despesas | Receitas**. O formulário de receita é o
mesmo, com `dia_vencimento` virando `dia_recebimento`.

Ambos os lados ganham o campo **Recorrência** (`fixa`/`esporádica`), e a tela
deixa claro o efeito: *"esporádica não gera previsão mensal — só aparece no mês
em que acontecer"*.

Ordenação (categoria / valor / vencimento) e importação por planilha/colagem
valem igual para receitas.

**"+ Nova despesa" / "+ Nova receita"** criados a partir do batimento nascem
como **`esporadica`** — se fosse recorrente, já estaria no catálogo. O usuário
promove para fixa depois, se for o caso.

---

## 8. Endpoints

| Rota | Método | O que faz |
|------|--------|-----------|
| `/api/receitas/catalogo` | GET | Lista o catálogo de receitas |
| `/api/receitas/catalogo` | POST | Cria/atualiza item |
| `/api/receitas/catalogo/<id>` | DELETE | Desativa |
| `/api/lancamentos` | GET | **Estendido**: devolve `{despesas: [], receitas: []}` |
| `/api/batimento` | POST | **Estendido**: aceita `natureza` (`despesa`\|`receita`\|`ambas`) |
| `/api/batimento/confirmar` | POST | **Estendido**: pares carregam `natureza` |
| `/api/transacoes/classificar` | POST | **Nova**: aplica `resgate_mensal` / `resgate_esporadico` (+objetivo) / `estorno` (+`estorna_transacao_id`) / `transferencia` |
| `/api/resumo` | GET | **Estendido**: `receitas`, `saldo_mes`, `resgate_necessario`, `resgate_ja_feito`, `falta_resgatar` |

O endpoint antigo `/api/receitas` (mes_ref/tipo/valor/origem) é **removido** —
nunca teve tela nem dados.

---

## 9. Ordem de implementação

Cada fase é utilizável sozinha e commitável separadamente.

| Fase | Entrega | Por que nesta ordem |
|---|---|---|
| **1** | Migração + campo `recorrencia` + efeito no batimento de despesas | Menor mudança, valor imediato: tira a despesa ocasional da lista de "não encontrei" |
| **2** | Catálogo de receitas (tabela, endpoints, aba no Catálogo) | Sem isso não há o que casar |
| **3** | Motor de batimento genérico + seletor Saídas/Entradas | O grosso; reusa tudo do doc 10 |
| **4** | Classificação de crédito (resgates, transferência) | Depende da fase 3 para ter onde aparecer |
| **5** | Mês Atual com dois blocos + calculadora de resgate fechada | Consome tudo acima |
| **6** | Estorno com sugestão automática | Independente; último por ser o de menor uso |

---

## 10. O que fica de fora

- **Categorizar receita automaticamente por IA** — o volume é baixo (poucas
  entradas por mês) e o batimento por regra aprendida resolve.
- **Detectar resgate mensal vs esporádico sozinho** — a mesma descrição serve
  aos dois; só o usuário sabe a intenção.
- **Orçamento / meta de gasto por categoria** — outro problema, outra função.
- **Conciliar investimento** (saldo das aplicações após resgate) — é a Função 4
  (doc 05), fora deste escopo.

---

## 11. Verificação

1. `python -m py_compile api/*.py` e `npx tsc --noEmit -p tsconfig.web.json`.
2. **Backup do `dev.sqlite`** antes da migração; conferir que as 58 despesas e
   os lançamentos históricos continuam intactos (`SELECT COUNT(*)` antes e
   depois).
3. Migração idempotente: rodar `init_db()` duas vezes não pode falhar.
4. Importar o extrato real de 08/2026 e conferir, contra a tabela do §2, que os
   21 créditos caem na classificação certa.
5. Conferir que o total de renda de agosto fica em ~R$ 20 mil e **não** em
   ~R$ 111 mil (o erro que este documento existe para evitar).
6. `python tools/avaliar_batimento.py 2026-08` — o lado das despesas **não pode
   regredir** (hoje: 36 casados, 0 erro conhecido).
