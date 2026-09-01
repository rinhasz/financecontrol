"""Plano de resgates: quando tirar dinheiro, e quanto, ao longo do mês.

A calculadora do Mês Atual responde **quanto** falta resgatar no mês inteiro.
Ela não responde o que se faz com esse número: resgatar tudo hoje deixa dinheiro
parado na conta corrente rendendo nada, e resgatar tarde demais fura a reserva.

Este módulo responde **quando**. Monta a linha do tempo do mês — saldo de hoje,
entradas e saídas dia a dia — e escolhe as datas de resgate que mantêm a reserva
sempre coberta, mantendo o dinheiro investido o máximo de tempo possível.

Três parâmetros governam o plano, todos em `config`:

| chave | o que é |
|---|---|
| `saldo_conta` / `saldo_data` | de onde a linha do tempo parte (lido do extrato) |
| `reserva_desejada` | piso que o saldo nunca pode furar |
| `resgates_por_mes` | quantos resgates você quer fazer |

`resgates_por_mes` é preferência, não restrição física: um resgate só no começo
do mês sempre resolveria. Mais resgates deixam o dinheiro investido por mais
tempo, ao custo de mais operações — é esse o trade-off que o número expressa.
"""
from datetime import date, timedelta

from flask import Blueprint, jsonify, request

from .db import get_db, get_config_value, periodo_competencia, competencia_da_data

bp = Blueprint('resgates', __name__)

# receitas que SÃO o resgate que estamos planejando — contá-las como entrada
# seria contar com o dinheiro que ainda vamos decidir tirar
TIPOS_RESGATE = ('resgate_mensal', 'resgate_esporadico')


def _num(conn, chave, padrao):
    try:
        return float(get_config_value(conn, chave, str(padrao)))
    except (TypeError, ValueError):
        return float(padrao)


def _data_do_dia(dia: int, ini: str, fim: str):
    """A data dentro da janela de competência que cai no dia `dia` do mês.

    A competência atravessa dois meses do calendário (26/08 a 24/09), então o
    dia 10 é do mês de trás e o dia 28 é do da frente. Devolve `None` quando o
    dia não existe na janela — fevereiro com vencimento no dia 30, por exemplo.
    """
    if not dia:
        return None
    d0 = date.fromisoformat(ini)
    d1 = date.fromisoformat(fim)
    for ano, mes in ((d0.year, d0.month), (d1.year, d1.month)):
        try:
            cand = date(ano, mes, int(dia))
        except ValueError:
            continue
        if d0 <= cand <= d1:
            return cand
    return None


def _eventos(conn, mes_ref: str, ini: str, fim: str, corte: date):
    """Entradas e saídas esperadas, por dia, a partir de `corte`.

    O que já aconteceu **antes** do corte não entra: já está refletido no saldo
    lido do extrato. Contar de novo debitaria duas vezes a mesma conta.

    O que está atrasado — vencimento antes do corte e ainda não pago — entra no
    próprio dia do corte: é dinheiro que sai a qualquer momento.
    """
    from . import projecao as pj

    eventos = []

    def por(dia_iso, valor, rotulo, especie):
        d = max(date.fromisoformat(dia_iso), corte) if dia_iso else corte
        eventos.append({'data': d.isoformat(), 'valor': round(valor, 2),
                        'rotulo': rotulo, 'especie': especie})

    proj_d = pj.projecoes_do_mes(conn, 'despesa', mes_ref)
    for l in conn.execute("""
            SELECT l.status, l.valor_real, l.valor_esperado, l.data_pagamento,
                   l.despesa_id AS item_id, d.nome, d.dia_vencimento
            FROM lancamento l JOIN despesa d ON d.id = l.despesa_id
            WHERE l.mes_ref = ? AND (d.ativo = 1 OR l.status != 'nao_encontrado')
              AND (d.recorrencia = 'fixa' OR l.status != 'nao_encontrado')""", (mes_ref,)):
        if l['status'] == 'pago':
            continue                       # já saiu: está dentro do saldo
        if l['status'] == 'agendado':
            valor = l['valor_real'] or l['valor_esperado'] or 0
            por(l['data_pagamento'], -abs(valor), l['nome'], 'agendado')
        else:
            valor = proj_d.get(l['item_id'], 0) or 0
            if valor > 0:
                d = _data_do_dia(l['dia_vencimento'], ini, fim)
                por(d.isoformat() if d else fim, -abs(valor), l['nome'], 'previsto')

    proj_r = pj.projecoes_do_mes(conn, 'receita', mes_ref)
    for l in conn.execute("""
            SELECT l.status, l.valor_real, l.valor_esperado, l.data_recebimento,
                   l.receita_id AS item_id, r.nome, r.tipo, r.dia_recebimento
            FROM lancamento_receita l JOIN receita r ON r.id = l.receita_id
            WHERE l.mes_ref = ? AND (r.ativo = 1 OR l.status != 'nao_encontrado')
              AND (r.recorrencia = 'fixa' OR l.status != 'nao_encontrado')""", (mes_ref,)):
        if l['status'] == 'recebido' or l['tipo'] in TIPOS_RESGATE:
            continue
        if l['status'] == 'previsto':
            valor = l['valor_real'] or l['valor_esperado'] or 0
            por(l['data_recebimento'], abs(valor), l['nome'], 'agendado')
        else:
            valor = proj_r.get(l['item_id'], 0) or 0
            if valor > 0:
                d = _data_do_dia(l['dia_recebimento'], ini, fim)
                por(d.isoformat() if d else ini, abs(valor), l['nome'], 'previsto')

    return eventos


def _vencimentos(conn, ini: str, fim: str):
    """Investimentos que vencem dentro da janela.

    Papel que vence vira dinheiro na conta sem ninguém pedir — é resgate certo,
    e o plano precisa contar com ele antes de sugerir qualquer outro. Usa a
    posição mais recente importada.
    """
    ultima = conn.execute('SELECT MAX(data_posicao) d FROM investimento').fetchone()['d']
    if not ultima:
        return []
    return [{
        'data': r['data_vencimento'],
        'valor': round(r['saldo_valorizado'] or r['saldo_bruto_mtm']
                       or r['saldo_bruto_accrual'] or 0, 2),
        'rotulo': f"{r['produto']} {r['ativo'] or ''}".strip(),
    } for r in conn.execute(
        'SELECT produto, ativo, data_vencimento, saldo_valorizado, saldo_bruto_mtm, '
        'saldo_bruto_accrual FROM investimento '
        'WHERE data_posicao = ? AND data_vencimento BETWEEN ? AND ? '
        'ORDER BY data_vencimento', (ultima, ini, fim))]


def _planejar(saldo0: float, reserva: float, dias: list, quantos: int):
    """Escolhe as datas e os valores dos resgates.

    A ideia em duas partes:

    **1. Quanto precisa ter sido resgatado até cada dia.** Simulando o mês sem
    resgate nenhum, o saldo fura a reserva em certos dias. `preciso[d]` é o
    máximo acumulado dessa falta até `d` — máximo, e não a falta do dia, porque
    dinheiro resgatado não volta: se o saldo furou 3 mil no dia 5, esses 3 mil
    precisam ter entrado até o dia 5, mesmo que no dia 6 chegue o salário.

    Essa curva é não-decrescente por construção, e os dias em que ela sobe são
    os **prazos**: resgatar exatamente ali é o mais tarde possível, ou seja, o
    plano que deixa o dinheiro investido por mais tempo.

    **2. Encaixar nos resgates disponíveis.** Se há mais prazos que resgates,
    dois prazos viram um só — e o resgate vai para o **primeiro** deles, porque
    antecipar é seguro e atrasar não é. Funde sempre o par mais barato, medindo
    custo como `valor × dias de antecipação`: adiantar R$ 100 por 10 dias
    incomoda menos que adiantar R$ 20 mil por 1 dia.
    """
    saldo, preciso, pico = saldo0, [], 0.0
    for dia in dias:
        saldo += dia['delta']
        pico = max(pico, round(reserva - saldo, 2))
        preciso.append(max(0.0, pico))

    prazos = []
    anterior = 0.0
    for dia, acumulado in zip(dias, preciso):
        if acumulado - anterior > 0.005:
            prazos.append({'data': dia['data'], 'valor': round(acumulado - anterior, 2)})
            anterior = acumulado

    if not prazos:
        return [], 0.0

    # funde os prazos mais baratos até caber em `quantos`
    while len(prazos) > max(1, quantos):
        custos = [
            (prazos[i + 1]['valor'] *
             (date.fromisoformat(prazos[i + 1]['data']) - date.fromisoformat(prazos[i]['data'])).days,
             i)
            for i in range(len(prazos) - 1)
        ]
        _, i = min(custos)
        prazos[i] = {'data': prazos[i]['data'],
                     'valor': round(prazos[i]['valor'] + prazos[i + 1]['valor'], 2)}
        del prazos[i + 1]

    return prazos, round(preciso[-1], 2)


@bp.route('/resgates/plano')
def plano():
    mes_ref = request.args.get('mes', '')
    conn = get_db()
    dia_corte = int(_num(conn, 'dia_recebimento_salario', 26))
    if not mes_ref:
        mes_ref = competencia_da_data(date.today().isoformat(), dia_corte)
    ini, fim = periodo_competencia(mes_ref, dia_corte)

    saldo = _num(conn, 'saldo_conta', 0)
    reserva = _num(conn, 'reserva_desejada', 5000)
    quantos = max(1, int(_num(conn, 'resgates_por_mes', 2)))
    saldo_data = get_config_value(conn, 'saldo_data', '') or ini

    # A linha do tempo começa no saldo, não no início do mês: metade do mês já
    # aconteceu e está dentro dele. Nunca antes do início da competência.
    corte = max(date.fromisoformat(saldo_data), date.fromisoformat(ini))
    corte = min(corte, date.fromisoformat(fim))

    eventos = _eventos(conn, mes_ref, ini, fim, corte)
    vencimentos = _vencimentos(conn, corte.isoformat(), fim)
    conn.close()

    # vencimento é entrada certa de dinheiro, e ao mesmo tempo um resgate que
    # já está contratado — entra na linha do tempo e ocupa uma vaga
    for v in vencimentos:
        eventos.append({'data': v['data'], 'valor': v['valor'],
                        'rotulo': f"vence {v['rotulo']}", 'especie': 'vencimento'})

    por_dia = {}
    d = corte
    dfim = date.fromisoformat(fim)
    while d <= dfim:
        por_dia[d.isoformat()] = {'data': d.isoformat(), 'delta': 0.0, 'eventos': []}
        d += timedelta(days=1)
    for e in eventos:
        alvo = por_dia.get(e['data'])
        if alvo is None:                      # fora da janela: joga na ponta mais próxima
            alvo = por_dia[fim] if e['data'] > fim else por_dia[corte.isoformat()]
        alvo['delta'] += e['valor']
        alvo['eventos'].append(e)

    dias = [por_dia[k] for k in sorted(por_dia)]
    vagas = max(1, quantos - len(vencimentos))
    resgates, total = _planejar(saldo, reserva, dias, vagas)

    # saldo dia a dia já com o plano aplicado, para a tela desenhar a linha
    plano_por_dia = {r['data']: r['valor'] for r in resgates}
    corrente = saldo
    linha = []
    for dia in dias:
        entrada = plano_por_dia.get(dia['data'], 0.0)
        corrente += dia['delta'] + entrada
        linha.append({**dia, 'resgate': round(entrada, 2),
                      'saldo': round(corrente, 2),
                      'abaixo_reserva': corrente < reserva - 0.005})

    return jsonify({
        'ok': True, 'mes_ref': mes_ref, 'periodo': {'ini': ini, 'fim': fim},
        'saldo_inicial': round(saldo, 2), 'saldo_data': saldo_data,
        'reserva': round(reserva, 2),
        'resgates_por_mes': quantos, 'vagas_livres': vagas,
        'total_a_resgatar': total,
        'vencimentos': vencimentos,
        'resgates': resgates,
        'saldo_final': round(corrente, 2),
        'dias': linha,
    })
