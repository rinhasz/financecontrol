# Categorias e Regras de Atribuição

Substitui as somas manuais da planilha (linhas fixas + números mágicos embutidos em fórmulas).

---

## Categorias iniciais (seed)

| Categoria | Exemplos de despesas |
|-----------|----------------------|
| **Financiamento imóvel** | Financ imob |
| **Casa/Utilidades** | Condomínio, luz, água, gás (Comgas), Net, Vivo, IPTU, estacionamento, vaga, aluguel vaga |
| **Saúde** | Convênios (sogra, Marco Antônio, pai e mãe), dentista, fisio |
| **Cartões** | Black, Porto, XP, Amazon, One, Mercado Livre, seguro cartões |
| **Filhos/Educação** | Escola Maju/Malu, bilíngue, música, natação, inglês, mesada, material, teatro, vôlei, Vicente |
| **Funcionária (Deusa)** | Salário/adiantamento/GPS/eSocial/vale-transporte Deusa, Thalita, IPEN Beto, Sr Mariano, Jeorge |
| **Outros** | Contador, reformas, itens não recorrentes |

---

## Regras de atribuição por palavra-chave

**Primeira regra que casar vence. Case-insensível e acento-insensível.**

| Prioridade | Padrão (regex) | Categoria |
|-----------|----------------|-----------|
| 1 | `financ imob` | Financiamento imóvel |
| 2 | `convenio\|fisio\|dentista` | Saúde |
| 3 | `cartao\|seguro cart` | Cartões |
| 4 | `escola\|bilingue\|musica\|natacao\|ingles\|mesada\|material\|teatro\|volei\|maju\|malu\|vicente` | Filhos/Educação |
| 5 | `deusa\|thalita\|gps\|adiantamento\|ipen\|mariano\|jeorge\|vale transporte` | Funcionária |
| 6 | `luz\|agua\|comgas\|gas\|net\|vivo\|condominio\|iptu\|estacionamento\|vaga\|aluguel` | Casa/Utilidades |
| 7 | *(nenhuma acima)* | Outros |

As regras são **editáveis** pelo usuário na interface.

---

## Criar novas categorias

Quando um grupo de novas despesas recorrentes não couber nas categorias existentes, o app:
1. Detecta o agrupamento (palavras comuns nas descrições).
2. **Propõe** uma nova categoria com nome sugerido.
3. Aguarda aprovação do usuário antes de criar.

Novas categorias ficam disponíveis para as regras de atribuição subsequentes.
