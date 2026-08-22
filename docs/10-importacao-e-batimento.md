# Importação de Extrato e Batimento

**Status:** implementado (`api/importacao.py`, aba "Importar Extrato").

Este documento descreve o fluxo como ele **é**, incluindo os problemas reais
que moldaram cada decisão. O doc 02 tem a visão conceitual original; onde os
dois divergirem, este aqui é o que vale.

---

## Mês de competência (não é mês do calendário)

As despesas de um mês começam a ser pagas quando o salário cai — **dia 26 do mês
anterior**, configurável em `config.dia_recebimento_salario` e editável na tela
Mês Atual. Então `mes_ref='2026-08'` normalmente cobre **2026-07-26 a
2026-08-25**.

### Antecipação por fim de semana e feriado

Salário que cairia em sábado, domingo ou feriado é creditado **antes**, no
último dia útil — e é a data do crédito que abre a competência. Em 2026, o 26 de
julho é um domingo, então `mes_ref='2026-08'` na verdade começa em
**2026-07-24** (sexta).

O fim **não** é um dia fixo: é sempre a véspera do próximo crédito. Se o 26 do
mês seguinte for antecipado, esta competência acaba antes. É o que faz os meses
se encaixarem sem buraco nem sobreposição — propriedade verificada mês a mês.

`feriados_bancarios(ano)` cobre os oito feriados nacionais fixos mais os móveis
em que o banco não opera (Carnaval segunda e terça, Sexta-feira Santa, Corpus
Christi), calculados a partir da Páscoa (`_pascoa`, algoritmo gregoriano
anônimo). **Feriado municipal não entra**: varia por cidade e o app não sabe
onde o usuário está. Se o salário cair num feriado só da cidade, o ajuste erra
por um dia — o dia continua editável na tela.

Implementado em `periodo_competencia(mes_ref, dia_corte)` (`api/db.py`), usado
por todo o batimento. Cuidados que a função já trata: mês anterior com menos
dias que o corte (`min(dia_corte, último dia do mês)`) e `dia_corte=1`, que
ingenuamente produziria uma data com dia 0.

A tela Mês Atual **mostra o intervalo calculado** ao lado do dia do salário: a
regra de antecipação é invisível demais para ficar só no código.

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

## Importação substitui o período (reimportar é o fluxo normal)

O usuário reimporta o extrato várias vezes ao longo do mês. **O extrato é a
verdade sobre o intervalo que ele cobre**: importar apaga as transações daquele
intervalo e reinsere as do arquivo.

```
DELETE FROM transacao WHERE data BETWEEN <primeira> AND <última do arquivo>
                        AND banco_origem = <banco selecionado>
```

O recorte por banco não é detalhe: sem ele, importar o extrato de outra conta
apagaria as transações desta no mesmo período.

### Por que não é mais incremental

A versão anterior deduplicava por `(data, descricao, valor)` e só sabia
**acrescentar**. Nada removia nada, e o lixo se acumulava para sempre:

- um PIX **agendado para 12/08 que foi cancelado** continuava aparecendo como
  transação disponível para associar, semanas depois de o extrato ter deixado
  de mencioná-lo;
- quando o banco reescrevia a descrição ao debitar, a versão antiga virava uma
  linha órfã e o batimento podia casar a despesa com ela — mostrando "Agendado"
  num mês em que o extrato já dizia pago.

O Itaú **muda o texto** nessa transição na maioria dos casos:

| Em "lançamentos futuros" | Depois de debitado |
|---|---|
| `Agendado` | `FINANC IMOBILIARIO 038/397` |
| `PAG TIT 662992535000` | `PAG BOLETO EDIFICIO LINCOLN GARDEN` |
| `PAG TIT BANCO 237` | `PAG BOLETO ESTAPAR` |
| `DA  CLARO BL/IT 77712744` | `DA  CLARO S.A. 77712744` |
| `PIX QRS SUL AMERICA` | `PIX QRS SUL AMERICA10/08` |

Substituir o período resolve os dois problemas de uma vez, e é uma regra só em
vez de duas heurísticas de reconciliação. Medido em agosto/2026: 93 transações
no banco contra 91 no extrato — as 2 sobrando eram lixo de importações antigas.

### Preservar o que o usuário confirmou

Casamento confirmado é trabalho manual e tem que atravessar a troca. Depois de
reinserir, cada transação nova reencontra seu vínculo:

1. por chave exata `(data, descricao, valor)`;
2. senão por `(data, valor)` — o que sobrevive quando o banco reescreve a
   descrição. **Só quando não há ambiguidade**: com duas candidatas, chutar
   arrastaria o vínculo para a despesa errada.

O `lancamento` é repontado para o novo `id` (com status, `valor_real` e
`data_pagamento` recalculados a partir da linha nova).

**Transação que sumiu do extrato** (agendamento cancelado, estorno, correção do
banco): o lançamento volta para `nao_encontrado` em vez de apontar para algo
que não existe mais. É lossy de propósito — se o extrato não menciona mais o
débito, ele não aconteceu.

> **Cuidado:** importar um extrato **antigo** faz o período voltar ao que aquele
> arquivo diz, removendo o que só existe no mais recente. É a consequência
> direta de "o extrato é a verdade", e o efeito é reversível reimportando o
> arquivo novo — mas os vínculos das transações removidas ficam em aberto para
> o batimento refazer.

## Batimento — preview, nunca gravação direta

`POST /api/batimento` **não grava nada**. Ele devolve um plano para revisão;
só `POST /api/batimento/confirmar` persiste.

Isso nasceu de um problema concreto: quando o batimento gravava direto, rodar
duas vezes sem revisar fazia parecer que o trabalho tinha sumido (a segunda
rodada só via o que sobrou da primeira). Com preview + "Confirmar tudo", rodar
de novo é inofensivo.

O mesmo padrão preview→confirmar é usado na importação de catálogo (doc 11) e
na associação de emails (doc 08).

### Um motor, duas naturezas

O casamento é o **mesmo** para saídas e entradas: despesa casa com débito,
receita casa com crédito, e scoring, resolução de conflito e regras aprendidas
são idênticos. Duplicar o motor significaria manter dois scorings em sincronia
para sempre, então ele vive em `api/motor_batimento.py`, parametrizado.

Tudo que difere está num dicionário só, `NATUREZAS`:

| | despesa | receita |
|---|---|---|
| catálogo | `despesa` | `receita` |
| lançamento | `lancamento` | `lancamento_receita` |
| FK | `despesa_id` | `receita_id` |
| dia | `dia_vencimento` | `dia_recebimento` |
| sinal da transação | `debito` | `credito` |
| regras aprendidas | `transacao_despesa_regra` | `transacao_receita_regra` |
| status casado | `pago` / `agendado` | `recebido` / `previsto` |

`POST /api/batimento` devolve os dois lados de uma vez
(`{despesa: {...}, receita: {...}}`), e a tela mostra um por vez com um seletor
**Saídas / Entradas**. "Confirmar tudo" grava os dois — o usuário revisa na
mesma passada, e confirmar duas vezes seria fácil de esquecer.

O payload usa nomes genéricos (`item_id`, `item_nome`, `item_id_sugerido`) em
vez de `despesa_*`, para a tela ter um caminho só.

### Quem entra no batimento

```sql
-- lançamentos candidatos
WHERE l.mes_ref = ? AND l.status = 'nao_encontrado'
  AND d.ativo = 1 AND d.recorrencia = 'fixa'

-- transações candidatas  (tipo e FK vêm da natureza)
WHERE data BETWEEN <competência> AND tipo=<debito|credito> AND <fk> IS NULL
```

Não há filtro por classificação no lado do crédito: um `RESGATE CDB DI` é
candidato legítimo, porque "resgate mensal" é um item do catálogo como qualquer
outro (doc 14). Quem impede o resgate de roubar o casamento do salário são as
mesmas defesas de sempre — palavra-chave, valor, data e regra aprendida.

`d.ativo = 1` importa: sem ele, uma despesa velha e genérica (ex.: "cartao xp",
desativada) vencia a disputa por uma transação contra outra ainda em uso.

`d.recorrencia = 'fixa'` porque esporádica nem lançamento tem (doc 14) — o
filtro fica explícito para o caso de sobrar lançamento anterior à migração.
Uma despesa esporádica **ainda recebe** transação pela seção 3; o vínculo é
gravado na própria transação, sem criar lançamento.

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

### Regras aprendidas (o que mais pesa)

Toda confirmação grava `padrão de descrição → despesa` em
`transacao_despesa_regra`. No batimento seguinte:

| Situação | Efeito no score |
|----------|-----------------|
| A despesa é dona conhecida daquele padrão | **+8** |
| O padrão tem dono conhecido, mas é **outra** despesa | **−4** |

O `padrao` ignora datas e números de documento (`padrao_descricao()`), porque
a descrição muda todo mês: `PIX TRANSF MARIA J28/07` e `...J01/08` são o mesmo
padrão.

**Por que isso existe:** as correções do usuário iam só para `docs/07`, um
arquivo legível que **nada lia de volta**. Medido em agosto/2026: de 10
casamentos para os quais já havia correção registrada, **7 repetiam o mesmo
erro**. Fechar o ciclo levou o mês de 18 para 31 casamentos, sem erro conhecido.

Dois cuidados que a medição obrigou a acrescentar:

- **Penalidade não vale quando o valor bate na mosca.** Uma mesma descrição
  serve a várias despesas (mensalidade e material da mesma escola); a segunda
  precisa conseguir casar antes de ser aprendida.
- **O bônus não atropela valor.** Se o valor da transação encaixa exato em
  outra despesa e destoa muito desta, o +8 não é aplicado — foi o que impedia
  `vale transporte` de receber o valor que era dele, tomado por `salário` só
  porque dividem a descrição. A condição inclui "existe outra despesa que
  encaixa no valor" de propósito: com previsto desatualizado (ou placeholder
  de R$ 1,00), a regra ainda é a melhor evidência disponível.

**Desaprender** é tão importante quanto aprender: ao rejeitar uma sugestão, a
regra da despesa recusada é apagada. Sem isso a regra errada disputaria com a
certa para sempre.

Para medir depois de mexer no scoring:

```bash
python tools/avaliar_batimento.py 2026-08
```

Ele usa as correções de `docs/07` como gabarito e reporta ACERTO / ERRO /
PERDIDO. Atenção ao viés: esse gabarito também alimenta as regras, então ele
mede se o ciclo funciona — **não** se generaliza para descrições nunca vistas.

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

### As três seções da revisão (ordem fixa)

| # | Seção | O que a linha mostra | O que o combo oferece |
|---|-------|----------------------|-----------------------|
| 1 | Despesas casadas | despesa + transação + status | — (só "Não é essa despesa") |

| 2 | Despesas ativas que não encontrei | despesa + previsto | as **transações** sobrando |
| 3 | Transações sem despesa | data + descrição + valor | as **despesas** ainda em aberto (+ nova) |

As seções 2 e 3 são espelho uma da outra: a mesma associação, atacada por
pontas opostas. Quem sabe qual despesa está faltando começa pela 2; quem olha
um débito estranho no extrato começa pela 3.

Ambas alimentam a mesma função (`associarPar`) e comem da mesma lista — o par
montado some das duas e aparece na seção 1, então as listas encolhem juntas.

**Invariante das duas combos: só aparece o que ainda não tem par.** "Ainda não
associado" tem três fontes, e as três são respeitadas:

1. não tem dono gravado no banco — o backend filtra `despesa_id IS NULL`;
2. não está num casamento sugerido nesta rodada — `tx_sugerida` / `lanc_sugerido`;
3. não foi usado num par montado na tela — conferido no render contra
   `detalhes`, porque essa parte muda a cada clique.

Só a primeira vem pronta do servidor. Depender só dela deixava a invariante
valer por acidente: qualquer caminho novo que mexesse em `detalhes` sem mexer
nas outras listas furaria a regra — foi o que aconteceu com "Não é essa
despesa", que trocava a despesa do casamento sem tirá-la da seção 2 nem
devolver a que tinha sido liberada. Por isso o filtro é derivado de `detalhes`
no render, e não mantido à mão em cada ação.

Trocar a despesa de um casamento devolve a antiga para a seção 2 (a não ser que
ela tenha casado com outra transação) — daí `valor_esperado` viajar junto em
`detalhes`, senão não haveria previsto para exibir na volta.

### A seção 1 inclui o que já foi gravado

Não só as sugestões da rodada: `detalhes` traz também os casamentos **já
persistidos** do mês (`ja_gravados`, o complemento exato de `defasados`).

Sem isso, um vínculo confirmado errado ficava **intocável**. Depois de gravado,
a despesa sai da busca por `status='nao_encontrado'` e a transação sai do
`despesa_id IS NULL` — o par não aparecia em nenhuma das três seções e a única
saída era resetar o mês inteiro. Foi assim que `PIX QRS MERCADO PAG01/08`
(R$ 143,88) ficou preso em `Teatro Maju` sem que houvesse como perceber ou
desfazer pela tela.

Essas linhas vêm marcadas `ja_gravado` e com o rótulo "gravado". **"Confirmar
tudo" não as reenvia** enquanto não forem trocadas: regravar o mesmo par não
muda nada no banco e ainda contaria como mais um acerto da regra aprendida,
inflando o contador a cada rodada do batimento.

O contador `total` passou a ser `len(detalhes) + len(nao_encontrados)` — antes
saía de `lancamentos`, que não enxerga defasados nem já gravados, e o placar
mentia conforme essas listas cresciam.

O combo da seção 3 lista **só as despesas da seção 2** (ativas e ainda não
associadas no mês), não o catálogo inteiro: oferecer tudo deixava escolher uma
despesa que já tinha casado com outra transação.

Para a seção 2 ficar completa mesmo antes de o usuário abrir o Mês Atual, o
batimento chama `_garantir_lancamentos()` — o mesmo `INSERT OR IGNORE` de
`/api/lancamentos`, só que mais cedo.

Ainda na revisão:
- **"Não é essa despesa"** — troca a despesa sugerida (via `DespesaPicker`,
  com opção de criar despesa nova ali mesmo);
- **"Confirmar tudo"** — só aqui grava.

> `DespesaPicker` é usado nas duas direções: na seção 2 as opções são
> transações, não despesas (daí a prop `vazio`, para o estado vazio não dizer
> "nenhuma despesa encontrada" quando a lista é de débitos).

### Rebater sem reimportar

As transações do mês já estão no banco desde a última importação — refazer
associação não precisa de arquivo. Dois atalhos evitam o ciclo inútil de
reenviar o extrato só para corrigir um vínculo:

- **"Rebater `AAAA-MM` sem importar"** no passo 1, que pula direto para a
  revisão;
- **"Rebater de novo"** depois de confirmar. Vale a pena: corrigir um vínculo
  costuma liberar uma transação que o batimento então casa sozinho (foi o que
  aconteceu com `Teatro Maju` assim que o Mercado Pago saiu do lugar dele).

### A lista de busca (`DespesaPicker`)

Dois problemas separados apareciam como "a busca é ruim":

**A lista era cortada.** O seletor vive dentro de tabelas com
`overflow-hidden`, então a caixa ancorada no fluxo normal era clipada na borda
da tabela — sobrava uma faixa impossível de rolar. Agora ela é renderizada em
**portal** com `position: fixed`, medida a partir do `getBoundingClientRect()`
do input (remedido no `scroll` com `capture: true`, porque quem rola é o
container interno, não a janela). Abre para cima quando a linha está perto do
rodapé, e tem largura mínima de 460px — rótulo de transação é
`data · descrição · valor` e não cabia em 288px.

**A busca era literal.** `includes` cru não acha "educação" em `EDUCACAO`, nem
"america" em `AMERICA10/08`. Cada termo agora é testado contra duas versões do
rótulo: sem acento e com pontuação virando espaço, **e** uma versão só com
letras e dígitos. É o que faz `038397` achar `FINANC IMOBILIARIO 038/397` e
`15508` achar `R$ 155,08`.

Teclado: setas navegam, Enter escolhe o destacado (ou "+ Nova despesa" no fim),
Esc fecha.

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
| `/api/importacao` | POST | Recebe arquivo (multipart) e **substitui** o período que ele cobre, preservando os vínculos confirmados |
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

`/api/lancamentos` devolve `{despesas: [], receitas: []}` — os dois lados de uma
vez, cada um já unindo lançamentos (fixas) e transações (esporádicas). Ver
doc 14 §6 para o layout da tela e a calculadora de resgate fechada.
