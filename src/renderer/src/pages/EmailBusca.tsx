import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { cn } from '../lib/utils'

interface EmailEncontrado {
  assunto: string
  remetente: string
  data: string | null
  linha_digitavel: string | null
  valor_encontrado: string | null
}

interface ResultadoDespesa {
  despesa_id: number
  despesa_nome: string
  emails: EmailEncontrado[]
}

export function EmailBusca() {
  const [status, setStatus] = useState<{ configurado: boolean; endereco: string | null } | null>(null)
  const [mesRef, setMesRef] = useState(() => {
    const n = new Date()
    return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, '0')}`
  })
  const [buscando, setBuscando] = useState(false)
  const [resultados, setResultados] = useState<ResultadoDespesa[] | null>(null)
  const [pesquisadas, setPesquisadas] = useState(0)
  const [erro, setErro] = useState('')
  const [copiado, setCopiado] = useState<string | null>(null)

  useEffect(() => { api.email.status().then(setStatus) }, [])

  async function buscar() {
    setBuscando(true)
    setErro('')
    setResultados(null)
    try {
      const res = await api.email.buscar(mesRef)
      if (res.ok) {
        setResultados(res.resultados)
        setPesquisadas(res.despesas_pesquisadas)
      } else {
        setErro(res.msg || 'Erro ao buscar emails')
      }
    } catch (e) {
      setErro(String(e))
    } finally {
      setBuscando(false)
    }
  }

  async function copiar(texto: string) {
    await navigator.clipboard.writeText(texto.replace(/\s+/g, ''))
    setCopiado(texto)
    setTimeout(() => setCopiado(null), 1500)
  }

  return (
    <div className="flex flex-col h-full pt-3">
      <div className="px-6 pb-4 flex-none">
        <h1 className="text-xl font-semibold text-zinc-100">Procurar em Emails</h1>
        <p className="text-sm text-zinc-500 mt-0.5">
          Busca faturas/boletos de despesas variáveis já cadastradas no catálogo — só para consulta,
          não cria nem altera nenhum lançamento.
        </p>
      </div>

      <div className="px-6 pb-6 flex-1 overflow-auto space-y-4">
        {status && !status.configurado && (
          <div className="rounded-lg border border-amber-800/40 bg-amber-950/20 p-4 text-sm text-amber-300 max-w-xl">
            Credenciais de email não configuradas. Copie <code className="text-amber-200">.env.example</code> para{' '}
            <code className="text-amber-200">.env</code> e preencha <code className="text-amber-200">EMAIL_ADDRESS</code> e{' '}
            <code className="text-amber-200">EMAIL_APP_PASSWORD</code> (senha de aplicativo, não a senha normal da conta),
            depois reinicie o app.
          </div>
        )}

        <div className="flex items-end gap-3">
          <div>
            <label className="text-sm text-zinc-400 block mb-2">Mês de referência</label>
            <input type="month" value={mesRef} onChange={e => setMesRef(e.target.value)}
              className="bg-zinc-800 border border-zinc-700 rounded-md px-3 py-2 text-sm text-zinc-200 outline-none focus:border-emerald-500" />
          </div>
          <button onClick={buscar} disabled={buscando || !status?.configurado}
            className="px-5 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
            {buscando ? 'Buscando...' : 'Buscar'}
          </button>
        </div>

        {erro && <p className="text-sm text-red-400">{erro}</p>}

        {resultados && (
          <div className="space-y-4">
            <p className="text-sm text-zinc-500">
              {pesquisadas} despesas pesquisadas — {resultados.length} com email encontrado no período.
            </p>

            {resultados.length === 0 && (
              <p className="text-sm text-zinc-600">Nenhum email bateu com as despesas variáveis do catálogo nesse período.</p>
            )}

            {resultados.map(r => (
              <div key={r.despesa_id} className="rounded-lg overflow-hidden border border-zinc-800/60">
                <div className="px-4 py-2 bg-zinc-900/40 border-b border-zinc-800 text-sm font-medium text-zinc-200">
                  {r.despesa_nome}
                </div>
                <div className="divide-y divide-zinc-800/40">
                  {r.emails.map((e, i) => (
                    <div key={i} className="px-4 py-3 text-sm">
                      <div className="flex items-center justify-between gap-3">
                        <div className="min-w-0">
                          <p className="text-zinc-300 truncate">{e.assunto}</p>
                          <p className="text-zinc-600 text-xs truncate">{e.remetente} — {e.data}</p>
                        </div>
                        {e.valor_encontrado && (
                          <span className="text-emerald-400 font-medium tabular-nums whitespace-nowrap">
                            R$ {e.valor_encontrado}
                          </span>
                        )}
                      </div>
                      {e.linha_digitavel ? (
                        <div className="mt-2 flex items-center gap-2">
                          <code className="text-xs text-zinc-400 bg-zinc-900/60 px-2 py-1 rounded flex-1 truncate">
                            {e.linha_digitavel}
                          </code>
                          <button onClick={() => copiar(e.linha_digitavel!)}
                            className={cn('text-xs px-2 py-1 rounded border transition-colors whitespace-nowrap',
                              copiado === e.linha_digitavel
                                ? 'border-emerald-700 text-emerald-400'
                                : 'border-zinc-700 text-zinc-400 hover:border-zinc-500')}>
                            {copiado === e.linha_digitavel ? 'Copiado!' : 'Copiar linha digitável'}
                          </button>
                        </div>
                      ) : (
                        <p className="mt-1 text-xs text-zinc-700">Linha digitável não encontrada — abra o email manualmente.</p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
