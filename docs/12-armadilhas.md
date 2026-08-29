
## Olinda (BCB) devolve 400 se o espaço vier como `+`

A API de Expectativas do Banco Central (boletim Focus), usada em
`mercado.baixar_focus_ipca`, é OData servida pelo Olinda. Passar o filtro por
`params=` do `requests` codifica os espaços como `+`, e o parser do Olinda **não**
lê `+` como espaço — ele junta tudo num token só e responde:

```
400 · "The types 'Edm.Boolean' and 'Edm.String' are not compatible."
```

A mensagem sugere erro de tipo no filtro e manda depurar o lado errado: o filtro
está certo, o que quebrou foi a codificação. O sintoma denuncia a causa —
**todo** filtro falha igual, até um trivial como `baseCalculo eq 0`. Se um filtro
óbvio também dá 400, o problema é o transporte, não a expressão.

A saída é montar a query à mão com `quote()`, que gera `%20`:

```python
url = f'{FOCUS}?%24format=json&%24filter={quote(filtro)}'
```

Note que `$` também precisa ir como `%24`.

## O Itaú carimba a posição com uma data velha

O arquivo de posição baixado em 28/08/2026 traz os saldos **de 28/08** — a LIG
prefixada soma R$ 39.322,82, exatamente o que o site mostrava naquele dia — e
mesmo assim escreve `25/08/2026` na coluna "Saldo atualizado até", em todas as
linhas. Dois arquivos com saldos diferentes carregam o mesmo carimbo.

Isso é grave aqui porque a substituição de posição é por `(data_posicao, origem)`:
importar o arquivo de 28/08 lendo o carimbo o gravaria como 25/08 e
**apagaria a posição anterior**, além de zerar a valorização derivada dela.

A coluna "PREÇO UNITÁRIO" da aba de corretora tem o mesmo defeito — no arquivo
de 28/08 ela repete o PU de 25/08 (1.051,27) enquanto o valor financeiro já
andou (PU implícito 1.053,59). Não dá para confiar nela; o PU tem de sair de
`valor financeiro / quantidade`.

A data confiável é a do **nome do arquivo** (`POSICAO_CONSOLIDADA_RF_28082026`),
que o internet banking carimba com o dia do download. `_data_do_nome()` tem
precedência sobre o carimbo; a data informada pelo usuário vence as duas.
