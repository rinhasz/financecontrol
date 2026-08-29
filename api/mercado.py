"""Dados de mercado — a base separada que a valorização consome.

Fica apartada do cálculo de propósito (doc 16, fase 2): guardando o dado bruto
de cada dia, a valorização pode ser **refeita** sem depender de a fonte estar
no ar, e a memória de cálculo pode ser reconstruída e conferida.

Cada série é `(serie, data) -> valor`, e a busca só vai à rede quando o dia
pedido ainda não está na base.

| série | o que é | fonte |
|---|---|---|
| `DI` | taxa CDI do dia, em % ao dia | Banco Central, SGS série 12 |
| `IPCA` | variação do mês, em % | Banco Central, SGS série 433 |
| `ACAO:<ticker>` | fechamento ajustado | Yahoo Finance |
| `DEB:<código>` | PU indicativo | ANBIMA, mercado secundário de debêntures |

Não há fonte pública gratuita para PU de **CRI/CRA** — ver `valorizacao.py`,
que trata esse caso explicitamente em vez de fingir um preço.
"""
import re
from datetime import date, datetime, timedelta

from .db import feriados_bancarios

SGS = 'https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie}/dados'
YAHOO = 'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}.SA'
ANBIMA_DEB = 'https://www.anbima.com.br/informacoes/merc-sec-debentures/arqs/db{ddmmaa}.txt'

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
TIMEOUT = 25


def dia_util(d: date) -> bool:
    return d.weekday() < 5 and d not in feriados_bancarios(d.year)


def dias_uteis(ini: date, fim: date) -> list:
    """Dias úteis em (ini, fim] — o dia da posição já está valorizado nela."""
    out, d = [], ini + timedelta(days=1)
    while d <= fim:
        if dia_util(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def dias_uteis_no_mes(ano: int, mes: int) -> int:
    import calendar
    ultimo = calendar.monthrange(ano, mes)[1]
    return sum(1 for dia in range(1, ultimo + 1) if dia_util(date(ano, mes, dia)))


# ------------------------------------------------------------------ cache ---

def ler(conn, serie: str, data: str):
    row = conn.execute('SELECT valor FROM mercado_serie WHERE serie=? AND data=?',
                       (serie, data)).fetchone()
    return row['valor'] if row else None


def gravar(conn, serie: str, data: str, valor: float, fonte: str):
    conn.execute(
        'INSERT INTO mercado_serie (serie, data, valor, fonte) VALUES (?,?,?,?) '
        'ON CONFLICT(serie, data) DO UPDATE SET valor=excluded.valor, fonte=excluded.fonte',
        (serie, data, valor, fonte))


# ------------------------------------------------------------------ fontes ---

def _get(url, **kw):
    from .net import preferir_ipv4
    preferir_ipv4()
    import requests
    return requests.get(url, timeout=TIMEOUT, headers=UA, **kw)


def baixar_sgs(conn, serie_sgs: int, nome: str, ini: date, fim: date) -> int:
    """Séries do Banco Central. Devolve quantos dias novos entraram."""
    r = _get(SGS.format(serie=serie_sgs), params={
        'formato': 'json',
        'dataInicial': ini.strftime('%d/%m/%Y'),
        'dataFinal': fim.strftime('%d/%m/%Y')})
    r.raise_for_status()
    n = 0
    for item in r.json():
        d = datetime.strptime(item['data'], '%d/%m/%Y').date().isoformat()
        gravar(conn, nome, d, float(item['valor']), f'bcb-sgs-{serie_sgs}')
        n += 1
    return n


def baixar_acao(conn, ticker: str, ini: date, fim: date) -> int:
    """Fechamentos diários. Pede uma janela folgada porque o Yahoo devolve só
    pregões — dia sem negociação simplesmente não vem, e é assim que se
    descobre que não houve."""
    r = _get(YAHOO.format(ticker=ticker), params={
        'period1': int(datetime.combine(ini - timedelta(days=10), datetime.min.time()).timestamp()),
        'period2': int(datetime.combine(fim + timedelta(days=1), datetime.min.time()).timestamp()),
        'interval': '1d'})
    r.raise_for_status()
    res = r.json()['chart']['result'][0]
    fechamentos = res['indicators']['quote'][0]['close']
    n = 0
    for ts, fech in zip(res['timestamp'], fechamentos):
        if fech is None:
            continue
        d = datetime.fromtimestamp(ts).date().isoformat()
        gravar(conn, f'ACAO:{ticker}', d, float(fech), 'yahoo')
        n += 1
    return n


def baixar_debentures(conn, d: date) -> int:
    """Arquivo diário da ANBIMA: `Código@Nome@Venc@Índice@...@PU@...`.

    A ANBIMA só publica em dia com mercado, e nem todo dia fica disponível.
    Ausência não é erro — é dia sem preço, e quem chama decide o que fazer.
    """
    r = _get(ANBIMA_DEB.format(ddmmaa=d.strftime('%d%m%y')))
    if r.status_code != 200 or 'DOCTYPE' in r.text[:200]:
        return 0
    n = 0
    for linha in r.content.decode('latin-1').split('\n'):
        campos = linha.split('@')
        if len(campos) < 11 or not re.match(r'^[A-Z0-9]{6}$', campos[0].strip()):
            continue
        pu = campos[10].strip().replace('.', '').replace(',', '.')
        try:
            gravar(conn, f"DEB:{campos[0].strip()}", d.isoformat(), float(pu), 'anbima')
            n += 1
        except ValueError:
            continue
    return n


def garantir_series(conn, tickers: list, debentures: list, ini: date, fim: date) -> dict:
    """Busca o que falta para valorizar de `ini` a `fim`. Devolve o que cada
    fonte trouxe e o que falhou — a tela precisa poder dizer por que um papel
    não foi valorizado."""
    resultado = {'baixados': {}, 'erros': {}}

    def tenta(nome, fn):
        try:
            resultado['baixados'][nome] = fn()
        except Exception as e:
            resultado['erros'][nome] = str(e)[:120]

    # o CDI de um dia só sai no dia seguinte; a janela folgada evita buraco
    tenta('DI', lambda: baixar_sgs(conn, 12, 'DI', ini - timedelta(days=10), fim))
    tenta('IPCA', lambda: baixar_sgs(conn, 433, 'IPCA', ini - timedelta(days=120), fim))

    for t in tickers:
        tenta(f'ACAO:{t}', lambda t=t: baixar_acao(conn, t, ini, fim))

    if debentures:
        dias = [d for d in dias_uteis(ini - timedelta(days=1), fim)]
        tenta('DEB', lambda: sum(baixar_debentures(conn, d) for d in dias))

    return resultado
