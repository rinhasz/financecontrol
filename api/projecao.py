"""Projeção do valor de cada despesa/receita a partir do histórico.

Todo item ativo tem, por definição, três números no mês:

    pago (ou recebido)   o que já saiu/entrou da conta
    agendado (previsto)  o que está marcado e ainda não aconteceu
    projetado            quanto se espera que custe/renda no total

`projetado` é calculado do histórico, e **como** se calcula é escolha por item
(`tipo_projecao`), porque a natureza do gasto muda o que é uma boa estimativa:
conta de luz pede sazonal, mensalidade pede média recente, IPTU pede o mês
correspondente dos anos anteriores.

A base é sempre a **visão consolidada** do mês: as ocorrências somadas e os
estornos abatidos. Projetar sobre lançamento cru contaria duas vezes uma despesa
cobrada em duas parcelas, e contaria um gasto que foi devolvido.
"""
from .db import get_config_value, periodo_competencia, dia_util_anterior

MEDIA_SIMPLES = 'media_simples'
MEDIA_MOVEL_6 = 'media_movel_6'
MEDIA_SAZONAL = 'media_sazonal'
# Não olha o histórico: vale o número cadastrado em `valor_projecao`, inclusive
# **zero**. É o caso da despesa que existe no catálogo mas não se espera que
# aconteça — uma média de meses antigos ficaria inflando o "a vencer" para
# sempre, e desativar o item apagaria o histórico dele.
VALOR_FIXO = 'valor_fixo'

TIPOS = [MEDIA_SIMPLES, MEDIA_MOVEL_6, MEDIA_SAZONAL, VALOR_FIXO]

# Como cada padrão de variabilidade já cadastrado se traduz numa projeção. É
# um ponto de partida razoável, não uma verdade: o usuário troca item a item.
PADRAO_PARA_PROJECAO = {
    'fixa': MEDIA_MOVEL_6,               # estável, mas com reajuste: os 6 últimos capturam
    'reajuste_anual': MEDIA_MOVEL_6,     # idem — a média longa ficaria presa no valor antigo
    'variavel_nao_sazonal': MEDIA_MOVEL_6,
    'variavel_sazonal': MEDIA_SAZONAL,
    'anual': MEDIA_SAZONAL,              # acontece num mês específico do ano
    'sem_dados': MEDIA_SIMPLES,
}


def mes_ref_de(data: str, dia_corte: int) -> str:
    """Competência a que uma data do extrato pertence.

    Inverso de `periodo_competencia`. A competência M vai de um dia do mês M-1
    até a véspera do mesmo dia em M, então uma data cai em M+1 assim que passa
    do corte do próprio mês.
    """
    import calendar
    from datetime import date

    d = date.fromisoformat(data)
    ano, mes = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    corte_deste_mes = dia_util_anterior(
        date(d.year, d.month, min(dia_corte, calendar.monthrange(d.year, d.month)[1])))
    return f'{ano}-{mes:02d}' if d >= corte_deste_mes else f'{d.year}-{d.month:02d}'


def series_consolidadas(conn, natureza: str) -> dict:
    """`{item_id: {mes_ref: valor_liquido}}` para todo o histórico.

    Calculado de uma vez para todos os itens: projetar 58 despesas com consultas
    por item seria dezenas de queries por tela.
    """
    from .motor_batimento import cfg
    c = cfg(natureza)
    dia_corte = int(get_config_value(conn, 'dia_recebimento_salario', '26'))

    series = {}

    def somar(item_id, mes_ref, valor):
        series.setdefault(item_id, {}).setdefault(mes_ref, 0.0)
        series[item_id][mes_ref] += valor

    for r in conn.execute(
            f"SELECT mes_ref, {c['fk']} as item_id, "
            'COALESCE(valor_real, valor_esperado) as v '
            f"FROM {c['lancamento']}"):
        somar(r['item_id'], r['mes_ref'], r['v'] or 0.0)

    # ocorrências que não couberam em lançamento (item esporádico, ou a 2ª
    # cobrança de um "mais de um por mês")
    for r in conn.execute(
            f"SELECT t.data, t.{c['fk']} as item_id, t.valor FROM transacao t "
            f"WHERE t.{c['fk']} IS NOT NULL AND t.tipo = ? "
            f"AND NOT EXISTS (SELECT 1 FROM {c['lancamento']} l WHERE l.transacao_id = t.id)",
            (c['tipo_tx'],)):
        somar(r['item_id'], mes_ref_de(r['data'], dia_corte), abs(r['valor']))

    # estornos abatem da despesa que anulam — o que voltou não custou
    if natureza == 'despesa':
        for r in conn.execute(
                'SELECT t.data, t.estorna_despesa_id as item_id, t.valor FROM transacao t '
                'JOIN receita x ON x.id = t.receita_id '
                "WHERE x.tipo = 'estorno' AND t.estorna_despesa_id IS NOT NULL"):
            somar(r['item_id'], mes_ref_de(r['data'], dia_corte), -abs(r['valor']))

    return series


def mes_corrente(conn) -> str:
    """Competência em que estamos hoje."""
    from datetime import date
    return mes_ref_de(date.today().isoformat(),
                      int(get_config_value(conn, 'dia_recebimento_salario', '26')))


def projetar(serie: dict, mes_ref: str, tipo: str, corrente: str = None,
             valor_fixo: float = None) -> float:
    """Valor projetado de um item para `mes_ref`, a partir da sua série.

    Com `tipo = VALOR_FIXO` o histórico não é consultado: vale `valor_fixo`,
    zero incluído. É a única forma de dizer "esta não vai acontecer" sem
    desativar o item e perder o histórico dele.

    Fora da conta ficam:

    - o **próprio mês projetado** e os posteriores — projetar um mês com ele
      mesmo é circular;
    - o **mês corrente**, que quase sempre está em andamento. Metade de um mês
      puxa a média para baixo e faria a previsão do mês seguinte encolher só
      porque hoje é dia 10.

    A exclusão do corrente **cede** quando é ele o único histórico que existe:
    sem isso a projeção seria zero, o que é pior que uma amostra imperfeita. É a
    mesma lógica do fallback da média sazonal.
    """
    if tipo == VALOR_FIXO:
        return float(valor_fixo or 0.0)

    passado = {m: v for m, v in serie.items() if m < mes_ref}
    if corrente:
        sem_corrente = {m: v for m, v in passado.items() if m != corrente}
        if sem_corrente:
            passado = sem_corrente
    if not passado:
        return 0.0

    if tipo == MEDIA_SAZONAL:
        mes = mes_ref.split('-')[1]
        iguais = [v for m, v in passado.items() if m.split('-')[1] == mes]
        if iguais:
            return sum(iguais) / len(iguais)
        # sem nenhum mês correspondente no histórico, cai para a média simples
        tipo = MEDIA_SIMPLES

    if tipo == MEDIA_MOVEL_6:
        ultimos = [passado[m] for m in sorted(passado)[-6:]]
        return sum(ultimos) / len(ultimos)

    return sum(passado.values()) / len(passado)


def manuais_do_mes(conn, natureza: str, mes_ref: str) -> dict:
    """`{item_id: valor}` corrigido à mão para este mês. Pode ser 0,00."""
    return {r['item_id']: r['valor'] for r in conn.execute(
        'SELECT item_id, valor FROM projecao_manual WHERE natureza=? AND mes_ref=?',
        (natureza, mes_ref))}


def projecoes_do_mes(conn, natureza: str, mes_ref: str) -> dict:
    """`{item_id: valor_projetado}` para os itens ativos do catálogo.

    Precedência, do mais específico para o mais genérico:

    1. **correção manual** daquele item naquele mês (`projecao_manual`);
    2. **valor fixo** do catálogo, quando `tipo_projecao = valor_fixo`;
    3. o **histórico**, pelo método cadastrado.

    Ponto único: resumo, consolidado, lista do mês e calculadora de resgate
    passam todos por aqui, então a correção manual vale em todos de uma vez.
    """
    from .motor_batimento import cfg
    c = cfg(natureza)

    series = series_consolidadas(conn, natureza)
    # Só itens fixos são projetados. Esporádico acontece quando acontece — a
    # média dos meses em que ele apareceu não diz nada sobre este mês, e somá-la
    # ao "a vencer" inflaria a necessidade de resgate com um gasto imaginário.
    itens = conn.execute(
        f"SELECT id, tipo_projecao, valor_projecao FROM {c['catalogo']} "
        "WHERE ativo = 1 AND recorrencia = 'fixa'").fetchall()

    corrente = mes_corrente(conn)
    manuais = manuais_do_mes(conn, natureza, mes_ref)
    out = {}
    for i in itens:
        # `is not None` e não `or`: uma correção de 0,00 é uma decisão do
        # usuário ("não vai acontecer"), não um valor ausente.
        if i['id'] in manuais:
            out[i['id']] = round(manuais[i['id']], 2)
        else:
            out[i['id']] = round(projetar(series.get(i['id'], {}), mes_ref,
                                          i['tipo_projecao'] or MEDIA_MOVEL_6,
                                          corrente, i['valor_projecao']), 2)
    return out


def realizado_do_mes(conn, natureza: str, mes_ref: str) -> dict:
    """`{item_id: {'pago': x, 'agendado': y}}` — o que já aconteceu no mês.

    "Pago" é dinheiro que já saiu da conta, e por isso já está refletido no
    saldo; "agendado" ainda vai sair. A distinção é o que faz a calculadora de
    resgate perguntar a coisa certa: o que falta cobrir é o que ainda **não**
    saiu.
    """
    from .motor_batimento import cfg
    c = cfg(natureza)
    dia_corte = int(get_config_value(conn, 'dia_recebimento_salario', '26'))
    ini, fim = periodo_competencia(mes_ref, dia_corte)

    out = {}

    def somar(item_id, chave, valor):
        out.setdefault(item_id, {'pago': 0.0, 'agendado': 0.0})[chave] += valor

    for r in conn.execute(
            f"SELECT t.{c['fk']} as item_id, t.valor, t.situacao FROM transacao t "
            f"WHERE t.{c['fk']} IS NOT NULL AND t.tipo = ? AND t.data BETWEEN ? AND ?",
            (c['tipo_tx'], ini, fim)):
        somar(r['item_id'], 'pago' if r['situacao'] == 'efetivada' else 'agendado',
              abs(r['valor']))

    # estorno devolve dinheiro que já tinha saído: abate do pago da despesa
    if natureza == 'despesa':
        for r in conn.execute(
                'SELECT t.estorna_despesa_id as item_id, t.valor FROM transacao t '
                'JOIN receita x ON x.id = t.receita_id '
                "WHERE x.tipo = 'estorno' AND t.estorna_despesa_id IS NOT NULL "
                'AND t.data BETWEEN ? AND ?', (ini, fim)):
            somar(r['item_id'], 'pago', -abs(r['valor']))

    return out
