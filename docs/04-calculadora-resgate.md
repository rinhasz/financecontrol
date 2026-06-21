# Calculadora de Resgate do Dia 1

Como o salário não cobre as despesas, calcula-se **quanto resgatar dos investimentos** no início do mês.

Na planilha original: células "A resgatar" / "Necessidade dia 1".

---

## Fórmula

```
necessidade_resgate =
    total_a_vencer_no_mes
  + reserva_desejada
  - saldo_conta_atual
  - receitas_previstas_do_mes
```

- Se `> 0` → é o valor a resgatar.
- Se `≤ 0` → o mês se paga sem resgate.

**Equivalência com a planilha:**
```
A resgatar = A vencer + Reserva − A receber − Saldo
```

---

## Componentes

| Componente | Fonte |
|------------|-------|
| `total_a_vencer_no_mes` | `SUM(lancamento.valor_esperado)` onde `pago + agendado + nao_encontrado`. Usa `valor_esperado` enquanto não pago; usa `valor_real` depois de casado. |
| `reserva_desejada` | Colchão configurado pelo usuário (editável) |
| `saldo_conta_atual` | Digitado pelo usuário no início do mês |
| `receitas_previstas_do_mes` | Salário + outros créditos previstos (tabela `receita`) |

---

## Interface esperada

- Mostrar composição: **pago** / **agendado** / **em aberto** (com valores).
- Sugestão de de onde resgatar: linkar com Função 4 (produtos com vencimento próximo e melhor liquidez).

---

## Referência histórica

| Marco | Valor |
|-------|-------|
| Necessidade dia 1 | ~R$ 58.357,86 |
| Necessidade dia 4 | ~R$ 47.704,20 |

Os valores caem do dia 1 para o dia 4 à medida que alguns débitos são confirmados e o saldo é reavaliado.
