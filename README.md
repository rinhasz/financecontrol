# App de Controle Financeiro Pessoal

Substitui a planilha `contas_mensais.xlsx` (~40 abas mês/ano copiadas à mão) por um app desktop com banco de dados.

## Objetivo macro

Hoje o salário não cobre as despesas. Todo mês (perto do dia 1) é preciso saber **quanto resgatar dos investimentos** para fechar as contas. Meta de longo prazo: medir o quão perto se está de **não depender mais do salário** (renda de juros >= despesas).

## As 4 Funções

| # | Função | Status |
|---|--------|--------|
| 1 | **Controle de contas mensais** — catálogo de despesas recorrentes + importação de extrato (OFX/CSV/PDF) + batimento automático + calculadora de resgate do dia 1 | 🔨 Em construção |
| 2 | **Despesas extras** — identificar o que não estava previsto, categorizar, perguntar se entra no controle | ⏳ Roadmap |
| 3 | **Análise do mês** — visão por categoria/tendência para otimizar gastos | ⏳ Roadmap |
| 4 | **Investimentos** — importar posições de vários bancos, consolidar, calcular rentabilidade, cruzar despesas x rendas rumo à independência financeira | ⏳ Roadmap |

**Ordem de implementação:** 1 → 2 → 3 → 4.

## Dados de partida (abas da planilha original)

- **Base Histórica** — 1.219 linhas, base bruta (1 linha por mês × despesa), Fev/23 a Jun/26. Seed da tabela `lancamento`.
- **Catálogo Despesas** — 45 despesas recorrentes já classificadas. Seed do catálogo `despesa`.
- **Classificação Var** — estatística de todas as rubricas (inclui ruído, para auditoria).

## Documentação

| Arquivo | Conteúdo |
|---------|----------|
| [PLAN.md](PLAN.md) | Arquitetura, stack, estrutura de diretórios e plano de implementação |
| [01-modelo-de-dados.md](docs/01-modelo-de-dados.md) | Modelo relacional completo (todas as tabelas e campos) |
| [02-funcao1-controle-contas.md](docs/02-funcao1-controle-contas.md) | Função 1: fluxo, importação de extrato, motor de batimento |
| [03-classificacao-variabilidade.md](docs/03-classificacao-variabilidade.md) | Classificação de variabilidade e previsão de valores |
| [04-calculadora-resgate.md](docs/04-calculadora-resgate.md) | Fórmula e lógica da calculadora de resgate do dia 1 |
| [05-categorias.md](docs/05-categorias.md) | Categorias iniciais e regras de atribuição por palavra-chave |
| [06-roadmap-funcoes-2-3-4.md](docs/06-roadmap-funcoes-2-3-4.md) | Roadmap das funções 2, 3 e 4 |
