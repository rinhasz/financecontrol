# Projeção e Calculadora de Resgate

**Status:** implementado (`api/projecao.py`).

O app existe para responder perguntas sobre o patrimônio. A primeira delas é
*"quanto preciso resgatar para cobrir este mês?"*. As seguintes — *"quantos anos
ainda preciso trabalhar para viver de renda?"* — dependem de existir uma
projeção confiável do que se gasta e do que se ganha. Este documento cobre a
base dessa projeção.

---

## Os três números de todo item

Toda despesa e toda receita têm, **por definição**, três valores no mês:

| | O que é | Já saiu/entrou da conta? |
|---|---|:---:|
| **pago** / recebido | aconteceu e está no extrato | sim |
| **agendado** / previsto | marcado, ainda vai acontecer | não |
| **projetado** | quanto se espera que custe/renda no total | — |

A distinção entre pago e agendado é o que faz a calculadora perguntar a coisa
certa. **O que já foi pago não precisa de dinheiro**: esse valor já saiu e já
está descontado do saldo da conta.

---

## Tipo de projeção (parâmetro do catálogo)

`projetado` sai do histórico, e **como** sai é escolha por item — a natureza do
gasto muda o que é uma boa estimativa.

| `tipo_projecao` | Cálculo |
|---|---|
| `media_simples` | média de todos os meses do histórico |
| `media_movel_6` | média dos **6 últimos meses presentes** no histórico |
| `media_sazonal` | média dos meses **iguais** ao projetado (todo março, para projetar março) |

A sazonal **cai para média simples** quando não há nenhum mês correspondente no
histórico — projetar sobre zero amostras seria pior que uma média larga.

A base é sempre a **visão consolidada** do mês: ocorrências somadas e estornos
abatidos. Projetar sobre lançamento cru contaria duas vezes uma despesa cobrada
em duas parcelas, e contaria um gasto que foi devolvido.

Só se olha o **passado**: usar o próprio mês para projetá-lo seria circular, e o
mês corrente quase sempre está incompleto.

### Valor inicial de cada item

O `padrao_variabilidade` já cadastrado descreve o comportamento de cada despesa;
a migração o traduz em método de estimativa, em vez de começar todo mundo no
mesmo padrão arbitrário:

| padrão | projeção | por quê |
|---|---|---|
| `fixa`, `reajuste_anual` | média móvel 6 | a média longa ficaria presa no valor anterior ao reajuste |
| `variavel_nao_sazonal` | média móvel 6 | o recente representa melhor |
| `variavel_sazonal`, `anual` | média sazonal | o mês do ano é o que explica o valor |
| `sem_dados` | média simples | sem informação melhor |

Resultado nas 58 despesas: 49 em média móvel, 2 em sazonal (as duas marcadas
como sazonais no cadastro). É ponto de partida, não verdade — o campo é editável
item a item.

---

## O que NÃO é projetado

**Item esporádico.** Acontece quando acontece; a média dos meses em que apareceu
não diz nada sobre este mês, e somá-la ao "a vencer" inflaria a necessidade de
resgate com um gasto imaginário.

**Item que já teve movimento no mês**, a menos que seja "mais de um por mês".
Uma conta que só acontece uma vez e já foi paga não vai acontecer de novo.
Insistir na diferença contra o projetado era o bug relatado: um mês inteiramente
quitado ainda aparecia com valor a vencer.

**Resgate como renda futura.** `a receber` soma apenas tipos que contam como
renda. Contar um resgate projetado como entrada faria a calculadora concluir que
não é preciso resgatar — usando a própria resposta como dado.

---

## A calculadora

```
a_vencer  = agendado + Σ max(0, projetado − pago − agendado)
a_receber = Σ renda projetada ainda não recebida

resgate_necessario = max(0, a_vencer + reserva − saldo − a_receber)
resgate_ja_feito   = Σ créditos de tipo `resgate_mensal` no mês
falta_resgatar     = max(0, resgate_necessario − resgate_ja_feito)
```

O `max(0, ...)` por item importa: uma despesa que veio mais cara que o projetado
não gera "crédito" para abater outra — o excesso já aconteceu e já está no pago.

### Bruto e líquido convivem

`total` é **bruto** (com o estorno ainda dentro) e bate com a soma das linhas da
visão analítica, que mostra o gasto e a devolução em blocos separados.
`totalLiquido` desconta o estorno e é o que alimenta saldo e resgate. Os dois são
devolvidos, com `estornado` explicitando a diferença — invariante verificada:
**a soma das linhas exibidas é sempre igual ao total exibido naquela visão**.
