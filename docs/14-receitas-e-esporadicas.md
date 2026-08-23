# Receitas, Esporádicas e Resgates — Especificação

**Status:** **implementado** (fases 1 a 5). Recorrência fixa/esporádica,
catálogo de receitas com `tipo`, batimento de créditos com motor único, Mês
Atual com renda e movimentação, ciclo do resgate fechado e estorno com sugestão
automática. Onde o código divergir deste documento, ver §12.

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

- **Fixa** → o mês é aberto com um **lançamento** previsto. Se o débito/crédito
  não aparecer no extrato, o item entra na seção *"não encontrei"* cobrando
  atenção.
- **Esporádica** → **não gera lançamento nenhum, nunca**. O registro é a
  própria transação, com `despesa_id`/`receita_id` preenchido. Nunca aparece em
  *"não encontrei"*, porque não ter acontecido é o estado normal.

> **Por que esporádica não usa lançamento.** `lancamento` tem
> `UNIQUE(mes_ref, despesa_id)` — um por mês. Sem previsão a fazer, o lançamento
> também não agregaria nada: a transação já tem valor e data. Então o item
> esporádico dispensa a camada inteira.

### Terceiro eixo: `varios_por_mes`

"Pode acontecer mais de uma vez no mesmo mês?" **não** é a mesma pergunta que
"acontece todo mês?". A escola cobra mensalidade e material — fixa e várias; o
aluguel é fixo e um só; uma consulta médica é esporádica e podem ser três.

A primeira versão deduzia isso da recorrência (esporádica ⇒ várias, fixa ⇒
uma), o que impedia uma despesa **fixa** de receber duas cobranças no mesmo mês.
Agora é campo próprio do catálogo, marcado pelo usuário:

| `recorrencia` | `varios_por_mes` | Exemplo | Onde o 2º movimento é gravado |
|---|:---:|---|---|
| fixa | não | aluguel | não acontece (o item sai da lista após casar) |
| fixa | **sim** | escola: mensalidade + material | na transação |
| esporádica | não | IPTU | na transação |
| esporádica | **sim** | consulta médica | na transação |

**Onde o par é gravado depende de haver lançamento livre**, e não mais da
recorrência: o primeiro casamento ocupa o lançamento do mês (é ele que carrega
a previsão); do segundo em diante o registro é a própria transação, e o Mês
Atual mostra a linha extra a partir dela.

`varios_por_mes` decide **se o item continua na lista de associação** depois de
já ter casado. Os que continuam vêm no campo `sempre_disponiveis` de cada lado
do batimento — separado de `nao_encontrados`, que é "ainda não aconteceu".

Migração: o valor inicial reproduz o comportamento anterior
(`varios_por_mes = 1` onde `recorrencia='esporadica'`), então nada muda até o
usuário marcar.

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

### A classificação mora no catálogo de receitas

Todo item do catálogo de receitas tem um **tipo**, e é o tipo que decide se
aquilo é renda. Assim **associar já classifica** — não existe um segundo
mecanismo de "classificar crédito" paralelo ao batimento. O crédito segue o
mesmo caminho de sempre: casa com um item do catálogo, e o item diz o que ele é.

| `receita.tipo` | Conta como renda? | Exemplo no extrato |
|---|:---:|---|
| `salario` | **sim** | `FOLHA PAGAMENTO MENSAL` |
| `juros` | **sim** | `COR JSCP ITUB4`, `REMUNER BASICA POUP AUT` |
| `reembolso` | **sim** | `PIX TRANSF THALITA27/07` |
| `outra` | **sim** | `TED 237.0001.BRADESCO S` |
| `resgate_mensal` | não | `RESGATE CDB DI` para cobrir o mês |
| `resgate_esporadico` | não | `RESGATE LCI DI` para comprar um carro |
| `estorno` | não | `CREDITO CARTAO ITAU`, `DEV PIX ZIG` |
| `transferencia` | não | entre contas próprias |

`conta_como_renda` é **derivado do tipo**, não um campo — dois campos que
precisam concordar acabam discordando. A regra é: tudo que não for
`resgate_*`, `estorno` ou `transferencia`.

Consequência prática: `resgate mensal` e `resgate esporádico` são **itens
normais do catálogo** de receitas. O mensal é `fixa` (acontece quase todo mês);
o esporádico é `esporadica`.

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
| **tipo** | TEXT | `salario` \| `juros` \| `reembolso` \| `outra` \| `resgate_mensal` \| `resgate_esporadico` \| `estorno` \| `transferencia` — ver §2 |
| recorrencia | TEXT | `fixa` \| `esporadica` |
| ativo | BOOLEAN | |

### `lancamento_receita` — nova

Só existe para receitas **fixas** (§1).

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
| receita_id | INTEGER FK → receita | Espelho de `despesa_id`. Para esporádicas, é o **único** registro do fato (§1) |
| objetivo | TEXT | Rótulo livre por ocorrência: "compra do carro". Fica aqui, e não no catálogo, porque o objetivo muda a cada resgate |
| estorna_transacao_id | INTEGER FK → transacao | Qual débito este crédito anula (§5) |

`transacao.classificacao` **não muda**. A classificação de negócio agora vem de
`receita.tipo` via `receita_id`; duplicá-la na transação criaria dois campos
que precisam concordar.

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
WHERE data BETWEEN <competência> AND tipo = 'credito' AND receita_id IS NULL
```

Não há filtro por classificação: um `RESGATE CDB DI` é candidato legítimo,
porque "resgate mensal" é um item do catálogo como qualquer outro. Quem impede
o resgate de roubar o casamento do salário é o mesmo mecanismo que já protege
as despesas — palavras-chave, valor, data e regra aprendida.

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

Na seção 3 das entradas, associar é o **único** gesto — não há um segundo botão
de "classificar". O combo lista os itens do catálogo de receitas agrupados por
tipo, e escolher `resgate mensal` já classifica o crédito como resgate:

```
  Salário
    salário                      previsto 14.404,90
  Juros
    juros poupança
  Resgate
    resgate mensal
    resgate esporádico           › pede objetivo ao confirmar
  Estorno
    estorno de cartão            › pede qual débito anula
  + Nova receita
```

Escolher um item cujo tipo é `resgate_esporadico` abre um campo de **objetivo**;
`estorno`, um seletor do débito anulado (§5). São os dois únicos tipos que
pedem informação extra na hora de associar.

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
RECEITAS                                          previsto      realizado
  Fixas        salário              [salário]     14.404,90     14.404,90 ✓
               juros poupança       [juros]          ~400,00       411,59 ✓
  Esporádicas  PIX Luiz             [outra]                      5.000,00
               TED Bradesco         [outra]                        700,00
  ───────────────────────────────────────────────────────────────────────
  Renda do mês                                                  20.516,49

DESPESAS
  (bloco atual, sem mudança)
  ───────────────────────────────────────────────────────────────────────
  Total pago / previsto

SALDO DO MÊS     renda − pago

MOVIMENTAÇÃO (não é renda)
  Resgate mensal                                                 8.000,00
  Resgate esporádico   compra do carro                          12.000,00
  Estorno              anula INT PERS BLACK                      5.709,27
```

O rótulo entre colchetes é o `tipo` do item — é ele que decide de qual lado da
linha o valor entra. **Renda e movimentação nunca somam juntas.**

Regras de exibição, herdadas do que já vale para despesas (doc 10):

- receita inativa não aparece — **exceto** se teve movimento real no mês
  (esconder falsearia o total);
- `/api/resumo` aplica **o mesmo filtro** da lista, senão o total não bate com
  as linhas exibidas;
- esporádicas aparecem só nos meses em que aconteceram — e como não têm
  lançamento (§1), a lista do mês é a **união** de `lancamento_receita` (fixas)
  com as `transacao` que têm `receita_id` de item esporádico.

### Calculadora de resgate

Passa a ser um ciclo fechado:

```
renda_do_mes       = Σ receitas cujo tipo conta como renda   (§2)
resgate_necessario = total_despesas + reserva_desejada − saldo_conta − renda_do_mes
resgate_ja_feito   = Σ créditos associados a item de tipo 'resgate_mensal'
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
| `/api/transacoes/<id>/detalhe` | PATCH | **Nova**: grava `objetivo` (resgate esporádico) e `estorna_transacao_id` (estorno). Só isso — a classificação vem de `receita.tipo` |
| `/api/resumo` | GET | **Estendido**: `receitas`, `saldo_mes`, `resgate_necessario`, `resgate_ja_feito`, `falta_resgatar` |

O endpoint antigo `/api/receitas` (mes_ref/tipo/valor/origem) é **removido** —
nunca teve tela nem dados.

---

## 9. Ordem de implementação

Cada fase é utilizável sozinha e commitável separadamente.

| Fase | Entrega | Por que nesta ordem |
|---|---|---|
| **1** ✅ | Migração + campo `recorrencia` + efeito no batimento de despesas | Menor mudança, valor imediato: tira a despesa ocasional da lista de "não encontrei" |
| **2** ✅ | Catálogo de receitas com `tipo` (tabela, endpoints, aba no Catálogo) | Sem isso não há o que casar — e o `tipo` já nasce junto, porque é ele que classifica |
| **3** ✅ | Motor de batimento genérico + seletor Saídas/Entradas | O grosso; reusa tudo do doc 10 |
| **4** ✅ | Mês Atual com dois blocos + calculadora de resgate fechada | Consome tudo acima |
| **5** ✅ | Estorno com sugestão automática | Independente; último por ser o de menor uso |

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

---

## 12. Desvios da especificação na fase 1

Registrados aqui porque a spec vale como contrato — divergir sem dizer seria
pior que divergir.

**"+ Nova despesa" continua nascendo `fixa`**, e não `esporadica` como o §7
previa. A spec argumentava "se fosse recorrente, já estaria no catálogo", o que
não se sustenta enquanto o catálogo está sendo construído aos poucos: uma
despesa recorrente criada a partir do batimento nasceria sem previsão nenhuma,
e o usuário não teria como perceber. Fica `fixa`, e marcar esporádica é um
gesto explícito no Catálogo.

**Virar esporádica não apaga lançamento nenhum.** A primeira versão apagava os
lançamentos vazios da despesa — e o teste mostrou que apagava os de **todos os
meses**, não só o corrente, destruindo a série histórica que alimenta
`_valor_previsto` (39 lançamentos viraram 0 num caso real). Corrigido: nada é
apagado, e a consulta de `/api/lancamentos` apenas **esconde** o lançamento
vazio de esporádica.

**Dupla contagem, encontrada no teste.** Uma despesa que vira esporádica
*depois* de já ter casado no mês apareceria duas vezes — pelo lançamento antigo
e pela transação. `_esporadicas_do_mes()` ignora transação que já tenha
lançamento apontando para ela.

**Fase 2 criou o schema inteiro do lado da receita**, incluindo
`lancamento_receita`, `transacao_receita_regra` e as colunas novas de
`transacao` — que só serão usadas na fase 3. Foi de propósito: uma migração só,
em vez de duas, evita a possibilidade de um banco meio migrado.

**`/api/receitas` antigo removido.** Era o CRUD do formato velho da tabela e
apontaria para colunas que não existem mais. `/api/resumo` deixou de somar
daquela tabela e passou a somar `lancamento_receita` filtrando pelos tipos que
contam como renda — na fase 2 isso devolve 0, exatamente como antes, porque a
tabela antiga estava vazia.

**Importar catálogo por planilha não existe no lado da receita.** A operação
desativa tudo que não está no arquivo, o que faz sentido para 58 despesas vindas
de uma planilha e nenhum para meia dúzia de entradas.

**Fase 3 extraiu o motor em vez de copiá-lo.** `api/motor_batimento.py` contém
o scoring, a resolução de conflito e as regras aprendidas, parametrizados por um
dicionário `NATUREZAS` que concentra tudo que difere entre os dois lados. O
lado da despesa foi medido antes e depois com `tools/avaliar_batimento.py`:
resultado idêntico (ACERTO 28 | ERRO 6 | PERDIDO 0), que era o critério para
aceitar a refatoração.

**O payload ficou genérico** (`item_id`/`item_nome` no lugar de `despesa_*`),
para a tela ter um caminho só em vez de dois componentes espelhados.

**"+ Nova receita" criada no batimento nasce com tipo `outra`.** Chutar o tipo
poderia classificar um resgate como renda — exatamente o erro que o campo existe
para evitar. O tipo é escolha explícita no Catálogo.

**`avaliar_batimento.py` teve de ser atualizado** junto: a resposta agora é
aninhada por natureza, e o gabarito de `docs/07` é só do lado da despesa.

**Fase 4: "saldo do mês" é realizado contra realizado.** A spec dizia
`recebido − pago`, e a primeira implementação somou `renda − pago` — misturando
renda ainda **prevista** com gasto **já pago**, o que produz um saldo que não
existe em lugar nenhum. Corrigido para `renda_recebida − pago`, e o card foi
rotulado **"Recebido − pago"** para não haver dúvida sobre o que ele mede.
"Vai fechar o mês?" é outra pergunta, e quem responde é a calculadora de
resgate logo abaixo.

**A decisão de qual bloco exibe cada linha está na tela; a soma vem do
backend.** O frontend repete a lista de tipos que não são renda só para separar
as linhas em dois blocos — os totais (`renda`, `movimentacao`, `faltaResgatar`)
chegam prontos de `/api/resumo`. Se a regra fosse aplicada duas vezes para
somar, os dois lugares poderiam discordar e a tela mostraria um total que não
bate com as próprias linhas.

**`/api/lancamentos` passou a devolver `{despesas, receitas}`** em vez de um
array. Consumidor único (Mês Atual), então não houve compatibilidade a manter —
mas `/api/resumo` **manteve** as chaves antigas `receitas` e `resgate` como
apelidos de `renda` e `resgateNecessario`.

---

## Fase 5 — o que a sugestão de estorno revelou

A heurística (mesmo valor, ±3 dias, débito ainda sem despesa) achou **três**
candidatos em agosto/2026, e só um é estorno de verdade:

| Crédito | Débito apontado | Veredito |
|---|---|---|
| `CREDITO CARTAO ITAU` +5.709,27 | `INT PERS BLACK` −5.709,27 | **estorno** — cobrança refeita como boleto |
| `PIX TRANSF INGRID` +260,00 | `PIX TRANSF Ingrid` −260,00 | provável devolução |
| `PIX TRANSF MARCIA` +1.288,18 | `PAG BOLETO LELLO` −1.288,18 | **não é estorno** — é reembolso de alguém que pagou a parte dela |

O terceiro caso é exatamente o falso positivo previsto na especificação: um
reembolso legítimo é indistinguível de um estorno pelo par valor+data. Se a
regra aplicasse sozinha, um débito real sumiria da conferência e a despesa
correspondente ficaria órfã. **Por isso a sugestão aparece rotulada na linha e
exige confirmação.**

### Desvios da fase 5

**Não existe `PATCH /api/transacoes/<id>/detalhe`.** O §8 previa esse endpoint
para gravar `objetivo` e `estorna_transacao_id`. Eles são gravados dentro do
`/api/batimento/confirmar`, junto do par, para "Confirmar tudo" continuar sendo
a **única** escrita do fluxo — um segundo caminho de gravação quebraria o
preview→confirmar que vale em toda a tela.

**Cada lado do batimento devolve também `esporadicos`.** Item esporádico não
tem lançamento, logo não aparece em `nao_encontrados` — e sem essa lista à
parte, "estorno" e "resgate esporádico" seriam **inalcançáveis** na seção 3.
Eles não são filtrados por "já associado" como os fixos, porque podem receber
mais de uma transação no mesmo mês.

---

## Correções depois do uso real

**A correção da seção 1 oferecia o catálogo errado.** Na aba Entradas, o
"Não é essa receita" listava **despesas** — a tela carregava só
`api.catalogo.list()`. Corrigir um crédito ali produziria um vínculo sem
sentido. Agora os dois catálogos são carregados e a tela usa o do lado corrente.

**O rótulo "+ Nova despesa" era fixo** no `DespesaPicker`, então aparecia
também na aba de receitas. Virou a prop `novaLabel`.

**O passo "Revisar" não mostrava que o extrato traz entradas.** Ganhou a
contagem "N saídas · N entradas", para ficar claro antes do batimento que os
dois lados vieram no mesmo arquivo.

**Vínculo sem lançamento também aparece na seção 1.** A 2ª ocorrência de um
item "mais de um por mês" — e todo item esporádico — mora só na transação, sem
lançamento. Sem aparecer entre as casadas, um vínculo errado desses ficaria
intocável, exatamente o problema já corrigido para os casamentos gravados. Vêm
marcados `sem_lancamento`, para a tela **não** devolvê-los à seção 2 quando o
usuário troca o item: não há previsão para onde voltar.

**O combo da seção 3 sai do catálogo, não das listas do batimento.** O batimento
é uma foto do instante em que rodou: um item criado depois dele não estaria em
`nao_encontrados` (item fixo só entra ali quando ganha lançamento) e ficaria
invisível até rodar de novo. Como a tela recarrega o catálogo ao voltar a ficar
visível, criar no Catálogo e vir associar aqui funciona na hora. As listas do
batimento seguem servindo para o **valor previsto**, que só existe quando há
lançamento no mês. Isso tornou `sempre_disponiveis` redundante — foi removido.

**O selo de status da seção 1 tinha texto fixo** (`Pago`/`Agendado`), então um
salário já creditado aparecia como "Agendado" na aba de entradas. O vocabulário
correto dos dois lados já existia em `STATUS_LABEL` e não estava sendo usado ali.

---

## Estorno é obrigatório dizer o que anula

Um crédito associado a item de tipo `estorno` **precisa** apontar o débito que
anula, e a informação é sempre gravada em `transacao.estorna_transacao_id`.

Sem o alvo, o estorno não estorna nada: o crédito vira uma entrada solta e o
débito continua contando como despesa paga — os dois erros que a classificação
existe para evitar.

**Validado no backend, não só na tela.** `/api/batimento/confirmar` recusa o par
com HTTP 400 e mensagem nomeando o item. A tela cobra antes (bloqueia
"Confirmar tudo" e aponta quantos faltam), mas é a gravação que precisa ser
confiável — a regra não pode depender de o caminho da interface ter sido
percorrido.

Antes disso a obrigatoriedade existia só na associação manual da seção 3. Um
estorno que o batimento casasse sozinho, ou que surgisse de um "Não é essa
receita", era gravado sem alvo.

### O débito estornado é desfeito

Confirmar um estorno **desfaz o vínculo do débito anulado**: o lançamento volta
para `nao_encontrado` e a transação perde o `despesa_id`. Se o banco devolveu o
dinheiro, o pagamento não aconteceu — deixar a despesa marcada como paga faria o
mês fechar com um pagamento que não existe. A despesa volta para a seção 2 e
pode ser casada com a cobrança de verdade (no caso real, o boleto refeito).

Por isso o seletor oferece **todos** os débitos do período, não só os sem
despesa: uma cobrança já casada também pode ser estornada.

### Como se cadastra um estorno (o fluxo não era descobrível)

O caminho existia mas ninguém adivinhava: era preciso saber que o registro
passa por uma *receita* — contraintuitivo, já que estorno não é renda — e
depois escolher o item no combo e procurar o débito anulado. Quatro passos, dois
deles invisíveis.

**Uma vez, no Catálogo → Receitas:** criar um item com **Tipo = Estorno**
(recorrência esporádica). É o mesmo item para todos os estornos; não se cria um
por ocorrência.

**A cada estorno, na Importação → Entradas → seção 3:** o crédito suspeito
mostra um botão **`é estorno de "X"?`** que faz tudo num clique — associa ao
item de estorno e já grava o débito anulado. O caminho manual pelo combo
continua existindo para quando a heurística não sugere nada.

Enquanto não existir nenhuma receita com Tipo = Estorno, a linha diz o que
falta em vez de simplesmente não oferecer a ação:
*"parece estornar X — falta uma receita com Tipo = Estorno"*.

---

## O vínculo do estorno é com a DESPESA

Quem estorna anula um **gasto**, e é contra o gasto que o valor se compensa. Só
que, na hora de classificar o crédito, o usuário pode ainda não ter classificado
o débito — então há dois caminhos para o mesmo vínculo:

| Escolha | Grava | Quando |
|---|---|---|
| **Despesa** | `transacao.estorna_despesa_id` | já se sabe qual despesa foi estornada |
| **Lançamento** | `transacao.estorna_transacao_id` | a linha do extrato ainda não foi classificada |

**Pela linha, a despesa é descoberta por propagação.** Se o débito já tem dono,
`estorna_despesa_id` é preenchido na hora. Se não tem, é preenchido depois, no
momento em que a linha for classificada (`_persistir_par`). O resultado é o
mesmo **em qualquer ordem** que o usuário faça as duas associações — e o vínculo
com a despesa fica gravado para sempre, que é o que a consolidação consome.

A tela oferece as duas opções lado a lado, na própria classificação da entrada.
Havendo sugestão automática, ela é de uma *linha* — então "Lançamento" já vem
escolhido e preenchido.

### Reversão: débito estornado continua sendo despesa

Uma versão anterior **desfazia** o vínculo do débito ao confirmar o estorno, na
ideia de que "estornado = não aconteceu". Está desfeito. O gasto e a devolução
são **dois fatos reais**, e o que se quer é que se anulem no fechamento — não
que um deles suma do extrato. Classificar o débito é inclusive o que revela a
despesa a que o estorno se refere.

Pelo mesmo motivo, débito estornado voltou a ser candidato do batimento.

---

## Duas visões do mês

| | Analítica | Consolidada |
|---|---|---|
| Pergunta | como o mês aconteceu | quanto cada item custou |
| Ocorrências repetidas | uma linha cada | somadas, com marca `3x` |
| Estorno | aparece nos **dois** blocos: o gasto nas saídas, a devolução nas entradas | abatido da despesa que anula |
| Serve para | conferir contra o extrato | fechar o mês |

Na consolidada, despesa **integralmente** estornada não aparece: custou zero, e
mostrá-la zerada seria ruído. Estorno parcial deixa só o líquido. Estorno cuja
despesa não teve gasto neste mês produz líquido **negativo** — voltou mais do
que saiu, o que é verdade e é exibido em verde.

Estorno já abatido de uma despesa **não** aparece de novo no bloco de entradas
da consolidada; contá-lo duas vezes inflaria a renda. Estorno ainda sem despesa
conhecida continua ali, como movimentação, até a propagação acontecer.

`GET /api/consolidado?mes=` faz o agrupamento no backend, para o total e as
linhas nunca poderem discordar. A invariante verificada é a de sempre: **a soma
das linhas exibidas é igual ao total exibido**.

---

## Por que o estorno "não funcionava"

O app decide tudo pelo **`tipo`** do item, não pelo nome. O usuário tinha um item
chamado `estorno` tipado como `reembolso` — para o app era uma receita comum:
não pedia alvo, não abatia nada e ainda contava como renda. Do lado de quem usa,
"escolhi estorno e não aconteceu nada".

Duas correções, além de ajustar o dado:

**O tipo agora aparece no combo de classificação** das entradas: cada opção é
`nome · Tipo`. É onde a decisão acontece, e era o único lugar onde a divergência
entre nome e tipo ficava invisível. Duas rodadas de "mude o Tipo no Catálogo"
não resolveram — sinal de que o problema era a tela não mostrar, não o usuário
não saber.

**A validação passou a rodar sobre o lote inteiro antes de gravar qualquer
coisa.** Validar dentro do laço fazia um único estorno sem alvo abortar no meio:
a transação era desfeita (nada parcial, isso estava certo), mas o usuário perdia
a revisão inteira sem saber qual linha era a culpada. Agora a resposta diz
quantos e quais, e é explícita: *"Nada foi gravado"*.

### A consolidada agrupa por categoria

O agrupamento por categoria é fixo; a ordenação escolhida (**maior valor** ou
**alfabética**) vale **dentro** de cada uma. Ordenar globalmente por valor
desfaria o agrupamento, que é justamente o que dá sentido à leitura — "quanto
foi Saúde neste mês".

As categorias vêm com **maior gasto primeiro**: a ordem alfabética delas
raramente é o que se quer olhar antes.

Toda linha que soma mais de um lançamento ganha um **`+`** e abre o analítico
ali mesmo, com data, descrição e valor de cada ocorrência — estorno inclusive,
em negativo, para o líquido se explicar sozinho. As linhas vêm no próprio
payload de `/api/consolidado`: quem consolidou precisa poder abrir sem uma
segunda chamada, e sem depender de a outra visão estar carregada.
