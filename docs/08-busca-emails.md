# Função: Procurar em Emails

**Status:** implementado (`api/email_busca.py`, aba "Procurar em Emails").

## Objetivo

Não cria despesas novas nem automatiza o batimento sozinho. É uma
ferramenta de apoio ao pagamento manual: busca, entre os emails do
usuário, faturas/boletos de qualquer despesa ativa do catálogo (cartão de
crédito, seguro saúde, convênio, contas de consumo etc.), extrai valor e
linha digitável, e deixa o usuário **associar** o achado a uma despesa e
mês específicos — só então isso aparece disponível para copiar na tela
Mês Atual.

## Decisões tomadas com o usuário

- **Provedor**: Outlook/Hotmail.
- **Escopo de busca**: qualquer despesa ativa do catálogo (não só
  variável — seguro saúde por boleto, por exemplo, pode ter valor fixo).
- **Período**: filtro de data início/fim livre na tela, não mais atrelado
  a um único mês de competência — faturas de coisas como seguro anual
  podem chegar meses antes do vencimento.

## Classificação e extração — por que Gemini

A primeira versão usava só palavra-chave do catálogo (`regras_match`) pra
sugerir despesa, e regex pra achar valor/linha digitável no texto. Isso
tem um limite estrutural: a operadora raramente aparece no *nome* da
despesa. Ex: a despesa é `convenio marco antonio`, mas o email da
seguradora diz "SulAmérica" — nenhuma palavra-chave bate, e por regex
puro o email nunca aparecia na busca.

Com `GEMINI_API_KEY` configurada (opcional, ver `.env.example`), a busca
passa a usar o Gemini em duas etapas:

1. **Classificação em lote** (barata: só assunto+remetente de todos os
   emails do período numa chamada só) — pergunta quais parecem fatura/
   boleto/cobrança, capturando semanticamente o que a lista de
   palavras-chave não cobre.
2. **Extração por email candidato** (corpo + texto de PDFs anexados via
   `pdfplumber`) — pede valor, linha digitável e a despesa mais provável
   entre os nomes exatos do catálogo.

Sem a chave, cai para a lógica antiga (palavra-chave + regex) — sem custo
de API, mas mais limitada.

### Validação obrigatória da linha digitável

Um LLM pode "alucinar" uma linha digitável plausível em vez de admitir que
não achou nenhuma — isso é inaceitável para um dado usado em pagamento.
Toda linha digitável que o Gemini retorna passa por `_linha_digitavel_valida()`
antes de ir pra tela:
- precisa ter exatamente 47 ou 48 dígitos;
- não pode ser degenerada (todos os dígitos iguais, sequência óbvia tipo
  `12345...`);
- os dígitos precisam aparecer **literalmente** no texto extraído do
  email/PDF (comparação por substring) — não passa se o modelo inventou.

Mesmo assim, a tela mostra um aviso pra sempre conferir contra o email
original antes de pagar. Isso não é uma garantia absoluta, só reduz bastante
o risco de um número inventado passar despercebido.

## Regras aprendidas (remetente → despesa)

Toda vez que o usuário associa um boleto a uma despesa, o remetente do
email é gravado em `email_despesa_regra` (tabela). Na próxima busca, esse
remetente já entra automaticamente como candidato com a despesa
pré-preenchida — sem depender de bater de novo por classificação/IA. Isso
é a resposta direta ao pedido do usuário: "no próximo mês trazer tudo
pré-preenchido só pra eu conferir".

A tabela é a fonte de verdade (é o que o app consulta); um espelho legível
fica em [09-dicionario-emails.md](09-dicionario-emails.md), atualizado
automaticamente por `_registrar_regra_email()` a cada associação nova.

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
4. Escolhe um período (data início/fim) → `POST /api/email/buscar` busca
   todas as mensagens do INBOX nesse período uma única vez, classifica
   (Gemini, ou palavra-chave se a chave não estiver configurada) e
   extrai valor/linha digitável dos candidatos (corpo HTML + anexos PDF
   via `pdfplumber`). Remetentes já associados antes (`email_despesa_regra`)
   entram automaticamente com a despesa pré-preenchida.
5. Mostra uma lista plana de boletos (não mais agrupada por despesa, já
   que agora cobre despesas sem correspondência óbvia por palavra-chave):
   assunto, remetente, data, valor, linha digitável com botão de copiar,
   e um seletor de despesa + mês pra associar. PDFs escaneados como
   imagem (sem texto extraível) não são suportados — aparece "linha
   digitável não encontrada, abra o email manualmente" em vez de falhar.
6. Ao confirmar a associação (`POST /api/email/associar`): grava
   `linha_digitavel` no `lancamento` da despesa/mês (sem mexer em status
   de pagamento) e grava/atualiza a regra remetente→despesa para a
   próxima busca.
7. Em Mês Atual, qualquer lançamento com `linha_digitavel` preenchida
   ganha um botão "Copiar boleto".

## Notas para manutenção futura

- Se um dia o usuário trocar de provedor de email, só `api/email_busca.py`
  muda — o resto do app (catálogo, competência, regex de extração) é
  reaproveitável.
- Se a busca por `$filter` de data no Graph API começar a retornar volume
  grande (muitos emails no período), falta paginação mais agressiva — hoje
  já pagina via `@odata.nextLink`, mas não há limite superior nem
  amostragem; para uma caixa de entrada muito cheia isso pode ficar lento.
