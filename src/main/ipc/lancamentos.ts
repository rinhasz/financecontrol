import { ipcMain } from 'electron'
import { getDb } from '../db/client'

export function registerLancamentosHandlers(): void {
  const db = getDb()

  ipcMain.handle('lancamentos:list', (_e, mesRef: string) => {
    return db
      .prepare(
        `SELECT l.*, d.nome as despesa_nome, d.tipo_valor, d.padrao_variabilidade,
                c.nome as categoria_nome, c.id as categoria_id
         FROM lancamento l
         JOIN despesa d ON d.id = l.despesa_id
         LEFT JOIN categoria c ON c.id = d.categoria_id
         WHERE l.mes_ref = ?
         ORDER BY c.nome, d.nome`
      )
      .all(mesRef)
  })

  ipcMain.handle('lancamentos:update', (_e, id: number, fields: Record<string, unknown>) => {
    const allowed = ['valor_esperado', 'status', 'transacao_id', 'valor_real', 'data_pagamento']
    const toSet = Object.keys(fields).filter((k) => allowed.includes(k))
    if (!toSet.length) return { ok: false }
    const sql = `UPDATE lancamento SET ${toSet.map((k) => `${k} = ?`).join(', ')} WHERE id = ?`
    db.prepare(sql).run(...toSet.map((k) => fields[k]), id)
    return { ok: true }
  })

  ipcMain.handle('lancamentos:abrir-mes', (_e, mesRef: string) => {
    const despesas = db
      .prepare('SELECT * FROM despesa WHERE ativo = 1')
      .all() as { id: number; valor_padrao: number; padrao_variabilidade: string }[]

    const insert = db.prepare(
      `INSERT OR IGNORE INTO lancamento (mes_ref, despesa_id, valor_esperado, status)
       VALUES (?, ?, ?, 'nao_encontrado')`
    )

    const insertMany = db.transaction(() => {
      for (const d of despesas) {
        const valorPrevisto = getValorPrevisto(db, d.id, mesRef, d.padrao_variabilidade, d.valor_padrao)
        insert.run(mesRef, d.id, valorPrevisto)
      }
    })
    insertMany()
    return { ok: true }
  })

  ipcMain.handle('lancamentos:resumo', (_e, mesRef: string) => {
    const rows = db
      .prepare(
        `SELECT status,
                SUM(CASE WHEN status='pago' THEN valor_real ELSE valor_esperado END) as total
         FROM lancamento WHERE mes_ref = ?
         GROUP BY status`
      )
      .all(mesRef) as { status: string; total: number }[]

    const pago = rows.find((r) => r.status === 'pago')?.total ?? 0
    const agendado = rows.find((r) => r.status === 'agendado')?.total ?? 0
    const naoEncontrado = rows.find((r) => r.status === 'nao_encontrado')?.total ?? 0
    const total = pago + agendado + naoEncontrado

    const config = db
      .prepare('SELECT chave, valor FROM config WHERE chave IN (?,?)')
      .all('reserva_desejada', 'saldo_conta') as { chave: string; valor: string }[]

    const reserva = parseFloat(config.find((c) => c.chave === 'reserva_desejada')?.valor ?? '0')
    const saldo = parseFloat(config.find((c) => c.chave === 'saldo_conta')?.valor ?? '0')

    const receitas = (
      db.prepare('SELECT COALESCE(SUM(valor),0) as v FROM receita WHERE mes_ref = ?').get(mesRef) as {
        v: number
      }
    ).v

    const resgate = Math.max(0, total + reserva - saldo - receitas)

    return { pago, agendado, naoEncontrado, total, reserva, saldo, receitas, resgate }
  })
}

function getValorPrevisto(
  db: ReturnType<typeof getDb>,
  despesaId: number,
  mesRef: string,
  padrao: string,
  valorPadrao: number
): number {
  const [ano, mes] = mesRef.split('-').map(Number)

  const ultimo = db
    .prepare(
      `SELECT COALESCE(valor_real, valor_esperado) as v
       FROM lancamento WHERE despesa_id = ? AND mes_ref < ?
       ORDER BY mes_ref DESC LIMIT 1`
    )
    .get(despesaId, mesRef) as { v: number } | undefined

  if (!ultimo) return valorPadrao

  if (padrao === 'fixa') return ultimo.v

  if (padrao === 'variavel_sazonal') {
    const mesStr = String(mes).padStart(2, '0')
    const rows = db
      .prepare(
        `SELECT COALESCE(valor_real, valor_esperado) as v
         FROM lancamento WHERE despesa_id = ? AND mes_ref LIKE ?
         ORDER BY mes_ref DESC LIMIT 3`
      )
      .all(despesaId, `%-${mesStr}`) as { v: number }[]
    if (rows.length) return rows.reduce((s, r) => s + r.v, 0) / rows.length
  }

  if (padrao === 'variavel_nao_sazonal') {
    const rows = db
      .prepare(
        `SELECT COALESCE(valor_real, valor_esperado) as v
         FROM lancamento WHERE despesa_id = ? AND mes_ref < ?
         ORDER BY mes_ref DESC LIMIT 3`
      )
      .all(despesaId, mesRef) as { v: number }[]
    if (rows.length) return rows.reduce((s, r) => s + r.v, 0) / rows.length
  }

  if (padrao === 'anual') {
    const mesStr = String(mes).padStart(2, '0')
    const temNoMes = db
      .prepare(`SELECT COUNT(*) as n FROM lancamento WHERE despesa_id = ? AND mes_ref LIKE ?`)
      .get(despesaId, `%-${mesStr}`) as { n: number }
    if (!temNoMes.n) return 0
  }

  return ultimo.v
}
