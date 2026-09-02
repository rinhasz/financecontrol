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
| `valorizacao` | um passo por papel por dia: PU anterior, fator, PU, saldo, **variação em R$**, método e detalhe | é a **memória de cálculo** — sem ela, "rendeu 0,10%" é um ato de fé |

### A coluna `variacao`

`variacao = (pu - pu_anterior) × quantidade` — quanto o papel rendeu **em reais
naquele dia**. Fica gravada por dia, e não derivada das pontas, por três motivos:

**1. É o que separa rendimento de fluxo de caixa.** Hoje as duas coisas
coincidem, porque a fase não tem aplicação nem resgate. Quando tiver, o
rendimento do dia passa a ser `variacao − fluxo do dia`; sem uma variação diária
gravada, um resgate de R$ 50 mil entraria na conta como prejuízo de R$ 50 mil.

**2. Rentabilidade só fecha se for encadeada por dia.** Comparar saldo inicial
com saldo final só funciona enquanto não há aporte no meio. A partir do momento
em que há, a rentabilidade correta é o produto dos fatores diários — e cada dia
precisa do seu próprio numerador.

**3. Distingue juro de oscilação.** Somando por produto, a renda fixa da carteira
gerou R$ 1.009,43 / R$ 1.009,92 / R$ 1.010,42 nos três dias — quase uma reta,
que é o que carrego é. As ações fizeram −2.536,38 / −8.995,95 / +1.703,59 no
mesmo período. "Quantos reais os investimentos geraram de juros no mês" é uma
pergunta sobre a primeira série, não sobre a soma das duas.

**Verificação:** em todos os 10 grupos de produto × indexador, `Σ variacao`
reproduz `saldo final − posição inicial` com erro nulo até a quarta casa.

Um único helper (`_passo`) grava os três caminhos possíveis de um dia — preço de
mercado, accrual, e o dia parado. A variação sai sempre do mesmo lugar, em vez de
ser recalculada em cada ramo com chance de divergir.

**O que isto habilita** (ainda não implementado): juros do mês em reais, e
rentabilidade efetiva em % do CDI, IPCA+ e CDI+ — todas são agregações desta
coluna contra as séries que já estão em `mercado_serie`.

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

> **`IPCA_mes` é o índice do mês corrente, e enquanto ele não fecha vale a
> projeção — não o último índice fechado.** O IBGE só divulga o mês por volta do
> dia 10 do mês seguinte, e o VNA da ANBIMA (que é o que o banco mostra) anda o
> tempo todo pela projeção de mercado. Carregar o mês anterior erra o **sinal**
> numa virada: em 28/08/2026 o banco embutia -0,205% ao mês, julho tinha fechado
> a +0,07%, e a LIG IPCA+ 2035 de R$ 90 mil ficava R$ 23,99 acima. A mediana do
> Focus para agosto (-0,18%) derruba o desvio para R$ 2,19.
>
> Testadas as três fontes livres, contra o mesmo gabarito:
>
> | fonte do IPCA do mês corrente | valor | desvio na LIG |
> |---|---:|---:|
> | julho fechado (SGS 433) | +0,07% | +R$ 23,99 |
> | IPCA-15 de agosto (SGS 7478) | -0,40% | -R$ 17,03 |
> | **mediana do Focus (Olinda)** | **-0,18%** | **+R$ 2,19** |
>
> O IPCA-15 acerta o sinal mas exagera a magnitude; o Focus é o que a ANBIMA
> efetivamente projeta. Fica o Focus, com queda para o último índice fechado se a
> API não responder — e o detalhe na tela diz qual dos dois foi usado.

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

## Conciliação desde o valor de aplicação

Para descobrir a convenção sem chutar, foram usadas as duas pontas que o arquivo
de posição oferece: `valor de aplicação` + `data da aplicação` de um lado,
`saldo bruto atualizado` + `saldo atualizado até` do outro. Dado o fator
observado, o número de dias implícito sai invertendo a fórmula:

```
n = ln(saldo / aplicacao) / ln(1 + taxa/100) * base
```

Comparar `n` com a contagem real de dias — corridos e úteis — diz qual base o
banco usou, sem precisar adivinhar.

**Janela curta (25/08 → 28/08) — decide a base.** Qualquer erro na data de
início do rendimento se cancela na razão entre dois saldos, então esta janela
isola a base. Na LCI prefixada 13,240%: `n` dá **2,978 em base 365** contra 3
dias corridos reais, e 2,056 em base 252 contra 3 dias úteis reais. Base 365,
dias corridos — e é o que está implementado.

**Janela longa (aplicação → 25/08) — não fecha, e é esperado.** Com base 365 os
quatro prefixados ficam entre R$ 0,72 e R$ 98,65 **acima** do banco, e a taxa
implícita fica sempre um pouco abaixo da declarada:

| papel | aplicação | dias | taxa declarada | taxa implícita |
|---|---:|---:|---:|---:|
| LCI-PRE | 03/12/24 | 630 | 13,240% | 13,136% |
| LIG-PRE | 18/03/24 | 890 | 10,770% | 10,745% |
| LIG-PRE | 19/03/24 | 889 | 10,850% | 10,820% |
| LIG-PRE | 11/12/24 | 622 | 14,630% | 14,541% |

A diferença **não** é um offset constante de dias (seria 4,7 / 2,0 / 2,3 / 3,5),
então não é "conta começa em D+1". Também não é arredondamento da taxa — as
implícitas não arredondam de volta para as declaradas. Sobra o histórico que o
banco carrega e o arquivo não expõe (taxa contratada com mais casas, ou reset).

**Isso não afeta o app**, e é a razão de a conciliação parar aqui: a valorização
nunca recalcula o histórico — ela **parte do saldo importado** e só aplica o
fator diário para a frente. O que precisa estar certo é o fator do dia, que é
exatamente o que a janela curta mede.

**O mesmo método validou o IPCA.** Na LIG IPCA+ 2035 (aplicada em 24/09/2020),
descontando o juro real de 4,05% em base 365 sobre 2.161 dias corridos, o IPCA
acumulado implícito no saldo do banco dá **+42,35% em 5,92 anos** — compatível
com o IPCA realizado do período. Confirma juro real em base 365 e VNA por
accrual, e foi o que permitiu isolar a projeção do mês corrente como a única
peça que faltava.

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
| projeção de IPCA | Banco Central, Focus (Olinda) | mediana; corrige o VNA do mês aberto |
| fechamento de ação | Yahoo Finance | dia sem pregão simplesmente não vem |
| PU de debênture | ANBIMA, mercado secundário | arquivo diário `db{ddmmaa}.txt`, campo 11 |

**CRI e CRA têm, sim, fonte pública gratuita — a afirmação anterior aqui estava
errada.** O que não existe é o arquivo `.txt` diário nos moldes do de debêntures
(testados os padrões de URL equivalentes, todos 404) e a API oficial
(`api.anbima.com.br/feed/precos-indices/v1/cri-cra/mercado-secundario`) exige
credencial. Mas a página pública **Taxas de CRI e CRA** publica, sem login, os
últimos 5 dias úteis com PU de 8 casas, taxa indicativa, % PU par / VNE e
duration:

```
POST https://www.anbima.com.br/pt_br/informar/precos-e-indices/precos/
     taxas-de-cri-e-cra/taxas-de-cri-e-cra.htm
  doui_fromForm = Form_4028B8816C8C4C20016C8C5236B6086D
  lumII         = 4028B8816C8C4C20016C8C5236B6086D
  doui_renderAction = none
  lum_filters.codigo.value = CRA019001E7   # aceita prefixo: CRA023 traz os 45
  filtroTermo              = CRA019001E7
```

O PU sai de `<td class="pu">`; a data de `<td class="data-referencia">`.

**Mas ela não cobre todo o universo.** A ANBIMA precifica os papéis sob o seu
Código de Negociação, e o CRA da carteira — `CRA02300NAX`, Dexco IPCA+6,05%
17/10/2033 — **não está lá**: a busca devolve zero linhas, enquanto o controle
com um código conhecido devolve os 5 dias corretamente, e nenhum dos 45 papéis
`CRA023*` precificados é da Dexco. Por isso a fonte não foi ligada: ela não
resolveria nenhum papel desta carteira hoje.

Esses papéis seguem valorizados **por accrual do indexador contratado**, o que
captura o carrego mas não o spread. Fica marcado no método, em vez de fingir um
preço.

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

**VNA do IPCA.** A ANBIMA corrige o VNA por aniversário no dia 15 e distribui a
projeção pro-rata por **dias úteis**; aqui a projeção usada é a mediana do Focus,
distribuída pro-rata por **dias corridos**. As duas escolhas se compensam
parcialmente e sobra R$ 2,19 na LIG IPCA+ de R$ 90 mil — a projeção da própria
ANBIMA, que fecharia o resto, é paga.

**Papel indexado a IPCA é acumulado, não marcado a mercado** — e a conciliação
mostrou que **o banco também acrua**. A suspeita anterior de marcação vinha de o
valor do banco ficar abaixo do juro real puro; a explicação é mais simples, o
IPCA de agosto/2026 era **negativo**, e o VNA caiu. Não faz falta uma fonte de
preço para LIG.

## De onde vem o PU do CRA: curva de NTN-B, não mercado de CRA

Único desvio grande na conferência de 28/08: o CRA Dexco IPCA+6,05% 2033 fez
**+0,221% em 3 dias** (R$ 98.819,46 → R$ 99.037,66) contra **+0,031%** de
carrego. R$ 218,20 onde caberiam R$ 30,49.

O **Manual de Marcação a Mercado do Itaú Securities Services** (público, exigência
do código ANBIMA de administração fiduciária) diz exatamente como o papel é
marcado — p. 38, "CRI / CRA / ... – Pós-fixado em IPCA":

> "Os títulos pós fixados em IPCA são títulos marcados a mercado considerando-se
> três componentes principais para a taxa de Marcação a Mercado: projeção de taxa
> IPCA, Taxa de Mercado e Spread de Crédito.
> A fonte primária para a projeção da taxa IPCA é a ANBIMA;
> **A fonte primária para o Cupom de IPCA é a curva proveniente das NTN-Bs**;
> A fonte do spread de crédito [...] é o Comitê de Precificação da WMS."

A taxa de mercado é a **curva de NTN-B**, não o mercado secundário de CRA. Com o
spread de crédito fixo entre revisões do Comitê, o PU segue a NTN-B diariamente.

**A conta fecha.** A curva de juro real caiu de 25/08 para 28/08 (Tesouro Direto,
Taxa Compra):

| vencimento | 25/08 | 28/08 | Δ |
|---|---:|---:|---:|
| 15/08/2032 | 7,97% | 7,93% | −4,0 bps |
| 15/05/2035 | 7,82% | 7,76% | −6,0 bps |
| interpolado p/ 17/10/2033 | | | **−4,6 bps** |

Decompondo o movimento do Itaú: +0,221% total − 0,031% de carrego = **+0,190% de
preço puro**. Contra −4,6 bps, isso implica **duration de 4,13 anos** — plausível
para um CRA de 2033 com amortização (o CRA da JBS vencendo 15/09/2033, o mais
parecido na base ANBIMA, tem duration de 1.216 du ≈ 4,8 anos). Refazendo:
`98.819,46 × carrego × (1 + 4,13 × 0,00046)` = **R$ 99.037,75**, contra
R$ 99.037,66 do banco — R$ 0,09.

**Por que o teste contra o mercado de CRA deu errado.** Antes eu havia medido os
145 CRA IPCA+ precificados pela ANBIMA e achado mediana de +0,027%, concluindo que
"não é movimento de curva". O erro foi a referência: os preços de CRA da ANBIMA
saem de **taxas indicativas de negócios observados**, não da curva. No mesmo
período o CRA da JBS teve a taxa indicativa **subindo** 10,8 bps (8,5061% →
8,6145%) enquanto a NTN-B **caía** 4 a 6 bps — os dois mercados andaram em
direções opostas nesses três dias. Comparar contra o setor de CRA testava a
hipótese errada.

**O que falta para reproduzir.** Ligar isso exigiria, por papel, o cronograma de
amortização (para calcular duration) e o spread de crédito implícito na
importação. Nenhum dos dois vem no arquivo de posição. Enquanto isso o papel
segue valorizado por accrual, com o desvio zerando na importação seguinte.

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
| LIG IPCA | 90.048,25 | 90.050,44 | **+2,19** |

Desvio absoluto total: **R$ 2,85** sobre R$ 1,75 milhão — 1,6 centavo por mil
reais. A evolução mostra o que cada conciliação encontrou:

| estado | desvio total |
|---|---:|
| base 252 para tudo | R$ 86,06 |
| prefixado em base 365, dias corridos | R$ 24,65 |
| VNA pela projeção Focus do mês corrente | **R$ 2,85** |

O que sobra são R$ 2,19 na LIG IPCA+, que é a distância entre a mediana do Focus
(-0,18%) e a projeção que a ANBIMA de fato usou (-0,205% implícito no extrato) —
essa última não tem fonte pública gratuita. **A hipótese anterior de que esse
papel era marcado a mercado estava errada:** o banco acrua, e o desvio era da
projeção do índice, não da curva.

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

---

# Movimentos: extrato mensal do produto

A posição é uma **foto** de um dia. Entre uma foto e a seguinte o dinheiro se
mexe — resgate parcial, principalmente — e a valorização, que só sabe render,
continuaria mostrando um papel que não existe mais no tamanho que ela pensa.

O botão **"Importar extrato"** na aba Investimentos lê o extrato mensal do
produto (`ExtratoMensal_LCA.xls`, HTML disfarçado de `.xls`, como todo arquivo
do internet banking), acha as linhas de **RESGATE** e grava cada uma como um
movimento.

## Tabela à parte, nunca posição corrigida

`movimento_investimento` fica ao lado da posição, e a posição **nunca é
alterada**. Escrever por cima dela destruiria o ponto de partida da conferência
— e conferir contra o banco é a razão de a tela existir. O movimento é aplicado
durante a valorização, e **apagar a linha reconstrói a posição anterior**, sem
nada mais a desfazer: a valorização é recalculada do zero a cada "Atualizar
Posições".

## Identificação da operação

Pela **data de aplicação + data de vencimento**, dentro do produto escolhido.
Zero ou mais de um casamento **para o import e acusa** — dois papéis com as
mesmas duas datas são indistinguíveis aqui, e escolher errado corromperia a
posição em silêncio.

> **O número da operação não serve para casar.** No mesmo arquivo, o resgate
> aparece como `110000690502` e a posição do mesmo papel como `110000095862`.
> Foi por isso que a identificação por datas, e não por código, era o caminho
> certo.

## Os três métodos, e por que a tela mostra os três

Depois do resgate, quanto vale o papel? Medido no resgate real de 01/09/2026
(LCA 94% do CDI, valor de 31/08 = R$ 233.746,38, creditado R$ 28.000,73,
principal resgatado R$ 23.187,00):

| método | conta | saldo em 01/09 | vs banco | fluxo registrado |
|---|---|---:|---:|---:|
| `extrato` | o valor que o banco publica em "Posição em" | 205.859,16 | **0,00** | −28.000,73 |
| `credito` | `valor_ontem − creditado`, depois rende o dia | 205.845,56 | −13,60 | −28.000,73 |
| `proporcional` | `valor_ontem × (aplicação − A) / aplicação` | 206.046,54 | +187,38 | −27.799,85 |

Os R$ 13,60 do `credito` **não são erro de fórmula**: são o CDI de 01/09, que o
BCB ainda não publicou e a valorização repete do dia anterior. A conta do banco
é essa — `(233.746,39 − 28.000,73) × 1,00055 = 205.859,16`, e 1,00055 é 94% do
CDI diário.

O `proporcional` erra mais, e erra duas coisas: o valor e o **fluxo de caixa**.
Ele registra R$ 27.799,85 saindo quando saíram R$ 28.000,73 — e é o fluxo que
alimenta a separação entre rendimento e movimentação, base da rentabilidade.

Por isso o padrão é `extrato`, com os três lado a lado e a diferença explícita:
a escolha fica visível em vez de escondida atrás de uma fórmula.

## Onde o movimento entra na valorização

Na quantidade, e não no saldo. O saldo é sempre `PU × quantidade`, então reduzir
a quantidade faz o resgate sobreviver a todos os dias seguintes sem nenhum caso
especial adiante.

**`proporcional` e `credito` entram antes do fator do dia**; o rendimento incide
sobre o que sobrou, que é o certo — quem resgatou de manhã não perde o dia
inteiro. **`extrato` entra depois**, porque o número publicado já é o fechamento,
com o rendimento dentro: aplicá-lo antes renderia o dia duas vezes, e foi
exatamente o que aconteceu no primeiro teste (R$ 99,97 a mais).

## `valorizacao.fluxo`

Coluna nova ao lado de `variacao`. Sem ela, um resgate de R$ 28 mil apareceria
como prejuízo de R$ 28 mil no dia. Com ela, o dia 01/09 do papel fica legível:
`variacao +99,92` (rendeu) e `fluxo −28.000,73` (saiu) — que é a decomposição de
que a rentabilidade precisa.

## Tela de movimentos

Abaixo da posição, lista os movimentos de um período com o antes, o depois, o
método usado e o arquivo de origem, e um botão de **excluir** por linha. É a
peça que torna o ajuste reversível: excluir devolve a posição ao que era.

---

# As três bases diárias

Reorganização pedida para não haver confusão sobre onde cada número mora.

| base | tabela | uma linha por |
|---|---|---|
| **posição** | `investimento` | papel, por foto importada |
| **valorização** | `valorizacao` | papel, por dia |
| **movimento** | `movimento_investimento` | movimento (resgate ou aplicação) |

## Cada base é autossuficiente

As três carregam a identidade do papel — produto, emissor, indexador, datas,
PU, quantidade, % do indexador, taxa, liquidez — além dos seus próprios números.
É denormalizado **de propósito**: são fotos diárias, e uma foto que depende de
`JOIN` para ser lida deixa de ser foto. Ler a valorização de 01/09 não pode
depender de a posição de 25/08 ainda existir com o mesmo `id`.

A valorização guarda também os quatro saldos separados
(`bruto/líquido × accrual/mtm`), preenchidos conforme o método do dia: MTM para
o que tem preço de mercado, accrual para o que rende por curva.

## `valor_aplicacao_atualizado`: o fio que liga as três

```
posição      nasce igual a valor_aplicacao      (foto do banco, sem movimento)
movimento    = valor_aplicacao + aplicação − resgate
valorização  transportado do movimento, dia a dia
```

Na caminhada diária ele **parte do valor de aplicação original**, não do já
atualizado: o já atualizado é o resultado da última passagem, e começar por ele
faria os dias *anteriores* ao movimento mostrarem um valor que só passou a valer
depois dele. Foi um bug real, pego em teste — 31/08 exibia o valor pós-resgate
de 01/09. Como cada movimento guarda o valor **absoluto**, reprocessar é
idempotente: três valorizações seguidas dão exatamente o mesmo resultado.

## As regras de recálculo

| situação | regra | onde entra |
|---|---|---|
| **3.3.1** sem movimento | saldo de ontem × atualização | — |
| **3.3.2** resgate | `(aplic_atualizada / aplic) × saldo_ontem × atualização` | **antes** do fator |
| **3.3.3** aplicação | `saldo_ontem × atualização + aplicação` | **depois** do fator |

A assimetria não é descuido. No resgate a razão multiplica o saldo, então tanto
faz a ordem; na aplicação o dinheiro **acabou de chegar** e não pode render um
dia que ainda não passou por ele — somá-lo antes do fator pagaria juros sobre um
aporte que nem existia.

Verificado com movimento sintético: `233.746,38 × 1,00048560 + 50.000 =
283.859,89`, exatamente o que a base gravou.

## O botão faz as duas coisas

"Confirmar e atualizar" grava o movimento **e já revaloriza a posição**. Sem
isso, a tela continuaria mostrando o papel do tamanho antigo e caberia ao
usuário lembrar de clicar noutro botão para ver o efeito do que acabou de
importar.

## Nota sobre o método do resgate

A regra 3.3.2 é o método `proporcional`. Medido contra o extrato do banco no
resgate real de 01/09, ele fica **R$ 187,38 acima** do valor publicado, e
registra fluxo de R$ 27.799,85 quando saíram R$ 28.000,73. O método `extrato`
— que usa o número que o banco publica — continua disponível na tela e acerta na
bucha. A escolha é do usuário, feita no preview, e fica gravada no movimento.
