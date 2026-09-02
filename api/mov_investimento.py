"""Extrato mensal de investimento: lê os resgates e ajusta a posição.

A posição importada (doc 16, fase 1) é uma **foto** de um dia. Entre uma foto e
a seguinte o dinheiro se mexe — resgate parcial, principalmente — e a
valorização, que só sabe render, continuaria mostrando um papel que não existe
mais no tamanho que ela pensa.

Este módulo lê o extrato mensal do produto (`ExtratoMensal_LCA.xls`, HTML
disfarçado de xls como todo arquivo do internet banking), acha as linhas de
RESGATE e grava cada uma como um **movimento**.

## Por que movimento em tabela à parte, e não posição corrigida

A posição é o que o banco mandou. Escrever por cima dela destrói o ponto de
partida da conferência — e conferir contra o banco é a razão de a tela existir.
O movimento fica ao lado, é aplicado durante a valorização, e **apagar a linha
reconstrói a posição anterior**. É o que permite reimportar sem medo.

## Identificação da operação

Pela **data de aplicação + data de vencimento**, dentro do produto escolhido.
Se casar com mais de um papel da base, o import **para e acusa** em vez de
adivinhar: dois papéis com as mesmas duas datas são indistinguíveis aqui, e
escolher errado corromperia silenciosamente a posição.

## Os três métodos de recálculo

Depois do resgate, quanto vale o papel? Três respostas, e o preview mostra as
três lado a lado porque elas discordam:

| método | conta | no caso real |
|---|---|---|
| `proporcional` | `valor_ontem × (aplicação − A) / aplicação` | 205.946,54 |
| `credito` | `valor_ontem − valor creditado` | 205.745,66 |
| `extrato` | o número que o próprio banco publica na seção "Posição em" | 205.859,16 |

`credito` mais o rendimento do dia reproduz `extrato` ao centavo
(205.745,66 × 1,00055 = 205.859,16, e 1,00055 é 94% do CDI diário) — é assim
que o banco faz a conta. `proporcional` supõe que o resgate tira uma fatia
proporcional do principal, o que não é o que aconteceu.
"""
import re
from datetime import date, datetime

from flask import Blueprint, jsonify, request

from .db import get_db, db
from .investimentos import _linhas_html, _num, _data

bp = Blueprint('mov_investimento', __name__)

PRODUTOS = ['LCI', 'LCA', 'CDB', 'COFRINHOS', 'LIG', 'LF']

# histórico que representa saída de dinheiro do papel
HISTORICOS_RESGATE = ('resgate',)


def _norm(v) -> str:
    return re.sub(r'\s+', ' ', str(v or '')).strip().lower()


def _achar_cabecalho(linhas):
    """Índice da linha de cabeçalho da movimentação.

    Procurada pelo conteúdo e não por posição fixa: o extrato tem um preâmbulo
    de tamanho variável (dados da conta, período) antes da tabela.
    """
    for i, l in enumerate(linhas):
        texto = ' '.join(_norm(c) for c in l)
        if 'histórico' in texto and 'valor aplicação' in texto:
            return i
    return None


def parse_resgates(conteudo: bytes) -> dict:
    """`{'resgates': [...], 'posicao': {n_operacao: {...}}, 'periodo': ...}`.

    A linha de RESGATE traz uma coluna a mais que o cabeçalho, porque o
    cabeçalho junta dois rótulos numa célula só ("Rentab. no período (%) Data
    aplicação"). Por isso as colunas são lidas por índice fixo a partir da linha
    de dados, e não pelo nome do cabeçalho.
    """
    linhas = _linhas_html(conteudo)
    cab = _achar_cabecalho(linhas)
    if cab is None:
        return {'resgates': [], 'posicao': {}, 'periodo': None, 'erro':
                'Não achei a tabela de movimentação — o arquivo é um extrato mensal de investimento?'}

    periodo = None
    for l in linhas[:cab]:
        texto = ' '.join(str(c) for c in l)
        m = re.search(r'(\d{2}/\d{2}/\d{4})\s*a\s*(\d{2}/\d{2}/\d{4})', texto)
        if m:
            periodo = {'ini': _data(m.group(1)), 'fim': _data(m.group(2))}

    resgates = []
    for l in linhas[cab + 1:]:
        if len(l) < 12:
            continue
        if _norm(l[1]) not in HISTORICOS_RESGATE:
            continue
        resgates.append({
            'data': _data(l[0]),
            'historico': str(l[1]).strip(),
            'valor_bruto': _num(l[2]),          # "Valor creditado": caiu na conta
            'valor_principal': _num(l[6]),      # "Valor aplicação": saiu do principal
            'data_aplicacao': _data(l[9]),
            'data_vencimento': _data(l[10]),
            'n_operacao': str(l[11]).strip(),
        })

    # Seção "Posição em DD/MM/AAAA": o banco publica ali o valor pós-resgate de
    # cada operação. É o gabarito — quando existe, não há por que estimar.
    posicao, cab_pos = {}, None
    for i, l in enumerate(linhas):
        texto = ' '.join(_norm(c) for c in l)
        if 'n. operação' in texto and 'data aplicação' in texto and 'valor aplicação' in texto:
            cab_pos = i
            datas_col = [c for c in l if re.search(r'\d{2}/\d{2}/\d{4}', str(c))]
            break
    if cab_pos is not None:
        rot = [str(c) for c in linhas[cab_pos]]
        # as duas últimas colunas de valor são "Valor em <ontem>" e "Valor em <hoje>"
        idx_hoje = len(rot) - 3 if len(rot) >= 8 else None
        for l in linhas[cab_pos + 1:]:
            if len(l) < 7 or not re.fullmatch(r'\d{6,}', str(l[0]).strip()):
                continue
            posicao[str(l[0]).strip()] = {
                'data_vencimento': _data(l[1]),
                'data_aplicacao': _data(l[2]),
                'valor_aplicacao': _num(l[3]),
                'valor_anterior': _num(l[5]),
                'valor_atual': _num(l[6]),
            }

    return {'resgates': resgates, 'posicao': posicao, 'periodo': periodo}


def _posicao_mais_recente(conn):
    r = conn.execute('SELECT MAX(data_posicao) d FROM investimento').fetchone()
    return r['d'] if r else None


def identificar(conn, produto: str, r: dict):
    """Acha na base o papel a que o resgate se refere.

    Devolve `(linha, erro)`. Nunca chuta: zero ou mais de um casamento é erro,
    e o import inteiro para. Corromper a posição em silêncio seria pior que
    pedir a intervenção.
    """
    data_pos = _posicao_mais_recente(conn)
    if not data_pos:
        return None, 'Nenhuma posição de investimentos importada ainda.'

    achados = conn.execute(
        'SELECT * FROM investimento WHERE data_posicao=? AND produto=? '
        'AND data_aplicacao=? AND data_vencimento=?',
        (data_pos, produto, r['data_aplicacao'], r['data_vencimento'])).fetchall()

    if not achados:
        return None, (f"Não achei nenhum {produto} aplicado em {r['data_aplicacao']} "
                      f"e vencendo em {r['data_vencimento']} na posição de {data_pos}.")
    if len(achados) > 1:
        return None, (f"Achei {len(achados)} papéis {produto} com aplicação em "
                      f"{r['data_aplicacao']} e vencimento em {r['data_vencimento']} — "
                      'não dá para saber de qual saiu o resgate. Corrija a posição antes de importar.')
    return achados[0], None


def _posicao_do_extrato(posicao: dict, r: dict):
    """A linha da seção "Posição em" que corresponde ao resgate.

    Casada pelas **datas**, não pelo número da operação: o extrato usa números
    diferentes para o movimento e para a posição do mesmo papel (110000690502
    no resgate, 110000095862 na posição).
    """
    for p in posicao.values():
        if (p['data_aplicacao'] == r['data_aplicacao']
                and p['data_vencimento'] == r['data_vencimento']):
            return p
    return None


def calcular(linha, r: dict, pos_extrato):
    """Os três valores possíveis para o papel depois do resgate.

    Devolvidos juntos de propósito: eles discordam, e a diferença é informação —
    a tela mostra as três e o número do banco ao lado, em vez de esconder a
    escolha atrás de uma fórmula.
    """
    aplicacao = linha['valor_aplicacao'] or 0
    ontem = linha['saldo_valorizado'] or linha['saldo_bruto_mtm'] or linha['saldo_bruto_accrual'] or 0
    a = r['valor_principal'] or 0

    proporcional = ontem * (aplicacao - a) / aplicacao if aplicacao else None
    credito = ontem - (r['valor_bruto'] or 0)
    extrato = pos_extrato['valor_atual'] if pos_extrato else None

    return {
        'valor_aplicacao_antes': round(aplicacao, 2),
        'valor_aplicacao_depois': round(aplicacao - a, 2),
        'valor_anterior': round(ontem, 2),
        'metodos': {
            'proporcional': round(proporcional, 2) if proporcional is not None else None,
            'credito': round(credito, 2),
            'extrato': round(extrato, 2) if extrato is not None else None,
        },
    }


# ------------------------------------------------------------------ rotas ---

def _analisar(conteudo: bytes, produto: str, arquivo: str):
    """Trabalho comum ao preview e à confirmação — a conta é feita uma vez só,
    para o que se confirma ser exatamente o que se viu."""
    lido = parse_resgates(conteudo)
    if lido.get('erro'):
        return None, lido['erro']
    if not lido['resgates']:
        return None, 'Nenhuma linha de RESGATE neste extrato — não há o que importar.'

    conn = get_db()
    itens, erros = [], []
    try:
        for r in lido['resgates']:
            linha, erro = identificar(conn, produto, r)
            if erro:
                erros.append(erro)
                continue
            conta = calcular(linha, r, _posicao_do_extrato(lido['posicao'], r))
            ja = conn.execute(
                'SELECT id FROM movimento_investimento WHERE produto=? AND data=? '
                'AND n_operacao=? AND valor_principal=?',
                (produto, r['data'], r['n_operacao'], r['valor_principal'])).fetchone()
            itens.append({**r, **conta, 'produto': produto,
                          'investimento_id': linha['id'],
                          'ativo': linha['ativo'], 'ja_importado': bool(ja)})
    finally:
        conn.close()

    return {'itens': itens, 'erros': erros, 'periodo': lido['periodo'],
            'arquivo': arquivo}, None


@bp.route('/investimentos/movimentos/preview', methods=['POST'])
def preview():
    """Lê o extrato e mostra o que faria — sem gravar nada."""
    file = request.files.get('file')
    produto = (request.form.get('produto') or '').upper()
    if not file:
        return jsonify({'ok': False, 'msg': 'Nenhum arquivo enviado'}), 400
    if produto not in PRODUTOS:
        return jsonify({'ok': False, 'msg': f'Produto inválido: {produto}'}), 400

    dados, erro = _analisar(file.read(), produto, file.filename or 'extrato')
    if erro:
        return jsonify({'ok': False, 'msg': erro}), 400
    return jsonify({'ok': True, **dados, 'produtos': PRODUTOS})


@bp.route('/investimentos/movimentos/confirmar', methods=['POST'])
def confirmar():
    """Grava os movimentos. A posição **não** é tocada — o efeito aparece na
    próxima valorização, e apagar o movimento desfaz tudo."""
    file = request.files.get('file')
    produto = (request.form.get('produto') or '').upper()
    metodo = request.form.get('metodo') or 'proporcional'
    if not file or produto not in PRODUTOS:
        return jsonify({'ok': False, 'msg': 'Arquivo e produto são obrigatórios'}), 400
    if metodo not in ('proporcional', 'credito', 'extrato'):
        return jsonify({'ok': False, 'msg': f'Método inválido: {metodo}'}), 400

    dados, erro = _analisar(file.read(), produto, file.filename or 'extrato')
    if erro:
        return jsonify({'ok': False, 'msg': erro}), 400
    if dados['erros']:
        # tudo ou nada: metade importada é pior que nada importado, porque a
        # posição fica num estado que ninguém sabe descrever
        return jsonify({'ok': False, 'msg': ' / '.join(dados['erros'])}), 409

    gravados = 0
    with db() as conn:
        for it in dados['itens']:
            valor_novo = it['metodos'].get(metodo)
            if valor_novo is None:
                valor_novo = it['metodos']['proporcional']
            conn.execute(
                'INSERT OR REPLACE INTO movimento_investimento '
                '(data, tipo, produto, data_aplicacao, data_vencimento, n_operacao, '
                ' valor_principal, valor_bruto, valor_anterior, valor_novo, metodo, arquivo) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (it['data'], 'resgate', produto, it['data_aplicacao'], it['data_vencimento'],
                 it['n_operacao'], it['valor_principal'], it['valor_bruto'],
                 it['valor_anterior'], valor_novo, metodo, dados['arquivo']))
            gravados += 1

    return jsonify({'ok': True, 'gravados': gravados,
                    'msg': f'{gravados} movimento(s) gravado(s). '
                           'Rode "Atualizar Posições" para o efeito aparecer.'})


@bp.route('/investimentos/movimentos')
def listar():
    """Movimentos de um período. Sem período, os do mês corrente para trás."""
    ini = request.args.get('ini') or ''
    fim = request.args.get('fim') or ''
    conn = get_db()
    sql = 'SELECT * FROM movimento_investimento'
    params = []
    if ini and fim:
        sql += ' WHERE data BETWEEN ? AND ?'
        params = [ini, fim]
    sql += ' ORDER BY data DESC, id DESC'
    movs = [dict(r) for r in conn.execute(sql, params)]
    conn.close()
    return jsonify({'ok': True, 'movimentos': movs,
                    'total_resgatado': round(sum(m['valor_bruto'] or 0 for m in movs), 2)})


@bp.route('/investimentos/movimentos/<int:mid>', methods=['DELETE'])
def excluir(mid):
    """Apaga um movimento — e com isso reconstrói a posição anterior.

    Nada mais precisa ser desfeito porque nada mais foi escrito: a posição
    importada nunca foi alterada, e a valorização é recalculada do zero a cada
    "Atualizar Posições".
    """
    with db() as conn:
        cur = conn.execute('DELETE FROM movimento_investimento WHERE id=?', (mid,))
        if not cur.rowcount:
            return jsonify({'ok': False, 'msg': 'Movimento não encontrado'}), 404
    return jsonify({'ok': True, 'msg': 'Movimento excluído. Rode "Atualizar Posições" '
                                       'para a posição voltar ao que era.'})
