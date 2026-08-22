import re
import csv as csvlib
import io
import os
from datetime import date
from flask import Blueprint, jsonify, request
from .db import (get_db, get_config_value, periodo_competencia,
                 padrao_descricao, registrar_regra_transacao)

DICIONARIO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs', '07-dicionario-despesas.md')

bp = Blueprint('importacao', __name__)


def normalize_text(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize('NFD', s.lower())
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z0-9 ]', ' ', s)


def tem_palavra(alvo: str, kw: str) -> bool:
    """Casamento de palavra inteira, não substring — 'conta' não pode bater
    com 'contato', nem 'net' com 'netflix', nem 'casa' com 'casas'.
    `alvo` já deve estar normalizado; `kw` é normalizado aqui."""
    kw_norm = normalize_text(kw)
    return bool(kw_norm) and re.search(r'\b' + re.escape(kw_norm) + r'\b', alvo) is not None


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


def _sheet_rows_xlsx(content: bytes):
    """Retorna uma lista de planilhas (cada uma uma lista de linhas) de um .xlsx/.xlsm."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    return [list(ws.iter_rows(values_only=True)) for ws in wb.worksheets]


def _sheet_rows_xls(content: bytes):
    """Retorna uma lista de planilhas (cada uma uma lista de linhas) de um .xls legado (BIFF/OLE2)."""
    import xlrd
    wb = xlrd.open_workbook(file_contents=content)
    sheets = []
    for ws in wb.sheets():
        sheets.append([
            [ws.cell_value(r, c) for c in range(ws.ncols)]
            for r in range(ws.nrows)
        ])
    return sheets


def _find_header(rows):
    """Acha a linha de cabeçalho (data/lançamento/valor) e o índice de cada coluna.

    Extratos de banco costumam ter várias linhas de metadados (logo, nome,
    agência...) antes da tabela real — por isso varremos as primeiras linhas
    procurando o cabeçalho, em vez de assumir que é a linha 0.
    """
    for i, row in enumerate(rows[:40]):
        headers = [normalize_text(str(c)) if c not in (None, '') else '' for c in row]
        # 'data' isolado identifica a coluna de data; não pode casar com a
        # coluna "lançamento" (descrição), que também contém a palavra.
        date_idx = next((j for j, h in enumerate(headers) if h.startswith('data') or 'dt lanc' in h or 'data mov' in h), None)
        desc_idx = next((j for j, h in enumerate(headers) if re.search(r'\bdesc|\bhist|\bmemo|\blancamento\b', h)), None)
        val_idx = next((j for j, h in enumerate(headers) if re.search(r'\bvalor\b|\bamount\b', h)), None)
        if date_idx is not None and desc_idx is not None and val_idx is not None and date_idx != desc_idx:
            col = {'data': date_idx, 'descricao': desc_idx, 'valor': val_idx}
            sit_idx = next((j for j, h in enumerate(headers) if re.search(r'situa|status', h)), None)
            if sit_idx is not None:
                col['situacao'] = sit_idx
            return i, col
    return None, {}


def _parse_excel_sheet(rows):
    from datetime import datetime as _dt, date as _date

    header_idx, col = _find_header(rows)
    if header_idx is None:
        return []

    txs = []
    # Extratos do Itaú marcam o início dos lançamentos agendados com uma
    # linha tipo "lançamentos futuros" / "saídas futuras" em vez de uma
    # coluna de status — tudo que vier depois dessa marca é 'agendada'.
    future = False
    for row in rows[header_idx + 1:]:
        if row is None or all(c is None or c == '' for c in row):
            continue

        raw_data = row[col['data']] if col['data'] < len(row) else None
        raw_desc = row[col['descricao']] if col['descricao'] < len(row) else None
        raw_valor = row[col['valor']] if col['valor'] < len(row) else None

        label = normalize_text(f"{raw_data or ''} {raw_desc or ''}")
        if 'futur' in label:
            future = True
            continue

        if raw_data in (None, '') or raw_valor in (None, ''):
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
        if future:
            tx['situacao'] = 'agendada'

        txs.append(tx)

    return txs


def parse_excel_content(content: bytes, ext: str):
    sheets = _sheet_rows_xls(content) if ext == 'xls' else _sheet_rows_xlsx(content)
    for rows in sheets:
        txs = _parse_excel_sheet(rows)
        if txs:
            return txs
    return []


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


def _reconciliar_agendadas(conn, ini: str, fim: str) -> int:
    """Colapsa o "fantasma" agendado quando o débito caiu com outra descrição.

    O Itaú **troca o texto** do lançamento quando ele sai de "lançamentos
    futuros" para debitado de fato:

        'PAG TIT 662992535000'  ->  'PAG BOLETO EDIFICIO LINCOLN GARDEN'
        'Agendado'              ->  'FINANC IMOBILIARIO 038/397'
        'PIX QRS SUL AMERICA'   ->  'PIX QRS SUL AMERICA10/08'

    A dedupe da importação é `(data, descrição, valor)`, então as duas
    versões coexistem: uma linha `agendada` órfã e a linha `efetivada` real.
    O batimento podia casar a despesa com a órfã e mostrar "Agendado" num mês
    em que o extrato já diz pago — foi exatamente o sintoma relatado.

    Data e valor **não** mudam nessa transição, e são a chave usada aqui. Só
    colapsa quando o par é inequívoco (exatamente uma agendada e uma efetivada
    para aquele data+valor) e a data já passou — agendamento futuro não tem o
    que reconciliar.

    Idempotente: rodar de novo não encontra mais par nenhum.
    """
    hoje = date.today().isoformat()
    rows = conn.execute(
        "SELECT id, data, descricao, valor, situacao, despesa_id, classificacao "
        "FROM transacao WHERE data BETWEEN ? AND ? AND tipo='debito'",
        (ini, fim)
    ).fetchall()

    grupos = {}
    for r in rows:
        grupos.setdefault((r['data'], r['valor']), []).append(r)

    colapsadas = 0
    for (data_tx, _valor), grupo in grupos.items():
        if data_tx > hoje:
            continue
        agendadas = [r for r in grupo if r['situacao'] == 'agendada']
        efetivadas = [r for r in grupo if r['situacao'] == 'efetivada']
        if len(agendadas) != 1 or len(efetivadas) != 1:
            continue
        fantasma, real = agendadas[0], efetivadas[0]

        # Um casamento já confirmado apontava para a órfã; migra para a linha
        # real antes de apagá-la, senão o lançamento perderia o vínculo.
        if fantasma['despesa_id'] and not real['despesa_id']:
            conn.execute(
                'UPDATE transacao SET despesa_id=?, classificacao=? WHERE id=?',
                (fantasma['despesa_id'], fantasma['classificacao'], real['id'])
            )
        conn.execute(
            "UPDATE lancamento SET transacao_id=?, status='pago', valor_real=?, data_pagamento=? "
            "WHERE transacao_id=?",
            (real['id'], abs(real['valor']), real['data'], fantasma['id'])
        )
        conn.execute('DELETE FROM transacao WHERE id=?', (fantasma['id'],))
        colapsadas += 1

    return colapsadas


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

    if ext in ('xls', 'xlsx', 'xlsm'):
        txs = parse_excel_content(content, ext)
        formato = ext
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

    today = date.today().isoformat()

    def _situacao(t):
        # o parser só marca 'agendada' (seção "lançamentos futuros" do Itaú);
        # o resto veio da parte já debitada do extrato
        return t.get('situacao') or ('agendada' if t['data'] > today else 'efetivada')

    # Importar o mesmo período mais de uma vez no mês é o fluxo normal (o
    # usuário reimporta pra pegar lançamentos novos) — não pode duplicar o
    # que já foi trazido antes. Mesma (data, descrição, valor) = mesma
    # transação; aceita o risco raro de duas transações reais idênticas no
    # mesmo dia serem tratadas como uma só, em troca de nunca duplicar.
    existentes = {
        (r['data'], r['descricao'], r['valor']): r
        for r in conn.execute(
            'SELECT id, data, descricao, valor, situacao FROM transacao WHERE data BETWEEN ? AND ?',
            (datas[0], datas[-1])
        ).fetchall()
    }

    novas = []
    efetivadas = []
    for t in txs:
        anterior = existentes.get((t['data'], t['descricao'], t['valor']))
        if anterior is None:
            novas.append(t)
        elif anterior['situacao'] == 'agendada' and _situacao(t) == 'efetivada':
            # É a mesma transação de antes, mas saiu de "lançamentos futuros"
            # para debitada de fato. Tratar como duplicata a deixaria agendada
            # pra sempre e o lançamento nunca viraria "Pago"; atualizar no
            # lugar preserva o vínculo com a despesa que já foi confirmado.
            efetivadas.append(anterior['id'])

    duplicadas = len(txs) - len(novas) - len(efetivadas)

    cur = conn.execute(
        'INSERT INTO importacao (banco, formato, arquivo, periodo_ini, periodo_fim) VALUES (?,?,?,?,?)',
        (banco, formato, filename, datas[0], datas[-1])
    )
    import_id = cur.lastrowid

    if efetivadas:
        conn.executemany(
            "UPDATE transacao SET situacao='efetivada' WHERE id=?",
            [(i,) for i in efetivadas]
        )

    batch = []
    for t in novas:
        tipo = 'debito' if t['valor'] < 0 else 'credito'
        batch.append((t['data'], t['descricao'], t['valor'], tipo, _situacao(t), banco, import_id))

    if batch:
        conn.executemany(
            'INSERT INTO transacao (data, descricao, valor, tipo, situacao, banco_origem, import_id) VALUES (?,?,?,?,?,?,?)',
            batch
        )

    # A promoção acima só pega quem manteve a descrição. Quando o banco troca
    # o texto ao debitar, a versão nova entrou como transação inédita e a
    # agendada ficou órfã — é aqui que as duas se juntam.
    colapsadas = _reconciliar_agendadas(conn, datas[0], datas[-1])

    conn.commit()
    conn.close()

    msg = f'{len(novas)} transações novas importadas'
    if efetivadas or colapsadas:
        msg += f', {len(efetivadas) + colapsadas} que estavam agendadas foram debitadas'
    if duplicadas:
        msg += f' ({duplicadas} já tinham sido importadas antes e foram ignoradas)'

    return jsonify({'ok': True, 'msg': msg, 'transacoes': novas, 'import_id': import_id})


def _garantir_lancamentos(conn, mes_ref: str) -> None:
    """Abre o mês: cria o lançamento de cada despesa ativa que ainda não tem.

    Mesma operação que /api/lancamentos faz ao listar o Mês Atual. Precisa
    acontecer aqui também porque a seção "despesas ativas que não encontrei"
    do batimento sai dos lançamentos do mês — sem isso, quem importa o extrato
    antes de abrir o Mês Atual veria uma lista incompleta.
    """
    from .lancamentos import _valor_previsto
    for d in conn.execute('SELECT * FROM despesa WHERE ativo=1').fetchall():
        prev = _valor_previsto(conn, d['id'], mes_ref, d['padrao_variabilidade'], d['valor_padrao'])
        conn.execute(
            'INSERT OR IGNORE INTO lancamento (mes_ref, despesa_id, valor_esperado, status) '
            'VALUES (?,?,?,\'nao_encontrado\')',
            (mes_ref, d['id'], prev)
        )


@bp.route('/batimento', methods=['POST'])
def rodar_batimento():
    """Calcula as sugestões de casamento (preview) — não grava nada no banco.
    Só é persistido quando o usuário confirma via /api/batimento/confirmar,
    para não perder o trabalho de quem ainda não terminou de revisar (rodar
    duas vezes sem confirmar não deve fazer nada desaparecer)."""
    mes_ref = request.json.get('mes_ref', '')

    conn = get_db()
    dia_corte = int(get_config_value(conn, 'dia_recebimento_salario', '27'))
    ini, fim = periodo_competencia(mes_ref, dia_corte)

    # Duas manutenções antes de casar. Nenhuma decide casamento — são reparo de
    # dado — mas as duas mudam o que a tela mostra, então rodam mesmo no preview:
    #  1. remove órfãs 'agendada' cujo débito já caiu com outra descrição, senão
    #     o casamento pode ser feito com a órfã e a despesa aparece "Agendado"
    #     num mês em que o extrato já diz pago;
    #  2. garante o lançamento de toda despesa ativa do mês, para a seção
    #     "despesas que não encontrei" ficar completa mesmo se o usuário ainda
    #     não abriu o Mês Atual (é o mesmo INSERT OR IGNORE de /api/lancamentos).
    _reconciliar_agendadas(conn, ini, fim)
    _garantir_lancamentos(conn, mes_ref)
    conn.commit()

    # d.ativo=1: despesa desativada não pode disputar transação com uma ativa —
    # é assim que uma despesa velha e genérica (ex: "cartao xp") acabava
    # roubando o casamento de outra parecida que ainda está em uso
    lancamentos = conn.execute("""
        SELECT l.*, d.nome as despesa_nome, d.tipo_valor, d.regras_match, d.dia_vencimento
        FROM lancamento l JOIN despesa d ON d.id = l.despesa_id
        WHERE l.mes_ref=? AND l.status='nao_encontrado' AND d.ativo=1
    """, (mes_ref,)).fetchall()

    transacoes = conn.execute("""
        SELECT * FROM transacao
        WHERE data BETWEEN ? AND ? AND tipo='debito' AND despesa_id IS NULL
    """, (ini, fim)).fetchall()

    import json as _json

    # Regras aprendidas das confirmações anteriores: padrão de descrição ->
    # despesas que o usuário já disse pertencerem àquele texto.
    regras_aprendidas = {}
    for r in conn.execute('SELECT padrao, despesa_id FROM transacao_despesa_regra'):
        regras_aprendidas.setdefault(r['padrao'], set()).add(r['despesa_id'])
    padrao_por_tx = {t['id']: padrao_descricao(t['descricao']) for t in transacoes}

    # Transações cujo valor encaixa quase exato no previsto de alguma despesa.
    # Só nessas o valor tem força para conter uma regra aprendida (abaixo):
    # se ninguém encaixa no valor, o previsto provavelmente é que está
    # desatualizado, e aí a regra continua sendo a melhor evidência.
    tx_com_dono_por_valor = set()
    for t in transacoes:
        tx_abs = abs(t['valor'])
        for l in lancamentos:
            esp = l['valor_esperado']
            if esp > 0 and abs(tx_abs - esp) / esp <= 0.01:
                tx_com_dono_por_valor.add(t['id'])
                break

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

            bateu_valor = False
            if l['tipo_valor'] == 'fixo':
                if esperado > 0 and abs(tx_abs - esperado) / esperado <= 0.005:
                    score += 3
                    bateu_valor = True
            else:
                if esperado > 0 and abs(tx_abs - esperado) / esperado <= 0.15:
                    score += 2
                    bateu_valor = esperado > 0 and abs(tx_abs - esperado) / esperado <= 0.01

            if l['dia_vencimento']:
                tx_dia = int(t['data'].split('-')[2])
                if abs(tx_dia - l['dia_vencimento']) <= janela:
                    score += 2

            desc_norm = normalize_text(t['descricao'])
            if keywords:
                matched_kw = sum(1 for kw in keywords if tem_palavra(desc_norm, kw))
                if matched_kw == len(keywords):
                    # Quanto mais palavras-chave a despesa exige (mais específica),
                    # mais peso o casamento completo ganha — uma despesa com uma
                    # única palavra genérica (ex: só "cartao") não pode mais vencer
                    # sozinha, sem nenhuma corroboração de valor ou data.
                    score += 2 * len(keywords)
                elif matched_kw > 0:
                    score += 1

            # O que o usuário já confirmou vale mais que qualquer heurística:
            # ele viu o extrato e disse de quem era. E o inverso também é
            # informação — se este texto pertence a outra despesa, casá-lo
            # aqui provavelmente repete um erro já corrigido antes.
            # Vários pagamentos ao mesmo destinatário dividem a descrição
            # (salário, adiantamento e vale transporte da mesma pessoa) e só o
            # valor os separa. Quando o valor desta transação encaixa exato em
            # OUTRA despesa e destoa muito desta, a regra aprendida não pode
            # atropelar — mas a condição é essa, não só o valor destoar: com
            # previsto desatualizado (ou placeholder), a regra ainda é a
            # melhor evidência que existe.
            valor_contradiz = (esperado > 0
                               and abs(tx_abs - esperado) / esperado > 0.5
                               and t['id'] in tx_com_dono_por_valor)

            donos = regras_aprendidas.get(padrao_por_tx.get(t['id']))
            if donos:
                if l['despesa_id'] in donos:
                    if not valor_contradiz:
                        score += 8
                elif not bateu_valor:
                    # penalidade, não desqualificação: uma mesma descrição
                    # pode servir a duas despesas (ex: mensalidade e material
                    # da mesma escola, separadas só pelo valor) e a segunda
                    # ainda precisa conseguir casar antes de ser aprendida.
                    # Por isso valor batendo na mosca isenta da penalidade:
                    # é evidência mais forte que a regra aprendida de outra
                    # despesa que divide o mesmo texto.
                    score -= 4

            if score >= 3:
                candidatos.append((score, l, t))

    # Casamentos com maior confiança (score) são resolvidos primeiro
    candidatos.sort(key=lambda c: c[0], reverse=True)

    lanc_sugerido = set()
    tx_sugerida = set()
    detalhes = []

    for score, l, t in candidatos:
        if l['id'] in lanc_sugerido or t['id'] in tx_sugerida:
            continue
        status = 'pago' if t['situacao'] == 'efetivada' else 'agendado'
        lanc_sugerido.add(l['id'])
        tx_sugerida.add(t['id'])
        detalhes.append({
            'lancamento_id': l['id'],
            'despesa_id': l['despesa_id'],
            'despesa_id_sugerido': l['despesa_id'],
            'despesa_nome': l['despesa_nome'],
            'transacao_id': t['id'],
            'descricao_transacao': t['descricao'],
            'valor': abs(t['valor']),
            'data': t['data'],
            'status': status,
        })

    # Lançamentos já casados antes, cuja transação mudou de situação desde
    # então — tipicamente estavam "Agendado" e o débito acabou de cair. Sem
    # isto eles nunca reapareceriam na revisão (a busca acima só olha
    # 'nao_encontrado') e ficariam presos no status antigo pra sempre.
    defasados = conn.execute("""
        SELECT l.id, l.despesa_id, l.status, d.nome as despesa_nome,
               t.id as tx_id, t.descricao as tx_descricao, t.valor as tx_valor,
               t.data as tx_data, t.situacao as tx_situacao
        FROM lancamento l
        JOIN despesa d ON d.id = l.despesa_id
        JOIN transacao t ON t.id = l.transacao_id
        WHERE l.mes_ref = ?
          AND l.status != (CASE WHEN t.situacao = 'efetivada' THEN 'pago' ELSE 'agendado' END)
    """, (mes_ref,)).fetchall()

    for l in defasados:
        detalhes.append({
            'lancamento_id': l['id'],
            'despesa_id': l['despesa_id'],
            'despesa_id_sugerido': l['despesa_id'],
            'despesa_nome': l['despesa_nome'],
            'transacao_id': l['tx_id'],
            'descricao_transacao': l['tx_descricao'],
            'valor': abs(l['tx_valor']),
            'data': l['tx_data'],
            'status': 'pago' if l['tx_situacao'] == 'efetivada' else 'agendado',
            'status_anterior': l['status'],
        })

    nao_encontrados = [
        {'lancamento_id': l['id'], 'despesa_id': l['despesa_id'], 'despesa_nome': l['despesa_nome'], 'valor_esperado': l['valor_esperado']}
        for l in lancamentos if l['id'] not in lanc_sugerido
    ]
    transacoes_sobrando = [dict(t) for t in transacoes if t['id'] not in tx_sugerida]

    conn.close()
    return jsonify({
        # total inclui os defasados: eles também entram em detalhes, e sem
        # somá-los aqui o contador da tela mostraria coisas como "15/12"
        'ok': True, 'matched': len(detalhes), 'total': len(lancamentos) + len(defasados),
        'periodo': {'ini': ini, 'fim': fim},
        'detalhes': detalhes,
        'nao_encontrados': nao_encontrados,
        'transacoes_sobrando': transacoes_sobrando,
    })


@bp.route('/batimento/confirmar', methods=['POST'])
def confirmar_batimento():
    """Grava de uma vez só os pares (transação, despesa) que o usuário
    revisou na tela — sugestões automáticas aceitas + correções + associações
    manuais. Nada do preview em /api/batimento é persistido antes disso."""
    data = request.json or {}
    mes_ref = data.get('mes_ref', '')
    pares = data.get('pares', [])
    if not mes_ref or not pares:
        return jsonify({'ok': False, 'msg': 'mes_ref e pares são obrigatórios'}), 400

    conn = get_db()
    confirmados = 0
    for par in pares:
        transacao_id = par.get('transacao_id')
        despesa_id = par.get('despesa_id')
        despesa_id_sugerido = par.get('despesa_id_sugerido')
        if not transacao_id or not despesa_id:
            continue

        transacao = conn.execute('SELECT * FROM transacao WHERE id=?', (transacao_id,)).fetchone()
        despesa = conn.execute('SELECT * FROM despesa WHERE id=?', (despesa_id,)).fetchone()
        if not transacao or not despesa:
            continue

        _persistir_par(conn, mes_ref, despesa_id, transacao_id, transacao)
        confirmados += 1

        if despesa_id_sugerido != despesa_id:
            nome_sugerido = None
            if despesa_id_sugerido:
                row = conn.execute('SELECT nome FROM despesa WHERE id=?', (despesa_id_sugerido,)).fetchone()
                nome_sugerido = row['nome'] if row else None
                # o usuário rejeitou esta sugestão: desaprende, senão a regra
                # errada continuaria disputando com a certa nos próximos meses.
                # (não basta o desaprender de _persistir_par: ali só cai o
                # vínculo já gravado, e uma sugestão recusada nunca chegou a
                # ser gravada)
                conn.execute(
                    'DELETE FROM transacao_despesa_regra WHERE padrao=? AND despesa_id=?',
                    (padrao_descricao(transacao['descricao']), despesa_id_sugerido)
                )
            _registrar_dicionario(transacao['descricao'], despesa['nome'], nome_sugerido)

    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'confirmados': confirmados})


def _persistir_par(conn, mes_ref: str, despesa_id: int, transacao_id: int, transacao):
    """Vincula uma transação a uma despesa: garante que o lançamento do mês
    existe, marca como pago/agendado conforme a situação da transação, e
    reverte qualquer vínculo anterior errado que a transação já tivesse."""
    despesa_errada = conn.execute(
        'SELECT id, despesa_id FROM lancamento WHERE transacao_id=? AND despesa_id != ?',
        (transacao_id, despesa_id)
    ).fetchone()
    if despesa_errada:
        conn.execute(
            "UPDATE lancamento SET status='nao_encontrado', transacao_id=NULL, valor_real=NULL, data_pagamento=NULL WHERE id=?",
            (despesa_errada['id'],)
        )
        # desaprende: sem isto a regra errada continuaria competindo com a
        # certa todo mês, e o usuário corrigiria o mesmo caso para sempre
        conn.execute(
            'DELETE FROM transacao_despesa_regra WHERE padrao=? AND despesa_id=?',
            (padrao_descricao(transacao['descricao']), despesa_errada['despesa_id'])
        )

    conn.execute(
        'INSERT OR IGNORE INTO lancamento (mes_ref, despesa_id, valor_esperado, status) VALUES (?,?,?,\'nao_encontrado\')',
        (mes_ref, despesa_id, abs(transacao['valor']))
    )
    status = 'pago' if transacao['situacao'] == 'efetivada' else 'agendado'
    conn.execute(
        """UPDATE lancamento SET status=?, transacao_id=?, valor_real=?, data_pagamento=?
           WHERE despesa_id=? AND mes_ref=?""",
        (status, transacao_id, abs(transacao['valor']), transacao['data'], despesa_id, mes_ref)
    )
    conn.execute(
        "UPDATE transacao SET despesa_id=?, classificacao='recorrente' WHERE id=?",
        (despesa_id, transacao_id)
    )
    # Confirmar é o usuário dizendo "esse texto é essa despesa" — vale tanto
    # quando ele corrigiu quanto quando aceitou a sugestão. É o que faz o
    # batimento do mês que vem já nascer certo.
    registrar_regra_transacao(conn, transacao['descricao'], despesa_id)


@bp.route('/batimento/corrigir', methods=['POST'])
def corrigir_batimento():
    """Corrige um vínculo já confirmado/persistido (ex: revisando um mês
    fechado). O fluxo normal de importação usa /api/batimento/confirmar."""
    data = request.json or {}
    mes_ref = data.get('mes_ref', '')
    transacao_id = data.get('transacao_id')
    despesa_id = data.get('despesa_id')
    if not (mes_ref and transacao_id and despesa_id):
        return jsonify({'ok': False, 'msg': 'mes_ref, transacao_id e despesa_id são obrigatórios'}), 400

    conn = get_db()
    transacao = conn.execute('SELECT * FROM transacao WHERE id=?', (transacao_id,)).fetchone()
    despesa = conn.execute('SELECT * FROM despesa WHERE id=?', (despesa_id,)).fetchone()
    if not transacao or not despesa:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Transação ou despesa não encontrada'}), 404

    despesa_errada = conn.execute(
        'SELECT l.*, d.nome as despesa_nome FROM lancamento l JOIN despesa d ON d.id=l.despesa_id WHERE l.transacao_id=?',
        (transacao_id,)
    ).fetchone()

    _persistir_par(conn, mes_ref, despesa_id, transacao_id, transacao)
    conn.commit()
    conn.close()

    _registrar_dicionario(transacao['descricao'], despesa['nome'], despesa_errada['despesa_nome'] if despesa_errada else None)

    return jsonify({'ok': True})


@bp.route('/batimento/resetar', methods=['POST'])
def resetar_mes():
    """Zera o progresso de batimento de um mês — volta todo lançamento pra
    'nao_encontrado' e desvincula as transações do período. Não apaga o
    histórico bruto importado (transacao/importacao): as transações
    voltam a aparecer como 'sobrando' pra bater de novo, sem precisar
    reimportar o extrato. As regras aprendidas (dicionário, remetente de
    email) também não são mexidas — são conhecimento geral, não do mês."""
    data = request.json or {}
    mes_ref = data.get('mes_ref', '')
    if not mes_ref:
        return jsonify({'ok': False, 'msg': 'mes_ref é obrigatório'}), 400

    conn = get_db()
    dia_corte = int(get_config_value(conn, 'dia_recebimento_salario', '27'))
    ini, fim = periodo_competencia(mes_ref, dia_corte)

    conn.execute(
        "UPDATE transacao SET despesa_id=NULL, classificacao='extra' WHERE data BETWEEN ? AND ? AND despesa_id IS NOT NULL",
        (ini, fim)
    )
    conn.execute(
        "UPDATE lancamento SET status='nao_encontrado', transacao_id=NULL, valor_real=NULL, data_pagamento=NULL, linha_digitavel=NULL WHERE mes_ref=?",
        (mes_ref,)
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


def _registrar_dicionario(descricao_transacao: str, despesa_correta: str, despesa_errada: str | None):
    """Acrescenta a correção ao dicionário de despesas em docs/, para que
    futuras sessões (e futuras revisões de regras_match) aproveitem o que
    já foi aprendido sobre como identificar cada despesa no extrato."""
    if not os.path.exists(DICIONARIO_PATH):
        os.makedirs(os.path.dirname(DICIONARIO_PATH), exist_ok=True)
        with open(DICIONARIO_PATH, 'w', encoding='utf-8') as f:
            f.write(
                '# Dicionário de Despesas\n\n'
                'Registro de correções feitas pelo usuário quando o batimento automático\n'
                'associa uma transação do extrato à despesa errada. Serve como referência\n'
                'para revisar `regras_match` (palavras-chave) do catálogo e evitar repetir\n'
                'o mesmo erro.\n\n'
                '| Descrição no extrato | Despesa correta | Erro anterior |\n'
                '|---|---|---|\n'
            )

    with open(DICIONARIO_PATH, encoding='utf-8') as f:
        linhas = f.readlines()

    if f'`{descricao_transacao}`' in ''.join(linhas):
        return

    obs = despesa_errada or '—'
    nova_linha = f'| `{descricao_transacao}` | {despesa_correta} | {obs} |\n'

    # insere logo após a última linha da tabela (bloco contíguo iniciado em '|'),
    # não no fim do arquivo — assim não fica depois de seções como "## Notas"
    idx_insercao = len(linhas)
    for i, linha in enumerate(linhas):
        if linha.startswith('|'):
            idx_insercao = i + 1

    linhas.insert(idx_insercao, nova_linha)
    with open(DICIONARIO_PATH, 'w', encoding='utf-8') as f:
        f.writelines(linhas)


@bp.route('/transacoes')
def list_transacoes():
    mes_ref = request.args.get('mes', '')
    conn = get_db()
    dia_corte = int(get_config_value(conn, 'dia_recebimento_salario', '27'))
    ini, fim = periodo_competencia(mes_ref, dia_corte)
    rows = conn.execute("""
        SELECT t.*, d.nome as despesa_nome
        FROM transacao t LEFT JOIN despesa d ON d.id = t.despesa_id
        WHERE t.data BETWEEN ? AND ? ORDER BY t.data
    """, (ini, fim)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])
