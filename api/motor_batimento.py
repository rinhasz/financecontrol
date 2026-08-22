"""Motor de casamento entre lançamentos previstos e transações do extrato.

Genérico por **natureza**: despesa casa com débito, receita casa com crédito, e
tudo o mais — scoring, resolução de conflito, regras aprendidas — é idêntico.
Antes de existir este módulo o motor era específico de despesa; duplicá-lo para
o lado da receita significaria manter dois scorings em sincronia para sempre.

O scoring e o porquê de cada peso estão no doc 10. O lado da receita, no doc 14.

Este módulo não conhece Flask nem devolve JSON: recebe conexão e devolve
estruturas Python, para poder ser testado direto.
"""
import json as _json

from .db import padrao_descricao

# Tudo que difere entre as duas naturezas, num lugar só. O resto do arquivo é
# escrito contra estas chaves — é o que garante que o comportamento não possa
# divergir entre entrada e saída sem alguém decidir que divirja.
NATUREZAS = {
    'despesa': {
        'catalogo': 'despesa',
        'lancamento': 'lancamento',
        'fk': 'despesa_id',
        'dia': 'dia_vencimento',
        'data_mov': 'data_pagamento',
        'tipo_tx': 'debito',
        'regra': 'transacao_despesa_regra',
        'status_ok': 'pago',          # transação já efetivada
        'status_pendente': 'agendado',  # ainda agendada
    },
    'receita': {
        'catalogo': 'receita',
        'lancamento': 'lancamento_receita',
        'fk': 'receita_id',
        'dia': 'dia_recebimento',
        'data_mov': 'data_recebimento',
        'tipo_tx': 'credito',
        'regra': 'transacao_receita_regra',
        'status_ok': 'recebido',
        'status_pendente': 'previsto',
    },
}


def cfg(natureza: str) -> dict:
    if natureza not in NATUREZAS:
        raise ValueError(f'natureza inválida: {natureza}')
    return NATUREZAS[natureza]


def status_de(natureza: str, situacao_tx: str) -> str:
    c = cfg(natureza)
    return c['status_ok'] if situacao_tx == 'efetivada' else c['status_pendente']


def garantir_lancamentos(conn, natureza: str, mes_ref: str) -> None:
    """Abre o mês: cria o lançamento de cada item ativo e **fixo** que ainda
    não tem. Esporádica não tem previsão a fazer (doc 14).

    Mesma operação que /api/lancamentos faz ao listar o Mês Atual. Precisa
    acontecer aqui também porque a seção "não encontrei" do batimento sai dos
    lançamentos do mês — sem isso, quem importa o extrato antes de abrir o Mês
    Atual veria uma lista incompleta.
    """
    from .lancamentos import _valor_previsto
    c = cfg(natureza)
    itens = conn.execute(
        f"SELECT * FROM {c['catalogo']} WHERE ativo=1 AND recorrencia='fixa'"
    ).fetchall()
    for d in itens:
        prev = _valor_previsto(conn, d['id'], mes_ref, d['padrao_variabilidade'],
                               d['valor_padrao'], natureza)
        conn.execute(
            f"INSERT OR IGNORE INTO {c['lancamento']} (mes_ref, {c['fk']}, valor_esperado, status) "
            "VALUES (?,?,?,'nao_encontrado')",
            (mes_ref, d['id'], prev)
        )


def carregar_lancamentos(conn, natureza: str, mes_ref: str) -> list:
    """Lançamentos em aberto, normalizados para a forma que o scoring usa.

    `ativo=1`: item desativado não pode disputar transação com um ativo — é
    assim que uma despesa velha e genérica ("cartao xp") roubava o casamento de
    outra ainda em uso. `recorrencia='fixa'` porque esporádica nem lançamento
    tem; o filtro fica explícito para o caso de sobrar lançamento anterior à
    migração.
    """
    c = cfg(natureza)
    rows = conn.execute(f"""
        SELECT l.id, l.{c['fk']} as item_id, l.valor_esperado,
               d.nome as item_nome, d.tipo_valor, d.regras_match, d.{c['dia']} as dia
        FROM {c['lancamento']} l JOIN {c['catalogo']} d ON d.id = l.{c['fk']}
        WHERE l.mes_ref=? AND l.status='nao_encontrado'
          AND d.ativo=1 AND d.recorrencia='fixa'
    """, (mes_ref,)).fetchall()
    return [dict(r) for r in rows]


def carregar_transacoes(conn, natureza: str, ini: str, fim: str) -> list:
    """Transações candidatas: do sinal certo, no período, ainda sem dono.

    Fica de fora a transação **anulada por um estorno**: se um débito foi
    estornado, ele não aconteceu, e oferecê-lo para casar com uma despesa faria
    a despesa parecer paga por um dinheiro que voltou (doc 14 §5).
    """
    c = cfg(natureza)
    return conn.execute(
        f"""SELECT * FROM transacao t
            WHERE t.data BETWEEN ? AND ? AND t.tipo=? AND t.{c['fk']} IS NULL
              AND NOT EXISTS (SELECT 1 FROM transacao e WHERE e.estorna_transacao_id = t.id)""",
        (ini, fim, c['tipo_tx'])
    ).fetchall()


def carregar_regras(conn, natureza: str) -> dict:
    """padrão de descrição -> conjunto de itens que o usuário já disse serem
    donos daquele texto."""
    c = cfg(natureza)
    regras = {}
    for r in conn.execute(f"SELECT padrao, {c['fk']} as item_id FROM {c['regra']}"):
        regras.setdefault(r['padrao'], set()).add(r['item_id'])
    return regras


def parear(lancamentos: list, transacoes: list, regras_aprendidas: dict) -> list:
    """Devolve [(score, lancamento, transacao)] com score >= 3.

    Monta **todos** os pares antes de decidir qualquer casamento: assim um item
    processado antes não "rouba" a transação de outro com palavra-chave
    parecida (era o caso de "cartao xp" vs "cartao black").
    """
    from .importacao import normalize_text, tem_palavra

    padrao_por_tx = {t['id']: padrao_descricao(t['descricao']) for t in transacoes}

    # Transações cujo valor encaixa quase exato no previsto de algum item. Só
    # nessas o valor tem força para conter uma regra aprendida (abaixo): se
    # ninguém encaixa no valor, o previsto provavelmente é que está
    # desatualizado, e aí a regra continua sendo a melhor evidência.
    tx_com_dono_por_valor = set()
    for t in transacoes:
        tx_abs = abs(t['valor'])
        for l in lancamentos:
            esp = l['valor_esperado']
            if esp > 0 and abs(tx_abs - esp) / esp <= 0.01:
                tx_com_dono_por_valor.add(t['id'])
                break

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

            if l['dia']:
                tx_dia = int(t['data'].split('-')[2])
                if abs(tx_dia - l['dia']) <= janela:
                    score += 2

            desc_norm = normalize_text(t['descricao'])
            if keywords:
                matched_kw = sum(1 for kw in keywords if tem_palavra(desc_norm, kw))
                if matched_kw == len(keywords):
                    # Quanto mais palavras-chave o item exige (mais específico),
                    # mais peso o casamento completo ganha — um item com uma
                    # única palavra genérica (ex: só "cartao") não pode vencer
                    # sozinho, sem corroboração de valor ou data.
                    score += 2 * len(keywords)
                elif matched_kw > 0:
                    score += 1

            # O que o usuário já confirmou vale mais que qualquer heurística:
            # ele viu o extrato e disse de quem era. E o inverso também é
            # informação — se este texto pertence a outro item, casá-lo aqui
            # provavelmente repete um erro já corrigido antes.
            # Vários pagamentos ao mesmo destinatário dividem a descrição
            # (salário, adiantamento e vale transporte da mesma pessoa) e só o
            # valor os separa. Quando o valor desta transação encaixa exato em
            # OUTRO item e destoa muito deste, a regra aprendida não pode
            # atropelar — mas a condição é essa, não só o valor destoar: com
            # previsto desatualizado (ou placeholder), a regra ainda é a melhor
            # evidência que existe.
            valor_contradiz = (esperado > 0
                               and abs(tx_abs - esperado) / esperado > 0.5
                               and t['id'] in tx_com_dono_por_valor)

            donos = regras_aprendidas.get(padrao_por_tx.get(t['id']))
            if donos:
                if l['item_id'] in donos:
                    if not valor_contradiz:
                        score += 8
                elif not bateu_valor:
                    # penalidade, não desqualificação: uma mesma descrição pode
                    # servir a dois itens (mensalidade e material da mesma
                    # escola, separados só pelo valor) e o segundo ainda precisa
                    # conseguir casar antes de ser aprendido. Por isso valor
                    # batendo na mosca isenta da penalidade.
                    score -= 4

            if score >= 3:
                candidatos.append((score, l, t))

    # Casamentos com maior confiança são resolvidos primeiro
    candidatos.sort(key=lambda c: c[0], reverse=True)
    return candidatos


def resolver(candidatos: list, natureza: str) -> tuple:
    """Trava os pares em ordem de score. Devolve (detalhes, ids usados)."""
    lanc_usado, tx_usada, detalhes = set(), set(), []
    for _score, l, t in candidatos:
        if l['id'] in lanc_usado or t['id'] in tx_usada:
            continue
        lanc_usado.add(l['id'])
        tx_usada.add(t['id'])
        detalhes.append({
            'natureza': natureza,
            'lancamento_id': l['id'],
            'item_id': l['item_id'],
            'item_id_sugerido': l['item_id'],
            'item_nome': l['item_nome'],
            # previsto vai junto porque a tela precisa devolver o item para a
            # seção "não encontrei" se o usuário trocar este casamento por outro
            'valor_esperado': l['valor_esperado'],
            'transacao_id': t['id'],
            'descricao_transacao': t['descricao'],
            'valor': abs(t['valor']),
            'data': t['data'],
            'status': status_de(natureza, t['situacao']),
        })
    return detalhes, lanc_usado, tx_usada
