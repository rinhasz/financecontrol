"""Fatura do cartão Mercado Pago (doc 17).

Coluna única e linhas bem formadas: aqui regex sobre o texto basta, e não há
necessidade da geometria que o layout do Itaú obrigou. Cinco páginas, mas só as
duas primeiras têm conteúdo — as outras são marketing e texto legal, incluindo
uma seção "Compras internacionais" que **não lista lançamento nenhum**, só a
alíquota de IOF. Casar por marca de seção, e não por página, é o que impede essa
seção de virar um bloco vazio de compras.

## Os dois blocos, e por que a distinção é obrigatória

```
Movimentações na fatura          <- encargo e pagamento, NÃO compra
  13/09 Pagamento da fatura de setembro/2026   R$ 710,59
  30/09 IOF do rotativo                        R$   3,24
  30/09 Juros de mora                          R$   2,14
  30/09 Multa por atraso                       R$  14,22
  30/09 Juros do rotativo                      R$  38,16
Cartão Visa [************3786]   <- as compras
  16/06 MERCADOLIVRE*MERCADOLIVRE Parcela 4 de 12  R$  44,08
  01/09 MERCADOLIVRE*MERCADOLIVRE                  R$ 114,90
Total                                          R$ 158,98
```

Tratar o `Pagamento da fatura` como despesa inflaria o mês em R$ 710,59 — é
dinheiro saindo para quitar a fatura anterior, não consumo. Ele entra como
`secao='pagamento'` e fica fora de todos os totais.

## Conferência

Fecha ao centavo por dois caminhos que o próprio PDF publica:
`encargos 57,76 + consumos 158,98 = 216,74`, o "Total a pagar" do topo. E o
resumo da página 1 quebra o mesmo número de outra forma
(`Tarifas 3,24 + Multas 14,22 + Juros do mês anterior 40,30`, onde
`40,30 = 2,14 + 38,16`), o que dá uma segunda prova de leitura.
"""
import re

from .cartoes import _num, _data_compra, extrair_texto

# `DD/MM <estabelecimento> [Parcela N de M] R$ 9.999,99`
#
# A parcela vem **escrita por extenso**, não como `PP/TT` — o padrão do Itaú não
# casaria aqui. É opcional e fica entre o estabelecimento e o valor.
RE_LANCAMENTO = re.compile(
    r'^(\d{2}/\d{2})\s+'                        # data
    r'(.+?)'                                    # estabelecimento
    r'(?:\s+Parcela\s+(\d+)\s+de\s+(\d+))?'     # parcela, opcional
    r'\s+R\$\s*(-?\d{1,3}(?:\.\d{3})*,\d{2})$'  # valor
)

RE_INICIO_MOVIMENTACOES = re.compile(r'Movimenta[çc][õo]es\s+na\s+fatura', re.I)
RE_INICIO_CARTAO = re.compile(r'Cart[ãa]o\s+\w+\s*\[\*+(\d{4})\]', re.I)
RE_TOTAL_BLOCO = re.compile(r'^Total\s+R\$\s*(-?\d{1,3}(?:\.\d{3})*,\d{2})$', re.I)

# O topo publica rótulos numa linha e valores na seguinte:
#   Total a pagar  Vence em  Limite total  Saque total
#   R$ 216,74      05/10/2026  R$ 18.000,00  R$ 50,00
RE_LINHA_TOPO = re.compile(
    r'^R\$\s*([\d.]+,\d{2})\s+(\d{2}/\d{2}/\d{4})\s+R\$\s*([\d.]+,\d{2})')

# Encargo ou pagamento? A diferença decide se o valor entra na conta do mês.
RE_PAGAMENTO = re.compile(r'pagamento\s+da\s+fatura|cr[ée]dito|estorno|devolv', re.I)
RE_ENCARGO = re.compile(r'IOF|juros|multa|tarifa|anuidade|encargo|mora|rotativo', re.I)


def _classificar(descricao: str) -> str:
    """O que é cada linha do bloco de movimentações.

    Classificado por palavra, não por posição, porque a ordem das linhas desse
    bloco muda conforme o mês tenha ou não atraso.
    """
    if RE_PAGAMENTO.search(descricao):
        return 'pagamento'
    if RE_ENCARGO.search(descricao):
        return 'encargo'
    return 'lancamento'


def _minimo(linhas: list, texto: str):
    """O pagamento mínimo, por frase inteira e não por palavra solta.

    Duas tentativas anteriores erraram, e as duas pelo mesmo motivo: procurar
    "mínimo" e depois deixar um `.*?` correr até o próximo `R$`. O texto tem
    nove ocorrências de "mínimo", e a primeira é a explicação
    (`Pagando o valor mínimo, a diferença...`), de onde o `.*?pagar` atravessa
    o documento inteiro e captura o R$ 710,59 do pagamento da fatura anterior.
    Uma variante pegou R$ 9,90, a tarifa de saque.

    A frase da página 4 é inequívoca e vem **colada**
    (`OvalormínimoquevocêdevepagarédeR$81,61`); com `\\s*` entre as palavras o
    padrão serve tanto para a forma colada quanto para a espaçada.
    """
    m = re.search(r'valor\s*m[íi]nimo\s*que\s*voc[êe]\s*deve\s*pagar\s*[ée]\s*de\s*'
                  r'R\$\s*([\d.]+,\d{2})', texto, re.I)
    if m:
        return _num(m.group(1))
    # Segunda fonte, independente da primeira: a linha de opções do topo traz a
    # parcela e o mínimo lado a lado (`Até 1 + 15x R$ 21,98 R$ 81,61`), e o
    # mínimo é o último valor dela.
    for l in linhas:
        if re.search(r'At[ée]\s*\d+\s*\+\s*\d+x', l, re.I):
            vals = re.findall(r'R\$\s*([\d.]+,\d{2})', l)
            if vals:
                return _num(vals[-1])
    return None


def parse(conteudo: bytes, arquivo: str = '') -> dict:
    linhas = extrair_texto(conteudo)
    texto = ' '.join(linhas)

    def achar(padrao, grupo=1):
        m = re.search(padrao, texto, re.I)
        return m.group(grupo) if m else None

    # `Vencimento: 05/10/2026` se repete no cabeçalho das páginas 2+ e é a
    # fonte mais confiável; a linha do topo é a alternativa.
    venc = achar(r'Vencimento:\s*(\d{2}/\d{2}/\d{4})')
    topo = next((m for l in linhas for m in [RE_LINHA_TOPO.match(l)] if m), None)
    if not venc and topo:
        venc = topo.group(2)
    if venc:
        d, m, a = venc.split('/')
        venc = f'{a}-{m}-{d}'

    final = achar(r'Cart[ãa]o\s+\w+\s*\[\*+(\d{4})\]')

    cab = {
        'cartao': 'Mercado Pago',
        'final': final,
        'data_vencimento': venc,
        'situacao': 'fechada',
        'data_extrato': None,
        'arquivo': arquivo,
        'total_fatura': _num(topo.group(1)) if topo else _num(achar(r'Total\s+R\$\s*([\d.]+,\d{2})')),
        'limite_total': _num(topo.group(3)) if topo else None,
        'pagamento_minimo': _minimo(linhas, texto),
        # não existe bloco de parcelas futuras nesta fatura
        'total_proximas_faturas': None,
        'total_proxima_fatura': None,
    }
    if venc:
        cab['mes_ref'] = venc[:7]

    itens, secao, total_consumos = [], None, None
    for linha in linhas:
        if RE_INICIO_MOVIMENTACOES.search(linha):
            secao = 'movimentacoes'
            continue
        mc = RE_INICIO_CARTAO.search(linha)
        if mc:
            secao = 'cartao'
            continue
        mt = RE_TOTAL_BLOCO.match(linha.strip())
        if mt and secao == 'cartao':
            total_consumos = _num(mt.group(1))
            secao = None
            continue
        if secao is None:
            continue

        m = RE_LANCAMENTO.match(linha.strip())
        if not m:
            continue
        estab = m.group(2).strip()
        tipo = 'lancamento' if secao == 'cartao' else _classificar(estab)
        itens.append({
            'secao': tipo,
            'portador': None,
            'cartao_final': final,
            'internacional': False,
            'data_compra': _data_compra(m.group(1), venc),
            'estabelecimento': estab,
            'valor': _num(m.group(5)),
            'parcela_n': int(m.group(3)) if m.group(3) else None,
            'parcela_total': int(m.group(4)) if m.group(4) else None,
            'categoria_fatura': None,
            'cidade': None,
        })

    cab['total_lancamentos'] = cab['total_fatura']
    return {'cabecalho': cab, 'itens': itens,
            'totais_cartao': {final: total_consumos} if final and total_consumos else {},
            'conferencia': _conferir(itens, cab, total_consumos)}


def _conferir(itens: list, cab: dict, total_consumos) -> dict:
    """Prova a leitura contra os totais que o PDF publica.

    O pagamento da fatura anterior fica fora das duas contas: ele não é consumo
    nem encargo, e somá-lo em qualquer das duas quebraria a conferência por
    exatamente o valor dele.
    """
    lanc = [i for i in itens if i['secao'] == 'lancamento']
    enc = [i for i in itens if i['secao'] == 'encargo']
    pagos = [i for i in itens if i['secao'] == 'pagamento']

    soma_lanc = round(sum(i['valor'] or 0 for i in lanc), 2)
    soma_enc = round(sum(i['valor'] or 0 for i in enc), 2)
    soma_total = round(soma_lanc + soma_enc, 2)

    dif_consumos = (round(soma_lanc - total_consumos, 2)
                    if total_consumos is not None else None)
    alvo = cab.get('total_fatura')
    dif_total = round(soma_total - alvo, 2) if alvo is not None else None

    cartoes = []
    if total_consumos is not None:
        cartoes.append({'final': cab.get('final'), 'esperado': total_consumos,
                        'lido': soma_lanc, 'diferenca': dif_consumos})

    return {
        'cartoes': cartoes,
        'itens_lancamento': len(lanc),
        'itens_proxima_fatura': 0,
        'itens_encargo': len(enc),
        'itens_pagamento': len(pagos),
        'soma_lancamentos': soma_total,
        'total_lancamentos_pdf': alvo,
        'diferenca_lancamentos': dif_total,
        'soma_proximas': 0.0,
        'total_proxima_fatura_pdf': None,
        'diferenca_proximas': None,
        'ok': ((dif_consumos is None or abs(dif_consumos) < 0.01)
               and (dif_total is None or abs(dif_total) < 0.01)),
    }
