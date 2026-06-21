# PLAN.md — Arquitetura e Plano de Implementação

## Stack escolhida

| Camada | Tecnologia | Razão |
|--------|-----------|-------|
| **Framework desktop** | Electron 32 + Vite 5 | Suporte a Windows, packaging nativo, sem servidor externo |
| **UI** | React 18 + TypeScript | Ecosystem rico, familiar ao projeto palmeiras |
| **Design system** | shadcn/ui + Tailwind CSS v4 | Componentes acessíveis, altamente customizáveis, mesmo sistema do palmeiras |
| **Banco de dados** | SQLite via `better-sqlite3` | Arquivo local, sem instalação, ideal para uso pessoal; queries síncronas simplificam o código |
| **OFX parsing** | `ofx-js` | Biblioteca pura JS, bem mantida |
| **CSV parsing** | `papaparse` | Robusto, suporte a encoding latin-1 (bancos brasileiros) |
| **PDF parsing** | Python `pdfplumber` via child_process | Melhor extração de tabelas; Python já instalado na máquina |
| **State management** | Zustand | Leve, sem boilerplate |
| **Gráficos** | Recharts | Já usado no palmeiras; excelente com React |
| **Package manager** | pnpm | Consistência com palmeiras |

---

## Estrutura de diretórios

```
financecontrol/
├── docs/                        # Especificações (este diretório)
├── src/
│   ├── main/                    # Processo principal Electron (Node.js)
│   │   ├── index.ts             # Entry point Electron
│   │   ├── db/
│   │   │   ├── client.ts        # Singleton better-sqlite3
│   │   │   ├── migrations/      # SQL de criação de tabelas (versionado)
│   │   │   └── seed/            # Scripts de seed (Base Histórica, Catálogo)
│   │   ├── ipc/                 # Handlers IPC (bridge main ↔ renderer)
│   │   │   ├── lancamentos.ts
│   │   │   ├── transacoes.ts
│   │   │   ├── categorias.ts
│   │   │   └── importacao.ts
│   │   └── parsers/
│   │       ├── ofx.ts
│   │       ├── csv.ts
│   │       └── pdf.ts           # Chama pdfplumber via child_process
│   ├── renderer/                # Processo renderer (React)
│   │   ├── App.tsx
│   │   ├── pages/
│   │   │   ├── MesAtual.tsx     # Função 1 — tela principal
│   │   │   ├── Catalogo.tsx     # Gestão do catálogo de despesas
│   │   │   ├── Importacao.tsx   # Upload e revisão de extratos
│   │   │   ├── Analise.tsx      # Função 3
│   │   │   └── Investimentos.tsx # Função 4
│   │   ├── components/
│   │   │   ├── LancamentoRow.tsx # Linha com cor (verde/azul/branco)
│   │   │   ├── ResumoMes.tsx    # Total, pago, agendado, a resgatar
│   │   │   ├── CalculadoraResgate.tsx
│   │   │   └── ImportacaoWizard.tsx
│   │   ├── store/               # Zustand stores
│   │   └── lib/
│   │       ├── batimento.ts     # Motor de batimento (pode rodar no renderer)
│   │       └── previsao.ts      # Lógica de classificação de variabilidade
├── electron.vite.config.ts
├── package.json
├── tsconfig.json
├── tailwind.config.ts
├── PLAN.md                      # Este arquivo
└── README.md
```

---

## Modelo de dados (SQLite)

Ver [docs/01-modelo-de-dados.md](docs/01-modelo-de-dados.md) para o esquema completo.

Arquivo do banco: `%APPDATA%/financecontrol/database.sqlite` (produção) ou `./dev.sqlite` (desenvolvimento).

---

## IPC (comunicação main ↔ renderer)

Electron expõe uma API via `contextBridge` no preload. O renderer chama `window.api.*`; o main executa no SQLite e retorna.

| Canal IPC | Descrição |
|-----------|-----------|
| `lancamentos:list` | Lista lançamentos de um mês |
| `lancamentos:update` | Atualiza valor/status |
| `transacoes:import` | Recebe arquivo, parseia, salva, roda batimento |
| `transacoes:list` | Lista transações de um período |
| `catalogo:list` / `:upsert` | CRUD do catálogo |
| `resgate:calcular` | Retorna necessidade de resgate com composição |
| `previsao:rodar` | Reclassifica variabilidade e atualiza previsões |

---

## Plano de implementação (Função 1 primeiro)

### Fase 0 — Setup (1 sessão)
- [ ] Scaffold `electron-vite` com React + TypeScript
- [ ] Configurar shadcn/ui + Tailwind
- [ ] Criar migrations SQLite (todas as tabelas)
- [ ] Estrutura de IPC (preload + handlers)

### Fase 1 — Catálogo e seed (1 sessão)
- [ ] Script de seed: `Catálogo Despesas` CSV → tabelas `categoria` + `despesa`
- [ ] Script de seed: `Base Histórica` CSV → tabela `lancamento`
- [ ] Tela Catálogo: listar, editar, ativar/desativar despesas

### Fase 2 — Mês atual (2 sessões)
- [ ] Abertura de mês: gerar lançamentos do catálogo ativo
- [ ] Motor de previsão (doc 03): calcular `valor_esperado`
- [ ] Tela MesAtual: tabela com cores, edição inline, resumo

### Fase 3 — Importação e batimento (2 sessões)
- [ ] Parser OFX
- [ ] Parser CSV (com perfil por banco)
- [ ] Parser PDF (via pdfplumber)
- [ ] Motor de batimento (doc 02)
- [ ] Wizard de importação + revisão de conflitos

### Fase 4 — Calculadora de resgate
- [ ] Lógica da fórmula (doc 04)
- [ ] Componente `CalculadoraResgate` com composição e sugestão de origem

### Fase 5 — Funções 2, 3 e 4
- [ ] Ver [docs/06-roadmap-funcoes-2-3-4.md](docs/06-roadmap-funcoes-2-3-4.md)

---

## Design

- **Tema:** dark mode como padrão (fácil de ver à noite para fechamento de mês).
- **Paleta:** zinc/slate (neutros) com accent verde (pago), azul (agendado), âmbar (atenção).
- **Tipografia:** Inter (já no shadcn/ui).
- **Layout:** sidebar de navegação fixa à esquerda + área de conteúdo principal.

---

## Dados de teste

Para rodar o seed, exportar do Excel as 3 abas como CSV UTF-8:
1. `Base Histórica` → `seed/base-historica.csv`
2. `Catálogo Despesas` → `seed/catalogo-despesas.csv`
3. `Classificação Var` → `seed/classificacao-var.csv`
