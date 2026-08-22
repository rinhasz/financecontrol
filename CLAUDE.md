# Instruções — FinanceControl

App desktop de controle de despesas mensais (Flask + PyWebView + React/TS +
SQLite). Documentação completa em **[docs/00-indice.md](docs/00-indice.md)**.

## Antes de mexer

- **Leia [docs/12-armadilhas-e-ambiente.md](docs/12-armadilhas-e-ambiente.md)**
  antes de investigar qualquer lentidão, travamento ou tela em branco. Vários
  sintomas já foram diagnosticados lá (IPv6 sem rota, dois backends na mesma
  porta, MSAL recriado por request) e custam horas se forem redescobertos.
- **O backend não recarrega sozinho.** Mudança em `api/*.py` exige fechar e
  reabrir o app. Mudança em `src/renderer` exige `pnpm run build:ui`.
- Antes de testar via HTTP, garanta que só há **um** processo Python rodando —
  no Windows dois conseguem escutar a porta 5173 e a requisição cai em
  qualquer um.

## Como testar

Prefira o test client do Flask, em processo próprio — sem porta, sem conflito:

```python
import api.db as dbmod
dbmod.DB_PATH = '<cópia do banco>'   # para não tocar no banco real
import app as appmod
cli = appmod.app.test_client()
```

Faça **backup de `dev.sqlite`** antes de qualquer teste que grave dados. É o
banco real do usuário, com anos de histórico.

Checagens: `npx tsc --noEmit -p tsconfig.web.json` e
`python -m py_compile api/*.py`.

## Princípios do produto

Vieram de problemas reais (histórico em [docs/13](docs/13-historico-de-pedidos.md)):

1. **Preview antes de gravar** em tudo que é destrutivo ou volumoso — batimento,
   importação de catálogo, associação de emails. Nada persiste sem confirmação
   explícita.
2. **Preservar trabalho já confirmado** — reimportar, reprocessar ou rodar duas
   vezes nunca pode destruir revisão manual do usuário.
3. **Dado vindo de IA nunca entra sem validação** de formato e de presença
   literal na origem. Código de pagamento errado custa dinheiro real.
4. **Nada silencioso** — falha parcial vira aviso na tela, nunca "nenhum
   resultado encontrado".
5. **Filtro de exibição não pode falsear total** — esconder da lista algo que
   entra no somatório (ou vice-versa) é bug.

## Convenções

- Commit após cada unidade de trabalho concluída, com mensagem focada no
  **porquê**. Sempre `git add` por arquivo, nunca `-A` (há artefatos de build e
  `__pycache__` no diretório). Depois, `git push`.
- `frontend/dist` **é versionado** — commite o build junto com a mudança de
  frontend.
- Segredos em `.env` (gitignored): `EMAIL_CLIENT_ID`, `GEMINI_API_KEY`,
  `FORCE_IPV4`. Nunca commitar.
- Comentários explicam **por que**, não o que — especialmente onde a escolha
  óbvia estaria errada. Muitos trechos parecem estranhos até se saber o caso
  real que os motivou.

## Manter a documentação viva

Ao implementar algo novo ou corrigir bug não trivial, atualize:

- [docs/13-historico-de-pedidos.md](docs/13-historico-de-pedidos.md) — sempre
  (pedido → decisão → motivo);
- o doc da área afetada (10 importação/batimento, 11 catálogo, 08 emails);
- [docs/01-modelo-de-dados.md](docs/01-modelo-de-dados.md) se o schema mudar;
- [docs/12](docs/12-armadilhas-e-ambiente.md) se descobrir uma armadilha nova.

`docs/07` e `docs/09` são **escritos pelo próprio app** — não editar à mão.
