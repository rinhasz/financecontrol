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
| CRI / CRA | accrual pelo indexador — **não** é como o banco marca (ver abaixo) | — |
| LCI, LCA, LIG, CDB | accrual pelo indexador contratado | fica sem valorizar, com o motivo |

Papel sem indexador reconhecido **não é valorizado** e diz por quê. Inventar um
rendimento seria pior que mostrar a posição parada.

## CRI/CRA: sabemos como o banco marca, e não é isto

O Manual de Marcação a Mercado do Itaú Securities Services (p. 38) marca CRI/CRA
IPCA+ com projeção de IPCA da ANBIMA + **cupom da curva de NTN-B** + spread de
crédito do Comitê WMS. Conferido em 28/08: a curva caiu 4,6 bps e o banco moveu
o CRA +0,190% além do carrego — coerente com duration de ~4,1 anos.

Aqui é feito **accrual**, que captura só o carrego. Reproduzir a marcação exige
por papel o cronograma de amortização (para a duration) e o spread implícito,
e nenhum dos dois vem no arquivo de posição. Ver doc 16.
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
    """Fator de um dia útil, base 252 — convenção dos pós-fixados em CDI."""
    return (1 + taxa_aa / 100) ** (1 / 252)


def _potencia_365(taxa_aa: float) -> float:
    """Fator de um dia corrido, base 365 — convenção dos prefixados e do juro
    real dos indexados a IPCA nestes produtos.

    Conferido contra o extrato: LCI e LIG prefixadas rendem exatamente
    `(1 + taxa)^(dias_corridos/365)`. Com base 252 o cálculo ficava R$ 28,86
    acima do banco numa LCI de R$ 61,9 mil; com 365, R$ 0,47.
    """
    return (1 + taxa_aa / 100) ** (1 / 365)


def fator_do_dia(inv: dict, d: date, conn, util: bool) -> tuple:
    """`(fator, metodo, detalhe)` de um papel num dia.

    `util` diz se é dia útil: o pós-DI só rende em dia útil (base 252), o
    prefixado rende todo dia (base 365). É a razão de a valorização caminhar
    por dia corrido e decidir aqui.

    `fator = None` significa "não deu para valorizar hoje" — e o detalhe diz o
    motivo, que é o que a tela mostra em vez de um número inventado.
    """
    iso = d.isoformat()
    indexador = (inv.get('indexador') or '').upper()

    if indexador == 'DI':
        if not util:
            return 1.0, 'di', 'fim de semana ou feriado — CDI não rende'
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
        dias = mercado.dias_no_mes(d.year, d.month)
        fator_vna = (1 + ipca / 100) ** (1 / dias)
        taxa = inv.get('taxa') or 0
        # accrual do VNA, não marcação a mercado — mas a conciliação contra o
        # extrato (doc 16) mostrou que, para estes papéis, o banco também
        # acrua: com a projeção certa o desvio cai a R$ 2 em R$ 90 mil.
        return fator_vna * _potencia_365(taxa), 'ipca', \
            (f'IPCA {ipca}% ({ref}) pro-rata em {dias} dias corridos'
             f' + {taxa}% a.a. em base 365 · accrual do VNA')

    if indexador == 'PRE' or (indexador == '' and inv.get('taxa')):
        taxa = inv.get('taxa')
        if not taxa:
            return None, 'parado', 'prefixado sem taxa cadastrada'
        return _potencia_365(taxa), 'pre', f'{taxa}% a.a. em base 365 (dias corridos)'

    return None, 'parado', 'sem indexador cadastrado'


def _cdi_vigente(conn, d: date) -> tuple:
    """`(taxa, data_da_taxa)` — o CDI do dia, ou o último conhecido antes dele."""
    row = conn.execute(
        "SELECT data, valor FROM mercado_serie WHERE serie='DI' AND data<=? "
        'ORDER BY data DESC LIMIT 1', (d.isoformat(),)).fetchone()
    return (row['valor'], row['data']) if row else (None, None)


def _ipca_vigente(conn, d: date) -> tuple:
    """IPCA que corrige o VNA no dia `d` — `(valor, referência)`.

    O índice do próprio mês, quando já fechado. Enquanto não fechou (o IBGE
    divulga por volta do dia 10 do mês seguinte), vale a **projeção** do mês, que
    é o que a ANBIMA usa no VNA e o que o banco mostra no extrato. Carregar o
    último índice fechado — o que se fazia aqui antes — erra o sinal em mês de
    virada: em agosto/2026 o banco embutia -0,205% e julho tinha fechado a
    +0,07%, uma diferença de R$ 23,99 numa LIG de R$ 90 mil.

    Só se não houver nem um nem outro é que se carrega o último fechado.
    """
    mes = d.replace(day=1).isoformat()
    fechado = conn.execute(
        "SELECT valor FROM mercado_serie WHERE serie='IPCA' AND data=?",
        (mes,)).fetchone()
    if fechado:
        return fechado['valor'], mes[:7]

    proj = conn.execute(
        "SELECT valor FROM mercado_serie WHERE serie='IPCA_PROJ' AND data=?",
        (mes,)).fetchone()
    if proj:
        return proj['valor'], f'{mes[:7]}, projeção Focus'

    row = conn.execute(
        "SELECT data, valor FROM mercado_serie WHERE serie='IPCA' AND data<=? "
        'ORDER BY data DESC LIMIT 1', (d.isoformat(),)).fetchone()
    return (row['valor'], f"{row['data'][:7]}, sem projeção") if row else (None, None)


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


def _passo(conn, inv_id: int, d: date, pu_ant: float, fator: float, pu_novo: float,
           qtd: float, metodo: str, detalhe: str, fluxo: float = 0.0) -> None:
    """Grava um dia da memória de cálculo.

    Existe para que `variacao` saia sempre do mesmo lugar: são três caminhos que
    gravam um passo (preço de mercado, accrual, e o dia parado), e derivar a
    variação em cada um convidava a divergirem.

    `variacao` é o rendimento do dia em reais. Hoje é exatamente a diferença de
    saldo, porque não há aplicação nem resgate; quando houver, o rendimento passa
    a ser esta variação líquida do fluxo do dia — e é por isso que ela fica
    gravada por dia, e não recalculada a partir das pontas.
    """
    conn.execute(
        'INSERT INTO valorizacao (investimento_id, data, pu_anterior, fator, pu, '
        'saldo, variacao, fluxo, metodo, detalhe) VALUES (?,?,?,?,?,?,?,?,?,?)',
        (inv_id, d.isoformat(), pu_ant, fator, pu_novo, pu_novo * qtd,
         (pu_novo - pu_ant) * qtd, fluxo, metodo, detalhe))


def _movimentos_por_papel(conn) -> dict:
    """`{(produto, data_aplicacao, data_vencimento): [movimentos]}`.

    A chave é de negócio, e não o id do investimento, porque a posição é
    reimportada com ids novos a cada foto — amarrar pelo id faria o movimento
    perder o papel na importação seguinte.
    """
    out = {}
    for r in conn.execute('SELECT * FROM movimento_investimento ORDER BY data'):
        out.setdefault((r['produto'], r['data_aplicacao'], r['data_vencimento']), []).append(dict(r))
    return out


def _aplicar_movimento(mov: dict, pu: float, qtd: float):
    """Ajusta a quantidade do papel no dia do movimento.

    Mexe na **quantidade**, e não no saldo: o saldo é sempre `pu × quantidade`,
    então reduzir a quantidade é o jeito de o resgate sobreviver a todos os dias
    seguintes sem precisar de nenhum caso especial adiante.

    Aplicado **antes** do fator do dia, e não depois. É assim que o banco faz:
    conferido no extrato de 01/09, `(233.746,39 − 28.000,73) × 1,00055 =
    205.859,16`, o valor que ele publica. O rendimento do dia incide sobre o que
    sobrou, o que é o certo — quem resgatou de manhã não perde o dia inteiro.

    `extrato` é a exceção e **não passa por aqui**: o número publicado pelo banco
    já é o fechamento do dia, com o rendimento dentro. Aplicá-lo antes do fator
    renderia o dia duas vezes — foi o que aconteceu no primeiro teste, R$ 99,97
    a mais. Ver `_aplicar_movimento_pos`.
    """
    saldo = pu * qtd
    metodo = mov.get('metodo') or 'proporcional'

    if metodo == 'credito':
        saldo_novo = saldo - (mov['valor_bruto'] or 0)
    else:
        # proporcional: tira do valor a mesma fatia que saiu do principal
        antes = mov.get('valor_anterior') or 0
        principal = mov['valor_principal'] or 0
        base = (mov.get('valor_novo') or 0)
        # a razão é recalculada a partir do que foi gravado no movimento, para o
        # resultado não depender de o saldo de hoje ter mudado desde o import
        razao = (base / antes) if antes else 1.0
        saldo_novo = saldo * razao

    saldo_novo = max(0.0, saldo_novo)
    qtd_nova = saldo_novo / pu if pu else 0.0
    return qtd_nova, round(saldo_novo - saldo, 2)


def _aplicar_movimento_pos(mov: dict, pu: float, qtd: float):
    """O método `extrato`, aplicado **depois** do fator do dia.

    O valor que o banco publica na seção "Posição em" já é o fechamento — tem o
    rendimento do dia dentro. Encaixá-lo aqui é o que faz o número da tela ser,
    literalmente, o número do extrato.
    """
    if (mov.get('metodo') or '') != 'extrato' or not mov.get('valor_novo'):
        return qtd, 0.0
    saldo = pu * qtd
    saldo_novo = max(0.0, mov['valor_novo'])
    return (saldo_novo / pu if pu else 0.0), round(saldo_novo - saldo, 2)


def valorizar(conn, data_posicao: str, ate: date = None) -> dict:
    """Caminha da posição até hoje, gravando a memória de cálculo."""
    hoje = ate or date.today()
    d0 = datetime.strptime(data_posicao, '%Y-%m-%d').date()
    dias = mercado.dias_corridos(d0, hoje)

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

    movimentos = _movimentos_por_papel(conn)

    valorizados = parados = 0
    for inv in itens:
        pu = _pu_base(inv)
        qtd = inv.get('quantidade') or 0
        # resgates deste papel, por dia — aplicados durante a caminhada
        movs = {}
        for m in movimentos.get(
                (inv['produto'], inv.get('data_aplicacao'), inv.get('data_vencimento')), []):
            movs.setdefault(m['data'], []).append(m)
        # O que vale é o último dia **efetivo**, não o último dia do calendário:
        # o CDI de hoje só é divulgado amanhã, e dizer "valorizado até hoje"
        # quando o último fator aplicado é de anteontem seria mentira.
        ultimo_metodo, ultimo_detalhe, ultima_data = 'parado', 'posição sem movimento', data_posicao
        andou = False

        for d in dias:
            util = mercado.dia_util(d)

            # Resgate entra ANTES do fator do dia: o rendimento incide sobre o
            # que sobrou. Ver _aplicar_movimento — é o que reproduz o extrato.
            do_dia = movs.get(d.isoformat(), [])
            fluxo_do_dia, nota_mov = 0.0, ''
            for m in do_dia:
                if (m.get('metodo') or '') == 'extrato':
                    continue                      # entra depois do fator
                qtd, delta = _aplicar_movimento(m, pu, qtd)
                fluxo_do_dia += delta
                nota_mov += f" · {m['tipo']} de {abs(delta):,.2f} ({m['metodo']})"

            pu_mercado, metodo, detalhe = _pu_de_mercado(conn, inv, d)
            if metodo in ('mercado', 'anbima'):
                if pu_mercado is None:
                    # sem preço no dia: o papel não some, mantém o último
                    _passo(conn, inv['id'], d, pu, 1.0, pu, qtd, metodo,
                           detalhe + nota_mov, fluxo_do_dia)
                    continue
                fator = pu_mercado / pu if pu else 1.0
                pu_novo = pu_mercado
            else:
                fator, metodo, detalhe = fator_do_dia(inv, d, conn, util)
                if fator is None:
                    _passo(conn, inv['id'], d, pu, 1.0, pu, qtd, 'parado',
                           detalhe + nota_mov, fluxo_do_dia)
                    continue
                pu_novo = pu * fator

            for m in do_dia:
                qtd, delta = _aplicar_movimento_pos(m, pu_novo, qtd)
                if delta:
                    fluxo_do_dia += delta
                    nota_mov += f" · {m['tipo']} de {abs(delta):,.2f} (extrato)"

            _passo(conn, inv['id'], d, pu, fator, pu_novo, qtd, metodo,
                   detalhe + nota_mov, fluxo_do_dia)
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
            'dias_corridos': len(dias),
            'dias_uteis_reais': sum(1 for d in dias if mercado.dia_util(d)),
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
