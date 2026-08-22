"""Catálogo de receitas — espelho de `catalogo.py` para o lado da entrada.

Ver doc 14. A diferença de fundo em relação às despesas é o campo `tipo`: é
ele que decide se aquilo conta como **renda**. Resgate de investimento e
estorno chegam na conta como dinheiro entrando, mas não são renda nova —
somá-los inflaria a receita do mês e destruiria o sentido da calculadora de
resgate (doc 04).
"""
from flask import Blueprint, jsonify, request

import sqlite3

from .db import db, get_db

bp = Blueprint('receitas', __name__)

# Tipos que NÃO são renda nova. `conta_como_renda` é derivado disto, e não um
# campo próprio: dois campos que precisam concordar acabam discordando.
TIPOS_SEM_RENDA = {'resgate_mensal', 'resgate_esporadico', 'estorno', 'transferencia'}

TIPOS = ['salario', 'juros', 'reembolso', 'outra',
         'resgate_mensal', 'resgate_esporadico', 'estorno', 'transferencia']

REGRAS_PADRAO = '{"palavras_chave":[],"faixa_valor":null,"janela_dias":5,"banco":null}'


def conta_como_renda(tipo: str) -> bool:
    return tipo not in TIPOS_SEM_RENDA


@bp.route('/receitas/catalogo')
def list_catalogo():
    conn = get_db()
    rows = conn.execute("""
        SELECT r.*, c.nome as categoria_nome
        FROM receita r LEFT JOIN categoria c ON c.id = r.categoria_id
        ORDER BY r.nome
    """).fetchall()
    conn.close()
    return jsonify([{**dict(r), 'conta_como_renda': conta_como_renda(r['tipo'])} for r in rows])


@bp.route('/receitas/tipos')
def list_tipos():
    """A tela precisa saber quais tipos existem e quais contam como renda —
    sem repetir a regra no frontend, onde ela sairia de sincronia."""
    return jsonify([{'tipo': t, 'conta_como_renda': conta_como_renda(t)} for t in TIPOS])


@bp.route('/receitas/catalogo', methods=['POST'])
def upsert_catalogo():
    data = request.json or {}
    nome = (data.get('nome') or '').strip()
    if not nome:
        return jsonify({'ok': False, 'msg': 'Nome é obrigatório'}), 400

    tipo = data.get('tipo', 'outra')
    if tipo not in TIPOS:
        return jsonify({'ok': False, 'msg': f'Tipo inválido: {tipo}'}), 400

    campos = (nome, data.get('categoria_id'), data.get('dia_recebimento'), tipo,
              data.get('tipo_valor', 'variavel'),
              data.get('padrao_variabilidade', 'variavel_nao_sazonal'),
              data.get('valor_padrao'), data.get('regras_match') or REGRAS_PADRAO,
              data.get('recorrencia', 'fixa'))

    try:
        with db() as conn:
            if data.get('id'):
                conn.execute("""
                    UPDATE receita SET nome=?, categoria_id=?, dia_recebimento=?, tipo=?, tipo_valor=?,
                    padrao_variabilidade=?, valor_padrao=?, regras_match=?, recorrencia=?, ativo=? WHERE id=?
                """, (*campos, data.get('ativo', 1), data['id']))
                receita_id = data['id']
            else:
                cur = conn.execute("""
                    INSERT INTO receita (nome, categoria_id, dia_recebimento, tipo, tipo_valor,
                                         padrao_variabilidade, valor_padrao, regras_match, recorrencia)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, campos)
                receita_id = cur.lastrowid
    except sqlite3.IntegrityError:
        # nome é UNIQUE: erro esperado, não falha de sistema
        return jsonify({'ok': False, 'msg': f'Já existe uma receita chamada "{nome}"'}), 409

    return jsonify({'ok': True, 'id': receita_id})


@bp.route('/receitas/catalogo/<int:rid>/toggle', methods=['POST'])
def toggle_ativo(rid):
    conn = get_db()
    conn.execute('UPDATE receita SET ativo = 1 - ativo WHERE id=?', (rid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})
