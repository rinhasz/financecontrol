"""Valorização diária da posição (doc 16, fase 2).

Parte da última posição importada e caminha **dia útil a dia útil** até hoje,
gravando cada passo. O valor de um papel é sempre `PU × quantidade`; o que muda
por produto é como o PU anda de um dia para o outro.

A memória de cálculo é gravada linha a linha (`valorizacao`) de propósito: com
o dado de mercado numa base à parte (`mercado.py`) e o passo diário guardado,
qualquer número da tela pode ser refeito e conferido. Sem isso, um saldo
estranho seria impossível de auditar.

Convenção de mercado brasileira: **base 252 dias úteis**. Fim de semana e
feriado bancário não rendem, e é por isso que se caminha por dia útil em vez de
por dia corrido.

---

## Fatores por indexador

**Pós-fixado em % do CDI** — padrão CETIP/B3 para CDB, LCI, LCA e LIG:

    fator_dia = 1 + DI_dia × (p / 100)

`DI_dia` é a taxa do CDI do dia (BCB SGS série 12, já expressa em % ao dia, que
é `(1 + DI_anual)^(1/252) − 1`), e `p` é o percentual contratado do CDI. Para
`p = 100` o fator é o próprio fator DI do dia.

**Prefixado**:

    fator_dia = (1 + taxa/100) ^ (1/252)

**IPCA + taxa**:

    fator_dia = (1 + IPCA_mês/100) ^ (1/du_mês) × (1 + taxa/100) ^ (1/252)

O primeiro termo é o VNA rendendo o IPCA do mês distribuído pro-rata pelos dias
úteis; o segundo é o juro real.

> **Simplificação assumida.** A ANBIMA corrige o VNA por aniversário no dia 15 e
> usa projeção de IPCA para o mês corrente; aqui se usa o último IPCA divulgado,
> distribuído pro-rata. Para janelas de poucos dias — o caso desta tela — a
> diferença é de centavos; para janelas longas, degrada. Está registrado porque
> o número precisa ser interpretável, não porque é irrelevante.

---

## Fontes por tipo de produto

| produto | PU do dia vem de | se faltar |
|---|---|---|
| ação / ETF | fechamento (Yahoo) | mantém o último fechamento conhecido |
| debênture | PU indicativo ANBIMA | cai para accrual pelo indexador |
| CRI / CRA | — não há fonte pública gratuita | accrual pelo indexador, marcado como aproximação |
| LCI, LCA, LIG, CDB | accrual pelo indexador contratado | fica sem valorizar, com o motivo |

Papel sem indexador reconhecido **não é valorizado** e diz por quê. Inventar um
rendimento seria pior que mostrar a posição parada.
"""
from datetime import date, datetime

from . import mercado
from .db import db, get_db

METODO_LABEL = {
    'di': '% do CDI',
    'pre': 'prefixado',
    'ipca': 'IPCA + juro real',
    'mercado': 'preço de fechamento',
    'anbima': 'PU ANBIMA',
    'parado': 'sem valorizar',
}


def _potencia_252(taxa_aa: float) -> float:
    return (1 + taxa_aa / 100) ** (1 / 252)


def fator_do_dia(inv: dict, d: date, conn) -> tuple:
    """`(fator, metodo, detalhe)` de um papel num dia útil.

    `fator = None` significa "não deu para valorizar hoje" — e o detalhe diz o
    motivo, que é o que a tela mostra em vez de um número inventado.
    """
    iso = d.isoformat()
    indexador = (inv.get('indexador') or '').upper()

    if indexador == 'DI':
        di, ref = _cdi_vigente(conn, d)
        if di is None:
            return None, 'parado', f'sem CDI publicado até {iso}'
        # O CDI de um dia só é divulgado no dia seguinte, mas o banco já credita
        # no próprio dia. Repetir o último conhecido é a prática correta: o CDI
        # só muda em reunião do Copom, e mesmo aí a diferença de um dia é de
        # centésimos. Parar sem valorizar é que produzia um número **errado** —
        # foi o que deixava uma LCA 340 reais atrás do extrato.
        estimado = '' if ref == iso else f' (CDI de {ref} repetido, {iso} ainda não divulgado)'
        p = inv.get('perc_indexador')
        if p is None:
            # DI + spread (típico de debênture/CRA): juro real sobre o CDI
            taxa = inv.get('taxa') or 0
            fator = (1 + di / 100) * _potencia_252(taxa)
            return fator, 'di', f'CDI {di:.6f}%/dia + {taxa}% a.a.{estimado}'
        fator = 1 + (di / 100) * (p / 100)
        return fator, 'di', f'CDI {di:.6f}%/dia × {p}%{estimado}'

    if indexador == 'IPCA':
        ipca, ref = _ipca_vigente(conn, d)
        if ipca is None:
            return None, 'parado', 'sem IPCA divulgado'
        du = mercado.dias_uteis_no_mes(d.year, d.month) or 21
        fator_vna = (1 + ipca / 100) ** (1 / du)
        taxa = inv.get('taxa') or 0
        return fator_vna * _potencia_252(taxa), 'ipca', \
            f'IPCA {ipca}% ({ref}) pro-rata em {du} du + {taxa}% a.a.'

    if indexador == 'PRE' or (indexador == '' and inv.get('taxa')):
        taxa = inv.get('taxa')
        if not taxa:
            return None, 'parado', 'prefixado sem taxa cadastrada'
        return _potencia_252(taxa), 'pre', f'{taxa}% a.a. em base 252'

    return None, 'parado', 'sem indexador cadastrado'


def _cdi_vigente(conn, d: date) -> tuple:
    """`(taxa, data_da_taxa)` — o CDI do dia, ou o último conhecido antes dele."""
    row = conn.execute(
        "SELECT data, valor FROM mercado_serie WHERE serie='DI' AND data<=? "
        'ORDER BY data DESC LIMIT 1', (d.isoformat(),)).fetchone()
    return (row['valor'], row['data']) if row else (None, None)


def _ipca_vigente(conn, d: date) -> tuple:
    """Último IPCA divulgado até a data. A série do BCB é mensal e sai com
    atraso, então o mês corrente costuma usar o índice do mês anterior."""
    row = conn.execute(
        "SELECT data, valor FROM mercado_serie WHERE serie='IPCA' AND data<=? "
        'ORDER BY data DESC LIMIT 1', (d.isoformat(),)).fetchone()
    return (row['valor'], row['data'][:7]) if row else (None, None)


def _pu_de_mercado(conn, inv: dict, d: date) -> tuple:
    """PU observado, para os papéis que têm preço. `(pu, metodo, detalhe)` ou
    `(None, ...)` quando não houve preço naquele dia."""
    iso = d.isoformat()
    if inv['produto'] == 'ACAO' and inv.get('ativo'):
        pu = mercado.ler(conn, f"ACAO:{inv['ativo']}", iso)
        if pu is not None:
            return pu, 'mercado', f'fechamento {iso}'
        return None, 'mercado', 'sem pregão / sem cotação'
    if inv['produto'] == 'DEB' and inv.get('ativo'):
        pu = mercado.ler(conn, f"DEB:{inv['ativo']}", iso)
        if pu is not None:
            return pu, 'anbima', f'PU indicativo {iso}'
        return None, 'anbima', 'sem PU ANBIMA no dia'
    return None, None, None


def _pu_base(inv: dict) -> float:
    """PU de partida.

    Para papel de emissão o extrato não traz PU (fase 1 grava `pu=1`), então o
    ponto de partida é o **saldo dividido pela quantidade** — assim
    `PU × quantidade` reproduz exatamente o saldo que o banco informou, que é o
    número que esta tela existe para conferir.
    """
    saldo = inv.get('saldo_bruto_mtm') or inv.get('saldo_bruto_accrual')
    qtd = inv.get('quantidade')
    if saldo and qtd:
        return saldo / qtd
    return inv.get('pu') or 1.0


def valorizar(conn, data_posicao: str, ate: date = None) -> dict:
    """Caminha da posição até hoje, gravando a memória de cálculo."""
    hoje = ate or date.today()
    d0 = datetime.strptime(data_posicao, '%Y-%m-%d').date()
    dias = mercado.dias_uteis(d0, hoje)

    itens = [dict(r) for r in conn.execute(
        'SELECT * FROM investimento WHERE data_posicao=?', (data_posicao,))]
    if not itens:
        return {'ok': False, 'msg': 'Nenhuma posição para valorizar'}

    tickers = sorted({i['ativo'] for i in itens if i['produto'] == 'ACAO' and i['ativo']})
    debs = sorted({i['ativo'] for i in itens if i['produto'] == 'DEB' and i['ativo']})
    fontes = mercado.garantir_series(conn, tickers, debs, d0, hoje) if dias else \
        {'baixados': {}, 'erros': {}}

    # recomeça a memória deste ciclo: valorizar de novo não pode empilhar
    ids = [i['id'] for i in itens]
    conn.execute(f"DELETE FROM valorizacao WHERE investimento_id IN ({','.join('?' * len(ids))})", ids)

    valorizados = parados = 0
    for inv in itens:
        pu = _pu_base(inv)
        qtd = inv.get('quantidade') or 0
        # O que vale é o último dia **efetivo**, não o último dia do calendário:
        # o CDI de hoje só é divulgado amanhã, e dizer "valorizado até hoje"
        # quando o último fator aplicado é de anteontem seria mentira.
        ultimo_metodo, ultimo_detalhe, ultima_data = 'parado', 'posição sem movimento', data_posicao
        andou = False

        for d in dias:
            pu_mercado, metodo, detalhe = _pu_de_mercado(conn, inv, d)
            if metodo in ('mercado', 'anbima'):
                if pu_mercado is None:
                    # sem preço no dia: o papel não some, mantém o último
                    conn.execute(
                        'INSERT INTO valorizacao (investimento_id, data, pu_anterior, fator, pu, '
                        'saldo, metodo, detalhe) VALUES (?,?,?,?,?,?,?,?)',
                        (inv['id'], d.isoformat(), pu, 1.0, pu, pu * qtd, metodo, detalhe))
                    continue
                fator = pu_mercado / pu if pu else 1.0
                pu_novo = pu_mercado
            else:
                fator, metodo, detalhe = fator_do_dia(inv, d, conn)
                if fator is None:
                    conn.execute(
                        'INSERT INTO valorizacao (investimento_id, data, pu_anterior, fator, pu, '
                        'saldo, metodo, detalhe) VALUES (?,?,?,?,?,?,?,?)',
                        (inv['id'], d.isoformat(), pu, 1.0, pu, pu * qtd, 'parado', detalhe))
                    continue
                pu_novo = pu * fator

            conn.execute(
                'INSERT INTO valorizacao (investimento_id, data, pu_anterior, fator, pu, '
                'saldo, metodo, detalhe) VALUES (?,?,?,?,?,?,?,?)',
                (inv['id'], d.isoformat(), pu, fator, pu_novo, pu_novo * qtd, metodo, detalhe))
            pu, andou = pu_novo, True
            ultimo_metodo, ultimo_detalhe, ultima_data = metodo, detalhe, d.isoformat()

        if andou and ultima_data < hoje.isoformat():
            ultimo_detalhe += ' · dado de mercado mais recente disponível'

        conn.execute(
            'UPDATE investimento SET data_valorizacao=?, pu_valorizado=?, '
            'saldo_valorizado=?, metodo_valorizacao=?, detalhe_valorizacao=? WHERE id=?',
            (ultima_data, pu, pu * qtd, ultimo_metodo, ultimo_detalhe, inv['id']))
        valorizados += 1 if andou else 0
        parados += 0 if andou else 1

    ultima = conn.execute(
        'SELECT MAX(data_valorizacao) d FROM investimento WHERE data_posicao=?',
        (data_posicao,)).fetchone()['d']
    return {'ok': True, 'data_posicao': data_posicao, 'data_valorizacao': ultima,
            'ate': hoje.isoformat(), 'dias_uteis': len(dias),
            'valorizados': valorizados, 'parados': parados, 'fontes': fontes}


def atualizar(ate: date = None) -> dict:
    with db() as conn:
        row = conn.execute('SELECT MAX(data_posicao) d FROM investimento').fetchone()
        if not row or not row['d']:
            return {'ok': False, 'msg': 'Importe uma posição antes de atualizar'}
        return valorizar(conn, row['d'], ate)


def memoria(investimento_id: int) -> list:
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM valorizacao WHERE investimento_id=? ORDER BY data',
        (investimento_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
