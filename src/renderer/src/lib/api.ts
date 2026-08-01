const BASE = ''  // same origin (Flask serves both frontend and API)

async function get(path: string) {
  const r = await fetch(BASE + path)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

async function post(path: string, body?: unknown) {
  const r = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

async function patch(path: string, body: unknown) {
  const r = await fetch(BASE + path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

async function del(path: string) {
  const r = await fetch(BASE + path, { method: 'DELETE' })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export const api = {
  lancamentos: {
    list: (mes: string) => get(`/api/lancamentos?mes=${mes}`),
    update: (id: number, fields: Record<string, unknown>) => patch(`/api/lancamentos/${id}`, fields),
    resumo: (mes: string) => get(`/api/resumo?mes=${mes}`)
  },
  config: {
    get: () => get('/api/config'),
    set: (data: Record<string, unknown>) => post('/api/config', data)
  },
  receitas: {
    list: (mes: string) => get(`/api/receitas?mes=${mes}`),
    upsert: (data: Record<string, unknown>) => post('/api/receitas', data),
    delete: (id: number) => del(`/api/receitas/${id}`)
  },
  catalogo: {
    list: () => get('/api/catalogo'),
    upsert: (data: Record<string, unknown>) => post('/api/catalogo', data),
    toggleAtivo: (id: number) => post(`/api/catalogo/${id}/toggle`)
  },
  categorias: {
    list: () => get('/api/categorias')
  },
  importacao: {
    enviar: (file: File, banco: string, mesRef: string) => {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('banco', banco)
      fd.append('mes_ref', mesRef)
      return fetch('/api/importacao', { method: 'POST', body: fd }).then(r => r.json())
    }
  },
  batimento: {
    rodar: (mesRef: string) => post('/api/batimento', { mes_ref: mesRef }),
    confirmar: (mesRef: string, pares: { transacao_id: number; despesa_id: number; despesa_id_sugerido: number | null }[]) =>
      post('/api/batimento/confirmar', { mes_ref: mesRef, pares }),
    corrigir: (mesRef: string, transacaoId: number, despesaId: number) =>
      post('/api/batimento/corrigir', { mes_ref: mesRef, transacao_id: transacaoId, despesa_id: despesaId })
  },
  transacoes: {
    list: (mes: string) => get(`/api/transacoes?mes=${mes}`)
  },
  email: {
    status: () => get('/api/email/status'),
    conectarIniciar: () => post('/api/email/conectar/iniciar'),
    conectarFinalizar: () => post('/api/email/conectar/finalizar'),
    buscar: (mesRef: string) => post('/api/email/buscar', { mes_ref: mesRef })
  }
}
