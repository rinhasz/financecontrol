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
    toggleAtivo: (id: number) => post(`/api/catalogo/${id}/toggle`),
    importarAmostra: (fonte: { file: File } | { texto: string }) => {
      const fd = new FormData()
      if ('file' in fonte) fd.append('file', fonte.file)
      else fd.append('texto', fonte.texto)
      return fetch('/api/catalogo/importar/amostra', { method: 'POST', body: fd }).then(r => r.json())
    },
    importarAnalisar: (fonte: { file: File } | { texto: string }, colNome: number, colCategoria: number | null, colValor: number | null, temCabecalho: boolean) => {
      const fd = new FormData()
      if ('file' in fonte) fd.append('file', fonte.file)
      else fd.append('texto', fonte.texto)
      fd.append('col_nome', String(colNome))
      if (colCategoria !== null) fd.append('col_categoria', String(colCategoria))
      if (colValor !== null) fd.append('col_valor', String(colValor))
      fd.append('tem_cabecalho', String(temCabecalho))
      return fetch('/api/catalogo/importar/analisar', { method: 'POST', body: fd }).then(r => r.json())
    },
    importarConfirmar: (plano: unknown) => post('/api/catalogo/importar/confirmar', plano)
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
      post('/api/batimento/corrigir', { mes_ref: mesRef, transacao_id: transacaoId, despesa_id: despesaId }),
    resetar: (mesRef: string) => post('/api/batimento/resetar', { mes_ref: mesRef })
  },
  transacoes: {
    list: (mes: string) => get(`/api/transacoes?mes=${mes}`)
  },
  email: {
    status: () => get('/api/email/status'),
    conectarIniciar: () => post('/api/email/conectar/iniciar'),
    conectarFinalizar: () => post('/api/email/conectar/finalizar'),
    buscarIniciar: (dataIni: string, dataFim: string) => post('/api/email/buscar/iniciar', { data_ini: dataIni, data_fim: dataFim }),
    buscarStatus: () => get('/api/email/buscar/status'),
    buscarCancelar: () => post('/api/email/buscar/cancelar'),
    associar: (despesaId: number, mesRef: string, linhaDigitavel: string | null, valor: string | null, remetente: string) =>
      post('/api/email/associar', { despesa_id: despesaId, mes_ref: mesRef, linha_digitavel: linhaDigitavel, valor, remetente })
  }
}
