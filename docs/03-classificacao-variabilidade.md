# Classificação de Variabilidade e Previsão

O app prevê o `valor_esperado` de cada despesa no próximo mês, conforme o **padrão de variabilidade** inferido do histórico (`Base Histórica`). Executar toda virada de mês.

---

## Padrões de variabilidade

| Padrão | Descrição | Exemplos |
|--------|-----------|----------|
| `fixa` | Não varia | Pagto IPEN 30 |
| `variavel_sazonal` | Varia com padrão por mês do ano | Gás/Comgas, luz, água |
| `variavel_nao_sazonal` | Varia sem padrão de calendário | Cartões |
| `reajuste_anual` | Estável com degrau ~1×/ano | Escola, convênios, financiamento |
| `anual` | Ocorre apenas em mês(es) específico(s) | IPVA (jan), IPTU |

---

## Critérios numéricos de classificação

Para cada despesa, calcular: `n`, `media`, `desvio`, `cv = desvio / media`, `media_mes[m]` = média dos valores do mês-calendário `m`, `forca_sazonal = var(media_mes) / var(todos)`.

**Classificar na ordem abaixo** (primeira regra que se encaixar vence):

1. `n < 3` → **sem dados** (usar último valor como previsão)
2. `cv ≤ 0,05` → **fixa**
3. Presença em `≤ 3` meses distintos do ano → **anual**
4. `forca_sazonal > 0,5` **e** há observações no mês-alvo → **variavel_sazonal**
5. Série quase monotônica crescente (`quedas ≤ 1`, `≥ 1 degrau de alta`, `cv < 0,35`) → **reajuste_anual**
6. Senão → **variavel_nao_sazonal**

---

## Previsão por padrão

| Padrão | Previsão para o mês-alvo |
|--------|--------------------------|
| `fixa` | Último valor |
| `variavel_sazonal` | Média dos valores do **mesmo mês-calendário** em anos anteriores (prever julho = média dos julhos passados) |
| `reajuste_anual` | Último valor; se degrau costuma cair no mês-alvo, aplicar reajuste % médio histórico |
| `variavel_nao_sazonal` | Média dos **últimos 3 meses** |
| `anual` | Valor apenas no(s) mês(es) de ocorrência; demais = 0 |
| `sem dados` | Último valor |

---

## Reclassificação periódica (rotina mensal)

1. Reprocessar histórico + novos lançamentos → recalcular estatística e padrão.
2. Sinalizar mudanças de padrão ao usuário.
3. Descobrir agrupamentos: nomes novos recorrentes sem categoria → sugerir por palavra-chave; propor **nova categoria** quando não couber (ver doc 05).
4. **Toda reclassificação é sugestão** — usuário confirma ou corrige (revisão manual obrigatória).

---

## Limitações conhecidas (revisar à mão)

- **Convênio pai e mãe**: é `reajuste_anual`, mas saltos recentes o jogam em `variavel_nao_sazonal`.
- **IPVA**: é `anual` (janeiro).
- **Cartão Porto**: marcado como sazonal por coincidência de julho — verificar.
- **Normalização**: une variantes entre parênteses (ex.: `Cartão azul blindagem/reforma` → um só nome canônico).
