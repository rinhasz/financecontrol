# Índice da Documentação

FinanceControl — app desktop de controle de despesas mensais. Substitui uma
planilha: o catálogo é cadastro único e **cada mês é gerado dele**, com
batimento contra o extrato bancário.

## Por onde começar

| Objetivo | Leia |
|----------|------|
| Entender o produto | [02](02-funcao1-controle-contas.md) → [10](10-importacao-e-batimento.md) |
| Mexer no banco | [01](01-modelo-de-dados.md) |
| Reconstruir do zero | [13](13-historico-de-pedidos.md) + [12](12-armadilhas-e-ambiente.md) |
| Debugar algo travando | [12](12-armadilhas-e-ambiente.md) |

## Documentos

| # | Documento | Conteúdo |
|---|-----------|----------|
| 01 | [Modelo de dados](01-modelo-de-dados.md) | Schema SQLite, tabelas, migrações |
| 02 | [Função 1 — Controle de contas](02-funcao1-controle-contas.md) | Spec conceitual original (ver nota no topo) |
| 03 | [Classificação de variabilidade](03-classificacao-variabilidade.md) | Como se prevê o valor de cada despesa |
| 04 | [Calculadora de resgate](04-calculadora-resgate.md) | Quanto resgatar dos investimentos |
| 05 | [Categorias](05-categorias.md) | Taxonomia de categorias |
| 06 | [Roadmap funções 2, 3 e 4](06-roadmap-funcoes-2-3-4.md) | Não implementadas |
| 07 | [Dicionário de despesas](07-dicionario-despesas.md) | **Gerado pelo app**: texto do extrato → despesa |
| 08 | [Busca em emails](08-busca-emails.md) | Boletos/Pix por email, OAuth2, Gemini |
| 09 | [Dicionário de emails](09-dicionario-emails.md) | **Gerado pelo app**: remetente → despesa |
| 10 | [Importação e batimento](10-importacao-e-batimento.md) | Fluxo real: parsers, competência, scoring, preview |
| 11 | [Catálogo](11-catalogo.md) | CRUD, importação de planilha, ordenação |
| 12 | [Armadilhas e ambiente](12-armadilhas-e-ambiente.md) | IPv6, Windows, MSAL, robustez de tela |
| 13 | [Histórico de pedidos](13-historico-de-pedidos.md) | O que foi pedido, o que foi feito e por quê |
| 14 | [Receitas e esporádicas](14-receitas-e-esporadicas.md) | Entradas, fixa vs esporádica, resgates e estorno |
| 15 | [Projeção e resgate](15-projecao-e-resgate.md) | pago/agendado/projetado, tipos de projeção, calculadora |
| 16 | [Investimentos](16-investimentos.md) | Posição (fase 1) e valorização diária (fase 2) |

Os docs 07 e 09 são **escritos pelo próprio app** conforme o usuário corrige
batimentos e associa remetentes — não editar à mão.

## Estado atual

**Implementado:** Função 1 completa (catálogo, competência, importação que
substitui o período, batimento com preview, Mês Atual, resgate), receitas com
classificação por tipo (doc 14) e busca de boletos/Pix por email.

**Não implementado:** Funções 2 (despesas extras), 3 (análise do mês) e 4
(investimentos) — ver doc 06. A tabela `posicao_investimento` existe no schema
mas não é usada.

## Stack

Flask + PyWebView (janela nativa), React 18 + TypeScript + Tailwind, SQLite.
O Flask serve o frontend buildado — um processo, porta 5173.

```bash
python app.py              # app com janela
python app.py --api        # só backend (para testes)
pnpm run build:ui          # build do frontend → frontend/dist
npx tsc --noEmit -p tsconfig.web.json    # checagem de tipos
```

**O backend não recarrega sozinho** — mudança em `api/*.py` exige fechar e
reabrir o app. Ver doc 12 antes de debugar qualquer lentidão ou tela em branco.
