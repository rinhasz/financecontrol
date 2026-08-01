import re
import csv as csvlib
import io
from datetime import date
from flask import Blueprint, jsonify, request
from .db import get_db

bp = Blueprint('importacao', __name__)


def normalize_text(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize('NFD', s.lower())
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z0-9 ]', ' ', s)


def parse_br_date(s: str):
    s = s.strip()
    m = re.match(r'^(\d{2})/(\d{2})/(\d{4})', s)
    if m:
        return f'{m.group(3)}-{m.group(2)}-{m.group(1)}'
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        return s[:10]
    return None


def parse_br_number(s: str) -> float:
    s = s.strip().strip('"')
    if re.search(r'[\d.]+,\d{2}$', s):
        return float(s.replace('.', '').replace(',', '.'))
    try:
        return float(s.replace(',', '.'))
    except ValueError:
        return float('nan')


def parse_ofx(content: str):
    txs = []
    blocks = re.findall(r'<STMTTRN>(.*?)</STMTTRN>', content, re.DOTALL)
    if not blocks:
        # SGML format
        lines = content.splitlines()
        in_tx = False
        cur = {}
        for line in lines:
            line = line.strip()
            if line == '<STMTTRN>':
                in_tx = True; cur = {}; continue
            if line == '</STMTTRN>':
                in_tx = False
                m = re.match(r'^(\d{8})', cur.get('DTPOSTED', ''))
                if m:
                    d = m.group(1)
                    data = f'{d[:4]}-{d[4:6]}-{d[6:8]}'
                    try:
                        valor = float(cur.get('TRNAMT', '0').replace(',', '.'))
                        txs.append({'data': data, 'descricao': cur.get('MEMO') or cur.get('NAME', ''), 'valor': valor})
                    except ValueError:
                        pass
                continue
            if in_tx:
                m = re.match(r'^<([A-Z]+)>(.*)', line)
                if m:
                    cur[m.group(1)] = m.group(2).strip()
    else:
        for block in blocks:
            def ex(tag):
                m = re.search(f'<{tag}>([^<]*)', block)
                return m.group(1).strip() if m else ''
            dt = ex('DTPOSTED')
            m = re.match(r'^(\d{8})', dt)
            if not m:
                continue
            d = m.group(1)
            data = f'{d[:4]}-{d[4:6]}-{d[6:8]}'
            try:
                valor = float(ex('TRNAMT').replace(',', '.'))
            except ValueError:
                continue
            txs.append({'data': data, 'descricao': ex('MEMO') or ex('NAME'), 'valor': valor})
    return txs


def parse_xlsx_content(content: bytes):
    import openpyxl
    from datetime import datetime as _dt, date as _date

    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    header_idx = None
    col = {}
    for i, row in enumerate(rows[:30]):
        headers = [normalize_text(str(c)) if c is not None else '' for c in row]
        date_idx = next((j for j, h in enumerate(headers) if re.search(r'data|lancamento|dt lanc', h)), None)
        desc_idx = next((j for j, h in enumerate(headers) if re.search(r'desc|hist|memo|lancamento', h)), None)
        val_idx = next((j for j, h in enumerate(headers) if re.search(r'valor|amount', h)), None)
        if date_idx is not None and desc_idx is not None and val_idx is not None and date_idx != desc_idx:
            header_idx = i
            col = {'data': date_idx, 'descricao': desc_idx, 'valor': val_idx}
            sit_idx = next((j for j, h in enumerate(headers) if re.search(r'situa|status', h)), None)
            if sit_idx is not None:
                col['situacao'] = sit_idx
            break

    if header_idx is None:
        return []

    txs = []
    for row in rows[header_idx + 1:]:
        if row is None or all(c is None for c in row):
            continue
        raw_data = row[col['data']] if col['data'] < len(row) else None
        raw_desc = row[col['descricao']] if col['descricao'] < len(row) else None
        raw_valor = row[col['valor']] if col['valor'] < len(row) else None
        if raw_data is None or raw_valor is None:
            continue

        if isinstance(raw_data, (_dt, _date)):
            data = raw_data.strftime('%Y-%m-%d')
        else:
            data = parse_br_date(str(raw_data))
        if not data:
            continue

        if isinstance(raw_valor, (int, float)):
            valor = float(raw_valor)
        else:
            valor = parse_br_number(str(raw_valor))
            if valor != valor:
                continue

        tx = {'data': data, 'descricao': str(raw_desc or '').strip(), 'valor': valor}

        if 'situacao' in col:
            raw_sit = row[col['situacao']] if col['situacao'] < len(row) else None
            sit_norm = normalize_text(str(raw_sit or ''))
            if any(k in sit_norm for k in ('futur', 'agend', 'program')):
                tx['situacao'] = 'agendada'
            elif sit_norm:
                tx['situacao'] = 'efetivada'

        txs.append(tx)

    return txs


def parse_csv_content(content: str):
    txs = []
    for delim in [',', ';', '\t']:
        reader = csvlib.DictReader(io.StringIO(content), delimiter=delim)
        rows = list(reader)
        if rows and len(rows[0]) >= 2:
            headers = list(rows[0].keys())
            date_col = next((h for h in headers if re.search(r'data|date', h, re.I)), None)
            desc_col = next((h for h in headers if re.search(r'desc|hist|lança|lancam|memo', h, re.I)), None)
            val_col = next((h for h in headers if re.search(r'valor|amount|value', h, re.I)), None)
            if not all([date_col, desc_col, val_col]):
                continue
            for row in rows:
                data = parse_br_date(row.get(date_col, ''))
                if not data:
                    continue
                try:
                    valor = parse_br_number(row.get(val_col, ''))
                    if valor != valor:
                        continue
                except Exception:
                    continue
                txs.append({'data': data, 'descricao': row.get(desc_col, ''), 'valor': valor})
            if txs:
                break
    return txs


@bp.route('/importacao', methods=['POST'])
def importar():
    file = request.files.get('file')
    banco = request.form.get('banco', 'Desconhecido')
    mes_ref = request.form.get('mes_ref', '')
    if not file:
        return jsonify({'ok': False, 'msg': 'Nenhum arquivo enviado'}), 400

    filename = file.filename or 'extrato'
    ext = filename.rsplit('.', 1)[-1].lower()
    content = file.read()

    if ext in ('xlsx', 'xlsm'):
        txs = parse_xlsx_content(content)
        formato = 'xlsx'
    else:
        try:
            text = content.decode('latin-1')
        except Exception:
            text = content.decode('utf-8', errors='replace')

        if ext == 'ofx':
            txs = parse_ofx(text)
            formato = 'ofx'
        else:
            txs = parse_csv_content(text)
            formato = 'csv'

    if not txs:
        return jsonify({'ok': False, 'msg': 'Nenhuma transação encontrada'}), 400

    datas = sorted(t['data'] for t in txs)
    conn = get_db()
    cur = conn.execute(
        'INSERT INTO importacao (banco, formato, arquivo, periodo_ini, periodo_fim) VALUES (?,?,?,?,?)',
        (banco, formato, filename, datas[0], datas[-1])
    )
    import_id = cur.lastrowid
    today = date.today().isoformat()

    batch = []
    for t in txs:
        situacao = t.get('situacao') or ('agendada' if t['data'] > today else 'efetivada')
        tipo = 'debito' if t['valor'] < 0 else 'credito'
        batch.append((t['data'], t['descricao'], t['valor'], tipo, situacao, banco, import_id))

    conn.executemany(
        'INSERT INTO transacao (data, descricao, valor, tipo, situacao, banco_origem, import_id) VALUES (?,?,?,?,?,?,?)',
        batch
    )
    conn.commit()
    conn.close()

    return jsonify({'ok': True, 'msg': f'{len(txs)} transações importadas', 'transacoes': txs, 'import_id': import_id})


@bp.route('/batimento', methods=['POST'])
def rodar_batimento():
    mes_ref = request.json.get('mes_ref', '')
    ano, mes = mes_ref.split('-')
    ini = f'{ano}-{mes}-01'
    fim = f'{ano}-{mes}-31'

    conn = get_db()
    lancamentos = conn.execute("""
        SELECT l.*, d.tipo_valor, d.regras_match, d.dia_vencimento
        FROM lancamento l JOIN despesa d ON d.id = l.despesa_id
        WHERE l.mes_ref=? AND l.status='nao_encontrado'
    """, (mes_ref,)).fetchall()

    transacoes = conn.execute("""
        SELECT * FROM transacao
        WHERE data BETWEEN ? AND ? AND tipo='debito' AND despesa_id IS NULL
    """, (ini, fim)).fetchall()

    import json as _json

    # Monta todos os pares (lançamento, transação) com score >= 3 primeiro,
    # sem travar nenhum casamento ainda — evita que uma despesa processada
    # antes "roube" a transação de outra despesa com palavra-chave parecida
    # (ex: "cartao xp" vs "cartao black" concorrendo pela mesma palavra "cartao").
    candidatos = []
    for l in lancamentos:
        regras = _json.loads(l['regras_match'])
        keywords = [kw for kw in regras.get('palavras_chave', []) if kw]
        janela = regras.get('janela_dias', 5)

        for t in transacoes:
            tx_abs = abs(t['valor'])
            esperado = l['valor_esperado']
            score = 0

            if l['tipo_valor'] == 'fixo':
                if esperado > 0 and abs(tx_abs - esperado) / esperado <= 0.005:
                    score += 3
            else:
                if esperado > 0 and abs(tx_abs - esperado) / esperado <= 0.15:
                    score += 2

            if l['dia_vencimento']:
                tx_dia = int(t['data'].split('-')[2])
                if abs(tx_dia - l['dia_vencimento']) <= janela:
                    score += 2

            desc_norm = normalize_text(t['descricao'])
            if keywords:
                matched_kw = sum(1 for kw in keywords if normalize_text(kw) in desc_norm)
                if matched_kw == len(keywords):
                    score += 3
                elif matched_kw > 0:
                    score += 1

            if score >= 3:
                candidatos.append((score, l, t))

    # Casamentos com maior confiança (score) são resolvidos primeiro
    candidatos.sort(key=lambda c: c[0], reverse=True)

    lanc_matched = set()
    tx_used = set()
    matched = 0

    for score, l, t in candidatos:
        if l['id'] in lanc_matched or t['id'] in tx_used:
            continue
        status = 'pago' if t['situacao'] == 'efetivada' else 'agendado'
        conn.execute(
            'UPDATE lancamento SET status=?, transacao_id=?, valor_real=?, data_pagamento=? WHERE id=?',
            (status, t['id'], abs(t['valor']), t['data'], l['id'])
        )
        conn.execute(
            "UPDATE transacao SET despesa_id=?, classificacao='recorrente' WHERE id=?",
            (l['despesa_id'], t['id'])
        )
        lanc_matched.add(l['id'])
        tx_used.add(t['id'])
        matched += 1

    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'matched': matched, 'total': len(lancamentos)})


@bp.route('/transacoes')
def list_transacoes():
    mes_ref = request.args.get('mes', '')
    ano, mes = mes_ref.split('-')
    ini = f'{ano}-{mes}-01'
    fim = f'{ano}-{mes}-31'
    conn = get_db()
    rows = conn.execute("""
        SELECT t.*, d.nome as despesa_nome
        FROM transacao t LEFT JOIN despesa d ON d.id = t.despesa_id
        WHERE t.data BETWEEN ? AND ? ORDER BY t.data
    """, (ini, fim)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])
