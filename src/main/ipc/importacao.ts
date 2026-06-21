import { ipcMain, dialog } from 'electron'
import { readFileSync } from 'fs'
import { basename } from 'path'
import { getDb } from '../db/client'
import { parseOfx } from '../parsers/ofx'
import { parseCsv } from '../parsers/csv'

export function registerImportacaoHandlers(): void {
  const db = getDb()

  ipcMain.handle('importacao:escolher-arquivo', async () => {
    const { canceled, filePaths } = await dialog.showOpenDialog({
      title: 'Selecionar extrato bancário',
      filters: [
        { name: 'Extratos', extensions: ['ofx', 'csv', 'txt'] }
      ],
      properties: ['openFile']
    })
    if (canceled || !filePaths.length) return null
    return filePaths[0]
  })

  ipcMain.handle('importacao:processar', async (_e, filePath: string, banco: string) => {
    const ext = filePath.split('.').pop()?.toLowerCase() ?? 'txt'
    const formato = ext === 'ofx' ? 'ofx' : 'csv'
    const content = readFileSync(filePath)

    let transacoes: ParsedTx[] = []
    if (formato === 'ofx') {
      transacoes = await parseOfx(content.toString('latin1'))
    } else {
      transacoes = parseCsv(content.toString('utf-8'), banco)
    }

    if (!transacoes.length) return { ok: false, msg: 'Nenhuma transação encontrada', transacoes: [] }

    const datas = transacoes.map((t) => t.data).sort()
    const info = db
      .prepare('INSERT INTO importacao (banco, formato, arquivo, periodo_ini, periodo_fim) VALUES (?,?,?,?,?)')
      .run(banco || 'Desconhecido', formato, basename(filePath), datas[0], datas[datas.length - 1])

    const importId = info.lastInsertRowid as number

    const insert = db.prepare(
      `INSERT INTO transacao (data, descricao, valor, tipo, situacao, banco_origem, classificacao, import_id)
       VALUES (?, ?, ?, ?, ?, ?, 'extra', ?)`
    )

    const today = new Date().toISOString().slice(0, 10)
    const insertMany = db.transaction(() => {
      for (const t of transacoes) {
        const situacao = t.data > today ? 'agendada' : 'efetivada'
        insert.run(t.data, t.descricao, t.valor, t.valor < 0 ? 'debito' : 'credito', situacao, banco || 'Desconhecido', importId)
      }
    })
    insertMany()

    return { ok: true, msg: `${transacoes.length} transações importadas`, transacoes, importId }
  })

  ipcMain.handle('transacoes:list', (_e, mesRef: string) => {
    const [ano, mes] = mesRef.split('-')
    const ini = `${ano}-${mes}-01`
    const fim = `${ano}-${mes}-31`
    return db
      .prepare(
        `SELECT t.*, d.nome as despesa_nome
         FROM transacao t LEFT JOIN despesa d ON d.id = t.despesa_id
         WHERE t.data BETWEEN ? AND ?
         ORDER BY t.data`
      )
      .all(ini, fim)
  })

  ipcMain.handle('batimento:rodar', (_e, mesRef: string) => {
    const [ano, mes] = mesRef.split('-')
    const ini = `${ano}-${mes}-01`
    const fim = `${ano}-${mes}-31`

    const lancamentos = db
      .prepare(
        `SELECT l.*, d.tipo_valor, d.regras_match, d.dia_vencimento
         FROM lancamento l JOIN despesa d ON d.id = l.despesa_id
         WHERE l.mes_ref = ? AND l.status = 'nao_encontrado'`
      )
      .all(mesRef) as LancRow[]

    const transacoes = db
      .prepare(
        `SELECT * FROM transacao
         WHERE data BETWEEN ? AND ? AND tipo='debito' AND despesa_id IS NULL`
      )
      .all(ini, fim) as TxRow[]

    let matched = 0
    const updateLanc = db.prepare(
      `UPDATE lancamento SET status=?, transacao_id=?, valor_real=?, data_pagamento=? WHERE id=?`
    )
    const updateTx = db.prepare(`UPDATE transacao SET despesa_id=?, classificacao='recorrente' WHERE id=?`)

    const doMatch = db.transaction(() => {
      for (const l of lancamentos) {
        const regras = JSON.parse(l.regras_match) as {
          palavras_chave: string[]
          faixa_valor: [number, number] | null
          janela_dias: number
        }

        let best: { score: number; tx: TxRow } | null = null

        for (const tx of transacoes) {
          if (tx._used) continue

          const txAbs = Math.abs(tx.valor)
          const esperado = l.valor_esperado

          let score = 0

          // Critério valor
          if (l.tipo_valor === 'fixo') {
            if (Math.abs(txAbs - esperado) / esperado <= 0.005) score += 3
          } else {
            if (esperado > 0 && Math.abs(txAbs - esperado) / esperado <= 0.15) score += 2
          }

          // Critério data
          if (l.dia_vencimento) {
            const txDia = parseInt(tx.data.split('-')[2])
            const diff = Math.abs(txDia - l.dia_vencimento)
            const janela = regras.janela_dias ?? 5
            if (diff <= janela) score += 2
          }

          // Critério texto
          const desc = normalizeText(tx.descricao)
          const keywords = regras.palavras_chave ?? []
          if (keywords.some((kw) => desc.includes(normalizeText(kw)))) score += 3

          if (score >= 3 && (!best || score > best.score)) {
            best = { score, tx }
          }
        }

        if (best) {
          const status = best.tx.situacao === 'efetivada' ? 'pago' : 'agendado'
          updateLanc.run(status, best.tx.id, Math.abs(best.tx.valor), best.tx.data, l.id)
          updateTx.run(l.despesa_id, best.tx.id)
          best.tx._used = true
          matched++
        }
      }
    })
    doMatch()

    return { ok: true, matched, total: lancamentos.length }
  })
}

function normalizeText(s: string): string {
  return s
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9 ]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

interface ParsedTx { data: string; descricao: string; valor: number }
interface LancRow {
  id: number; despesa_id: number; valor_esperado: number; tipo_valor: string
  regras_match: string; dia_vencimento: number | null
}
interface TxRow {
  id: number; data: string; descricao: string; valor: number
  situacao: string; _used?: boolean
}
