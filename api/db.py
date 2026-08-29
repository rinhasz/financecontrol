import sqlite3
import os
import csv
import re
from contextlib import contextmanager

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
  -- 'fixa' gera lançamento previsto todo mês e cobra atenção quando não
  -- aparece no extrato; 'esporadica' não gera lançamento nenhum e só existe
  -- no mês em que uma transação for associada a ela (doc 14)
  recorrencia          TEXT NOT NULL DEFAULT 'fixa',
  -- eixo INDEPENDENTE da recorrência: pode acontecer mais de uma vez no mesmo
  -- mês? A escola cobra mensalidade e material (fixa, vários); o aluguel é um
  -- só (fixa, um). É este campo que decide se o item continua na lista de
  -- associação depois de já ter recebido uma transação.
  varios_por_mes       INTEGER NOT NULL DEFAULT 0,
  -- como estimar quanto vai custar no mês, a partir do histórico consolidado:
  -- media_simples | media_movel_6 | media_sazonal (ver api/projecao.py)
  tipo_projecao        TEXT NOT NULL DEFAULT 'media_movel_6',
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

-- Catálogo de receitas: espelho de `despesa` para o lado da entrada (doc 14).
-- `tipo` é o que decide se aquilo conta como renda: resgate de investimento e
-- estorno entram na conta como dinheiro que chega, mas não são renda nova.
CREATE TABLE IF NOT EXISTS receita (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  nome                 TEXT NOT NULL UNIQUE,
  categoria_id         INTEGER REFERENCES categoria(id),
  dia_recebimento      INTEGER,
  tipo                 TEXT NOT NULL DEFAULT 'outra',
  tipo_valor           TEXT NOT NULL DEFAULT 'variavel',
  padrao_variabilidade TEXT NOT NULL DEFAULT 'variavel_nao_sazonal',
  valor_padrao         REAL,
  regras_match         TEXT NOT NULL DEFAULT '{"palavras_chave":[],"faixa_valor":null,"janela_dias":5,"banco":null}',
  recorrencia          TEXT NOT NULL DEFAULT 'fixa',
  varios_por_mes       INTEGER NOT NULL DEFAULT 0,
  tipo_projecao        TEXT NOT NULL DEFAULT 'media_movel_6',
  ativo                INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS lancamento_receita (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  mes_ref          TEXT NOT NULL,
  receita_id       INTEGER NOT NULL REFERENCES receita(id),
  valor_esperado   REAL NOT NULL DEFAULT 0,
  status           TEXT NOT NULL DEFAULT 'nao_encontrado',
  transacao_id     INTEGER REFERENCES transacao(id),
  valor_real       REAL,
  data_recebimento TEXT,
  UNIQUE(mes_ref, receita_id)
);

CREATE TABLE IF NOT EXISTS transacao_receita_regra (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  padrao     TEXT NOT NULL,
  receita_id INTEGER NOT NULL REFERENCES receita(id),
  acertos    INTEGER NOT NULL DEFAULT 1,
  criado_em  TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(padrao, receita_id)
);

-- Posição de investimentos (doc 16). Uma linha por produto por foto: cada
-- importação é uma `carga`, e a posição de uma data é substituída por completo
-- quando reimportada — mesma regra do extrato bancário.
--
-- Os saldos vêm em quatro colunas porque os dois regimes convivem: produto de
-- emissão (LCI/LCA/LIG/CDB Itaú) rende por accrual e o banco informa o saldo
-- acumulado; produto de corretora é marcado a mercado (MTM). Guardar tudo no
-- mesmo campo perderia a distinção, que é justamente o que decide qual resgatar.
CREATE TABLE IF NOT EXISTS carga_investimento (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  data_posicao TEXT NOT NULL,
  arquivo      TEXT,
  origens      TEXT,
  criado_em    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS investimento (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  data_posicao          TEXT NOT NULL,
  origem                TEXT NOT NULL,   -- emissao_itau | acoes | rf_corretora
  produto               TEXT NOT NULL,   -- LCI | LCA | LIG | CDB | ACAO | CRA | CRI | DEB
  ativo                 TEXT,            -- código/rótulo do papel, quando existe
  emissor               TEXT,
  indexador             TEXT,            -- DI | IPCA | PRE
  perc_indexador        REAL,            -- 94.0 (% do DI)
  taxa                  REAL,            -- 13.24 (% a.a.)
  data_aplicacao        TEXT,
  data_vencimento       TEXT,
  data_liquidez         TEXT,
  pu                    REAL NOT NULL DEFAULT 1,
  quantidade            REAL,
  valor_aplicacao       REAL,
  saldo_bruto_accrual   REAL,
  saldo_liquido_accrual REAL,
  saldo_bruto_mtm       REAL,
  saldo_liquido_mtm     REAL,
  -- resultado da valorização diária (fase 2): até quando foi atualizado, com
  -- que método, e o porquê quando não deu para atualizar
  data_valorizacao      TEXT,
  pu_valorizado         REAL,
  saldo_valorizado      REAL,
  metodo_valorizacao    TEXT,
  detalhe_valorizacao   TEXT,
  carga_id              INTEGER REFERENCES carga_investimento(id)
);

-- Dados de mercado, apartados do cálculo de propósito (doc 16 fase 2): com o
-- dado bruto de cada dia guardado, a valorização pode ser refeita sem depender
-- de a fonte estar no ar, e o número da tela pode ser auditado.
CREATE TABLE IF NOT EXISTS mercado_serie (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  serie     TEXT NOT NULL,   -- DI | IPCA | ACAO:<ticker> | DEB:<codigo>
  data      TEXT NOT NULL,
  valor     REAL NOT NULL,
  fonte     TEXT,
  criado_em TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(serie, data)
);

-- Memória de cálculo: uma linha por papel por dia útil. É o que permite
-- reconstruir como se chegou ao saldo de hoje.
CREATE TABLE IF NOT EXISTS valorizacao (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  investimento_id INTEGER NOT NULL REFERENCES investimento(id),
  data            TEXT NOT NULL,
  pu_anterior     REAL,
  fator           REAL,
  pu              REAL,
  saldo           REAL,
  metodo          TEXT,
  detalhe         TEXT
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

-- Aprendizado do batimento: "esse texto do extrato é essa despesa".
-- Equivalente, para extrato bancário, do que email_despesa_regra já fazia
-- para email. Sem isto o usuário corrigia os mesmos erros todo mês: as
-- correções iam só para docs/07 (arquivo legível) e nada as lia de volta.
-- A chave é o PADRÃO da descrição (ver padrao_descricao), não o texto
-- literal, porque data e número de documento mudam a cada mês.
CREATE TABLE IF NOT EXISTS transacao_despesa_regra (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  padrao     TEXT NOT NULL,
  despesa_id INTEGER NOT NULL REFERENCES despesa(id),
  acertos    INTEGER NOT NULL DEFAULT 1,
  criado_em  TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(padrao, despesa_id)
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
INSERT OR IGNORE INTO config VALUES ('dia_recebimento_salario', '26');
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


@contextmanager
def db():
    """Conexão que fecha mesmo se der exceção no meio.

    Sem isto, qualquer erro entre `get_db()` e `close()` deixa a conexão aberta
    segurando o lock de escrita — e **todas** as chamadas seguintes falham com
    "database is locked" até o app ser reiniciado. Aconteceu de verdade: tentar
    criar uma receita com nome que já existia derrubava o app inteiro.

    Faz commit ao sair normalmente; exceção propaga sem gravar nada.
    """
    conn = get_db()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrar_receita_para_catalogo(conn) -> None:
    """Troca a tabela `receita` do formato antigo (mes_ref/tipo/valor/origem)
    pelo catálogo do doc 14.

    O formato antigo era um resquício do desenho original da Função 4: uma
    linha por receita por mês, sem catálogo e sem tela. Nunca foi usado.

    `CREATE TABLE IF NOT EXISTS` não converte tabela existente, então a troca é
    explícita — e **só acontece se a tabela estiver vazia**. Havendo qualquer
    linha, aborta com erro em vez de destruir dado: este código roda no banco
    real do usuário, e a premissa "está vazia" foi verificada num banco
    específico, não é garantia universal.
    """
    colunas = [r[1] for r in conn.execute('PRAGMA table_info(receita)').fetchall()]
    if not colunas or 'mes_ref' not in colunas:
        return  # já é o catálogo novo (ou a tabela ainda nem existe)

    n = conn.execute('SELECT COUNT(*) FROM receita').fetchone()[0]
    if n:
        raise RuntimeError(
            f'A tabela `receita` tem {n} linhas no formato antigo e a migração do '
            'doc 14 as apagaria. Migre esses dados à mão antes de continuar.'
        )

    conn.execute('DROP TABLE receita')
    conn.executescript(SCHEMA)  # recria no formato novo
    print('[db] tabela `receita` (vazia) recriada como catálogo — doc 14')


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    _migrar_receita_para_catalogo(conn)

    # migração: bancos criados antes de linha_digitavel existir
    colunas = [r[1] for r in conn.execute('PRAGMA table_info(lancamento)').fetchall()]
    if 'linha_digitavel' not in colunas:
        conn.execute('ALTER TABLE lancamento ADD COLUMN linha_digitavel TEXT')

    # migração: distingue código de boleto de código Pix "copia e cola" —
    # ambos ficam guardados em linha_digitavel, este campo só marca qual é
    if 'tipo_codigo' not in colunas:
        conn.execute('ALTER TABLE lancamento ADD COLUMN tipo_codigo TEXT')

    # migração: recorrência (doc 14). O padrão 'fixa' preserva exatamente o
    # comportamento de todas as despesas já cadastradas — só o que o usuário
    # marcar como esporádica muda de vida.
    colunas_despesa = [r[1] for r in conn.execute('PRAGMA table_info(despesa)').fetchall()]
    if 'recorrencia' not in colunas_despesa:
        conn.execute("ALTER TABLE despesa ADD COLUMN recorrencia TEXT NOT NULL DEFAULT 'fixa'")

    # migração: "aceita mais de um por mês" deixa de ser deduzido da
    # recorrência e vira campo próprio. O valor inicial reproduz exatamente o
    # comportamento anterior (esporádica aceitava vários; fixa, um só), então
    # nada muda até o usuário marcar.
    for tabela in ('despesa', 'receita'):
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info({tabela})').fetchall()]
        if cols and 'varios_por_mes' not in cols:
            conn.execute(f'ALTER TABLE {tabela} ADD COLUMN varios_por_mes INTEGER NOT NULL DEFAULT 0')
            conn.execute(f"UPDATE {tabela} SET varios_por_mes=1 WHERE recorrencia='esporadica'")

        # migração: tipo de projeção. O valor inicial é derivado do
        # `padrao_variabilidade` que já estava cadastrado — o que se sabia
        # sobre o comportamento de cada despesa vira o método de estimativa,
        # em vez de todo mundo começar no mesmo padrão arbitrário.
        if cols and 'tipo_projecao' not in cols:
            from .projecao import PADRAO_PARA_PROJECAO, MEDIA_MOVEL_6
            conn.execute(f"ALTER TABLE {tabela} ADD COLUMN tipo_projecao TEXT NOT NULL DEFAULT '{MEDIA_MOVEL_6}'")
            for padrao, projecao in PADRAO_PARA_PROJECAO.items():
                conn.execute(f'UPDATE {tabela} SET tipo_projecao=? WHERE padrao_variabilidade=?',
                             (projecao, padrao))

    # migração: lado da receita na transação (doc 14). Criadas já na fase 2,
    # junto com as tabelas, para a fase 3 não precisar de outra migração e não
    # existir banco meio migrado.
    colunas_transacao = [r[1] for r in conn.execute('PRAGMA table_info(transacao)').fetchall()]
    if 'receita_id' not in colunas_transacao:
        conn.execute('ALTER TABLE transacao ADD COLUMN receita_id INTEGER REFERENCES receita(id)')
    if 'objetivo' not in colunas_transacao:
        # rótulo do resgate esporádico ("compra do carro") — por ocorrência,
        # não por item de catálogo, porque muda a cada resgate
        conn.execute('ALTER TABLE transacao ADD COLUMN objetivo TEXT')
    if 'estorna_transacao_id' not in colunas_transacao:
        conn.execute('ALTER TABLE transacao ADD COLUMN estorna_transacao_id INTEGER REFERENCES transacao(id)')
    if 'estorna_despesa_id' not in colunas_transacao:
        # O vínculo que interessa a longo prazo é estorno -> DESPESA, não
        # estorno -> linha do extrato. Quem estorna anula um gasto, e é contra o
        # gasto que o valor se compensa. A linha do extrato é só o caminho mais
        # curto quando ela ainda não foi classificada; assim que for, este campo
        # é preenchido por propagação e passa a ser a fonte de verdade.
        conn.execute('ALTER TABLE transacao ADD COLUMN estorna_despesa_id INTEGER REFERENCES despesa(id)')

    # migração: valorização diária (doc 16 fase 2) numa tabela `investimento`
    # criada na fase 1 — CREATE TABLE IF NOT EXISTS não acrescenta coluna
    cols_inv = [r[1] for r in conn.execute('PRAGMA table_info(investimento)').fetchall()]
    if cols_inv:
        for coluna, tipo in (('data_valorizacao', 'TEXT'), ('pu_valorizado', 'REAL'),
                             ('saldo_valorizado', 'REAL'), ('metodo_valorizacao', 'TEXT'),
                             ('detalhe_valorizacao', 'TEXT')):
            if coluna not in cols_inv:
                conn.execute(f'ALTER TABLE investimento ADD COLUMN {coluna} {tipo}')

    conn.commit()
    _seed_regras_transacao(conn)
    conn.close()
    print(f'[db] initialized at {DB_PATH}')


def _seed_regras_transacao(conn):
    """Migra para a tabela as correções que já existiam só em docs/07.

    Até aqui as correções do usuário eram gravadas apenas naquele arquivo
    (legível, mas nunca lido de volta pelo app). Esta semeadura roda uma
    única vez, quando a tabela ainda está vazia, para esse histórico não se
    perder — daí em diante quem alimenta é a confirmação do batimento.
    """
    if conn.execute('SELECT COUNT(*) FROM transacao_despesa_regra').fetchone()[0]:
        return

    caminho = os.path.join(BASE_DIR, 'docs', '07-dicionario-despesas.md')
    if not os.path.exists(caminho):
        return

    despesas = {r[1].strip().lower(): r[0] for r in conn.execute('SELECT id, nome FROM despesa')}
    n = 0
    with open(caminho, encoding='utf-8') as f:
        for linha in f:
            m = re.match(r'\|\s*`(.+?)`\s*\|\s*(.+?)\s*\|', linha)
            if not m:
                continue
            despesa_id = despesas.get(m.group(2).strip().lower())
            if despesa_id:
                registrar_regra_transacao(conn, m.group(1).strip(), despesa_id)
                n += 1
    conn.commit()
    if n:
        print(f'[db] {n} regras de batimento semeadas a partir do dicionário')


def padrao_descricao(descricao: str) -> str:
    """Reduz a descrição do extrato ao que se repete mês a mês.

    A mesma despesa vem com texto ligeiramente diferente todo mês, porque a
    descrição carrega data e número de documento:
        'PIX TRANSF MARIA J28/07'  e  'PIX TRANSF MARIA J01/08'
        'INT IPTU02102204944'
    Guardar a regra pelo texto literal só acertaria no mês em que foi
    aprendida. Removendo data e sequências longas de dígitos sobra a parte
    estável ('pix transf maria j', 'int iptu'), que é o que identifica a
    despesa.

    O que NÃO se remove: letras coladas ao número ('maria j' vs 'maria l',
    'claro s.a.' vs 'claro bl/it') — são justamente o que distingue duas
    despesas parecidas.
    """
    import unicodedata
    s = unicodedata.normalize('NFKD', str(descricao or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r'\d{1,2}/\d{1,2}(?:/\d{2,4})?', ' ', s)   # datas
    s = re.sub(r'\d{4,}', ' ', s)                          # documento/conta
    s = re.sub(r'[^a-z0-9./-]+', ' ', s)
    # sobra do passo acima: pedaços sem letra nenhuma ('.', '-', '.072').
    # Só entram tokens que tenham ao menos uma letra — é a letra que
    # identifica a despesa; dígito solto é resto de número de documento.
    tokens = [t.strip('./-') for t in s.split()]
    return ' '.join(t for t in tokens if t and re.search(r'[a-z]', t))


def registrar_regra(conn, tabela: str, fk: str, descricao: str, item_id: int):
    """Grava (ou reforça) 'esse padrão de descrição é esse item'.

    `tabela`/`fk` vêm de motor_batimento.NATUREZAS — o mesmo aprendizado vale
    para despesa e receita, mudando só onde é guardado.
    """
    padrao = padrao_descricao(descricao)
    if not padrao or not item_id:
        return
    conn.execute(
        f'INSERT INTO {tabela} (padrao, {fk}) VALUES (?,?) '
        f'ON CONFLICT(padrao, {fk}) DO UPDATE SET acertos = acertos + 1',
        (padrao, item_id)
    )


def registrar_regra_transacao(conn, descricao: str, despesa_id: int):
    """Atalho para o lado da despesa (usado pela semeadura do dicionário)."""
    registrar_regra(conn, 'transacao_despesa_regra', 'despesa_id', descricao, despesa_id)


def get_config_value(conn, chave: str, default: str) -> str:
    row = conn.execute('SELECT valor FROM config WHERE chave=?', (chave,)).fetchone()
    return row['valor'] if row else default


def _pascoa(ano: int):
    """Domingo de Páscoa (algoritmo gregoriano anônimo). Base dos feriados
    móveis: Carnaval, Sexta-feira Santa e Corpus Christi."""
    from datetime import date
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * l) // 433
    mes = (h + l - 7 * m + 90) // 25
    dia = (h + l - 7 * m + 33 * mes + 19) % 32
    return date(ano, mes, dia)


def feriados_bancarios(ano: int) -> set:
    """Dias em que banco não credita: feriados nacionais + os móveis em que o
    sistema bancário não opera (Carnaval e Corpus Christi são ponto facultativo,
    mas o banco fecha, que é o que importa aqui).

    Feriado municipal não entra — varia por cidade e o app não sabe onde o
    usuário está. Se o salário cair num feriado só da cidade, o ajuste erra por
    um dia; o `dia_recebimento_salario` continua editável na tela.
    """
    from datetime import date, timedelta
    pascoa = _pascoa(ano)
    return {
        date(ano, 1, 1),    # Confraternização Universal
        date(ano, 4, 21),   # Tiradentes
        date(ano, 5, 1),    # Dia do Trabalho
        date(ano, 9, 7),    # Independência
        date(ano, 10, 12),  # Nossa Senhora Aparecida
        date(ano, 11, 2),   # Finados
        date(ano, 11, 15),  # Proclamação da República
        date(ano, 12, 25),  # Natal
        pascoa - timedelta(days=48),  # Carnaval (segunda)
        pascoa - timedelta(days=47),  # Carnaval (terça)
        pascoa - timedelta(days=2),   # Sexta-feira Santa
        pascoa + timedelta(days=60),  # Corpus Christi
    }


def dia_util_anterior(d):
    """Recua até o primeiro dia útil <= d.

    Salário que cairia em sábado, domingo ou feriado é creditado **antes**, no
    último dia útil — e é a data do crédito que abre a competência.
    """
    from datetime import timedelta
    feriados = feriados_bancarios(d.year) | feriados_bancarios(d.year - 1)
    while d.weekday() >= 5 or d in feriados:
        d -= timedelta(days=1)
    return d


def periodo_competencia(mes_ref: str, dia_corte: int):
    """Converte um mes_ref (competência) no intervalo real de datas do extrato.

    As despesas de um mês começam a ser pagas quando o salário cai — dia 26 do
    mês anterior, ou **antes** se o 26 for fim de semana ou feriado, porque o
    banco antecipa o crédito. Então `mes_ref='2026-08'` com `dia_corte=26`
    normalmente cobre 2026-07-26 a 2026-08-25.

    O fim é sempre a **véspera do próximo crédito**, não um dia fixo: se o 26 do
    mês seguinte for antecipado para o 24, esta competência acaba no 23. É o que
    faz os meses se encaixarem sem buraco nem sobreposição.
    """
    import calendar
    from datetime import date, timedelta

    ano, mes = (int(x) for x in mes_ref.split('-'))
    if mes == 1:
        ano_ant, mes_ant = ano - 1, 12
    else:
        ano_ant, mes_ant = ano, mes - 1

    # min(): mês com menos dias que o corte (fevereiro com dia_corte=30)
    ini = dia_util_anterior(
        date(ano_ant, mes_ant, min(dia_corte, calendar.monthrange(ano_ant, mes_ant)[1])))

    proximo = dia_util_anterior(
        date(ano, mes, min(dia_corte, calendar.monthrange(ano, mes)[1])))
    fim = proximo - timedelta(days=1)

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
