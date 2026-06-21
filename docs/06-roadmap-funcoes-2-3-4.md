# Roadmap — Funções 2, 3 e 4

Todas dependem da **Função 1** estar completa e estável.

---

## Função 2 — Despesas Extras

**Objetivo:** identificar no extrato o que não estava previsto no catálogo.

**Fluxo:**
1. Transações débito **não casadas** com o catálogo = candidatas a despesa extra.
2. App categoriza automaticamente usando as regras do doc 05.
3. Pergunta ao usuário: **incluir no mês** (vira lançamento avulso) ou **ignorar** (transferência, investimento, ruído)?
4. **Aprendizado:** descrições repetidas em meses futuros viram sugestão de nova despesa recorrente no catálogo.

---

## Função 3 — Análise do Mês

**Objetivo:** apontar onde otimizar os gastos.

**Visões:**
- Total por categoria vs. média histórica vs. mês anterior.
- Top variações (quais despesas mais cresceram/caíram).
- Despesas sazonais: previsto vs. realizado.
- Tendência de longo prazo por categoria.

**Output esperado:** gráficos de barras/linha por categoria, tabela de desvios, alertas de anomalia.

---

## Função 4 — Investimentos e Independência Financeira

**Objetivo:** medir o quão perto se está de não depender do salário.

### Importação de posições
- Múltiplos bancos → tabela `posicao_investimento` (snapshot mensal).
- Formato: CSV exportado de cada banco (inicialmente manual; automatizar depois).

### Rentabilidade
- Por produto: `valor / valor_mes_anterior - 1`.
- Consolidada por classe (`RF`, `RV`, `Prev`, `FGTS`) e total.
- Horizontes: mês atual, ano, 12 meses, desde o início.

### Métrica de independência
```
grau_independencia = renda_juros_mensal / despesas_mensais_totais
```
- **Meta:** ≥ 100% → sustentável sem salário.
- Acompanhar a **série temporal** dessa razão mês a mês.
- Cruzar: salário vs. juros vs. despesas no mesmo gráfico.

### Ligação com Função 1 (calculadora de resgate)
- Sugerir **de onde resgatar**: produtos com vencimento próximo e melhor liquidez primeiro.
