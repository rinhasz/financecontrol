import re
import csv as csvlib
import io
import os
from datetime import date
from flask import Blueprint, jsonify, request
from .db import (get_db, get_config_value, periodo_competencia,
                 padrao_descricao, registrar_regra)
from . import motor_batimento as motor

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

    ini, fim = datas[0], datas[-1]

    # SUBSTITUIÇÃO DO PERÍODO — o extrato é a verdade sobre o intervalo que ele
    # cobre; o banco não pode guardar nada além dele.
    #
    # A dedupe incremental de antes só sabia acrescentar, nunca remover, e o
    # lixo se acumulava para sempre: um PIX agendado para 12/08 que foi
    # cancelado continuava aparecendo como lançamento disponível para associar,
    # semanas depois de o extrato ter deixado de mencioná-lo. Também era ela
    # que criava a versão órfã quando o banco mudava a descrição ao debitar.
    #
    # Apagar e reinserir o intervalo resolve as duas coisas de uma vez. O
    # recorte é por banco: sem isso, importar um extrato de outra conta
    # apagaria as transações desta no mesmo período.
    antigas = conn.execute(
        'SELECT id, data, descricao, valor, despesa_id, classificacao FROM transacao '
        'WHERE data BETWEEN ? AND ? AND banco_origem = ?',
        (ini, fim, banco)
    ).fetchall()

    ids_antigos = [t['id'] for t in antigas]
    lanc_por_tx = {}
    if ids_antigos:
        ph = ','.join('?' * len(ids_antigos))
        for r in conn.execute(
                f'SELECT id, transacao_id FROM lancamento WHERE transacao_id IN ({ph})', ids_antigos):
            lanc_por_tx.setdefault(r['transacao_id'], []).append(r['id'])

    # Casamentos confirmados são trabalho manual do usuário e têm que atravessar
    # a troca. Por chave exata primeiro; depois por (data, valor), que é o que
    # sobrevive quando o banco reescreve a descrição ao debitar
    # ('PAG TIT 662992535000' -> 'PAG BOLETO EDIFICIO LINCOLN GARDEN').
    vinculo_exato = {}
    vinculo_por_valor = {}
    for t in antigas:
        if not t['despesa_id']:
            continue
        vinculo_exato[(t['data'], t['descricao'], t['valor'])] = t
        vinculo_por_valor.setdefault((t['data'], t['valor']), []).append(t)

    cur = conn.execute(
        'INSERT INTO importacao (banco, formato, arquivo, periodo_ini, periodo_fim) VALUES (?,?,?,?,?)',
        (banco, formato, filename, ini, fim)
    )
    import_id = cur.lastrowid

    if ids_antigos:
        ph = ','.join('?' * len(ids_antigos))
        conn.execute(f'UPDATE lancamento SET transacao_id=NULL WHERE transacao_id IN ({ph})', ids_antigos)
        conn.execute(f'DELETE FROM transacao WHERE id IN ({ph})', ids_antigos)

    novos = []
    for t in txs:
        tipo = 'debito' if t['valor'] < 0 else 'credito'
        c = conn.execute(
            'INSERT INTO transacao (data, descricao, valor, tipo, situacao, banco_origem, import_id) '
            'VALUES (?,?,?,?,?,?,?)',
            (t['data'], t['descricao'], t['valor'], tipo, _situacao(t), banco, import_id)
        )
        novos.append((c.lastrowid, t))

    casados = {}
    sem_par = []
    for novo_id, t in novos:
        antiga = vinculo_exato.pop((t['data'], t['descricao'], t['valor']), None)
        if antiga is not None:
            casados[antiga['id']] = (novo_id, t)
        else:
            sem_par.append((novo_id, t))

    for novo_id, t in sem_par:
        candidatas = [a for a in vinculo_por_valor.get((t['data'], t['valor']), [])
                      if a['id'] not in casados]
        # só quando não há ambiguidade: com duas candidatas não dá para saber
        # qual é qual, e chutar arrastaria o vínculo para a despesa errada
        if len(candidatas) == 1:
            casados[candidatas[0]['id']] = (novo_id, t)

    restaurados = 0
    orfaos = 0
    for antiga in antigas:
        if not antiga['despesa_id']:
            continue
        lancs = lanc_por_tx.get(antiga['id'], [])
        alvo = casados.get(antiga['id'])
        if alvo is None:
            # A transação sumiu do extrato — agendamento cancelado, estorno,
            # correção do banco. O lançamento volta a ficar em aberto em vez de
            # continuar apontando para algo que não existe mais.
            orfaos += 1
            for lid in lancs:
                conn.execute(
                    "UPDATE lancamento SET status='nao_encontrado', transacao_id=NULL, "
                    "valor_real=NULL, data_pagamento=NULL WHERE id=?", (lid,))
            continue
        novo_id, t = alvo
        conn.execute('UPDATE transacao SET despesa_id=?, classificacao=? WHERE id=?',
                     (antiga['despesa_id'], antiga['classificacao'], novo_id))
        status = 'pago' if _situacao(t) == 'efetivada' else 'agendado'
        for lid in lancs:
            conn.execute(
                'UPDATE lancamento SET status=?, transacao_id=?, valor_real=?, data_pagamento=? WHERE id=?',
                (status, novo_id, abs(t['valor']), t['data'], lid))
        restaurados += 1

    from collections import Counter
    antes = Counter((t['data'], t['descricao'], t['valor']) for t in antigas)
    depois = Counter((t['data'], t['descricao'], t['valor']) for _, t in novos)
    removidas = sum((antes - depois).values())

    conn.commit()
    conn.close()

    msg = f'{len(txs)} lançamentos do extrato ({ini} a {fim})'
    if removidas:
        msg += f' — {removidas} que não estão mais no extrato foram removidos'
    if restaurados:
        msg += f', {restaurados} associações preservadas'
    if orfaos:
        msg += f', {orfaos} lançamento(s) voltaram a ficar em aberto'

    return jsonify({'ok': True, 'msg': msg, 'transacoes': txs, 'import_id': import_id})


@bp.route('/batimento', methods=['POST'])
def rodar_batimento():
    """Calcula as sugestões de casamento (preview) — não grava nada no banco.

    Devolve os dois lados: `despesa` (débitos) e `receita` (créditos). O motor é
    o mesmo para ambos (`motor_batimento`); o que muda é de qual tabela vêm os
    lançamentos e qual o sinal da transação.

    Só é persistido quando o usuário confirma via /api/batimento/confirmar,
    para não perder o trabalho de quem ainda não terminou de revisar (rodar
    duas vezes sem confirmar não deve fazer nada desaparecer)."""
    mes_ref = (request.json or {}).get('mes_ref', '')

    conn = get_db()
    dia_corte = int(get_config_value(conn, 'dia_recebimento_salario', '27'))
    ini, fim = periodo_competencia(mes_ref, dia_corte)

    # Abre o mês antes de casar, para a seção "não encontrei" ficar completa
    # mesmo se o usuário ainda não abriu o Mês Atual.
    for natureza in ('despesa', 'receita'):
        motor.garantir_lancamentos(conn, natureza, mes_ref)
    conn.commit()

    resultado = {natureza: _batimento_de(conn, natureza, mes_ref, ini, fim)
                 for natureza in ('despesa', 'receita')}
    conn.close()

    return jsonify({
        'ok': True,
        'periodo': {'ini': ini, 'fim': fim},
        **resultado,
    })


def _batimento_de(conn, natureza: str, mes_ref: str, ini: str, fim: str) -> dict:
    """Um lado do batimento — mesma lógica para despesa e receita."""
    c = motor.cfg(natureza)

    lancamentos = motor.carregar_lancamentos(conn, natureza, mes_ref)
    transacoes = motor.carregar_transacoes(conn, natureza, ini, fim)
    regras = motor.carregar_regras(conn, natureza)

    candidatos = motor.parear(lancamentos, transacoes, regras)
    detalhes, lanc_sugerido, tx_sugerida = motor.resolver(candidatos, natureza)

    # Lançamentos já casados antes, cuja transação mudou de situação desde
    # então — tipicamente estavam "Agendado" e o débito acabou de cair. Sem
    # isto eles nunca reapareceriam na revisão (a busca acima só olha
    # 'nao_encontrado') e ficariam presos no status antigo pra sempre.
    defasados = conn.execute(f"""
        SELECT l.id, l.{c['fk']} as item_id, l.status, l.valor_esperado, d.nome as item_nome,
               t.id as tx_id, t.descricao as tx_descricao, t.valor as tx_valor,
               t.data as tx_data, t.situacao as tx_situacao
        FROM {c['lancamento']} l
        JOIN {c['catalogo']} d ON d.id = l.{c['fk']}
        JOIN transacao t ON t.id = l.transacao_id
        WHERE l.mes_ref = ?
          AND l.status != (CASE WHEN t.situacao = 'efetivada' THEN ? ELSE ? END)
    """, (mes_ref, c['status_ok'], c['status_pendente'])).fetchall()

    # Casamentos já gravados e coerentes (o complemento exato de `defasados`).
    # Sem eles a seção "casadas" mostraria só as sugestões novas, e um vínculo
    # confirmado errado ficaria intocável: o item some da busca por
    # 'nao_encontrado' e a transação some do `<fk> IS NULL`, então o par não
    # apareceria em nenhuma das três seções e não haveria como desfazê-lo sem
    # resetar o mês inteiro.
    ja_gravados = conn.execute(f"""
        SELECT l.id, l.{c['fk']} as item_id, l.valor_esperado, d.nome as item_nome,
               t.id as tx_id, t.descricao as tx_descricao, t.valor as tx_valor,
               t.data as tx_data, t.situacao as tx_situacao
        FROM {c['lancamento']} l
        JOIN {c['catalogo']} d ON d.id = l.{c['fk']}
        JOIN transacao t ON t.id = l.transacao_id
        WHERE l.mes_ref = ?
          AND l.status = (CASE WHEN t.situacao = 'efetivada' THEN ? ELSE ? END)
        ORDER BY t.data
    """, (mes_ref, c['status_ok'], c['status_pendente'])).fetchall()

    for l in defasados:
        detalhes.append(_detalhe_de_lancamento(l, natureza, status_anterior=l['status']))
    for l in ja_gravados:
        detalhes.append(_detalhe_de_lancamento(l, natureza, ja_gravado=True))

    tipos = {}
    if natureza == 'receita':
        # a tela precisa do tipo para saber quando pedir objetivo (resgate
        # esporádico) ou qual débito anular (estorno)
        tipos = {r['id']: r['tipo'] for r in conn.execute('SELECT id, tipo FROM receita')}

    nao_encontrados = [
        {'natureza': natureza, 'lancamento_id': l['id'], 'item_id': l['item_id'],
         'item_nome': l['item_nome'], 'valor_esperado': l['valor_esperado'],
         **({'tipo': tipos.get(l['item_id'])} if natureza == 'receita' else {})}
        for l in lancamentos if l['id'] not in lanc_sugerido
    ]
    sobrando = [{**dict(t), 'natureza': natureza, 'valor': abs(t['valor'])}
                for t in transacoes if t['id'] not in tx_sugerida]

    if natureza == 'receita':
        _sugerir_estornos(conn, sobrando, ini, fim)

    # Itens esporádicos não têm lançamento (doc 14), então não aparecem em
    # `nao_encontrados` — e sem isto a tela não teria como oferecê-los na seção
    # 3, deixando "estorno" e "resgate esporádico" inalcançáveis. Vão à parte
    # justamente porque não são uma cobrança em aberto: não têm previsão.
    esporadicos = [
        {'natureza': natureza, 'item_id': r['id'], 'item_nome': r['nome'],
         **({'tipo': r['tipo']} if natureza == 'receita' else {})}
        for r in conn.execute(
            f"SELECT * FROM {c['catalogo']} WHERE ativo=1 AND recorrencia='esporadica' ORDER BY nome")
    ]

    return {
        # total é derivado das próprias listas devolvidas — casadas + em aberto.
        # Contar só os lançamentos deixava o placar mentir conforme entravam
        # defasados e já gravados, que não estão naquela busca.
        'matched': len(detalhes), 'total': len(detalhes) + len(nao_encontrados),
        'detalhes': detalhes,
        'nao_encontrados': nao_encontrados,
        'esporadicos': esporadicos,
        'transacoes_sobrando': sobrando,
    }


def _sugerir_estornos(conn, creditos: list, ini: str, fim: str) -> None:
    """Marca cada crédito que **parece** estornar um débito.

    Um estorno chega com valor idêntico ao da cobrança e poucos dias depois.
    Caso real de 03/08: `INT PERS BLACK` -5.709,27, `CREDITO CARTAO ITAU`
    +5.709,27 e `PAG BOLETO ITAU UNIBANCO` -5.709,27 — cobrança estornada e
    refeita como boleto, com o primeiro débito aparecendo como despesa
    disponível para associar, o que já causou confusão real.

    **Sugestão, nunca decisão.** Um reembolso legítimo de valor redondo se
    parece com estorno, e marcar sozinho apagaria um débito real da conferência.
    Quem confirma é o usuário.
    """
    from datetime import date as _date

    debitos = conn.execute("""
        SELECT t.id, t.data, t.descricao, t.valor FROM transacao t
        WHERE t.data BETWEEN ? AND ? AND t.tipo='debito' AND t.despesa_id IS NULL
          AND NOT EXISTS (SELECT 1 FROM transacao e WHERE e.estorna_transacao_id = t.id)
    """, (ini, fim)).fetchall()

    def dias(a, b):
        return abs((_date.fromisoformat(a) - _date.fromisoformat(b)).days)

    usados = set()
    for cr in creditos:
        alvo = None
        for db in debitos:
            if db['id'] in usados or abs(abs(db['valor']) - cr['valor']) > 0.005:
                continue
            if dias(db['data'], cr['data']) > 3:
                continue
            if alvo is None or dias(db['data'], cr['data']) < dias(alvo['data'], cr['data']):
                alvo = db
        if alvo is not None:
            usados.add(alvo['id'])
            cr['estorno_sugerido'] = {
                'transacao_id': alvo['id'], 'descricao': alvo['descricao'],
                'data': alvo['data'], 'valor': abs(alvo['valor']),
            }


def _detalhe_de_lancamento(l, natureza: str, status_anterior=None, ja_gravado=False) -> dict:
    d = {
        'natureza': natureza,
        'lancamento_id': l['id'],
        'item_id': l['item_id'],
        'item_id_sugerido': l['item_id'],
        'item_nome': l['item_nome'],
        'valor_esperado': l['valor_esperado'],
        'transacao_id': l['tx_id'],
        'descricao_transacao': l['tx_descricao'],
        'valor': abs(l['tx_valor']),
        'data': l['tx_data'],
        'status': motor.status_de(natureza, l['tx_situacao']),
    }
    if status_anterior is not None:
        d['status_anterior'] = status_anterior
    if ja_gravado:
        d['ja_gravado'] = True
    return d


@bp.route('/batimento/confirmar', methods=['POST'])
def confirmar_batimento():
    """Grava de uma vez só os pares (transação, item) que o usuário revisou na
    tela — sugestões automáticas aceitas + correções + associações manuais.
    Nada do preview em /api/batimento é persistido antes disso."""
    data = request.json or {}
    mes_ref = data.get('mes_ref', '')
    pares = data.get('pares', [])
    if not mes_ref or not pares:
        return jsonify({'ok': False, 'msg': 'mes_ref e pares são obrigatórios'}), 400

    conn = get_db()
    confirmados = 0
    for par in pares:
        natureza = par.get('natureza', 'despesa')
        transacao_id = par.get('transacao_id')
        item_id = par.get('item_id')
        item_id_sugerido = par.get('item_id_sugerido')
        if not transacao_id or not item_id:
            continue

        c = motor.cfg(natureza)
        transacao = conn.execute('SELECT * FROM transacao WHERE id=?', (transacao_id,)).fetchone()
        item = conn.execute(f"SELECT * FROM {c['catalogo']} WHERE id=?", (item_id,)).fetchone()
        if not transacao or not item:
            continue

        _persistir_par(conn, mes_ref, item_id, transacao_id, transacao, natureza)
        confirmados += 1

        # Detalhes que existem por ocorrência, não no catálogo: o objetivo muda
        # a cada resgate esporádico e o débito anulado muda a cada estorno.
        # Gravados aqui, junto do par, para "Confirmar tudo" continuar sendo a
        # única escrita do fluxo.
        if par.get('objetivo') is not None:
            conn.execute('UPDATE transacao SET objetivo=? WHERE id=?',
                         (par['objetivo'] or None, transacao_id))
        if par.get('estorna_transacao_id'):
            conn.execute('UPDATE transacao SET estorna_transacao_id=? WHERE id=?',
                         (par['estorna_transacao_id'], transacao_id))

        if item_id_sugerido and item_id_sugerido != item_id:
            # o usuário rejeitou esta sugestão: desaprende, senão a regra errada
            # continuaria disputando com a certa nos próximos meses. (não basta o
            # desaprender de _persistir_par: ali só cai o vínculo já gravado, e
            # uma sugestão recusada nunca chegou a ser gravada)
            conn.execute(
                f"DELETE FROM {c['regra']} WHERE padrao=? AND {c['fk']}=?",
                (padrao_descricao(transacao['descricao']), item_id_sugerido)
            )
            if natureza == 'despesa':
                # o dicionário legível em docs/07 é só do lado da despesa; do
                # lado da receita a tabela de regras já é a fonte de verdade
                row = conn.execute('SELECT nome FROM despesa WHERE id=?', (item_id_sugerido,)).fetchone()
                _registrar_dicionario(transacao['descricao'], item['nome'], row['nome'] if row else None)

    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'confirmados': confirmados})


def _persistir_par(conn, mes_ref: str, item_id: int, transacao_id: int, transacao,
                   natureza: str = 'despesa'):
    """Vincula uma transação a um item do catálogo: garante que o lançamento do
    mês existe, marca o status conforme a situação da transação, e reverte
    qualquer vínculo anterior errado que a transação já tivesse.

    Item esporádico não passa por lançamento (doc 14): a própria transação, com
    a FK preenchida, é o registro do fato."""
    c = motor.cfg(natureza)
    tab, fk, regra = c['lancamento'], c['fk'], c['regra']

    item_errado = conn.execute(
        f'SELECT id, {fk} as item_id FROM {tab} WHERE transacao_id=? AND {fk} != ?',
        (transacao_id, item_id)
    ).fetchone()
    if item_errado:
        conn.execute(
            f"UPDATE {tab} SET status='nao_encontrado', transacao_id=NULL, valor_real=NULL, "
            f"{c['data_mov']}=NULL WHERE id=?",
            (item_errado['id'],)
        )
        # desaprende: sem isto a regra errada continuaria competindo com a certa
        # todo mês, e o usuário corrigiria o mesmo caso para sempre
        conn.execute(
            f'DELETE FROM {regra} WHERE padrao=? AND {fk}=?',
            (padrao_descricao(transacao['descricao']), item_errado['item_id'])
        )
    elif transacao[fk] and transacao[fk] != item_id:
        # a transação estava com outro dono e esse dono não tinha lançamento
        # (caso do item esporádico) — o desaprender acima não pegaria
        conn.execute(
            f'DELETE FROM {regra} WHERE padrao=? AND {fk}=?',
            (padrao_descricao(transacao['descricao']), transacao[fk])
        )

    esporadico = conn.execute(
        f"SELECT recorrencia='esporadica' FROM {c['catalogo']} WHERE id=?", (item_id,)
    ).fetchone()[0]

    if not esporadico:
        conn.execute(
            f"INSERT OR IGNORE INTO {tab} (mes_ref, {fk}, valor_esperado, status) "
            "VALUES (?,?,?,'nao_encontrado')",
            (mes_ref, item_id, abs(transacao['valor']))
        )
        conn.execute(
            f"""UPDATE {tab} SET status=?, transacao_id=?, valor_real=?, {c['data_mov']}=?
                WHERE {fk}=? AND mes_ref=?""",
            (motor.status_de(natureza, transacao['situacao']), transacao_id,
             abs(transacao['valor']), transacao['data'], item_id, mes_ref)
        )

    classificacao = 'receita' if natureza == 'receita' else ('extra' if esporadico else 'recorrente')
    conn.execute(
        f'UPDATE transacao SET {fk}=?, classificacao=? WHERE id=?',
        (item_id, classificacao, transacao_id)
    )
    # Confirmar é o usuário dizendo "esse texto é esse item" — vale tanto quando
    # ele corrigiu quanto quando aceitou a sugestão. É o que faz o batimento do
    # mês que vem já nascer certo.
    registrar_regra(conn, regra, fk, transacao['descricao'], item_id)


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
        "UPDATE transacao SET despesa_id=NULL, receita_id=NULL, classificacao='extra' "
        "WHERE data BETWEEN ? AND ? AND (despesa_id IS NOT NULL OR receita_id IS NOT NULL)",
        (ini, fim)
    )
    conn.execute(
        "UPDATE lancamento SET status='nao_encontrado', transacao_id=NULL, valor_real=NULL, "
        "data_pagamento=NULL, linha_digitavel=NULL WHERE mes_ref=?",
        (mes_ref,)
    )
    conn.execute(
        "UPDATE lancamento_receita SET status='nao_encontrado', transacao_id=NULL, valor_real=NULL, "
        "data_recebimento=NULL WHERE mes_ref=?",
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
