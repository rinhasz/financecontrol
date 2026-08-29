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
| `valor_fixo` | não olha o histórico: vale `valor_projecao` do catálogo, **zero incluído** |

A sazonal **cai para média simples** quando não há nenhum mês correspondente no
histórico — projetar sobre zero amostras seria pior que uma média larga.

`valor_fixo` existe para o item cujo histórico não diz nada útil sobre o futuro:
a despesa que acabou, a que passou a ser cobrada em outro lugar, a que só existe
no catálogo para receber lançamento eventual. Antes, a única forma de tirar um
número errado do "a vencer" era **desativar o item** — o que apaga o histórico
dele e estraga a projeção dos meses seguintes. Zero como valor de projeção diz
"não espero que aconteça" sem perder nada.

Por isso `valor_projecao` é `NOT NULL DEFAULT 0`: quem escolhe valor fixo e não
digita nada está justamente dizendo zero. E o campo só aparece na tela quando
`valor_fixo` está selecionado — em qualquer outro método ele seria ignorado, e
um campo ignorado engana.

A base é sempre a **visão consolidada** do mês: ocorrências somadas e estornos
abatidos. Projetar sobre lançamento cru contaria duas vezes uma despesa cobrada
em duas parcelas, e contaria um gasto que foi devolvido.

### O que fica fora da amostra

- **O mês projetado e os posteriores.** Projetar um mês com ele mesmo é circular.
- **O mês corrente**, que quase sempre está em andamento. Metade de um mês puxa
  a média para baixo e faria a previsão do mês seguinte encolher só porque hoje
  é dia 10.

A exclusão do corrente **cede quando é ele o único histórico existente** — sem
isso a projeção seria zero, o que é pior que uma amostra imperfeita. Mesma
lógica do fallback da sazonal.

> Isso vale hoje: depois da limpeza do histórico, agosto/2026 é o único mês real
> e também o corrente. Setembro projeta a partir dele por causa do fallback;
> assim que agosto fechar e setembro virar o corrente, a regra passa a operar
> normalmente.

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

## Correção manual da projeção

A projeção automática acerta na maioria, mas não em todas. `projecao_manual`
`(natureza, item_id, mes_ref, valor)` guarda a correção de **um item num mês**,
e tem precedência sobre tudo:

| ordem | origem | escopo |
|---|---|---|
| 1 | `projecao_manual` | aquele item, **naquele mês** |
| 2 | `valor_projecao` com `tipo_projecao = valor_fixo` | aquele item, todo mês |
| 3 | histórico, pelo método cadastrado | aquele item, todo mês |

Aplicada dentro de `projecoes_do_mes()`, que é **ponto único**: lista do mês,
consolidado, resumo e calculadora de resgate passam todos por lá, então a
correção vale nos quatro de uma vez, sem risco de a tela dizer um número e a
calculadora usar outro.

**Tabela à parte, não coluna em `lancamento`**, por dois motivos: a correção
precisa existir para meses que ainda não têm lançamento aberto (projeção
adiante); e `lancamento.valor_esperado` é **sobrescrito pela projeção a cada
carregamento** para as linhas em aberto — era exatamente por isso que editar o
valor de uma linha em aberto na tela não colava. A edição inline do Mês Atual
agora roteia por status: linha em aberto grava em `projecao_manual`, linha paga
ou agendada continua editando o lançamento.

`valor: null` **apaga** a correção e devolve o item ao cálculo automático. Zero
não apaga — zero é a correção que diz "não vai acontecer neste mês", e é
justamente o caso que o cálculo automático não consegue expressar. É a mesma
distinção do `valor_fixo`, um mês por vez.

Na tela, o selo muda de origem junto com o número: **PREVISTO** (âmbar) para
projeção automática, **AJUSTADO** (azul) para corrigida à mão — e o AJUSTADO é
clicável, devolvendo a linha ao automático.

**Verificado:** numa despesa em aberto projetada em R$ 12.737,86, corrigir para
zero derruba o "a vencer" do mês em exatamente R$ 12.737,86; corrigir para
R$ 50 sobe R$ 50; desfazer devolve ao valor original ao centavo.

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

`config.saldo_conta` é lido na importação, da coluna *saldos (R$)* do extrato.
A data fica em `config.saldo_data` e aparece na tela ao lado do valor.

O Itaú fecha cada dia com uma linha *SALDO TOTAL DISPONÍVEL DIA*, **depois** dos
lançamentos daquele dia. Mas o extrato puxado hoje traz, no máximo, o saldo de
**ontem**: os movimentos de hoje já aparecem e ainda não têm linha de saldo
fechando. Então o saldo é o **último fechamento mais o que veio depois dele**:

```
21/08  PIX ...                       -272,50
21/08  SALDO TOTAL DISPONÍVEL DIA               -111,21   ← último fechamento
22/08  PIX ...                        -50,00              ← ainda sem fechamento
22/08  CRÉDITO ...                   +200,00
                                                  38,79   ← saldo de verdade
```

Ler só o fechamento deixaria a calculadora **um dia atrasada**, e um dia de
movimento pode ser justamente o que decide quanto resgatar. A data devolvida é
a do último movimento contado, não a do fechamento.

A contagem para na marca *"lançamentos futuros"*: dali em diante são
agendamentos, que ainda não saíram da conta.

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

---

## Limpeza do histórico (agosto/2026 em diante)

O histórico anterior a agosto/2026 foi apagado a pedido: 1128 lançamentos, dos
quais **zero** tinham valor real e **zero** tinham transação. Não era histórico —
era a planilha de previsões importada no início, nunca conferida contra extrato.
Projetar sobre ela seria projetar sobre palpites antigos.

Junto, um ajuste pontual (script, não faz parte do app): as despesas e receitas
ativas sem `dia_vencimento`/`dia_recebimento` receberam o dia do movimento real
de agosto/2026. Passaram de 19 para 49 despesas com dia, e de 0 para 9 receitas.
Duas despesas ficaram sem dia por não terem tido movimento no mês
(`Demais compras`, `Cantina Colégio Batista`).

## "Em aberto" + PREVISTO

Item que ainda não foi pago, recebido nem agendado mostra status **Em aberto**
com o selo **PREVISTO** ao lado. O valor daquela linha não veio do extrato: veio
da projeção. Sem dizer isso, um previsto se confunde com um realizado — e a
diferença é exatamente o que separa "já saiu da conta" de "estimativa".
