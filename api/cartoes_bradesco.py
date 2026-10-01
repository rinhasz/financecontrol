"""Fatura do cartão Amazon, emitida pelo Bradesco (doc 17).

Três páginas: a primeira é cabeçalho, resumo e boleto; a segunda tem **todos** os
lançamentos; a terceira é só a vitrine de opções de parcelamento da fatura, sem
lançamento nenhum.

## A página 2 é de duas colunas

E elas se achatam juntas em `extract_text()` — a mesma armadilha do Itaú:

```
21/03 AMAZONMKTPLC*VOOLTINDU SAO PAULO(06/06) 21,65 Demais faturas R$ 1.360,26
                                                    ^^^^^^^^^^^^^^^^^^^^^^^^^^
                                                    isto é da coluna da direita
```

Aqui, porém, a separação é fácil: **todo** valor de lançamento fica em x1=288, e
nada da coluna direita aparece antes de x≈410. Um corte em 310 resolve, sem
precisar do agrupamento por cluster que o Itaú exigiu.

## Gramática de um lançamento

```
DD/MM  <descrição + cidade>[(PP/TT)]  [BRA]  9.999,99[-]
x0=49  x0=73                                 x1=288
```

| detalhe | por que importa |
|---|---|
| parcela **entre parênteses e colada na cidade**: `PAULO(04/17)` | nem `PP/TT` do Itaú nem "Parcela N de M" do Mercado Pago casam |
| **sinal de menos no fim**: `1.224,70-` | é pagamento recebido; lido como positivo, inverteria o saldo |
| sufixo `BRA` | marca de país, não parte do nome do estabelecimento |
| cidade embutida na descrição | sem separador confiável — ver abaixo |

A cidade fica **dentro** do `estabelecimento`, de propósito. Não há como separá-la
com segurança: o espaço entre nome e cidade varia de 2px (`AMAZONMKTPLC*VOOLTINDU
SAO`) a 25px (`G LUCCO    SAO`), então qualquer heurística de distância erraria. O
campo guarda o texto cru, como manda o doc 17, e quem produz o nome legível é a
normalização da IA — que também não se incomoda com a cidade sobrando.

## Conferência: duas provas independentes

A primeira é o total. O resumo da página 1 fecha assim:

```
Saldo anterior              1.224,70
(-) Créditos/Pagamentos     1.224,70-
(+) Compras/Débitos         3.602,26
(=) Total                   3.602,26
```

Então a soma dos lançamentos **sem o pagamento** tem de dar 3.602,26.

A segunda é melhor, porque valida uma suposição: as parcelas em andamento são
`(06/06)`, `(04/17)` e `(02/12)`, e supondo parcelas iguais faltam
`13 × 93,48 + 10 × 26,50 = 1.480,24` — exatamente o `Total para as próximas
faturas` que o banco publica. E a soma das próximas parcelas,
`93,48 + 26,50 = 119,98`, bate com o `Próxima fatura` dele. A estimativa de
parcelas iguais deixa de ser suposição e passa a ser conferida contra o emissor.

> Esta fatura **não traz lista itemizada** de parcelas futuras, só os totais. Por
> isso não há itens `secao='proxima_fatura'` aqui, e a conferência dessa parte é
> feita contra os parcelamentos derivados dos próprios lançamentos.
"""
import io
import re

from .cartoes import _num, _data_compra

# Faixas de x medidas no PDF real.
X_CORTE = 310        # nada à direita disto é lançamento
X_VALOR = (280, 295)  # x1 do valor
X_DATA = (44, 70)     # x0 da data
X_DESC = (70, 250)    # x0 da descrição

RE_MOEDA = re.compile(r'^(\d{1,3}(?:\.\d{3})*,\d{2})(-?)$')
RE_DATA_CURTA = re.compile(r'^\d{2}/\d{2}$')
# `PAULO(04/17)`: parcela entre parênteses, colada no fim da descrição
RE_PARCELA = re.compile(r'\((\d{1,2})/(\d{1,2})\)')
# o que não é compra: entra como `pagamento` e fica fora dos totais de consumo
RE_PAGAMENTO = re.compile(r'PAGAMENTO\s*RECEBIDO|CREDITO|ESTORNO|DEVOLU', re.I)


def parse(conteudo: bytes, arquivo: str = '') -> dict:
    import pdfplumber
    palavras, linhas_txt = [], []
    with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
        for pg in pdf.pages:
            ws = pg.extract_words()
            if not ws:
                continue
            palavras.append(ws)
            linhas_txt.extend((pg.extract_text() or '').split('\n'))
    texto = ' '.join(linhas_txt)

    def achar(padrao, grupo=1):
        m = re.search(padrao, texto, re.I)
        return m.group(grupo) if m else None

    cab = _cabecalho(linhas_txt, texto, achar, arquivo)
    itens = []
    for ws in palavras:
        itens.extend(_lancamentos(ws, cab.get('data_vencimento')))
    itens.sort(key=lambda i: i['data_compra'] or '')

    final = cab.get('final')
    return {'cabecalho': cab, 'itens': itens,
            'totais_cartao': ({final: cab['total_compras']}
                              if final and cab.get('total_compras') is not None else {}),
            'conferencia': _conferir(itens, cab)}


def _cabecalho(linhas: list, texto: str, achar, arquivo: str) -> dict:
    nome = achar(r'(AMAZON[A-Z\s]*?(?:PLATINUM|GOLD|INFINITE|BLACK))\s*\d{4}\.')
    final = achar(r'\d{4}\.\d{2}\*{2}\.\*{4}\.(\d{4})')

    # O topo põe os rótulos numa linha e os valores na seguinte:
    #   Total da fatura  Vencimento  Limite de compras  Limite de saque
    #   R$ 3.602,26      01/10/2026  R$ 6.000,00        R$ 1.200,00
    topo = next((m for l in linhas for m in
                 [re.match(r'^R\$\s*([\d.]+,\d{2})\s+(\d{2}/\d{2}/\d{4})\s+'
                           r'R\$\s*([\d.]+,\d{2})', l.strip())] if m), None)

    venc = topo.group(2) if topo else achar(r'Vencimento[^\d]{0,30}(\d{2}/\d{2}/\d{4})')
    if venc:
        d, m, a = venc.split('/')
        venc = f'{a}-{m}-{d}'

    # O mínimo é o terceiro valor da linha de opções de pagamento
    # (`R$ 3.602,26  1ª parcela de R$503,61  R$ 361,00`). Procurar pela palavra
    # "mínimo" não serve: ela é um rótulo numa linha e o número está noutra.
    minimo = next((m.group(1) for l in linhas for m in
                   [re.match(r'^R\$\s*[\d.]+,\d{2}\s+1[ªa]\s*parcela\s*de\s*'
                             r'R\$\s*[\d.]+,\d{2}\s+R\$\s*([\d.]+,\d{2})', l.strip())] if m), None)

    cab = {
        'cartao': (nome or 'Amazon').title().strip(),
        'final': final,
        'data_vencimento': venc,
        'situacao': 'fechada',
        'data_extrato': None,
        'arquivo': arquivo,
        'total_fatura': _num(topo.group(1)) if topo else _num(achar(r'\(=\)\s*Total\s*R\$\s*([\d.]+,\d{2})')),
        'limite_total': _num(topo.group(3)) if topo else None,
        'pagamento_minimo': _num(minimo),
        'total_compras': _num(achar(r'\(\+\)\s*Compras/D[ée]bitos\s*R\$\s*([\d.]+,\d{2})')),
        'saldo_anterior': _num(achar(r'Saldo\s*anterior\s*R\$\s*([\d.]+,\d{2})')),
        'total_proxima_fatura': _num(achar(r'Pr[óo]xima\s*fatura\s*R\$\s*([\d.]+,\d{2})')),
        'total_proximas_faturas': _num(
            achar(r'Total\s*para\s*as\s*pr[óo]ximas\s*faturas\s*R\$\s*([\d.]+,\d{2})')),
    }
    cab['total_lancamentos'] = cab['total_compras'] if cab['total_compras'] is not None \
        else cab['total_fatura']
    if venc:
        cab['mes_ref'] = venc[:7]
    return cab


def _lancamentos(palavras: list, venc) -> list:
    """Uma linha por valor em x1=288, dentro da coluna da esquerda."""
    esq = [w for w in palavras if w['x0'] < X_CORTE]
    linhas = {}
    for w in esq:
        linhas.setdefault(round(w['top']), []).append(w)

    itens = []
    for top in sorted(linhas):
        ws = sorted(linhas[top], key=lambda w: w['x0'])
        val = next((w for w in ws
                    if X_VALOR[0] <= w['x1'] <= X_VALOR[1] and RE_MOEDA.match(w['text'])), None)
        data = next((w for w in ws
                     if X_DATA[0] <= w['x0'] <= X_DATA[1] and RE_DATA_CURTA.match(w['text'])), None)
        if not val or not data:
            continue

        m = RE_MOEDA.match(val['text'])
        valor = _num(m.group(1))
        # O menos vem **depois** do número (`1.224,70-`): ignorá-lo faria um
        # pagamento recebido somar como compra.
        if m.group(2) == '-' and valor is not None:
            valor = -valor

        desc = ' '.join(w['text'] for w in ws
                        if X_DESC[0] <= w['x0'] < X_DESC[1] and w is not val)
        pn = pt = None
        mp = RE_PARCELA.search(desc)
        if mp:
            pn, pt = int(mp.group(1)), int(mp.group(2))
            desc = RE_PARCELA.sub('', desc)
        desc = re.sub(r'\s+BRA$', '', desc.strip()).strip()
        if not desc:
            continue

        itens.append({
            'secao': 'pagamento' if RE_PAGAMENTO.search(desc) else 'lancamento',
            'portador': None,
            'cartao_final': None,
            'internacional': False,
            'data_compra': _data_compra(data['text'], venc),
            'estabelecimento': desc,
            'valor': valor,
            'parcela_n': pn,
            'parcela_total': pt,
            'categoria_fatura': None,
            'cidade': None,
        })
    return itens


def _conferir(itens: list, cab: dict) -> dict:
    """Duas provas independentes, e a segunda valida uma suposição.

    A soma dos lançamentos sem o pagamento tem de dar `(+) Compras/Débitos`. E as
    parcelas que faltam, calculadas supondo parcelas iguais, têm de dar o
    `Total para as próximas faturas` que o banco publica — é o que transforma
    essa suposição em número conferido.
    """
    lanc = [i for i in itens if i['secao'] == 'lancamento']
    pagos = [i for i in itens if i['secao'] == 'pagamento']

    soma = round(sum(i['valor'] or 0 for i in lanc), 2)
    alvo = cab.get('total_lancamentos')
    dif = round(soma - alvo, 2) if alvo is not None else None

    # parcelas que ainda faltam, derivadas dos próprios lançamentos
    resta = 0.0
    prox = 0.0
    for i in lanc:
        pn, pt = i.get('parcela_n'), i.get('parcela_total')
        if pn and pt and pt > pn:
            resta += (pt - pn) * (i['valor'] or 0)
            prox += i['valor'] or 0
    resta, prox = round(resta, 2), round(prox, 2)
    alvo_resta = cab.get('total_proximas_faturas')
    alvo_prox = cab.get('total_proxima_fatura')
    dif_resta = round(resta - alvo_resta, 2) if alvo_resta is not None else None
    dif_prox = round(prox - alvo_prox, 2) if alvo_prox is not None else None

    cartoes = []
    if alvo is not None:
        cartoes.append({'final': cab.get('final'), 'esperado': alvo,
                        'lido': soma, 'diferenca': dif})

    return {
        'cartoes': cartoes,
        'itens_lancamento': len(lanc),
        'itens_pagamento': len(pagos),
        'itens_proxima_fatura': 0,
        'soma_lancamentos': soma,
        'total_lancamentos_pdf': alvo,
        'diferenca_lancamentos': dif,
        # esta fatura não itemiza parcelas futuras, então o que se confere é a
        # estimativa derivada dos lançamentos
        'soma_proximas': prox,
        'total_proxima_fatura_pdf': alvo_prox,
        'diferenca_proximas': dif_prox,
        'soma_parcelas_a_vencer': resta,
        'total_proximas_faturas_pdf': alvo_resta,
        'diferenca_parcelas_a_vencer': dif_resta,
        'ok': ((dif is None or abs(dif) < 0.01)
               and (dif_prox is None or abs(dif_prox) < 0.01)
               and (dif_resta is None or abs(dif_resta) < 0.01)),
    }
