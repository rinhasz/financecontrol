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

from .db import get_db, parse_number, get_config_value, competencia_da_data
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

# --------------------------------------------- o que o código já diz de si ---
#
# A linha digitável de 47 dígitos **carrega o vencimento e o valor**. Isso não é
# detalhe: num carnê de financiamento (Safra) o mesmo PDF traz dezenas de
# parcelas, e perguntar a uma IA "qual o valor desta parcela" num texto com 60
# valores é justamente o que não funciona. Lendo do próprio código, cada parcela
# nasce com valor e mês certos, sem IA e sem ambiguidade.
#
# Campo 5 da linha digitável (posições 34-47): fator de vencimento (4) + valor
# em centavos (10).
FATOR_BASE_ANTIGA = datetime.date(1997, 10, 7)
# Quando o fator de vencimento chegou a 9999 (21/02/2025), a FEBRABAN reiniciou
# a contagem em 1000 — que vale 22/02/2025. Boleto emitido hoje usa esta base; a
# antiga ainda aparece em boleto velho. Qual das duas vale sai por proximidade
# da data do email (ver _vencimento_do_fator).
FATOR_BASE_NOVA = datetime.date(2025, 2, 22)


def _so_digitos(s) -> str:
    return re.sub(r'\D', '', s or '')


def _dv_mod10(bloco: str) -> int:
    soma, peso = 0, 2
    for ch in reversed(bloco):
        p = int(ch) * peso
        soma += p if p < 10 else p - 9
        peso = 1 if peso == 2 else 2
    return (10 - soma % 10) % 10


def _dv_mod11_barra(barra43: str) -> int:
    """DV geral do código de barras (módulo 11, pesos 2..9). Resto 0, 1 ou
    10 vira DV 1, pela especificação FEBRABAN."""
    pesos = [2, 3, 4, 5, 6, 7, 8, 9]
    soma = sum(int(ch) * pesos[i % 8] for i, ch in enumerate(reversed(barra43)))
    dv = 11 - soma % 11
    return 1 if dv in (0, 1, 10, 11) else dv


def _boleto_dv_ok(d47: str) -> bool:
    """Os quatro dígitos verificadores de uma linha digitável conferem?

    Vale muito mais que a checagem de formato: varrendo um PDF inteiro em busca
    de **todos** os códigos, qualquer sequência de dígitos no layout certo
    entraria como boleto. E um código com um dígito trocado (extração de PDF
    quebra linha em lugar estranho) é pior que nenhum — pagamento vai para o
    lugar errado. Código reprovado aqui não é descartado: vai para a tela com
    aviso, porque sumir em silêncio contraria o princípio 4.
    """
    if len(d47) != 47:
        return False
    if int(d47[9]) != _dv_mod10(d47[0:9]):
        return False
    if int(d47[20]) != _dv_mod10(d47[10:20]):
        return False
    if int(d47[31]) != _dv_mod10(d47[21:31]):
        return False
    # barra 44 = banco(3) moeda(1) dv(1) fator(4) valor(10) campo livre(25)
    sem_dv = d47[0:4] + d47[33:47] + d47[4:9] + d47[10:20] + d47[21:31]
    return int(d47[32]) == _dv_mod11_barra(sem_dv)


def _vencimento_do_fator(fator: int, referencia: datetime.date):
    """Data de vencimento a partir do fator, escolhendo a base por proximidade
    da data do email. Fator 1603 é 26/02/2002 na base antiga e 18/10/2026 na
    nova; para um email de 2026 só a segunda faz sentido. Fator < 1000 é
    "sem vencimento" por especificação."""
    if fator < 1000:
        return None
    candidatas = [FATOR_BASE_ANTIGA + datetime.timedelta(days=fator),
                  FATOR_BASE_NOVA + datetime.timedelta(days=fator - 1000)]
    return min(candidatas, key=lambda d: abs((d - referencia).days))


def _dados_do_boleto(d47: str, referencia: datetime.date) -> dict:
    """Vencimento e valor lidos do próprio código."""
    venc = _vencimento_do_fator(int(d47[33:37]), referencia)
    centavos = int(d47[37:47])
    return {
        'vencimento': venc.isoformat() if venc else None,
        # valor zerado existe (boleto em branco, a preencher) — null, não 0,00
        'valor_num': (centavos / 100) if centavos else None,
        'dv_ok': _boleto_dv_ok(d47),
    }


def _fmt_valor(v) -> str:
    """Número -> '1.234,56' (o formato que a tela e parse_number já usam)."""
    return f'{v:,.2f}'.replace(',', '@').replace('.', ',').replace('@', '.')


def _formatar_linha_digitavel(d47: str) -> str:
    return (f'{d47[0:5]}.{d47[5:10]} {d47[10:15]}.{d47[15:21]} '
            f'{d47[21:26]}.{d47[26:32]} {d47[32]} {d47[33:47]}')


def _todos_os_codigos(texto: str, referencia: datetime.date) -> list:
    """**Todos** os códigos de pagamento do texto, não só o primeiro.

    Era aqui que o carnê do Safra se perdia: `RE_LINHA_DIGITAVEL.search()`
    devolvia uma parcela e as outras iam para o lixo sem nenhum aviso na tela.
    Boletos primeiro (ordenados por vencimento), Pix depois — a ordem em que
    aparecem no PDF não tem relação com a ordem das parcelas.
    """
    boletos, pix, vistos = [], [], set()

    for m in RE_LINHA_DIGITAVEL.finditer(texto):
        d = _so_digitos(m.group())
        # o último bloco da regex aceita 14-17 dígitos para tolerar
        # pontuação/colagem; a linha digitável em si tem 47
        if len(d) > 47:
            d = d[:47]
        if len(d) != 47 or d in vistos:
            continue
        vistos.add(d)
        info = _dados_do_boleto(d, referencia)
        boletos.append({
            'chave': d,
            'codigo': _formatar_linha_digitavel(d),
            'tipo_codigo': 'boleto',
            'valor': _fmt_valor(info['valor_num']) if info['valor_num'] else None,
            'valor_num': info['valor_num'],
            'vencimento': info['vencimento'],
            'dv_ok': info['dv_ok'],
        })

    for m in RE_PIX.finditer(texto):
        codigo = re.sub(r'\s+', '', m.group())
        if not _codigo_pix_valido(codigo) or codigo in vistos:
            continue
        vistos.add(codigo)
        pix.append({
            'chave': codigo, 'codigo': codigo, 'tipo_codigo': 'pix',
            'valor': None, 'valor_num': None, 'vencimento': None, 'dv_ok': True,
        })

    boletos.sort(key=lambda b: (b['vencimento'] or '9999-12-31', b['chave']))
    return boletos + pix


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
    'erro': None, 'avisos': [], 'emails': [], 'total_codigos': 0,
    'total_emails': 0, 'despesas_pesquisadas': 0, 'dia_atual': None,
    'total_dias': 0, 'dias_processados': 0, 'periodo': None,
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


_msal_cache: 'msal.SerializableTokenCache | None' = None
_msal_client: 'msal.PublicClientApplication | None' = None


def _msal_app() -> msal.PublicClientApplication:
    """Reaproveita a mesma instância entre chamadas. Construir um
    PublicClientApplication faz o MSAL validar/descobrir a autoridade da
    Microsoft pela rede — isso já levou 20s+ (ou nem terminava) quando a
    rede estava ruim, e como /email/status é chamado toda vez que a tela
    de busca de email abre, recriar o client a cada request deixava até a
    checagem mais simples travada."""
    global _msal_cache, _msal_client
    with _lock:
        if _msal_client is None:
            _msal_cache = msal.SerializableTokenCache()
            if os.path.exists(TOKEN_CACHE_PATH):
                with open(TOKEN_CACHE_PATH, 'r', encoding='utf-8') as f:
                    _msal_cache.deserialize(f.read())
            _msal_client = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=_msal_cache)
        return _msal_client


def _save_msal_cache():
    if _msal_cache is not None and _msal_cache.has_state_changed:
        with open(TOKEN_CACHE_PATH, 'w', encoding='utf-8') as f:
            f.write(_msal_cache.serialize())


def _configurado() -> bool:
    return bool(CLIENT_ID)


def _conta_conectada():
    """Lê a conta direto do arquivo de cache, sem instanciar o MSAL — que
    faria descoberta de autoridade pela rede. Esta função responde ao
    /email/status, chamado toda vez que a tela de busca abre; se ela
    depender de rede, uma rede lenta deixa a tela inteira sem renderizar.
    A tela só precisa saber *se* há conta conectada, o que está no cache."""
    if not CLIENT_ID or not os.path.exists(TOKEN_CACHE_PATH):
        return None
    try:
        with open(TOKEN_CACHE_PATH, encoding='utf-8') as f:
            contas = json.load(f).get('Account', {})
        for conta in contas.values():
            if conta.get('username'):
                return conta['username']
    except (OSError, ValueError) as e:
        print(f'[email_busca] não consegui ler o cache de token: {e}')
    return None


def _token_valido():
    """Tenta obter um token de acesso do cache local, sem interação do usuário."""
    if not CLIENT_ID:
        return None
    app_ = _msal_app()
    accounts = app_.get_accounts()
    if not accounts:
        return None
    result = app_.acquire_token_silent(SCOPES, account=accounts[0])
    _save_msal_cache()
    return result.get('access_token') if result else None


@bp.route('/email/status')
def status():
    return jsonify({'configurado': _configurado(), 'conectado': _conta_conectada()})


@bp.route('/email/conectar/iniciar', methods=['POST'])
def conectar_iniciar():
    if not CLIENT_ID:
        return jsonify({'ok': False, 'msg': 'EMAIL_CLIENT_ID não configurado no .env — veja .env.example.'}), 400

    app_ = _msal_app()
    flow = app_.initiate_device_flow(scopes=SCOPES)
    if 'user_code' not in flow:
        return jsonify({'ok': False, 'msg': f'Falha ao iniciar login: {flow.get("error_description", flow)}'}), 502

    with _lock:
        _pending['flow'] = flow

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

    if not flow:
        return jsonify({'ok': False, 'msg': 'Nenhum login em andamento — clique em "Conectar" de novo.'}), 400

    app_ = _msal_app()
    result = app_.acquire_token_by_device_flow(flow)
    _save_msal_cache()

    with _lock:
        _pending.pop('flow', None)

    if 'access_token' not in result:
        return jsonify({'ok': False, 'msg': result.get('error_description', 'Login não concluído ou expirado.')}), 400

    accounts = app_.get_accounts()
    return jsonify({'ok': True, 'conta': accounts[0]['username'] if accounts else None})


def _extrair_texto_html(html: str) -> str:
    return re.sub(r'<[^>]+>', ' ', html or '')


def _texto_de_pdf(payload: bytes, senha: str = None) -> str:
    """Texto de um PDF, tentando abrir com senha quando ele vem cifrado.

    Tenta primeiro sem senha: um PDF aberto não aceita `password` errado de
    graça em toda versão do pdfminer, e a maioria das faturas não é cifrada.
    """
    import pdfplumber
    for tentativa in ([None, senha] if senha else [None]):
        try:
            with pdfplumber.open(io.BytesIO(payload),
                                 **({'password': tentativa} if tentativa else {})) as pdf:
                return '\n'.join(p.extract_text() or '' for p in pdf.pages)
        except Exception:
            continue
    return ''


def _extrair_pdf(conteudo_base64: str, senha: str = None) -> str:
    try:
        return _texto_de_pdf(base64.b64decode(conteudo_base64), senha)
    except Exception:
        return ''


# ------------------------------------------------- fatura atrás de um link ---
#
# Alguns emissores não anexam a fatura: mandam um link ("Clique aqui para
# baixar a fatura") que devolve um PDF **cifrado**, cuja senha o cliente já
# conhece. Sem seguir o link, o email não tem código nenhum para extrair.
#
# Só se segue link de remetente que esteja neste mapa. Buscar URL de email
# arbitrário transformaria o app num clicador automático de qualquer coisa que
# chegue na caixa de entrada — que é exatamente o vetor de phishing. A chave
# casa por substring no endereço do remetente.
#
# Editável sem mexer no código: `config.senhas_fatura`, um JSON
# `{"remetente": "senha"}`, sobrepõe este padrão.
# A chave é `sousulamerica`, não `sulamerica`: o boleto vem de
# `grp-sousulamerica@sulamerica.com.br`, e casar o domínio inteiro liberaria
# também `mkt@marketing.sulamerica.com.br` — que é propaganda, e cujos links o
# app passaria a abrir sozinho sem nenhum motivo.
SENHAS_FATURA_PADRAO = {
    'sousulamerica': '5551',        # grp-sousulamerica@sulamerica.com.br
    'faturaporto': '289702730',     # cartaoportoseguro@faturaporto.com.br
}

RE_ANCORA = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
RE_ALT = re.compile(r'\balt=["\']([^"\']*)["\']', re.I)

# texto da âncora que indica "a fatura está do outro lado deste link"
TEXTO_DE_FATURA = ('baixar a fatura', 'baixar fatura', 'segunda via', 'ver fatura',
                   'visualizar boleto', 'baixar boleto', 'ver boleto', 'segunda-via',
                   '2a via', 'clique aqui')

MAX_PDF_BYTES = 12 * 1024 * 1024
# teto de links tentados por email quando nenhum rótulo casa (ver _links_de_fatura)
MAX_LINKS_CEGOS = 18


def _desembrulhar_safelink(url: str) -> str:
    """`safelinks.protection.outlook.com/?url=<encoded>` -> URL real.

    O Outlook reescreve todo link recebido. Sem desembrulhar, o log fica
    ilegível e a deduplicação não funciona — dois wrappers diferentes podem
    apontar para o mesmo destino.
    """
    if 'safelinks.protection.outlook.com' not in url:
        return url
    from urllib.parse import urlparse, parse_qs
    try:
        # `parse_qs` JÁ desfaz o percent-encoding. Chamar `unquote` no
        # resultado decodifica duas vezes, e isso quebra o link: o token do
        # rastreador da SulAmérica contém `%2B`, que vira `+` literal — e o
        # servidor relê `+` como espaço, devolvendo 404. Com o decode simples,
        # o mesmo link responde 200 com o PDF de 208 KB.
        alvo = parse_qs(urlparse(url).query).get('url', [None])[0]
        return alvo or url
    except Exception:
        return url


_SENHAS_CACHE = None


def _senhas_fatura() -> dict:
    """Lida uma vez por busca: `_texto_completo` roda por email, e abrir conexão
    com o banco a cada um seria desperdício. `_rodar_busca` limpa o cache."""
    global _SENHAS_CACHE
    if _SENHAS_CACHE is not None:
        return _SENHAS_CACHE

    conn = get_db()
    try:
        from .db import get_config_value
        bruto = get_config_value(conn, 'senhas_fatura', '')
    finally:
        conn.close()
    senhas = dict(SENHAS_FATURA_PADRAO)
    if bruto:
        try:
            senhas.update(json.loads(bruto))
        except (ValueError, TypeError):
            print('[email_busca] config.senhas_fatura não é JSON válido — usando o padrão')
    _SENHAS_CACHE = senhas
    return senhas


def _senha_do_remetente(remetente: str):
    """Senha da fatura linkada, ou `None` se o remetente não está liberado."""
    alvo = (remetente or '').lower()
    for chave, senha in _senhas_fatura().items():
        if chave.lower() in alvo:
            return senha
    return None


def _rotulo_da_ancora(interno: str) -> str:
    """O que a âncora 'diz' — texto visível **mais o alt das imagens**.

    Na SulAmérica o botão da fatura é uma **imagem**, não texto: olhando só o
    texto, o rótulo vem vazio e nenhum link é reconhecido. Era esse o motivo de
    o boleto nunca ser buscado, mesmo com a guarda corrigida.
    """
    alts = ' '.join(RE_ALT.findall(interno or ''))
    visivel = re.sub(r'<[^>]+>', ' ', interno or '')
    return normalize_text(f'{visivel} {alts}')


def _links_de_fatura(html: str, cegos: bool = False) -> list:
    """URLs candidatas a ter a fatura do outro lado, em ordem de preferência.

    Primeiro as âncoras cujo rótulo anuncia a fatura. Se `cegos` e nenhuma
    casar, devolve **todos** os links do email (deduplicados, limitados): num
    email de remetente já liberado, gastar alguns GETs é melhor que não achar o
    boleto — e é o único caminho quando o botão é uma imagem sem `alt` útil.
    """
    rotuladas, todas = [], []
    for href, interno in RE_ANCORA.findall(html or ''):
        if not href.lower().startswith(('http://', 'https://')):
            continue
        real = _desembrulhar_safelink(href)
        if real not in todas:
            todas.append(real)
        rotulo = _rotulo_da_ancora(interno)
        if any(k in rotulo for k in TEXTO_DE_FATURA) or real.lower().endswith('.pdf'):
            if real not in rotuladas:
                rotuladas.append(real)

    if not cegos:
        return rotuladas
    # Rotuladas primeiro, o resto como rede de segurança: o link certo pode
    # falhar (rastreador que expira, 404) e não é motivo para desistir do email.
    resto = [u for u in todas if u not in rotuladas]
    return (rotuladas + resto)[:MAX_LINKS_CEGOS]


def _texto_da_fatura_linkada(url: str, senha: str, saltos: int = 1) -> str:
    """Baixa o que está atrás do link e devolve o texto do PDF.

    Se o link levar a uma página em vez do arquivo, procura ali dentro um link
    de PDF e segue **uma** vez — o suficiente para o padrão "página intermediária
    com o botão de download", sem virar um crawler.
    """
    try:
        r = requests.get(url, timeout=40, allow_redirects=True,
                         headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        r.raise_for_status()
    except Exception as e:
        print(f'[email_busca] falha ao baixar fatura de {url[:80]}: {e}')
        return ''

    conteudo = r.content[:MAX_PDF_BYTES]
    if conteudo[:5] == b'%PDF-' or 'pdf' in r.headers.get('Content-Type', '').lower():
        texto = _texto_de_pdf(conteudo, senha)
        if not texto:
            print(f'[email_busca] PDF baixado mas não abriu (senha errada?): {url[:80]}')
        return texto

    if saltos > 0:
        try:
            html = conteudo.decode('utf-8', errors='replace')
        except Exception:
            return ''
        for prox in _links_de_fatura(html):
            if prox != url:
                texto = _texto_da_fatura_linkada(prox, senha, saltos - 1)
                if texto:
                    return texto
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


def _e_pdf(att: dict) -> bool:
    """O anexo é um PDF?

    Não basta `contentType == 'application/pdf'`: emissor que manda o carnê
    como `application/octet-stream` (ou com o tipo em branco) tinha o anexo
    **ignorado sem aviso** — o email aparecia na busca sem código nenhum e
    parecia problema de extração. Nome e bytes mágicos resolvem sem confiar no
    cabeçalho declarado.
    """
    tipo = (att.get('contentType') or '').lower()
    nome = (att.get('name') or '').lower()
    if 'pdf' in tipo or nome.endswith('.pdf'):
        return True
    bruto = att.get('contentBytes')
    if bruto:
        try:
            return base64.b64decode(bruto[:12])[:5] == b'%PDF-'
        except Exception:
            return False
    return False


def _texto_completo(token: str, m: dict):
    """Texto do corpo + PDFs anexados de uma mensagem — busca o corpo sob
    demanda (só é chamada pra quem já passou na classificação/filtro).

    Devolve `(texto, notas)`. As notas existem porque "não achei código" tem
    causas muito diferentes (anexo não baixou, PDF não abriu, PDF é imagem) e
    a tela precisa dizer qual foi — princípio 4, nada silencioso.
    """
    headers = {'Authorization': f'Bearer {token}'}
    remetente = (m.get('from') or {}).get('emailAddress', {}).get('address') or ''
    # a senha só existe para remetente liberado; ela é também a permissão para
    # seguir link deste email
    senha = _senha_do_remetente(remetente)
    notas = []

    texto, html = '', ''
    try:
        body_resp = requests.get(f'{GRAPH}/me/messages/{m["id"]}', headers=headers,
                                  params={'$select': 'body'}, timeout=30)
        body_resp.raise_for_status()
        html = body_resp.json().get('body', {}).get('content', '')
        texto = _extrair_texto_html(html)
    except Exception as e:
        print(f'[email_busca] falha ao buscar corpo de {m["id"]}: {e}')
        notas.append('não consegui baixar o corpo do email')

    if m.get('hasAttachments'):
        try:
            att_resp = requests.get(f'{GRAPH}/me/messages/{m["id"]}/attachments', headers=headers, timeout=60)
            att_resp.raise_for_status()
            for att in att_resp.json().get('value', []):
                if not _e_pdf(att) or not att.get('contentBytes'):
                    continue
                extraido = _extrair_pdf(att['contentBytes'], senha)
                if extraido.strip():
                    texto += '\n' + extraido
                elif senha:
                    notas.append(f'o PDF "{att.get("name")}" não abriu — senha '
                                 'cadastrada não serviu, ou é imagem escaneada')
                else:
                    notas.append(f'o PDF "{att.get("name")}" não rendeu texto — '
                                 'pode estar protegido por senha (cadastre em '
                                 'config.senhas_fatura) ou ser imagem escaneada')
        except Exception as e:
            print(f'[email_busca] falha ao buscar anexos de {m["id"]}: {e}')
            notas.append('não consegui baixar os anexos deste email')

    # Fatura atrás de link (SulAmérica): só quando o remetente está liberado e
    # ainda **não há boleto**.
    #
    # A condição olha especificamente a linha digitável, e não "qualquer código".
    # O email da SulAmérica traz um Pix no corpo: checar "já tem algum código"
    # achava esse Pix, concluía que estava resolvido e nunca seguia o link — que
    # é justamente onde mora o boleto. Pix no corpo não é motivo para desistir
    # do boleto; boleto já encontrado, sim.
    if senha and not RE_LINHA_DIGITAVEL.search(texto):
        links = _links_de_fatura(html, cegos=True)
        print(f'[email_busca] {remetente}: sem boleto no corpo, tentando {len(links)} link(s)')
        for url in links:
            baixado = _texto_da_fatura_linkada(url, senha)
            if baixado:
                achou = bool(RE_LINHA_DIGITAVEL.search(baixado))
                print(f'[email_busca]   {url[:70]} -> {len(baixado)} chars, boleto={achou}')
                texto += '\n' + baixado
                if achou:
                    break

    return texto, notas


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


def _marcar_ja_associado(codigo: dict, associados: dict) -> dict:
    """Esse código já foi associado numa busca anterior?

    Rodar a busca de novo no mesmo período é normal (e "Repetir mês anterior"
    faz isso de propósito). Sem essa checagem, tudo que já está gravado volta
    pedindo associação outra vez, e no carnê do Safra isso significa reconferir
    dezenas de parcelas já resolvidas. `valor_bate` separa "já resolvido,
    ignore" de "estava associado mas o valor mudou" — este segundo caso precisa
    de atenção, não de silêncio.
    """
    ja = associados.get(codigo['chave'])
    if not ja:
        return None
    valor_novo = codigo.get('valor_num')
    esperado = ja['valor_esperado']
    if valor_novo is None or not esperado:
        bate = None          # sem valor dos dois lados não há o que comparar
    else:
        bate = abs(valor_novo - esperado) < 0.01
    return {
        'despesa_id': ja['despesa_id'],
        'despesa_nome': ja['despesa_nome'],
        'mes_ref': ja['mes_ref'],
        'valor_esperado': esperado,
        'valor_bate': bate,
    }


def _buscar_dia(token, dia, despesas, despesas_por_nome, despesas_por_id, regras,
                client, associados, dia_corte):
    """Busca e classifica os emails de um único dia. Retorna
    (emails_do_dia, total_emails_do_dia, lotes_de_classificacao_com_falha).

    Cada email achado carrega **todos** os códigos de pagamento que tem — um
    carnê de financiamento traz o cronograma inteiro num PDF só.
    """
    mensagens = _mensagens_no_periodo(token, dia, dia)

    lotes_com_falha = 0
    if client:
        ids_fatura, lotes_com_falha = _gemini_classificar(client, mensagens)
    else:
        ids_fatura = None

    achados = []
    for m in mensagens:
        assunto = m.get('subject') or ''
        remetente = (m.get('from') or {}).get('emailAddress', {}).get('address') or ''
        recebido = (m.get('receivedDateTime') or '')[:10]
        # remetente já associado antes por você — entra na lista e vem
        # pré-preenchido, sem depender de bater de novo por assunto/IA
        despesa_conhecida = despesas_por_id.get(regras.get(remetente))

        # ---- passa pelo filtro? (as duas portas de entrada de antes) ----
        if client:
            if m['id'] not in ids_fatura and not despesa_conhecida:
                continue
            sugerida_kw = None
        else:
            sugerida_kw = despesa_conhecida or _despesa_sugerida(assunto, remetente, despesas)
            if not sugerida_kw and not _parece_fatura(assunto):
                continue

        texto, notas = _texto_completo(token, m)

        # ---- todos os códigos, do próprio texto ----
        try:
            referencia = datetime.date.fromisoformat(recebido)
        except ValueError:
            referencia = datetime.date.today()
        codigos = _todos_os_codigos(texto, referencia)

        # ---- despesa sugerida ----
        valor_ia = None
        if client:
            # O Gemini fica para quando ele é de fato necessário: sem regra de
            # remetente, ou sem nenhum boleto com valor próprio. É a chamada que
            # domina o tempo da busca (uma por email candidato), e num carnê com
            # 60 parcelas ela não tem nada a acrescentar — valor e vencimento
            # saem da linha digitável, que não alucina.
            tem_valor_proprio = any(c['tipo_codigo'] == 'boleto' and c['valor_num'] for c in codigos)
            if despesa_conhecida and tem_valor_proprio:
                despesa, origem_sugestao = despesa_conhecida, 'regra'
            else:
                info = _gemini_extrair(client, texto, list(despesas_por_nome.keys()))
                despesa_ia = despesas_por_nome.get(info.get('despesa_sugerida') or '')
                despesa = despesa_conhecida or despesa_ia
                origem_sugestao = 'regra' if despesa_conhecida else ('ia' if despesa_ia else None)
                bruto = info.get('valor')
                valor_ia = _fmt_valor(bruto) if isinstance(bruto, (int, float)) else None
        else:
            despesa = sugerida_kw
            origem_sugestao = 'regra' if despesa_conhecida else ('palavra_chave' if sugerida_kw else None)
            if not despesa and not codigos:
                continue
            _, _, valor_ia = _extrair_codigo_regex(texto)

        # Pix e boleto sem valor no código herdam o valor lido do texto — é o
        # único que existe nesses casos. Com vários códigos no mesmo email não
        # dá para saber de qual parcela esse valor é, então só vale quando há
        # um código sozinho.
        if valor_ia and len(codigos) == 1 and not codigos[0]['valor']:
            codigos[0]['valor'] = valor_ia

        mes_email = competencia_da_data(recebido, dia_corte) if len(recebido) == 10 else None

        for c in codigos:
            # O mês vem do **vencimento do próprio boleto**, não da data do
            # email: num carnê todas as parcelas chegam no mesmo email e cairiam
            # todas no mesmo mês. Sem vencimento (Pix), vale a competência do
            # email, como antes.
            c['mes_ref_sugerido'] = (competencia_da_data(c['vencimento'], dia_corte)
                                     if c.get('vencimento') else mes_email)
            c['ja_associado'] = _marcar_ja_associado(c, associados)
            c.pop('valor_num', None)

        if not codigos and not notas:
            notas.append('nenhum boleto ou Pix no corpo nem nos anexos — '
                         'abra o email manualmente')

        achados.append({
            'id': m['id'],
            'assunto': assunto,
            'remetente': remetente,
            'data': m.get('receivedDateTime'),
            'mes_ref_sugerido': mes_email,
            'despesa_sugerida_id': despesa['id'] if despesa else None,
            'despesa_sugerida_nome': despesa['nome'] if despesa else None,
            'origem_sugestao': origem_sugestao,
            'codigos': codigos,
            'notas': notas,
        })

    return achados, len(mensagens), lotes_com_falha


def _rodar_busca(token, dias):
    """Roda em thread de fundo: processa um dia por vez, publicando progresso
    em _busca_job a cada dia concluído para o frontend fazer polling. Verifica
    o pedido de cancelamento entre dias — se cancelada, para ali e mantém tudo
    que já foi encontrado (nada é descartado)."""
    global _SENHAS_CACHE
    _SENHAS_CACHE = None      # relê config.senhas_fatura a cada busca
    conn = get_db()
    despesas = conn.execute("SELECT * FROM despesa WHERE ativo=1 ORDER BY nome").fetchall()
    regras = {r['remetente']: r['despesa_id'] for r in conn.execute('SELECT remetente, despesa_id FROM email_despesa_regra').fetchall()}
    # Códigos já gravados, para a busca repetida não pedir de novo o que já está
    # resolvido. A chave é o código **sem pontuação**: o mesmo boleto gravado
    # como '03399.80740 ...' e reextraído com outro espaçamento tem de casar.
    associados = {}
    for r in conn.execute(
        'SELECT l.despesa_id, l.mes_ref, l.valor_esperado, l.linha_digitavel, '
        '       l.tipo_codigo, d.nome AS despesa_nome '
        '  FROM lancamento l JOIN despesa d ON d.id = l.despesa_id '
        " WHERE l.linha_digitavel IS NOT NULL AND l.linha_digitavel <> ''"
    ).fetchall():
        bruto = r['linha_digitavel']
        chave = _so_digitos(bruto) if r['tipo_codigo'] != 'pix' else re.sub(r'\s+', '', bruto)
        associados[chave] = {
            'despesa_id': r['despesa_id'], 'despesa_nome': r['despesa_nome'],
            'mes_ref': r['mes_ref'], 'valor_esperado': r['valor_esperado'],
        }
    # O mês de destino da associação é competência, não mês de calendário — e
    # essa regra mora no servidor. A tela derivava o mês do fim do período
    # pesquisado, o que arquivava um boleto de outubro em setembro quando a
    # busca terminava em 30/09.
    dia_corte = int(get_config_value(conn, 'dia_recebimento_salario', '26'))
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
            emails_dia, total_dia, falha_dia = _buscar_dia(
                token, dia, despesas, despesas_por_nome, despesas_por_id, regras,
                client, associados, dia_corte
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
            _busca_job['emails'].extend(emails_dia)
            _busca_job['emails'].sort(key=lambda e: e['data'] or '', reverse=True)
            _busca_job['total_codigos'] += sum(len(e['codigos']) for e in emails_dia)
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


@bp.route('/email/diagnostico')
def diagnostico():
    """Por que o boleto de um remetente não saiu — passo a passo, sem adivinhar.

    Abra no navegador: `/api/email/diagnostico?termo=sulamerica`

    Não grava nada e não associa nada: só mostra o que cada etapa enxergou, para
    dizer se o problema é a âncora que não casa, o download que falha ou a senha
    que não abre o PDF.
    """
    termo = request.args.get('termo', 'sulamerica')
    limite = min(int(request.args.get('limite', 3)), 10)

    token = _token_valido()
    if not token:
        return jsonify({'ok': False, 'msg': 'Conecte a conta de email primeiro'}), 400

    headers = {'Authorization': f'Bearer {token}'}
    r = requests.get(f'{GRAPH}/me/messages', headers=headers, timeout=40, params={
        '$search': f'"{termo}"', '$top': limite,
        '$select': 'id,subject,from,receivedDateTime,hasAttachments'})
    if r.status_code != 200:
        return jsonify({'ok': False, 'msg': f'Graph respondeu {r.status_code}', 'corpo': r.text[:400]}), 502

    saida = []
    for m in r.json().get('value', []):
        remetente = (m.get('from') or {}).get('emailAddress', {}).get('address') or ''
        senha = _senha_do_remetente(remetente)
        item = {
            'assunto': m.get('subject'), 'de': remetente,
            'recebido': (m.get('receivedDateTime') or '')[:10],
            'remetente_liberado': bool(senha),
            'senha_usada': ('*' * len(senha)) if senha else None,
        }

        html = ''
        try:
            b = requests.get(f'{GRAPH}/me/messages/{m["id"]}', headers=headers,
                             params={'$select': 'body'}, timeout=30)
            html = b.json().get('body', {}).get('content', '')
        except Exception as e:
            item['erro_corpo'] = str(e)[:200]

        texto_corpo = _extrair_texto_html(html)
        try:
            ref = datetime.date.fromisoformat(item['recebido'])
        except ValueError:
            ref = datetime.date.today()
        achado_corpo = RE_LINHA_DIGITAVEL.search(texto_corpo)
        item['corpo'] = {
            'tem_boleto': bool(achado_corpo),
            'boleto': re.sub(r'\s+', ' ', achado_corpo.group()).strip() if achado_corpo else None,
            'tem_pix': bool(RE_PIX.search(texto_corpo)),
            'codigos': _todos_os_codigos(texto_corpo, ref),
        }

        # Anexos: a Porto manda a fatura como PDF anexado e cifrado, sem link
        # nenhum no corpo. Sem enxergar o anexo aqui, o diagnostico nao
        # distinguiria "nao achei o link" de "o link nao existe".
        item['anexos'] = []
        if m.get('hasAttachments'):
            try:
                ar = requests.get(f'{GRAPH}/me/messages/{m["id"]}/attachments',
                                  headers=headers, timeout=60)
                for att in ar.json().get('value', []):
                    a_item = {'nome': att.get('name'), 'tipo': att.get('contentType'),
                              'bytes': att.get('size'), 'reconhecido_como_pdf': _e_pdf(att)}
                    # `_e_pdf` em vez de comparar contentType: emissor que manda
                    # o carnê como octet-stream tinha o anexo ignorado, e o
                    # diagnóstico precisa mostrar justamente essa diferença
                    if _e_pdf(att) and att.get('contentBytes'):
                        bruto = base64.b64decode(att['contentBytes'])
                        texto_anexo = _texto_de_pdf(bruto)
                        a_item['abriu_sem_senha'] = bool(texto_anexo)
                        if senha and not texto_anexo:
                            texto_anexo = _texto_de_pdf(bruto, senha)
                            a_item['abriu_com_senha'] = bool(texto_anexo)
                        a_item['chars_extraidos'] = len(texto_anexo)
                        # TODOS os códigos do anexo: é o ponto do carnê de
                        # financiamento, que traz dezenas de parcelas num PDF só
                        codigos = _todos_os_codigos(texto_anexo, ref)
                        a_item['qtd_codigos'] = len(codigos)
                        a_item['codigos'] = codigos
                        if codigos:
                            item['BOLETO'] = codigos[0]['codigo']
                    item['anexos'].append(a_item)
            except Exception as e:
                item['erro_anexos'] = str(e)[:200]

        # TODAS as âncoras, não só as que casam — é isso que diz se o rótulo
        # esperado ("Clique aqui para baixar a fatura") está sendo reconhecido
        todas = [{'rotulo': _rotulo_da_ancora(t)[:70],
                  'href': _desembrulhar_safelink(h)[:110]}
                 for h, t in RE_ANCORA.findall(html or '')]
        item['ancoras_no_email'] = todas[:25]
        item['ancoras_rotuladas'] = _links_de_fatura(html)
        alvos = _links_de_fatura(html, cegos=True)
        item['modo'] = 'rotulo' if item['ancoras_rotuladas'] else 'cego'
        item['alvos'] = len(alvos)

        if senha:
            item['downloads'] = []
            for url in alvos:
                d = {'url': url[:110]}
                try:
                    resp = requests.get(url, timeout=40, allow_redirects=True,
                                        headers={'User-Agent': 'Mozilla/5.0'})
                    d['status'] = resp.status_code
                    d['content_type'] = resp.headers.get('Content-Type', '')[:60]
                    d['bytes'] = len(resp.content)
                    d['e_pdf'] = resp.content[:5] == b'%PDF-'
                    if d['e_pdf']:
                        d['abriu_sem_senha'] = bool(_texto_de_pdf(resp.content))
                        txt = _texto_de_pdf(resp.content, senha)
                        d['abriu_com_senha'] = bool(txt)
                        d['tem_boleto'] = bool(RE_LINHA_DIGITAVEL.search(txt))
                    else:
                        # página intermediária: mostra o que há dentro dela
                        dentro = resp.content[:400000].decode('utf-8', errors='replace')
                        d['links_dentro'] = _links_de_fatura(dentro)[:6]
                        d['tem_form_senha'] = bool(
                            re.search(r'<input[^>]+type=["\']password', dentro, re.I))
                except Exception as e:
                    d['erro'] = str(e)[:200]
                item['downloads'].append(d)
                if d.get('tem_boleto'):
                    # achou: mostra o codigo e para de tentar
                    achado = RE_LINHA_DIGITAVEL.search(_texto_de_pdf(resp.content, senha))
                    item['BOLETO'] = re.sub(r'\s+', ' ', achado.group()).strip()
                    break

        saida.append(item)

    return jsonify({'ok': True, 'termo': termo, 'mensagens': saida})


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
            'erro': None, 'avisos': [], 'emails': [], 'total_codigos': 0,
            'total_emails': 0, 'despesas_pesquisadas': 0, 'dia_atual': dias[0],
            'total_dias': len(dias), 'dias_processados': 0,
            'periodo': {'ini': ini, 'fim': fim},
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
    status de pagamento, só guarda o código de pagamento e atualiza o valor
    esperado pro valor real encontrado no email (a maioria dos meses já tem
    uma linha prevista herdada do histórico, então só gravar o valor quando
    o lançamento ainda não existia deixava o valor da fatura de fora quase
    sempre). Também grava o remetente como regra: da próxima vez, esse email
    já vem pré-associado à despesa."""
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

    # Uma despesa só tem um código por competência (UNIQUE(mes_ref, despesa_id)).
    # Associar um carnê inteiro de uma vez põe dezenas de parcelas na mesma
    # despesa, e duas caindo na mesma competência fariam a segunda sobrescrever
    # a primeira sem ninguém ver. Trabalho já confirmado não se perde calado
    # (princípio 2): o item é recusado e volta como conflito para a tela.
    atual = conn.execute(
        'SELECT linha_digitavel FROM lancamento WHERE despesa_id=? AND mes_ref=?',
        (despesa_id, mes_ref)
    ).fetchone()
    if (atual and atual['linha_digitavel'] and linha_digitavel
            and _so_digitos(atual['linha_digitavel']) != _so_digitos(linha_digitavel)
            and not item.get('substituir')):
        return {
            'conflito': True, 'mes_ref': mes_ref, 'despesa_id': despesa_id,
            'codigo_atual': atual['linha_digitavel'], 'codigo_novo': linha_digitavel,
        }

    conn.execute(
        'INSERT OR IGNORE INTO lancamento (mes_ref, despesa_id, valor_esperado, status) VALUES (?,?,?,\'nao_encontrado\')',
        (mes_ref, despesa_id, valor or 0)
    )
    if valor:
        conn.execute(
            'UPDATE lancamento SET linha_digitavel=?, tipo_codigo=?, valor_esperado=? WHERE despesa_id=? AND mes_ref=?',
            (linha_digitavel, tipo_codigo, valor, despesa_id, mes_ref)
        )
    else:
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

    return None


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
    conflitos, aplicadas = [], 0
    try:
        for item in itens:
            conflito = _aplicar_associacao(conn, item)
            if conflito:
                conflitos.append(conflito)
            else:
                aplicadas += 1
    except ValueError as e:
        conn.close()
        return jsonify({'ok': False, 'msg': str(e)}), 400

    conn.commit()
    conn.close()
    # Conflito não é erro da requisição: o que deu para gravar foi gravado, e o
    # que foi recusado volta nomeado para a tela poder dizer qual mês e por quê.
    return jsonify({'ok': True, 'aplicadas': aplicadas, 'conflitos': conflitos})


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
