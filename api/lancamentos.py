import json
from flask import Blueprint, jsonify, request
from .db import get_db, get_config_value, periodo_competencia

bp = Blueprint('lancamentos', __name__)


def _valor_previsto(conn, despesa_id, mes_ref, padrao, valor_padrao, natureza='despesa'):
    """Prevê o valor do mês a partir do histórico (doc 03).

    `natureza` escolhe de qual par tabela/coluna o histórico vem — receita usa
    `lancamento_receita`/`receita_id`. O algoritmo é o mesmo: o que muda é onde
    a série está guardada.
    """
    from .motor_batimento import cfg
    c = cfg(natureza)
    tab, fk = c['lancamento'], c['fk']

    mes = int(mes_ref.split('-')[1])
    ultimo = conn.execute(
        f"SELECT COALESCE(valor_real, valor_esperado) as v FROM {tab} WHERE {fk}=? AND mes_ref<? ORDER BY mes_ref DESC LIMIT 1",
        (despesa_id, mes_ref)
    ).fetchone()

    if not ultimo:
        return valor_padrao or 0.0

    if padrao == 'fixa':
        return ultimo['v']

    if padrao == 'variavel_sazonal':
        mes_str = str(mes).zfill(2)
        rows = conn.execute(
            f"SELECT COALESCE(valor_real, valor_esperado) as v FROM {tab} WHERE {fk}=? AND mes_ref LIKE ? ORDER BY mes_ref DESC LIMIT 3",
            (despesa_id, f'%-{mes_str}')
        ).fetchall()
        if rows:
            return sum(r['v'] for r in rows) / len(rows)

    if padrao == 'variavel_nao_sazonal':
        rows = conn.execute(
            f"SELECT COALESCE(valor_real, valor_esperado) as v FROM {tab} WHERE {fk}=? AND mes_ref<? ORDER BY mes_ref DESC LIMIT 3",
            (despesa_id, mes_ref)
        ).fetchall()
        if rows:
            return sum(r['v'] for r in rows) / len(rows)

    if padrao == 'anual':
        mes_str = str(mes).zfill(2)
        n = conn.execute(
            f"SELECT COUNT(*) FROM {tab} WHERE {fk}=? AND mes_ref LIKE ?",
            (despesa_id, f'%-{mes_str}')
        ).fetchone()[0]
        if not n:
            return 0.0

    return ultimo['v']


@bp.route('/lancamentos')
def list_lancamentos():
    mes_ref = request.args.get('mes', '')
    conn = get_db()

    # Garantir que o mês está aberto. Só as fixas: esporádica não tem previsão
    # a fazer e só existe no mês em que acontecer (doc 14).
    from .motor_batimento import garantir_lancamentos
    garantir_lancamentos(conn, 'despesa', mes_ref)
    garantir_lancamentos(conn, 'receita', mes_ref)
    conn.commit()

    # Despesa desativada não deve poluir o mês. A exceção é a que teve
    # movimento de verdade no mês (paga/agendada antes de ser desativada):
    # escondê-la tiraria um pagamento real da lista e dos totais, fazendo o
    # mês fechar com número errado.
    rows = conn.execute("""
        SELECT l.*, d.nome as despesa_nome, d.tipo_valor, d.padrao_variabilidade,
               d.recorrencia, c.nome as categoria_nome, c.id as categoria_id
        FROM lancamento l
        JOIN despesa d ON d.id = l.despesa_id
        LEFT JOIN categoria c ON c.id = d.categoria_id
        WHERE l.mes_ref = ?
          AND (d.ativo = 1 OR l.status != 'nao_encontrado')
          -- esporádica não cobra previsão: o lançamento vazio dela some da
          -- tela. Só some da tela — apagar destruiria a série histórica que
          -- alimenta _valor_previsto, e boa parte dela é seed sem transação.
          AND (d.recorrencia = 'fixa' OR l.status != 'nao_encontrado')
        ORDER BY c.nome, d.nome
    """, (mes_ref,)).fetchall()

    itens = [dict(r) for r in rows] + _esporadicas_do_mes(conn, mes_ref)
    itens.sort(key=lambda x: ((x['categoria_nome'] or '~').lower(), x['despesa_nome'].lower()))
    conn.close()
    return jsonify(itens)


def _esporadicas_do_mes(conn, mes_ref: str):
    """Despesas esporádicas que aconteceram no mês.

    Elas não têm lançamento (doc 14) — a transação associada é o registro. Cada
    transação vira uma linha própria, de propósito: três consultas médicas no
    mesmo mês são três despesas, e é justamente isso que o lançamento, com seu
    UNIQUE(mes_ref, despesa_id), não conseguiria representar.

    O `id` devolvido é negativo para não colidir com id de lançamento no
    frontend, e sinaliza "esta linha não é editável como lançamento".

    Transação que já tem lançamento apontando para ela fica de fora: é o caso
    de uma despesa que virou esporádica *depois* de já ter casado no mês. Sem
    esse filtro ela apareceria duas vezes — pela via antiga e pela nova — e o
    mês fecharia com o valor dobrado.
    """
    dia_corte = int(get_config_value(conn, 'dia_recebimento_salario', '27'))
    ini, fim = periodo_competencia(mes_ref, dia_corte)
    rows = conn.execute("""
        SELECT t.id, t.data, t.valor, t.situacao, t.descricao,
               d.id as despesa_id, d.nome as despesa_nome, d.tipo_valor,
               d.padrao_variabilidade, d.recorrencia,
               c.nome as categoria_nome, c.id as categoria_id
        FROM transacao t
        JOIN despesa d ON d.id = t.despesa_id
        LEFT JOIN categoria c ON c.id = d.categoria_id
        WHERE d.recorrencia = 'esporadica' AND t.tipo = 'debito'
          AND t.data BETWEEN ? AND ?
          AND NOT EXISTS (SELECT 1 FROM lancamento l WHERE l.transacao_id = t.id)
    """, (ini, fim)).fetchall()

    return [{
        'id': -r['id'],
        'mes_ref': mes_ref,
        'despesa_id': r['despesa_id'],
        'despesa_nome': r['despesa_nome'],
        'categoria_nome': r['categoria_nome'],
        'categoria_id': r['categoria_id'],
        'tipo_valor': r['tipo_valor'],
        'padrao_variabilidade': r['padrao_variabilidade'],
        'recorrencia': 'esporadica',
        'valor_esperado': abs(r['valor']),
        'valor_real': abs(r['valor']),
        'status': 'pago' if r['situacao'] == 'efetivada' else 'agendado',
        'transacao_id': r['id'],
        'data_pagamento': r['data'],
        'descricao_transacao': r['descricao'],
        'linha_digitavel': None,
        'tipo_codigo': None,
    } for r in rows]


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
    # mesmo filtro de /lancamentos — se o resumo somasse despesas que a lista
    # não mostra, o total da tela não bateria com as linhas exibidas
    rows = conn.execute("""
        SELECT l.status,
               SUM(CASE WHEN l.status='pago' THEN l.valor_real ELSE l.valor_esperado END) as total
        FROM lancamento l
        JOIN despesa d ON d.id = l.despesa_id
        WHERE l.mes_ref=? AND (d.ativo = 1 OR l.status != 'nao_encontrado')
          AND (d.recorrencia = 'fixa' OR l.status != 'nao_encontrado')
        GROUP BY l.status
    """, (mes_ref,)).fetchall()

    pago = next((r['total'] for r in rows if r['status'] == 'pago'), 0) or 0
    agendado = next((r['total'] for r in rows if r['status'] == 'agendado'), 0) or 0
    nao = next((r['total'] for r in rows if r['status'] == 'nao_encontrado'), 0) or 0

    # Esporádicas não estão em `lancamento`, mas estão na lista da tela — se o
    # resumo as ignorasse, o total não bateria com as linhas exibidas
    for e in _esporadicas_do_mes(conn, mes_ref):
        if e['status'] == 'pago':
            pago += e['valor_real']
        else:
            agendado += e['valor_real']

    total = pago + agendado + nao

    cfg = {r['chave']: float(r['valor']) for r in conn.execute('SELECT chave, valor FROM config').fetchall()}
    reserva = cfg.get('reserva_desejada', 5000)
    saldo = cfg.get('saldo_conta', 0)
    # Renda do mês: só o que conta como renda (doc 14). Resgate e estorno
    # entram na conta como dinheiro chegando, mas não são renda nova — somá-los
    # faria a calculadora de resgate concluir que não é preciso resgatar nada.
    # Fase 2: ainda não há lançamento de receita, então isto devolve 0 — mesmo
    # resultado de antes, quando a tabela `receita` estava vazia.
    receitas = conn.execute("""
        SELECT COALESCE(SUM(COALESCE(lr.valor_real, lr.valor_esperado)), 0)
        FROM lancamento_receita lr JOIN receita r ON r.id = lr.receita_id
        WHERE lr.mes_ref = ? AND r.tipo NOT IN ('resgate_mensal','resgate_esporadico','estorno','transferencia')
    """, (mes_ref,)).fetchone()[0] or 0
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
