"""Fatura do cartão Porto Seguro, emitida pela PORTOSEG (doc 17).

Duas páginas, e a segunda é só boleto: os lançamentos cabem todos na primeira,
no `DEMONSTRATIVO DE DESPESAS`, que é a coluna da **direita**. A coluna da
esquerda (resumo e encargos) se achata junto no texto corrido:

```
Saldo 3.071,20 27/07/2 PORTO SEGURO AUTO PA/03 412,32
      ^^^^^^^^ esquerda            lançamento ^^^^^^
```

Por isso aqui também se lê por coordenada, não por linha de texto. A geometria é
rígida e igual nas seis linhas: data em x0=290, descrição em x0=324, parcela em
x0≈403-449, valor right-aligned em x1=553. O resumo da esquerda tem valores em
x1=282, bem longe — e começa antes de x0=285, de modo que a faixa da direita o
exclui inteiro.

## Quatro diferenças que nenhum outro emissor tinha

| achado | consequência |
|---|---|
| **duas notações de parcela**: `PA/03` e `09/12` | `PA/NN` dá o número **sem o total** |
| **data truncada**: `27/07/2` | lê-se `DD/MM`; o ano sai do vencimento |
| valores **sem `R$`** no cabeçalho | padrões que exigem `R$` não casam |
| **menos à esquerda**: `-2.693,62` | o Bradesco põe à direita |

> **`09/12` é indistinguível de uma data pelo formato** — o que as separa é só o
> x (429 contra 290). Um parser de linha de texto confundiria as duas.

> **`PORTO SEGURO AUTO` é compra de seguro.** Isso proíbe usar `SEGURO` como
> marca de encargo, senão a compra viraria encargo. Encargo aqui é só anuidade,
> tarifa, IOF, juros, multa e mora.

## Conferência

As despesas sem o pagamento somam 3.071,20, e o PDF confirma por três caminhos:
`Despesas/Debitos (+)`, `PAGAMENTO TOTAL` e o total do portador
(`RAFAEL INHASZ - NR.3151: R$ 3.071,20`).

Um quarto número, `Total Nacional -2.316,04`, **não fecha** e não é usado: a
hipótese é que subtraia o pagamento duas vezes, mas hipótese não é leitura.
"""
import io
import re

from .cartoes import _num, _data_compra

# Faixas de x medidas no PDF real.
X_DATA = (285, 320)      # x0 da data (290,0)
X_DESC = (320, 470)      # x0 da descrição (324,0) e da parcela (403-449)
X_VALOR = (545, 560)     # x1 do valor (553,0)

RE_MOEDA = re.compile(r'^-?\d{1,3}(?:\.\d{3})*,\d{2}$')
# `27/07/2`: o ano vem truncado em um dígito
RE_DATA_TRUNC = re.compile(r'^(\d{2}/\d{2})/\d?$')
# as duas notações, na mesma fatura
RE_PARCELA_PA = re.compile(r'^PA/(\d{1,2})$', re.I)
RE_PARCELA_NN = re.compile(r'^(\d{1,2})/(\d{1,2})$')

RE_PAGAMENTO = re.compile(r'PAGAMENTO|CREDITO|ESTORNO|DEVOLU', re.I)
# `SEGURO` fica DE FORA: `PORTO SEGURO AUTO` é compra, não encargo.
RE_ENCARGO = re.compile(r'ANUIDADE|TARIFA|IOF|JUROS|MULTA|MORA|ENCARGO', re.I)


def parse(conteudo: bytes, arquivo: str = '') -> dict:
    import pdfplumber
    paginas, linhas_txt = [], []
    with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
        for pg in pdf.pages:
            ws = pg.extract_words()
            if not ws:
                continue
            paginas.append(ws)
            linhas_txt.extend((pg.extract_text() or '').split('\n'))
    texto = ' '.join(linhas_txt)

    cab = _cabecalho(texto, arquivo)
    itens = []
    for ws in paginas:
        itens.extend(_lancamentos(ws, cab.get('data_vencimento')))
    itens.sort(key=lambda i: i['data_compra'] or '')

    final = cab.get('final')
    return {'cabecalho': cab, 'itens': itens,
            'totais_cartao': ({final: cab['total_portador']}
                              if final and cab.get('total_portador') is not None else {}),
            'conferencia': _conferir(itens, cab)}


def _cabecalho(texto: str, arquivo: str) -> dict:
    def achar(padrao, grupo=1):
        m = re.search(padrao, texto, re.I)
        return m.group(grupo) if m else None

    # `4152 75** **** 3151 08/10/2026` — final e vencimento na mesma linha, o
    # que os pega juntos e sem ambiguidade (o documento repete a data no boleto).
    m = re.search(r'\d{4}\s*\d{2}\*{2}\s*\*{4}\s*(\d{4})\s+(\d{2}/\d{2}/\d{4})', texto)
    final = m.group(1) if m else achar(r'NR\.?(\d{4})')
    venc = m.group(2) if m else achar(r'VENCIMENTO[^\d]{0,40}(\d{2}/\d{2}/\d{4})')
    if venc:
        d, mes, a = venc.split('/')
        venc = f'{a}-{mes}-{d}'

    # Aqui os valores vêm SEM `R$`, ao contrário de todos os outros emissores.
    pag = re.search(r'PAGAMENTO\s*M[ÍI]NIMO\s*PAGAMENTO\s*TOTAL\s*'
                    r'([\d.]+,\d{2})\s+([\d.]+,\d{2})', texto, re.I)
    lim = re.search(r'LIMITE\s*DE\s*CR[ÉE]DITO\s*LIMITE\s*DE\s*SAQUE\s*'
                    r'([\d.]+,\d{2})\s+([\d.]+,\d{2})', texto, re.I)

    total = _num(pag.group(2)) if pag else _num(
        achar(r'Total\s*desta\s*fatura:\s*R\$\s*([\d.]+,\d{2})'))

    cab = {
        'cartao': 'Porto Seguro',
        'final': final,
        'data_vencimento': venc,
        'situacao': 'fechada',
        'data_extrato': None,
        'arquivo': arquivo,
        'total_fatura': total,
        'pagamento_minimo': _num(pag.group(1)) if pag else None,
        'limite_total': _num(lim.group(1)) if lim else None,
        # as três fontes que concordam entre si
        'total_despesas': _num(achar(r'Despesas/D[ée]bitos\s*\(\+\)\s*([\d.]+,\d{2})')),
        'total_portador': _num(achar(r'NR\.\d{4}:\s*R\$\s*([\d.]+,\d{2})')),
        'saldo_anterior': _num(achar(r'Saldo\s*da\s*fatura\s*anterior\s*([\d.]+,\d{2})')),
        # esta fatura não publica total de parcelas futuras
        'total_proxima_fatura': None,
        'total_proximas_faturas': None,
    }
    cab['total_lancamentos'] = (cab['total_despesas'] if cab['total_despesas'] is not None
                                else cab['total_fatura'])
    if venc:
        cab['mes_ref'] = venc[:7]
    return cab


def _lancamentos(palavras: list, venc) -> list:
    """Um lançamento por valor em x1≈553 que tenha uma data em x0≈290.

    Exigir as duas âncoras é o que descarta o total do portador
    (`NR.3151: R$ 3.071,20`, cujo valor fica em x1=437) e as linhas de
    `Total Nacional` / `Total no Exterior`, que têm valor na coluna certa mas
    nenhuma data.
    """
    linhas = {}
    for w in palavras:
        linhas.setdefault(round(w['top']), []).append(w)

    itens = []
    for top in sorted(linhas):
        ws = sorted(linhas[top], key=lambda w: w['x0'])
        val = next((w for w in ws
                    if X_VALOR[0] <= w['x1'] <= X_VALOR[1] and RE_MOEDA.match(w['text'])), None)
        data = next((w for w in ws
                     if X_DATA[0] <= w['x0'] <= X_DATA[1] and RE_DATA_TRUNC.match(w['text'])), None)
        if not val or not data:
            continue

        pn = pt = None
        partes = []
        for w in ws:
            if not (X_DESC[0] <= w['x0'] < X_DESC[1]) or w is val:
                continue
            mpa = RE_PARCELA_PA.match(w['text'])
            if mpa:
                # `PA/03`: o número da parcela, sem o total. Sem saber quantas
                # faltam, este papel não entra no "ainda vai chegar".
                pn = int(mpa.group(1))
                continue
            mnn = RE_PARCELA_NN.match(w['text'])
            if mnn:
                pn, pt = int(mnn.group(1)), int(mnn.group(2))
                continue
            partes.append(w['text'])
        desc = ' '.join(partes).strip()
        if not desc:
            continue

        if RE_PAGAMENTO.search(desc):
            secao = 'pagamento'
        elif RE_ENCARGO.search(desc):
            secao = 'encargo'
        else:
            secao = 'lancamento'

        itens.append({
            'secao': secao,
            'portador': None,
            'cartao_final': None,
            'internacional': False,
            'data_compra': _data_compra(RE_DATA_TRUNC.match(data['text']).group(1), venc),
            'estabelecimento': desc,
            'valor': _num(val['text']),
            'parcela_n': pn,
            'parcela_total': pt,
            'categoria_fatura': None,
            'cidade': None,
        })
    return itens


def _conferir(itens: list, cab: dict) -> dict:
    """Confere contra os três números que concordam, nunca contra o quarto.

    `Total Nacional -2.316,04` não fecha com nada que se possa explicar — a
    hipótese é que subtraia o pagamento duas vezes. Conferir contra ele faria a
    importação recusar uma fatura que está correta.
    """
    lanc = [i for i in itens if i['secao'] == 'lancamento']
    enc = [i for i in itens if i['secao'] == 'encargo']
    pagos = [i for i in itens if i['secao'] == 'pagamento']

    # anuidade é encargo, mas entra no total de despesas que o PDF publica
    soma = round(sum(i['valor'] or 0 for i in lanc + enc), 2)
    alvo = cab.get('total_lancamentos')
    dif = round(soma - alvo, 2) if alvo is not None else None

    cartoes = []
    port = cab.get('total_portador')
    if port is not None:
        cartoes.append({'final': cab.get('final'), 'esperado': port,
                        'lido': soma, 'diferenca': round(soma - port, 2)})

    return {
        'cartoes': cartoes,
        'itens_lancamento': len(lanc),
        'itens_encargo': len(enc),
        'itens_pagamento': len(pagos),
        'itens_proxima_fatura': 0,
        'soma_lancamentos': soma,
        'total_lancamentos_pdf': alvo,
        'diferenca_lancamentos': dif,
        'soma_proximas': 0.0,
        'total_proxima_fatura_pdf': None,
        'diferenca_proximas': None,
        'ok': ((dif is None or abs(dif) < 0.01)
               and all(abs(x['diferenca']) < 0.01 for x in cartoes)),
    }
