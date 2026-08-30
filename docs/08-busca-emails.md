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

## Boleto **e** Pix

Nem toda cobrança vem como boleto: várias operadoras (SulAmérica, por
exemplo) mandam **código Pix "copia e cola"**. Os dois formatos são
reconhecidos, e o tipo fica gravado em `lancamento.tipo_codigo`
(`boleto`/`pix`) para a tela Mês Atual rotular o botão corretamente
("Copiar boleto" ou "Copiar Pix").

Ambos são achados por assinatura estrutural, sem precisar entender o
conteúdo:

| Tipo | Assinatura | Regex |
|------|-----------|-------|
| Boleto | 5 blocos de dígitos, 47–48 dígitos no total | `RE_LINHA_DIGITAVEL` |
| Pix (BR Code / EMV) | começa em `00020...`, termina no CRC `6304XXXX` | `RE_PIX` |

O regex de Pix tolera espaço/quebra de linha no meio, porque o HTML do email
pode reformatar o texto ao virar texto puro (`_extrair_texto_html`); os
espaços são removidos antes de validar e guardar.

### Validação obrigatória do código de pagamento

Um LLM pode "alucinar" um código plausível em vez de admitir que não achou
nenhum — inaceitável para um dado usado em pagamento. Todo código que o
Gemini retorna passa por validação antes de ir pra tela:

- **Boleto** (`_linha_digitavel_valida`): exatamente 47 ou 48 dígitos; não
  degenerado (dígitos todos iguais, sequência óbvia tipo `12345...`).
- **Pix** (`_codigo_pix_valido`): tamanho entre 40 e 700; começa com `0002`;
  tem `6304` perto do fim.
- **Os dois**: os caracteres precisam aparecer **literalmente** no texto
  extraído do email/PDF — se o modelo inventou, não passa.

Mesmo assim, a tela mostra um aviso pra sempre conferir contra o email
original antes de pagar. Isso não é garantia absoluta, só reduz bastante o
risco de um número inventado passar despercebido.

## Fatura atrás de link protegido por senha

Nem todo emissor anexa a fatura. A **SulAmérica** manda um link ("Clique aqui
para baixar a fatura") que devolve um PDF **cifrado**, cuja senha o cliente já
conhece. Sem seguir o link, esse email não tem código nenhum para extrair — o
corpo é só o convite para baixar.

O fluxo, dentro de `_texto_completo`:

1. o remetente tem senha cadastrada? Se não, **para aqui**;
2. o que já se tem (corpo + anexos) contém algum código? Se sim, para aqui;
3. procura no HTML âncoras cujo texto anuncie a fatura (`_links_de_fatura`);
4. baixa; se vier uma página em vez do arquivo, procura ali dentro um link de
   PDF e segue **uma** vez — o padrão "página intermediária com o botão";
5. abre o PDF com a senha e joga o texto no mesmo caminho de sempre.

Daí para frente nada muda: o código sai por Gemini ou regex, passa pela mesma
validação anti-alucinação, e chega em Mês Atual como "Copiar boleto" igual a
qualquer outro.

### Só se segue link de remetente liberado

A senha cadastrada **é** a permissão. Seguir URL de email arbitrário
transformaria o app num clicador automático de qualquer coisa que chegue na
caixa de entrada — que é exatamente o vetor do phishing. A chave casa por
substring no endereço (`sulamerica` casa `faturas@sulamerica.com.br`).

`config.senhas_fatura`, um JSON `{"remetente": "senha"}`, sobrepõe o padrão —
para acrescentar emissor sem mexer no código, e para a senha não morar no
repositório. Fica em cache durante a busca e é relido a cada nova.

**Só baixa quando precisa** (passo 2): email cujo **boleto** já veio no corpo ou
no anexo não gasta um download.

> A primeira versão desta guarda perguntava "já tem **algum** código?" e quebrou
> exatamente o caso que a funcionalidade existe para resolver. O email da
> SulAmérica traz um **Pix no corpo**: a guarda achava esse Pix, concluía que
> estava resolvido e nunca seguia o link — o boleto ficava do outro lado, sem
> ninguém buscar. A pergunta certa é "já tem **boleto**?"; Pix no corpo não é
> motivo para desistir do boleto.

### Quando os dois existem, o boleto ganha

Para remetente com senha cadastrada, o boleto tem precedência sobre o Pix. O
texto final concatena corpo + PDF, e o Pix aparece primeiro — o Gemini lê os dois
e costuma devolver o Pix. Ir atrás do PDF e ainda assim mostrar o Pix
desperdiça exatamente o trabalho que este caminho existe para fazer.

### `/api/email/diagnostico`

`GET /api/email/diagnostico?termo=sulamerica` abre no navegador e mostra, por
mensagem: se o remetente está liberado, se o corpo já tem Pix/boleto, **todas**
as âncoras do email (não só as reconhecidas), e para cada download o
status HTTP, o content-type, se é PDF, se abriu com e sem senha, e se saiu
boleto. Quando a página não é PDF, mostra os links de dentro dela e se há
`<input type="password">`.

Existe porque diagnosticar isso adivinhando custou uma rodada: separa em um
olhar se o problema é a âncora que não casa, o download que falha ou a senha que
não abre. Não grava nem associa nada.

### Verificação

Testado ponta a ponta contra um servidor HTTP local servindo uma página que
aponta para um PDF cifrado com `5551`:

| passo | resultado |
|---|---|
| `faturas@sulamerica.com.br` | senha `5551` |
| `noreply@bancoqualquer.com` | `None` — não segue link |
| âncoras do corpo | acha a da fatura, ignora a de "Ajuda" |
| página → PDF → texto | linha digitável extraída |
| código final | 47 dígitos, `tipo_codigo='boleto'`, idêntico ao original |
| senha errada (`9999`) | texto vazio, com aviso no log |

**Pendente de confirmação com email real:** os testes usam um PDF cifrado
sintético. O que ainda não foi verificado contra a SulAmérica de verdade é se a
senha `5551` é do **PDF** (é o que o código assume) ou de um **formulário web**
na página intermediária. Se for formulário, o passo 4 precisa aprender a
postá-lo. O token do Graph estava expirado durante a implementação, então não
deu para inspecionar uma mensagem real.

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
4. Escolhe um período (data início/fim) → `POST /api/email/buscar/iniciar`
   dispara a busca **em thread de fundo, um dia por vez** (ver "Busca
   dia a dia" abaixo). A tela acompanha por polling em
   `GET /api/email/buscar/status`.
5. Mostra uma lista plana de boletos (não agrupada por despesa, já que
   cobre despesas sem correspondência óbvia por palavra-chave): assunto,
   remetente, data, valor, código de pagamento com botão de copiar, e um
   seletor de despesa + mês pra associar. PDFs escaneados como imagem
   (sem texto extraível) não são suportados — aparece "código de
   pagamento não encontrado, abra o email manualmente" em vez de falhar.
6. Associar **não grava na hora**: entra numa lista de pendentes (ver
   "Revisão em lote").
7. Em Mês Atual, qualquer lançamento com código ganha um botão
   "Copiar boleto" ou "Copiar Pix", conforme `tipo_codigo`.

## Busca dia a dia, interrompível

Buscar um período inteiro numa requisição só não escala: numa caixa com
~3000 emails em 2 meses, a chamada demorava minutos, sem retorno nenhum
na tela e sem como interromper — e ainda corria risco de timeout.

Hoje a busca roda numa **thread de fundo**, processando **um dia por
vez** e publicando o progresso em `_busca_job` a cada dia concluído:

- `POST /email/buscar/iniciar` — dispara e retorna na hora (`total_dias`).
- `GET /email/buscar/status` — progresso, avisos e os boletos achados
  **até agora**; a tela vai preenchendo a lista conforme os dias passam.
- `POST /email/buscar/cancelar` — pede parada; a busca encerra ao fim do
  dia corrente e **mantém tudo que já achou** (nada é descartado).

Só uma busca por vez (app de um usuário só): iniciar outra enquanto uma
roda devolve HTTP 409. Se a tela remontar com uma busca em andamento no
servidor, ela retoma o acompanhamento em vez de perder o progresso.

A classificação pelo Gemini é feita em **lotes de 200** mensagens. Um
prompt com tudo de uma vez é frágil (estoura limite, trunca, dá timeout)
e, quando falhava, a exceção era engolida e a busca dizia "nada
encontrado" — um falso negativo silencioso. Hoje cada lote falha
isoladamente e as falhas aparecem como aviso na tela.

O corpo do email é buscado **sob demanda**, só para quem passou na
classificação. Trazer o HTML completo de milhares de mensagens no
`$select` em massa era lento e consumia muita memória à toa.

O cliente Gemini tem **timeout de 60s** (`HttpOptions(timeout=60_000)`).
Sem ele, uma chamada travada ficava pendurada para sempre e a busca nunca
terminava nem dava erro.

## Revisão em lote (confirmar tudo / cancelar tudo / desfazer)

Associar um boleto **não grava nada** na hora: cria uma pendência na
tela. A barra fixa no topo mostra quantas há, com **"Confirmar tudo"** e
**"Cancelar tudo"**; cada item tem **"Desfazer"** individual. Só
`POST /email/associar/lote` persiste — mesmo padrão preview→confirmar do
batimento (doc 10) e da importação de catálogo (doc 11).

A lista fica em duas seções: primeiro os **associados/pendentes**, depois
os **não associados**, para a revisão seguir de cima para baixo.

Ao confirmar, para cada item: grava `linha_digitavel` + `tipo_codigo` no
`lancamento` da despesa/mês, **atualiza o `valor_esperado`** com o valor
achado no email, e grava/atualiza a regra remetente→despesa.

> A atualização do valor foi um bug real: como quase todo mês já tem um
> lançamento herdado do histórico, o `INSERT OR IGNORE` nunca disparava e
> o valor do email era descartado — só o código era salvo.

**Desfazer só existe antes de confirmar.** Depois de gravado, vira
lançamento normal; para reverter, edita-se em Mês Atual.

## Repetir mês anterior

Depois de uma busca com pelo menos uma associação confirmada, o período é
guardado em `localStorage` e aparece o botão **"Repetir mês anterior"**.
Ele desloca o período salvo em +1 mês e refaz a busca.

Durante essa repetição, todo boleto cujo remetente **já foi confirmado
antes** (`origem_sugestao === 'regra'`) entra automaticamente como
pendente, já preenchido. Sugestão vinda da IA ou de palavra-chave
**nunca** entra sozinha — fica em "não associados" para revisão manual.
Assim o resumo já sai pronto: em cima o que foi reconhecido, embaixo o
que sobrou. Tudo ainda pode ser desfeito antes de confirmar.

Cada boleto carrega `origem_sugestao`: `regra` (remetente já associado),
`ia` (só o Gemini sugeriu) ou `palavra_chave` (fallback sem Gemini).

## Endpoints

| Rota | Método | O que faz |
|------|--------|-----------|
| `/api/email/status` | GET | Se está configurado e qual conta está conectada |
| `/api/email/conectar/iniciar` | POST | Inicia device code flow |
| `/api/email/conectar/finalizar` | POST | Aguarda o login no navegador (bloqueia) |
| `/api/email/buscar/iniciar` | POST | Dispara a busca em thread de fundo |
| `/api/email/buscar/status` | GET | Progresso + boletos achados até agora |
| `/api/email/buscar/cancelar` | POST | Para ao fim do dia corrente |
| `/api/email/associar/lote` | POST | Persiste as associações revisadas |

`/email/status` **não pode depender de rede**: ele lê a conta direto do
arquivo de cache de token, sem instanciar o MSAL. Já foi o contrário, e
uma rede lenta deixava a tela inteira sem renderizar (ver doc 12).

## Notas para manutenção futura

- Se um dia o usuário trocar de provedor de email, só `api/email_busca.py`
  muda — o resto do app (catálogo, competência, extração de código) é
  reaproveitável.
- A busca gasta uma chamada de extração do Gemini por email candidato, o
  que domina o tempo total. Se ficar caro/lento, o caminho é filtrar
  melhor os candidatos antes da extração, não paralelizar às cegas.
- O estado da busca (`_busca_job`) é global em memória, adequado a um app
  desktop de um usuário. Para múltiplos usuários seria preciso job_id e
  sessão.
