# Função: Procurar em Emails

**Status:** implementado (`api/email_busca.py`, aba "Procurar em Emails").

## Objetivo

Não cria despesas novas nem automatiza o batimento. É uma ferramenta de
apoio ao pagamento manual: busca, entre os emails do usuário, faturas/
boletos referentes a **despesas variáveis já cadastradas no catálogo**, e
extrai:

- o **valor** da fatura do mês
- o **código de boleto** (linha digitável) para copiar e colar no pagamento

O usuário confere na tela e paga manualmente fora do app — o app só poupa
o trabalho de vasculhar a caixa de entrada. Nada é gravado no banco por
essa tela.

## Decisões tomadas com o usuário

- **Provedor**: Outlook/Hotmail.
- **Escopo de busca**: só despesas com `tipo_valor='variavel'` (cartões,
  contas de consumo) — despesas fixas com débito automático não têm
  boleto. Busca dentro da janela de competência do mês (mesma lógica do
  batimento bancário, `periodo_competencia()` em `api/db.py`).

## Autenticação — por que OAuth2 e não usuário/senha

A implementação original usava IMAP com "senha de aplicativo" (usuário +
senha). Ao testar, a autenticação falhava mesmo com credenciais corretas.
Motivo: a Microsoft desativou completamente a autenticação por senha
(Basic Authentication, incluindo senhas de aplicativo) para IMAP em
contas Outlook/Hotmail pessoais — desligamento começou em 1º de março de
2026, aplicação total em 30 de abril de 2026. Não tem senha que funcione
mais por esse caminho.

A solução foi reescrever para **OAuth2 + Microsoft Graph API**:

- Biblioteca `msal` (Microsoft Authentication Library), fluxo *device
  code*: o usuário clica "Conectar com Microsoft", recebe um código de 8
  caracteres, abre uma página da Microsoft no navegador, cola o código e
  loga — sem digitar senha nenhuma dentro do app.
- Requer um **app registrado no Azure** (gratuito, ~5 min, passo a passo
  em `.env.example`) — gera um `EMAIL_CLIENT_ID` público (não é segredo,
  mas fica em `.env` mesmo assim, fora do controle de versão).
- O token (access + refresh) fica em cache local (`.msal_token_cache.json`,
  gitignored) e renova sozinho — login manual só é necessário quando o
  refresh token expira (tipicamente meses) ou é revogado.
- Busca de mensagens via Microsoft Graph (`GET /me/mailFolders/inbox/messages`)
  em vez de IMAP puro — mais robusto e é o caminho que a própria
  Microsoft recomenda daqui pra frente.
- Escopo de permissão pedido: só `Mail.Read` (leitura) — o app nunca
  envia nem apaga email.

## Fluxo implementado

1. Usuário abre "Procurar em Emails".
2. Se `EMAIL_CLIENT_ID` não está no `.env` → mostra instruções para
   registrar o app no Azure.
3. Se configurado mas ainda não conectado → botão "Conectar com
   Microsoft" → `POST /api/email/conectar/iniciar` retorna o código de
   dispositivo e o link → usuário confirma no navegador →
   `POST /api/email/conectar/finalizar` (fica bloqueado aguardando; por
   isso o Flask roda com `threaded=True`) confirma e salva o token.
4. Escolhe o mês de referência → `POST /api/email/buscar` busca todas as
   mensagens do INBOX no período uma única vez (evita repetir a mesma
   consulta por despesa), filtra por despesa comparando palavras-chave de
   `regras_match` contra assunto/remetente (mesma função `normalize_text`
   usada no batimento bancário — melhorar o catálogo melhora as duas
   buscas), e tenta extrair valor (regex `R\$ ...`) e linha digitável
   (regex de 5 blocos de dígitos) do corpo HTML e de anexos PDF
   (`pdfplumber`).
5. Mostra por despesa: assunto, remetente, data, valor encontrado e linha
   digitável com botão de copiar. PDFs escaneados como imagem (sem texto
   extraível) não são suportados — aparece "linha digitável não
   encontrada, abra o email manualmente" em vez de falhar.

## Notas para manutenção futura

- Se um dia o usuário trocar de provedor de email, só `api/email_busca.py`
  muda — o resto do app (catálogo, competência, regex de extração) é
  reaproveitável.
- Se a busca por `$filter` de data no Graph API começar a retornar volume
  grande (muitos emails no período), falta paginação mais agressiva — hoje
  já pagina via `@odata.nextLink`, mas não há limite superior nem
  amostragem; para uma caixa de entrada muito cheia isso pode ficar lento.
