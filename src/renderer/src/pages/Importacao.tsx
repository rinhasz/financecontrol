import { useState, useRef } from 'react'
import { api } from '../lib/api'
import { cn } from '../lib/utils'

type Step = 'selecionar' | 'revisar' | 'concluido'

interface ParsedTx { data: string; descricao: string; valor: number }

const BANCOS = ['Itaú', 'Bradesco', 'Nubank', 'BTG', 'XP', 'Outro']

export function Importacao() {
  const [step, setStep] = useState<Step>('selecionar')
  const [banco, setBanco] = useState('Itaú')
  const [file, setFile] = useState<File | null>(null)
  const [transacoes, setTransacoes] = useState<ParsedTx[]>([])
  const [mesRef, setMesRef] = useState(() => {
    const n = new Date()
    return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, '0')}`
  })
  const [resultado, setResultado] = useState<{ matched: number; total: number } | null>(null)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  async function processar() {
    if (!file) return
    setLoading(true)
    setMsg('')
    try {
      const res = await api.importacao.enviar(file, banco, mesRef)
      if (res.ok) {
        setTransacoes(res.transacoes)
        setMsg(res.msg)
        setStep('revisar')
      } else {
        setMsg(res.msg || 'Erro ao processar arquivo')
      }
    } catch (e) {
      setMsg(String(e))
    } finally {
      setLoading(false)
    }
  }

  async function rodarBatimento() {
    setLoading(true)
    try {
      const res = await api.batimento.rodar(mesRef)
      setResultado(res)
      setStep('concluido')
    } finally {
      setLoading(false)
    }
  }

  const STEPS: [Step, string][] = [['selecionar', '1. Selecionar'], ['revisar', '2. Revisar'], ['concluido', '3. Concluído']]
  const stepIdx = STEPS.findIndex(([s]) => s === step)

  return (
    <div className="flex flex-col h-full pt-3">
      <div className="px-6 pb-4 flex-none">
        <h1 className="text-xl font-semibold text-zinc-100">Importar Extrato</h1>
        <p className="text-sm text-zinc-500 mt-0.5">OFX ou CSV — importa e bate automaticamente com os lançamentos do mês</p>
      </div>

      {/* Steps indicator */}
      <div className="px-6 pb-5 flex items-center gap-2 text-xs flex-none">
        {STEPS.map(([s, label], i) => {
          const done = stepIdx > i
          const active = step === s
          return (
            <div key={s} className="flex items-center gap-2">
              <span className={cn('px-2.5 py-1 rounded font-medium',
                active ? 'bg-emerald-600 text-white' : done ? 'text-emerald-400' : 'text-zinc-600')}>
                {label}
              </span>
              {i < STEPS.length - 1 && <span className="text-zinc-700">›</span>}
            </div>
          )
        })}
      </div>

      <div className="px-6 pb-6 flex-1 overflow-auto">

        {/* Step 1 — selecionar */}
        {step === 'selecionar' && (
          <div className="space-y-5 max-w-lg">
            <div>
              <label className="text-sm text-zinc-400 block mb-2">Banco</label>
              <div className="flex flex-wrap gap-2">
                {BANCOS.map(b => (
                  <button key={b} onClick={() => setBanco(b)}
                    className={cn('px-3 py-1.5 rounded-md text-sm border transition-colors',
                      banco === b ? 'bg-emerald-600/10 border-emerald-600 text-emerald-400' : 'border-zinc-700 text-zinc-400 hover:border-zinc-500')}>
                    {b}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="text-sm text-zinc-400 block mb-2">Mês de referência</label>
              <input type="month" value={mesRef} onChange={e => setMesRef(e.target.value)}
                className="bg-zinc-800 border border-zinc-700 rounded-md px-3 py-2 text-sm text-zinc-200 outline-none focus:border-emerald-500" />
            </div>

            <div>
              <label className="text-sm text-zinc-400 block mb-2">Arquivo (.ofx ou .csv)</label>
              <input ref={fileRef} type="file" accept=".ofx,.csv,.txt" className="hidden"
                onChange={e => setFile(e.target.files?.[0] ?? null)} />
              <div className="flex items-center gap-3">
                <button onClick={() => fileRef.current?.click()}
                  className="px-4 py-2 rounded-md border border-zinc-700 text-sm text-zinc-300 hover:border-zinc-500 hover:text-zinc-100 transition-colors">
                  Selecionar arquivo
                </button>
                {file && <span className="text-sm text-zinc-500 truncate max-w-xs">{file.name}</span>}
              </div>
            </div>

            {msg && <p className="text-sm text-red-400">{msg}</p>}

            <button onClick={processar} disabled={!file || loading}
              className="px-5 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
              {loading ? 'Processando...' : 'Importar'}
            </button>
          </div>
        )}

        {/* Step 2 — revisar */}
        {step === 'revisar' && (
          <div>
            <div className="flex items-center justify-between mb-3">
              <p className="text-sm text-zinc-400">{msg}</p>
              <button onClick={rodarBatimento} disabled={loading}
                className="px-4 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
                {loading ? 'Batendo...' : 'Rodar Batimento Automático'}
              </button>
            </div>
            <div className="rounded-lg overflow-hidden border border-zinc-800/60 max-h-[500px] overflow-y-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-zinc-800 bg-zinc-900/40 sticky top-0">
                    <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Data</th>
                    <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Descrição</th>
                    <th className="px-4 py-2 text-right text-xs text-zinc-500 font-medium">Valor</th>
                  </tr>
                </thead>
                <tbody>
                  {transacoes.map((t, i) => (
                    <tr key={i} className={cn('hover:bg-zinc-800/40', i > 0 && 'border-t border-zinc-800/40')}>
                      <td className="px-4 py-2 text-zinc-500 text-xs tabular-nums">
                        {new Date(t.data + 'T00:00:00').toLocaleDateString('pt-BR')}
                      </td>
                      <td className="px-4 py-2 text-zinc-300">{t.descricao}</td>
                      <td className={cn('px-4 py-2 text-right tabular-nums font-medium', t.valor < 0 ? 'text-red-400' : 'text-emerald-400')}>
                        {new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(t.valor)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Step 3 — concluído */}
        {step === 'concluido' && resultado && (
          <div className="space-y-4 max-w-md">
            <div className="rounded-lg border border-emerald-800/40 bg-emerald-950/20 p-8 text-center">
              <div className="text-5xl font-bold text-emerald-400 mb-2">{resultado.matched}/{resultado.total}</div>
              <p className="text-zinc-400 text-sm">lançamentos casados automaticamente</p>
            </div>
            <p className="text-sm text-zinc-500">
              Vá para <strong className="text-zinc-300">Mês Atual</strong> para revisar os lançamentos em aberto.
            </p>
            <button onClick={() => { setStep('selecionar'); setFile(null); setMsg(''); setTransacoes([]) }}
              className="px-4 py-2 rounded-md border border-zinc-700 text-sm text-zinc-300 hover:border-zinc-500 transition-colors">
              Nova importação
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
