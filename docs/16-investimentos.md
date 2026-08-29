# Acompanhamento de investimentos

    O app deve ter uma jornada de acompanhamento de investimentos. O objetivo dela é ser capaz de replicar o acompanhamento de investimentos que tenho no meu internet banking, para:

     - ser uma fonte para que eu possa conferir o valor que está ali
     - ser capaz de me orientar sobre qual seria a minha melhor alternativa de resgate, quando um resgate for necessário
     - me permitir simular realocações

A implementação será feita em fases.

## Fase 1: Posição

    Objetivo: capturar a posição atual e criar uma base de dados completa para os dados dos investimentos. Nesta etapas a ideia não é que vc faça cálculos, mas somente preencha as bases com a posição consolidada, e crie no app uma visão de investimentos com a posição consolidada e detalhada.

### Fontes de informação

A carga inicial será feita através de planilhas, a seguir.

- Planilha com a posição consolidada do internet banking 
- Planilha com a posição detalhadas dos produtos de emissão Itaú
- Planilha com a posição detalhada dos produtos de corretora

### Quais são os produtos e os tipos

#### Produtos de emissão Itaú

LCI, LCA, LIG, CDB, todos produtos de emissão 

#### Produtos  de Corretora

Ações,  CDBs de emissão de outros Bancos e crédito corporativo (CRI, CRA, DEB)

### Como cada produto deve ser armezado em base 

#### Campos necessários

Todo produto deverá ter em base de dados os seguintes campos:

    Produto
    Emissor
    Indexador
    Data de valorização
    Data de Aplicação
    Data de vencimento
    Preco unitário (PU)
    Quantidade
    Valor de aplicação
    % indexador
    Taxa
    Saldo bruto atualizado_acrrual
    Saldo líquido atualizado_accrual
    Saldo bruto atualizado_mtm
    Saldo líquido atualizado_mtm
    Data de liquidez diária     

#### Produtos de emissão Itaú
São produtos não fungíveis - ou seja, os extratos  não mostrarão preço unitário. Para estes, vc deverá armazenar PU = 1 e calcular a quantidade = valor de aplicação / PU

#### Produtos de corretora
São produtos fungíveis, vc encontrará quantidade

### Arquivos de importacao

O arquivo base de importação está na pasta seed e é o POSICAO_CONSOLIDADA_RF_25082026_FAKE.xlsx.

---

# Fase 1 — implementado

**Status:** implementado (`api/investimentos.py`, aba "Investimentos").

O que está acima é o pedido; daqui para baixo é como ficou.

## Modelo

Duas tabelas: `carga_investimento` (uma por importação, com data da foto,
arquivo e origens) e `investimento` (uma linha por papel por foto).

Os saldos ocupam **quatro** colunas — `bruto/líquido × accrual/mtm` — porque os
dois regimes convivem e guardar tudo num campo só perderia a distinção, que é
justamente o que decide qual papel resgatar:

| origem | produtos | fungível? | regime |
|---|---|:---:|---|
| `emissao_itau` | LCI, LCA, LIG, CDB Itaú | não | accrual |
| `acoes` | ações e ETFs | sim | mercado (MTM) |
| `rf_corretora` | CDB de outros bancos, CRI, CRA, DEB | sim | mercado (MTM) |

Produto de emissão não tem preço unitário no extrato, então `pu = 1` e
`quantidade = valor de aplicação`, como o pedido define.

## Leitura dos arquivos

O `.xlsx` de carga tem as três abas e é lido de uma vez. **Também são aceitos os
`.xls` que o internet banking exporta** — que na verdade são HTML com uma
`<table>`, um por origem. Sem isso, os arquivos que o usuário realmente baixa não
abririam, e a carga só funcionaria com o arquivo preparado.

A remuneração vem num campo de texto com três formatos, e cada um vira campos
diferentes — somá-los impediria comparar produtos na hora do resgate:

| texto do banco | indexador | % indexador | taxa |
|---|---|---|---|
| `94.0000% do DI` | DI | 94,0 | — |
| `13.240% aa` | PRE | — | 13,24 |
| `IPCA + 4.05% aa` | IPCA | — | 4,05 |

No lado da corretora, o rótulo do papel carrega tudo junto —
`CDB1223I9RK - CDB PINEBM IPCA 7% 18/01/2027` — e é quebrado em código,
produto, indexador, taxa e vencimento. Cada pedaço é opcional: o arquivo de
carga vem com o rótulo truncado e ainda assim precisa importar.

**Nada é inventado.** Liquidez de ação, valor de aplicação de papel de corretora
e saldo líquido de MTM ficam nulos, porque a posição não os informa e a fase 1
não calcula.

## Substituição por origem

Reimportar substitui a posição daquela data **só nas origens presentes no
arquivo**: importar as ações não pode apagar a renda fixa, que veio de outro
arquivo. Verificado.

## Tela

Aba **Investimentos** com posição total, consolidado alternável por
**produto / indexador / emissor / origem** (com participação %, que é o que
mostra concentração) e a tabela detalhada com todos os campos.

Cada linha do consolidado abre num **`+`** e quebra por **indexador** — a
pergunta natural sobre um produto é "quanto disso é DI, quanto é IPCA". Quando
já se agrupa por indexador, a quebra vira por **produto**, que é a dimensão
complementar; quebrar por indexador dentro de indexador não diria nada.

> A soma da quebra é exatamente o total do grupo. Isso obrigou a corrigir o
> agrupamento, que arredondava a cada parcela: a diferença era de frações de
> centavo, invisível em reais, mas suficiente para a quebra não fechar com o
> grupo que a contém.

O `saldo` de cada linha é MTM quando existe e accrual quando não — o valor que o
internet banking mostra, que é o número que esta tela existe para conferir.

## Verificação

Totais conferidos contra a planilha, linha a linha:

| origem | linhas | total |
|---|---:|---:|
| emissão Itaú | 39 | 19.903.675,51 |
| ações | 3 | 892.354,44 |
| RF corretora | 2 | 995.342,52 |
| **total** | **44** | **21.791.372,47** |

Também testados: reimportar não duplica; importar só ações preserva a renda
fixa; arquivo inválido responde erro em vez de quebrar; migração idempotente; e
o resto do app segue intacto (60 despesas, 25 receitas classificadas).

## O que a fase 1 deliberadamente não faz

Rentabilidade, marcação a mercado própria, IR/IOF, sugestão de resgate e
simulação de realocação — são as fases seguintes. O objetivo aqui era ter a base
preenchida e conferível.

## Fase 2 - Valorização diária

Na fase 2 você aprenderá a fazer a valorização diária dos investimentos. Neste momento, vamos admitir que não há novas aplicações ou resgates, de forma que você simplesmente atualizará os valores. Por exemplo, se você tem a informação da posição de 25/08 (que é aquilo que foi importado) e estamos em 28/08, você deve calcular quanto vale aquele investimento nesta data. Para isso, foi acrescentada a data de valorização na base. Como isso deve acontecer ? Vc sempre mostra a sua última posição, e implementa um botão na tela de investimentos intitulado "Atualizar Posições". Ao clicar neste botão, vc processa as atualizações, calculando as posições atualizadas dia a dia até chegar ao dia atual, e grave na base de dados.

---

# Fase 2 — implementado

**Status:** implementado (`api/mercado.py`, `api/valorizacao.py`, botão
"Atualizar Posições").

## Duas bases novas, separadas de propósito

| tabela | o que guarda | por quê |
|---|---|---|
| `mercado_serie` | `(série, data) -> valor`, com a fonte | permite **refazer** o cálculo sem depender de a fonte estar no ar |
| `valorizacao` | um passo por papel por dia útil: PU anterior, fator, PU, saldo, método e detalhe | é a **memória de cálculo** — sem ela, "rendeu 0,10%" é um ato de fé |

A tela deixa abrir a memória clicando no método de cada papel.

## Base 252 e dias úteis

Convenção do mercado brasileiro: **base 252 dias úteis**. Fim de semana e
feriado bancário não rendem, e por isso a valorização caminha por dia útil, não
por dia corrido — reaproveitando `feriados_bancarios()`, que já existia para o
mês de competência.

## Fatores por indexador

**Pós-fixado em % do CDI** — padrão CETIP/B3 para CDB, LCI, LCA e LIG:

```
fator_dia = 1 + DI_dia * (p / 100)
```

`DI_dia` é a taxa do CDI do dia, que o Banco Central publica **já em % ao dia**
(SGS série 12) — é exatamente o `(1 + DI_anual)^(1/252) - 1` da fórmula CETIP,
o que dispensa reanualizar. `p` é o percentual contratado.

**Prefixado — base 365, dias corridos:** `fator_dia = (1 + taxa/100)^(1/365)`

**IPCA + taxa:** `fator_dia = (1 + IPCA_mes/100)^(1/dias_mes) * (1 + taxa/100)^(1/365)`
— o primeiro termo é o VNA rendendo o IPCA pro-rata pelos dias do mês, o
segundo é o juro real.

> **As duas bases convivem, e por isso a valorização caminha por dia corrido.**
> Pós-DI rende só em dia útil (252); prefixado rende todo dia, inclusive fim de
> semana (365). Caminhar só nos dias úteis perdia os sábados e domingos do
> prefixado.
>
> A base 365 do prefixado foi **descoberta conferindo com o extrato**, não
> assumida: com 252, uma LCI de R$ 61,9 mil ficava R$ 28,86 acima do banco e uma
> LIG de R$ 39,3 mil, R$ 17,09 acima. Os fatores implícitos do banco davam
> exatamente 2,98 dias em base 365 — ou seja, os 3 dias corridos entre as duas
> datas. Com 365, os desvios caem para R$ 0,47 e R$ 0,16.

**DI + spread** (debênture, CRA): `fator_dia = (1 + DI_dia) * (1 + taxa/100)^(1/252)`

## PU de partida

O valor é sempre `PU * quantidade`. Papel de emissão não tem PU no extrato (a
fase 1 grava `pu = 1`), então o ponto de partida é **saldo / quantidade** — assim
`PU * quantidade` reproduz exatamente o saldo que o banco informou, que é o
número que esta tela existe para conferir.

## Fontes de dado de mercado

| série | fonte | observação |
|---|---|---|
| CDI diário | Banco Central, SGS série 12 | público, sem autenticação |
| IPCA mensal | Banco Central, SGS série 433 | mensal, sai com atraso |
| fechamento de ação | Yahoo Finance | dia sem pregão simplesmente não vem |
| PU de debênture | ANBIMA, mercado secundário | arquivo diário `db{ddmmaa}.txt`, campo 11 |

**CRI e CRA não têm fonte pública gratuita.** A ANBIMA publica o arquivo de
debêntures aberto, mas não o de CRI/CRA — testados os padrões de URL
equivalentes, todos 404, e a API de preços exige credencial. Esses papéis são
valorizados **por accrual do indexador contratado**, o que captura o carrego mas
não o spread de mercado. Fica marcado no método, em vez de fingir um preço.

## O que acontece quando falta dado

Nada é inventado. Cada caso tem um comportamento explícito e visível na tela:

| situação | comportamento |
|---|---|
| CDI do dia ainda não divulgado | **repete o último CDI conhecido**, dizendo no detalhe qual dia foi repetido |
| dia sem pregão | mantém o último fechamento |
| sem PU ANBIMA | cai para accrual pelo indexador |
| sem indexador cadastrado | não valoriza, e o motivo aparece em âmbar |

### Por que o CDI do dia é repetido, e não ignorado

O CDI de um dia só é divulgado no dia seguinte, mas **o banco já credita no
próprio dia**. A primeira versão parava de valorizar no último dia divulgado, e
isso produzia um número **errado**, não conservador: uma LCA de R$ 233.292,90 a
94% do CDI ficava em R$ 233.519,53 quando o extrato mostrava R$ 233.632,93 — R$
340 atrás, por faltar um dia.

Repetir o último CDI conhecido é a prática correta: o CDI só muda em reunião do
Copom, e mesmo no dia da mudança a diferença de um dia é de centésimos. O
detalhe da memória diz sempre qual dia foi repetido, então a premissa fica
visível.

## Simplificações assumidas

**VNA do IPCA.** A ANBIMA corrige o VNA por aniversário no dia 15 e usa
projeção para o mês corrente; aqui usa-se o último IPCA divulgado, distribuído
pro-rata pelos dias do mês.

**Papel indexado a IPCA é acumulado, não marcado a mercado.** Uma LIG IPCA+ com
vencimento em 2035 tem duration alta, e o banco a marca pela curva de juro real
— conferindo em 28/08, o banco mostrava R$ 90.048,25 contra R$ 90.072,24 de
accrual. Note que o valor do banco fica **abaixo** até do juro real puro sem
inflação nenhuma (R$ 90.066,15), o que só se explica por marcação. Reproduzir
isso exige uma fonte de preço para LIG, que não existe pública. O detalhe da
linha diz "accrual, não marcação a mercado" para o desvio não passar por número
exato.

**Sem IR/IOF.** A valorização é bruta. O líquido depende de prazo e de fato
gerador, e entra quando a fase de resgate precisar comparar alternativas.

**Sem aplicações e resgates no período**, como a fase define.

## Verificação

Com os arquivos reais do internet banking (44 papéis, posição de 25/08
valorizada até 28/08 — 3 dias úteis):

| tipo | variação em 2 dias de CDI | método |
|---|---:|---|
| LCI/LCA/LIG/CDB pós-DI | +0,10% | `di` |
| prefixados | +0,12% a +0,16% | `pre` |
| LIG IPCA | +0,06% | `ipca` |
| ações | movimento real de mercado | `mercado` |

**Conferência por grupo, contra o site do banco em 28/08:**

| grupo | banco | calculado | desvio |
|---|---:|---:|---:|
| LCI DI | 647.071,19 | 647.071,21 | **+0,02** |
| LIG DI | 913.775,86 | 913.775,87 | **+0,01** |
| LCI PRE | 61.933,25 | 61.933,72 | **+0,47** |
| LIG PRE | 39.322,82 | 39.322,98 | **+0,16** |
| LIG IPCA | 90.048,25 | 90.072,24 | +23,99 |

Desvio absoluto total: **R$ 24,65** sobre R$ 1,75 milhão — e R$ 24,00 disso é o
único papel marcado a mercado pelo banco. Antes da correção da base do
prefixado, era R$ 86,06.

**Conferência papel a papel.** Partindo da posição de 25/08 e
valorizando 3 dias úteis a 94% do CDI:

| papel | posição 25/08 | calculado | banco |
|---|---:|---:|---:|
| LCA | 233.292,90 | **233.632,93** | 233.632,93 |
| LCA | 27.600,83 | **27.641,06** | 27.641,06 |
| LCI | 7.182,36 | **7.192,83** | 7.192,83 |
| LCI | 63.784,91 | **63.877,88** | 63.877,88 |
| LCI | 222.012,11 | **222.335,70** | 222.335,70 |

Bate ao centavo (um caso difere em R$ 0,01, por arredondamento). O fator é
`1 + 0,0005166 × 0,94 = 1,00048560`, que em 3 dias dá +0,1458%.

Também testados: atualizar duas vezes não empilha (132 linhas de memória = 44
papéis x 3 dias, idempotente); migração das colunas novas numa tabela criada na
fase 1; e o resto do app intacto.

## O que a fase 2 deliberadamente não faz

Recomendação de resgate e simulação de realocação — que são o objetivo final do
doc, e agora têm a base de que precisam: PU diário por papel, memória auditável
e dado de mercado guardado.

## Reimportar descarta a memória de valorização

A posição é substituída por data e origem, e a memória de valorização aponta
para as linhas substituídas — então ela é apagada junto. É derivada de
propósito: "Atualizar Posições" a reconstrói a partir do dado de mercado, que
fica guardado e **não** é rebaixado.

Sem isso o `DELETE` falhava por chave estrangeira e a importação devolvia um 500
mudo. Ver a armadilha registrada no doc 12 — é a segunda vez que uma tabela nova
apontando para outra quebra uma importação que substitui.
