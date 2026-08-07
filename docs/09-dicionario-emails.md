# Dicionário de Remetentes de Email

Toda vez que você associa um boleto encontrado por email a uma despesa, o app
grava o remetente na tabela `email_despesa_regra` — da próxima busca, esse
remetente já vem pré-associado à mesma despesa, só pra você conferir o valor/linha
digitável do mês. Este arquivo é só o espelho legível dessa tabela; a fonte de
verdade que o app consulta é o banco.

| Remetente | Despesa |
|---|---|
| `cartaoportoseguro@faturaporto.com.br` | cartao porto |
| `suafatura@comgas.com.br` | comgas |
| `boleto@jaime.com.br` | condominio |
| `faturadigital@minhaclaro.com.br` | net |
| `faturadigital@itaupersonnalite.com.br` | cartao black |
| `grp-sousulamerica@sulamerica.com.br` | convenio sogra |
