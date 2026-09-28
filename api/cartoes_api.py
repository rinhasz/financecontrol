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
    'Esporte', 'Eletrônicos', 'Filhos', 'Outros', 'Encargos',
]

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
        if it['secao'] == 'encargo':
            # IOF não é compra: não tem estabelecimento nem escolha por trás.
            # Mandá-lo para a IA rendeu "Repasse de IOF em R$ -> Outros", que
            # polui a análise com uma linha que ninguém decidiu gastar.
            it['categoria'], it['origem_categoria'] = 'Encargos', 'fatura'
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
    if not cab.get('data_vencimento'):
        return None, 'Não consegui achar a data de vencimento na fatura.'
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
        antiga = conn.execute(
            'SELECT id FROM fatura_cartao WHERE cartao=? AND data_vencimento=?',
            (cab['rotulo'], cab['data_vencimento'])).fetchone()
        if antiga:
            # substituição limpa: os itens da importação anterior saem antes,
            # senão a fatura reimportada soma em dobro na análise
            conn.execute('DELETE FROM fatura_item WHERE fatura_id=?', (antiga['id'],))
            conn.execute('DELETE FROM fatura_cartao WHERE id=?', (antiga['id'],))

        cur = conn.execute(
            'INSERT INTO fatura_cartao (cartao, final, arquivo, mes_ref, data_vencimento, '
            ' total_fatura, total_lancamentos, total_proximas_faturas, pagamento_minimo, limite_total) '
            'VALUES (?,?,?,?,?,?,?,?,?,?)',
            (cab['rotulo'], cab.get('final'), cab.get('arquivo'), cab.get('mes_ref'),
             cab['data_vencimento'], cab.get('total_fatura'), cab.get('total_lancamentos'),
             cab.get('total_proximas_faturas'), cab.get('pagamento_minimo'),
             cab.get('limite_total')))
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
    return jsonify({'ok': True, 'itens': [dict(r) for r in rs], 'categorias': CATEGORIAS})


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
        hist = [dict(r) for r in conn.execute(
            'SELECT i.categoria, i.estabelecimento, i.valor, f.mes_ref FROM fatura_item i '
            "JOIN fatura_cartao f ON f.id=i.fatura_id WHERE f.mes_ref<? AND i.secao='lancamento'",
            (mes,)).fetchall()]
        meses = [r['mes_ref'] for r in conn.execute(
            'SELECT DISTINCT mes_ref FROM fatura_cartao ORDER BY mes_ref DESC').fetchall()]

    lanc = [i for i in itens if i['secao'] == 'lancamento']
    fut = [i for i in itens if i['secao'] == 'proxima_fatura']
    enc = [i for i in itens if i['secao'] == 'encargo']

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
    })
