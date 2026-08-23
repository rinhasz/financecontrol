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
        SELECT l.*, d.nome as item_nome, l.despesa_id as item_id, d.tipo_valor,
               d.padrao_variabilidade, d.recorrencia,
               c.nome as categoria_nome, c.id as categoria_id
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

    despesas = [dict(r) for r in rows] + _esporadicas_do_mes(conn, mes_ref, 'despesa')
    despesas.sort(key=lambda x: ((x['categoria_nome'] or '~').lower(), x['item_nome'].lower()))

    receitas = _receitas_do_mes(conn, mes_ref)
    conn.close()
    return jsonify({'despesas': despesas, 'receitas': receitas})


def _receitas_do_mes(conn, mes_ref: str) -> list:
    """Receitas do mês: as fixas (com lançamento) mais as esporádicas (que só
    existem como transação). Mesma união do lado da despesa."""
    rows = conn.execute("""
        SELECT lr.*, r.nome as item_nome, r.tipo, r.tipo_valor, r.padrao_variabilidade,
               r.recorrencia, c.nome as categoria_nome, c.id as categoria_id
        FROM lancamento_receita lr
        JOIN receita r ON r.id = lr.receita_id
        LEFT JOIN categoria c ON c.id = r.categoria_id
        WHERE lr.mes_ref = ?
          AND (r.ativo = 1 OR lr.status != 'nao_encontrado')
          AND (r.recorrencia = 'fixa' OR lr.status != 'nao_encontrado')
        ORDER BY r.nome
    """, (mes_ref,)).fetchall()

    itens = [{**dict(r), 'item_id': r['receita_id']} for r in rows]
    itens += _esporadicas_do_mes(conn, mes_ref, 'receita')
    itens.sort(key=lambda x: (x['tipo'], x['item_nome'].lower()))
    return itens


def _esporadicas_do_mes(conn, mes_ref: str, natureza: str = 'despesa'):
    """Movimentos do mês que **não cabem** num lançamento.

    `lancamento` tem UNIQUE(mes_ref, item_id): cabe um por mês. Sobra tudo isto,
    que precisa aparecer na tela do mesmo jeito:

    - item esporádico, que não tem lançamento nenhum;
    - a segunda (terceira, quarta) transação de um item marcado "mais de um por
      mês" — três consultas médicas no mesmo mês são três despesas.

    O critério é justamente esse: **transação sem lançamento apontando para
    ela**. Quem já tem lançamento fica de fora, senão apareceria duas vezes — a
    via antiga e a nova — e o mês fecharia com o valor dobrado.

    O `id` devolvido é negativo para não colidir com id de lançamento no
    frontend, e sinaliza "esta linha não é editável como lançamento".
    """
    from .motor_batimento import cfg, status_de
    c = cfg(natureza)

    dia_corte = int(get_config_value(conn, 'dia_recebimento_salario', '27'))
    ini, fim = periodo_competencia(mes_ref, dia_corte)
    rows = conn.execute(f"""
        SELECT t.id, t.data, t.valor, t.situacao, t.descricao, t.objetivo,
               d.id as item_id, d.nome as item_nome, d.tipo_valor,
               d.padrao_variabilidade, d.recorrencia,
               {'d.tipo as tipo,' if natureza == 'receita' else ''}
               c.nome as categoria_nome, c.id as categoria_id
        FROM transacao t
        JOIN {c['catalogo']} d ON d.id = t.{c['fk']}
        LEFT JOIN categoria c ON c.id = d.categoria_id
        WHERE t.tipo = ? AND t.data BETWEEN ? AND ?
          AND NOT EXISTS (SELECT 1 FROM {c['lancamento']} l WHERE l.transacao_id = t.id)
    """, (c['tipo_tx'], ini, fim)).fetchall()

    return [{
        'id': -r['id'],
        'mes_ref': mes_ref,
        'item_id': r['item_id'],
        'item_nome': r['item_nome'],
        **({'tipo': r['tipo']} if natureza == 'receita' else {}),
        'categoria_nome': r['categoria_nome'],
        'categoria_id': r['categoria_id'],
        'tipo_valor': r['tipo_valor'],
        'padrao_variabilidade': r['padrao_variabilidade'],
        'recorrencia': 'esporadica',
        'valor_esperado': abs(r['valor']),
        'valor_real': abs(r['valor']),
        'status': status_de(natureza, r['situacao']),
        'transacao_id': r['id'],
        c['data_mov']: r['data'],
        'descricao_transacao': r['descricao'],
        'objetivo': r['objetivo'],
        'linha_digitavel': None,
        'tipo_codigo': None,
    } for r in rows]


@bp.route('/consolidado')
def consolidado():
    """Visão consolidada do mês: uma linha por item, com estorno já abatido.

    A visão analítica (/api/lancamentos) mostra o mês como ele aconteceu — cada
    cobrança numa linha, e um estorno aparecendo tanto no grupo de despesas
    (o gasto) quanto no de receitas (a devolução). É o que se quer para
    conferir contra o extrato.

    Esta aqui responde outra pergunta: **quanto essa despesa custou de fato no
    mês**. Então agrupa as ocorrências repetidas e subtrai o que voltou. Uma
    despesa integralmente estornada custou zero e **não aparece** — mostrá-la
    zerada seria ruído. Estorno parcial deixa só o líquido.
    """
    mes_ref = request.args.get('mes', '')
    conn = get_db()
    dia_corte = int(get_config_value(conn, 'dia_recebimento_salario', '26'))
    ini, fim = periodo_competencia(mes_ref, dia_corte)

    # --- saídas: soma das ocorrências de cada despesa ---
    grupos = {}
    for l in _todas_despesas_do_mes(conn, mes_ref):
        g = grupos.setdefault(l['item_id'], {
            'item_id': l['item_id'], 'item_nome': l['item_nome'],
            'categoria_nome': l['categoria_nome'], 'bruto': 0.0,
            'ocorrencias': 0, 'estornado': 0.0, 'linhas': [],
        })
        valor = l['valor_real'] if l['status'] == 'pago' else l['valor_esperado']
        g['bruto'] += valor
        g['ocorrencias'] += 1
        # as linhas que formaram o número vão junto: quem consolida precisa
        # poder abrir e ver de onde veio, sem uma segunda chamada
        g['linhas'].append({
            'tipo': 'gasto', 'valor': valor, 'status': l['status'],
            'data': l.get('data_pagamento'),
            'descricao': l.get('descricao_transacao') or l['item_nome'],
        })

    # --- estornos com despesa conhecida abatem daquela despesa ---
    estornos = conn.execute("""
        SELECT t.estorna_despesa_id as despesa_id, t.valor, t.descricao, t.data, d.nome as despesa_nome
        FROM transacao t
        LEFT JOIN despesa d ON d.id = t.estorna_despesa_id
        JOIN receita r ON r.id = t.receita_id
        WHERE r.tipo = 'estorno' AND t.data BETWEEN ? AND ?
          AND t.estorna_despesa_id IS NOT NULL
    """, (ini, fim)).fetchall()

    for e in estornos:
        g = grupos.get(e['despesa_id'])
        if g is None:
            # a despesa não teve gasto neste mês, mas a devolução chegou aqui:
            # vira crédito, e some do bloco de saídas
            g = grupos.setdefault(e['despesa_id'], {
                'item_id': e['despesa_id'], 'item_nome': e['despesa_nome'] or '?',
                'categoria_nome': None, 'bruto': 0.0, 'ocorrencias': 0,
                'estornado': 0.0, 'linhas': [],
            })
        g['estornado'] += abs(e['valor'])
        g['linhas'].append({
            'tipo': 'estorno', 'valor': -abs(e['valor']), 'status': None,
            'data': e['data'], 'descricao': e['descricao'],
        })

    despesas = []
    anulados = []
    total_estornado = 0.0
    for g in grupos.values():
        liquido = round(g['bruto'] - g['estornado'], 2)
        # contabiliza antes de filtrar: o abatimento total é o que mais importa
        # mostrar, e some justamente nos casos em que a linha é removida
        total_estornado += g['estornado']
        g['linhas'].sort(key=lambda x: (x['data'] or '9999'))
        linha = {**g, 'liquido': liquido}
        # Anulou por completo: fica fora do total (custou zero) mas **não some**.
        # Vai para uma lista à parte, com o detalhe, senão o gasto e a devolução
        # desapareceriam sem deixar rastro e o mês pareceria não tê-los tido.
        (anulados if abs(liquido) < 0.01 and g['estornado'] > 0 else despesas).append(linha)

    ordenar = lambda xs: sorted(xs, key=lambda x: ((x['categoria_nome'] or '~').lower(), x['item_nome'].lower()))
    despesas, anulados = ordenar(despesas), ordenar(anulados)

    # --- entradas: agrupa por item; estorno já abatido acima não entra aqui ---
    abatidos = {e['despesa_id'] for e in estornos}
    from .receitas import conta_como_renda

    grupos_r = {}
    for r in _receitas_do_mes(conn, mes_ref):
        # estorno que já compensou uma despesa não pode ser contado de novo
        if r['tipo'] == 'estorno' and abatidos:
            tx = r.get('transacao_id')
            if tx and conn.execute(
                    'SELECT estorna_despesa_id FROM transacao WHERE id=?', (tx,)).fetchone()['estorna_despesa_id']:
                continue
        g = grupos_r.setdefault(r['item_id'], {
            'item_id': r['item_id'], 'item_nome': r['item_nome'], 'tipo': r['tipo'],
            'total': 0.0, 'ocorrencias': 0, 'renda': conta_como_renda(r['tipo']),
        })
        g['total'] += r['valor_real'] if r['valor_real'] is not None else r['valor_esperado']
        g['ocorrencias'] += 1

    receitas = sorted(grupos_r.values(), key=lambda x: (x['tipo'], x['item_nome'].lower()))
    conn.close()

    return jsonify({
        'periodo': {'ini': ini, 'fim': fim},
        'despesas': despesas,
        'anulados': anulados,
        'receitas': receitas,
        'totais': {
            'despesas': round(sum(d['liquido'] for d in despesas), 2),
            'estornado': round(total_estornado, 2),
            'renda': round(sum(r['total'] for r in receitas if r['renda']), 2),
            'movimentacao': round(sum(r['total'] for r in receitas if not r['renda']), 2),
        },
    })


def _todas_despesas_do_mes(conn, mes_ref: str) -> list:
    """A mesma lista que /api/lancamentos devolve no lado das saídas."""
    rows = conn.execute("""
        SELECT l.*, d.nome as item_nome, l.despesa_id as item_id,
               c.nome as categoria_nome, t.descricao as descricao_transacao
        FROM lancamento l
        LEFT JOIN transacao t ON t.id = l.transacao_id
        JOIN despesa d ON d.id = l.despesa_id
        LEFT JOIN categoria c ON c.id = d.categoria_id
        WHERE l.mes_ref = ?
          AND (d.ativo = 1 OR l.status != 'nao_encontrado')
          AND (d.recorrencia = 'fixa' OR l.status != 'nao_encontrado')
    """, (mes_ref,)).fetchall()
    return [dict(r) for r in rows] + _esporadicas_do_mes(conn, mes_ref, 'despesa')


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
    for e in _esporadicas_do_mes(conn, mes_ref, 'despesa'):
        if e['status'] == 'pago':
            pago += e['valor_real']
        else:
            agendado += e['valor_real']

    total = pago + agendado + nao

    # --- lado da entrada ---------------------------------------------------
    # Renda e movimentação vêm da MESMA lista que a tela mostra, e são somadas
    # separadas: resgate e estorno chegam na conta como dinheiro entrando, mas
    # não são renda nova. Somá-los faria a calculadora concluir que não é
    # preciso resgatar nada — justamente o erro que o `tipo` existe para evitar.
    from .receitas import conta_como_renda

    renda = 0.0
    renda_recebida = 0.0
    movimentacao = {}
    for r in _receitas_do_mes(conn, mes_ref):
        valor = r['valor_real'] if r['valor_real'] is not None else r['valor_esperado']
        if conta_como_renda(r['tipo']):
            renda += valor
            if r['status'] == 'recebido':
                renda_recebida += valor
        else:
            movimentacao[r['tipo']] = movimentacao.get(r['tipo'], 0.0) + valor

    cfg = {r['chave']: float(r['valor']) for r in conn.execute('SELECT chave, valor FROM config').fetchall()}
    reserva = cfg.get('reserva_desejada', 5000)
    saldo = cfg.get('saldo_conta', 0)

    # O ciclo do resgate, fechado: quanto precisa, quanto já foi feito, quanto
    # falta. Só o resgate MENSAL abate — o esporádico tem objetivo próprio e
    # não é resposta ao déficit do mês (doc 14).
    resgate_necessario = max(0, total + reserva - saldo - renda)
    resgate_ja_feito = movimentacao.get('resgate_mensal', 0.0)
    falta_resgatar = max(0, resgate_necessario - resgate_ja_feito)

    # a competência não é o mês do calendário; mostrar o intervalo evita o
    # usuário ter que deduzir a regra de antecipação de fim de semana/feriado
    ini, fim = periodo_competencia(mes_ref, int(get_config_value(conn, 'dia_recebimento_salario', '26')))

    conn.close()
    return jsonify({
        'periodo': {'ini': ini, 'fim': fim},
        'pago': pago, 'agendado': agendado, 'naoEncontrado': nao,
        'total': total, 'reserva': reserva, 'saldo': saldo,
        'renda': renda, 'rendaRecebida': renda_recebida,
        'movimentacao': movimentacao,
        # realizado contra realizado: somar renda ainda não recebida com gasto
        # já pago daria um saldo que não existe em lugar nenhum. "Vai fechar o
        # mês?" é a pergunta que a calculadora de resgate responde, logo abaixo.
        'saldoMes': renda_recebida - pago,
        'resgateNecessario': resgate_necessario,
        'resgateJaFeito': resgate_ja_feito,
        'faltaResgatar': falta_resgatar,
        # nomes antigos mantidos para não quebrar nada que ainda os leia
        'receitas': renda, 'resgate': resgate_necessario,
    })


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
