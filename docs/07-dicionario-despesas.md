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
| `TBI 4949.02160-63789.072` | pagto thalita | — |
| `PIX TRANSF MARIA L28/07` | mesada malu | — |
| `PIX TRANSF MARIA J28/07` | mesada maju | — |
| `PAG BOLETO PREVENT S P OP SAUDE LTDA` | convenio marco antonio | — |
| `PIX TRANSF ALBERTO31/07` | pagto ipen beto | — |
| `PIX TRANSF MARCELO01/08` | aluguel vaga garagem | — |
| `ITAU VISA     0703-7489` | cartao azul | — |
| `Agendado` | financ imob | — |
| `DA  CLARO BL/IT 77712744` | net | — |
| `PIX TRANSF DEUSA D01/08` | salario deusa final mes | adiantamento deusa |
| `INT IPTU02102204944` | iptu apto | cartao porto |
| `PAG BOLETO LELLO` | reforma thiago | cartao azul |
| `PIX TRANSF MARIA J01/08` | mesada maju | material maju |
| `PAG BOLETO ITAU UNIBANCO HOLDING S.A.` | cartao black | — |
| `INT /DOC ARREC E-SOCI 07` | esocial deusa | — |
| `PAG BOLETO BANCO BRADESCARD S A` | cartao amazon | — |
| `PAG TIT 662992535000` | condominio | — |
| `PIX TRANSF INGRID 03/08` | fisio thalita | — |
| `PAG BOLETO ENEL DISTRIBUICAO SAO PAULO` | conta luz | — |
| `PAG BOLETO REDE BATISTA DE EDUCACAO DA` | material malu | — |
| `PAG BOLETO CCM - CENTRO DE CULTURA MUSI` | escola musica maju | — |
| `PAG BOLETO EDIFICIO LINCOLN GARDEN` | condominio | — |
| `DA  CLARO S.A. 77712744` | net claro | — |
| `PIX TRANSF PIERANG06/08` | personal thalita | — |
| `PAG TIT 348295769000` | cartao porto | — |
| `PIX QRS SUL AMERICA` | convenio sogra | — |
| `PAG TIT BANCO 237` | cartao azul | — |

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
