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

### Ler por coordenada, não por linha de texto

`extract_text()` achata as duas colunas numa linha só. Dá para extrair os dois
lançamentos daí com regex — a primeira versão fazia isso — mas os **marcadores
de seção também caem nas colunas**, e aí um `Lançamentosnocartão(final8051)` da
coluna direita fechava, por estar "depois" no texto, itens da coluna esquerda. O
sintoma foi característico: **total geral certo, totais por cartão errados**.
Nada se perdia; tudo ia para o cartão errado.

A geometria real é regular e idêntica nas três faturas:

| | data | estabelecimento | valor (x1) |
|---|---|---|---|
| coluna A | x≈151 | x≈178 | **x1≈340** |
| coluna B | x≈367 | x≈394 | **x1≈556** |

O corte fica em ~353 — **não** no meio da página (298), que jogava o valor da
coluna A para dentro da coluna B.

> **A âncora tem que ser o valor, não a data.** A segunda tentativa derivou as
> colunas das posições das datas, e piorou tudo: **a parcela (`07/10`) também é
> um token `DD/MM`**, com x próprio (~301), e criava uma coluna fantasma entre o
> valor da A e a data da B. As linhas se despedaçaram — 20 lançamentos lidos
> onde havia 168. Valores são right-aligned em x1 estável; datas não servem.

Clusters de valor com menos de três membros são ignorados: são cabeçalho, rodapé
e as tabelas de simulação de parcelamento, não colunas de lançamento.

### Compra internacional e encargo

Dois casos que só apareceram porque a conferência não fechava, e que **não são
compra comum**:

```
Lançamentosinternacionais          <- bloco próprio, depois dos blocos por cartão
RAFAELINHASZ(final7484)
12/09 ANTHROPIC*CLAUDESUB 116,69   <- entra no total da fatura...
SANFRANCISCO 110,00 BRL 21,57      <- cidade e valor em US$ (sem data: não casa)
Totaltransaçõesinter.emR$ 116,69   <- ...mas tem total próprio
RepassedeIOFemR$ 4,06              <- encargo: sem data, sem estabelecimento
```

O `Lançamentosnocartão(final7484)` cobre **só o doméstico**. Somar a compra
internacional ali acusava +116,69 num cartão que estava certo. E o IOF, que não
é compra mas é dinheiro da fatura, era exatamente o que faltava para o total
fechar: `20.319,61 + 4,06 = 20.323,67`.

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
valor, parcela_n, parcela_total, internacional,
categoria_fatura, cidade, categoria, origem_categoria, criado_em
```

- **`secao`**: `lancamento` (desta fatura) ou `proxima_fatura` (compromisso já
  contratado). Mesma forma, significados opostos: um é gasto do mês, o outro é
  dívida futura. Separá-los é requisito, não detalhe.
- **`estabelecimento`** é o texto cru do PDF, preservado sempre. `_norm` é a
  versão legível (`ARBORETTOCAFEECOZIN` → `Arboretto Café e Cozinha`).
- **`origem_categoria`**: `fatura` | `ia` | `regra` | `manual`, em ordem
  crescente de precedência. Sem isso não há como saber no que confiar nem o que
  a IA acertou.
- **`categoria_fatura`** é o rótulo do próprio emissor, cru e **nunca
  sobrescrito**. É a única categoria que não depende de modelo nenhum, e é
  contra ela que se audita o refino.

### `cartao_categoria_regra` — o que o usuário ensinou

```
id, padrao, categoria, acertos, criado_em
UNIQUE(padrao, categoria)
```

Corrigir a categoria de um estabelecimento **ensina** — exatamente como
`transacao_despesa_regra` faz no batimento (doc 10). Na importação seguinte a
regra vale mais que a IA, porque o usuário sabe mais que o modelo sobre a
própria vida.

## A fatura já categoriza

Descoberto ao conferir os PDFs, e muda o desenho: **o emissor põe uma categoria
em cada compra**, numa linha `CATEGORIA.CIDADE` logo abaixo do lançamento
(`ALIMENTAÇÃO.SAOPAULO`, `VEÍCULOS.SAOPAULO`). É determinístico, é de graça, e
cobre quase tudo:

| fatura | lançamentos do mês | parcelas futuras |
|---|---|---|
| The One | **167/168** | 0/18 |
| Black | **38/39** | 0/4 |
| Azul | **2/2** | 0/1 |

As duas faltas do mês são a **compra internacional** e o **estorno de cashback**
— que o banco realmente não categoriza, e nenhum dos dois é gasto a analisar.

As parcelas futuras não vêm categorizadas em nenhuma fatura: é bloco de
compromisso, não de compra. Mas é o **mesmo estabelecimento** da parcela deste
mês (`ITAUSHOP 07/10` agora, `ITAUSHOP 08/10` na próxima), então a categoria se
**herda por casamento de estabelecimento** — sem IA, sem chute.

> **A linha de categoria nem sempre traz a cidade.** Às vezes quebra e sobra só
> `DIVERSOS.`. Exigir cidade derrubava a cobertura de 100% para 88%.

> **O look-ahead não pode atravessar coluna.** A leitura é coluna a coluna: o
> último lançamento de uma coluna teria como "próxima linha" a primeira da
> coluna seguinte, e pegaria a categoria errada **em silêncio** — nenhuma
> conferência de total acusaria. Medido nas três faturas: zero lançamentos no
> fim de coluna, risco hoje nulo. Ainda assim o parser carrega o id do bloco e
> se recusa a atravessar, porque a próxima fatura pode ter outro layout.

## O papel da IA, e o que ela não faz

Com a categoria do emissor na mão, a IA deixa de ser a fonte da categoria — o
que é bom, porque a fonte determinística é melhor. Ela passa a fazer só o que
regex não faz.

Gemini (já configurado, `GEMINI_API_KEY`, mesmo cliente de `email_busca.py`) faz
**duas coisas**:

1. **Normalizar o estabelecimento** — desfazer o texto colado e devolver o nome
   legível (`ARBORETTOCAFEECOZIN` → "Arboretto Café e Cozinha").
2. **Refinar a categoria** — porque os baldes do emissor são grossos demais para
   a pergunta "o que dá para cortar":

| balde do emissor | o que esconde | por que importa |
|---|---|---|
| `ALIMENTAÇÃO` R$ 8.736,94 | Sacolão e Pão de Açúcar junto com Arboretto (12x) e Homem de Mello (16x) | **supermercado ≠ restaurante** — é o corte mais acionável da fatura |
| `TURISMOEENTRETENIM` R$ 3.972,07 | `Apple.com/Bill` 4x, `GoogleOne` | são **assinaturas**, o sinal de corte mais óbvio, perdidas dentro de "viagem" |
| `DIVERSOS` R$ 3.088,88 | Prudential (seguro), Sephora (cosmético), Vivara (joia), Panini (brinquedo) | 23 lançamentos num balde que não responde nada |
| `EDUCAÇÃO` R$ 1.191,60 | `MP*GOCASE` (capinha de celular) | o emissor também erra |

A categoria do emissor fica gravada em `categoria_fatura` e **não é
sobrescrita**: se o refino errar, dá para voltar.

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

| conferência | fonte no PDF | o que entra |
|---|---|---|
| soma por portador | `Lançamentosnocartão(finalXXXX)` | só compra **doméstica** |
| soma de todos os lançamentos | `Totaldoslançamentosatuais` | doméstica + internacional + encargo |
| soma das parcelas futuras | **`Próximafatura`** | o bloco lista só a próxima |

> **A conferência das parcelas mirava o número errado.** `Totalparapróximasfaturas`
> (18.792,38) soma **todas** as parcelas que ainda faltam; o bloco lista apenas
> as da **próxima** fatura (4.314,76). O PDF traz os dois, e
> `Próxima + Demais = Total` fecha nas três faturas — o alvo certo é `Próximafatura`.

### Resultado

As três faturas fecham **ao centavo**, nas três conferências:

| fatura | itens | por cartão | total geral | próxima fatura |
|---|---|---|---|---|
| The One | 167 dom. + 1 inter. + 1 encargo + 18 futuras | 9/9 ✓ | 20.323,67 ✓ | 4.314,76 ✓ |
| Black | 39 + 4 futuras | 4/4 ✓ | 5.166,19 ✓ | 1.247,69 ✓ |
| Azul | 2 + 1 futura | 2/2 ✓ | 1.166,61 ✓ | 1.105,11 ✓ |

Quinze seções de cartão conferidas, zero divergências.

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

## O que ficou implementado, e o que foi medido

### A taxonomia do app

Vinte categorias, mais finas que as oito do emissor porque a pergunta é outra:
ele classifica para faturar, o app classifica para **cortar**.

```
Supermercado · Restaurante e bar · Delivery · Combustível · Transporte ·
Saúde · Farmácia · Educação · Vestuário · Beleza · Assinaturas · Viagem ·
Lazer · Presentes · Seguros · Casa · Esporte · Eletrônicos · Filhos ·
Outros · Encargos
```

### Precedência, da mais fraca para a mais forte

| origem | quem decide |
|---|---|
| `fatura` | o rótulo do emissor, traduzido para a taxonomia do app |
| `ia` | Gemini, só onde o balde era grosso ou não havia nada |
| `regra` | palavra-chave do app **ou** o que o usuário já ensinou |
| `manual` | o usuário, nesta fatura |

### Medido nas faturas reais

**Só com o caminho determinístico**, sem IA nenhuma: `fatura` 107 + `regra` 101
nos lançamentos do mês, e **R$ 344,47 de R$ 26.652,41 sem classificar — 1,3%**.
O dicionário de palavras-chave sozinho já corrige erros do emissor: `MP*GOCASE`
sai de `EDUCAÇÃO` para Eletrônicos.

**Com a IA ligada** (The One, 187 itens): sobra **zero** não classificado, 93
lançamentos ganham nome legível e ela muda **12** categorias. As que importam
são exatamente a divisão que o emissor não faz:

| estabelecimento | antes | depois |
|---|---|---|
| Minuto Pão de Açúcar | Restaurante e bar | **Supermercado** |
| Clement's Carnes | Restaurante e bar | **Supermercado** |
| Hiper Zaffari São Paulo | Restaurante e bar | **Supermercado** |
| Google Cloud | Não classificado | **Assinaturas** |
| Iupp Tag Itaú | Não classificado | **Transporte** |

A normalização entrega o que regex não entrega: `ARBORETTOCAFEECOZIN` →
"Arboretto Café e Cozinha", `BacioDiLatte` → "Bacio di Latte".

> **Encargo não é compra.** O IOF entrou na IA na primeira rodada e voltou como
> "Repasse de IOF em R$ → Outros", poluindo a análise com uma linha que ninguém
> decidiu gastar. Agora `secao='encargo'` recebe a categoria `Encargos` direto e
> nunca é enviado ao modelo.

> **A IA só é creditada quando muda algo.** Marcar `ia` também onde ela apenas
> confirmou o que a fatura já dizia inflava o contador de 12 para 88 — e o
> resumo de origens existe justamente para o usuário saber em quanto do
> resultado o modelo opinou. Confirmar não é opinar.

## Detalhe por categoria, e conselho de verdade

Pedido depois da primeira entrega: ver o **detalhe de cada categoria**, e
sugestões bem mais inteligentes — *"em alguns casos a pergunta será se preciso
realmente gastar o que foi gasto. Em outros casos, se posso comprar as mesmas
coisas gastando menos."*

### Três níveis, porque a decisão não está no resumo

`categoria → estabelecimento → lançamento`. A categoria diz onde o dinheiro
está; o estabelecimento é onde a decisão acontece. "Restaurante e bar R$
5.074,67" não é acionável. "Arboretto, 12 vezes, R$ 91,75 em média" é.

### A fronteira da IA muda aqui — de propósito

Até agora a IA **não lia valores**, e essa regra continua valendo para
**extração**: valor, data e parcela saem do parser, sempre. Conselho é outra
coisa. Não existe resposta para "precisava gastar isso?" sem ver quanto, quantas
vezes e com que regularidade.

| a IA passa a ler | a IA nunca faz |
|---|---|
| agregados: total, contagem e ticket médio por categoria e estabelecimento | escrever valor na base |
| parcelamentos: quanto sai por mês e quanto ainda falta | criar, apagar ou alterar lançamento |
| repetição, micro-compras, cobranças no mesmo dia | citar estabelecimento que não está no dossiê |

Extração é determinística, análise é opinativa. Misturar as duas é o que produz
número errado com cara de certeza.

### As duas perguntas, separadas na tela

| tipo | pergunta | exemplo real do mês |
|---|---|---|
| `necessidade` | precisava gastar isso? | 41 compras em três lugares, R$ 2.548,49 |
| `preco` | dava para gastar menos na mesma coisa? | R$ 603,98/mês de assinaturas = R$ 7.247/ano |

São decisões de natureza diferente: cortar um hábito não é o mesmo que trocar de
fornecedor, e embaralhar os dois deixa o conselho inútil.

### O que out/2026 revelou, e a primeira entrega não mostrava

- **R$ 23.789,32 ainda vão chegar** em parcelas já contratadas — **3,5x** o que
  a tela chamava de comprometido, que era só a próxima fatura (R$ 6.667,56).
- **Só R$ 1.100,80 foi decisão nova** deste mês em parcelamento; R$ 6.170,00 são
  parcelas de decisões antigas e R$ 19.381,61 à vista. Quase um quarto da fatura
  já estava contratado antes de o mês começar.
- **R$ 2.548,49 em 41 compras** em três lugares (Homem de Mello 16x, L.G.A. 13x,
  Arboretto 12x). É hábito, não decisão — e é onde o corte pesa.
- **Apple.com/Bill 4x no mês**, duas no mesmo dia: várias assinaturas somadas
  num nome só.
- **HMODONTOLOGIA, duas cobranças de R$ 1.160,00 no mesmo dia.** Pode ser
  entrada mais parcela, pode ser cobrança repetida. O app aponta; conferir é do
  usuário.

> A estimativa de parcelamento supõe **parcelas iguais** — é o que a fatura
> permite afirmar, e vai rotulada como estimativa por isso.

### Validação do que o modelo devolve

Sem isto o conselho é chute com aparência de análise:

- o **alvo** tem de existir no dossiê (uma categoria ou um estabelecimento);
- a **economia estimada** não pode passar o que foi gasto naquele alvo no mês;
- sugestão sem diagnóstico ou sem ação concreta é descartada.

### Economia pontual não é economia mensal

A primeira rodada de sugestões somou **R$ 4.003,80/mês** e, multiplicando,
anunciaria R$ 48 mil por ano. O número era enganoso, e de três formas
diferentes:

| alvo | o que a IA propôs | por que não fecha |
|---|---|---|
| Sephora R$ 561,00 | parar de comprar cosméticos | **uma compra só** no mês — não se repete |
| Palmeiras Store R$ 250,00 | parar compras por impulso | idem, gasto pontual |
| MP*GOCASE R$ 415,50 | cessar compras de acessórios | é **parcelamento** (2x R$ 207,75): já contratado, não há o que cortar |

Então a recorrência **não** é opinião do modelo — sai dos dados, antes de ele
falar:

| classe | critério | o que a economia significa |
|---|---|---|
| `recorrente` | 3+ compras no mês no mesmo lugar | hábito: a economia se repete todo mês |
| `pontual` | 1 ou 2 compras, sem parcela | **ganho único**, nunca multiplicado por 12 |
| `comprometido` | tem parcela em andamento | não se corta agora; o que volta, volta quando a parcela acabar |

A tela mostra **três totais separados** e jamais um só: economia recorrente por
mês, ganho pontual (uma vez) e o que se libera quando os parcelamentos
terminarem. Somar os três num número anual é o tipo de conta que faz a análise
inteira perder credibilidade.

> Com **um único mês importado** não existe prova de repetição: `recorrente`
> ainda é inferido da frequência dentro do mês, não medida entre meses. A tela
> diz isso, em vez de fingir precisão.

## Mais de um emissor: um parser por layout

A primeira versão supôs que "fatura de cartão" fosse uma coisa só. Não é: o
parser do Itaú é inteiramente modelado no layout dele — duas colunas
entrelaçadas, valor right-aligned em x1 estável, marcadores
`Lançamentosnocartão(finalXXXX)` e a linha `CATEGORIA.CIDADE`. Nada disso
aparece no Mercado Pago nem no Bradesco.

A saída é **um parser por emissor, com contrato único**:

```
detectar(texto) -> 'itau' | 'mercadopago' | 'bradesco'
parse_fatura()  -> despacha e devolve sempre
                   {cabecalho, itens, totais_cartao, conferencia}
```

Quem consome — persistência, categorização, análise — não sabe de qual banco
veio a fatura. Acrescentar um emissor é escrever um parser e uma regra de
detecção, sem tocar em nada do resto.

### Mercado Pago

Coluna única e linhas bem formadas: regex sobre texto basta, sem geometria.
Cinco páginas, mas só as duas primeiras têm conteúdo — as outras são marketing
e texto legal, inclusive uma seção "Compras internacionais" que **não tem
lançamento nenhum**, só a explicação da alíquota de IOF.

| diferença | consequência |
|---|---|
| parcela escrita por extenso: `Parcela 4 de 12` | o `PP/TT` do Itaú não casa |
| dois blocos: `Movimentações na fatura` e `Cartão Visa [...]` | o primeiro é encargo e pagamento, **não compra** |
| `Pagamento da fatura de setembro/2026 R$ 710,59` | é **pagamento**: entra como `secao='pagamento'` e fica fora de todo total |
| nenhuma categoria do emissor | a IA e as palavras-chave fazem todo o trabalho |

A conferência fecha ao centavo, e por dois caminhos independentes que o próprio
PDF publica:

```
encargos  3,24 + 2,14 + 14,22 + 38,16 = 57,76
consumos                              158,98   <- "Total" do bloco do cartão
                                       ------
total                                  216,74   <- "Total a pagar" do topo
```

E o resumo da página 1 quebra o mesmo número de outra forma
(`Tarifas e encargos 3,24` + `Multas por atraso 14,22` +
`Juros do mês anterior 40,30`, onde 40,30 = 2,14 + 38,16), o que dá uma segunda
prova de leitura.

> **Não deixe um `.*?` correr atrás de um rótulo.** O pagamento mínimo custou
> três tentativas. A palavra "mínimo" aparece **nove vezes** nesta fatura, e a
> primeira é a explicação (`Pagando o valor mínimo, a diferença...`), não o
> valor. Um padrão `m[íi]nimo.*?R\$` casa ali e então atravessa o documento
> inteiro até o próximo `R$`, devolvendo R$ 710,59 — o pagamento da fatura
> anterior. Uma variante devolveu R$ 9,90, a tarifa de saque. O que funciona é
> a **frase inteira**: `valor mínimo que você deve pagar é de R$ 81,61`, com
> `\s*` entre as palavras, porque na página 4 ela vem colada
> (`OvalormínimoquevocêdevepagarédeR$81,61`). Mesma lição do PU e dos totais:
> ancorar no rótulo completo, nunca numa palavra que se repete.

### Bradesco (cartão Amazon)

Aqui o documento **não é uma fatura fechada** — é um extrato em aberto, e ele
avisa: *"Valores sujeitos a alteração até o fechamento da fatura."*

| diferença | consequência |
|---|---|
| **não existe data de vencimento** em nenhuma página | quebra a chave `UNIQUE(cartao, data_vencimento)` |
| `Situação do Extrato: EM ABERTO` | o número pode mudar amanhã: a tela precisa dizer isso |
| seis colunas, várias zeradas de câmbio | o valor em R$ é a **última**, x1≈549 — não a primeira que casa |
| valor negativo (`-11,98`) | crédito legítimo, não erro de leitura |
| página 2 completamente vazia | 0 caracteres: iterar páginas sem checar quebra |

> **O nome do estabelecimento se parte acima e abaixo da linha do valor.** Em
> `AMAZONMKTPLC*CMCOMERCI` os `top` são 602 (início do nome), 609 (data e valor)
> e 615 (`SA 3/12`, o resto do nome mais a parcela). Agrupar por linha, como no
> Itaú, despedaçaria o lançamento. Aqui a âncora é o **valor**: para cada valor
> em x1≈549, o nome se monta com as palavras da faixa da coluna Histórico cujo
> `top` esteja a ±12px dele.

A conferência também fecha ao centavo, por um invariante que o PDF permite
montar:

```
soma dos 10 lançamentos listados      =   706,99
Total para RAFAEL INHASZ  5.016,24
Total da Fatura em Real   4.309,25
                          ---------
diferença                   706,99   <- igual à soma. Fecha.
```

### A decisão sobre extrato em aberto

Importar ou recusar? Recusar seria perder o cartão inteiro. Importar com data de
vencimento inventada seria pior: um campo que mente.

A solução tem três partes:

1. **`situacao`** (`fechada` | `aberta`) na fatura, para a tela nunca apresentar
   número provisório como definitivo.
2. **`data_extrato`**, que é o que o documento realmente traz.
3. **Índice único em `(cartao, mes_ref)`**, porque `mes_ref` é estável nos dois
   casos — enquanto `data_vencimento` pode ser nula, e em SQLite dois `NULL` não
   colidem, de modo que reimportar duplicaria a fatura silenciosamente.

O `mes_ref` do extrato em aberto sai do **mês seguinte ao da extração**: um
extrato tirado em 30/09 é a fatura que vence em outubro, e é assim que ele fica
comparável com as outras quatro, todas em `2026-10`.

E uma quarta parte, que só apareceu quando as cinco faturas ficaram na mesma
tela: **o total de um extrato aberto não é comparável com o das faturas
fechadas.** O do cartão Amazon soma R$ 5.016,24, mas R$ 4.309,25 disso é saldo
anterior que o PDF **não detalha lançamento a lançamento** — só os R$ 706,99 de
consumo do período entram na análise. Exibir 5.016,24 na lista de faturas
enquanto a análise conta 706,99 seria o mesmo defeito do IOF: um total que não
fecha com o resto da tela. Então para `situacao='aberta'` a lista mostra o
**consumo**, com o total da fatura no tooltip.

O teste disso é uma soma: a coluna que a lista exibe tem de dar exatamente
`gasto + encargos` da análise.

```
20.323,67 + 5.166,19 + 1.166,61 + 216,74 + 706,99 = 27.580,20
gasto 27.518,38 + encargos 61,82              = 27.580,20
```

## O que esta fase deliberadamente não faz

- **Não lança no Mês Atual.** A fatura já entra lá como uma despesa só; duplicar
  cada item viraria dois registros do mesmo dinheiro.
- **Não importa CSV nem HTML.** Existe um `fatura-20260401.csv` com formato
  limpo, mas ele cobre um cartão e um mês; o PDF é o que o banco entrega para
  todos. Fica como fonte alternativa futura.
- **Não corta nada sozinho.** Sugere candidatos; a decisão é do usuário.
