# Análise de fatura de cartão de crédito

**Status:** especificação. Escrita antes da implementação, como todo o resto do
app, para poder ser refeita em outra linguagem ou plataforma.

## O problema

Hoje o cartão entra no app como **uma linha só**: "cartao one itau — R$ 20.323,67".
Isso paga a conta, mas não responde nada. A fatura de outubro/2026 do The One tem
**119 lançamentos** espalhados por **10 portadores**, e a pergunta que importa —
*onde está indo esse dinheiro, e o que dá para cortar* — exige olhar linha a linha.

As três perguntas que esta função existe para responder:

1. **Onde estou gastando?** Por categoria, por estabelecimento, por portador.
2. **Quanto já está comprometido?** Parcelamento contratado não é decisão deste
   mês — é dívida que vai chegar de qualquer jeito.
3. **O que dá para cortar?** Assinatura esquecida, gasto recorrente que cresceu,
   concentração num estabelecimento.

## A fonte: PDF da fatura

Conferido nas três faturas reais de outubro/2026 (The One, Black, Azul). O texto
**extrai sem OCR** (`pdfplumber`), mas vem com quatro armadilhas:

| armadilha | exemplo real |
|---|---|
| **sem espaços** entre palavras | `BacioDiLatte`, `ARBORETTOCAFEECOZIN` |
| **duas colunas** por linha de texto | `01/03 ITAUSHOP 07/10 31,69 27/08 NUTRICARBRASILCOMERC 4,99` |
| **parcela colada** no estabelecimento | `MAGACARCAUTOMOT07/10 54,40` |
| blocos **entrelaçados** com texto da coluna vizinha | `21/03 MAGACARCAUTOMOT08/10 54,40 Osjuroseencargosquevocê...` |

A consequência da última é a regra mais importante do parser: **reconhecer pela
forma da linha, nunca pela posição na página**. Um bloco não termina onde parece
terminar.

### Gramática de um lançamento

```
DD/MM   <estabelecimento>   [PP/TT]   [-]9.999,99
```

`PP/TT` é a parcela (7 de 10). Distingue-se da data porque vem **depois** do
estabelecimento, e da própria data de compra porque esta abre a linha. Valor
negativo existe e é legítimo: `PROGRAMADECASHBACK -54,00`.

Testado contra os quatro casos difíceis (parcela solta, parcela colada, compra
simples, crédito negativo) — os quatro saem corretos com uma expressão só.

### Seções

| marcador | o que delimita |
|---|---|
| `RAFAELINHASZ(final8051)` | início dos lançamentos de um portador/cartão |
| `Lançamentosnocartão(final8051) 9.123,13` | fim daquele portador **e o total de conferência** |
| `Comprasparceladas-próximasfaturas` | parcelas já contratadas que ainda vão chegar |
| `Totalparapróximasfaturas 18.792,38` | total do compromisso futuro |

O portador importa: a fatura do The One tem 10 cartões adicionais, e "quem
gastou" é uma dimensão de análise por si só.

## Modelo de dados

Três tabelas, no padrão do resto do app — foto importada, itens, e regras
aprendidas.

### `fatura_cartao` — uma por arquivo importado

```
id, cartao, final, arquivo, mes_ref, data_vencimento,
total_fatura, total_lancamentos, total_proximas_faturas,
pagamento_minimo, limite_total, criado_em
UNIQUE(cartao, data_vencimento)
```

`UNIQUE` para reimportar substituir, nunca duplicar — mesma regra do extrato
bancário (doc 10).

### `fatura_item` — uma por lançamento

```
id, fatura_id, secao, portador, cartao_final,
data_compra, estabelecimento, estabelecimento_norm,
valor, parcela_n, parcela_total,
categoria, origem_categoria, criado_em
```

- **`secao`**: `lancamento` (desta fatura) ou `proxima_fatura` (compromisso já
  contratado). Mesma forma, significados opostos: um é gasto do mês, o outro é
  dívida futura. Separá-los é requisito, não detalhe.
- **`estabelecimento`** é o texto cru do PDF, preservado sempre. `_norm` é a
  versão legível (`ARBORETTOCAFEECOZIN` → `Arboretto Café e Cozinha`).
- **`origem_categoria`**: `regra` | `ia` | `manual`. Sem isso não há como saber
  no que confiar nem o que a IA acertou.

### `cartao_categoria_regra` — o que o usuário ensinou

```
id, padrao, categoria, acertos, criado_em
UNIQUE(padrao, categoria)
```

Corrigir a categoria de um estabelecimento **ensina** — exatamente como
`transacao_despesa_regra` faz no batimento (doc 10). Na importação seguinte a
regra vale mais que a IA, porque o usuário sabe mais que o modelo sobre a
própria vida.

## O papel da IA, e o que ela não faz

Gemini (já configurado, `GEMINI_API_KEY`, mesmo cliente de `email_busca.py`) faz
**duas coisas**:

1. **Normalizar o estabelecimento** — desfazer o texto colado e devolver o nome
   legível.
2. **Categorizar** — atribuir categoria a partir do nome do estabelecimento.

E **não faz** nenhuma destas, por decisão explícita:

- **não lê valores.** Valor, data e parcela saem do parser determinístico. Um
  modelo que erra um dígito num valor produz uma análise errada e silenciosa;
  regex sobre texto estruturado, não.
- **não decide o que é parcela.** Isso é forma, e forma é do parser.
- **não inventa lançamento.** O conjunto de itens é fechado pelo parser antes de
  a IA ver qualquer coisa; ela só anota.

É a mesma divisão de trabalho da busca de boletos (doc 08), onde o código já
valida tudo que o modelo devolve. Aqui a validação é estrutural: a IA recebe N
estabelecimentos e precisa devolver N categorias, nada mais.

**Sem chave de IA o app continua funcionando**: cai para as regras aprendidas
mais um dicionário inicial de palavras-chave, e o que não casar fica em
`Não classificado` — visível, para o usuário resolver.

## Conferência: a fatura tem que fechar

O PDF traz os próprios totais, então o parser pode ser verificado contra ele —
e deve, porque um lançamento perdido numa coluna passa despercebido:

| conferência | fonte no PDF |
|---|---|
| soma dos itens de cada portador | `Lançamentosnocartão(finalXXXX)` |
| soma de todos os lançamentos | `Totaldoslançamentosatuais 20.323,67` |
| soma das parcelas futuras | `Totalparapróximasfaturas 18.792,38` |
| total da fatura | `=Totaldestafatura` |

A importação **mostra as diferenças antes de gravar**. Divergiu, não importa.

Há ainda uma quarta conferência, contra o próprio app: o total da fatura tem que
bater com o lançamento da despesa correspondente (`cartao one itau`) no Mês
Atual. É o que liga a análise ao fluxo de caixa que já existe.

## As análises

### Onde estou gastando

Por **categoria**, por **estabelecimento** e por **portador**, com comparação
contra os meses anteriores já importados. A comparação é o que transforma um
número em informação: R$ 90 no Arboretto não diz nada; R$ 90 **vinte e duas
vezes no mês** diz.

### O que já está comprometido

O bloco de parcelas futuras somado por mês de chegada. Em outubro/2026 o The One
sozinho tem **R$ 18.792,38** já contratados, sendo R$ 4.314,76 na próxima fatura
— dinheiro que vai sair independentemente de qualquer decisão deste mês.

### Alternativas para reduzir

Candidatos a corte, cada um com o critério que o tornou candidato:

| sinal | critério | por que é acionável |
|---|---|---|
| **assinatura** | mesmo valor, mesmo estabelecimento, 3+ meses seguidos | cancelável hoje, efeito permanente |
| **repetição** | mesmo estabelecimento 10+ vezes no mês | hábito, não decisão — é onde o corte pesa |
| **salto** | categoria 50%+ acima da média dos meses anteriores | mudança recente, ainda reversível |
| **parcela terminando** | `PP/TT` com `PP == TT` | dinheiro que volta ao orçamento mês que vem |

O último é o mais útil e o menos óbvio: saber que uma parcela acaba é saber
quanto de fôlego chega sem precisar cortar nada.

## O que esta fase deliberadamente não faz

- **Não lança no Mês Atual.** A fatura já entra lá como uma despesa só; duplicar
  cada item viraria dois registros do mesmo dinheiro.
- **Não importa CSV nem HTML.** Existe um `fatura-20260401.csv` com formato
  limpo, mas ele cobre um cartão e um mês; o PDF é o que o banco entrega para
  todos. Fica como fonte alternativa futura.
- **Não corta nada sozinho.** Sugere candidatos; a decisão é do usuário.
