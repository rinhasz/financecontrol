# Catálogo de Despesas

**Status:** implementado (`api/catalogo.py`, aba "Catálogo").

O catálogo é o cadastro único de despesas. Cada mês é **gerado** a partir dele
(doc 10) — não existe cópia de aba.

---

## Campos editáveis

Nome, categoria, padrão de variabilidade (doc 03), tipo de valor
(`fixo`/`variavel`), valor previsto, dia de vencimento e **palavras-chave**.

As palavras-chave alimentam o `regras_match` usado pelo batimento. Ao criar uma
despesa automaticamente (pela tela de importação ou de emails), as palavras
saem do próprio nome: tokens com 3+ caracteres.

---

## Ativar / desativar

Despesa desativada não gera lançamento novo, não aparece em Mês Atual e **não
disputa transação no batimento**. Desativar é preferível a excluir porque
preserva o histórico dos meses anteriores.

Exceção deliberada: se a despesa foi desativada mas já tinha movimento real no
mês corrente, ela continua visível naquele mês (ver doc 10).

---

## Ordenação da lista

Seletor "Ordenar por" no topo:

| Ordem | Comportamento |
|-------|---------------|
| **Categoria** (padrão) | Lista agrupada por categoria, como sempre foi |
| **Maior valor** | Lista única, decrescente |
| **Dia de vencimento** | Lista única, crescente (dia 1 → 31) |

Nas duas últimas a lista deixa de ser agrupada (agrupar quebraria a ordenação)
e ganha uma coluna de Categoria para não perder essa informação. Despesas sem
valor ou sem dia de vencimento vão para o fim — no topo ocupariam espaço sem
informar nada.

---

## Importar catálogo de uma planilha

Sincroniza o catálogo com uma lista externa (a planilha que o usuário já
mantinha). Três cenários suportados:

1. **Só nome** — cria as que faltam, desativa as que não estão na lista.
2. **Nome + categoria** — idem, e ajusta categorias (criando categoria nova se
   preciso).
3. **Nome + categoria + valor** — idem, e atualiza o valor previsto,
   assumindo que o valor recebido é o último valor pago.

Em todos, **as despesas do arquivo passam a ser as únicas ativas**.

### Fluxo em 3 passos (preview obrigatório)

```
amostra  →  mapear colunas  →  revisar plano  →  confirmar
```

- `POST /catalogo/importar/amostra` — devolve as primeiras linhas **cruas**,
  sem interpretar nada, para o usuário dizer qual coluna é qual (não adivinha
  cabeçalho).
- `POST /catalogo/importar/analisar` — monta o plano: `novas`, `atualizadas`,
  `desativadas`, `categorias_novas`. **Não grava.**
- `POST /catalogo/importar/confirmar` — executa exatamente o plano revisado.

O preview é obrigatório aqui porque a operação é destrutiva: desativar tudo que
não está no arquivo é irreversível na prática se feito por engano.

### Duas formas de entrada

- **Arquivo** `.xls`/`.xlsx` — reaproveita `_sheet_rows_xls`/`_sheet_rows_xlsx`
  de `api/importacao.py`.
- **Colar da área de transferência** — quando você copia células do Excel, o
  texto vem como **TSV** (tabulação entre colunas). `_ler_linhas(file, texto)`
  aceita as duas origens e devolve a mesma "lista de linhas cruas", então todo
  o resto do fluxo é agnóstico à origem.

Comparação de nomes é feita por `_norm_nome()` (minúsculas, sem acento, espaços
colapsados) para "Conta de Luz" e "conta de luz" não virarem duas despesas.

---

## DespesaPicker (componente compartilhado)

`src/renderer/src/components/DespesaPicker.tsx` — seletor com busca por
digitação, usado em toda tela onde se escolhe despesa (importação de extrato e
busca de emails). Substituiu os `<select>` nativos, que ficaram inviáveis com
dezenas de despesas.

Filtra por **todos** os termos digitados (busca "cartao azul" acha
"cartao azul itau"). Com `allowNova`, oferece **"+ Nova despesa"** já com o
texto digitado, permitindo criar e associar sem sair da tela.

---

## Endpoints

| Rota | Método | O que faz |
|------|--------|-----------|
| `/api/catalogo` | GET | Lista despesas com categoria |
| `/api/catalogo` | POST | Cria ou atualiza (upsert por `id`) |
| `/api/catalogo/<id>/toggle` | POST | Alterna ativo/inativo |
| `/api/categorias` | GET | Lista categorias |
| `/api/catalogo/importar/amostra` | POST | Passo 1 — amostra crua |
| `/api/catalogo/importar/analisar` | POST | Passo 2 — plano de mudanças |
| `/api/catalogo/importar/confirmar` | POST | Passo 3 — aplica o plano |

---

## Armadilha: lista desatualizada entre abas

As telas ficam **todas montadas** ao mesmo tempo (só a visibilidade muda, para
não perder estado ao trocar de aba — ver `App.tsx`). Por isso, cada tela que
usa o catálogo precisa recarregá-lo quando volta a ficar visível; senão uma
despesa criada no Catálogo nunca aparece na aba de Importação já aberta.

Implementado com a prop `active` em `Importacao` e `EmailBusca`.
