# Armadilhas de Ambiente e Decisões de Robustez

Problemas que **não** estão no código de negócio, custaram horas de
diagnóstico e voltariam a morder numa reconstrução. Cada um traz o sintoma
observado, a causa real e a solução.

---

## IPv6 anunciado mas sem rota — travava toda chamada de rede

**Sintoma:** a aba "Procurar em Emails" abria em branco, sem nenhum botão. A
busca não terminava nunca.

**Diagnóstico:** a rede anuncia endereços IPv6 para `login.microsoftonline.com`,
`graph.microsoft.com` e a API do Gemini, mas **não roteia nenhum deles**.
Conectar num IPv6 só falha depois do timeout de TCP do Windows (~21s); o IPv4
responde em ~0,02s. O Python tenta os endereços na ordem que o sistema devolve
— IPv6 primeiro.

Medições reais:

| Operação | Antes | Depois |
|----------|-------|--------|
| Construir cliente MSAL | 21 s | 0,48 s |
| Requisição HTTP simples | 168 s | 0,57 s |
| Chamada ao Gemini | pendurava (>120 s) | 1,43 s |

O `curl` não sofria disso porque implementa **Happy Eyeballs (RFC 8305)**,
testando IPv4 e IPv6 em paralelo — foi o que mascarou o problema no começo do
diagnóstico ("no curl funciona").

**Solução:** `api/net.py` — `preferir_ipv4()` faz patch em
`socket.getaddrinfo`, forçando `AF_INET` quando a família não foi pedida
explicitamente. Chamado no topo de `app.py`, **antes** de importar qualquer
coisa que use rede.

> O patch é em `socket.getaddrinfo` de propósito: é o ponto único por onde
> `requests` (MSAL/Graph) e `httpx` (SDK do Gemini) passam. A primeira
> tentativa remendou só o `urllib3` e o Gemini continuou travando — remendar
> biblioteca por biblioteca deixa brechas.

Escape: `FORCE_IPV4=0` no `.env`, caso o app rode numa rede só-IPv6.

---

## Dois backends na mesma porta (Windows)

**Sintoma:** uma correção testada e comprovadamente certa "não funcionava"
quando exercitada via HTTP.

**Causa:** no Windows, o Werkzeug/Flask usa `SO_REUSEADDR` e **dois processos
conseguem escutar a porta 5173 ao mesmo tempo**, sem erro. As requisições caem
em qualquer um deles — inclusive num processo antigo, rodando código velho.

**Como evitar:** antes de testar, confirme que só há um processo:

```bash
tasklist //FI "IMAGENAME eq python.exe"
taskkill //F //IM python.exe //T     # encerra todos
```

Melhor ainda: para testar backend, use o **test client do Flask** em processo
próprio, sem porta nenhuma:

```python
import api.db as dbmod
dbmod.DB_PATH = '<copia do banco>'   # opcional: testar sem tocar no banco real
import app as appmod
cli = appmod.app.test_client()
cli.post('/api/batimento', json={'mes_ref': '2026-08'})
```

---

## Python não recarrega sozinho

`app.py` roda com `use_reloader=False`. Qualquer mudança em `api/*.py` **só vale
depois de fechar e reabrir o app**. Mudança de frontend exige `pnpm run build:ui`.

Sintoma clássico: "corrigi e continua igual".

---

## MSAL: não reconstruir o cliente a cada request

Construir um `PublicClientApplication` dispara descoberta/validação da
autoridade pela rede. O código original criava um cliente novo em cada função
(`_msal_app(cache)`), então **toda** ação na tela de email pagava esse custo.

Hoje a instância é construída **uma vez por execução** e reaproveitada
(`_msal_app()` com cache em módulo).

Complementarmente, `/email/status` **nem usa MSAL**: lê a conta direto do JSON
de cache de token. Abrir a aba não pode depender de rede.

---

## Nunca deixar a tela em branco

Três defesas, porque cada uma dessas falhas sozinha já produziu tela preta:

1. **Timeout em toda chamada de API** (`AbortController`, 20s padrão em
   `lib/api.ts`). Sem timeout, uma chamada que não responde prende a tela em
   "carregando" para sempre. Exceção: `conectar/finalizar` usa `timeoutMs: 0`,
   porque espera legitimamente o login no navegador.
2. **Estados explícitos de carregando / erro / retry** — a tela de email
   distingue "verificando", "erro + tentar de novo" e "conectado". Renderizar
   `null` enquanto espera é o que produzia a tela vazia.
3. **`ErrorBoundary` por aba** (`components/ErrorBoundary.tsx`) — um erro de
   render passa a mostrar a mensagem na aba afetada, em vez de apagar o app
   inteiro.

> O bug que motivou o item 3: um `status.configurado` lido enquanto `status`
> ainda era `null` lançava TypeError e derrubava toda a árvore do React.

---

## Excel do Itaú

- É **`.xls` BIFF legado**, não `.xlsx` renomeado → precisa de `xlrd`, não
  `openpyxl`.
- **Não tem coluna de status.** Lançamentos agendados são identificados por uma
  **linha-marcador** com o texto "lançamentos futuros"; tudo depois dela é
  agendado.
- É o **único** formato em que o Itaú entrega lançamentos futuros — por isso o
  suporte a Excel não é conveniência, é requisito.

---

## Casamento de texto por palavra inteira

Comparar com `in` (substring) gerou falsos positivos reais: "conta agua" casando
com `contato@...`, "net" com "netflix", "iptu casa" com "Casas Bahia".

`tem_palavra()` usa fronteira de palavra e é **compartilhada** entre batimento e
busca de emails, para as duas telas não divergirem.

---

## Dados vindos de IA nunca entram sem validação

Todo código de pagamento sugerido pelo Gemini é validado por formato **e** por
presença literal no texto de origem antes de chegar à tela (doc 08). Um LLM
prefere inventar um número plausível a admitir que não achou — e aqui o dado
vai ser usado para pagar uma conta.

---

## Ambiente de execução

| Item | Detalhe |
|------|---------|
| Rodar | `python app.py` (janela PyWebView) ou `python app.py --api` (só backend) |
| Porta | 5173, fixa |
| Build do front | `pnpm run build:ui` → `frontend/dist` (versionado) |
| Tipagem | `npx tsc --noEmit -p tsconfig.web.json` |
| Segredos | `.env` (gitignored): `EMAIL_CLIENT_ID`, `GEMINI_API_KEY`, `FORCE_IPV4` |
| Token de email | `.msal_token_cache.json` (gitignored) |
| Banco | `dev.sqlite` (gitignored) — fazer backup antes de teste destrutivo |

---

## `docs/07` é escrito mesmo em teste com banco de rascunho

`_registrar_dicionario()` grava direto em `docs/07-dicionario-despesas.md`, um
caminho fixo no repositório — **não** depende de `DB_PATH`. Então um teste que
aponta `api.db.DB_PATH` para uma cópia do banco e confirma um batimento **suja
o dicionário de verdade**, e como esse arquivo é o gabarito de
`tools/avaliar_batimento.py`, a medição seguinte muda de resultado sozinha.

Aconteceu: uma comparação "antes x depois" acusou um erro a mais que não
existia — o gabarito tinha crescido no meio do experimento, com entradas que o
próprio teste havia escrito.

**Ao testar confirmação de batimento:** rode `git diff docs/07-...` no fim e
remova o que o teste escreveu. E para comparar duas versões do código, garanta
que o gabarito é o mesmo nas duas rodadas.

---

## Exceção entre `get_db()` e `close()` trava o app inteiro

O padrão `conn = get_db()` … `conn.commit()` / `conn.close()` **não tem
try/finally**. Qualquer exceção no meio deixa a conexão aberta segurando o lock
de escrita do SQLite, e a partir dali **todas** as chamadas falham com
`database is locked` — até fechar e reabrir o app.

Aconteceu de verdade: criar uma receita com um nome que já existia levantava
`sqlite3.IntegrityError` (a coluna `nome` é UNIQUE), a conexão vazava, e o app
ficava inutilizável. Um erro de digitação derrubava tudo.

**Ao escrever no banco numa rota, use o context manager `db()`** (`api/db.py`),
que faz commit ao sair normalmente e fecha em qualquer caso. E trate
`IntegrityError` como resposta 409 legítima: nome repetido é erro do usuário,
não falha de sistema.

As rotas de escrita de catálogo (despesa e receita) já foram convertidas; as
demais ainda usam o padrão antigo.
