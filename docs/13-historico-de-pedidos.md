# Histórico de Pedidos e Decisões

Ordem cronológica do que foi **pedido** e do que foi **feito**, com o porquê.
Serve para reconstruir o app sem repetir os becos sem saída — vários itens aqui
existem porque uma primeira abordagem falhou por um motivo que só aparece em uso
real.

Formato: **pedido → decisão → motivo**. O commit fica ao lado para consultar o
diff quando o detalhe importar.

---

## Base

**Especificação e stack** (`d4e6817`, `fcd2c58`)
Flask + PyWebView (janela nativa) + React/TypeScript/Tailwind, SQLite local.
Backend serve o frontend buildado — um processo, uma porta.

Substituir uma planilha cheia de fórmulas com números fixos embutidos
(`=D2+D39+209,9+340*2+D23`). O catálogo passa a ser cadastro único e cada mês é
**gerado** dele — não existe "copiar aba".

---

## Importação de extrato

**"Importar Excel, porque o Itaú só exporta agendados assim"** (`850c5eb`, `82b4c07`)
Suporte a `.xlsx` (openpyxl) e, depois, ao **`.xls` BIFF legado** (xlrd) — o
arquivo real do Itaú não era xlsx renomeado.

Descoberta no arquivo real: **não há coluna de status**. O agendado é
identificado por uma linha-marcador "lançamentos futuros"; tudo depois dela é
agendado. O parser foi reescrito para isso.

**"As despesas de um mês começam quando meu salário cai, dia 27"** (`fe09f38`)
Mês de competência parametrizado (`config.dia_recebimento_salario`), não mês do
calendário. `periodo_competencia()` converte `mes_ref` no intervalo real.

**"Mostre um resumo do que casou para eu conferir e corrigir"** (`fe09f38`, `afe4a20`)
Resumo pós-batimento com correção item a item, alimentando um dicionário legível
(`docs/07`) de "texto do extrato → despesa".

**"Inverta: liste as transações sobrando, não as despesas"** (`a2df61c`)
O fluxo original pedia para escolher a transação de cada despesa. Invertido:
mostra os débitos sem correspondência e o usuário escolhe a despesa — inclusive
criando uma nova ali mesmo.

**"Rodei o batimento duas vezes e o trabalho sumiu"** (`98ed9ba`)
O batimento gravava direto; a segunda rodada só via o que sobrou da primeira, o
que parecia perda de dados.

→ Virou **preview + "Confirmar tudo"**: `/api/batimento` não grava nada, só
`/api/batimento/confirmar` persiste. Rodar de novo passou a ser inofensivo.
Esse padrão foi depois reaplicado no catálogo e nos emails.

**"A importação tem que ser incremental, com botão de reset"** (`6252599`)
Dedupe por `(data, descricao, valor)`; reimportar só traz o que é novo e
preserva o que já foi confirmado. "Resetar mês" zera a conferência **sem**
apagar transações — não precisa reenviar arquivo.

**"Reimportei e o agendado não virou pago"** (`e5bd15a`)
Bug real: quando um agendado é debitado, ele reaparece com data, descrição e
valor **idênticos** — só a situação muda. A dedupe descartava a versão debitada
como duplicata e a transação ficava `agendada` para sempre.

→ Agora a transação existente é **atualizada no lugar** (preservando o vínculo
com a despesa). E o batimento passou a reavaliar lançamentos já casados cuja
transação mudou de situação, mostrando o status anterior riscado.

**"O batimento está ruim — identificou metade, e várias erradas"** (2026-08)
Diagnóstico mediu o mês inteiro: 18 de 39 casados e, entre os 10 com correção
já registrada, **7 repetindo exatamente o erro que o usuário já tinha
corrigido**.

A causa não era o scoring: `_registrar_dicionario()` gravava as correções em
`docs/07` (arquivo legível) e **nada lia de volta**. Para email já existia
aprendizado (`email_despesa_regra`); para extrato, não.

→ Tabela `transacao_despesa_regra`, semeada com as 29 correções já existentes e
alimentada por toda confirmação. Chave é o *padrão* da descrição, sem data nem
número de documento, porque o texto muda todo mês. Resultado: 18 → 31
casamentos, zero erro conhecido.

Bugs encontrados durante a medição, que só apareceram por medir:
- variável `regras` do meu código colidia com a `regras` do laço (o
  `regras_match` da despesa), anulando tudo em silêncio — as regras existiam no
  banco e não surtiam efeito nenhum;
- o bônus da regra atropelava o valor, fazendo `salário` levar o valor que era
  do `vale transporte` (mesma descrição, valores diferentes);
- o próprio avaliador acusava acerto como erro, por assumir uma despesa por
  descrição quando a mesma descrição serve a várias.

Ferramenta que ficou: `tools/avaliar_batimento.py`, para medir em vez de julgar
por impressão.

**"Muita coisa que o extrato mostra como paga aparece como agendada"** (2026-08)
Continuação do bug `e5bd15a`, que só resolvia metade. O Itaú **troca a
descrição** quando o agendado é debitado (`PAG TIT 662992535000` vira
`PAG BOLETO EDIFICIO LINCOLN GARDEN`), então a dedupe por
`(data, descrição, valor)` não reconhecia a transação: a versão debitada
entrava como nova e a `agendada` ficava órfã no banco. O batimento casava a
despesa com a órfã e mostrava "Agendado".

→ `_reconciliar_agendadas()` casa órfã e real por `(data, valor)` — os dois
campos que **não** mudam nessa transição — e apaga a órfã, migrando antes
qualquer vínculo já confirmado. Só age em par inequívoco e em data passada.
Roda na importação e também no batimento, para curar o que já estava no banco
sem exigir reimportação. Eram 6 fantasmas em agosto.

**"Divida a revisão em 3 partes, e cada uma com o combo invertido"** (2026-08)
A tela mostrava casadas e transações sobrando, mas **nunca listava as despesas
que ficaram sem casar** — só dava para trabalhar a partir do extrato. E o combo
das transações oferecia o catálogo inteiro, inclusive despesas já casadas com
outra transação.

→ Três seções numeradas: casadas, despesas ativas não encontradas, transações
sem despesa. As duas últimas são espelho (mesma associação por pontas opostas),
alimentam a mesma função e comem da mesma lista, encolhendo juntas. O combo da
seção 3 passou a listar só as despesas da seção 2.

---

## Catálogo

**"Combobox não serve, quero digitar e achar"** (`66379c2`)
`DespesaPicker`: seletor com busca por digitação, filtrando por todos os termos.
Usado em toda tela que escolhe despesa.

**"Importar minha lista de despesas de uma planilha"** (`52e7159`)
Três cenários (só nome / +categoria / +categoria+valor), sempre com **plano de
revisão antes de aplicar** — a operação desativa tudo que não está no arquivo,
o que é destrutivo demais para rodar às cegas.

**"Deixe colar da área de transferência"** (`dcb235f`)
Células copiadas do Excel chegam como **TSV**. `_ler_linhas(file, texto)` aceita
as duas origens e devolve a mesma lista de linhas cruas, então o resto do fluxo
não muda.

**"Criar despesa direto na tela de associar"** (`6d732e0`)
"+ Nova despesa" no `DespesaPicker`, em importação e emails.

No mesmo commit: as telas ficam **todas montadas** (só a visibilidade muda, para
não perder estado ao trocar de aba), então cada uma precisa **recarregar o
catálogo ao voltar a ficar visível** — senão despesa criada em outra aba nunca
aparecia.

**"Ordenar por categoria, valor ou vencimento"** (`b9f7f7c`)
Por categoria mantém o agrupamento; nas outras vira lista única (agrupar
quebraria a ordem) com coluna de categoria. Sem valor/sem dia vão para o fim.

**"Só devem aparecer as despesas ativas no mês"** (`00b6403`)
Filtro por `ativo`, **exceto** despesa que já teve movimento real no mês —
escondê-la tiraria um pagamento real do total e o mês fecharia errado. O mesmo
filtro vale no `/resumo`, senão o total não bate com as linhas exibidas.

Aproveitado: o batimento também passou a ignorar despesas inativas, que podiam
roubar transação de despesas em uso.

---

## Busca em emails

**"Procure boletos no meu email"** (`33e7c58`)
Objetivo explícito: **não** criar despesa nem automatizar batimento — só achar
valor e código para pagar manualmente.

**Senha de aplicativo parou de funcionar** (`d6ef98c`, `f2c3a3d`)
A Microsoft desativou Basic Auth (inclusive senha de app) para IMAP em contas
pessoais, com aplicação total em 30/04/2026. Não havia senha que funcionasse.

→ Reescrito para **OAuth2 + Microsoft Graph** com `msal`, fluxo *device code*,
escopo só `Mail.Read`. Exige app registrado no Azure (passo a passo em
`.env.example`, incluindo os erros AADSTS16000 e AADSTS70002).

**"Palavra-chave não acha a SulAmérica"** (`4b15564`)
Limite estrutural: a operadora quase nunca aparece no *nome* da despesa (a
despesa é "convenio marco antonio", o email diz "SulAmérica").

→ Classificação e extração com **Gemini**, com fallback para palavra-chave/regex
quando não há chave. Junto veio a **validação anti-alucinação**: o modelo
chegou a devolver linhas digitáveis inventadas em teste.

**"Pesquisei 2 meses e não achou nada — impossível"** (`2ee769d`, `c5a07f5`)
Três problemas somados: corpo de todos os emails baixado em massa; classificação
num prompt gigante cuja falha era **engolida** (virava "nada encontrado"); e
cliente Gemini **sem timeout**, que pendurava.

→ Corpo sob demanda, classificação em lotes de 200 com falha visível, timeout de
60s. Depois, a pedido, a busca virou **loop diário em thread de fundo**, com
progresso na tela e **cancelamento** que preserva o que já foi achado.

**"E se o boleto for Pix?"** (`4ffe9fd`)
Reconhecimento de **BR Code/EMV** além de boleto, com `tipo_codigo` gravado para
Mês Atual rotular "Copiar Pix" vs "Copiar boleto".

**"Quero confirmar/cancelar tudo, e desfazer item a item"** (`4ffe9fd`)
Associação deixou de gravar na hora: vira pendência em tela, com "Confirmar
tudo", "Cancelar tudo" e "Desfazer" por item.

**"Repita o mês anterior e me diga o que ficou de fora"** (`4ffe9fd`)
Botão que desloca o período em +1 mês e pré-marca como pendente **só** o que
casa com remetente já confirmado antes; IA e palavra-chave continuam exigindo
revisão manual. Lista dividida em "associados/pendentes" e "não associados".

**"Associei mas o valor não mudou no mês"** (`94083b2`)
Como quase todo mês já tem lançamento herdado do histórico, o `INSERT OR IGNORE`
nunca disparava e o valor do email era descartado. Agora a confirmação atualiza
`valor_esperado`.

---

## Robustez e ambiente

**"A tela de email não mostra mais o botão / tela preta"** (`03ceb84`, `a1cc019`)
Causa raiz: **IPv6 anunciado e não roteado**. Cada chamada de rede esperava o
timeout de TCP do Windows — 21s para construir o cliente MSAL, 168s numa
requisição simples, Gemini pendurado. Como `/email/status` é chamado ao abrir a
aba, a tela nunca renderizava.

→ `api/net.py` força IPv4 em `socket.getaddrinfo` (ponto único de `requests` e
`httpx`); MSAL passou a ser instanciado uma vez só; `/email/status` deixou de
usar rede. Somaram-se três defesas para que rede ruim nunca mais produza tela
vazia: timeout nas chamadas, estados de carregando/erro/retry e `ErrorBoundary`
por aba.

Detalhes e medições em [12-armadilhas-e-ambiente.md](12-armadilhas-e-ambiente.md).

---

## Princípios que se repetiram

1. **Preview antes de gravar** em tudo que é destrutivo ou volumoso —
   batimento, catálogo, associação de emails.
2. **Dado de IA nunca entra sem validação** de formato e de presença literal na
   origem.
3. **Nada silencioso**: falha parcial vira aviso na tela, não "nenhum
   resultado".
4. **Preservar trabalho já confirmado** — reimportar, reprocessar ou rodar duas
   vezes nunca pode destruir revisão manual.
5. **Escondê-lo não pode falsear total**: filtro de exibição que remove
   pagamento real do somatório é bug, não feature.
