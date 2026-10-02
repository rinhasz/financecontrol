"""Importação, categorização e análise da fatura de cartão (doc 17).

O parser fica em `cartoes.py` e **não importa Flask** de propósito: é o que
permite conferi-lo contra os PDFs reais num script solto. Aqui mora o que
precisa de banco e de rede.

## A ordem de precedência da categoria

Quatro origens, da mais fraca para a mais forte:

| origem | quem decide | quando |
|---|---|---|
| `fatura` | o emissor, na própria fatura | sempre que houver |
| `ia` | Gemini | só refina balde grosso ou preenche vazio |
| `regra` | o que o usuário já ensinou | vence a IA |
| `manual` | o usuário, nesta fatura | vence tudo |

A regra vence a IA porque o usuário sabe mais que o modelo sobre a própria
vida: `MP*GOCASE` é capinha de celular, não `EDUCAÇÃO`, e quem descobre isso é
quem comprou.

**Sem chave de IA nada quebra**: sobram as regras aprendidas e o dicionário de
palavras-chave abaixo, que sozinhos já resolvem o grosso — e o que não casar
fica em `Não classificado`, visível, em vez de virar um chute invisível.
"""
import json
import re
import unicodedata
from datetime import date

from flask import Blueprint, jsonify, request

from .db import db
from .cartoes import parse_fatura

bp = Blueprint('cartoes', __name__)

# A taxonomia do app. Mais fina que a do emissor porque a pergunta é outra:
# ele classifica para faturar, o app classifica para **cortar**. "Supermercado"
# e "Restaurante" são a mesma `ALIMENTAÇÃO` para o banco e decisões opostas
# para quem quer gastar menos.
CATEGORIAS = [
    'Supermercado', 'Restaurante e bar', 'Delivery', 'Combustível',
    'Transporte', 'Saúde', 'Farmácia', 'Educação', 'Vestuário', 'Beleza',
    'Assinaturas', 'Viagem', 'Lazer', 'Presentes', 'Seguros', 'Casa',
    'Esporte', 'Eletrônicos', 'Filhos', 'Outros', 'Encargos', 'Pagamento',
]

# Seções que não são compra: categoria fixa, e nunca enviadas à IA. `Encargos`
# porque IOF e juros não são escolha de ninguém; `Pagamento` porque quitar a
# fatura anterior é dinheiro saindo para zerar dívida, não consumo — e deixá-lo
# ir ao modelo rendeu "Pagamento da Fatura de Setembro -> Outros".
CATEGORIA_FIXA = {'encargo': 'Encargos', 'pagamento': 'Pagamento'}

NAO_CLASSIFICADO = 'Não classificado'

# Tradução direta do balde do emissor. É o piso: vale quando nada mais souber.
# `ALIMENTAÇÃO` cai em restaurante porque, nas faturas reais, mercado é minoria
# em quantidade — e as palavras-chave abaixo pegam os mercados pelo nome.
DE_FATURA = {
    'ALIMENTAÇÃO': 'Restaurante e bar',
    'VEÍCULOS': 'Transporte',
    'SAÚDE': 'Saúde',
    'VESTUÁRIO': 'Vestuário',
    'EDUCAÇÃO': 'Educação',
    'HOBBY': 'Esporte',
    'TURISMOEENTRETENIM': 'Viagem',
    'DIVERSOS': None,          # 23 lançamentos e nenhuma informação: não traduz
}

# Primeira que casar vence, como em docs/05. Casa contra o estabelecimento sem
# acento e em minúsculas.
PALAVRAS = [
    (r'mercadolivre|mercpago', 'Outros'),        # antes de "mercado"
    (r'sacolao|paodeacucar|pao de acucar|carrefour|assai|hortifruti|'
     r'oxxo|st ?marche|natural da terra|emporio|mercado|supermerc', 'Supermercado'),
    (r'ifd\*|ifood|rappi|ubereats|uber \*eats', 'Delivery'),
    (r'abastec|shellbox|autoposto|posto |ipiranga|petrobras|br mania', 'Combustível'),
    (r'uber|99app|99 ?tecnologia|tagitau|sem parar|conectcar|lavepark|'
     r'estapar|parking|estacion|taxi', 'Transporte'),
    (r'drogaria|drogasil|droga ?raia|pacheco|farmacia|panvel', 'Farmácia'),
    (r'apple\.com|googleone|google one|spotify|netflix|globoplay|premiere|'
     r'anthropic|claude|openai|chatgpt|disney|hbo|max\.com|amazonprime|'
     r'prime video|youtubepremium|icloud', 'Assinaturas'),
    (r'sephora|cosmetic|boticario|natura|avon|perfum|cabelereir|barbear', 'Beleza'),
    (r'prudential|seguro|porto ?seg|bradesco ?seg|allianz', 'Seguros'),
    (r'vivara|pandora|joalher|presente', 'Presentes'),
    (r'centauro|bayard|decathlon|nike|adidas|palmeiras|avanti|'
     r'sociedadeesportiva|academia|smartfit', 'Esporte'),
    (r'hotel|hoteis|pousada|vilagale|booking|airbnb|latam|gol ?linhas|'
     r'azul ?viagens|cvc|decolar', 'Viagem'),
    (r'cinema|cinemark|bienal|fever|betocarrero|ingresso|teatro|museu|'
     r'parque|showlivre', 'Lazer'),
    (r'odontolog|clinica|dentist|hospital|laborator|fleury|einstein|'
     r'invisalig|medic|psicolog|fisioter', 'Saúde'),
    (r'livro|papelaria|escolar|loyola|suprioeste|colegio|curso', 'Educação'),
    (r'gocase|kabum|magazineluiza|magalu|fastshop|samsung|motorola|'
     r'informatic|eletronic', 'Eletrônicos'),
    (r'panini|redballoon|brinquedo|rihappy|pbkids|toy', 'Filhos'),
    (r'leroy|telhanorte|obramax|casa ?e ?construcao|tok ?stok|etna|'
     r'karstens|blumen|utilidades', 'Casa'),
    (r'zara|inditex|renner|cea|riachuelo|tng|hering|meias|acessorios', 'Vestuário'),
]

# Baldes grossos demais para servir de resposta. Item que caiu num destes (ou
# em nenhum) é o que vale a pena mandar para a IA refinar — o resto já está
# resolvido de graça.
GROSSOS = {NAO_CLASSIFICADO, 'Outros', 'Restaurante e bar', 'Viagem'}


def _sem_acento(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', s or '')
                   if unicodedata.category(c) != 'Mn').lower()


def _por_palavra(estab: str):
    alvo = _sem_acento(estab)
    for padrao, cat in PALAVRAS:
        if re.search(padrao, alvo):
            return cat
    return None


def _regras(conn) -> list:
    return [(r['padrao'], r['categoria']) for r in conn.execute(
        'SELECT padrao, categoria FROM cartao_categoria_regra ORDER BY acertos DESC').fetchall()]


def _por_regra(estab: str, regras: list):
    alvo = _sem_acento(estab)
    for padrao, cat in regras:
        if _sem_acento(padrao) in alvo:
            return cat
    return None


def _classificar(itens: list, conn, usar_ia: bool = True) -> dict:
    """Atribui `categoria` e `origem_categoria` a cada item, sem tocar em valor.

    Devolve um resumo do que veio de onde — que é o que a tela mostra antes de
    gravar, para o usuário saber em quanto do resultado a IA opinou.
    """
    regras = _regras(conn)
    for it in itens:
        fixa = CATEGORIA_FIXA.get(it['secao'])
        if fixa:
            it['categoria'], it['origem_categoria'] = fixa, 'fatura'
            continue
        cat = _por_regra(it['estabelecimento'], regras)
        if cat:
            it['categoria'], it['origem_categoria'] = cat, 'regra'
            continue
        cat = _por_palavra(it['estabelecimento'])
        if cat:
            it['categoria'], it['origem_categoria'] = cat, 'regra'
            continue
        cat = DE_FATURA.get((it.get('categoria_fatura') or '').upper())
        it['categoria'] = cat or NAO_CLASSIFICADO
        it['origem_categoria'] = 'fatura' if cat else None

    ia = {}
    if usar_ia:
        pendentes = sorted({it['estabelecimento'] for it in itens
                            if it['categoria'] in GROSSOS or not it['origem_categoria']})
        if pendentes:
            ia = _gemini_classificar(pendentes, itens)
    for it in itens:
        sug = ia.get(it['estabelecimento'])
        if not sug:
            continue
        if sug.get('nome'):
            it['estabelecimento_norm'] = sug['nome']
        # A IA só melhora o que estava grosso ou vazio; nunca derruba regra. E
        # só é creditada quando **mudou** algo: confirmar o que a fatura já
        # dizia não é opinião, e contabilizar isso como "ia" inflava o número
        # de 12 para 88, tirando o sentido do resumo que a tela mostra.
        if (sug.get('categoria') and it['origem_categoria'] in (None, 'fatura')
                and sug['categoria'] != it['categoria']):
            it['categoria'], it['origem_categoria'] = sug['categoria'], 'ia'

    for it in itens:
        it.setdefault('estabelecimento_norm', None)
        if not it['origem_categoria']:
            it['origem_categoria'] = 'fatura' if it.get('categoria_fatura') else None

    resumo = {}
    for it in itens:
        k = it['origem_categoria'] or 'nenhuma'
        resumo[k] = resumo.get(k, 0) + 1
    return resumo


def _gemini_classificar(estabelecimentos: list, itens: list) -> dict:
    """Normaliza o nome e refina a categoria. **Não** vê valores.

    Mandar o valor junto seria convidar o modelo a raciocinar sobre dinheiro, e
    dinheiro aqui já está resolvido pelo parser. O que ele recebe é uma lista de
    nomes colados e a categoria que o emissor deu; o que devolve é nome legível
    e categoria — validada contra `CATEGORIAS` antes de entrar.
    """
    from .email_busca import _gemini_client, GEMINI_MODEL
    client = _gemini_client()
    if not client:
        return {}
    from google.genai import types

    dica = {}
    for it in itens:
        if it['estabelecimento'] in estabelecimentos and it.get('categoria_fatura'):
            dica.setdefault(it['estabelecimento'], it['categoria_fatura'])
    lista = [{'bruto': e, 'categoria_emissor': dica.get(e)} for e in estabelecimentos]

    prompt = (
        'Você recebe nomes de estabelecimentos como aparecem numa fatura de cartão '
        'brasileira: o texto vem SEM ESPAÇOS e às vezes com prefixo de adquirente '
        '("EC *", "PAG10*", "DL*", "MP*", "IFD*", "ZP*"). Para CADA item devolva:\n'
        '- bruto: o texto exatamente como recebido (para eu casar de volta)\n'
        '- nome: o nome legível do estabelecimento, com espaços e acentos '
        '(ex: "ARBORETTOCAFEECOZIN" -> "Arboretto Café e Cozinha"). Se não '
        'reconhecer, apenas separe as palavras da melhor forma possível.\n'
        f'- categoria: exatamente uma destas: {json.dumps(CATEGORIAS, ensure_ascii=False)}\n\n'
        'A "categoria_emissor" é o rótulo grosseiro que o banco deu — use como '
        'pista, mas corrija quando estiver claramente errado (o banco chama de '
        'ALIMENTAÇÃO tanto supermercado quanto restaurante, e já classificou '
        'capinha de celular como EDUCAÇÃO).\n'
        'Nunca invente estabelecimento que não está na lista. Devolva a lista '
        'inteira, um objeto por item.\n\n'
        f'ITENS:\n{json.dumps(lista, ensure_ascii=False)}'
    )
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type='application/json',
                response_schema={
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'bruto': {'type': 'string'},
                            'nome': {'type': 'string'},
                            'categoria': {'type': 'string', 'enum': CATEGORIAS},
                        },
                        'required': ['bruto'],
                    },
                },
            ),
        )
        dados = json.loads(resp.text)
    except Exception as e:
        print(f'[cartoes] falha ao classificar com IA: {e}')
        return {}

    # validação estrutural: só entra o que eu pedi e categoria que eu conheço.
    # Sem isso um modelo criativo insere estabelecimento que não existe na
    # fatura, e a análise passa a descrever uma vida que não é a do usuário.
    conhecidos = set(estabelecimentos)
    saida = {}
    for d in dados if isinstance(dados, list) else []:
        bruto = (d or {}).get('bruto')
        if bruto not in conhecidos:
            continue
        cat = d.get('categoria')
        saida[bruto] = {
            'nome': (d.get('nome') or '').strip() or None,
            'categoria': cat if cat in CATEGORIAS else None,
        }
    return saida


# O nome do produto vem colado no cabeçalho (`THEONE`, `MASTERCARDBLACK`) e às
# vezes não vem — a fatura Azul não traz nenhum. `.title()` sozinho devolve
# "Mastercardblack", então os conhecidos vão num mapa e o resto cai no final do
# cartão, que é sempre verdadeiro.
ROTULOS = {
    'THEONE': 'The One',
    'MASTERCARDBLACK': 'Mastercard Black',
    'BLACK': 'Black',
    'INFINITE': 'Infinite',
    'PLATINUM': 'Platinum',
    'GOLD': 'Gold',
    'AZUL': 'Azul',
}


def _rotulo_cartao(cab: dict) -> str:
    """O nome pelo qual o usuário chama o cartão.

    Não é enfeite: é metade da chave `UNIQUE(cartao, data_vencimento)`. Se o
    rótulo mudar entre duas importações da mesma fatura, ela entra duas vezes e
    a análise conta o gasto em dobro — por isso o preview mostra o rótulo e
    deixa corrigir antes de gravar.
    """
    nome = (cab.get('cartao') or '').strip().upper()
    if nome:
        return ROTULOS.get(nome, nome.title())
    return f"Cartão {cab.get('final') or '????'}"


def _analisar(conteudo: bytes, arquivo: str, usar_ia: bool):
    dados = parse_fatura(conteudo, arquivo)
    cab = dados['cabecalho']
    # Extrato em aberto não tem vencimento (o do cartão Amazon não traz em
    # página nenhuma). O que não pode faltar é o `mes_ref`, que é a chave real.
    if not cab.get('mes_ref'):
        return None, ('Não consegui achar nem vencimento nem data de extrato '
                      'nesta fatura, então não sei a que mês ela pertence.')
    cab['rotulo'] = _rotulo_cartao(cab)
    with db() as conn:
        dados['origens'] = _classificar(dados['itens'], conn, usar_ia)
    return dados, None


@bp.route('/cartoes/importar/preview', methods=['POST'])
def preview():
    """Lê o PDF, categoriza e mostra — sem gravar nada.

    A conferência vai junto **antes** de gravar porque um lançamento perdido
    numa coluna não dá erro: só some, e a análise fica errada em silêncio.
    """
    file = request.files.get('file')
    if not file:
        return jsonify({'ok': False, 'msg': 'Nenhum arquivo enviado'}), 400
    usar_ia = (request.form.get('ia') or '1') != '0'
    try:
        dados, erro = _analisar(file.read(), file.filename or 'fatura.pdf', usar_ia)
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'Não consegui ler o PDF: {e}'}), 400
    if erro:
        return jsonify({'ok': False, 'msg': erro}), 400

    with db() as conn:
        ja = conn.execute(
            'SELECT id, arquivo, criado_em FROM fatura_cartao WHERE cartao=? AND data_vencimento=?',
            (dados['cabecalho']['rotulo'], dados['cabecalho']['data_vencimento'])).fetchone()
    return jsonify({'ok': True, **dados, 'categorias': CATEGORIAS,
                    'ja_importada': dict(ja) if ja else None})


@bp.route('/cartoes/importar/confirmar', methods=['POST'])
def confirmar():
    """Grava a fatura. Reimportar **substitui**, nunca duplica."""
    file = request.files.get('file')
    if not file:
        return jsonify({'ok': False, 'msg': 'Nenhum arquivo enviado'}), 400
    usar_ia = (request.form.get('ia') or '1') != '0'
    forcar = (request.form.get('forcar') or '0') == '1'
    try:
        dados, erro = _analisar(file.read(), file.filename or 'fatura.pdf', usar_ia)
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'Não consegui ler o PDF: {e}'}), 400
    if erro:
        return jsonify({'ok': False, 'msg': erro}), 400

    # o usuário pode corrigir o rótulo no preview — a Azul não traz nome no PDF
    rotulo = (request.form.get('rotulo') or '').strip()
    if rotulo:
        dados['cabecalho']['rotulo'] = rotulo

    conf = dados['conferencia']
    if not conf['ok'] and not forcar:
        return jsonify({'ok': False, 'conferencia': conf,
                        'msg': 'A fatura não fecha com os totais do PDF. '
                               'Confira as diferenças antes de gravar.'}), 409

    cab = dados['cabecalho']
    with db() as conn:
        # Dedupe por (cartao, mes_ref): é a chave estável nos dois casos, já
        # que o extrato em aberto não tem vencimento e dois NULL não colidem.
        antiga = conn.execute(
            'SELECT id FROM fatura_cartao WHERE cartao=? AND mes_ref=?',
            (cab['rotulo'], cab.get('mes_ref'))).fetchone()
        if antiga:
            # substituição limpa: os itens da importação anterior saem antes,
            # senão a fatura reimportada soma em dobro na análise
            conn.execute('DELETE FROM fatura_item WHERE fatura_id=?', (antiga['id'],))
            conn.execute('DELETE FROM fatura_cartao WHERE id=?', (antiga['id'],))

        cur = conn.execute(
            'INSERT INTO fatura_cartao (cartao, final, arquivo, mes_ref, data_vencimento, '
            ' total_fatura, total_lancamentos, total_proximas_faturas, pagamento_minimo, '
            ' limite_total, situacao, data_extrato, emissor) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (cab['rotulo'], cab.get('final'), cab.get('arquivo'), cab.get('mes_ref'),
             cab.get('data_vencimento'), cab.get('total_fatura'), cab.get('total_lancamentos'),
             cab.get('total_proximas_faturas'), cab.get('pagamento_minimo'),
             cab.get('limite_total'), cab.get('situacao') or 'fechada',
             cab.get('data_extrato'), cab.get('emissor')))
        fid = cur.lastrowid

        for it in dados['itens']:
            conn.execute(
                'INSERT INTO fatura_item (fatura_id, secao, portador, cartao_final, '
                ' data_compra, estabelecimento, estabelecimento_norm, valor, parcela_n, '
                ' parcela_total, internacional, categoria_fatura, cidade, categoria, '
                ' origem_categoria) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (fid, it['secao'], it.get('portador'), it.get('cartao_final'),
                 it.get('data_compra'), it['estabelecimento'], it.get('estabelecimento_norm'),
                 it['valor'], it.get('parcela_n'), it.get('parcela_total'),
                 1 if it.get('internacional') else 0, it.get('categoria_fatura'),
                 it.get('cidade'), it.get('categoria'), it.get('origem_categoria')))

    return jsonify({'ok': True, 'fatura_id': fid, 'itens': len(dados['itens']),
                    'substituiu': bool(antiga), 'conferencia': conf,
                    'msg': f"{len(dados['itens'])} lançamento(s) gravado(s) "
                           f"de {cab['rotulo']} ({cab['data_vencimento']})."})


@bp.route('/cartoes/faturas')
def faturas():
    with db() as conn:
        rs = conn.execute(
            'SELECT f.*, (SELECT COUNT(*) FROM fatura_item i WHERE i.fatura_id=f.id) n_itens '
            'FROM fatura_cartao f ORDER BY data_vencimento DESC, cartao').fetchall()
    return jsonify({'ok': True, 'faturas': [dict(r) for r in rs]})


@bp.route('/cartoes/faturas/<int:fid>', methods=['DELETE'])
def excluir(fid):
    with db() as conn:
        conn.execute('DELETE FROM fatura_item WHERE fatura_id=?', (fid,))
        conn.execute('DELETE FROM fatura_cartao WHERE id=?', (fid,))
    return jsonify({'ok': True})


@bp.route('/cartoes/itens')
def itens():
    mes = request.args.get('mes_ref')
    with db() as conn:
        sql = ('SELECT i.*, f.cartao, f.mes_ref, f.data_vencimento FROM fatura_item i '
               'JOIN fatura_cartao f ON f.id = i.fatura_id')
        args = []
        if mes:
            sql += ' WHERE f.mes_ref = ?'
            args.append(mes)
        sql += ' ORDER BY i.data_compra DESC, i.valor DESC'
        rs = conn.execute(sql, args).fetchall()
        estr = _estrategias(conn)
        locais = _locais(conn)

    # O SERVIDOR estampa o segmento de cada item. A regra depende de
    # `estabelecimento_local`, `sexta_por_local`, `discricionario` e do corte de
    # ticket — reimplementá-la no cliente seria manter a mesma regra em duas
    # linguagens, e elas divergiriam na primeira correção.
    itens_ = []
    for r in rs:
        d = dict(r)
        d['segmento'] = _segmento(d, estr, locais)
        itens_.append(d)
    return jsonify({'ok': True, 'itens': itens_, 'categorias': CATEGORIAS})


@bp.route('/cartoes/itens/<int:iid>/categoria', methods=['POST'])
def recategorizar(iid):
    """Corrigir a categoria **ensina**: vira regra e vale na próxima fatura.

    Mesma ideia de `transacao_despesa_regra` no batimento (doc 10) — a correção
    que não é aprendida é uma correção que o usuário vai refazer todo mês.
    """
    d = request.get_json(force=True) or {}
    cat = d.get('categoria')
    if cat not in CATEGORIAS and cat != NAO_CLASSIFICADO:
        return jsonify({'ok': False, 'msg': f'Categoria inválida: {cat}'}), 400
    aprender = d.get('aprender', True)

    with db() as conn:
        it = conn.execute('SELECT estabelecimento FROM fatura_item WHERE id=?', (iid,)).fetchone()
        if not it:
            return jsonify({'ok': False, 'msg': 'Lançamento não encontrado'}), 404
        estab = it['estabelecimento']
        conn.execute("UPDATE fatura_item SET categoria=?, origem_categoria='manual' WHERE id=?",
                     (cat, iid))
        afetados = 1
        if aprender:
            conn.execute(
                'INSERT INTO cartao_categoria_regra (padrao, categoria) VALUES (?,?) '
                'ON CONFLICT(padrao, categoria) DO UPDATE SET acertos = acertos + 1',
                (estab, cat))
            # a mesma correção vale para todo o histórico do estabelecimento,
            # menos onde o usuário já decidiu outra coisa à mão
            cur = conn.execute(
                "UPDATE fatura_item SET categoria=?, origem_categoria='regra' "
                "WHERE estabelecimento=? AND id<>? AND origem_categoria<>'manual'",
                (cat, estab, iid))
            afetados += cur.rowcount
    return jsonify({'ok': True, 'afetados': afetados, 'estabelecimento': estab})


@bp.route('/cartoes/analise')
def analise():
    """As três perguntas do doc 17: onde gastei, o que está comprometido, o que cortar."""
    mes = request.args.get('mes_ref')
    with db() as conn:
        if not mes:
            r = conn.execute('SELECT MAX(mes_ref) m FROM fatura_cartao').fetchone()
            mes = r['m'] if r else None
        if not mes:
            return jsonify({'ok': True, 'mes_ref': None, 'vazio': True})

        itens = [dict(r) for r in conn.execute(
            'SELECT i.*, f.cartao, f.mes_ref FROM fatura_item i '
            'JOIN fatura_cartao f ON f.id=i.fatura_id WHERE f.mes_ref=?', (mes,)).fetchall()]
        # `data_compra` entra aqui porque a segmentação por dia da semana
        # precisa dela também no histórico, para a base da meta percentual
        hist = [dict(r) for r in conn.execute(
            'SELECT i.categoria, i.estabelecimento, i.valor, i.data_compra, f.mes_ref '
            'FROM fatura_item i JOIN fatura_cartao f ON f.id=i.fatura_id '
            "WHERE f.mes_ref<? AND i.secao='lancamento'", (mes,)).fetchall()]
        estr = _estrategias(conn)
        metas_cfg = _metas(conn)
        locais = _locais(conn)
        meses = [r['mes_ref'] for r in conn.execute(
            'SELECT DISTINCT mes_ref FROM fatura_cartao ORDER BY mes_ref DESC').fetchall()]
        # Quais faturas compõem o mês, e se alguma é extrato **aberto**: valor
        # provisório não pode ser mostrado como definitivo, e sem isto a tela
        # não teria como saber a diferença.
        # `total_lancamentos` vai junto porque num extrato aberto ele difere do
        # total da fatura: o do cartão Amazon soma R$ 5.016,24, dos quais
        # R$ 4.309,25 são saldo anterior que o PDF não detalha — só os R$ 706,99
        # de consumo entram na análise. Mostrar 5.016,24 na tabela enquanto a
        # análise conta 706,99 seria um total que não reconcilia.
        faturas = [dict(r) for r in conn.execute(
            'SELECT cartao, emissor, situacao, data_vencimento, data_extrato, '
            ' total_fatura, total_lancamentos FROM fatura_cartao '
            'WHERE mes_ref=? ORDER BY cartao', (mes,)).fetchall()]

    lanc = [i for i in itens if i['secao'] == 'lancamento']
    fut = [i for i in itens if i['secao'] == 'proxima_fatura']
    enc = [i for i in itens if i['secao'] == 'encargo']
    parc = _parcelamentos(lanc)

    def agrupar(chave, base=lanc):
        g = {}
        for i in base:
            k = i.get(chave) or '—'
            e = g.setdefault(k, {'chave': k, 'valor': 0.0, 'n': 0})
            e['valor'] += i['valor'] or 0
            e['n'] += 1
        return sorted(({**v, 'valor': round(v['valor'], 2)} for v in g.values()),
                      key=lambda x: -x['valor'])

    # média das categorias nos meses anteriores, para medir salto
    n_meses = len({h['mes_ref'] for h in hist}) or 1
    media = {}
    for h in hist:
        media[h['categoria']] = media.get(h['categoria'], 0.0) + (h['valor'] or 0)
    media = {k: v / n_meses for k, v in media.items()}

    candidatos = []

    # assinatura: mesmo estabelecimento e mesmo valor em 3+ meses
    combo = {}
    for h in hist + [{'estabelecimento': i['estabelecimento'], 'valor': i['valor'],
                      'mes_ref': mes} for i in lanc]:
        k = (h['estabelecimento'], round(h['valor'] or 0, 2))
        combo.setdefault(k, set()).add(h['mes_ref'])
    for (estab, val), ms in combo.items():
        if len(ms) >= 3 and val > 0:
            candidatos.append({'sinal': 'assinatura', 'estabelecimento': estab,
                               'valor': val, 'detalhe': f'{len(ms)} meses seguidos no mesmo valor',
                               'economia_mes': val})

    # repetição: 10+ vezes no mês
    for g in agrupar('estabelecimento'):
        if g['n'] >= 10:
            candidatos.append({'sinal': 'repeticao', 'estabelecimento': g['chave'],
                               'valor': g['valor'], 'detalhe': f"{g['n']} compras no mês",
                               'economia_mes': round(g['valor'] * 0.3, 2)})

    # salto: categoria 50%+ acima da média dos meses anteriores
    for g in agrupar('categoria'):
        m = media.get(g['chave'])
        if m and m > 0 and g['valor'] > m * 1.5:
            candidatos.append({'sinal': 'salto', 'estabelecimento': g['chave'],
                               'valor': g['valor'],
                               'detalhe': f"{round((g['valor']/m - 1)*100)}% acima da média "
                                          f"de {round(m, 2)}",
                               'economia_mes': round(g['valor'] - m, 2)})

    # parcela terminando: dinheiro que volta ao orçamento no mês que vem
    for i in lanc:
        if i.get('parcela_n') and i['parcela_n'] == i.get('parcela_total'):
            candidatos.append({'sinal': 'parcela_terminando',
                               'estabelecimento': i['estabelecimento'], 'valor': i['valor'],
                               'detalhe': f"última parcela ({i['parcela_n']}/{i['parcela_total']})",
                               'economia_mes': i['valor']})

    candidatos.sort(key=lambda c: -(c.get('economia_mes') or 0))

    return jsonify({
        'ok': True, 'mes_ref': mes, 'meses': meses,
        'faturas': faturas,
        'tem_aberta': any(f.get('situacao') == 'aberta' for f in faturas),
        'total_lancamentos': round(sum(i['valor'] or 0 for i in lanc), 2),
        'total_comprometido': round(sum(i['valor'] or 0 for i in fut), 2),
        # Encargo fica fora do gasto — IOF não é escolha de ninguém — mas vai
        # na resposta porque sem ele a tela não fecha com a fatura: 26.652,41 de
        # compra + 4,06 de IOF = 26.656,47, que é a soma dos PDFs. Um total que
        # não reconcilia com o papel do banco destrói a confiança na análise.
        'total_encargos': round(sum(i['valor'] or 0 for i in enc), 2),
        'por_categoria': agrupar('categoria'),
        'por_estabelecimento': agrupar('estabelecimento')[:40],
        'por_portador': agrupar('portador'),
        'por_cartao': agrupar('cartao'),
        'comprometido': agrupar('estabelecimento', fut),
        'candidatos': candidatos[:25],
        'media_anterior': {k: round(v, 2) for k, v in media.items()},
        # O compromisso real, que a primeira versão desta tela escondia: ela
        # chamava de "comprometido" só a próxima fatura (6.667,56) quando o que
        # ainda vai chegar é 23.789,32 — 3,5x mais.
        'parcelamentos': parc,
        'total_a_chegar': round(sum(p['resta'] for p in parc), 2),
        'decisao': _decisao(lanc),
        # A alavanca é da categoria e a decisão é do segmento: almoço de dia
        # útil e jantar de sábado são o mesmo "Restaurante e bar" e metas
        # completamente diferentes.
        'segmentos': _avaliar_segmentos(lanc, hist, estr, metas_cfg,
                                        n_meses if hist else 0, locais),
        'estrategias': list(estr.values()),
        # sexta é o único dia com os dois comportamentos; o que falta marcar
        # torna o número provisório, e isso tem de aparecer
        'sextas_pendentes': _sextas_pendentes(lanc, estr, locais),
        'locais': [dict(r) for r in locais.values()],
    })


@bp.route('/cartoes/locais', methods=['POST'])
def salvar_local():
    """Marca onde fica um estabelecimento. `local` nulo apaga a marcação.

    Marcar é o que separa almoço de trabalho de jantar numa sexta — a fatura não
    traz bairro, então esta é a única fonte confiável.
    """
    d = request.get_json(force=True) or {}
    padrao = (d.get('padrao') or '').strip()
    local = d.get('local')
    if not padrao:
        return jsonify({'ok': False, 'msg': 'Estabelecimento é obrigatório'}), 400
    with db() as conn:
        if local is None:
            conn.execute('DELETE FROM estabelecimento_local WHERE padrao=?', (padrao,))
            return jsonify({'ok': True, 'apagada': True})
        if local not in ('trabalho', 'outro'):
            return jsonify({'ok': False, 'msg': f'Local inválido: {local}'}), 400
        origem = d.get('origem') or 'manual'
        if origem not in ('fatura', 'pesquisa', 'ia', 'manual'):
            return jsonify({'ok': False, 'msg': f'Origem inválida: {origem}'}), 400
        # `evidencia` guarda o endereço achado e de onde veio. Uma marcação sem
        # rastro não se audita depois, e o usuário não tem como julgar se
        # confia nela.
        conn.execute(
            'INSERT INTO estabelecimento_local (padrao, local, origem, evidencia) '
            'VALUES (?,?,?,?) ON CONFLICT(padrao) DO UPDATE SET local=excluded.local, '
            ' origem=excluded.origem, evidencia=excluded.evidencia',
            (padrao, local, origem, d.get('evidencia')))
    return jsonify({'ok': True})


def _parcelamentos(lanc: list) -> list:
    """Parcelas em andamento e quanto falta de cada uma.

    Supõe parcelas iguais: é o que a fatura permite afirmar, já que ela só
    publica a parcela do mês e o total. Por isso o número vai rotulado como
    estimativa na tela.

    A mesma compra aparece duas vezes na fatura — parcela k no bloco do mês e
    k+1 no bloco de próximas — então a contagem sai só do bloco do mês, senão
    cada parcelamento entraria em dobro.
    """
    vistos, saida = set(), []
    for i in lanc:
        pn, pt = i.get('parcela_n'), i.get('parcela_total')
        if not pn or not pt:
            continue
        chave = (i['estabelecimento'], pt, round(i['valor'] or 0, 2))
        if chave in vistos:
            continue
        vistos.add(chave)
        faltam = max(0, pt - pn)
        saida.append({
            'estabelecimento': i.get('estabelecimento_norm') or i['estabelecimento'],
            'categoria': i.get('categoria'),
            'parcela_n': pn, 'parcela_total': pt,
            'valor_mes': round(i['valor'] or 0, 2),
            'faltam': faltam,
            'resta': round(faltam * (i['valor'] or 0), 2),
            'termina_agora': pn >= pt,
        })
    saida.sort(key=lambda x: -x['resta'])
    return saida


def _decisao(lanc: list) -> dict:
    """Quanto da fatura foi decidido **neste** mês.

    Separa o que ainda é escolha do que já era compromisso: em out/2026, de R$
    26.652,41, apenas R$ 1.100,80 foram parcelamento novo e R$ 6.170,00 eram
    parcelas de decisões antigas. Sem esta conta, "precisava gastar isso?" é
    perguntado sobre dinheiro que já não estava em disputa.
    """
    def soma(g):
        return round(sum(i['valor'] or 0 for i in g), 2)
    avista = [i for i in lanc if not i.get('parcela_n')]
    nova = [i for i in lanc if i.get('parcela_n') == 1]
    antiga = [i for i in lanc if (i.get('parcela_n') or 0) > 1]
    return {
        'avista': soma(avista), 'avista_n': len(avista),
        'parcela_nova': soma(nova), 'parcela_nova_n': len(nova),
        'parcela_antiga': soma(antiga), 'parcela_antiga_n': len(antiga),
    }


def _dossie(lanc: list, parc: list, media: dict) -> dict:
    """O que a IA recebe para aconselhar: agregados, nunca a base crua.

    Mandar 233 linhas convidaria o modelo a somar — e somar é do código. Ele
    recebe as contas já fechadas e opina sobre elas.
    """
    por_cat, por_est = {}, {}
    for i in lanc:
        nome = i.get('estabelecimento_norm') or i['estabelecimento']
        for chave, alvo in ((i.get('categoria') or '—', por_cat), (nome, por_est)):
            e = alvo.setdefault(chave, {'nome': chave, 'n': 0, 'total': 0.0})
            e['n'] += 1
            e['total'] += i['valor'] or 0
    for d in (por_cat, por_est):
        for e in d.values():
            e['total'] = round(e['total'], 2)
            e['medio'] = round(e['total'] / e['n'], 2)

    # cobranças repetidas no mesmo dia e no mesmo lugar: pode ser rotina, pode
    # ser cobrança dobrada — o app aponta, o usuário confere
    dia = {}
    for i in lanc:
        k = (i.get('data_compra'), i['estabelecimento'])
        dia.setdefault(k, []).append(i['valor'] or 0)
    repetidas = [{'data': k[0], 'estabelecimento': k[1], 'vezes': len(v),
                  'total': round(sum(v), 2)}
                 for k, v in dia.items() if len(v) > 1]
    repetidas.sort(key=lambda x: -x['total'])

    # Quem tem parcela em andamento não é candidato a corte neste mês: o
    # dinheiro já está contratado. Sem esta marca a IA sugeriu "cessar compras"
    # num parcelamento de 2x que já estava fechado.
    comprometido = {}
    for p in parc:
        comprometido[p['estabelecimento']] = \
            comprometido.get(p['estabelecimento'], 0.0) + p['resta']
    for e in por_est.values():
        e['parcelado'] = e['nome'] in comprometido
        if e['parcelado']:
            e['resta_parcelas'] = round(comprometido[e['nome']], 2)

    ordenado = sorted(por_est.values(), key=lambda e: -e['total'])
    return {
        'mes_total': round(sum(i['valor'] or 0 for i in lanc), 2),
        'decisao': _decisao(lanc),
        'por_categoria': sorted(por_cat.values(), key=lambda e: -e['total']),
        'por_estabelecimento': ordenado[:45],
        'frequentes': [e for e in sorted(por_est.values(), key=lambda e: -e['n'])
                       if e['n'] >= 4][:15],
        'micro_compras': [e for e in ordenado if e['medio'] < 15 and e['n'] >= 3],
        'cobrancas_no_mesmo_dia': repetidas[:12],
        'parcelamentos': parc[:25],
        'total_a_chegar': round(sum(p['resta'] for p in parc), 2),
        'media_meses_anteriores': {k: round(v, 2) for k, v in media.items()},
    }


TIPOS = ('necessidade', 'preco')


def _gemini_sugestoes(dossie: dict) -> list:
    """Conselho de economia a partir dos agregados.

    Duas perguntas, deliberadamente separadas, porque exigem decisões
    diferentes: `necessidade` ("precisava gastar isso?") e `preco` ("dava para
    gastar o mesmo por menos?"). Trocar de fornecedor não é cortar um hábito.
    """
    from .email_busca import _gemini_client, GEMINI_MODEL
    client = _gemini_client()
    if not client:
        return []
    from google.genai import types

    prompt = (
        'Você é um analista de finanças pessoais brasileiro, direto e concreto. '
        'Abaixo estão os agregados de UM mês de faturas de cartão de crédito de '
        'uma família (valores em R$, já somados por mim — não recalcule).\n\n'
        'Produza sugestões de economia de DOIS tipos:\n'
        '- "necessidade": o gasto podia simplesmente não ter acontecido, ou '
        'acontecer menos vezes. Use frequência e ticket médio como prova.\n'
        '- "preco": a mesma coisa podia ser comprada por menos — trocar de '
        'fornecedor, plano anual em vez de mensal, plano família em vez de '
        'individual, comprar no mercado em vez do delivery, evitar parcelamento '
        'com juros, rever anuidade ou seguro.\n\n'
        'Para cada sugestão devolva:\n'
        '- tipo: "necessidade" ou "preco"\n'
        '- alvo: o nome EXATO de uma categoria ou estabelecimento que aparece '
        'no dossiê. Nunca invente nome.\n'
        '- diagnostico: o que os números mostram, citando os números.\n'
        '- acao: o que fazer, concreto e executável nesta semana.\n'
        '- economia_mes: quanto por mês, no máximo o que foi gasto naquele alvo.\n'
        '- confianca: "alta", "media" ou "baixa".\n\n'
        'Regras: seja específico — "reduzir alimentação" não serve, '
        '"trocar 6 das 16 idas ao Homem de Mello por café em casa" serve. '
        'Não repita o mesmo alvo em duas sugestões do mesmo tipo. '
        'Estabelecimento com "parcelado": true tem parcela já contratada: NÃO '
        'sugira "parar de comprar" como economia deste mês, porque o dinheiro já '
        'saiu da decisão. Se valer a pena, fale em não recontratar e diga quanto '
        'volta quando as parcelas terminarem.\n'
        'Gasto que aconteceu 1 ou 2 vezes no mês é pontual: a economia dele NÃO '
        'se repete todo mês, e o diagnóstico deve dizer isso com clareza.\n'
        'ATENÇÃO às alavancas: o campo "alavancas" diz, por categoria, que tipo '
        'de economia existe ali. "volume" só admite sugestão do tipo '
        '"necessidade" (gastar menos vezes); "preco" e "substituicao" só admitem '
        '"preco" (mesma coisa mais barata, outro fornecedor, outro plano). '
        'Propor o tipo errado para a categoria faz a sugestão ser descartada.\n'
        'Priorize o que economiza mais dinheiro com menos sacrifício. '
        'Não sugira cortar saúde, educação ou seguro sem uma alternativa clara '
        'de mesmo serviço por menos. Entre 6 e 12 sugestões.\n\n'
        f'DOSSIÊ:\n{json.dumps(dossie, ensure_ascii=False, indent=1)}'
    )
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type='application/json',
                response_schema={
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'tipo': {'type': 'string', 'enum': list(TIPOS)},
                            'alvo': {'type': 'string'},
                            'diagnostico': {'type': 'string'},
                            'acao': {'type': 'string'},
                            'economia_mes': {'type': 'number'},
                            'confianca': {'type': 'string',
                                          'enum': ['alta', 'media', 'baixa']},
                        },
                        'required': ['tipo', 'alvo', 'diagnostico', 'acao'],
                    },
                },
            ),
        )
        return json.loads(resp.text)
    except Exception as e:
        print(f'[cartoes] falha ao gerar sugestões: {e}')
        return []


def _validar_sugestoes(brutas: list, dossie: dict) -> tuple:
    """Descarta o que não se sustenta nos números, e diz o que descartou.

    Um conselho com alvo inventado ou economia maior que o próprio gasto é pior
    que nenhum conselho: parece análise e é chute.
    """
    # a alavanca vem no dossiê, para prompt e validação lerem a mesma fonte
    alavanca_do = dossie.get('alavancas') or {}
    gasto, perfil = {}, {}
    for e in dossie['por_categoria'] + dossie['por_estabelecimento']:
        gasto[e['nome']] = e['total']
        # Recorrência não é opinião do modelo: sai dos dados. Sem isto, uma
        # compra única de R$ 561 virava "economia mensal" e, multiplicada por
        # 12, um número que destrói a credibilidade de toda a análise.
        if e.get('parcelado'):
            classe = 'comprometido'
        elif e['n'] >= 3:
            classe = 'recorrente'
        else:
            classe = 'pontual'
        perfil[e['nome']] = (classe, e.get('resta_parcelas') or 0.0)
    saida, recusadas = [], []
    vistos = set()
    for s in brutas if isinstance(brutas, list) else []:
        alvo = (s or {}).get('alvo', '')
        if alvo not in gasto:
            recusadas.append({'alvo': alvo, 'motivo': 'alvo não existe no dossiê'})
            continue
        if not (s.get('diagnostico') or '').strip() or not (s.get('acao') or '').strip():
            recusadas.append({'alvo': alvo, 'motivo': 'sem diagnóstico ou sem ação'})
            continue
        # Dente novo da validação: a alavanca é da categoria, então uma sugestão
        # de cortar frequência numa categoria cujo único caminho é preço não é
        # conselho, é ruído com cara de análise.
        alav = alavanca_do.get(alvo)
        if alav == 'nenhum':
            recusadas.append({'alvo': alvo, 'motivo': 'categoria sem alavanca de economia'})
            continue
        esperado = TIPO_DA_ALAVANCA.get(alav)
        if esperado and s.get('tipo') != esperado:
            recusadas.append({'alvo': alvo,
                              'motivo': f"alavanca da categoria é '{alav}', "
                                        f"que só admite sugestão de '{esperado}'"})
            continue
        if (s.get('tipo'), alvo) in vistos:
            continue
        vistos.add((s.get('tipo'), alvo))
        eco = s.get('economia_mes')
        teto = gasto[alvo]
        limitada = False
        if not isinstance(eco, (int, float)) or eco < 0:
            eco = None
        elif eco > teto:
            eco, limitada = teto, True
        classe, resta = perfil.get(alvo, ('pontual', 0.0))
        valor = round(eco, 2) if eco is not None else None
        saida.append({
            'tipo': s['tipo'] if s.get('tipo') in TIPOS else 'necessidade',
            'alvo': alvo, 'gasto_no_mes': teto,
            'diagnostico': s['diagnostico'].strip(), 'acao': s['acao'].strip(),
            'recorrencia': classe,
            # três coisas diferentes, nunca somadas: o que cai todo mês, o que
            # se ganha uma vez, e o que só volta quando a parcela acabar
            'economia_mes': valor if classe == 'recorrente' else None,
            'ganho_unico': valor if classe == 'pontual' else None,
            'resta_parcelas': round(resta, 2) if classe == 'comprometido' else None,
            'economia_limitada_ao_gasto': limitada,
            'confianca': s.get('confianca') or 'media',
        })
    saida.sort(key=lambda x: -((x['economia_mes'] or 0) * 12
                               + (x['ganho_unico'] or 0)
                               + (x['resta_parcelas'] or 0)))
    return saida, recusadas


@bp.route('/cartoes/sugestoes')
def sugestoes():
    """Onde economizar, com o número que sustenta cada conselho."""
    mes = request.args.get('mes_ref')
    with db() as conn:
        if not mes:
            r = conn.execute('SELECT MAX(mes_ref) m FROM fatura_cartao').fetchone()
            mes = r['m'] if r else None
        if not mes:
            return jsonify({'ok': True, 'mes_ref': None, 'vazio': True, 'sugestoes': []})
        itens = [dict(r) for r in conn.execute(
            'SELECT i.* FROM fatura_item i JOIN fatura_cartao f ON f.id=i.fatura_id '
            "WHERE f.mes_ref=? AND i.secao='lancamento'", (mes,)).fetchall()]
        hist = [dict(r) for r in conn.execute(
            'SELECT i.categoria, i.valor, f.mes_ref FROM fatura_item i '
            "JOIN fatura_cartao f ON f.id=i.fatura_id WHERE f.mes_ref<? AND i.secao='lancamento'",
            (mes,)).fetchall()]

    n_meses = len({h['mes_ref'] for h in hist}) or 1
    media = {}
    for h in hist:
        media[h['categoria']] = media.get(h['categoria'], 0.0) + (h['valor'] or 0)
    media = {k: v / n_meses for k, v in media.items()}

    with db() as conn:
        estr = _estrategias(conn)

    # Categoria sem alavanca não entra no dossiê: a IA não vê, não opina, e o
    # usuário não gasta atenção lendo sugestão sobre dentista.
    sem_alavanca = {c for c, e in estr.items() if e['alavanca'] == 'nenhum'}
    visiveis = [i for i in itens if (i.get('categoria') or '') not in sem_alavanca]
    aguardando = sorted({(i.get('categoria') or '') for i in itens
                         if estr.get(i.get('categoria') or '', {}).get('alavanca')
                         == 'requer_detalhe'})

    dossie = _dossie(visiveis, _parcelamentos(visiveis), media)
    dossie['alavancas'] = {c: e['alavanca'] for c, e in estr.items()}
    brutas = _gemini_sugestoes(dossie)
    sug, recusadas = _validar_sugestoes(brutas, dossie)
    return jsonify({
        'ok': True, 'mes_ref': mes, 'dossie': dossie, 'sugestoes': sug,
        'recusadas': recusadas, 'com_ia': bool(brutas),
        'meses_de_historico': n_meses if hist else 0,
        # Três totais, deliberadamente separados. Um número só aqui seria uma
        # promessa que os dados não sustentam.
        'economia_recorrente_mes': round(sum(s['economia_mes'] or 0 for s in sug), 2),
        'ganho_pontual': round(sum(s['ganho_unico'] or 0 for s in sug), 2),
        'a_liberar_parcelas': round(sum(s['resta_parcelas'] or 0 for s in sug), 2),
        # o que ficou deliberadamente fora, e por quê — melhor dizer que omitir
        'sem_alavanca': sorted(sem_alavanca),
        'aguardando_detalhe': aguardando,
    })


# ── estratégia por categoria ──────────────────────────────────────────────────
#
# A alavanca autoriza **um** tipo de sugestão, e só ele. Sem este mapa a IA
# propunha corte de frequência em categoria cujo único caminho é preço.
TIPO_DA_ALAVANCA = {
    'volume': 'necessidade',
    'preco': 'preco',
    'substituicao': 'preco',
}

ALAVANCAS = ('volume', 'preco', 'substituicao', 'nenhum', 'requer_detalhe')
SEGMENTACOES = ('nenhuma', 'dia_semana', 'ticket')
LIMITE_TICKET_PADRAO = 200.0


def _estrategias(conn) -> dict:
    return {r['categoria']: dict(r)
            for r in conn.execute('SELECT * FROM categoria_estrategia').fetchall()}


def _metas(conn) -> dict:
    return {(r['categoria'], r['segmento']): dict(r)
            for r in conn.execute('SELECT * FROM meta_categoria').fetchall()}


def _locais(conn) -> dict:
    """Onde fica cada estabelecimento marcado. Padrão mais longo primeiro.

    A ordem importa: casando por substring, um padrão curto venceria um longo e
    mais específico — e os textos crus diferem entre emissores (`GLUCCO` no Itaú
    contra `G LUCCO SAO PAULO` no Bradesco), que é justamente por que o
    casamento é por substring e não por igualdade.
    """
    rs = conn.execute(
        'SELECT padrao, local, origem, evidencia FROM estabelecimento_local').fetchall()
    return {r['padrao']: dict(r)
            for r in sorted(rs, key=lambda r: -len(r['padrao'] or ''))}


def _local_do(estab: str, locais: dict):
    alvo = _sem_acento(estab)
    for padrao, r in locais.items():
        if _sem_acento(padrao) in alvo:
            return r['local']
    return None


def _segmento(item: dict, estr: dict, locais: dict = None) -> str:
    """Em que segmento da categoria este lançamento cai.

    Vazio quando a categoria não é segmentada, e aí a meta vale para ela inteira.

    Sexta é o caso difícil, e o único dia que contém os dois comportamentos:
    almoço de trabalho, que não se evita, e jantar, que se evita. O critério é o
    **lugar** — perto do Itaim Bibi é almoço; fora dali, numa sexta, é jantar, e
    jantar conta no balde do fim de semana.

    Sexta **sem marcação** cai em `dia_util` de propósito: o default conservador
    não infla o balde evitável, onde a meta é agressiva. Quem conta quanto ainda
    está sem marcar é `_sextas_pendentes`, para o número ser lido como
    provisório.
    """
    e = estr.get(item.get('categoria') or '')
    if not e:
        return ''
    # Evitável por natureza: iFood e hotel não são almoço de trabalho em dia
    # nenhum, então o dia da semana não entra na conta.
    if e.get('discricionario'):
        return 'fim_de_semana'
    if e.get('segmentacao') == 'dia_semana':
        d = item.get('data_compra')
        if not d:
            return ''
        wd = date.fromisoformat(d).weekday()
        if wd >= 5:
            return 'fim_de_semana'
        # Só onde o ato é comer fora. Em hotel o bairro não diz nada, e em
        # delivery o que importa é o endereço de entrega, não o do restaurante.
        if (wd == 4 and e.get('sexta_por_local')
                and _local_do(item.get('estabelecimento') or '', locais or {}) == 'outro'):
            return 'fim_de_semana'
        return 'dia_util'
    if e.get('segmentacao') == 'ticket':
        lim = e.get('limite_ticket') or LIMITE_TICKET_PADRAO
        return 'grande' if (item.get('valor') or 0) >= lim else 'pequeno'
    return ''


def _sextas_pendentes(lanc: list, estr: dict, locais: dict) -> dict:
    """Quanto de sexta ainda está sem marcação de lugar.

    O default conservador manda a sexta não marcada para `dia_util`, o que está
    certo como default e errado como silêncio: sem este número o usuário leria
    como definitivo um valor que uma marcação pode mover de balde.
    """
    pend = {}
    for i in lanc:
        e = estr.get(i.get('categoria') or '')
        # pendência só existe onde a marcação muda alguma coisa
        if (not e or e.get('segmentacao') != 'dia_semana'
                or not e.get('sexta_por_local') or not i.get('data_compra')):
            continue
        if date.fromisoformat(i['data_compra']).weekday() != 4:
            continue
        if _local_do(i.get('estabelecimento') or '', locais):
            continue
        k = i.get('estabelecimento_norm') or i['estabelecimento']
        r = pend.setdefault(k, {'estabelecimento': k, 'bruto': i['estabelecimento'],
                                'categoria': i.get('categoria'), 'n': 0, 'valor': 0.0})
        r['n'] += 1
        r['valor'] += i['valor'] or 0
    itens = sorted(({**r, 'valor': round(r['valor'], 2)} for r in pend.values()),
                   key=lambda x: -x['valor'])
    return {'itens': itens, 'n': sum(r['n'] for r in itens),
            'valor': round(sum(r['valor'] for r in itens), 2)}


# Corte sugerido, em %, por alavanca. É ponto de partida para o usuário mexer,
# não veredito — e por isso a tela mostra o campo editável, não um número fixo.
# O balde evitável aceita corte maior justamente porque é onde a decisão existe.
CORTE_SUGERIDO = {
    ('volume', 'fim_de_semana'): 30,
    ('volume', None): 15,
    ('preco', None): 12,
    ('substituicao', None): 20,
    ('requer_detalhe', None): 0,
    ('nenhum', None): 0,
}

# Onde eu tenho uma razão concreta para divergir do padrão da alavanca.
CORTE_POR_CATEGORIA = {
    'Delivery': 40,       # 8 pedidos, R$ 684,51: é o mais fácil de cortar
    'Assinaturas': 30,    # plano família e anual em vez de mensal
    'Viagem': 20,         # 3 compras de ticket alto: não se corta pela metade
}

TATICAS = {
    ('volume', 'dia_util'): [
        'Levar marmita 1 vez por semana',
        'Cortar o café/lanche intermediário',
        'Trocar restaurante por opção mais barata perto do trabalho',
    ],
    ('volume', 'fim_de_semana'): [
        'Cortar 1 saída a cada 3',
        'Cozinhar em casa nos fins de semana',
        'Definir um teto por saída',
    ],
    ('volume', None): [
        'Reduzir a frequência',
        'Definir um teto mensal',
        'Comprar só o planejado',
    ],
    ('preco', None): [
        'Trocar para atacarejo ou rede mais barata',
        'Comprar a granel ou em volume',
        'Usar cupom e cashback do app',
    ],
    ('substituicao', None): [
        'Cotar com 3 concorrentes',
        'Trocar plano mensal por anual',
        'Migrar para plano família',
        'Cancelar o que está ocioso',
    ],
    ('requer_detalhe', None): [
        'Aguardando a nota item a item para decidir',
    ],
}


def _sugestao_de_corte(categoria: str, alavanca: str, segmento: str):
    """Quanto cortar, e como — como ponto de partida editável."""
    pct = CORTE_POR_CATEGORIA.get(categoria)
    if pct is None:
        pct = CORTE_SUGERIDO.get((alavanca, segmento))
        if pct is None:
            pct = CORTE_SUGERIDO.get((alavanca, None), 0)
    taticas = (TATICAS.get((alavanca, segmento))
               or TATICAS.get((alavanca, None)) or [])
    return pct, taticas


def _avaliar_segmentos(lanc: list, hist: list, estr: dict, metas: dict,
                       n_meses_hist: int, locais: dict = None) -> list:
    """Realizado por (categoria, segmento), com a meta ao lado.

    A meta percentual **não é calculada sem histórico**: com um mês importado a
    média é o próprio mês e a meta se autoconfirmaria. Nesse caso devolve o
    motivo em vez de um número — mesma regra do resto da tela, onde um número
    que não se sustenta é pior que nenhum.
    """
    atual = {}
    for i in lanc:
        cat = i.get('categoria') or '—'
        k = (cat, _segmento(i, estr, locais))
        e = atual.setdefault(k, {'categoria': cat, 'segmento': k[1], 'n': 0, 'valor': 0.0})
        e['n'] += 1
        e['valor'] += i['valor'] or 0

    base = {}
    for h in hist:
        k = (h.get('categoria') or '—', _segmento(h, estr, locais))
        base[k] = base.get(k, 0.0) + (h['valor'] or 0)

    saida = []
    for k, e in atual.items():
        cat, seg = k
        est = estr.get(cat) or {}
        # meta do segmento; se não houver, a da categoria inteira
        m = metas.get(k) or (metas.get((cat, '')) if seg else None)
        valor = round(e['valor'], 2)
        alav = est.get('alavanca', 'volume')

        # A baseline é o que o % morde. Com histórico, a média dos meses
        # anteriores; sem histórico, o próprio mês — e a tela DIZ qual das duas,
        # porque um percentual sem a base à vista não quer dizer nada.
        if n_meses_hist and base.get(k):
            baseline = round(base.get(k, 0.0) / n_meses_hist, 2)
            baseline_origem = 'media_meses_anteriores'
        else:
            baseline = valor
            baseline_origem = 'mes_atual'

        pct_sug, taticas = _sugestao_de_corte(cat, alav, seg)
        meta_valor = None
        if m:
            if m['tipo'] == 'absoluto':
                meta_valor = m['valor']
            else:
                meta_valor = round(baseline * (1 - (m['valor'] or 0) / 100.0), 2)

        saida.append({
            'categoria': cat, 'segmento': seg, 'n': e['n'], 'valor': valor,
            'ticket_medio': round(valor / e['n'], 2) if e['n'] else 0,
            'alavanca': alav,
            'segmentacao': est.get('segmentacao', 'nenhuma'),
            'observacao': est.get('observacao'),
            'baseline': baseline,
            'baseline_origem': baseline_origem,
            # sugestão: ponto de partida editável, nunca imposição
            'corte_sugerido': pct_sug,
            'taticas': taticas,
            'tatica': (m.get('tatica') if m else None) or (taticas[0] if taticas else None),
            'tatica_salva': bool(m and m.get('tatica')),
            'meta_tipo': m['tipo'] if m else None,
            'meta_bruta': m['valor'] if m else None,
            'meta_valor': meta_valor,
            # quanto se economiza se a meta for cumprida
            'economia': (round(baseline - meta_valor, 2)
                         if meta_valor is not None and baseline > meta_valor else None),
            'meta_indisponivel': None,
            'excedeu': (round(valor - meta_valor, 2)
                        if meta_valor is not None and valor > meta_valor else None),
        })
    saida.sort(key=lambda x: -x['valor'])
    return saida


@bp.route('/cartoes/estrategias')
def estrategias():
    with db() as conn:
        estr = _estrategias(conn)
    return jsonify({'ok': True, 'estrategias': list(estr.values()),
                    'categorias': CATEGORIAS, 'alavancas': list(ALAVANCAS),
                    'segmentacoes': list(SEGMENTACOES),
                    'tipo_da_alavanca': TIPO_DA_ALAVANCA})


@bp.route('/cartoes/estrategias', methods=['POST'])
def salvar_estrategia():
    d = request.get_json(force=True) or {}
    cat = (d.get('categoria') or '').strip()
    alav = d.get('alavanca')
    seg = d.get('segmentacao') or 'nenhuma'
    if not cat:
        return jsonify({'ok': False, 'msg': 'Categoria é obrigatória'}), 400
    if alav not in ALAVANCAS:
        return jsonify({'ok': False, 'msg': f'Alavanca inválida: {alav}'}), 400
    if seg not in SEGMENTACOES:
        return jsonify({'ok': False, 'msg': f'Segmentação inválida: {seg}'}), 400
    lim = d.get('limite_ticket')
    # `sexta_por_local` só faz sentido junto de `dia_semana`: fora dali a
    # marcação de lugar não reclassifica nada.
    sexta = 1 if (d.get('sexta_por_local') and seg == 'dia_semana') else 0
    disc = 1 if d.get('discricionario') else 0
    with db() as conn:
        conn.execute(
            'INSERT INTO categoria_estrategia '
            '(categoria, alavanca, segmentacao, limite_ticket, observacao, '
            ' sexta_por_local, discricionario) VALUES (?,?,?,?,?,?,?) '
            'ON CONFLICT(categoria) DO UPDATE SET alavanca=excluded.alavanca, '
            ' segmentacao=excluded.segmentacao, limite_ticket=excluded.limite_ticket, '
            ' observacao=excluded.observacao, sexta_por_local=excluded.sexta_por_local, '
            ' discricionario=excluded.discricionario',
            (cat, alav, seg, lim, d.get('observacao'), sexta, disc))
    return jsonify({'ok': True})


@bp.route('/cartoes/metas', methods=['POST'])
def salvar_meta():
    """Grava a meta de um (categoria, segmento). `valor` nulo apaga."""
    d = request.get_json(force=True) or {}
    cat = (d.get('categoria') or '').strip()
    seg = d.get('segmento') or ''
    tipo = d.get('tipo') or 'absoluto'
    valor = d.get('valor')
    if not cat:
        return jsonify({'ok': False, 'msg': 'Categoria é obrigatória'}), 400
    if tipo not in ('absoluto', 'percentual'):
        return jsonify({'ok': False, 'msg': f'Tipo inválido: {tipo}'}), 400
    with db() as conn:
        if valor is None:
            conn.execute('DELETE FROM meta_categoria WHERE categoria=? AND segmento=?',
                         (cat, seg))
            return jsonify({'ok': True, 'apagada': True})
        if tipo == 'percentual' and not (0 < float(valor) < 100):
            return jsonify({'ok': False,
                            'msg': 'Meta percentual é o corte desejado, entre 0 e 100'}), 400
        conn.execute(
            'INSERT INTO meta_categoria (categoria, segmento, tipo, valor, tatica) '
            'VALUES (?,?,?,?,?) '
            'ON CONFLICT(categoria, segmento) DO UPDATE SET tipo=excluded.tipo, '
            ' valor=excluded.valor, tatica=excluded.tatica',
            (cat, seg, tipo, float(valor), d.get('tatica')))
    return jsonify({'ok': True})
