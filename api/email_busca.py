import base64
import io
import json
import os
import re
import threading

import msal
import requests
from flask import Blueprint, jsonify, request

from .db import get_db, get_config_value, periodo_competencia
from .importacao import normalize_text

bp = Blueprint('email_busca', __name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_CACHE_PATH = os.path.join(BASE_DIR, '.msal_token_cache.json')

# A Microsoft desativou autenticação por senha (inclusive senha de app) para
# IMAP em contas Outlook/Hotmail pessoais em abril/2026 — o caminho que
# restou é OAuth2 via Microsoft Graph. Requer um app registrado em
# portal.azure.com (client ID público, sem segredo) — ver .env.example.
CLIENT_ID = os.environ.get('EMAIL_CLIENT_ID')
AUTHORITY = 'https://login.microsoftonline.com/common'
SCOPES = ['Mail.Read']
GRAPH = 'https://graph.microsoft.com/v1.0'

# Linha digitável de boleto: 5 blocos de dígitos (com ou sem pontuação)
RE_LINHA_DIGITAVEL = re.compile(
    r'\d{5}[.\s]?\d{5}\s+\d{5}[.\s]?\d{6}\s+\d{5}[.\s]?\d{6}\s+\d\s+\d{14,17}'
)
RE_VALOR = re.compile(r'R\$\s*([\d.]{1,12},\d{2})')

_lock = threading.Lock()
_pending = {}  # device flow em andamento — app desktop de 1 usuário, sem sessão


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
    """Busca todas as mensagens do INBOX no período uma única vez (evita
    repetir a mesma consulta para cada despesa)."""
    headers = {'Authorization': f'Bearer {token}'}
    url = f'{GRAPH}/me/mailFolders/inbox/messages'
    params = {
        '$filter': f'receivedDateTime ge {ini}T00:00:00Z and receivedDateTime le {fim}T23:59:59Z',
        '$select': 'id,subject,from,receivedDateTime,body,hasAttachments',
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


def _buscar_despesa(token: str, despesa, mensagens: list):
    regras = json.loads(despesa['regras_match'])
    keywords = [kw for kw in regras.get('palavras_chave', []) if kw]
    if not keywords:
        return []

    headers = {'Authorization': f'Bearer {token}'}
    resultados = []

    for m in mensagens:
        assunto = m.get('subject') or ''
        remetente = (m.get('from') or {}).get('emailAddress', {}).get('address') or ''
        alvo = normalize_text(f'{assunto} {remetente}')
        if not any(normalize_text(kw) in alvo for kw in keywords):
            continue

        texto = _extrair_texto_html((m.get('body') or {}).get('content', ''))

        if m.get('hasAttachments'):
            try:
                att_resp = requests.get(f'{GRAPH}/me/messages/{m["id"]}/attachments', headers=headers, timeout=30)
                att_resp.raise_for_status()
                for att in att_resp.json().get('value', []):
                    if att.get('contentType') == 'application/pdf' and att.get('contentBytes'):
                        texto += '\n' + _extrair_pdf(att['contentBytes'])
            except Exception:
                pass

        linha = RE_LINHA_DIGITAVEL.search(texto)
        valores = RE_VALOR.findall(texto)

        resultados.append({
            'assunto': assunto,
            'remetente': remetente,
            'data': m.get('receivedDateTime'),
            'linha_digitavel': re.sub(r'\s+', ' ', linha.group()).strip() if linha else None,
            'valor_encontrado': valores[0] if valores else None,
        })

    return resultados


@bp.route('/email/buscar', methods=['POST'])
def buscar():
    token = _token_valido()
    if not token:
        return jsonify({'ok': False, 'msg': 'Email não conectado — clique em "Conectar com Microsoft" antes de buscar.'}), 400

    mes_ref = request.json.get('mes_ref', '')
    conn = get_db()
    dia_corte = int(get_config_value(conn, 'dia_recebimento_salario', '27'))
    ini, fim = periodo_competencia(mes_ref, dia_corte)

    despesas = conn.execute(
        "SELECT * FROM despesa WHERE ativo=1 AND tipo_valor='variavel' ORDER BY nome"
    ).fetchall()
    conn.close()

    try:
        mensagens = _mensagens_no_periodo(token, ini, fim)
    except requests.HTTPError as e:
        return jsonify({'ok': False, 'msg': f'Falha ao consultar o Microsoft Graph: {e}'}), 502

    achados = []
    for d in despesas:
        emails = _buscar_despesa(token, d, mensagens)
        if emails:
            achados.append({'despesa_id': d['id'], 'despesa_nome': d['nome'], 'emails': emails})

    return jsonify({'ok': True, 'periodo': {'ini': ini, 'fim': fim}, 'despesas_pesquisadas': len(despesas), 'resultados': achados})
