import { useEffect, useState, useRef, useCallback } from 'react'
import { api } from '../lib/api'
import { cn, formatBRL } from '../lib/utils'

/** Uma posição — o que o internet banking mostra para um papel.
 *  `saldo` vem pronto do backend: MTM quando existe, accrual quando não. */
interface Investimento {
  id: number
  origem: 'emissao_itau' | 'acoes' | 'rf_corretora'
  produto: string
  ativo: string | null
  emissor: string | null
  indexador: string | null
  perc_indexador: number | null
  taxa: number | null
  data_aplicacao: string | null
  data_vencimento: string | null
  data_liquidez: string | null
  pu: number
  quantidade: number | null
  valor_aplicacao: number | null
  saldo_bruto_accrual: number | null
  saldo_liquido_accrual: number | null
  saldo_bruto_mtm: number | null
  saldo_liquido_mtm: number | null
  saldo: number | null
}

interface Grupo { chave: string; total: number; itens: number }

interface Posicao {
  data_posicao: string | null
  itens: Investimento[]
  total: number
  consolidado: Record<Agrupamento, Grupo[]>
}

type Agrupamento = 'produto' | 'indexador' | 'emissor' | 'origem'

const AGRUPAMENTO_LABEL: Record<Agrupamento, string> = {
  produto: 'Produto', indexador: 'Indexador', emissor: 'Emissor', origem: 'Origem'
}

const ORIGEM_LABEL: Record<string, string> = {
  emissao_itau: 'Emissão Itaú', acoes: 'Ações', rf_corretora: 'RF corretora'
}

const data = (d: string | null) =>
  d ? new Date(d + 'T00:00:00').toLocaleDateString('pt-BR') : '—'

/** Remuneração legível: os dois formatos que o banco usa não cabem num campo
 *  só — '94% do DI' e 'IPCA + 4,05% a.a.' dizem coisas diferentes. */
function remuneracao(i: Investimento) {
  if (i.perc_indexador != null && i.indexador)
    return `${i.perc_indexador.toLocaleString('pt-BR')}% do ${i.indexador}`
  if (i.taxa != null && i.indexador && i.indexador !== 'PRE')
    return `${i.indexador} + ${i.taxa.toLocaleString('pt-BR')}% a.a.`
  if (i.taxa != null) return `${i.taxa.toLocaleString('pt-BR')}% a.a.`
  return '—'
}

export function Investimentos({ active }: { active: boolean }) {
  const [pos, setPos] = useState<Posicao | null>(null)
  const [datas, setDatas] = useState<{ data_posicao: string; n: number; total: number }[]>([])
  const [dataSel, setDataSel] = useState('')
  const [agrupamento, setAgrupamento] = useState<Agrupamento>('produto')
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')
  const [erro, setErro] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async (d?: string) => {
    setLoading(true)
    try {
      const [p, ds] = await Promise.all([
        api.investimentos.listar(d), api.investimentos.datas()
      ])
      setPos(p)
      setDatas(ds)
      setDataSel(p.data_posicao ?? '')
    } finally {
      setLoading(false)
    }
  }, [])

  // a tela fica montada (ver App.tsx): recarrega ao voltar a ficar visível
  useEffect(() => { if (active) load(dataSel || undefined) }, [active])

  async function importar(file: File) {
    setErro(''); setMsg('')
    setLoading(true)
    try {
      const res = await api.investimentos.importar(file)
      if (res.ok) { setMsg(res.msg); await load(res.data_posicao) }
      else setErro(res.msg || 'Não consegui importar')
    } catch (e) {
      setErro(String(e))
    } finally {
      setLoading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const grupos = pos?.consolidado[agrupamento] ?? []
  const total = pos?.total ?? 0

  return (
    <div className="flex flex-col h-full pt-3">
      <div className="px-6 pb-4 flex items-start justify-between flex-none">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">Investimentos</h1>
          <p className="text-sm text-zinc-500 mt-0.5">
            {pos?.data_posicao
              ? <>Posição de <strong className="text-zinc-300">{data(pos.data_posicao)}</strong> · {pos.itens.length} papéis</>
              : 'Nenhuma posição importada ainda.'}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {datas.length > 1 && (
            <label className="flex items-center gap-2 text-sm text-zinc-400">
              Data
              <select value={dataSel} onChange={e => { setDataSel(e.target.value); load(e.target.value) }}
                className="bg-zinc-800 border border-zinc-700 rounded-md px-2 py-1.5 text-sm text-zinc-200 outline-none focus:border-emerald-500">
                {datas.map(d => <option key={d.data_posicao} value={d.data_posicao}>{data(d.data_posicao)}</option>)}
              </select>
            </label>
          )}
          <input ref={fileRef} type="file" accept=".xlsx,.xlsm,.xls" className="hidden"
            onChange={e => { const f = e.target.files?.[0]; if (f) importar(f) }} />
          <button onClick={() => fileRef.current?.click()} disabled={loading}
            className="px-4 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
            {loading ? 'Lendo...' : 'Importar posição'}
          </button>
        </div>
      </div>

      {(msg || erro) && (
        <div className="px-6 pb-3 flex-none">
          <p className={cn('text-sm', erro ? 'text-red-400' : 'text-zinc-400')}>{erro || msg}</p>
        </div>
      )}

      <div className="flex-1 overflow-auto px-6 pb-6">
        {!pos?.itens.length ? (
          <div className="rounded-lg border border-zinc-800/60 bg-zinc-900/40 p-8 text-center">
            <p className="text-zinc-400 text-sm">
              Importe a planilha de posição para começar.
            </p>
            <p className="text-zinc-600 text-xs mt-2">
              Aceita o arquivo de carga com as três abas, ou os <code>.xls</code> que o
              internet banking exporta (posição de ações e de renda fixa), um de cada vez.
            </p>
          </div>
        ) : (
          <div className="space-y-5">
            <div className="rounded-lg border border-zinc-700/60 bg-zinc-900/60 p-6">
              <p className="text-xs text-zinc-500 uppercase tracking-wider mb-1">Posição total</p>
              <p className="text-3xl font-bold text-emerald-400 tabular-nums">{formatBRL(total)}</p>
            </div>

            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider">
                  Consolidado
                </span>
                <div className="flex rounded-md border border-zinc-700 overflow-hidden">
                  {(Object.keys(AGRUPAMENTO_LABEL) as Agrupamento[]).map(a => (
                    <button key={a} onClick={() => setAgrupamento(a)}
                      className={cn('px-3 py-1 text-xs transition-colors',
                        agrupamento === a ? 'bg-emerald-600 text-white' : 'text-zinc-400 hover:text-zinc-200')}>
                      {AGRUPAMENTO_LABEL[a]}
                    </button>
                  ))}
                </div>
              </div>
              <div className="rounded-lg overflow-hidden border border-zinc-800/60">
                <table className="w-full text-sm">
                  <tbody>
                    {grupos.map((g, i) => (
                      <tr key={g.chave} className={cn('hover:bg-zinc-800/40', i > 0 && 'border-t border-zinc-800/40')}>
                        <td className="px-4 py-2.5 text-zinc-300 font-medium">
                          {agrupamento === 'origem' ? (ORIGEM_LABEL[g.chave] ?? g.chave) : g.chave}
                          <span className="ml-2 text-[10px] text-zinc-600">{g.itens} {g.itens === 1 ? 'papel' : 'papéis'}</span>
                        </td>
                        {/* a participação de cada grupo é o que mostra a concentração —
                            e concentração é o que decide realocação, nas próximas fases */}
                        <td className="px-4 py-2.5 text-right text-zinc-500 text-xs tabular-nums w-20">
                          {total ? `${(g.total / total * 100).toFixed(1)}%` : '—'}
                        </td>
                        <td className="px-4 py-2.5 text-right tabular-nums text-zinc-200 font-medium w-40">
                          {formatBRL(g.total)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div>
              <div className="flex items-center gap-2 mb-2">
                <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider">Detalhado</span>
                <div className="flex-1 h-px bg-zinc-800" />
              </div>
              <div className="rounded-lg border border-zinc-800/60 overflow-x-auto">
                <table className="w-full text-sm whitespace-nowrap">
                  <thead>
                    <tr className="border-b border-zinc-800 bg-zinc-900/40 text-xs text-zinc-500">
                      <th className="px-3 py-2 text-left font-medium">Produto</th>
                      <th className="px-3 py-2 text-left font-medium">Ativo</th>
                      <th className="px-3 py-2 text-left font-medium">Emissor</th>
                      <th className="px-3 py-2 text-left font-medium">Remuneração</th>
                      <th className="px-3 py-2 text-center font-medium">Aplicação</th>
                      <th className="px-3 py-2 text-center font-medium">Vencimento</th>
                      <th className="px-3 py-2 text-center font-medium">Liquidez</th>
                      <th className="px-3 py-2 text-right font-medium">PU</th>
                      <th className="px-3 py-2 text-right font-medium">Qtde</th>
                      <th className="px-3 py-2 text-right font-medium">Aplicado</th>
                      <th className="px-3 py-2 text-right font-medium">Saldo</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pos.itens.map((i, k) => (
                      <tr key={i.id} className={cn('hover:bg-zinc-800/40', k > 0 && 'border-t border-zinc-800/40')}>
                        <td className="px-3 py-2 text-zinc-300 font-medium">{i.produto}</td>
                        <td className="px-3 py-2 text-zinc-400">{i.ativo || '—'}</td>
                        <td className="px-3 py-2 text-zinc-500 text-xs">{i.emissor || '—'}</td>
                        <td className="px-3 py-2 text-zinc-400 text-xs">{remuneracao(i)}</td>
                        <td className="px-3 py-2 text-center text-zinc-500 text-xs">{data(i.data_aplicacao)}</td>
                        <td className="px-3 py-2 text-center text-zinc-500 text-xs">{data(i.data_vencimento)}</td>
                        <td className="px-3 py-2 text-center text-zinc-500 text-xs">{data(i.data_liquidez)}</td>
                        <td className="px-3 py-2 text-right text-zinc-500 tabular-nums text-xs">
                          {i.pu?.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </td>
                        <td className="px-3 py-2 text-right text-zinc-500 tabular-nums text-xs">
                          {i.quantidade?.toLocaleString('pt-BR', { maximumFractionDigits: 2 }) ?? '—'}
                        </td>
                        <td className="px-3 py-2 text-right text-zinc-500 tabular-nums text-xs">
                          {i.valor_aplicacao != null ? formatBRL(i.valor_aplicacao) : '—'}
                        </td>
                        <td className="px-3 py-2 text-right text-zinc-200 tabular-nums font-medium">
                          {i.saldo != null ? formatBRL(i.saldo) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-xs text-zinc-600 mt-1.5">
                Saldo é o valor de mercado quando o produto tem cotação, e o saldo
                acumulado quando é de emissão — é o número que o internet banking mostra.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
