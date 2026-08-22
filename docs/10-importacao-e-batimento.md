# Importação de Extrato e Batimento

**Status:** implementado (`api/importacao.py`, aba "Importar Extrato").

Este documento descreve o fluxo como ele **é**, incluindo os problemas reais
que moldaram cada decisão. O doc 02 tem a visão conceitual original; onde os
dois divergirem, este aqui é o que vale.

---

## Mês de competência (não é mês do calendário)

As despesas de um mês começam a ser pagas quando o salário cai — dia 27 do mês
anterior, configurável em `config.dia_recebimento_salario` e editável na tela
Mês Atual. Então `mes_ref='2026-08'` com corte 27 cobre o extrato de
**2026-07-27 a 2026-08-26**.

Implementado em `periodo_competencia(mes_ref, dia_corte)` (`api/db.py`), usado
por todo o batimento. Cuidados que a função já trata: mês anterior com menos
dias que o corte (`min(dia_corte, último dia do mês)`) e `dia_corte=1`, que
ingenuamente produziria uma data com dia 0.

---

## Formatos de extrato

| Formato | Parser | Observação |
|---------|--------|-----------|
| OFX | `parse_ofx` | Mais confiável; lê `STMTTRN` |
| CSV | `parse_csv_content` | Detecta separador e colunas |
| `.xlsx`/`.xlsm` | `openpyxl` via `_sheet_rows_xlsx` | |
| `.xls` (BIFF legado) | `xlrd` via `_sheet_rows_xls` | O Itaú exporta neste formato antigo, **não** é xlsx renomeado — por isso as duas bibliotecas |

**Por que Excel importa:** o Itaú só entrega os **lançamentos futuros/agendados**
via Excel. Sem esse formato não dá para ver o que está agendado e ainda não
debitou.

### Como se detecta "agendado" no Excel do Itaú

O arquivo **não tem coluna de status**. O que existe é uma linha-marcador de
seção com o texto "lançamentos futuros"; tudo que vem depois dela é agendado.
`_parse_excel_sheet` procura esse marcador e só então marca `situacao='agendada'`.

Fora do Excel, a regra de fallback é a data: transação com data futura é
agendada, o resto é efetivada (`_situacao()` em `importar()`).

---

## Importação incremental (reimportar é o fluxo normal)

O usuário reimporta o extrato várias vezes ao longo do mês para pegar o que
apareceu de novo. A importação **nunca pode duplicar** nem perder o que já foi
confirmado.

**Chave de deduplicação:** `(data, descricao, valor)`. Aceita o risco raro de
duas transações reais idênticas no mesmo dia virarem uma só, em troca de nunca
duplicar.

### A armadilha do agendado → pago (bug real, corrigido)

Quando uma transação agendada é finalmente debitada, ela reaparece no extrato
seguinte com **data, descrição e valor idênticos** — só a situação muda. Como a
chave de dedupe é exatamente esses três campos, a versão debitada era
descartada como duplicata e a transação ficava `agendada` para sempre: o
lançamento nunca virava "Pago", por mais vezes que o usuário reimportasse.

**Regra atual:** se a transação já existe como `agendada` e reaparece como
`efetivada`, ela é **atualizada no lugar** em vez de ignorada. Atualizar (e não
inserir outra) preserva o `despesa_id` de um casamento já confirmado.

---

## Batimento — preview, nunca gravação direta

`POST /api/batimento` **não grava nada**. Ele devolve um plano para revisão;
só `POST /api/batimento/confirmar` persiste.

Isso nasceu de um problema concreto: quando o batimento gravava direto, rodar
duas vezes sem revisar fazia parecer que o trabalho tinha sumido (a segunda
rodada só via o que sobrou da primeira). Com preview + "Confirmar tudo", rodar
de novo é inofensivo.

O mesmo padrão preview→confirmar é usado na importação de catálogo (doc 11) e
na associação de emails (doc 08).

### Quem entra no batimento

```sql
-- lançamentos candidatos
WHERE l.mes_ref = ? AND l.status = 'nao_encontrado' AND d.ativo = 1

-- transações candidatas
WHERE data BETWEEN <competência> AND tipo='debito' AND despesa_id IS NULL
```

`d.ativo = 1` importa: sem ele, uma despesa velha e genérica (ex.: "cartao xp",
desativada) vencia a disputa por uma transação contra outra ainda em uso.

### Scoring

Para cada par (lançamento, transação) soma-se:

| Critério | Pontos |
|----------|--------|
| Valor, despesa `fixo` (tolerância ≤ 0,5%) | +3 |
| Valor, despesa `variavel` (tolerância ≤ 15%) | +2 |
| Data dentro da janela do `dia_vencimento` (padrão ±5 dias) | +2 |
| **Todas** as palavras-chave batem | +2 × nº de palavras |
| Alguma palavra-chave bate (parcial) | +1 |

Só pares com **score ≥ 3** viram candidatos.

O peso proporcional ao número de palavras-chave é deliberado: uma despesa com
uma única palavra genérica (só "cartao") não pode vencer sozinha, sem
corroboração de valor ou data.

### Resolução de conflito

Os candidatos são ordenados por score **decrescente** e resolvidos em ordem,
travando lançamento e transação já usados. Montar todos os pares antes de
decidir evita que uma despesa processada primeiro "roube" a transação de outra
com palavra-chave parecida (era o caso de "cartao xp" vs "cartao black").

### Casamento por palavra inteira, não substring

`tem_palavra(alvo, kw)` usa fronteira de palavra (regex `\b`). Com `in` puro
aconteciam falsos positivos reais: "conta agua" casava com `contato@...`, "net"
com "netflix", "iptu casa" com "Casas Bahia". A mesma função é usada na busca
de emails, para o comportamento não divergir entre as duas telas.

### Reavaliação de status já casado

O batimento também traz de volta lançamentos **já confirmados** cuja transação
mudou de situação desde então (tipicamente estavam "Agendado" e o débito caiu).
Sem isso, quem já tinha confirmado ficava preso no status antigo, porque a
busca principal só olha `status='nao_encontrado'`.

Esses vêm com `status_anterior` preenchido, e a tela mostra o status antigo
riscado ao lado do novo.

---

## Telas do fluxo (`Importacao.tsx`)

Três passos: **Selecionar** → **Revisar** → **Concluído**.

Na revisão o usuário pode:
- **"Não é essa despesa"** — troca a despesa sugerida (via `DespesaPicker`,
  com opção de criar despesa nova ali mesmo);
- associar **transações sobrando** (débitos sem despesa correspondente) a uma
  despesa existente ou nova;
- **"Confirmar tudo"** — só aqui grava.

Correções alimentam `docs/07-dicionario-despesas.md` via `_registrar_dicionario()`,
para o histórico de "esse texto do extrato é essa despesa" ficar legível.

---

## Reset do mês

`POST /api/batimento/resetar` volta todos os lançamentos do mês para
`nao_encontrado`, desfaz os vínculos com transações e limpa `linha_digitavel`.

**Não apaga** as transações importadas nem o histórico de `importacao` — a
intenção é refazer a conferência sem precisar reenviar arquivo.

---

## Endpoints

| Rota | Método | O que faz |
|------|--------|-----------|
| `/api/importacao` | POST | Recebe arquivo (multipart), deduplica, insere novas, promove agendada→efetivada |
| `/api/batimento` | POST | Preview do casamento — não grava |
| `/api/batimento/confirmar` | POST | Grava os pares revisados |
| `/api/batimento/corrigir` | POST | Corrige um casamento já gravado |
| `/api/batimento/resetar` | POST | Zera a conferência do mês |
| `/api/transacoes` | GET | Lista transações do mês |

---

## Mês Atual — o que aparece

`/api/lancamentos` cria automaticamente os lançamentos do mês a partir do
catálogo **ativo** e devolve a lista.

**Filtro de ativas:** despesa desativada não aparece — *exceto* se teve
movimento real no mês (status diferente de `nao_encontrado`). Esconder uma
despesa desativada que já foi paga tiraria um pagamento real da lista e do
total, e o mês fecharia com número errado.

`/api/resumo` aplica **o mesmo filtro**; se somasse o que a lista não mostra, o
total da tela não bateria com as linhas exibidas.
