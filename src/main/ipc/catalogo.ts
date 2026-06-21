import { ipcMain } from 'electron'
import { getDb } from '../db/client'

export function registerCatalogoHandlers(): void {
  const db = getDb()

  ipcMain.handle('catalogo:list', () => {
    return db
      .prepare(
        `SELECT d.*, c.nome as categoria_nome
         FROM despesa d LEFT JOIN categoria c ON c.id = d.categoria_id
         ORDER BY c.nome, d.nome`
      )
      .all()
  })

  ipcMain.handle('catalogo:upsert', (_e, data: Record<string, unknown>) => {
    if (data.id) {
      db.prepare(
        `UPDATE despesa SET nome=?, categoria_id=?, dia_vencimento=?, tipo_valor=?,
         padrao_variabilidade=?, valor_padrao=?, regras_match=?, ativo=? WHERE id=?`
      ).run(
        data.nome, data.categoria_id, data.dia_vencimento, data.tipo_valor,
        data.padrao_variabilidade, data.valor_padrao, data.regras_match, data.ativo, data.id
      )
      return { id: data.id }
    }
    const info = db.prepare(
      `INSERT INTO despesa (nome, categoria_id, dia_vencimento, tipo_valor, padrao_variabilidade, valor_padrao, regras_match)
       VALUES (?, ?, ?, ?, ?, ?, ?)`
    ).run(
      data.nome, data.categoria_id, data.dia_vencimento, data.tipo_valor,
      data.padrao_variabilidade, data.valor_padrao,
      data.regras_match ?? JSON.stringify({ palavras_chave: [], faixa_valor: null, janela_dias: 5, banco: null })
    )
    return { id: info.lastInsertRowid }
  })

  ipcMain.handle('catalogo:toggle-ativo', (_e, id: number) => {
    db.prepare('UPDATE despesa SET ativo = 1 - ativo WHERE id = ?').run(id)
    return { ok: true }
  })

  ipcMain.handle('categorias:list', () => {
    return db.prepare('SELECT * FROM categoria ORDER BY nome').all()
  })

  ipcMain.handle('config:get', () => {
    return db.prepare('SELECT chave, valor FROM config').all()
  })

  ipcMain.handle('config:set', (_e, chave: string, valor: string) => {
    db.prepare('INSERT OR REPLACE INTO config (chave, valor) VALUES (?, ?)').run(chave, valor)
    return { ok: true }
  })

  ipcMain.handle('receitas:list', (_e, mesRef: string) => {
    return db.prepare('SELECT * FROM receita WHERE mes_ref = ? ORDER BY tipo').all(mesRef)
  })

  ipcMain.handle('receitas:upsert', (_e, data: Record<string, unknown>) => {
    if (data.id) {
      db.prepare('UPDATE receita SET tipo=?, valor=?, origem=? WHERE id=?')
        .run(data.tipo, data.valor, data.origem, data.id)
      return { id: data.id }
    }
    const info = db.prepare('INSERT INTO receita (mes_ref, tipo, valor, origem) VALUES (?,?,?,?)')
      .run(data.mes_ref, data.tipo, data.valor, data.origem)
    return { id: info.lastInsertRowid }
  })

  ipcMain.handle('receitas:delete', (_e, id: number) => {
    db.prepare('DELETE FROM receita WHERE id = ?').run(id)
    return { ok: true }
  })
}
