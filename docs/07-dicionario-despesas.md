# Dicionário de Despesas

Registro de correções feitas pelo usuário quando o batimento automático
associa uma transação do extrato à despesa errada. Serve como referência
para revisar `regras_match` (palavras-chave) do catálogo e evitar repetir
o mesmo erro.

Toda vez que o usuário corrige um casamento pela tela de Importação
("Não é essa despesa"), o backend (`_registrar_dicionario` em
`api/importacao.py`) acrescenta uma linha aqui automaticamente. As linhas
abaixo, anteriores a isso, foram registradas manualmente a partir da
conversa com o usuário.

| Descrição no extrato | Despesa correta | Erro anterior |
|---|---|---|
| `CARTAO ITAU THE ONE` | cartao one itau | cartao xp |
| `SEGURO CARTAO` | seguro cartoes | cartao xp |

## Notas

- `cartao xp` no catálogo tem só a palavra-chave genérica `"cartao"` —
  qualquer transação de cartão sem uma despesa mais específica competindo
  por ela pode acabar caindo nela, mesmo com valor muito destoante do
  esperado (~R$20). Foi assim que `CARTAO ITAU THE ONE` (R$32.356,10) e
  `SEGURO CARTAO` (R$26,14) foram parar em `cartao xp` antes da correção.
- A despesa `seguro cartoes` tinha a palavra-chave `"cartoes"` (plural),
  mas o Itaú descreve o lançamento real como `SEGURO CARTAO` (singular) —
  nunca batia. Corrigido para `"cartao"` (singular) em 2026-08-01.
  **Lição geral**: ao cadastrar `regras_match`, usar a forma singular
  das palavras, do jeito que aparece no extrato bruto, não o nome bonito
  da despesa no catálogo.
- Para reduzir esse tipo de colisão de forma estrutural (não só corrigindo
  caso a caso), o score de casamento em `rodar_batimento` agora pesa mais
  despesas com múltiplas palavras-chave (`2 * nº de palavras` quando todas
  batem) — uma despesa com uma única palavra genérica não vence mais
  sozinha, sem nenhuma corroboração de valor ou data. Ainda assim, se uma
  despesa nova e específica for cadastrada com palavras-chave mais
  precisas, ela sempre tem prioridade sobre uma despesa genérica.
- O extrato do Itaú (.xls) não tem coluna de "situação/status". Os
  lançamentos futuros/agendados vêm depois de uma linha marcadora
  `"lançamentos futuros"` / `"saídas futuras"` na mesma planilha — é assim
  que o parser (`_parse_excel_sheet`) sabe marcar `situacao='agendada'`.
- O mês de competência (`mes_ref`) não é o mês calendário: as despesas de
  um mês começam a ser pagas quando o salário cai, configurável em
  "Recebo o salário no dia" (padrão dia 27). Ver `periodo_competencia()`
  em `api/db.py`.
