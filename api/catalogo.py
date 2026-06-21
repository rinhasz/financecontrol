from flask import Blueprint, jsonify, request
from .db import get_db

bp = Blueprint('catalogo', __name__)


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
    conn = get_db()
    if data.get('id'):
        conn.execute("""
            UPDATE despesa SET nome=?, categoria_id=?, dia_vencimento=?, tipo_valor=?,
            padrao_variabilidade=?, valor_padrao=?, regras_match=?, ativo=? WHERE id=?
        """, (data['nome'], data.get('categoria_id'), data.get('dia_vencimento'),
              data.get('tipo_valor', 'variavel'), data.get('padrao_variabilidade', 'variavel_nao_sazonal'),
              data.get('valor_padrao'), data.get('regras_match'), data.get('ativo', 1), data['id']))
    else:
        conn.execute("""
            INSERT INTO despesa (nome, categoria_id, dia_vencimento, tipo_valor, padrao_variabilidade, valor_padrao, regras_match)
            VALUES (?,?,?,?,?,?,?)
        """, (data['nome'], data.get('categoria_id'), data.get('dia_vencimento'),
              data.get('tipo_valor', 'variavel'), data.get('padrao_variabilidade', 'variavel_nao_sazonal'),
              data.get('valor_padrao'),
              data.get('regras_match', '{"palavras_chave":[],"faixa_valor":null,"janela_dias":5,"banco":null}')))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


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
