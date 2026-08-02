import json
from flask import Blueprint, jsonify, request
from .db import get_db

bp = Blueprint('lancamentos', __name__)


def _valor_previsto(conn, despesa_id, mes_ref, padrao, valor_padrao):
    mes = int(mes_ref.split('-')[1])
    ultimo = conn.execute(
        "SELECT COALESCE(valor_real, valor_esperado) as v FROM lancamento WHERE despesa_id=? AND mes_ref<? ORDER BY mes_ref DESC LIMIT 1",
        (despesa_id, mes_ref)
    ).fetchone()

    if not ultimo:
        return valor_padrao or 0.0

    if padrao == 'fixa':
        return ultimo['v']

    if padrao == 'variavel_sazonal':
        mes_str = str(mes).zfill(2)
        rows = conn.execute(
            "SELECT COALESCE(valor_real, valor_esperado) as v FROM lancamento WHERE despesa_id=? AND mes_ref LIKE ? ORDER BY mes_ref DESC LIMIT 3",
            (despesa_id, f'%-{mes_str}')
        ).fetchall()
        if rows:
            return sum(r['v'] for r in rows) / len(rows)

    if padrao == 'variavel_nao_sazonal':
        rows = conn.execute(
            "SELECT COALESCE(valor_real, valor_esperado) as v FROM lancamento WHERE despesa_id=? AND mes_ref<? ORDER BY mes_ref DESC LIMIT 3",
            (despesa_id, mes_ref)
        ).fetchall()
        if rows:
            return sum(r['v'] for r in rows) / len(rows)

    if padrao == 'anual':
        mes_str = str(mes).zfill(2)
        n = conn.execute(
            "SELECT COUNT(*) FROM lancamento WHERE despesa_id=? AND mes_ref LIKE ?",
            (despesa_id, f'%-{mes_str}')
        ).fetchone()[0]
        if not n:
            return 0.0

    return ultimo['v']


@bp.route('/lancamentos')
def list_lancamentos():
    mes_ref = request.args.get('mes', '')
    conn = get_db()

    # Garantir que o mês está aberto
    despesas = conn.execute('SELECT * FROM despesa WHERE ativo=1').fetchall()
    for d in despesas:
        prev = _valor_previsto(conn, d['id'], mes_ref, d['padrao_variabilidade'], d['valor_padrao'])
        conn.execute(
            'INSERT OR IGNORE INTO lancamento (mes_ref, despesa_id, valor_esperado, status) VALUES (?,?,?,"nao_encontrado")',
            (mes_ref, d['id'], prev)
        )
    conn.commit()

    rows = conn.execute("""
        SELECT l.*, d.nome as despesa_nome, d.tipo_valor, d.padrao_variabilidade,
               c.nome as categoria_nome, c.id as categoria_id
        FROM lancamento l
        JOIN despesa d ON d.id = l.despesa_id
        LEFT JOIN categoria c ON c.id = d.categoria_id
        WHERE l.mes_ref = ?
        ORDER BY c.nome, d.nome
    """, (mes_ref,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/lancamentos/<int:lid>', methods=['PATCH'])
def update_lancamento(lid):
    data = request.json or {}
    allowed = ['valor_esperado', 'status', 'transacao_id', 'valor_real', 'data_pagamento', 'linha_digitavel']
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({'ok': False}), 400
    sql = f"UPDATE lancamento SET {', '.join(k+'=?' for k in fields)} WHERE id=?"
    conn = get_db()
    conn.execute(sql, (*fields.values(), lid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@bp.route('/resumo')
def resumo():
    mes_ref = request.args.get('mes', '')
    conn = get_db()
    rows = conn.execute("""
        SELECT status,
               SUM(CASE WHEN status='pago' THEN valor_real ELSE valor_esperado END) as total
        FROM lancamento WHERE mes_ref=? GROUP BY status
    """, (mes_ref,)).fetchall()

    pago = next((r['total'] for r in rows if r['status'] == 'pago'), 0) or 0
    agendado = next((r['total'] for r in rows if r['status'] == 'agendado'), 0) or 0
    nao = next((r['total'] for r in rows if r['status'] == 'nao_encontrado'), 0) or 0
    total = pago + agendado + nao

    cfg = {r['chave']: float(r['valor']) for r in conn.execute('SELECT chave, valor FROM config').fetchall()}
    reserva = cfg.get('reserva_desejada', 5000)
    saldo = cfg.get('saldo_conta', 0)
    receitas = conn.execute('SELECT COALESCE(SUM(valor),0) FROM receita WHERE mes_ref=?', (mes_ref,)).fetchone()[0] or 0
    resgate = max(0, total + reserva - saldo - receitas)

    conn.close()
    return jsonify({'pago': pago, 'agendado': agendado, 'naoEncontrado': nao,
                    'total': total, 'reserva': reserva, 'saldo': saldo,
                    'receitas': receitas, 'resgate': resgate})


@bp.route('/config', methods=['GET'])
def get_config():
    conn = get_db()
    rows = conn.execute('SELECT chave, valor FROM config').fetchall()
    conn.close()
    return jsonify({r['chave']: r['valor'] for r in rows})


@bp.route('/config', methods=['POST'])
def set_config():
    data = request.json or {}
    conn = get_db()
    for k, v in data.items():
        conn.execute('INSERT OR REPLACE INTO config (chave, valor) VALUES (?,?)', (k, str(v)))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@bp.route('/receitas')
def list_receitas():
    mes_ref = request.args.get('mes', '')
    conn = get_db()
    rows = conn.execute('SELECT * FROM receita WHERE mes_ref=? ORDER BY tipo', (mes_ref,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/receitas', methods=['POST'])
def upsert_receita():
    data = request.json or {}
    conn = get_db()
    if data.get('id'):
        conn.execute('UPDATE receita SET tipo=?, valor=?, origem=? WHERE id=?',
                     (data['tipo'], data['valor'], data.get('origem'), data['id']))
    else:
        conn.execute('INSERT INTO receita (mes_ref, tipo, valor, origem) VALUES (?,?,?,?)',
                     (data['mes_ref'], data['tipo'], data['valor'], data.get('origem')))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@bp.route('/receitas/<int:rid>', methods=['DELETE'])
def delete_receita(rid):
    conn = get_db()
    conn.execute('DELETE FROM receita WHERE id=?', (rid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})
