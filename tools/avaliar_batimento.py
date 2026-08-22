"""Mede a qualidade do batimento contra um gabarito.

Uso:  python tools/avaliar_batimento.py [mes_ref]

Gabarito = as correções que o próprio usuário já confirmou, gravadas em
docs/07-dicionario-despesas.md (descrição do extrato -> despesa correta).
Não é um gabarito completo (só cobre o que já foi corrigido alguma vez),
mas é real e cresce com o uso — melhor que julgar "no olho".

Roda o batimento sem gravar nada (é preview) e reporta:
  - ACERTO  : casou com a despesa que o gabarito diz ser a certa
  - ERRO    : casou com outra despesa (o pior caso — dá confiança falsa)
  - PERDIDO : a transação estava lá, o gabarito sabe de quem é, e não casou
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DICIONARIO = os.path.join(BASE, 'docs', '07-dicionario-despesas.md')


def carregar_gabarito():
    """Lê o dicionário de correções: descrição do extrato -> despesas possíveis.

    É um conjunto, não um valor único: uma mesma descrição pode pertencer
    legitimamente a várias despesas, separadas só pelo valor (a mesma escola
    cobra mensalidade e material; o mesmo destinatário recebe salário,
    adiantamento e vale transporte). Tratar como valor único fazia o
    avaliador acusar acerto como erro.
    """
    gabarito = {}
    if not os.path.exists(DICIONARIO):
        return gabarito
    with open(DICIONARIO, encoding='utf-8') as f:
        for linha in f:
            m = re.match(r'\|\s*`(.+?)`\s*\|\s*(.+?)\s*\|', linha)
            if m and m.group(2) != 'Despesa correta':
                gabarito.setdefault(m.group(1).strip(), set()).add(m.group(2).strip())
    return gabarito


def avaliar(mes_ref: str):
    import app as appmod
    cli = appmod.app.test_client()
    d = cli.post('/api/batimento', json={'mes_ref': mes_ref}).get_json()

    gabarito = carregar_gabarito()
    faltantes = {x['despesa_nome'] for x in d['nao_encontrados']}

    acertos, erros, perdidos = [], [], []

    # valor previsto de cada despesa no mês, para arbitrar descrição ambígua.
    # Vem do banco porque a resposta do batimento não traz o previsto das
    # despesas que casaram — justamente as que precisamos julgar.
    import sqlite3
    from api.db import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    previsto = {nome: valor for nome, valor in conn.execute(
        'SELECT d.nome, l.valor_esperado FROM lancamento l '
        'JOIN despesa d ON d.id = l.despesa_id WHERE l.mes_ref = ?', (mes_ref,))}
    conn.close()

    for x in d['detalhes']:
        possiveis = gabarito.get(x['descricao_transacao'].strip())
        if not possiveis:
            continue
        if x['despesa_nome'] in possiveis:
            acertos.append(x['despesa_nome'])
            continue
        # Descrição compartilhada: se o valor bate na mosca com o previsto
        # desta despesa, o casamento está certo mesmo não constando no
        # dicionário — o dicionário só registra o que já foi corrigido.
        esp = previsto.get(x['despesa_nome'])
        if esp and abs(x['valor'] - esp) / esp <= 0.01:
            acertos.append(x['despesa_nome'])
        else:
            erros.append((x['descricao_transacao'], x['despesa_nome'], '/'.join(sorted(possiveis))))

    for t in d['transacoes_sobrando']:
        possiveis = gabarito.get(t['descricao'].strip()) or set()
        pendentes = possiveis & faltantes
        if pendentes:
            perdidos.append((t['descricao'], '/'.join(sorted(pendentes))))

    print(f"=== batimento {mes_ref} ===")
    print(f"casou {d['matched']} de {d['total']} lançamentos "
          f"| {len(d['transacoes_sobrando'])} transações sobrando")
    print(f"gabarito conhecido: {len(gabarito)} descrições\n")

    if erros:
        print(f"--- ERRO ({len(erros)}) — casou com a despesa errada ---")
        for desc, sugerido, correto in erros:
            print(f"  {desc[:42]:<44} sugeriu {sugerido:<24} deveria ser {correto}")
        print()

    if perdidos:
        print(f"--- PERDIDO ({len(perdidos)}) — dava pra casar e não casou ---")
        for desc, correto in perdidos:
            print(f"  {desc[:42]:<44} -> {correto}")
        print()

    avaliados = len(acertos) + len(erros) + len(perdidos)
    pct = (len(acertos) / avaliados * 100) if avaliados else 0
    print(f"ACERTO {len(acertos)} | ERRO {len(erros)} | PERDIDO {len(perdidos)}"
          f"  ->  {pct:.0f}% dos casos com gabarito")
    return len(acertos), len(erros), len(perdidos)


if __name__ == '__main__':
    avaliar(sys.argv[1] if len(sys.argv) > 1 else '2026-08')
