import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { cn } from '../lib/utils'

interface Despesa {
  id: number; nome: string; categoria_nome: string
  dia_vencimento: number | null; tipo_valor: string
  padrao_variabilidade: string; valor_padrao: number; ativo: number
}

const PADRAO_LABEL: Record<string, string> = {
  fixa: 'Fixa', variavel_sazonal: 'Sazonal', variavel_nao_sazonal: 'Variável',
  reajuste_anual: 'Reajuste anual', anual: 'Anual', sem_dados: 'Sem dados'
}

export function Catalogo() {
  const [despesas, setDespesas] = useState<Despesa[]>([])
  const [busca, setBusca] = useState('')
  const [apenasAtivas, setApenasAtivas] = useState(true)
  const [loading, setLoading] = useState(true)

  async function load() {
    setLoading(true)
    try { setDespesas(await api.catalogo.list()) }
    finally { setLoading(false) }
  }

  useEffect(() => { load() }, [])

  async function toggle(id: number) {
    await api.catalogo.toggleAtivo(id)
    load()
  }

  const filtradas = despesas.filter(d => {
    if (apenasAtivas && !d.ativo) return false
    if (busca && !d.nome.toLowerCase().includes(busca.toLowerCase())) return false
    return true
  })

  const byCategory = filtradas.reduce<Record<string, Despesa[]>>((acc, d) => {
    const cat = d.categoria_nome || 'Outros'
    if (!acc[cat]) acc[cat] = []
    acc[cat].push(d)
    return acc
  }, {})

  return (
    <div className="flex flex-col h-full pt-3">
      <div className="px-6 pb-4 flex items-center justify-between flex-none">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">Catálogo de Despesas</h1>
          <p className="text-sm text-zinc-500 mt-0.5">{filtradas.length} despesas</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-zinc-400 cursor-pointer">
            <input type="checkbox" checked={apenasAtivas} onChange={e => setApenasAtivas(e.target.checked)} className="rounded" />
            Apenas ativas
          </label>
          <input type="text" placeholder="Buscar..." value={busca} onChange={e => setBusca(e.target.value)}
            className="bg-zinc-800 border border-zinc-700 rounded-md px-3 py-1.5 text-sm text-zinc-200 outline-none focus:border-emerald-500 w-48 placeholder:text-zinc-600" />
        </div>
      </div>

      <div className="flex-1 overflow-auto px-6 pb-6">
        {loading ? (
          <div className="flex items-center justify-center h-40 text-zinc-500">Carregando...</div>
        ) : (
          <div className="space-y-4">
            {Object.entries(byCategory).map(([cat, items]) => (
              <div key={cat}>
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider">{cat}</span>
                  <div className="flex-1 h-px bg-zinc-800" />
                  <span className="text-xs text-zinc-600">{items.length} despesas</span>
                </div>
                <div className="rounded-lg overflow-hidden border border-zinc-800/60">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-zinc-800 bg-zinc-900/40">
                        <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Nome</th>
                        <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Padrão</th>
                        <th className="px-4 py-2 text-right text-xs text-zinc-500 font-medium">Previsão</th>
                        <th className="px-4 py-2 text-center text-xs text-zinc-500 font-medium">Dia Venc.</th>
                        <th className="px-4 py-2 text-center text-xs text-zinc-500 font-medium">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((d, i) => (
                        <tr key={d.id} className={cn('transition-colors hover:bg-zinc-800/40', i > 0 && 'border-t border-zinc-800/40', !d.ativo && 'opacity-40')}>
                          <td className="px-4 py-2.5 text-zinc-300">{d.nome}</td>
                          <td className="px-4 py-2.5">
                            <span className="text-xs text-zinc-500 bg-zinc-800 px-2 py-0.5 rounded">
                              {PADRAO_LABEL[d.padrao_variabilidade] ?? d.padrao_variabilidade}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-right text-zinc-300 tabular-nums">
                            {d.valor_padrao > 0 ? new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(d.valor_padrao) : '—'}
                          </td>
                          <td className="px-4 py-2.5 text-center text-zinc-500 text-xs">{d.dia_vencimento ?? '—'}</td>
                          <td className="px-4 py-2.5 text-center">
                            <button onClick={() => toggle(d.id)}
                              className={cn('text-xs px-2 py-0.5 rounded border transition-colors',
                                d.ativo
                                  ? 'border-emerald-800/50 text-emerald-400 bg-emerald-950/30 hover:bg-emerald-950/60'
                                  : 'border-zinc-700 text-zinc-500 hover:bg-zinc-800')}>
                              {d.ativo ? 'Ativa' : 'Inativa'}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
