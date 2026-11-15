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

### Quando a IA cai no meio da busca

A busca roda **dia a dia**, então um erro que não se resolve sozinho era
repetido em cada um dos 60 dias do período: 60 chamadas condenadas, 60 avisos
idênticos empilhados na tela, busca mais lenta para nada. E, pior, degradação
escondida — sem classificação, `ids_fatura` volta vazio e só passam emails de
remetente já conhecido; o emissor novo, que é o motivo de a IA existir aqui,
some da busca.

`_motivo_para_desistir_da_ia()` separa os dois tipos de erro:

| erro | o que a busca faz |
|---|---|
| 402 sem crédito, 401/403 chave recusada, 429 cota, 400 requisição | desliga a IA no resto da busca, avisa **uma vez** com a causa e o que fazer, segue por palavra-chave |
| rede, timeout, 5xx | transitório — tenta o próximo lote normalmente |

Os avisos da tela são **montados a partir do estado** (`_avisos_compostos()`),
não acumulados um por dia. Guardar uma string por dia foi o que produziu a tela
cheia de linhas iguais.

Sintoma, diagnóstico e o texto exato do erro 402 ficam em
[12-armadilhas-e-ambiente.md](12-armadilhas-e-ambiente.md).

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

## Um email, vários boletos (carnê de financiamento)

Pedido do usuário: *"o email do banco Safra vem com um PDF com uma série de
boletos, já de todo o financiamento, e hoje você não consegue ler esse PDF"*.

O PDF era lido; o **modelo de dados** é que não cabia. A busca devolvia
`linha_digitavel` — singular — por email: `RE_LINHA_DIGITAVEL.search()` achava a
primeira parcela e as outras 59 iam para o lixo **sem nada na tela**. Nenhum
aviso, porque da perspectiva do código tinha dado certo: achou um boleto.

Hoje cada email carrega uma lista `codigos`, e `_todos_os_codigos()` varre o
texto inteiro com `finditer`.

### O código já diz o seu vencimento e o seu valor

Essa é a peça que faz o resto funcionar sem IA. A linha digitável de 47 dígitos
tem, no campo 5, **fator de vencimento (4 dígitos) + valor em centavos (10)**:

| posições | conteúdo |
|---|---|
| 34–37 | fator de vencimento |
| 38–47 | valor em centavos |

Num carnê com 60 parcelas, perguntar a um LLM "qual o valor desta parcela" num
texto com 60 valores é exatamente o que não funciona — e não precisa: cada
parcela nasce com valor e vencimento lidos do próprio código, que não alucina.

**O mês de destino vem do vencimento da parcela**, não da data do email. Todas
as parcelas do carnê chegam no mesmo email; pela data do email as 60 cairiam na
mesma competência. Pix, que não tem vencimento, continua usando a competência do
email.

### Fator de vencimento: duas bases

O fator chegou a 9999 em 21/02/2025 e a FEBRABAN reiniciou a contagem em 1000 =
**22/02/2025**. Então um mesmo fator tem duas leituras: 1603 é 26/02/2002 na base
antiga (07/10/1997) e 18/10/2026 na nova. `_vencimento_do_fator` escolhe a
**mais próxima da data do email** — boleto novo cai na base nova, boleto antigo
na antiga, sem configuração. Fator abaixo de 1000 significa "sem vencimento" por
especificação, e vira `None`.

### Dígito verificador, agora obrigatório de conferir

Varrendo um PDF inteiro em busca de *todos* os códigos, qualquer sequência de
dígitos no layout certo entraria como boleto. Pior: a extração de PDF quebra
linha em lugar estranho, e um código com um dígito trocado é **pior que nenhum**
— o pagamento vai para o lugar errado.

`_boleto_dv_ok` confere os quatro DVs: módulo 10 nos campos 1, 2 e 3 e módulo 11
no DV geral do código de barras. Validado contra os 5 boletos reais que já
estavam gravados no banco (Porto, Bradesco, SulAmérica): os quatro DVs conferem
nos cinco, o valor sai exato e o vencimento da SulAmérica dá 08/09/2026 —
exatamente o que está registrado mais abaixo neste doc.

Código reprovado no DV **não é descartado**: vai para a tela com aviso em
âmbar. Sumir em silêncio é o que o princípio 4 proíbe, e um emissor fora do
padrão não pode deixar o usuário sem o boleto.

### Anexo que não se declara PDF

Segundo gargalo do mesmo pedido: o anexo só era lido se
`contentType == 'application/pdf'` **exato**. Emissor que manda o carnê como
`application/octet-stream` (ou com o tipo em branco) tinha o anexo ignorado, e
o email aparecia na busca sem código nenhum — parecendo falha de extração.

`_e_pdf()` decide por três sinais: tipo declarado contendo `pdf`, nome
terminando em `.pdf`, ou os bytes mágicos `%PDF-`. E quando o anexo é PDF mas
não rende texto, o email volta com a nota dizendo qual das causas foi (senha que
não serviu, PDF de imagem escaneada) em vez de aparecer vazio.

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

### Duas armadilhas que só o email real revelou

**O botão da fatura é uma imagem.** Das 14 âncoras do email "📬 Seu boleto
SulAmérica Saúde Chegou!", todas são imagens sem texto visível. Casando só pelo
texto da âncora, o rótulo vinha vazio e **nenhum link era reconhecido**. O
`alt` da imagem tem exatamente `"Clique aqui para baixar a fatura"` — é dele que
o rótulo precisa sair. Como rede de segurança, se nenhum rótulo casar o app
tenta todos os links do email (máx. 18), o que cobre o caso do `alt` vazio.

**`parse_qs` já decodifica — decodificar de novo quebra o link.** Todo link
recebido vem embrulhado em `safelinks.protection.outlook.com/?url=...`. O
desembrulho fazia `unquote(parse_qs(...)['url'][0])`, e esse decode duplo
transformava o `%2B` do token do rastreador em `+` literal. O servidor relê `+`
como espaço:

| forma do token | resposta |
|---|---|
| `...II+S7...` (decode duplo) | **404**, 1.245 bytes de HTML |
| `...II%2BS7...` (decode simples) | **200**, `application/pdf`, 208.019 bytes |

`parse_qs` já desfaz o percent-encoding; o `unquote` extra é que sobrava.

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

**Confirmado com email real** (boleto de 20/08/2026, vencimento 08/09): o link
responde 302 para `informe-documento.paas.sulamerica.com.br`, entrega um PDF de
208 KB que **não abre sem senha** e abre com `5551`, de onde sai a linha
digitável de 47 dígitos e o valor de R$ 5.278,79. A senha é mesmo do **PDF** —
não há formulário web no caminho.

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
5. Mostra **um cartão por email** (não agrupado por despesa, já que cobre
   despesas sem correspondência óbvia por palavra-chave): assunto, remetente,
   data, um seletor de despesa para o email inteiro, e abaixo **todos** os
   códigos de pagamento que aquele email tem — cada um com tipo, vencimento,
   valor, botão de copiar, o mês de competência sugerido (editável) e o botão
   **Confirmar**. PDFs escaneados como imagem (sem texto extraível) não são
   suportados — o cartão traz a nota dizendo por que não saiu código, em vez
   de aparecer vazio.
6. **Confirmar grava.** Até clicar, a busca é só leitura.
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

## Confirmar em três alcances

O resultado da busca **é** o preview: nada é gravado enquanto não se clica em
Confirmar (princípio 1 — e só `POST /email/associar/lote` persiste, mesmo padrão
do batimento, doc 10, e da importação de catálogo, doc 11). O que muda com o
carnê é a escala: confirmar 60 parcelas uma a uma não é revisão, é digitação.

| botão | onde | o que grava |
|---|---|---|
| **Confirmar** | na linha do código | aquela parcela, naquele mês |
| **Confirmar os N** | no cabeçalho do email | todas as parcelas pendentes daquele email |
| **Confirmar todos (N)** | barra fixa no topo | tudo que já tem despesa definida, de todos os emails |

A despesa é escolhida **por email** e o mês **por código**: todas as parcelas de
um carnê são da mesma despesa, e o que varia entre elas é a competência. Um
seletor de despesa por parcela seriam 60 seletores para uma informação só.

Ao confirmar, para cada item: grava `linha_digitavel` + `tipo_codigo` no
`lancamento` da despesa/mês, **atualiza o `valor_esperado`** com o valor
achado no email, e grava/atualiza a regra remetente→despesa.

### Já associado antes: indicação, não trabalho de novo

Pedido do usuário: *"se a pesquisa for feita de novo e você achar despesas já
associadas e corretas (valores batem), simplesmente coloca uma indicação de
despesa já associada"*.

A busca carrega de uma vez todos os códigos já gravados em `lancamento`,
indexados pelo código **sem pontuação** — o mesmo boleto reextraído com outro
espaçamento tem de casar. Cada código achado vem com `ja_associado`:

| situação | na tela |
|---|---|
| código gravado, valor igual | *"já associado — despesa / mês ✓"*, sem botão |
| código gravado, valor diferente | aviso em âmbar com o valor que está gravado |
| sem valor dos dois lados | indicação sem afirmar que bate (`valor_bate: null`) |
| nunca visto | fluxo normal de associação |

Valor divergente não é escondido como "já resolvido": é o caso que precisa de
atenção. Repetir a busca no mesmo período passa a ser indolor, e
"Repetir mês anterior" só mostra o que de fato é novo.

### Uma despesa só tem um código por competência

`UNIQUE(mes_ref, despesa_id)`: confirmar um carnê inteiro põe dezenas de
parcelas na mesma despesa, e duas caindo na mesma competência fariam a segunda
**sobrescrever** a primeira sem ninguém ver. O item é recusado e volta como
`conflito` na resposta, mostrando o código que já estava lá e um botão
**Substituir** — a troca existe, mas é decisão explícita (princípio 2).

Conflito não derruba a requisição: o que deu para gravar é gravado, e a resposta
traz `aplicadas` e `conflitos` separados.

> A atualização do valor foi um bug real: como quase todo mês já tem um
> lançamento herdado do histórico, o `INSERT OR IGNORE` nunca disparava e
> o valor do email era descartado — só o código era salvo.

**Não há desfazer.** Confirmado é gravado; para reverter, edita-se em Mês Atual,
como qualquer lançamento.

## Três coisas que o uso real revelou

### Reconhecer não é gravar — e a tela não deixava isso óbvio

Relato do usuário: *"a da porto reconheceu, mas não associou o código de uma
maneira que eu possa copiar na aba de mês atual"*. Não era defeito de código.

O Porto aparece reconhecido porque o remetente já estava mapeado, e com
"Repetir mês anterior" ele entra **sozinho em pendentes**. Mas pendente não é
gravado: só `POST /email/associar/lote` persiste. O usuário foi ao Mês Atual
esperando o botão de copiar, e o lançamento de outubro estava com
`linha_digitavel` nulo — o de **setembro** tinha código, de uma confirmação
anterior.

A lição é de produto, não de implementação: **reconhecimento se parece com
conclusão.** O represamento é deliberado (preview→confirmar, como no batimento),
mas precisa ser inescapável na tela.

> **Resolvido pela reforma do carnê.** O estado intermediário "pendente" não
> existe mais: o resultado da busca é o preview, e **Confirmar grava**. Some o
> estado que parecia conclusão sem ser, e com ele a possibilidade de ir ao Mês
> Atual achando que estava salvo. Um clique a menos por item, e o princípio 1
> continua honrado — nada persiste sem confirmação explícita.

### O mês de destino vinha do calendário, não da competência

Bug real, e pior que o sintoma relatado. A tela derivava o mês da associação do
**fim do período pesquisado** (`novaFim.slice(0, 7)`). Uma busca terminando em
30/09 arquivaria o boleto de **outubro dentro de setembro**.

Agora cada boleto carrega **`mes_ref_sugerido`**, calculado no servidor por
`competencia_da_data()` com o dia de corte configurado. A tela usa isso e mantém
o alvo da repetição apenas como reserva.

> **Bug latente, medido e ainda aberto:** `currentMesRef()` no frontend devolve
> `YYYY-MM` do calendário e **ignora o corte de competência**. Com corte no dia
> 26, ela erra em **6 dias de cada mês** (26 a 31) — em 28/10 diz `2026-10`
> quando a competência já é `2026-11`. Hoje a regra de competência existe em
> dois lugares, e um deles está errado. Consertar exige levar o dia de corte até
> um `useState` de inicialização (`MesAtual.tsx`), que roda antes do config
> carregar — é mudança própria, não um remendo.

### Mercado Pago: reconhecido agora, mas sem código para copiar

O e-mail do Mercado Pago **não tinha remetente mapeado**, e por isso dependia só
do classificador de primeira passada. A linha de corte é implacável: se o
classificador não marca o e-mail como fatura **e** o remetente é desconhecido, o
e-mail é descartado sem nem ser lido.

E o classificador tinha motivo para errar — os assuntos reais são *"Você já pode
pagar seu cartão de crédito"* e *"Pague seu cartão, a fatura vence amanhã"*,
enquanto o único que diz "fatura" claramente é *"Você **pagou** a fatura"*, um
recibo que o prompt manda ignorar.

Mapeados os **dois** remetentes que o emissor usa:

| remetente | despesa |
|---|---|
| `nao-responder@mercadopago.com.br` | Cartao Mercado Pago |
| `no-reply@mercadopago.com.br` | Cartao Mercado Pago |

> **Mas não haverá código para copiar.** O diagnóstico é categórico:
> `corpo: {"boleto": null, "tem_boleto": false, "tem_pix": false}` e
> `anexos: []` nos dois avisos de cobrança. O único caminho de pagamento é um
> link `onlinepayments/universal-link` rotulado "pagar fatura", que abre o app
> atrás de login. **É limitação do emissor, não do app** — e a tela deve dizer
> isso em vez de deixar o usuário esperando um botão que não pode existir.

## Repetir mês anterior

Depois de uma busca com pelo menos uma associação confirmada, o período é
guardado em `localStorage` e aparece o botão **"Repetir mês anterior"**.
Ele desloca o período salvo em +1 mês e refaz a busca.

Cada email carrega `origem_sugestao`: `regra` (remetente já associado antes),
`ia` (só o Gemini sugeriu) ou `palavra_chave` (fallback sem Gemini). Com regra,
a despesa já vem escolhida no seletor e **"Confirmar todos"** fecha a revisão do
mês num clique — que era o pedido original ("no próximo mês trazer tudo
pré-preenchido só pra eu conferir"), agora sem o estado "pendente" no meio.

O que já estava gravado aparece como indicação (ver "Já associado antes"), então
repetir um período que se sobrepõe ao anterior não traz trabalho repetido.

## Endpoints

| Rota | Método | O que faz |
|------|--------|-----------|
| `/api/email/status` | GET | Se está configurado e qual conta está conectada |
| `/api/email/conectar/iniciar` | POST | Inicia device code flow |
| `/api/email/conectar/finalizar` | POST | Aguarda o login no navegador (bloqueia) |
| `/api/email/buscar/iniciar` | POST | Dispara a busca em thread de fundo |
| `/api/email/buscar/status` | GET | Progresso + emails (com seus códigos) achados até agora |
| `/api/email/buscar/cancelar` | POST | Para ao fim do dia corrente |
| `/api/email/associar/lote` | POST | Persiste as associações; devolve `aplicadas` e `conflitos` |
| `/api/email/diagnostico` | GET | Por que um remetente não rendeu código — inclui **todos** os códigos de cada anexo |

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
