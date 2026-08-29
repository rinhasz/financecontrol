const BASE = ''  // same origin (Flask serves both frontend and API)

// Sem timeout, uma chamada que nunca responde deixa a tela presa pra
// sempre no estado "carregando" — foi o que aconteceu quando a rede
// travava o backend por 20s+ e a tela de email ficava em branco. Com
// timeout a chamada falha e a tela consegue mostrar o erro e oferecer
// "tentar de novo". Exceção: conectar/finalizar espera o login no
// navegador e por isso passa timeoutMs: 0 (sem limite).
const DEFAULT_TIMEOUT_MS = 20_000

async function comTimeout(url: string, init: RequestInit, timeoutMs: number) {
  if (!timeoutMs) return fetch(url, init)
  const ctrl = new AbortController()
  const t = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    return await fetch(url, { ...init, signal: ctrl.signal })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new Error(`A resposta demorou mais de ${Math.round(timeoutMs / 1000)}s — o app pode estar sem conexão.`)
    }
    throw e
  } finally {
    clearTimeout(t)
  }
}

async function get(path: string, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const r = await comTimeout(BASE + path, {}, timeoutMs)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

async function post(path: string, body?: unknown, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const r = await comTimeout(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined
  }, timeoutMs)
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

export const api = {
  lancamentos: {
    list: (mes: string) => get(`/api/lancamentos?mes=${mes}`),
    update: (id: number, fields: Record<string, unknown>) => patch(`/api/lancamentos/${id}`, fields),
    resumo: (mes: string) => get(`/api/resumo?mes=${mes}`),
    consolidado: (mes: string) => get(`/api/consolidado?mes=${mes}`)
  },
  config: {
    get: () => get('/api/config'),
    set: (data: Record<string, unknown>) => post('/api/config', data)
  },
  receitas: {
    list: () => get('/api/receitas/catalogo'),
    tipos: () => get('/api/receitas/tipos'),
    upsert: (data: Record<string, unknown>) => post('/api/receitas/catalogo', data),
    toggleAtivo: (id: number) => post(`/api/receitas/catalogo/${id}/toggle`)
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
  investimentos: {
    listar: (data?: string) => get('/api/investimentos' + (data ? `?data=${data}` : '')),
    datas: () => get('/api/investimentos/datas'),
    // a valorização vai à rede buscar CDI, IPCA e cotações: sem timeout curto
    atualizar: () => post('/api/investimentos/atualizar', undefined, 120_000),
    memoria: (id: number) => get(`/api/investimentos/${id}/memoria`),
    importar: (file: File) => {
      const fd = new FormData()
      fd.append('file', file)
      return fetch('/api/investimentos/importar', { method: 'POST', body: fd }).then(r => r.json())
    }
  },
  categorias: {
    list: () => get('/api/categorias')
  },
  importacao: {
    // sem mês: a competência de cada lançamento sai da data dele, no servidor,
    // e a resposta traz em `meses` quais o extrato cobriu
    enviar: (file: File, banco: string) => {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('banco', banco)
      return fetch('/api/importacao', { method: 'POST', body: fd }).then(r => r.json())
    }
  },
  batimento: {
    // mesRef vazio deixa o servidor usar a competência de hoje pela regra do corte
    rodar: (mesRef: string) => post('/api/batimento', { mes_ref: mesRef }),
    confirmar: (mesRef: string, pares: {
      natureza: 'despesa' | 'receita'; transacao_id: number
      item_id: number; item_id_sugerido: number | null
    }[]) => post('/api/batimento/confirmar', { mes_ref: mesRef, pares }),
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
    // espera você confirmar o login no navegador — pode levar minutos
    conectarFinalizar: () => post('/api/email/conectar/finalizar', undefined, 0),
    buscarIniciar: (dataIni: string, dataFim: string) => post('/api/email/buscar/iniciar', { data_ini: dataIni, data_fim: dataFim }),
    buscarStatus: () => get('/api/email/buscar/status'),
    buscarCancelar: () => post('/api/email/buscar/cancelar'),
    associarLote: (itens: {
      despesa_id: number; mes_ref: string; linha_digitavel: string | null; tipo_codigo: string | null
      valor: string | null; remetente: string
    }[]) => post('/api/email/associar/lote', { itens })
  }
}
