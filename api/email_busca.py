import email
import imaplib
import io
import json
import os
import re
from datetime import datetime, timedelta
from email.header import decode_header

from flask import Blueprint, jsonify, request

from .db import get_db, get_config_value, periodo_competencia
from .importacao import normalize_text

bp = Blueprint('email_busca', __name__)

IMAP_HOST = os.environ.get('EMAIL_IMAP_HOST', 'outlook.office365.com')
EMAIL_ADDRESS = os.environ.get('EMAIL_ADDRESS')
EMAIL_APP_PASSWORD = os.environ.get('EMAIL_APP_PASSWORD')

# Linha digitável de boleto: 5 blocos de dígitos (com ou sem pontuação)
RE_LINHA_DIGITAVEL = re.compile(
    r'\d{5}[.\s]?\d{5}\s+\d{5}[.\s]?\d{6}\s+\d{5}[.\s]?\d{6}\s+\d\s+\d{14,17}'
)
RE_VALOR = re.compile(r'R\$\s*([\d.]{1,12},\d{2})')


def _configurado() -> bool:
    return bool(EMAIL_ADDRESS and EMAIL_APP_PASSWORD)


def _decode(raw) -> str:
    if raw is None:
        return ''
    parts = decode_header(raw)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or 'utf-8', errors='replace'))
        else:
            out.append(text)
    return ''.join(out)


def _extrair_texto(msg) -> str:
    """Concatena o texto de todas as partes de um email — corpo (texto/HTML)
    e anexos em PDF — para rodar as regexes de valor/linha digitável."""
    textos = []
    for part in msg.walk():
        content_type = part.get_content_type()
        disposition = str(part.get('Content-Disposition') or '')

        if content_type == 'text/plain':
            try:
                textos.append(part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='replace'))
            except Exception:
                pass
        elif content_type == 'text/html':
            try:
                html = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='replace')
                textos.append(re.sub(r'<[^>]+>', ' ', html))
            except Exception:
                pass
        elif content_type == 'application/pdf' or (content_type == 'application/octet-stream' and 'pdf' in disposition.lower()):
            try:
                import pdfplumber
                payload = part.get_payload(decode=True)
                with pdfplumber.open(io.BytesIO(payload)) as pdf:
                    for page in pdf.pages:
                        textos.append(page.extract_text() or '')
            except Exception:
                pass

    return '\n'.join(textos)


MESES_IMAP = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def _data_imap(iso: str) -> str:
    """IMAP exige abreviação de mês em inglês (SINCE/BEFORE) — não dá para
    confiar em strftime('%b'), que varia com o locale do sistema."""
    d = datetime.strptime(iso, '%Y-%m-%d')
    return f'{d.day:02d}-{MESES_IMAP[d.month - 1]}-{d.year}'


def _buscar_despesa(imap, despesa, ini: str, fim: str):
    """Busca no INBOX emails dentro do período cujo assunto ou remetente
    bata com alguma palavra-chave da despesa, e extrai valor/linha digitável."""
    regras = json.loads(despesa['regras_match'])
    keywords = [kw for kw in regras.get('palavras_chave', []) if kw]
    if not keywords:
        return []

    # IMAP BEFORE exclui a própria data-limite — soma 1 dia para incluir "fim"
    desde = _data_imap(ini)
    ate = _data_imap((datetime.strptime(fim, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d'))

    status, data = imap.search(None, f'(SINCE {desde} BEFORE {ate})')
    if status != 'OK':
        return []

    resultados = []
    for uid in data[0].split():
        status, msg_data = imap.fetch(uid, '(RFC822)')
        if status != 'OK' or not msg_data or not msg_data[0]:
            continue
        msg = email.message_from_bytes(msg_data[0][1])

        assunto = _decode(msg.get('Subject'))
        remetente = _decode(msg.get('From'))
        alvo = normalize_text(f'{assunto} {remetente}')

        if not any(normalize_text(kw) in alvo for kw in keywords):
            continue

        texto = _extrair_texto(msg)
        linha = RE_LINHA_DIGITAVEL.search(texto)
        valores = RE_VALOR.findall(texto)

        resultados.append({
            'assunto': assunto,
            'remetente': remetente,
            'data': msg.get('Date'),
            'linha_digitavel': re.sub(r'\s+', ' ', linha.group()).strip() if linha else None,
            'valor_encontrado': valores[0] if valores else None,
        })

    return resultados


@bp.route('/email/status')
def status():
    return jsonify({'configurado': _configurado(), 'endereco': EMAIL_ADDRESS if _configurado() else None})


@bp.route('/email/buscar', methods=['POST'])
def buscar():
    if not _configurado():
        return jsonify({
            'ok': False,
            'msg': 'Credenciais de email não configuradas. Defina EMAIL_ADDRESS e EMAIL_APP_PASSWORD no arquivo .env (veja .env.example).'
        }), 400

    mes_ref = request.json.get('mes_ref', '')
    conn = get_db()
    dia_corte = int(get_config_value(conn, 'dia_recebimento_salario', '27'))
    ini, fim = periodo_competencia(mes_ref, dia_corte)

    despesas = conn.execute(
        "SELECT * FROM despesa WHERE ativo=1 AND tipo_valor='variavel' ORDER BY nome"
    ).fetchall()
    conn.close()

    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        imap.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
        imap.select('INBOX')
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'Falha ao conectar/autenticar no email: {e}'}), 502

    achados = []
    try:
        for d in despesas:
            emails = _buscar_despesa(imap, d, ini, fim)
            if emails:
                achados.append({'despesa_id': d['id'], 'despesa_nome': d['nome'], 'emails': emails})
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    return jsonify({'ok': True, 'periodo': {'ini': ini, 'fim': fim}, 'despesas_pesquisadas': len(despesas), 'resultados': achados})
