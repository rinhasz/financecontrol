import base64
import datetime
import io
import json
import os
import re
import threading

import msal
import requests
from flask import Blueprint, jsonify, request

from .db import get_db, parse_number
from .importacao import normalize_text, tem_palavra

bp = Blueprint('email_busca', __name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_CACHE_PATH = os.path.join(BASE_DIR, '.msal_token_cache.json')
DICIONARIO_EMAILS_PATH = os.path.join(BASE_DIR, 'docs', '09-dicionario-emails.md')

# A Microsoft desativou autenticação por senha (inclusive senha de app) para
# IMAP em contas Outlook/Hotmail pessoais em abril/2026 — o caminho que
# restou é OAuth2 via Microsoft Graph. Requer um app registrado em
# portal.azure.com (client ID público, sem segredo) — ver .env.example.
CLIENT_ID = os.environ.get('EMAIL_CLIENT_ID')
AUTHORITY = 'https://login.microsoftonline.com/common'
SCOPES = ['Mail.Read']
GRAPH = 'https://graph.microsoft.com/v1.0'

# Se configurada, o Gemini faz a classificação (é fatura?) e extração
# (despesa provável, valor, linha digitável) — mais robusto que palavra-chave
# porque entende semanticamente ("SulAmérica" -> despesa "convenio ...") sem
# precisar que o catálogo liste o nome da operadora. Sem a chave, cai para a
# lógica por palavra-chave/regex abaixo.
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
GEMINI_MODEL = 'gemini-flash-latest'
# Caixas de entrada reais têm milhares de emails por período (propaganda,
# newsletter etc.) — mandar tudo numa chamada só pro Gemini é frágil (prompt
# gigante, mais chance de estourar limite/truncar) e, se falhar, derruba a
# busca inteira sem avisar. Processar em lotes limita o dano de uma falha
# isolada e mantém cada chamada num tamanho previsível.
GEMINI_BATCH_SIZE = 200

# Linha digitável de boleto: 5 blocos de dígitos (com ou sem pontuação)
RE_LINHA_DIGITAVEL = re.compile(
    r'\d{5}[.\s]?\d{5}\s+\d{5}[.\s]?\d{6}\s+\d{5}[.\s]?\d{6}\s+\d\s+\d{14,17}'
)
# Código Pix "copia e cola" (BR Code / EMV): sempre começa com o payload
# format indicator "00020..." e termina no campo de CRC "6304XXXX" — mesma
# ideia da linha digitável, uma assinatura estrutural fixa que dá pra casar
# por regex sem precisar entender o conteúdo em si. Aceita espaço/quebra de
# linha no meio porque o HTML do email pode "reformatar" o texto ao virar
# texto puro (ver _extrair_texto_html) mesmo o código original não tendo —
# os espaços são removidos do resultado antes de guardar/validar.
RE_PIX = re.compile(r'00020\d{2}[0-9A-Za-z.\-/*\s]{40,600}?6304[0-9A-Fa-f]{4}')
RE_VALOR = re.compile(r'R\$\s*([\d.]{1,12},\d{2})')


def _linha_digitavel_valida(s) -> bool:
    """Sanity check obrigatório pro que o Gemini extrai — um LLM pode
    'alucinar' uma linha digitável plausível (ou devolver texto de
    placeholder/nome de campo) em vez de dizer que não achou nada. Nunca
    confiar cegamente num código usado pra pagamento."""
    if not isinstance(s, str):
        return False
    digitos = re.sub(r'\D', '', s)
    if len(digitos) not in (47, 48):
        return False
    if len(set(digitos)) <= 2:  # tudo zero, tudo um dígito repetido etc.
        return False
    if digitos in ('12345678901234567890123456789012345678901234567', '123456789012345678901234567890123456789012345678'):
        return False
    return True


def _codigo_pix_valido(s) -> bool:
    """Mesma cautela anti-alucinação que _linha_digitavel_valida, adaptada
    à estrutura do Pix copia-e-cola: tamanho plausível, começa com o
    indicador de payload e tem o campo de CRC perto do fim."""
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not (40 <= len(s) <= 700):
        return False
    if not s.startswith('0002'):
        return False
    if '6304' not in s[-12:]:
        return False
    return True

# Fallback quando nenhuma despesa do catálogo bate por palavra-chave — muitas
# operadoras (ex: SulAmérica) não aparecem no nome da despesa (ex: "convenio
# marco antonio"), só no assunto do email. Um assunto com essas palavras já
# entra na lista de candidatos, mesmo sem despesa sugerida.
ASSUNTO_FATURA_KEYWORDS = ['fatura', 'boleto', 'conta', 'cobranca', 'cobrança', 'vencimento', 'mensalidade', 'invoice']

_lock = threading.Lock()
_pending = {}  # device flow em andamento — app desktop de 1 usuário, sem sessão

# Estado da busca de emails em andamento — app desktop de 1 usuário, então uma
# busca por vez é suficiente (sem necessidade de job_id/sessão). A busca roda
# dia a dia numa thread de fundo (ver _rodar_busca) para: (1) nunca segurar
# uma requisição HTTP por minutos — o risco real que travou a busca de 2
# meses/3005 emails; (2) permitir atualizar a tela incrementalmente conforme
# cada dia termina; (3) permitir cancelar e ainda assim aproveitar o que já
# foi encontrado até ali.
_busca_job = {
    'rodando': False, 'cancelar': False, 'cancelado': False, 'concluido': False,
    'erro': None, 'avisos': [], 'boletos': [], 'total_emails': 0,
    'despesas_pesquisadas': 0, 'dia_atual': None, 'total_dias': 0,
    'dias_processados': 0, 'periodo': None,
}


def _dias_no_periodo(ini: str, fim: str):
    d0 = datetime.date.fromisoformat(ini)
    d1 = datetime.date.fromisoformat(fim)
    dias = []
    d = d0
    while d <= d1:
        dias.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return dias


def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if os.path.exists(TOKEN_CACHE_PATH):
        with open(TOKEN_CACHE_PATH, 'r', encoding='utf-8') as f:
            cache.deserialize(f.read())
    return cache


def _save_cache(cache: msal.SerializableTokenCache):
    if cache.has_state_changed:
        with open(TOKEN_CACHE_PATH, 'w', encoding='utf-8') as f:
            f.write(cache.serialize())


def _msal_app(cache: msal.SerializableTokenCache) -> msal.PublicClientApplication:
    return msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=cache)


def _configurado() -> bool:
    return bool(CLIENT_ID)


def _conta_conectada():
    if not CLIENT_ID:
        return None
    app_ = _msal_app(_load_cache())
    accounts = app_.get_accounts()
    return accounts[0]['username'] if accounts else None


def _token_valido():
    """Tenta obter um token de acesso do cache local, sem interação do usuário."""
    if not CLIENT_ID:
        return None
    cache = _load_cache()
    app_ = _msal_app(cache)
    accounts = app_.get_accounts()
    if not accounts:
        return None
    result = app_.acquire_token_silent(SCOPES, account=accounts[0])
    _save_cache(cache)
    return result.get('access_token') if result else None


@bp.route('/email/status')
def status():
    return jsonify({'configurado': _configurado(), 'conectado': _conta_conectada()})


@bp.route('/email/conectar/iniciar', methods=['POST'])
def conectar_iniciar():
    if not CLIENT_ID:
        return jsonify({'ok': False, 'msg': 'EMAIL_CLIENT_ID não configurado no .env — veja .env.example.'}), 400

    cache = _load_cache()
    app_ = _msal_app(cache)
    flow = app_.initiate_device_flow(scopes=SCOPES)
    if 'user_code' not in flow:
        return jsonify({'ok': False, 'msg': f'Falha ao iniciar login: {flow.get("error_description", flow)}'}), 502

    with _lock:
        _pending['flow'] = flow
        _pending['cache'] = cache

    return jsonify({
        'ok': True,
        'verification_uri': flow['verification_uri'],
        'user_code': flow['user_code'],
        'expires_in': flow['expires_in'],
    })


@bp.route('/email/conectar/finalizar', methods=['POST'])
def conectar_finalizar():
    """Bloqueia até o usuário completar o login no navegador (ou o código
    expirar) — o servidor Flask roda com threaded=True para essa espera não
    travar o resto do app."""
    with _lock:
        flow = _pending.get('flow')
        cache = _pending.get('cache')

    if not flow:
        return jsonify({'ok': False, 'msg': 'Nenhum login em andamento — clique em "Conectar" de novo.'}), 400

    app_ = _msal_app(cache)
    result = app_.acquire_token_by_device_flow(flow)
    _save_cache(cache)

    with _lock:
        _pending.pop('flow', None)
        _pending.pop('cache', None)

    if 'access_token' not in result:
        return jsonify({'ok': False, 'msg': result.get('error_description', 'Login não concluído ou expirado.')}), 400

    accounts = app_.get_accounts()
    return jsonify({'ok': True, 'conta': accounts[0]['username'] if accounts else None})


def _extrair_texto_html(html: str) -> str:
    return re.sub(r'<[^>]+>', ' ', html or '')


def _extrair_pdf(conteudo_base64: str) -> str:
    try:
        import pdfplumber
        payload = base64.b64decode(conteudo_base64)
        textos = []
        with pdfplumber.open(io.BytesIO(payload)) as pdf:
            for page in pdf.pages:
                textos.append(page.extract_text() or '')
        return '\n'.join(textos)
    except Exception:
        return ''


def _mensagens_no_periodo(token: str, ini: str, fim: str):
    """Busca metadados de todas as mensagens do INBOX no período uma única
    vez (evita repetir a mesma consulta para cada despesa). Não inclui o
    corpo — uma caixa de entrada real pode ter milhares de emails num
    período de 1-2 meses (propaganda, newsletter etc.), e baixar o HTML
    completo de todos de uma vez é lento e consome muita memória à toa; o
    corpo só é buscado depois, sob demanda, para quem passar na
    classificação (ver _texto_completo)."""
    headers = {'Authorization': f'Bearer {token}'}
    url = f'{GRAPH}/me/mailFolders/inbox/messages'
    params = {
        '$filter': f'receivedDateTime ge {ini}T00:00:00Z and receivedDateTime le {fim}T23:59:59Z',
        '$select': 'id,subject,from,receivedDateTime,hasAttachments',
        '$top': '100',
    }

    mensagens = []
    while url:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        mensagens.extend(payload.get('value', []))
        url = payload.get('@odata.nextLink')
        params = None  # já embutido no nextLink
    return mensagens


def _despesa_sugerida(assunto: str, remetente: str, despesas: list):
    """Acha a primeira despesa do catálogo cujas palavras-chave batem com o
    assunto/remetente do email — só uma sugestão, o usuário confirma ou troca."""
    alvo = normalize_text(f'{assunto} {remetente}')
    for d in despesas:
        regras = json.loads(d['regras_match'])
        keywords = [kw for kw in regras.get('palavras_chave', []) if kw]
        if keywords and all(tem_palavra(alvo, kw) for kw in keywords):
            return d
    for d in despesas:
        regras = json.loads(d['regras_match'])
        keywords = [kw for kw in regras.get('palavras_chave', []) if kw]
        if keywords and any(tem_palavra(alvo, kw) for kw in keywords):
            return d
    return None


def _parece_fatura(assunto: str) -> bool:
    alvo = normalize_text(assunto)
    return any(tem_palavra(alvo, kw) for kw in ASSUNTO_FATURA_KEYWORDS)


def _texto_completo(token: str, m: dict) -> str:
    """Texto do corpo + PDFs anexados de uma mensagem — busca o corpo sob
    demanda (só é chamada pra quem já passou na classificação/filtro)."""
    headers = {'Authorization': f'Bearer {token}'}
    texto = ''
    try:
        body_resp = requests.get(f'{GRAPH}/me/messages/{m["id"]}', headers=headers,
                                  params={'$select': 'body'}, timeout=30)
        body_resp.raise_for_status()
        texto = _extrair_texto_html(body_resp.json().get('body', {}).get('content', ''))
    except Exception as e:
        print(f'[email_busca] falha ao buscar corpo de {m["id"]}: {e}')

    if m.get('hasAttachments'):
        try:
            att_resp = requests.get(f'{GRAPH}/me/messages/{m["id"]}/attachments', headers=headers, timeout=30)
            att_resp.raise_for_status()
            for att in att_resp.json().get('value', []):
                if att.get('contentType') == 'application/pdf' and att.get('contentBytes'):
                    texto += '\n' + _extrair_pdf(att['contentBytes'])
        except Exception as e:
            print(f'[email_busca] falha ao buscar anexos de {m["id"]}: {e}')

    return texto


def _extrair_codigo_regex(texto: str):
    """Fallback sem Gemini: tenta achar boleto primeiro, depois Pix.
    Retorna (codigo, tipo_codigo, valor)."""
    valores = RE_VALOR.findall(texto)
    valor = valores[0] if valores else None

    linha = RE_LINHA_DIGITAVEL.search(texto)
    if linha:
        return re.sub(r'\s+', ' ', linha.group()).strip(), 'boleto', valor

    pix = RE_PIX.search(texto)
    if pix:
        return re.sub(r'\s+', '', pix.group()).strip(), 'pix', valor

    return None, None, valor


def _gemini_client():
    if not GEMINI_API_KEY:
        return None
    from google import genai
    from google.genai import types
    # Sem timeout explícito, uma chamada que trava (rede, sobrecarga da API)
    # fica pendurada pra sempre e a busca nunca termina nem retorna erro —
    # foi exatamente o que aconteceu numa caixa de entrada com ~3000 emails
    # no período. 60s é generoso pra um lote de classificação ou extração.
    return genai.Client(api_key=GEMINI_API_KEY, http_options=types.HttpOptions(timeout=60_000))


def _gemini_classificar(client, mensagens: list):
    """Descobre quais mensagens parecem fatura/boleto/conta a pagar (barato:
    só assunto+remetente). Processa em lotes de GEMINI_BATCH_SIZE — uma
    caixa de entrada real pode ter milhares de emails num período de 1-2
    meses, e um prompt com tudo de uma vez é frágil (mais chance de estourar
    limite, truncar ou dar timeout); em lotes, uma falha isolada não derruba
    a busca inteira. Retorna (ids_fatura, lotes_com_falha)."""
    from google.genai import types

    schema = {
        'type': 'object',
        'properties': {'ids_fatura': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['ids_fatura'],
    }

    ids_fatura = set()
    falhas = 0

    for i in range(0, len(mensagens), GEMINI_BATCH_SIZE):
        lote = mensagens[i:i + GEMINI_BATCH_SIZE]
        itens = [
            {'id': m['id'], 'assunto': m.get('subject') or '',
             'remetente': (m.get('from') or {}).get('emailAddress', {}).get('address') or ''}
            for m in lote
        ]
        prompt = (
            'Você recebe uma lista de emails (id, assunto, remetente) de uma caixa de entrada pessoal '
            'no Brasil. Identifique quais parecem ser fatura, boleto, conta a pagar, cobrança ou aviso '
            'de vencimento — cartão de crédito, seguro/convênio de saúde, contas de consumo (água, luz, '
            'internet), mensalidades, financiamentos, etc. Ignore promoções, newsletters, notificações de '
            'redes sociais, recibos de compras já pagas e emails que não sejam cobrança pendente. '
            'Responda em JSON com os ids que são cobranças.\n\n'
            + json.dumps(itens, ensure_ascii=False)
        )
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type='application/json', response_schema=schema),
            )
            ids_fatura |= set(json.loads(resp.text).get('ids_fatura', []))
        except Exception as e:
            falhas += 1
            print(f'[email_busca] falha ao classificar lote {i}-{i + len(lote)} de {len(mensagens)}: {e}')

    return ids_fatura, falhas


def _gemini_extrair(client, texto: str, despesas_nomes: list) -> dict:
    from google.genai import types

    prompt = (
        'Analise o texto abaixo, extraído de um email/fatura em português. Extraia:\n'
        '- valor: o valor total a pagar, em reais, como número (ex: 1234.56). null se não achar.\n'
        '- linha_digitavel: o código de pagamento — pode ser a linha digitável de um BOLETO (uma '
        'sequência de 47 ou 48 dígitos, às vezes com pontos/espaços) OU um código PIX "copia e cola" '
        '(uma string alfanumérica longa que começa com "00020..." e termina perto de "6304" seguido de '
        '4 caracteres). Copie EXATAMENTE como aparece no texto abaixo — nunca invente, estime, complete '
        'ou use um exemplo/placeholder; se nenhuma dessas duas sequências aparece literalmente escrita '
        'no texto, retorne null. Não é aceitável "chutar" um valor plausível.\n'
        '- tipo_codigo: "boleto" se linha_digitavel for uma linha digitável de boleto, "pix" se for um '
        'código Pix copia e cola. null se linha_digitavel for null.\n'
        f'- despesa_sugerida: a mais provável entre exatamente estes nomes: {json.dumps(despesas_nomes, ensure_ascii=False)}. '
        'null se nenhuma corresponder bem ao remetente/assunto/conteúdo.\n\n'
        f'TEXTO:\n{texto[:8000]}'
    )
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type='application/json',
                response_schema={
                    'type': 'object',
                    'properties': {
                        'valor': {'type': 'number'},
                        'linha_digitavel': {'type': 'string'},
                        'tipo_codigo': {'type': 'string', 'enum': ['boleto', 'pix']},
                        'despesa_sugerida': {'type': 'string'},
                    },
                },
            ),
        )
        return json.loads(resp.text)
    except Exception as e:
        print(f'[email_busca] falha ao extrair dados do email: {e}')
        return {}


def _buscar_dia(token, dia, despesas, despesas_por_nome, despesas_por_id, regras, client):
    """Busca e classifica os emails de um único dia. Retorna
    (boletos_do_dia, total_emails_do_dia, lotes_de_classificacao_com_falha)."""
    mensagens = _mensagens_no_periodo(token, dia, dia)

    lotes_com_falha = 0
    if client:
        ids_fatura, lotes_com_falha = _gemini_classificar(client, mensagens)
    else:
        ids_fatura = None

    boletos = []
    for m in mensagens:
        assunto = m.get('subject') or ''
        remetente = (m.get('from') or {}).get('emailAddress', {}).get('address') or ''
        # remetente já associado antes por você — entra na lista e vem
        # pré-preenchido, sem depender de bater de novo por assunto/IA
        despesa_conhecida = despesas_por_id.get(regras.get(remetente))

        origem_sugestao = None
        if client:
            if m['id'] not in ids_fatura and not despesa_conhecida:
                continue
            texto = _texto_completo(token, m)
            info = _gemini_extrair(client, texto, list(despesas_por_nome.keys()))
            despesa_ia = despesas_por_nome.get(info.get('despesa_sugerida') or '')
            despesa = despesa_conhecida or despesa_ia
            origem_sugestao = 'regra' if despesa_conhecida else ('ia' if despesa_ia else None)
            valor_num = info.get('valor')
            valor = f'{valor_num:.2f}'.replace('.', ',') if isinstance(valor_num, (int, float)) else None
            linha_digitavel = info.get('linha_digitavel')
            tipo_codigo = info.get('tipo_codigo') if info.get('tipo_codigo') in ('boleto', 'pix') else None
            # o Gemini pode "alucinar" um código plausível em vez de dizer
            # que não achou — só aceita se tiver o formato certo (boleto:
            # 47/48 dígitos; pix: estrutura 0002...6304XXXX) E se aparecer
            # de fato, literalmente, no texto extraído do email
            valido = (
                (tipo_codigo == 'boleto' and _linha_digitavel_valida(linha_digitavel)) or
                (tipo_codigo == 'pix' and _codigo_pix_valido(linha_digitavel))
            )
            if valido:
                if tipo_codigo == 'boleto':
                    achado = re.sub(r'\D', '', linha_digitavel) in re.sub(r'\D', '', texto)
                else:
                    achado = re.sub(r'\s+', '', linha_digitavel) in re.sub(r'\s+', '', texto)
                if not achado:
                    valido = False
            if not valido:
                linha_digitavel, tipo_codigo = None, None
        else:
            sugerida = despesa_conhecida or _despesa_sugerida(assunto, remetente, despesas)
            origem_sugestao = 'regra' if despesa_conhecida else ('palavra_chave' if sugerida else None)
            if not sugerida and not _parece_fatura(assunto):
                continue
            texto = _texto_completo(token, m)
            linha_digitavel, tipo_codigo, valor = _extrair_codigo_regex(texto)
            if not sugerida and not linha_digitavel and not valor:
                continue
            despesa = sugerida

        boletos.append({
            'id': m['id'],
            'assunto': assunto,
            'remetente': remetente,
            'data': m.get('receivedDateTime'),
            'valor_encontrado': valor,
            'linha_digitavel': linha_digitavel,
            'tipo_codigo': tipo_codigo,
            'despesa_sugerida_id': despesa['id'] if despesa else None,
            'despesa_sugerida_nome': despesa['nome'] if despesa else None,
            'origem_sugestao': origem_sugestao,
        })

    return boletos, len(mensagens), lotes_com_falha


def _rodar_busca(token, dias):
    """Roda em thread de fundo: processa um dia por vez, publicando progresso
    em _busca_job a cada dia concluído para o frontend fazer polling. Verifica
    o pedido de cancelamento entre dias — se cancelada, para ali e mantém tudo
    que já foi encontrado (nada é descartado)."""
    conn = get_db()
    despesas = conn.execute("SELECT * FROM despesa WHERE ativo=1 ORDER BY nome").fetchall()
    regras = {r['remetente']: r['despesa_id'] for r in conn.execute('SELECT remetente, despesa_id FROM email_despesa_regra').fetchall()}
    conn.close()
    despesas_por_nome = {d['nome']: d for d in despesas}
    despesas_por_id = {d['id']: d for d in despesas}
    client = _gemini_client()

    with _lock:
        _busca_job['despesas_pesquisadas'] = len(despesas)

    for dia in dias:
        with _lock:
            if _busca_job['cancelar']:
                break
            _busca_job['dia_atual'] = dia

        try:
            boletos_dia, total_dia, falha_dia = _buscar_dia(
                token, dia, despesas, despesas_por_nome, despesas_por_id, regras, client
            )
        except requests.HTTPError as e:
            with _lock:
                _busca_job['erro'] = f'Falha ao consultar o Microsoft Graph em {dia}: {e}'
            break
        except Exception as e:
            with _lock:
                _busca_job['erro'] = f'Falha inesperada ao buscar {dia}: {e}'
            break

        with _lock:
            _busca_job['boletos'].extend(boletos_dia)
            _busca_job['boletos'].sort(key=lambda b: b['data'] or '', reverse=True)
            _busca_job['total_emails'] += total_dia
            _busca_job['dias_processados'] += 1
            if falha_dia:
                _busca_job['avisos'].append(
                    f'Falha ao classificar por IA parte dos emails de {dia} — resultado desse dia pode estar incompleto.'
                )

    with _lock:
        _busca_job['cancelado'] = _busca_job['cancelar']
        _busca_job['rodando'] = False
        _busca_job['concluido'] = True
        _busca_job['dia_atual'] = None


@bp.route('/email/buscar/iniciar', methods=['POST'])
def buscar_iniciar():
    token = _token_valido()
    if not token:
        return jsonify({'ok': False, 'msg': 'Email não conectado — clique em "Conectar com Microsoft" antes de buscar.'}), 400

    data = request.json or {}
    ini, fim = data.get('data_ini'), data.get('data_fim')
    if not ini or not fim:
        return jsonify({'ok': False, 'msg': 'data_ini e data_fim são obrigatórios'}), 400

    try:
        dias = _dias_no_periodo(ini, fim)
    except ValueError:
        return jsonify({'ok': False, 'msg': 'Datas inválidas'}), 400
    if not dias:
        return jsonify({'ok': False, 'msg': 'Período inválido — data final antes da inicial.'}), 400

    with _lock:
        if _busca_job['rodando']:
            return jsonify({'ok': False, 'msg': 'Já existe uma busca em andamento — aguarde ou cancele antes de iniciar outra.'}), 409
        _busca_job.update({
            'rodando': True, 'cancelar': False, 'cancelado': False, 'concluido': False,
            'erro': None, 'avisos': [], 'boletos': [], 'total_emails': 0,
            'despesas_pesquisadas': 0, 'dia_atual': dias[0], 'total_dias': len(dias),
            'dias_processados': 0, 'periodo': {'ini': ini, 'fim': fim},
        })

    threading.Thread(target=_rodar_busca, args=(token, dias), daemon=True).start()
    return jsonify({'ok': True, 'total_dias': len(dias)})


@bp.route('/email/buscar/status')
def buscar_status():
    with _lock:
        return jsonify({'ok': True, **_busca_job})


@bp.route('/email/buscar/cancelar', methods=['POST'])
def buscar_cancelar():
    with _lock:
        if not _busca_job['rodando']:
            return jsonify({'ok': False, 'msg': 'Nenhuma busca em andamento.'}), 400
        _busca_job['cancelar'] = True
    return jsonify({'ok': True})


def _aplicar_associacao(conn, item: dict):
    """Vincula um boleto/pix encontrado a uma despesa/mês — não mexe em
    status de pagamento, só guarda o código (e o valor, se ainda não tinha
    um esperado) pra aparecer em Mês Atual. Também grava o remetente como
    regra: da próxima vez, esse email já vem pré-associado à despesa."""
    despesa_id = item.get('despesa_id')
    mes_ref = item.get('mes_ref', '')
    linha_digitavel = item.get('linha_digitavel')
    tipo_codigo = item.get('tipo_codigo')
    remetente = item.get('remetente')
    valor = item.get('valor')
    if isinstance(valor, str):
        valor = parse_number(valor)

    if not (despesa_id and mes_ref):
        raise ValueError('despesa_id e mes_ref são obrigatórios')

    conn.execute(
        'INSERT OR IGNORE INTO lancamento (mes_ref, despesa_id, valor_esperado, status) VALUES (?,?,?,\'nao_encontrado\')',
        (mes_ref, despesa_id, valor or 0)
    )
    conn.execute(
        'UPDATE lancamento SET linha_digitavel=?, tipo_codigo=? WHERE despesa_id=? AND mes_ref=?',
        (linha_digitavel, tipo_codigo, despesa_id, mes_ref)
    )

    if remetente:
        despesa = conn.execute('SELECT nome FROM despesa WHERE id=?', (despesa_id,)).fetchone()
        regra_anterior = conn.execute('SELECT despesa_id FROM email_despesa_regra WHERE remetente=?', (remetente,)).fetchone()
        nova = not regra_anterior or regra_anterior['despesa_id'] != despesa_id
        conn.execute(
            'INSERT INTO email_despesa_regra (remetente, despesa_id) VALUES (?,?) '
            'ON CONFLICT(remetente) DO UPDATE SET despesa_id=excluded.despesa_id',
            (remetente, despesa_id)
        )
        if nova and despesa:
            _registrar_regra_email(remetente, despesa['nome'])


@bp.route('/email/associar/lote', methods=['POST'])
def associar_lote():
    """Confirma de uma vez todas as associações represadas na tela de
    busca — nada é gravado antes disso (ver EmailBusca.tsx: 'Confirmar
    tudo'/'Cancelar tudo'), o mesmo padrão de preview-then-confirm já usado
    no batimento."""
    itens = (request.json or {}).get('itens', [])
    if not itens:
        return jsonify({'ok': False, 'msg': 'Nenhuma associação para confirmar'}), 400

    conn = get_db()
    try:
        for item in itens:
            _aplicar_associacao(conn, item)
    except ValueError as e:
        conn.close()
        return jsonify({'ok': False, 'msg': str(e)}), 400

    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'aplicadas': len(itens)})


def _registrar_regra_email(remetente: str, despesa_nome: str):
    """Espelha a regra remetente->despesa em docs/, para consulta humana —
    a fonte de verdade que o app usa é a tabela email_despesa_regra."""
    if not os.path.exists(DICIONARIO_EMAILS_PATH):
        os.makedirs(os.path.dirname(DICIONARIO_EMAILS_PATH), exist_ok=True)
        with open(DICIONARIO_EMAILS_PATH, 'w', encoding='utf-8') as f:
            f.write(
                '# Dicionário de Remetentes de Email\n\n'
                'Toda vez que você associa um boleto encontrado por email a uma despesa, o app\n'
                'grava o remetente na tabela `email_despesa_regra` — da próxima busca, esse\n'
                'remetente já vem pré-associado à mesma despesa, só pra você conferir o valor/linha\n'
                'digitável do mês. Este arquivo é só o espelho legível dessa tabela; a fonte de\n'
                'verdade que o app consulta é o banco.\n\n'
                '| Remetente | Despesa |\n'
                '|---|---|\n'
            )

    with open(DICIONARIO_EMAILS_PATH, encoding='utf-8') as f:
        linhas = f.readlines()

    if f'`{remetente}`' in ''.join(linhas):
        # já existia — atualiza a linha em vez de duplicar
        linhas = [l for l in linhas if f'`{remetente}`' not in l]

    nova_linha = f'| `{remetente}` | {despesa_nome} |\n'
    idx_insercao = len(linhas)
    for i, linha in enumerate(linhas):
        if linha.startswith('|'):
            idx_insercao = i + 1
    linhas.insert(idx_insercao, nova_linha)

    with open(DICIONARIO_EMAILS_PATH, 'w', encoding='utf-8') as f:
        f.writelines(linhas)
