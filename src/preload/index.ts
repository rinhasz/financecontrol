import { contextBridge, ipcRenderer } from 'electron'

const api = {
  // Lançamentos
  lancamentos: {
    list: (mesRef: string) => ipcRenderer.invoke('lancamentos:list', mesRef),
    update: (id: number, fields: Record<string, unknown>) =>
      ipcRenderer.invoke('lancamentos:update', id, fields),
    abrirMes: (mesRef: string) => ipcRenderer.invoke('lancamentos:abrir-mes', mesRef),
    resumo: (mesRef: string) => ipcRenderer.invoke('lancamentos:resumo', mesRef)
  },
  // Catálogo
  catalogo: {
    list: () => ipcRenderer.invoke('catalogo:list'),
    upsert: (data: Record<string, unknown>) => ipcRenderer.invoke('catalogo:upsert', data),
    toggleAtivo: (id: number) => ipcRenderer.invoke('catalogo:toggle-ativo', id)
  },
  categorias: {
    list: () => ipcRenderer.invoke('categorias:list')
  },
  config: {
    get: () => ipcRenderer.invoke('config:get'),
    set: (chave: string, valor: string) => ipcRenderer.invoke('config:set', chave, valor)
  },
  receitas: {
    list: (mesRef: string) => ipcRenderer.invoke('receitas:list', mesRef),
    upsert: (data: Record<string, unknown>) => ipcRenderer.invoke('receitas:upsert', data),
    delete: (id: number) => ipcRenderer.invoke('receitas:delete', id)
  },
  // Importação
  importacao: {
    escolherArquivo: () => ipcRenderer.invoke('importacao:escolher-arquivo'),
    processar: (filePath: string, banco: string) =>
      ipcRenderer.invoke('importacao:processar', filePath, banco)
  },
  transacoes: {
    list: (mesRef: string) => ipcRenderer.invoke('transacoes:list', mesRef)
  },
  batimento: {
    rodar: (mesRef: string) => ipcRenderer.invoke('batimento:rodar', mesRef)
  }
}

contextBridge.exposeInMainWorld('api', api)

export type API = typeof api
