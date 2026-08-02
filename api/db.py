import sqlite3
import os
import csv
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'dev.sqlite')
SEED_DIR = os.path.join(BASE_DIR, 'seed')

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS categoria (
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  nome TEXT NOT NULL UNIQUE,
  ativo INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS despesa (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  nome                 TEXT NOT NULL UNIQUE,
  categoria_id         INTEGER REFERENCES categoria(id),
  dia_vencimento       INTEGER,
  tipo_valor           TEXT NOT NULL DEFAULT 'variavel',
  padrao_variabilidade TEXT NOT NULL DEFAULT 'variavel_nao_sazonal',
  valor_padrao         REAL,
  regras_match         TEXT NOT NULL DEFAULT '{"palavras_chave":[],"faixa_valor":null,"janela_dias":5,"banco":null}',
  ativo                INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS importacao (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  banco       TEXT,
  formato     TEXT NOT NULL,
  arquivo     TEXT NOT NULL,
  data_import TEXT NOT NULL DEFAULT (datetime('now')),
  periodo_ini TEXT,
  periodo_fim TEXT
);

CREATE TABLE IF NOT EXISTS transacao (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  data          TEXT NOT NULL,
  descricao     TEXT NOT NULL,
  valor         REAL NOT NULL,
  tipo          TEXT NOT NULL,
  situacao      TEXT NOT NULL DEFAULT 'efetivada',
  banco_origem  TEXT,
  classificacao TEXT NOT NULL DEFAULT 'extra',
  despesa_id    INTEGER REFERENCES despesa(id),
  import_id     INTEGER NOT NULL REFERENCES importacao(id)
);

CREATE TABLE IF NOT EXISTS lancamento (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  mes_ref         TEXT NOT NULL,
  despesa_id      INTEGER NOT NULL REFERENCES despesa(id),
  valor_esperado  REAL NOT NULL DEFAULT 0,
  status          TEXT NOT NULL DEFAULT 'nao_encontrado',
  transacao_id    INTEGER REFERENCES transacao(id),
  valor_real      REAL,
  data_pagamento  TEXT,
  linha_digitavel TEXT,
  UNIQUE(mes_ref, despesa_id)
);

CREATE TABLE IF NOT EXISTS receita (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  mes_ref TEXT NOT NULL,
  tipo    TEXT NOT NULL,
  valor   REAL NOT NULL,
  origem  TEXT
);

CREATE TABLE IF NOT EXISTS posicao_investimento (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  mes_ref            TEXT NOT NULL,
  banco              TEXT NOT NULL,
  produto            TEXT NOT NULL,
  classe             TEXT NOT NULL,
  valor              REAL NOT NULL,
  valor_mes_anterior REAL,
  rentabilidade      REAL
);

CREATE TABLE IF NOT EXISTS email_despesa_regra (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  remetente  TEXT NOT NULL UNIQUE,
  despesa_id INTEGER NOT NULL REFERENCES despesa(id),
  criado_em  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS config (
  chave TEXT PRIMARY KEY,
  valor TEXT NOT NULL
);

INSERT OR IGNORE INTO config VALUES ('reserva_desejada', '5000');
INSERT OR IGNORE INTO config VALUES ('saldo_conta', '0');
INSERT OR IGNORE INTO config VALUES ('dia_recebimento_salario', '27');
"""

PADRAO_MAP = {
    'reajuste anual': 'reajuste_anual',
    'variavel sazonal': 'variavel_sazonal',
    'variavel nao-sazonal': 'variavel_nao_sazonal',
    'nao-sazonal': 'variavel_nao_sazonal',
    'fixa': 'fixa',
    'anual': 'anual',
    'sem dados': 'sem_dados',
}

CATEGORIAS = [
    'Financiamento imóvel', 'Casa/Utilidades', 'Saúde',
    'Cartões', 'Filhos/Educação', 'Funcionária', 'Outros'
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    # migração: bancos criados antes de linha_digitavel existir
    colunas = [r[1] for r in conn.execute('PRAGMA table_info(lancamento)').fetchall()]
    if 'linha_digitavel' not in colunas:
        conn.execute('ALTER TABLE lancamento ADD COLUMN linha_digitavel TEXT')

    conn.commit()
    conn.close()
    print(f'[db] initialized at {DB_PATH}')


def get_config_value(conn, chave: str, default: str) -> str:
    row = conn.execute('SELECT valor FROM config WHERE chave=?', (chave,)).fetchone()
    return row['valor'] if row else default


def periodo_competencia(mes_ref: str, dia_corte: int):
    """Converte um mes_ref (competência) no intervalo real de datas do extrato.

    As despesas de um mês começam a ser pagas a partir do dia em que o
    salário cai (ex: dia 27 do mês anterior), não no dia 1 do próprio mês.
    Então mes_ref='2026-08' com dia_corte=27 cobre 2026-07-27 a 2026-08-26.
    """
    import calendar
    from datetime import date, timedelta

    ano, mes = (int(x) for x in mes_ref.split('-'))
    if mes == 1:
        ano_ant, mes_ant = ano - 1, 12
    else:
        ano_ant, mes_ant = ano, mes - 1

    dia_ini = min(dia_corte, calendar.monthrange(ano_ant, mes_ant)[1])
    ini = date(ano_ant, mes_ant, dia_ini)

    # fim = véspera do próximo corte (o corte de dia_corte dentro do próprio
    # mes_ref) — evita construir uma data com dia 0 quando dia_corte=1
    dia_corte_no_mes = min(dia_corte, calendar.monthrange(ano, mes)[1])
    fim = date(ano, mes, dia_corte_no_mes) - timedelta(days=1)

    return ini.isoformat(), fim.isoformat()


def parse_number(s: str) -> float:
    """Parse both US (1,234.56) and BR (1.234,56) number formats."""
    if not s:
        return 0.0
    s = s.strip().strip('"').replace(' ', '').replace('R$', '')
    if not s:
        return 0.0
    try:
        comma = s.rfind(',')
        period = s.rfind('.')
        if comma == -1 and period == -1:
            return float(s)
        if comma == -1:
            return float(s)
        if period == -1:
            return float(s.replace(',', '.'))
        if comma > period:          # 1.234,56 — Brazilian
            return float(s.replace('.', '').replace(',', '.'))
        else:                       # 1,234.56 — US/international
            return float(s.replace(',', ''))
    except ValueError:
        return 0.0

# Keep alias for backwards compat inside this file
parse_br_number = parse_number


def resolve_categoria(nome: str, cat_map: dict) -> int:
    n = nome.lower()
    if 'financ' in n:          return cat_map.get('Financiamento imóvel', 7)
    if 'saúde' in n or 'saude' in n: return cat_map.get('Saúde', 7)
    if 'cart' in n:            return cat_map.get('Cartões', 7)
    if 'filhos' in n or 'educ' in n: return cat_map.get('Filhos/Educação', 7)
    if 'func' in n:            return cat_map.get('Funcionária', 7)
    if 'casa' in n or 'util' in n:   return cat_map.get('Casa/Utilidades', 7)
    return cat_map.get('Outros', 7)


def seed_db():
    conn = get_db()
    n = conn.execute('SELECT COUNT(*) FROM despesa').fetchone()[0]
    if n > 0:
        conn.close()
        print('[seed] already seeded')
        return

    # Categorias
    for cat in CATEGORIAS:
        conn.execute('INSERT OR IGNORE INTO categoria (nome) VALUES (?)', (cat,))
    conn.commit()

    cat_map = {row['nome']: row['id'] for row in conn.execute('SELECT id, nome FROM categoria').fetchall()}

    # Catálogo
    catalog_path = os.path.join(SEED_DIR, 'catalogo_despesas.csv')
    despesa_map = {}
    with open(catalog_path, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            padrao_raw = (row.get('padrao') or '').lower().strip()
            padrao = PADRAO_MAP.get(padrao_raw, 'variavel_nao_sazonal')
            valor = parse_br_number(row.get('previsao_jul26') or row.get('media') or '0')
            cat_id = resolve_categoria(row.get('categoria_sugerida', ''), cat_map)
            keywords = [w for w in re.split(r'[\s/,()\[\]]+', row['conta_norm'].lower()) if len(w) >= 3]
            import json
            regras = json.dumps({'palavras_chave': keywords, 'faixa_valor': None, 'janela_dias': 5, 'banco': None})

            conn.execute(
                'INSERT OR IGNORE INTO despesa (nome, categoria_id, padrao_variabilidade, valor_padrao, regras_match) VALUES (?,?,?,?,?)',
                (row['conta_norm'], cat_id, padrao, valor, regras)
            )
    conn.commit()

    for row in conn.execute('SELECT id, nome FROM despesa').fetchall():
        despesa_map[row['nome']] = row['id']

    # Base histórica
    hist_path = os.path.join(SEED_DIR, 'base_historica.csv')
    with open(hist_path, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    batch = []
    for row in rows:
        did = despesa_map.get(row['conta_norm'])
        if not did:
            continue
        valor = parse_br_number(row.get('valor', '0'))
        batch.append((row['ano_mes'], did, valor))

    conn.executemany(
        'INSERT OR IGNORE INTO lancamento (mes_ref, despesa_id, valor_esperado, status) VALUES (?,?,?,"nao_encontrado")',
        batch
    )
    conn.commit()
    conn.close()
    print(f'[seed] {len(despesa_map)} despesas, {len(batch)} lançamentos históricos')
