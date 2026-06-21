PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS categoria (
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  nome TEXT    NOT NULL UNIQUE,
  ativo INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS despesa (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  nome                 TEXT    NOT NULL UNIQUE,
  categoria_id         INTEGER REFERENCES categoria(id),
  dia_vencimento       INTEGER,
  tipo_valor           TEXT    NOT NULL DEFAULT 'variavel' CHECK(tipo_valor IN ('fixo','variavel')),
  padrao_variabilidade TEXT    NOT NULL DEFAULT 'variavel_nao_sazonal'
                                CHECK(padrao_variabilidade IN ('fixa','variavel_sazonal','variavel_nao_sazonal','reajuste_anual','anual','sem_dados')),
  valor_padrao         REAL,
  regras_match         TEXT    NOT NULL DEFAULT '{"palavras_chave":[],"faixa_valor":null,"janela_dias":5,"banco":null}',
  ativo                INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS importacao (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  banco       TEXT,
  formato     TEXT NOT NULL CHECK(formato IN ('ofx','csv','pdf')),
  arquivo     TEXT NOT NULL,
  data_import TEXT NOT NULL DEFAULT (datetime('now')),
  periodo_ini TEXT,
  periodo_fim TEXT
);

CREATE TABLE IF NOT EXISTS transacao (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  data         TEXT    NOT NULL,
  descricao    TEXT    NOT NULL,
  valor        REAL    NOT NULL,
  tipo         TEXT    NOT NULL CHECK(tipo IN ('debito','credito')),
  situacao     TEXT    NOT NULL DEFAULT 'efetivada' CHECK(situacao IN ('efetivada','agendada')),
  banco_origem TEXT,
  classificacao TEXT   NOT NULL DEFAULT 'extra'
                       CHECK(classificacao IN ('recorrente','extra','receita','transferencia','investimento')),
  despesa_id   INTEGER REFERENCES despesa(id),
  import_id    INTEGER NOT NULL REFERENCES importacao(id)
);

CREATE TABLE IF NOT EXISTS lancamento (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  mes_ref        TEXT    NOT NULL,
  despesa_id     INTEGER NOT NULL REFERENCES despesa(id),
  valor_esperado REAL    NOT NULL DEFAULT 0,
  status         TEXT    NOT NULL DEFAULT 'nao_encontrado'
                         CHECK(status IN ('pago','agendado','nao_encontrado')),
  transacao_id   INTEGER REFERENCES transacao(id),
  valor_real     REAL,
  data_pagamento TEXT,
  UNIQUE(mes_ref, despesa_id)
);

CREATE TABLE IF NOT EXISTS receita (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  mes_ref TEXT    NOT NULL,
  tipo    TEXT    NOT NULL CHECK(tipo IN ('salario','juros','outro')),
  valor   REAL    NOT NULL,
  origem  TEXT
);

CREATE TABLE IF NOT EXISTS posicao_investimento (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  mes_ref            TEXT    NOT NULL,
  banco              TEXT    NOT NULL,
  produto            TEXT    NOT NULL,
  classe             TEXT    NOT NULL CHECK(classe IN ('RF','RV','Prev','FGTS','Outro')),
  valor              REAL    NOT NULL,
  valor_mes_anterior REAL,
  rentabilidade      REAL
);

CREATE TABLE IF NOT EXISTS config (
  chave TEXT PRIMARY KEY,
  valor TEXT NOT NULL
);

INSERT OR IGNORE INTO config VALUES ('reserva_desejada', '5000');
INSERT OR IGNORE INTO config VALUES ('saldo_conta', '0');
