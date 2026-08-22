import json
import re

from flask import Blueprint, jsonify, request

import sqlite3

from .db import db, get_db, parse_number
from .importacao import normalize_text, _sheet_rows_xls, _sheet_rows_xlsx

bp = Blueprint('catalogo', __name__)


def _norm_nome(s) -> str:
    return re.sub(r'\s+', ' ', normalize_text(str(s or ''))).strip()


@bp.route('/catalogo')
def list_catalogo():
    conn = get_db()
    rows = conn.execute("""
        SELECT d.*, c.nome as categoria_nome
        FROM despesa d LEFT JOIN categoria c ON c.id = d.categoria_id
        ORDER BY c.nome, d.nome
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/catalogo', methods=['POST'])
def upsert_catalogo():
    data = request.json or {}
    nome = (data.get('nome') or '').strip()
    if not nome:
        return jsonify({'ok': False, 'msg': 'Nome é obrigatório'}), 400
    try:
        return _gravar_despesa(data, nome)
    except sqlite3.IntegrityError:
        # nome é UNIQUE: erro esperado, não falha de sistema. Sem este tratamento
        # a conexão vazava e o app inteiro travava (ver doc 12).
        return jsonify({'ok': False, 'msg': f'Já existe uma despesa chamada "{nome}"'}), 409


def _gravar_despesa(data, nome):
    with db() as conn:
        return _gravar(conn, data)


def _gravar(conn, data):
    if data.get('id'):
        conn.execute("""
            UPDATE despesa SET nome=?, categoria_id=?, dia_vencimento=?, tipo_valor=?,
            padrao_variabilidade=?, valor_padrao=?, regras_match=?, recorrencia=?, varios_por_mes=?, ativo=? WHERE id=?
        """, (data['nome'], data.get('categoria_id'), data.get('dia_vencimento'),
              data.get('tipo_valor', 'variavel'), data.get('padrao_variabilidade', 'variavel_nao_sazonal'),
              data.get('valor_padrao'), data.get('regras_match'),
              data.get('recorrencia', 'fixa'), 1 if data.get('varios_por_mes') else 0,
              data.get('ativo', 1), data['id']))
        despesa_id = data['id']
    else:
        cur = conn.execute("""
            INSERT INTO despesa (nome, categoria_id, dia_vencimento, tipo_valor, padrao_variabilidade, valor_padrao, recorrencia, varios_por_mes, regras_match)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (data['nome'], data.get('categoria_id'), data.get('dia_vencimento'),
              data.get('tipo_valor', 'variavel'), data.get('padrao_variabilidade', 'variavel_nao_sazonal'),
              data.get('valor_padrao'), data.get('recorrencia', 'fixa'),
              1 if data.get('varios_por_mes') else 0,
              data.get('regras_match', '{"palavras_chave":[],"faixa_valor":null,"janela_dias":5,"banco":null}')))
        despesa_id = cur.lastrowid

    # Virar esporádica não apaga lançamento nenhum, de propósito: os
    # lançamentos antigos são a série histórica que alimenta a previsão de
    # valor (`_valor_previsto`), e vários deles são seed sem transação. Quem
    # esconde a previsão vazia é o filtro da consulta em /api/lancamentos.
    return jsonify({'ok': True, 'id': despesa_id})


@bp.route('/catalogo/<int:did>/toggle', methods=['POST'])
def toggle_ativo(did):
    conn = get_db()
    conn.execute('UPDATE despesa SET ativo = 1 - ativo WHERE id=?', (did,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@bp.route('/categorias')
def list_categorias():
    conn = get_db()
    rows = conn.execute('SELECT * FROM categoria ORDER BY nome').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


def _ler_linhas(file, texto):
    """Lê linhas cruas de um .xls/.xlsx enviado OU de texto colado da área
    de transferência (TSV — é o formato que o Excel gera ao copiar células),
    como lista de linhas (sem assumir cabeçalho nem nomes de coluna)."""
    if file:
        filename = file.filename or 'planilha'
        ext = filename.rsplit('.', 1)[-1].lower()
        if ext not in ('xls', 'xlsx', 'xlsm'):
            return None
        content = file.read()
        sheets = _sheet_rows_xls(content) if ext == 'xls' else _sheet_rows_xlsx(content)
        return sheets[0] if sheets else []
    if texto and texto.strip():
        linhas = texto.replace('\r\n', '\n').replace('\r', '\n').strip('\n').split('\n')
        return [linha.split('\t') for linha in linhas]
    return None


@bp.route('/catalogo/importar/amostra', methods=['POST'])
def importar_catalogo_amostra():
    """Primeiro passo: mostra uma amostra crua da planilha (sem interpretar
    nada) pra o usuário indicar qual coluna é qual."""
    file = request.files.get('file')
    texto = request.form.get('texto')
    if not file and not texto:
        return jsonify({'ok': False, 'msg': 'Envie um arquivo ou cole os dados'}), 400

    rows = _ler_linhas(file, texto)
    if rows is None:
        return jsonify({'ok': False, 'msg': 'Envie um arquivo Excel (.xls ou .xlsx) ou cole os dados'}), 400
    if not rows:
        return jsonify({'ok': False, 'msg': 'Planilha vazia'}), 400

    amostra = rows[:10]
    n_colunas = max((len(r) for r in amostra), default=0)
    linhas = [
        [('' if c is None else c) for c in list(row) + [None] * (n_colunas - len(row))]
        for row in amostra
    ]
    return jsonify({'ok': True, 'colunas': n_colunas, 'linhas': linhas, 'total_linhas': len(rows)})


@bp.route('/catalogo/importar/analisar', methods=['POST'])
def importar_catalogo_analisar():
    """Segundo passo: com o mapeamento de colunas escolhido, monta o plano
    de mudanças (não grava nada ainda) pro usuário revisar."""
    file = request.files.get('file')
    texto = request.form.get('texto')
    if not file and not texto:
        return jsonify({'ok': False, 'msg': 'Envie um arquivo ou cole os dados'}), 400

    try:
        col_nome = int(request.form.get('col_nome'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'msg': 'Selecione a coluna do nome da despesa'}), 400
    col_categoria_raw = request.form.get('col_categoria', '')
    col_valor_raw = request.form.get('col_valor', '')
    col_categoria = int(col_categoria_raw) if col_categoria_raw != '' else None
    col_valor = int(col_valor_raw) if col_valor_raw != '' else None
    tem_cabecalho = request.form.get('tem_cabecalho') == 'true'

    rows = _ler_linhas(file, texto)
    if rows is None:
        return jsonify({'ok': False, 'msg': 'Envie um arquivo Excel (.xls ou .xlsx) ou cole os dados'}), 400
    if tem_cabecalho:
        rows = rows[1:]

    itens = {}
    for row in rows:
        if row is None or col_nome >= len(row) or row[col_nome] in (None, ''):
            continue
        nome_norm = _norm_nome(row[col_nome])
        if not nome_norm:
            continue

        categoria = None
        if col_categoria is not None and col_categoria < len(row) and row[col_categoria] not in (None, ''):
            categoria = str(row[col_categoria]).strip()

        valor = None
        if col_valor is not None and col_valor < len(row) and row[col_valor] not in (None, ''):
            bruto = row[col_valor]
            valor = float(bruto) if isinstance(bruto, (int, float)) else parse_number(str(bruto))
            if valor != valor:  # NaN
                valor = None

        itens[nome_norm] = {'nome': nome_norm, 'categoria': categoria, 'valor': valor}

    if not itens:
        return jsonify({'ok': False, 'msg': 'Nenhuma despesa encontrada nessa coluna — confira o mapeamento.'}), 400

    conn = get_db()
    despesas_existentes = conn.execute(
        'SELECT d.*, c.nome as categoria_nome FROM despesa d LEFT JOIN categoria c ON c.id=d.categoria_id'
    ).fetchall()
    categorias_existentes = {_norm_nome(r['nome']) for r in conn.execute('SELECT nome FROM categoria').fetchall()}
    conn.close()

    despesas_por_nome = {_norm_nome(d['nome']): d for d in despesas_existentes}

    novas = []
    atualizadas = []
    categorias_novas = set()

    for nome_norm, item in itens.items():
        if item['categoria'] and _norm_nome(item['categoria']) not in categorias_existentes:
            categorias_novas.add(item['categoria'])

        existente = despesas_por_nome.get(nome_norm)
        if existente:
            categoria_muda = bool(item['categoria']) and _norm_nome(item['categoria']) != _norm_nome(existente['categoria_nome'])
            valor_muda = item['valor'] is not None and (
                existente['valor_padrao'] is None or abs(existente['valor_padrao'] - item['valor']) > 0.01
            )
            if categoria_muda or valor_muda or not existente['ativo']:
                atualizadas.append({
                    'despesa_id': existente['id'],
                    'nome': existente['nome'],
                    'categoria_nome_antiga': existente['categoria_nome'],
                    'categoria_nome_nova': item['categoria'] if categoria_muda else None,
                    'valor_antigo': existente['valor_padrao'],
                    'valor_novo': item['valor'] if valor_muda else None,
                    'reativada': not existente['ativo'],
                })
        else:
            novas.append({'nome': nome_norm, 'categoria': item['categoria'], 'valor': item['valor']})

    desativadas = [
        {'despesa_id': d['id'], 'nome': d['nome']}
        for d in despesas_existentes
        if d['ativo'] and _norm_nome(d['nome']) not in itens
    ]

    return jsonify({
        'ok': True,
        'novas': novas,
        'atualizadas': atualizadas,
        'desativadas': desativadas,
        'categorias_novas': sorted(categorias_novas),
        'colunas_usadas': {'categoria': col_categoria is not None, 'valor': col_valor is not None},
    })


@bp.route('/catalogo/importar/confirmar', methods=['POST'])
def importar_catalogo_confirmar():
    """Terceiro passo: executa exatamente o plano que o usuário revisou."""
    data = request.json or {}
    novas = data.get('novas', [])
    atualizadas = data.get('atualizadas', [])
    desativadas = data.get('desativadas', [])
    categorias_novas = data.get('categorias_novas', [])

    conn = get_db()

    for nome_cat in categorias_novas:
        conn.execute('INSERT OR IGNORE INTO categoria (nome) VALUES (?)', (nome_cat,))
    conn.commit()

    categorias = {_norm_nome(r['nome']): r['id'] for r in conn.execute('SELECT * FROM categoria').fetchall()}

    for item in novas:
        categoria_id = categorias.get(_norm_nome(item['categoria'])) if item.get('categoria') else None
        keywords = [w for w in re.split(r'[\s/,()\[\]]+', item['nome']) if len(w) >= 3]
        regras = json.dumps({'palavras_chave': keywords, 'faixa_valor': None, 'janela_dias': 5, 'banco': None})
        conn.execute(
            'INSERT INTO despesa (nome, categoria_id, tipo_valor, padrao_variabilidade, valor_padrao, regras_match, ativo) '
            'VALUES (?,?,?,?,?,?,1)',
            (item['nome'], categoria_id, 'variavel', 'variavel_nao_sazonal', item.get('valor'), regras)
        )

    for item in atualizadas:
        campos = ['ativo=1']
        valores = []
        if item.get('categoria_nome_nova'):
            campos.append('categoria_id=?')
            valores.append(categorias.get(_norm_nome(item['categoria_nome_nova'])))
        if item.get('valor_novo') is not None:
            campos.append('valor_padrao=?')
            valores.append(item['valor_novo'])
        valores.append(item['despesa_id'])
        conn.execute(f"UPDATE despesa SET {', '.join(campos)} WHERE id=?", valores)

    for item in desativadas:
        conn.execute('UPDATE despesa SET ativo=0 WHERE id=?', (item['despesa_id'],))

    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'criadas': len(novas), 'atualizadas': len(atualizadas), 'desativadas': len(desativadas)})
