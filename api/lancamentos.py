import json
from flask import Blueprint, jsonify, request
from .db import get_db, get_config_value, periodo_competencia, data_no_periodo

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
               d.padrao_variabilidade, d.recorrencia, d.dia_vencimento,
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

    # O previsto de uma linha em aberto passa a vir da projeção (api/projecao.py),
    # que olha o histórico consolidado. `valor_esperado` era o palpite antigo,
    # herdado do mês anterior; mantê-lo faria a lista somar um total diferente do
    # que a calculadora usa.
    from . import projecao as pj
    proj = pj.projecoes_do_mes(conn, 'despesa', mes_ref)
    # quais vieram de correção manual: a tela precisa marcá-las e oferecer o
    # caminho de volta para o automático
    manuais = pj.manuais_do_mes(conn, 'despesa', mes_ref)

    despesas = [dict(r) for r in rows] + _esporadicas_do_mes(conn, mes_ref, 'despesa')
    _marcar_vencimento(conn, despesas, mes_ref)
    for d in despesas:
        d['projetado'] = proj.get(d['item_id'])
        d['projecao_manual'] = d['item_id'] in manuais
        if d['status'] == 'nao_encontrado' and d['projetado'] is not None:
            d['valor_esperado'] = d['projetado']
    despesas.sort(key=lambda x: ((x['categoria_nome'] or '~').lower(), x['item_nome'].lower()))

    receitas = _receitas_do_mes(conn, mes_ref)
    conn.close()
    return jsonify({'despesas': despesas, 'receitas': receitas})


def _marcar_vencimento(conn, despesas: list, mes_ref: str) -> None:
    """Acrescenta `data_prevista` e `dias_para_vencer` a cada despesa.

    Serve para a tela avisar do que vence e ainda **não está nem agendado** —
    o caso que custa multa e juros. O sinal precisa vir daqui e não do
    frontend: montar a data a partir do `dia_vencimento` exige saber que a
    competência atravessa dois meses do calendário, e essa regra já mora no
    servidor (`data_no_periodo`).

    A contagem é contra **hoje**, não contra o mês exibido: olhar um mês
    passado não deve pintar tudo de vermelho, e por isso `dias_para_vencer`
    fica `None` quando a data não existe.
    """
    from datetime import date

    dia_corte = int(get_config_value(conn, 'dia_recebimento_salario', '26'))
    ini, fim = periodo_competencia(mes_ref, dia_corte)
    hoje = date.today()
    for d in despesas:
        venc = data_no_periodo(d.get('dia_vencimento'), ini, fim)
        d['data_prevista'] = venc.isoformat() if venc else None
        d['dias_para_vencer'] = (venc - hoje).days if venc else None


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

    from . import projecao as pj
    proj = pj.projecoes_do_mes(conn, 'receita', mes_ref)
    manuais = pj.manuais_do_mes(conn, 'receita', mes_ref)

    itens = [{**dict(r), 'item_id': r['receita_id']} for r in rows]
    itens += _esporadicas_do_mes(conn, mes_ref, 'receita')
    for r in itens:
        r['projetado'] = proj.get(r['item_id'])
        r['projecao_manual'] = r['item_id'] in manuais
        if r['status'] == 'nao_encontrado' and r['projetado'] is not None:
            r['valor_esperado'] = r['projetado']
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
    """Fechamento do mês e a calculadora de resgate.

    Cada item tem três números, por definição: **pago** (já saiu da conta),
    **agendado** (marcado, ainda vai sair) e **projetado** (quanto se espera que
    custe no total, estimado do histórico — ver api/projecao.py).

    Daí sai o que a calculadora precisa: *a vencer* = agendado + o que ainda
    falta acontecer para chegar ao projetado. O que já foi pago **não** entra —
    esse dinheiro já saiu e já está descontado do saldo. Somá-lo era o erro que
    fazia um mês inteiramente quitado ainda pedir resgate.
    """
    from . import projecao as pj

    mes_ref = request.args.get('mes', '')
    conn = get_db()

    proj_d = pj.projecoes_do_mes(conn, 'despesa', mes_ref)
    real_d = pj.realizado_do_mes(conn, 'despesa', mes_ref)
    proj_r = pj.projecoes_do_mes(conn, 'receita', mes_ref)
    real_r = pj.realizado_do_mes(conn, 'receita', mes_ref)

    varios = {n: {r['id'] for r in conn.execute(
        f'SELECT id FROM {tab} WHERE varios_por_mes = 1')}
        for n, tab in (('despesa', 'despesa'), ('receita', 'receita'))}

    def totalizar(projecoes, realizados, natureza, somente=None):
        """(realizado, agendado, a_realizar) somando item a item.

        `a_realizar` é por item e nunca negativo: uma despesa que veio mais cara
        que o projetado não gera "crédito" para abater outra — o excesso já
        aconteceu e já está no realizado.

        **Item que já teve movimento no mês não projeta resíduo**, a menos que
        seja "mais de um por mês". Uma conta que só acontece uma vez e já foi
        paga não vai acontecer de novo: insistir na diferença contra o projetado
        fazia um mês inteiramente quitado ainda aparecer com valor a vencer.
        """
        feito = agendado = falta = 0.0
        for item_id in set(projecoes) | set(realizados):
            if somente is not None and item_id not in somente:
                continue
            r = realizados.get(item_id, {'pago': 0.0, 'agendado': 0.0})
            feito += r['pago']
            agendado += r['agendado']
            aconteceu = r['pago'] != 0 or r['agendado'] != 0
            if not aconteceu or item_id in varios[natureza]:
                falta += max(0.0, projecoes.get(item_id, 0.0) - r['pago'] - r['agendado'])
        return round(feito, 2), round(agendado, 2), round(falta, 2)

    pago, agendado, a_realizar = totalizar(proj_d, real_d, 'despesa')

    # A lista analítica mostra o mês **bruto** — o gasto numa linha e o estorno
    # noutra, nos dois blocos. `pago` acima é líquido (o estorno já abatido),
    # que é o certo para saldo e resgate. Os dois números convivem: o card do
    # total na visão analítica usa o bruto, para bater com as linhas exibidas.
    estornado = round(sum(
        abs(r['valor']) for r in conn.execute(
            'SELECT t.valor FROM transacao t JOIN receita x ON x.id = t.receita_id '
            "WHERE x.tipo = 'estorno' AND t.estorna_despesa_id IS NOT NULL "
            'AND t.data BETWEEN ? AND ?',
            periodo_competencia(mes_ref, int(get_config_value(conn, 'dia_recebimento_salario', '26'))))), 2)

    # Só o que é renda de verdade entra na conta do resgate: resgate e estorno
    # chegam na conta mas não são renda nova (doc 14). Isso vale sobretudo para
    # o PROJETADO — contar um resgate futuro como renda faria a calculadora
    # concluir que não é preciso resgatar, usando a própria resposta como dado.
    from .receitas import conta_como_renda
    tipos = {r['id']: r['tipo'] for r in conn.execute('SELECT id, tipo FROM receita')}
    itens_renda = {i for i, t in tipos.items() if conta_como_renda(t)}

    recebido, a_receber_marcado, a_receber_projetado = totalizar(
        proj_r, real_r, 'receita', somente=itens_renda)
    renda = round(sum(
        v['pago'] + v['agendado']
        for i, v in real_r.items() if conta_como_renda(tipos.get(i, 'outra'))), 2)
    renda_recebida = round(sum(
        v['pago'] for i, v in real_r.items() if conta_como_renda(tipos.get(i, 'outra'))), 2)

    movimentacao = {}
    for i, v in real_r.items():
        tipo = tipos.get(i, 'outra')
        if not conta_como_renda(tipo):
            movimentacao[tipo] = round(movimentacao.get(tipo, 0.0) + v['pago'] + v['agendado'], 2)

    # só as chaves numéricas: `config` também guarda texto (a data do saldo),
    # e converter tudo para float quebrava a tela inteira
    def _num(chave, padrao):
        row = conn.execute('SELECT valor FROM config WHERE chave=?', (chave,)).fetchone()
        try:
            return float(row['valor'])
        except (TypeError, ValueError):
            return padrao

    reserva = _num('reserva_desejada', 5000)
    saldo = _num('saldo_conta', 0)
    saldo_data = get_config_value(conn, 'saldo_data', '')

    a_vencer = round(agendado + a_realizar, 2)
    a_receber = round(a_receber_marcado + a_receber_projetado, 2)

    # O resgate já feito **não** abate: ele já entrou na conta e portanto já está
    # dentro do `saldo`. Descontá-lo de novo era contar o mesmo dinheiro duas
    # vezes — com saldo zerado à mão o erro passava despercebido, mas com o saldo
    # real do extrato ele diria "não falta nada" numa conta negativa.
    resgate_necessario = max(0.0, round(a_vencer + reserva - saldo - a_receber, 2))
    resgate_ja_feito = round(movimentacao.get('resgate_mensal', 0.0), 2)
    falta_resgatar = resgate_necessario

    ini, fim = periodo_competencia(mes_ref, int(get_config_value(conn, 'dia_recebimento_salario', '26')))
    conn.close()

    return jsonify({
        'periodo': {'ini': ini, 'fim': fim},
        'pago': pago, 'agendado': agendado, 'aRealizar': a_realizar,
        'total': round(pago + estornado + agendado + a_realizar, 2),
        'totalLiquido': round(pago + agendado + a_realizar, 2),
        'estornado': estornado,
        'aVencer': a_vencer,
        'reserva': reserva, 'saldo': saldo, 'saldoData': saldo_data,
        'renda': renda, 'rendaRecebida': renda_recebida, 'aReceber': a_receber,
        'movimentacao': movimentacao,
        'saldoMes': round(renda_recebida - pago, 2),
        'resgateNecessario': resgate_necessario,
        'resgateJaFeito': resgate_ja_feito,
        'faltaResgatar': falta_resgatar,
        # nomes antigos, mantidos para não quebrar quem ainda os leia
        'naoEncontrado': a_realizar, 'receitas': renda, 'resgate': resgate_necessario,
    })


@bp.route('/projecao/manual', methods=['POST'])
def projecao_manual():
    """Corrige à mão a projeção de um item num mês.

    `valor: null` **apaga** a correção e devolve o item para a projeção
    automática. Zero não apaga — zero é a correção que diz "esta não vai
    acontecer neste mês", e é justamente o caso que a projeção automática não
    consegue expressar sozinha.
    """
    data = request.json or {}
    natureza = data.get('natureza')
    item_id = data.get('item_id')
    mes_ref = data.get('mes_ref')
    if natureza not in ('despesa', 'receita') or not item_id or not mes_ref:
        return jsonify({'ok': False, 'msg': 'natureza, item_id e mes_ref são obrigatórios'}), 400

    valor = data.get('valor')
    conn = get_db()
    if valor is None:
        conn.execute('DELETE FROM projecao_manual WHERE natureza=? AND item_id=? AND mes_ref=?',
                     (natureza, item_id, mes_ref))
    else:
        try:
            valor = abs(float(valor))
        except (TypeError, ValueError):
            conn.close()
            return jsonify({'ok': False, 'msg': f'Valor inválido: {data.get("valor")!r}'}), 400
        conn.execute(
            'INSERT INTO projecao_manual (natureza, item_id, mes_ref, valor) VALUES (?,?,?,?) '
            'ON CONFLICT(natureza, item_id, mes_ref) DO UPDATE SET valor=excluded.valor',
            (natureza, item_id, mes_ref, valor))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'valor': valor})


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
