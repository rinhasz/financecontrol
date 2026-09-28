"""Leitura da fatura de cartão de crédito em PDF (doc 17).

Este módulo faz **só o parser determinístico**: valor, data, parcela e portador
saem daqui, por forma. A IA entra depois, e só para normalizar o nome do
estabelecimento e categorizá-lo — um modelo que erra um dígito num valor produz
análise errada em silêncio.

## Por que ler por coordenada, e não por linha de texto

`extract_text()` devolve as **duas colunas da fatura achatadas numa linha só**:

    31/08 GALERIADOSPAES 53,57 15/09 ARBORETTOCAFEECOZIN 90,00

Dá para extrair os dois lançamentos daí com regex, e a primeira versão fazia
isso. Mas as colunas são **fluxos verticais independentes**, e os marcadores de
seção também caem nelas — então um `Lançamentosnocartão(final8051)` que aparece
na coluna direita fechava, por estar "depois" no texto, itens da coluna
esquerda. O resultado tinha total geral certo e **totais por cartão errados**:
nada se perdia, tudo ia para o cartão errado.

A geometria real da página é regular e resolve isso:

| | data | estabelecimento | valor |
|---|---|---|---|
| coluna A | x≈151 | x≈178 | x≈323 |
| coluna B | x≈367 | x≈394 | x≈539 |

A fronteira fica em ~360 — **não** no meio da página (298), que cortava o valor
da coluna A para dentro da coluna B. Por isso os limites são derivados das
posições reais das datas em cada página, e não fixados.
"""
import io
import re

# Um lançamento: data, estabelecimento, parcela opcional, valor.
# `(?!/)` impede casar o começo de uma data completa (01/10/2026 do cabeçalho).
RE_LANCAMENTO = re.compile(
    r'(\d{2}/\d{2})(?!/)\s*'
    r'(.+?)\s*'
    r'(?:(\d{2}/\d{2})(?!/)\s*)?'
    r'(-?\d{1,3}(?:\.\d{3})*,\d{2})'
)

RE_DATA_CURTA = re.compile(r'^\d{2}/\d{2}$')
RE_PORTADOR = re.compile(r'([A-ZÀ-Ú][A-ZÀ-Úa-zà-ú.\s]{2,40}?)\s*\(final\s*(\d{4})\)')
RE_TOTAL_CARTAO = re.compile(
    r'Lançamentosnocart[ãa]o\s*\(final\s*(\d{4})\)\s*(-?\d{1,3}(?:\.\d{3})*,\d{2})', re.I)
RE_INICIO_FUTURAS = re.compile(r'Comprasparceladas\s*-\s*pr[óo]ximasfaturas', re.I)

# Compra no exterior fica num bloco próprio, DEPOIS dos blocos por cartão e com
# total separado (`Totaltransaçõesinter.emR$`). Ela entra no total da fatura,
# mas **não** no `Lançamentosnocartão(finalXXXX)` — que é só o doméstico. Sem
# distinguir as duas coisas, a conferência do cartão 7484 acusava +116,69.
RE_INTERNACIONAL = re.compile(r'Lançamentosinternacionais', re.I)
RE_FIM_INTERNACIONAL = re.compile(r'Total(lançamentos|transações)inter\.?emR\$', re.I)

# IOF sobre compra internacional: é dinheiro da fatura e entra no total, mas não
# é compra — não tem data nem estabelecimento. Faltava exatamente ele para o
# total do The One fechar (20.319,61 + 4,06 = 20.323,67).
RE_ENCARGO = re.compile(r'(RepassedeIOFemR\$)\s*(-?\d{1,3}(?:\.\d{3})*,\d{2})', re.I)

# A categoria que o próprio emissor atribui, na linha logo abaixo da compra:
# `ALIMENTAÇÃO.SAOPAULO`. A cidade é **opcional** — às vezes a linha quebra e
# sobra só `DIVERSOS.`, e exigi-la derrubava a cobertura de 100% para 88%.
# Dígitos ficam de fora do padrão de propósito: é o que impede uma linha de
# valor (`SANFRANCISCO 110,00 BRL 21,57`) de passar por categoria.
RE_CATEGORIA = re.compile(r'^([A-ZÀ-Ú][A-ZÀ-Ú\s]{2,30}?)\.\s*([A-ZÀ-Úa-zà-ú\s]{0,30})$')


def _num(s):
    if s is None:
        return None
    try:
        return float(str(s).replace('.', '').replace(',', '.'))
    except ValueError:
        return None


def _data_compra(ddmm: str, vencimento: str):
    """`26/08` + vencimento `2026-10-01` -> `2026-08-26`.

    A fatura só traz dia/mês; o ano sai do vencimento, voltando um ano quando o
    mês da compra é maior — compra de dezembro numa fatura de janeiro.
    """
    if not ddmm or not vencimento:
        return None
    dia, mes = ddmm.split('/')
    ano = int(vencimento[:4])
    if int(mes) > int(vencimento[5:7]):
        ano -= 1
    return f'{ano}-{mes}-{dia}'


RE_MOEDA = re.compile(r'^-?\d{1,3}(?:\.\d{3})*,\d{2}$')


def _corte_de_coluna(palavras: list):
    """Onde termina a coluna da esquerda, ou `None` se a página tem uma só.

    A âncora é o **valor**, não a data. Valores são right-aligned em posições
    muito estáveis — x1≈340 na coluna A e x1≈556 na B, idênticos nas três
    faturas conferidas e em todas as páginas de lançamento.

    A primeira tentativa ancorava nas datas, e falhou por um motivo que só o
    PDF real mostra: a **parcela** (`07/10`) também é um token `DD/MM`, com x
    próprio (~301). Ela criava uma coluna fantasma entre o valor da A e a data
    da B, e as linhas se despedaçavam — 20 lançamentos lidos onde havia 168.

    Clusters com menos de três valores são ignorados: são cabeçalho, rodapé e
    as tabelas de simulação de parcelamento, não colunas de lançamento.
    """
    xs = sorted(w['x1'] for w in palavras if RE_MOEDA.match(w['text']))
    if not xs:
        return None
    grupos, atual = [[xs[0]]], xs[0]
    for x in xs[1:]:
        if x - atual > 25:
            grupos.append([])
        grupos[-1].append(x)
        atual = x
    fortes = [sum(g) / len(g) for g in grupos if len(g) >= 3]
    if len(fortes) < 2:
        return None

    # o corte fica logo à direita do valor da coluna A, antes da data da B
    esq = fortes[0]
    datas_dir = [w['x0'] for w in palavras
                 if RE_DATA_CURTA.match(w['text']) and w['x0'] > esq]
    return (esq + min(datas_dir)) / 2 if datas_dir else esq + 13


def _linhas_em_ordem_de_leitura(conteudo: bytes) -> list:
    """Linhas do PDF na ordem em que um humano leria: coluna por coluna.

    Cada coluna é percorrida de cima a baixo antes de passar para a próxima. É
    isso que mantém marcador e lançamento no mesmo fluxo — sem isso, um
    `Lançamentosnocartão(final…)` da coluna direita fechava itens da esquerda.

    Devolve `(bloco, texto)`, onde `bloco` identifica a coluna de origem
    (página, coluna). O id existe para a categoria: ela vem na linha **seguinte**
    ao lançamento, e sem saber onde uma coluna termina o último item dela pegaria
    a categoria da primeira linha da coluna vizinha — errado e silencioso.
    """
    import pdfplumber
    saida = []
    with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
        for npg, pg in enumerate(pdf.pages):
            palavras = pg.extract_words()
            if not palavras:
                continue
            corte = _corte_de_coluna(palavras)
            faixas = [(-float('inf'), corte), (corte, float('inf'))] if corte \
                else [(-float('inf'), float('inf'))]
            for ncol, (xini, xfim) in enumerate(faixas):
                col = [w for w in palavras if xini <= w['x0'] < xfim]
                linhas = {}
                for w in col:
                    linhas.setdefault(round(w['top']), []).append(w)
                for chave in sorted(linhas):
                    ws = sorted(linhas[chave], key=lambda y: y['x0'])
                    saida.append(((npg, ncol), ' '.join(w['text'] for w in ws).strip()))
    return saida


def extrair_texto(conteudo: bytes) -> list:
    """Linhas do texto corrido — usado só para o cabeçalho, que não tem colunas."""
    import pdfplumber
    with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
        txt = '\n'.join((pg.extract_text() or '') for pg in pdf.pages)
    return [l.strip() for l in txt.split('\n') if l.strip()]


def _cabecalho(linhas: list) -> dict:
    """Totais e dados do cartão.

    Procurado por rótulo e sobre o texto **juntado**, não linha a linha: o valor
    costuma cair na linha seguinte ao rótulo (`Pagamentomínimo:` numa, `R$3.042,51`
    na outra), e o cabeçalho muda de tamanho conforme o cartão tenha ou não
    parcelamento — a fatura Azul é duas linhas mais curta que as outras.
    """
    topo = ' '.join(linhas[:45])
    tudo = ' '.join(linhas)

    def achar(padrao, texto=None, grupo=1):
        m = re.search(padrao, texto if texto is not None else topo, re.I)
        return m.group(grupo) if m else None

    venc = achar(r'Comvencimentoem[:\s]*.*?(\d{2}/\d{2}/\d{4})') or achar(r'(\d{2}/\d{2}/\d{4})')
    if venc:
        d, m, a = venc.split('/')
        venc = f'{a}-{m}-{d}'

    # `Cartão 5244.XXXX.XXXX.8051THEONE`: o nome vem **colado** nos 4 dígitos, e
    # é opcional — a Azul não tem. Aceitar espaço aqui fazia a captura invadir a
    # linha seguinte e devolver "THEONE O".
    mc = re.search(r'Cart[ãa]o\s*\d{4}(?:\.X+)*\.?(\d{4})([A-ZÀ-Ú]{2,20})?', topo)
    return {
        'cartao': (mc.group(2).strip() if mc and mc.group(2) else None),
        'final': mc.group(1) if mc else None,
        'data_vencimento': venc,
        'total_fatura': _num(achar(r'=?Totaldestafatura\s*(-?[\d.]+,\d{2})')),
        'total_lancamentos': _num(achar(r'L?Lançamentosatuais\s*(-?[\d.]+,\d{2})')),
        # o rótulo e o valor caem em linhas diferentes ("Pagamentomínimo:
        # Parcelasfixas:" numa, "R$3.042,51 R$2.424,11" na outra), então é
        # preciso atravessar o que houver entre os dois e pegar o primeiro valor
        'pagamento_minimo': _num(achar(r'Pagamentom[íi]nimo:.*?R\$\s*([\d.]+,\d{2})')),
        # no topo o rótulo tem dois-pontos e é seguido de TRÊS valores em ordem
        # diferente; a ocorrência sem dois-pontos, mais adiante na fatura, traz
        # o número sozinho e é a confiável
        'limite_total': _num(achar(r'Limitetotaldecr[ée]dito\s+([\d.]+,\d{2})', tudo)),
    }


def parse_fatura(conteudo: bytes, arquivo: str = '') -> dict:
    """`{'cabecalho', 'itens', 'totais_cartao', 'conferencia'}`."""
    cab = _cabecalho(extrair_texto(conteudo))
    venc = cab.get('data_vencimento')

    itens, totais_cartao = [], {}
    portador, final_atual = None, cab.get('final')
    secao = 'lancamento'
    comecou = False        # antes do primeiro portador é tudo cabeçalho

    internacional = False
    registros = _linhas_em_ordem_de_leitura(conteudo)
    for idx, (bloco, linha) in enumerate(registros):
        if RE_INICIO_FUTURAS.search(linha):
            secao = 'proxima_fatura'
        if RE_INTERNACIONAL.search(linha):
            internacional = True

        me = RE_ENCARGO.search(linha)
        if me:
            itens.append({
                'secao': 'encargo', 'portador': None, 'cartao_final': None,
                'internacional': True, 'data_compra': None,
                'estabelecimento': me.group(1), 'valor': _num(me.group(2)),
                'parcela_n': None, 'parcela_total': None,
            })
            continue
        if RE_FIM_INTERNACIONAL.search(linha):
            internacional = False
            continue

        mp = RE_PORTADOR.search(linha)
        mf = RE_TOTAL_CARTAO.search(linha)
        if mf:
            totais_cartao[mf.group(1)] = _num(mf.group(2))
            continue
        if mp:
            portador = re.sub(r'\s+', ' ', mp.group(1)).strip()
            final_atual = mp.group(2)
            comecou = True
            continue
        if not comecou:
            continue

        for m in RE_LANCAMENTO.finditer(linha):
            estab = m.group(2).strip()
            parc = m.group(3)
            # No bloco de parcelas futuras toda linha é parcela — exigir a marca
            # PP/TT ali descarta o texto da coluna vizinha, que se entrelaça.
            if secao == 'proxima_fatura' and not parc:
                continue
            pn = pt = None
            if parc:
                a, b = parc.split('/')
                pn, pt = int(a), int(b)
            itens.append({
                'secao': secao,
                'portador': portador,
                'cartao_final': final_atual,
                'internacional': internacional,
                'data_compra': _data_compra(m.group(1), venc),
                'estabelecimento': estab,
                'valor': _num(m.group(4)),
                'parcela_n': pn,
                'parcela_total': pt,
                'categoria_fatura': None,
                'cidade': None,
                '_idx': idx,
                '_bloco': bloco,
            })

    _categorizar_pela_fatura(itens, registros)

    texto = ' '.join(extrair_texto(conteudo))

    def achar(p):
        m = re.search(p, texto, re.I)
        return _num(m.group(1)) if m else None

    # Duas coisas diferentes, e confundi-las fazia a conferência acusar erro
    # onde não havia: o bloco lista só as parcelas da PRÓXIMA fatura, enquanto
    # `Totalparapróximasfaturas` soma todas as que ainda faltam.
    cab['total_proxima_fatura'] = achar(r'Pr[óo]ximafatura\s*(-?[\d.]+,\d{2})')
    cab['total_proximas_faturas'] = achar(r'Totalparapr[óo]ximasfaturas\s*(-?[\d.]+,\d{2})')
    cab['arquivo'] = arquivo
    if venc:
        cab['mes_ref'] = venc[:7]

    return {'cabecalho': cab, 'itens': itens, 'totais_cartao': totais_cartao,
            'conferencia': conferir(itens, totais_cartao, cab)}


def _categorizar_pela_fatura(itens: list, registros: list):
    """Preenche a categoria que o **emissor** atribuiu a cada compra.

    Cobertura medida nas três faturas reais: 167/168, 38/39 e 2/2 nos
    lançamentos do mês. As duas faltas são a compra internacional e o estorno de
    cashback, que o banco de fato não categoriza — e nenhum dos dois é gasto.

    O bloco de parcelas futuras não vem categorizado em fatura nenhuma (0/18,
    0/4, 0/1): é compromisso, não compra. Mas é o **mesmo estabelecimento** da
    parcela deste mês (`ITAUSHOP 07/10` agora, `08/10` na próxima), então a
    categoria se herda por casamento — sem IA e sem chute.
    """
    for it in itens:
        idx, bloco = it.pop('_idx', None), it.pop('_bloco', None)
        if idx is None or idx + 1 >= len(registros):
            continue
        prox_bloco, prox_texto = registros[idx + 1]
        # nunca atravessar a fronteira da coluna: a linha seguinte ali é de
        # outra coluna e a categoria pertenceria a outro lançamento
        if prox_bloco != bloco:
            continue
        m = RE_CATEGORIA.match(prox_texto.strip())
        if m:
            it['categoria_fatura'] = m.group(1).strip()
            it['cidade'] = (m.group(2) or '').strip() or None

    herdado = {}
    for it in itens:
        if it['secao'] == 'lancamento' and it.get('categoria_fatura'):
            herdado.setdefault(it['estabelecimento'], it['categoria_fatura'])
    for it in itens:
        if not it.get('categoria_fatura'):
            it['categoria_fatura'] = herdado.get(it['estabelecimento'])


def conferir(itens: list, totais_cartao: dict, cab: dict) -> dict:
    """A fatura traz os próprios totais — então o parser pode ser provado.

    Um lançamento perdido numa coluna não dá erro nenhum: só some. Sem estas
    contas a importação seria um ato de fé, e é por isso que a tela mostra as
    diferenças **antes** de gravar.
    """
    lanc = [i for i in itens if i['secao'] == 'lancamento']
    fut = [i for i in itens if i['secao'] == 'proxima_fatura']
    enc = [i for i in itens if i['secao'] == 'encargo']

    # O total por cartão que a fatura publica é só do **doméstico**: a compra
    # internacional tem bloco e total próprios, e o encargo não é de cartão
    # nenhum. Somá-los aqui era o que fazia o 7484 acusar +116,69.
    por_cartao = {}
    for i in lanc:
        if i.get('internacional'):
            continue
        por_cartao[i['cartao_final']] = por_cartao.get(i['cartao_final'], 0.0) + (i['valor'] or 0)

    cartoes = []
    for final, esperado in totais_cartao.items():
        lido = round(por_cartao.get(final, 0.0), 2)
        cartoes.append({'final': final, 'esperado': esperado, 'lido': lido,
                        'diferenca': round(lido - (esperado or 0), 2)})

    # o total da fatura inclui compra doméstica, internacional e encargo
    soma_lanc = round(sum(i['valor'] or 0 for i in lanc + enc), 2)
    soma_fut = round(sum(i['valor'] or 0 for i in fut), 2)
    alvo_fut = cab.get('total_proxima_fatura')
    dif_lanc = round(soma_lanc - (cab.get('total_lancamentos') or 0), 2)
    dif_fut = round(soma_fut - (alvo_fut or 0), 2) if alvo_fut is not None else None

    return {
        'cartoes': cartoes,
        'itens_lancamento': len(lanc),
        'itens_proxima_fatura': len(fut),
        'soma_lancamentos': soma_lanc,
        'total_lancamentos_pdf': cab.get('total_lancamentos'),
        'diferenca_lancamentos': dif_lanc,
        'soma_proximas': soma_fut,
        'total_proxima_fatura_pdf': alvo_fut,
        'diferenca_proximas': dif_fut,
        'ok': (all(abs(c['diferenca']) < 0.01 for c in cartoes)
               and abs(dif_lanc) < 0.01
               and (dif_fut is None or abs(dif_fut) < 0.01)),
    }
