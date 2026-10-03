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

No terceiro nível cada lançamento mostra **de qual cartão saiu**, com o final
quando existir (`The One ·1801`). Isso deixou de ser detalhe quando passaram a
conviver cinco faturas de três emissores no mesmo mês: sem a origem, duas
compras iguais no mesmo dia são indistinguíveis, e o final é o que separa entre
si os dez adicionais do The One. O portador aparece ao lado, mas só quando há um
— Mercado Pago e Bradesco não trazem portador, e um campo vazio ali só abriria
um vão na linha.

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

Três páginas: a primeira é cabeçalho, resumo e boleto; a segunda tem **todos** os
lançamentos; a terceira é só a vitrine de opções de parcelamento, sem lançamento
nenhum.

A página 2 é de **duas colunas**, e elas se achatam juntas no texto — a mesma
armadilha do Itaú:

```
21/03 AMAZONMKTPLC*VOOLTINDU SAO PAULO(06/06) 21,65 Demais faturas R$ 1.360,26
                                                    ^^^^^^^^^^^^^^^^^^^^^^^^^^
                                                    isto é da coluna da direita
```

Aqui, porém, separar é fácil: **todos** os 29 valores de lançamento ficam em
x1=288, e nada da coluna da direita aparece antes de x≈410. Um corte em 310
resolve, sem precisar do agrupamento por cluster que o Itaú exigiu.

| diferença | consequência |
|---|---|
| parcela **entre parênteses e colada na cidade**: `PAULO(04/17)` | nem o `PP/TT` do Itaú nem o "Parcela N de M" do Mercado Pago casam |
| **sinal de menos no fim**: `1.224,70-` | é pagamento recebido; lido como positivo, inverteria o saldo |
| sufixo `BRA` | marca de país, não parte do nome |
| cidade embutida na descrição | sem separador confiável — ver abaixo |
| nenhuma categoria do emissor | regras e IA fazem todo o trabalho |

> **A cidade fica dentro do `estabelecimento`, de propósito.** Não há como
> separá-la com segurança: o espaço entre nome e cidade varia de 2px
> (`AMAZONMKTPLC*VOOLTINDU SAO`) a 25px (`G LUCCO    SAO`), então qualquer
> heurística de distância erraria. O campo guarda o texto cru, como o resto do
> doc manda, e quem produz o nome legível é a normalização da IA — que não se
> incomoda com a cidade sobrando.

#### Duas provas independentes, e a segunda valida uma suposição

A primeira é o total, e o resumo da página 1 a monta:

```
Saldo anterior              1.224,70
(-) Créditos/Pagamentos     1.224,70-
(+) Compras/Débitos         3.602,26
(=) Total                   3.602,26
```

A soma dos lançamentos **sem o pagamento** tem de dar 3.602,26.

A segunda é a mais valiosa de todo o documento. As parcelas em andamento são
`(06/06)`, `(04/17)` e `(02/12)`; supondo **parcelas iguais**, faltam
`13 × 93,48 + 10 × 26,50 = 1.480,24` — exatamente o `Total para as próximas
faturas` que o banco publica. E a soma das próximas parcelas,
`93,48 + 26,50 = 119,98`, bate com o `Próxima fatura` dele.

Isso importa além desta fatura: a estimativa de parcelas iguais, que o doc até
aqui rotulava como suposição, está **conferida contra o emissor**.

> Esta fatura não traz lista itemizada de parcelas futuras, só os totais — por
> isso não há itens `secao='proxima_fatura'` aqui, e essa conferência é feita
> contra os parcelamentos derivados dos próprios lançamentos.

### Porto Seguro (PORTOSEG)

Duas páginas, e a segunda é só boleto — os lançamentos cabem todos na primeira,
num `DEMONSTRATIVO DE DESPESAS` que é a coluna da direita. A coluna da esquerda
(resumo e encargos) se achata junto no texto, como em Itaú e Bradesco:

```
Saldo 3.071,20 27/07/2 PORTO SEGURO AUTO PA/03 412,32
      ^^^^^^^^ coluna esquerda      lançamento ^^^^^^
```

A geometria é rígida e idêntica nas seis linhas:

| | x |
|---|---|
| data | x0 = **290,0** |
| descrição | x0 = **324,0** |
| parcela | x0 ≈ 403–449 |
| valor | x1 = **553,0** |
| (resumo da esquerda) | valor em x1 = 282 |

#### Quatro coisas que nenhum outro emissor tinha

| achado | consequência |
|---|---|
| **duas notações de parcela na mesma fatura**: `PA/03` e `09/12` | `PA/NN` traz o número **sem o total** — `parcela_total` fica nulo, e esses papéis legitimamente não entram no "ainda vai chegar", porque não se sabe quantas faltam |
| **data truncada**: `27/07/2` | o ano vem cortado em um dígito; lê-se `DD/MM` e o ano sai do vencimento, como nos outros |
| valores **sem `R$`** no cabeçalho (`3.019,90`) | padrões que exigem `R$` não casam nada aqui |
| **menos à esquerda**: `-2.693,62` | o Bradesco põe à direita (`1.224,70-`); as duas convenções agora coexistem |

> **`09/12` é indistinguível de uma data pelo formato.** O que as separa é só a
> coordenada: a parcela fica em x0≈429, a data em x0=290. Um parser de linha de
> texto confundiria as duas — foi o exame das coordenadas que evitou isso.

> **`PORTO SEGURO AUTO` é compra de seguro**, e casa a palavra-chave
> `porto ?seg` → Seguros, sem precisar de IA. Mas isso proíbe usar `SEGURO` como
> marca de encargo: a compra viraria encargo. Encargo aqui é só
> `ANUIDADE|TARIFA|IOF|JUROS|MULTA|MORA|ENCARGO` — e `ANUIDADE DIFERENCIADA`,
> cobrada em 12 parcelas, é encargo, não compra.

#### Conferência: três números concordam, um não

As despesas, sem o pagamento, somam **3.071,20** — e o PDF confirma isso por
três caminhos independentes:

```
Despesas/Debitos (+)              3.071,20
PAGAMENTO TOTAL                   3.071,20
RAFAEL INHASZ - NR.3151:      R$  3.071,20
```

O quarto número **não fecha e fica registrado como inexplicado**:
`Total Nacional -2.316,04`. A hipótese é que subtraia o pagamento duas vezes
(`3.071,20 − 2.693,62 − 2.693,62 = −2.316,04`), mas é hipótese, não leitura — e
por isso a conferência se apoia nos três que concordam, nunca nele.

> O PDF também vem com **mojibake** em parte do texto (`perÃ odo`, `4Âº`): UTF-8
> lido como Latin-1. Não afeta estes seis lançamentos, mas afetaria um
> estabelecimento com acento.

### O extrato em aberto, e por que a máquinaria ficou

Houve uma volta falsa aqui, e ela vale registro porque moldou o schema. A
primeira amostra do cartão Amazon era um **extrato em aberto** — não uma fatura
— baixado por engano: sem data de vencimento em página nenhuma, avisando
*"Valores sujeitos a alteração até o fechamento"*, e com o total trazendo saldo
anterior não detalhado. O arquivo foi substituído pela fatura de verdade e já não
existe em disco, então o parser dele **foi removido**: manter código que não se
pode mais testar é pior que não tê-lo.

O que ficou, porque continua certo por mérito próprio:

1. **Índice único em `(cartao, mes_ref)`** em vez de
   `(cartao, data_vencimento)`. Mesmo cartão, mesmo mês é a mesma fatura — e
   `mes_ref` não pode ser nulo, enquanto `data_vencimento` pode; em SQLite dois
   `NULL` não colidem, e reimportar duplicaria em silêncio.
2. **`situacao`** (`fechada` | `aberta`) e **`data_extrato`**, hoje sempre
   `fechada`. Extrato em aberto é um documento real que o usuário pode baixar de
   novo; quando acontecer, precisa de um ramo próprio no parser — e a tela já
   sabe avisar que número provisório não é definitivo.
3. **`emissor`** na fatura, que é o que torna a lista de faturas legível quando
   há cinco cartões de três bancos.

A lição é a mesma do PU e do pagamento mínimo: **o documento manda, não a
suposição sobre ele.** Um parser inteiro foi escrito para um layout que o usuário
nunca quis importar.

## Estratégia por categoria: a alavanca é da categoria, não do lançamento

Pedido do usuário, e ele está certo de um jeito que conserta um defeito meu: *"em
dias de semana eu preciso almoçar fora, e finais de semana não necessariamente…
em Saúde não dá para economizar… em Farmácia eu não vou conseguir comprar menos,
mas podemos olhar os produtos e comprar no atacado"*.

O motor de sugestões até aqui aplicava **um único modelo mental** a tudo — corte
a frequência ou troque de fornecedor. Foi isso que produziu "pausar aquisições"
num parcelamento já contratado e "moratória de maquiagem" numa compra única. A
correção não é um prompt melhor: é declarar, por categoria, **qual alavanca
existe** — e só deixar a IA propor daquele tipo.

### As cinco alavancas

| alavanca | pergunta que ela autoriza | exemplo |
|---|---|---|
| `volume` | precisava gastar isso? | restaurante no fim de semana |
| `preco` | dava para pagar menos pela mesma coisa? | supermercado, combustível |
| `substituicao` | dava para trocar de fornecedor ou plano? | assinaturas, seguros |
| `nenhum` | **nenhuma** — a categoria sai das sugestões | Saúde, Encargos, Pagamento |
| `requer_detalhe` | a alavanca existe, mas falta dado | Farmácia |

`nenhum` é o mais valioso dos cinco. Saúde são 7 compras, todas acima de R$ 200,
mediana R$ 541,74 — dentista, Invisalign, clínica. Qualquer sugestão ali é ruído
que gasta a atenção do usuário. **A categoria simplesmente não entra no dossiê.**

`requer_detalhe` é o segundo. Em Farmácia a alavanca é de preço (atacado,
genérico, outra rede), mas exercê-la exige saber **quais produtos** — e isso só
vem da nota item a item. Até chegar, o app diz "aguardando dado" em vez de
inventar conselho.

### Segmentação: o mesmo gasto, decisões diferentes

Restaurante e bar, R$ 5.149,99 em 76 compras, parte em dois comportamentos
distintos:

| | valor | compras | ticket |
|---|---|---|---|
| dia útil | R$ 3.990,11 | 66 | R$ 60,46 |
| fim de semana | R$ 1.159,88 | 10 | **R$ 115,99** |

Dia útil é **alta frequência e ticket baixo** — hábito, 66 ocorrências, e a meta
razoável é marginal. Fim de semana é **baixa frequência e ticket dobrado** —
cada um é uma decisão, e a meta pode ser agressiva. Metas iguais para os dois
seriam erradas nos dois.

A regra é **seg-sex / sáb-dom**, escolhida pelo usuário.

#### Sexta é dois comportamentos no mesmo dia

**Sexta é o maior dia da categoria: R$ 1.182,08 em 16 compras**, mais que sábado
e domingo somados (R$ 1.159,88). E é o único dia que contém as duas coisas:
almoço de trabalho, que não se evita, e jantar, que se evita.

O critério, dado pelo usuário, é **o lugar**: estabelecimento nas proximidades do
Itaim Bibi é almoço de trabalho; fora dali, numa sexta, é jantar — e jantar conta
no balde do fim de semana.

> **A fatura não traz bairro.** O campo `cidade` existe em 204 dos 243 itens
> (84%), mas só nas faturas Itaú e com valor de **cidade**: `SAOPAULO` em 158
> itens, mais Barueri, Osasco, Guarulhos. Para esses 158 não há como distinguir
> Itaim de Perdizes. A exceção é curiosa e pequena: em **2 itens** o emissor
> escreveu `Itaim` nesse campo em vez da cidade.

Como o dado não existe, o lugar é **marcado no estabelecimento**, não inferido do
endereço:

```
estabelecimento_local(padrao, local, origem)
   local  = trabalho | outro
   origem = fatura | ia | manual
```

Marca-se uma vez e vale para sempre, como a regra de categoria — e o custo é
pequeno: a categoria inteira tem **34 estabelecimentos distintos**, 11 deles
aparecendo em sextas.

| origem | quem decidiu |
|---|---|
| `fatura` | o emissor escreveu `Itaim` no campo cidade — evidência, não palpite |
| `pesquisa` | busca na web pelo nome, com o endereço guardado em `evidencia` |
| `ia` | o modelo propôs de memória; vale até o usuário dizer o contrário |
| `manual` | o usuário marcou, e isso vence tudo |

Toda marcação guarda **`evidencia`**: o endereço achado e de onde veio. Sem
rastro, ninguém sabe em seis meses por que Burdog está `outro`, e uma marcação
sem rastro é palpite com aparência de dado.

#### O que a busca resolveu

Pesquisar o nome do estabelecimento funcionou melhor que qualquer inferência —
os nomes são de lugares reais e o endereço é público:

| estabelecimento | achado | veredito | confiança |
|---|---|---|---|
| Arboretto Café e Cozinha | R. Prof. Atílio Innocenti, 29 — **Itaim Bibi** | `trabalho` | alta, unidade única |
| Xico Gastronomia | R. Joaquim Floriano, 1053 — **Itaim Bibi** | `trabalho` | alta |
| Homem de Mello | Panificadora, R. Dr. Homem de Melo, 626 — **Perdizes** | `outro` | alta |
| L.G.A. Estúdio Gourmet | **Perdizes** e Água Branca | `outro` | alta no bairro, duas unidades |
| Burdog | **Pacaembu** e Brooklin | `outro` | média: rede, nenhuma unidade no Itaim |
| `IFD*AlemaoSp-Itaim` | o emissor escreveu `Itaim` | `trabalho` | alta |
| Nutricar | o emissor escreveu `SANTANADEPA` | `outro` | alta |
| Restaurante e Pizzaria | nada — nome genérico | sem marca | — |

> **Os dois maiores da categoria ficam perto de casa, não do trabalho.** Homem de
> Mello (16 compras) e L.G.A. (13) estão em Perdizes, e o endereço do próprio
> usuário no boleto do Bradesco é Perdizes. São a padaria e o restaurante do
> bairro — não almoço de trabalho, e portanto gasto discricionário numa sexta.

> **Rede com várias unidades não se resolve por bairro.** Burdog tem Pacaembu e
> Brooklin; a marcação diz `outro` porque nenhuma delas é Itaim, mas a confiança
> é menor que a de um endereço único, e fica registrada como média.

#### A regra de sexta, e o que acontece com o não marcado

```
sáb, dom                      -> fim_de_semana
sexta + local = 'outro'       -> fim_de_semana   (jantar, evitável)
sexta + local = 'trabalho'    -> dia_util        (almoço, inevitável)
sexta + sem marcação          -> dia_util        (conservador)
seg a qui                     -> dia_util
```

Sexta sem marcação cai em `dia_util` de propósito: **o default conservador não
infla o balde evitável**, que é onde a meta é agressiva. Mas um default não pode
virar silêncio — a análise devolve quanto dinheiro de sexta ainda está sem
marcação, para o número ser lido como provisório e não como verdade.

> **A regra de sexta vale só onde o ato é comer fora.** A primeira versão a
> aplicou a toda categoria segmentada por dia, e o erro apareceu no resultado:
> um **hotel** (Deville, R$ 398,74, categoria Viagem) e um **iFood**
> (`ARCOS DOURADOS`, R$ 175,39) entraram na fila de "sexta sem marcação". Em
> hotel o bairro não diz nada; em delivery o que importa é o endereço de
> entrega, não o do restaurante. Agora é uma propriedade declarada da categoria,
> `sexta_por_local`, ligada só em Restaurante e bar — e as pendências caíram de
> R$ 802,73 para **R$ 228,60**, que é o número honesto.

### Categoria evitável por natureza

Correção do usuário, que vale mais que a regra de dia: *"quando for ifood ou
hotel, é final de semana, com ctz"*. iFood e hotel não são almoço de trabalho em
dia nenhum — o dia da semana simplesmente não entra na conta.

Isso é uma propriedade da categoria, `discricionario`, ligada em **Delivery** e
**Viagem**: tudo nelas cai no balde evitável, qualquer que seja o dia.

> **Daí em diante, `fim_de_semana` quer dizer "evitável", não "sábado e
> domingo".** O balde passa a conter sábado, domingo, jantar de sexta fora do
> Itaim e categoria discricionária em qualquer dia — então um iFood de terça
> aparece ali, e isso está certo. O nome ficou por continuidade; o significado é
> este.

O efeito é grande, porque move categorias inteiras:

| categoria | antes (dia útil / evitável) | depois |
|---|---|---|
| Delivery | 441,04 (3) / 243,47 (5) | **0 / 684,51 (8)** |
| Viagem | 1.048,54 (2) / 1.105,11 (1) | **0 / 2.153,65 (3)** |

Somando ao fim de semana de Restaurante (R$ 1.858,36), são **R$ 4.696,52** de
gasto declaradamente evitável em três categorias — e é sobre esse número que a
meta agressiva faz sentido, não sobre o total da fatura.

#### O efeito medido

A reclassificação move dinheiro real, e exatamente o previsto:

| | antes | depois | delta |
|---|---|---|---|
| dia útil | R$ 3.990,11 (66) | R$ 3.291,63 (55) | **−698,48** |
| fim de semana | R$ 1.159,88 (10) | R$ 1.858,36 (21) | **+698,48** |

São as sextas em Homem de Mello, L.G.A., Burdog e Nutricar — todas fora do
Itaim. O balde evitável cresceu 60%, e é sobre ele que a meta agressiva passa a
incidir.

> **O mesmo lugar tem textos diferentes em emissores diferentes**, e a conferência
> de padrões antes de gravar foi o que revelou: `HOMEMDEMELLO` (16 itens, cru do
> Itaú) e `HOMEM DE MELLO` (1 item, cru do Bradesco) são a mesma padaria;
> `L.G.A.ESTUDIOGOURM`, `CAPPTA*L.G.A.ESTUDIO`, `L.G.A.EST-CTGOURM` e
> `LGAESTUDIOGOURMETCO` são o mesmo restaurante. O casamento é por **substring
> sobre o texto cru**, e por isso um lugar pode precisar de mais de um padrão.

O corte não é exclusividade de restaurante: **Vestuário 64%** no fim de semana,
Beleza 55%, Viagem 51%. E não serve em Educação, Filhos, Lazer e Casa, que têm
**0%** — ali a segmentação só adicionaria uma linha vazia.

Para Supermercado o corte útil é outro: **faixa de ticket**, separando a compra
grande do mês da reposição (mediana R$ 108,75, 5 compras acima de R$ 200).

### Metas

Por `(categoria, segmento)`, e aceitam duas formas, à escolha em cada linha:

- **`absoluto`** — "restaurante fim de semana: R$ 800/mês". Funciona desde o
  primeiro mês.
- **`percentual`** — "−20% sobre a média dos meses anteriores". Se adapta, mas
  **exige histórico**: com um mês importado a média é o próprio mês e a meta
  nasce circular. Nesse caso o app mostra a meta como indisponível e diz por quê,
  em vez de devolver um número que se autoconfirma.

### O que isso muda no motor

| antes | agora |
|---|---|
| a IA propunha qualquer coisa sobre qualquer categoria | recebe a alavanca declarada e só pode propor daquele tipo |
| Saúde recebia sugestões | sai do dossiê |
| Farmácia recebia conselho genérico | marcada como aguardando o dado item a item |
| uma meta implícita por categoria | meta explícita por segmento, absoluta ou relativa |

A validação ganha dente novo: sugestão cujo tipo não corresponde à alavanca da
categoria é **descartada**, do mesmo modo que já se descarta alvo inexistente e
economia maior que o gasto.

## Uma tela só: onde gastei e onde cortar

Pedido do usuário: juntar "onde gastei" com "onde estou gastando por
estratégia", mostrar **só as categorias que fazem sentido para economia**, e em
cada linha ter uma estratégia e uma meta de redução editáveis — *"por ora
fazemos assim para eu testar um orçamento de redução"*.

Cada linha é um `(categoria, segmento)` com **dois controles** e **dois
expansores**:

| controle | o que faz |
|---|---|
| combo de **tática** | como se pretende cortar, com as opções da alavanca daquele segmento |
| campo de **%** | quanto cortar; grava ao sair do campo ou no Enter |
| `⊕` | junta os segmentos da categoria numa linha só (e `⊖` separa de novo) |
| `▸` | abre os lançamentos **daquela linha** |

Categorias com `alavanca='nenhum'` não aparecem: manter uma linha de zero
economia só gastaria atenção. A API continua devolvendo-as — é o cliente que
filtra —, porque a seção "o que ficou fora, e por quê" precisa delas.

> **Juntar é definir meta da categoria.** A linha junta grava com segmento
> vazio, que é a chave da categoria inteira e já funciona como herança para os
> segmentos. Não é um modo de exibição separado: é a mesma meta num nível acima.

### O segmento é estampado pelo servidor

Para abrir "os lançamentos deste segmento" o cliente precisaria saber em que
segmento cada lançamento caiu — e a regra depende de `estabelecimento_local`,
`sexta_por_local`, `discricionario` e do corte de ticket. Reimplementá-la em
TypeScript seria manter a **mesma regra em duas linguagens**, e elas divergiriam
na primeira correção. Então `/cartoes/itens` devolve `segmento` em cada item, e
o cliente só agrupa.

### A baseline, e por que ela aparece na tela

O % morde uma base, e a base muda conforme haja histórico:

| situação | baseline | rótulo |
|---|---|---|
| há meses anteriores | média deles | `(média)` |
| só este mês | o próprio mês | `(este mês)` |

A tela escreve **sobre qual valor** o percentual está incidindo, sempre. Um
percentual sem a base à vista não quer dizer nada — e foi por isso que a versão
anterior, que recusava meta percentual sem histórico, estava certa no diagnóstico
e errada na conclusão: o certo não é esconder a meta, é mostrar a base.

### Corte sugerido: ponto de partida, não veredito

Por alavanca, com o balde evitável aceitando mais porque é onde a decisão existe:

| alavanca | sugestão |
|---|---|
| `volume` em segmento evitável | 30% |
| `volume` no resto | 15% |
| `substituicao` | 20% |
| `preco` | 12% |
| `requer_detalhe` | 0% — a conta espera o dado |

Com três exceções que têm razão concreta: **Delivery 40%** (8 pedidos, R$ 684,51
— o mais fácil de cortar), **Assinaturas 30%** (plano família e anual) e
**Viagem 20%** (3 compras de ticket alto não se cortam pela metade).

E as táticas acompanham o par (alavanca, segmento), porque *"levar marmita 1 vez
por semana"* e *"cortar 1 saída a cada 3"* são a mesma alavanca (`volume`) e
ações diferentes — daí a tática morar no segmento, não na categoria.

### "Manter como está" é uma decisão, não a ausência de uma

Pedido do usuário, e a distinção é fina e importante: **decidir não cortar não é
o mesmo que não ter decidido.** A opção entra em **toda** lista de tática, por
último — é saída de escape, não sugestão — e vale **0%**.

Isso tem três consequências, e as três são o motivo de valer a pena:

| estado | `meta_bruta` | no orçamento | no "potencial" |
|---|---|---|---|
| sem meta | `null` | não entra | **entra** — é economia não aproveitada |
| mantido | `0` | não entra | **não entra** — você já decidiu |
| meta de N% | `N` | entra | não entra |

A linha mantida sai do potencial de propósito: continuar cobrando uma economia
que o usuário já recusou é o jeito mais rápido de a tela perder credibilidade.

> Gravar 0% exigiu afrouxar a validação, que recusava `percentual` fora de
> `0 < v < 100`. Sem isso "manter como está" seria indistinguível de "não
> decidi" — e o estado mais informativo da tela seria justamente o impossível
> de salvar.

### Ordenação: sempre do maior para o menor

Vale para todas as listas da tela. A que estava errada era a de **faturas do
mês**, em ordem alfabética (`ORDER BY cartao`), e a correção tem uma sutileza:
ordena-se pelo **valor que aparece na linha**, não pelo total da fatura. No
extrato aberto a tela mostra o consumo do período (R$ 706,99), não o total com
saldo anterior (R$ 5.016,24) — ordenar pelo total deixaria a lista visivelmente
fora de ordem em relação ao que se lê.

### O orçamento soma só o que foi cravado

O total no cabeçalho conta **apenas as metas que o usuário gravou**. O que ainda
é sugestão aparece à parte, como *"+R$ X nas sugestões que você ainda não
cravou"*. Somar os dois num número só seria prometer uma economia que ninguém
decidiu — o mesmo erro dos R$ 48 mil/ano da primeira versão das sugestões.

## O que esta fase deliberadamente não faz

- **Não lança no Mês Atual.** A fatura já entra lá como uma despesa só; duplicar
  cada item viraria dois registros do mesmo dinheiro.
- **Não importa CSV nem HTML.** Existe um `fatura-20260401.csv` com formato
  limpo, mas ele cobre um cartão e um mês; o PDF é o que o banco entrega para
  todos. Fica como fonte alternativa futura.
- **Não corta nada sozinho.** Sugere candidatos; a decisão é do usuário.
