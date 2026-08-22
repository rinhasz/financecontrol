# App de Controle Financeiro Pessoal

Substitui a planilha `contas_mensais.xlsx` (~40 abas mês/ano copiadas à mão) por um app desktop com banco de dados.

## Objetivo macro

Hoje o salário não cobre as despesas. Todo mês (perto do dia 1) é preciso saber **quanto resgatar dos investimentos** para fechar as contas. Meta de longo prazo: medir o quão perto se está de **não depender mais do salário** (renda de juros >= despesas).

## As 4 Funções

| # | Função | Status |
|---|--------|--------|
| 1 | **Controle de contas mensais** — catálogo de despesas recorrentes + importação de extrato (OFX/CSV/Excel) + batimento com revisão + calculadora de resgate do dia 1 | ✅ Implementada |
| 1b | **Busca de boletos/Pix por email** — acha cobranças no email e disponibiliza o código para pagamento manual | ✅ Implementada |
| 2 | **Despesas extras** — identificar o que não estava previsto, categorizar, perguntar se entra no controle | ⏳ Roadmap |
| 3 | **Análise do mês** — visão por categoria/tendência para otimizar gastos | ⏳ Roadmap |
| 4 | **Investimentos** — importar posições de vários bancos, consolidar, calcular rentabilidade, cruzar despesas x rendas rumo à independência financeira | ⏳ Roadmap |

**Ordem de implementação:** 1 → 2 → 3 → 4.

## Dados de partida (abas da planilha original)

- **Base Histórica** — 1.219 linhas, base bruta (1 linha por mês × despesa), Fev/23 a Jun/26. Seed da tabela `lancamento`.
- **Catálogo Despesas** — 45 despesas recorrentes já classificadas. Seed do catálogo `despesa`.
- **Classificação Var** — estatística de todas as rubricas (inclui ruído, para auditoria).

## Como rodar

```bash
python app.py              # app com janela nativa
python app.py --api        # só o backend (para testes)
pnpm run build:ui          # build do frontend após mexer em src/renderer
```

O backend **não recarrega sozinho**: mudança em `api/*.py` exige fechar e
reabrir o app.

## Documentação

Índice completo e guia de leitura em **[docs/00-indice.md](docs/00-indice.md)**.

Atalhos:

| Arquivo | Conteúdo |
|---------|----------|
| [PLAN.md](PLAN.md) | Arquitetura, stack e plano de implementação original |
| [00-indice.md](docs/00-indice.md) | **Índice de toda a documentação** |
| [01-modelo-de-dados.md](docs/01-modelo-de-dados.md) | Schema completo do SQLite |
| [10-importacao-e-batimento.md](docs/10-importacao-e-batimento.md) | Como a importação e o batimento funcionam de fato |
| [12-armadilhas-e-ambiente.md](docs/12-armadilhas-e-ambiente.md) | Ler antes de debugar lentidão ou tela em branco |
| [13-historico-de-pedidos.md](docs/13-historico-de-pedidos.md) | O que foi pedido, o que foi feito e por quê |
