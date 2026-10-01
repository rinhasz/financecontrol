"""Extrato do cartão Amazon, emitido pelo Bradesco (doc 17).

Este documento **não é uma fatura fechada** — é um extrato em aberto, e ele
mesmo avisa: *"Valores sujeitos a alteração até o fechamento da fatura."* Duas
consequências que mudam o desenho, não só o parser:

1. **Não existe data de vencimento em nenhuma página.** A única data completa no
   PDF é a da extração. Como `data_vencimento` era metade da chave da fatura, a
   deduplicação passou a um índice único em `(cartao, mes_ref)` — ver doc 17.
2. **O número pode mudar amanhã.** `situacao='aberta'` existe para a tela nunca
   apresentar valor provisório como definitivo.

## Por que geometria, e não linha de texto

O nome do estabelecimento **se parte acima e abaixo da linha do valor**:

```
top=602   AMAZONMKTPLC*CMCOMERCI          <- começo do nome
top=609   21/07  000 0,00 0,00 R$ 0,00  26,50   <- data e valor
top=615   SA 3/12                         <- resto do nome, e a parcela
```

Agrupar por linha, como no Itaú, despedaçaria esse lançamento em três. Aqui a
âncora é o **valor**: para cada valor na coluna da direita, o nome se monta com
as palavras da faixa da coluna Histórico cujo `top` esteja a ±12px dele.

E a coluna do valor é a **última**: antes dela vêm quatro colunas de câmbio
quase sempre zeradas (`000 0,00 0,00 R$ 0,00`). Pegar o primeiro número que
casa devolveria 0,00 em todo lançamento.

## Conferência

O PDF não publica a soma dos lançamentos, mas permite montá-la:

```
Total para RAFAEL INHASZ   5.016,24
Total da Fatura em Real    4.309,25
                           --------
diferença                    706,99  = soma dos 10 lançamentos listados
```
"""
import io
import re

from .cartoes import _num, _data_compra

RE_MOEDA = re.compile(r'^-?\d{1,3}(?:\.\d{3})*,\d{2}$')
RE_DATA_CURTA = re.compile(r'^\d{2}/\d{2}$')
RE_PARCELA = re.compile(r'^(\d{1,2})/(\d{1,2})$')

# Faixas de x medidas no PDF real. A coluna do valor em R$ é a da direita; as
# de câmbio ficam entre 320 e 480 e são ignoradas de propósito.
X_DATA = (50, 100)
X_HISTORICO = (100, 320)
X_VALOR = (505, 560)

# tolerância vertical para juntar os fragmentos de um mesmo lançamento
TOL_TOP = 12


def parse(conteudo: bytes, arquivo: str = '') -> dict:
    import pdfplumber
    palavras, texto_linhas = [], []
    with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
        for pg in pdf.pages:
            ws = pg.extract_words()
            if not ws:
                # a página 2 vem com zero caracteres: iterar sem checar quebra
                continue
            palavras.extend(ws)
            texto_linhas.extend((pg.extract_text() or '').split('\n'))
    texto = ' '.join(texto_linhas)

    def achar(padrao, grupo=1):
        m = re.search(padrao, texto, re.I)
        return m.group(grupo) if m else None

    extrato = achar(r'Data:\s*(\d{2}/\d{2}/\d{4})')
    if extrato:
        d, m, a = extrato.split('/')
        extrato = f'{a}-{m}-{d}'

    aberto = bool(re.search(r'EM\s+ABERTO', texto, re.I))
    final = achar(r'X{4}\.X{4}\.X{4}\.(\d{4})')
    nome_cartao = achar(r'-\s*((?:AMAZON|BRADESCO)[A-Z\s]*?(?:PLATINUM|GOLD|INFINITE|BLACK)?)\s*X{4}')

    # As linhas de total vêm com pontos de preenchimento entre o rótulo e o
    # valor (`. Total para RAFAEL INHASZ . . . R$ 5.016,24`), então o padrão
    # precisa atravessar ponto — uma classe `[^.]` falhava em silêncio e
    # deixava os dois totais nulos, derrubando a conferência inteira.
    total_para = _num(achar(r'Total\s+para\s+.*?R\$\s*([\d.]+,\d{2})'))
    total_real = _num(achar(r'Total\s+da\s+Fatura\s+em\s+Real.*?R\$\s*([\d.]+,\d{2})'))

    itens = _lancamentos(palavras, extrato)

    cab = {
        'cartao': (nome_cartao or 'Amazon').title().strip(),
        'final': final,
        # não existe vencimento no documento: inventar um seria um campo que mente
        'data_vencimento': None,
        'situacao': 'aberta' if aberto else 'fechada',
        'data_extrato': extrato,
        'arquivo': arquivo,
        'total_fatura': total_para,
        'total_lancamentos': (round(total_para - total_real, 2)
                             if total_para is not None and total_real is not None else None),
        'total_fatura_anterior': total_real,
        'total_proximas_faturas': None,
        'total_proxima_fatura': None,
        'pagamento_minimo': None,
        'limite_total': None,
        'mes_ref': _mes_seguinte(extrato),
    }
    return {'cabecalho': cab, 'itens': itens,
            'totais_cartao': {final: cab['total_lancamentos']} if final else {},
            'conferencia': _conferir(itens, cab)}


def _mes_seguinte(data_iso):
    """Um extrato tirado em 30/09 é a fatura que vence em outubro.

    Sem vencimento no documento, o `mes_ref` sai do mês seguinte ao da
    extração — é o que deixa este extrato comparável com as faturas fechadas
    dos outros cartões, todas em `2026-10`.
    """
    if not data_iso:
        return None
    a, m = int(data_iso[:4]), int(data_iso[5:7])
    return f'{a + 1}-01' if m == 12 else f'{a}-{m + 1:02d}'


def _lancamentos(palavras: list, extrato) -> list:
    """Um lançamento por valor na coluna da direita, nome montado por faixa."""
    valores = [w for w in palavras
               if RE_MOEDA.match(w['text']) and X_VALOR[0] <= w['x1'] <= X_VALOR[1]]

    # As linhas de total usam a mesma coluna do valor: sem excluí-las, "Total da
    # Fatura em Real" entraria como compra de R$ 4.309,25.
    tops_total = {round(w['top']) for w in palavras if w['text'].lower().startswith('total')}

    itens = []
    for v in valores:
        topo = v['top']
        if any(abs(topo - t) <= TOL_TOP for t in tops_total):
            continue
        perto = [w for w in palavras if abs(w['top'] - topo) <= TOL_TOP]

        data = next((w['text'] for w in perto
                     if X_DATA[0] <= w['x0'] <= X_DATA[1] and RE_DATA_CURTA.match(w['text'])), None)
        if not data:
            continue

        frag = sorted((w for w in perto if X_HISTORICO[0] <= w['x0'] < X_HISTORICO[1]),
                      key=lambda w: (round(w['top']), w['x0']))
        partes, pn, pt = [], None, None
        for w in frag:
            mp = RE_PARCELA.match(w['text'])
            if mp:
                pn, pt = int(mp.group(1)), int(mp.group(2))
                continue
            partes.append(w['text'])
        estab = ' '.join(partes).strip()
        if not estab:
            continue

        itens.append({
            'secao': 'lancamento',
            'portador': None,
            'cartao_final': None,
            'internacional': False,
            'data_compra': _data_compra(data, extrato),
            'estabelecimento': estab,
            'valor': _num(v['text']),
            'parcela_n': pn,
            'parcela_total': pt,
            'categoria_fatura': None,
            'cidade': None,
        })
    itens.sort(key=lambda i: i['data_compra'] or '')
    return itens


def _conferir(itens: list, cab: dict) -> dict:
    soma = round(sum(i['valor'] or 0 for i in itens), 2)
    alvo = cab.get('total_lancamentos')
    dif = round(soma - alvo, 2) if alvo is not None else None
    return {
        'cartoes': ([{'final': cab.get('final'), 'esperado': alvo,
                      'lido': soma, 'diferenca': dif}] if alvo is not None else []),
        'itens_lancamento': len(itens),
        'itens_proxima_fatura': 0,
        'soma_lancamentos': soma,
        'total_lancamentos_pdf': alvo,
        'diferenca_lancamentos': dif,
        'soma_proximas': 0.0,
        'total_proxima_fatura_pdf': None,
        'diferenca_proximas': None,
        'ok': dif is not None and abs(dif) < 0.01,
    }
