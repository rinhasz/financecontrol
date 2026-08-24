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

falta_resgatar = max(0, a_vencer + reserva − saldo − a_receber)
```

### O saldo vem do extrato

`config.saldo_conta` é lido na importação, da coluna *saldos (R$)* do extrato:
o **último "SALDO TOTAL DISPONÍVEL DIA" antes da marca "lançamentos futuros"**
— depois dela vêm agendamentos, que ainda não afetaram a conta. A data fica em
`config.saldo_data` e aparece na tela ao lado do valor.

Com saldo digitado à mão, ou zerado, a calculadora responde sobre uma conta que
não existe. No caso real: o saldo era **−111,21**, não zero.

O saldo acompanha **o último extrato importado**, inclusive para trás: importar
um arquivo antigo devolve o saldo daquela data. É coerente com "o extrato é a
verdade", e a data exibida denuncia o que aconteceu.

**A defasagem fica à vista.** A resposta da calculadora depende do saldo, e o
saldo só muda ao importar — saldo velho dá resposta velha sem avisar. Passando
de 3 dias, a data aparece em âmbar com a idade ao lado; sem nenhum saldo lido de
extrato, aparece "(digitado)".

> **O backend não recarrega sozinho.** Uma importação feita com o app aberto
> antes da atualização roda o código antigo — foi o que fez o saldo continuar
> zerado depois desta mudança. Fechar e reabrir é parte do procedimento (doc 12).

### O resgate já feito NÃO abate

Ele já entrou na conta e portanto **já está dentro do saldo**. Descontá-lo de
novo contava o mesmo dinheiro duas vezes. Com saldo zerado à mão o erro passava
despercebido — os dois se cancelavam por acaso; com o saldo real do extrato ele
diria "não falta nada" numa conta negativa. O valor continua sendo devolvido,
como informação, no tooltip do card.

O `max(0, ...)` por item importa: uma despesa que veio mais cara que o projetado
não gera "crédito" para abater outra — o excesso já aconteceu e já está no pago.

### Bruto e líquido convivem

`total` é **bruto** (com o estorno ainda dentro) e bate com a soma das linhas da
visão analítica, que mostra o gasto e a devolução em blocos separados.
`totalLiquido` desconta o estorno e é o que alimenta saldo e resgate. Os dois são
devolvidos, com `estornado` explicitando a diferença — invariante verificada:
**a soma das linhas exibidas é sempre igual ao total exibido naquela visão**.

---

## Reimportar preservava só metade

Ao ler o saldo do extrato, dois defeitos da substituição de período apareceram:

**O DELETE falhava por chave estrangeira** quando algum estorno apontava para
uma linha do intervalo (`estorna_transacao_id`). A referência é solta antes de
apagar; o vínculo que importa (`estorna_despesa_id`) sobrevive — foi exatamente
para isto que ele existe.

**A reimportação apagava a classificação das entradas.** O snapshot guardava só
`despesa_id`; `receita_id`, `objetivo` e `estorna_despesa_id` se perdiam em
silêncio, e `lancamento_receita.transacao_id` nem era desligado. Agora atravessa
tudo que é decisão do usuário sobre a linha. Verificado com o extrato real: duas
importações seguidas preservam 60 despesas, 21 receitas e 3 estornos.
