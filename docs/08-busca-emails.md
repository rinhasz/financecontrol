# Função: Procurar em Emails

**Status:** planejamento — aguardando decisões do usuário antes de implementar.

## Objetivo

Não é para criar despesas novas nem para automatizar o batimento. É uma
ferramenta de apoio ao pagamento manual: buscar, entre os emails do
usuário, faturas/boletos referentes a **despesas já cadastradas no
catálogo**, e extrair:

- o **valor** da fatura do mês
- o **código de boleto** (linha digitável) para copiar e colar no pagamento

O usuário confere na tela e paga manualmente fora do app — o app só
poupa o trabalho de vasculhar a caixa de entrada.

## Fluxo proposto

1. Usuário abre a aba "Procurar em Emails".
2. Escolhe o mês de referência (mesma lógica de competência já usada no
   resto do app — ver [07-dicionario-despesas.md](07-dicionario-despesas.md)).
3. O app busca, para cada despesa ativa do catálogo que tenha
   `regras_match.palavras_chave`, emails recentes cujo assunto/remetente
   bata com essas palavras (reaproveita a mesma lógica de palavras-chave
   do catálogo, então melhorar o catálogo para o batimento bancário
   também melhora a busca de email, e vice-versa).
4. Para cada email encontrado, tenta extrair valor e linha digitável do
   corpo (HTML/texto) ou de um PDF anexado.
5. Mostra por despesa: valor extraído, linha digitável (com botão
   "copiar"), data de vencimento se identificável, e um link para abrir
   o email original no cliente de email do usuário.
6. Não grava nada no banco automaticamente — é só um painel de consulta.

## Decisões em aberto (bloqueiam a implementação)

### 1. Provedor de email e autenticação
Preciso saber qual provedor o usuário usa (Gmail, Outlook/Hotmail, iCloud,
outro via IMAP genérico) porque isso muda a estratégia de autenticação:

- **Gmail** → API do Gmail com OAuth2 (mais robusto, requer criar um
  projeto no Google Cloud Console e fazer o usuário autorizar uma vez;
  token fica salvo localmente).
- **IMAP genérico** (Outlook, iCloud, etc.) → mais simples de implementar,
  mas exige uma "senha de app" gerada no provedor (a senha normal da
  conta não funciona com a maioria dos provedores por segurança).

Isso é uma decisão que não dá para assumir — foi perguntado ao usuário
via pergunta interativa antes de começar a implementação.

### 2. Escopo de busca (quais despesas / quanto tempo)
Provável default: só despesas com `tipo_valor='variavel'` que normalmente
chegam por boleto/fatura (cartões, contas de consumo) — despesas fixas
com débito automático não precisam de boleto. Buscar dentro da janela do
mês de competência atual (mesma janela do batimento bancário). A
confirmar com o usuário quando a implementação começar.

### 3. Extração de valor e linha digitável
- Linha digitável de boleto tem formato bem definido (5 blocos de
  dígitos) — dá para usar regex com boa confiabilidade no texto puro do
  email.
- Quando a fatura vem em PDF anexado (comum em fatura de cartão), precisa
  abrir o PDF e extrair texto (`pdfplumber` ou similar) antes de aplicar
  o mesmo regex. PDFs escaneados como imagem exigiriam OCR — fora de
  escopo inicial; se acontecer, mostrar "não consegui extrair, abra o
  email manualmente" em vez de falhar silenciosamente.

## Riscos / cuidados

- Credenciais de email (senha de app ou token OAuth) devem ir para
  `.env.local`, nunca commitadas.
- Acesso de leitura de email é sensível — o app deve pedir só escopo de
  **leitura** (nunca enviar/apagar), e deixar claro na UI que credenciais
  ficam armazenadas localmente.
