
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
