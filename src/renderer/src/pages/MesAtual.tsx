import { useEffect, useState, useCallback } from 'react'
import { api } from '../lib/api'
import { formatBRL, mesRefLabel, currentMesRef, prevMesRef, nextMesRef, cn } from '../lib/utils'

interface Lancamento {
  id: number
  mes_ref: string
  despesa_id: number
  despesa_nome: string
  categoria_nome: string
  valor_esperado: number
  valor_real: number | null
  status: 'pago' | 'agendado' | 'nao_encontrado'
  data_pagamento: string | null
  linha_digitavel: string | null
}

interface Resumo {
  pago: number; agendado: number; naoEncontrado: number
  total: number; reserva: number; saldo: number; receitas: number; resgate: number
}

const STATUS_STYLE = {
  pago:           'bg-emerald-950/50 text-emerald-400 border-emerald-800/50',
  agendado:       'bg-blue-950/50 text-blue-400 border-blue-800/50',
  nao_encontrado: 'bg-zinc-800/30 text-zinc-500 border-zinc-700/30'
}
const STATUS_LABEL = { pago: 'Pago', agendado: 'Agendado', nao_encontrado: 'Em aberto' }
const ROW_BG = {
  pago:           'bg-emerald-950/20 hover:bg-emerald-950/30',
  agendado:       'bg-blue-950/20 hover:bg-blue-950/30',
  nao_encontrado: 'hover:bg-zinc-800/40'
}

export function MesAtual() {
  const [mesRef, setMesRef] = useState(currentMesRef())
  const [lancamentos, setLancamentos] = useState<Lancamento[]>([])
  const [resumo, setResumo] = useState<Resumo | null>(null)
  const [loading, setLoading] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [editVal, setEditVal] = useState('')
  const [saldoEdit, setSaldoEdit] = useState(false)
  const [saldoVal, setSaldoVal] = useState('')
  const [diaSalarioEdit, setDiaSalarioEdit] = useState(false)
  const [diaSalarioVal, setDiaSalarioVal] = useState('27')
  const [boletoCopiado, setBoletoCopiado] = useState<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [data, res, cfg] = await Promise.all([
        api.lancamentos.list(mesRef),
        api.lancamentos.resumo(mesRef),
        api.config.get()
      ])
      setLancamentos(data)
      setResumo(res)
      setSaldoVal(String(res.saldo))
      setDiaSalarioVal(String(cfg.dia_recebimento_salario ?? '27'))
    } finally {
      setLoading(false)
    }
  }, [mesRef])

  useEffect(() => { load() }, [load])

  async function saveValor(l: Lancamento) {
    const val = parseFloat(editVal.replace(',', '.'))
    if (!isNaN(val)) await api.lancamentos.update(l.id, { valor_esperado: val })
    setEditId(null)
    load()
  }

  async function saveSaldo() {
    const val = parseFloat(saldoVal.replace(',', '.'))
    if (!isNaN(val)) await api.config.set({ saldo_conta: val })
    setSaldoEdit(false)
    load()
  }

  async function saveDiaSalario() {
    const val = parseInt(diaSalarioVal, 10)
    if (!isNaN(val) && val >= 1 && val <= 31) await api.config.set({ dia_recebimento_salario: val })
    setDiaSalarioEdit(false)
    load()
  }

  async function resetarMes() {
    const ok = window.confirm(
      `Resetar ${mesRefLabel(mesRef)}? Isso volta todas as despesas desse mês pra "Em aberto" e desfaz os ` +
      'casamentos com o extrato. As transações importadas continuam no banco (não precisa reimportar).'
    )
    if (!ok) return
    await api.batimento.resetar(mesRef)
    load()
  }

  async function copiarBoleto(l: Lancamento) {
    if (!l.linha_digitavel) return
    await navigator.clipboard.writeText(l.linha_digitavel.replace(/\s+/g, ''))
    setBoletoCopiado(l.id)
    setTimeout(() => setBoletoCopiado(null), 1500)
  }

  const byCategory = lancamentos.reduce<Record<string, Lancamento[]>>((acc, l) => {
    const cat = l.categoria_nome || 'Outros'
    if (!acc[cat]) acc[cat] = []
    acc[cat].push(l)
    return acc
  }, {})

  return (
    <div className="flex flex-col h-full pt-3">
      {/* Header */}
      <div className="px-6 pb-4 flex items-center justify-between flex-none">
        <div className="flex items-center gap-3">
          <button onClick={() => setMesRef(prevMesRef(mesRef))}
            className="p-1.5 rounded-md text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors">
            <ChevronLeft />
          </button>
          <h1 className="text-xl font-semibold text-zinc-100 w-28 text-center">{mesRefLabel(mesRef)}</h1>
          <button onClick={() => setMesRef(nextMesRef(mesRef))}
            className="p-1.5 rounded-md text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors">
            <ChevronRight />
          </button>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5 text-sm text-zinc-500">
            <span>Recebo o salário no dia</span>
            {diaSalarioEdit ? (
              <input autoFocus type="number" min={1} max={31}
                className="bg-zinc-800 border border-zinc-600 rounded px-1.5 py-0.5 text-sm text-zinc-200 w-14 outline-none focus:border-emerald-500"
                value={diaSalarioVal} onChange={e => setDiaSalarioVal(e.target.value)}
                onBlur={saveDiaSalario}
                onKeyDown={e => { if (e.key === 'Enter') saveDiaSalario() }}
              />
            ) : (
              <button onClick={() => setDiaSalarioEdit(true)} className="text-zinc-300 font-medium hover:text-emerald-400 transition-colors">
                {diaSalarioVal}
              </button>
            )}
          </div>
          <button onClick={load}
            className="flex items-center gap-2 px-3 py-1.5 rounded-md text-sm text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors">
            <RefreshIcon /> Atualizar
          </button>
          <button onClick={resetarMes}
            className="px-3 py-1.5 rounded-md text-sm text-zinc-500 hover:text-red-400 hover:bg-red-950/20 transition-colors">
            Resetar mês
          </button>
        </div>
      </div>

      {/* Resumo cards */}
      {resumo && (
        <div className="px-6 pb-4 grid grid-cols-4 gap-3 flex-none">
          <Card label="Total do mês"  value={resumo.total}        color="zinc" />
          <Card label="Pago"          value={resumo.pago}         color="emerald" />
          <Card label="Agendado"      value={resumo.agendado}     color="blue" />
          <Card label="Em aberto"     value={resumo.naoEncontrado} color="amber" />
        </div>
      )}

      {/* Calculadora de resgate */}
      {resumo && (
        <div className="px-6 pb-4 flex-none">
          <div className="rounded-lg border border-zinc-700/60 bg-zinc-900/60 p-4">
            <p className="text-xs text-zinc-500 uppercase tracking-wider mb-3 font-medium">Calculadora de Resgate</p>
            <div className="grid grid-cols-5 gap-4 text-sm">
              {[
                ['Total a vencer',    formatBRL(resumo.total)],
                ['Reserva',           formatBRL(resumo.reserva)],
                ['Receitas previstas',formatBRL(resumo.receitas)],
              ].map(([lbl, val]) => (
                <div key={lbl}>
                  <p className="text-zinc-500 text-xs mb-0.5">{lbl}</p>
                  <p className="text-zinc-200 font-medium">{val}</p>
                </div>
              ))}
              <div>
                <p className="text-zinc-500 text-xs mb-0.5">Saldo conta</p>
                {saldoEdit ? (
                  <input autoFocus
                    className="bg-zinc-800 border border-zinc-600 rounded px-2 py-0.5 text-sm text-zinc-200 w-28 outline-none focus:border-emerald-500"
                    value={saldoVal} onChange={e => setSaldoVal(e.target.value)}
                    onBlur={saveSaldo}
                    onKeyDown={e => { if (e.key === 'Enter') saveSaldo() }}
                  />
                ) : (
                  <button onClick={() => setSaldoEdit(true)}
                    className="text-zinc-200 font-medium hover:text-emerald-400 transition-colors">
                    {formatBRL(resumo.saldo)}
                  </button>
                )}
              </div>
              <div>
                <p className="text-zinc-500 text-xs mb-0.5">A resgatar</p>
                <p className={cn('text-lg font-bold', resumo.resgate > 0 ? 'text-amber-400' : 'text-emerald-400')}>
                  {formatBRL(resumo.resgate)}
                </p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Tabela */}
      <div className="flex-1 overflow-auto px-6 pb-6">
        {loading ? (
          <div className="flex items-center justify-center h-40 text-zinc-500">Carregando...</div>
        ) : (
          <div className="space-y-3">
            {Object.entries(byCategory).map(([cat, items]) => (
              <div key={cat}>
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider">{cat}</span>
                  <div className="flex-1 h-px bg-zinc-800" />
                  <span className="text-xs text-zinc-600">
                    {formatBRL(items.reduce((s, l) => s + (l.valor_real ?? l.valor_esperado), 0))}
                  </span>
                </div>
                <div className="rounded-lg overflow-hidden border border-zinc-800/60">
                  <table className="w-full text-sm">
                    <tbody>
                      {items.map((l, i) => (
                        <tr key={l.id} className={cn('transition-colors', ROW_BG[l.status], i > 0 && 'border-t border-zinc-800/40')}>
                          <td className="px-4 py-2.5 text-zinc-300 font-medium">{l.despesa_nome}</td>
                          <td className="px-4 py-2.5 text-right">
                            {editId === l.id ? (
                              <input autoFocus
                                className="bg-zinc-800 border border-zinc-600 rounded px-2 py-0.5 text-sm text-zinc-200 w-28 text-right outline-none focus:border-emerald-500"
                                value={editVal} onChange={e => setEditVal(e.target.value)}
                                onBlur={() => saveValor(l)}
                                onKeyDown={e => { if (e.key === 'Enter') saveValor(l); if (e.key === 'Escape') setEditId(null) }}
                              />
                            ) : (
                              <button onClick={() => { setEditId(l.id); setEditVal(String(l.valor_real ?? l.valor_esperado)) }}
                                className="text-zinc-200 hover:text-emerald-400 transition-colors font-medium tabular-nums">
                                {formatBRL(l.valor_real ?? l.valor_esperado)}
                              </button>
                            )}
                          </td>
                          <td className="px-4 py-2.5 text-zinc-500 text-xs">
                            {l.data_pagamento ? new Date(l.data_pagamento + 'T00:00:00').toLocaleDateString('pt-BR') : '—'}
                          </td>
                          <td className="px-4 py-2.5">
                            <span className={cn('inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border', STATUS_STYLE[l.status])}>
                              {STATUS_LABEL[l.status]}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-right">
                            {l.linha_digitavel && (
                              <button onClick={() => copiarBoleto(l)}
                                className={cn('text-xs px-2 py-0.5 rounded border transition-colors whitespace-nowrap',
                                  boletoCopiado === l.id
                                    ? 'border-emerald-700 text-emerald-400'
                                    : 'border-zinc-700 text-zinc-400 hover:border-zinc-500')}>
                                {boletoCopiado === l.id ? 'Copiado!' : 'Copiar boleto'}
                              </button>
                            )}
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

function Card({ label, value, color }: { label: string; value: number; color: string }) {
  const colors: Record<string, string> = { zinc: 'text-zinc-200', emerald: 'text-emerald-400', blue: 'text-blue-400', amber: 'text-amber-400' }
  return (
    <div className="rounded-lg border border-zinc-800/60 bg-zinc-900/60 px-4 py-3">
      <p className="text-xs text-zinc-500 mb-1">{label}</p>
      <p className={cn('text-lg font-bold tabular-nums', colors[color])}>{formatBRL(value)}</p>
    </div>
  )
}

function ChevronLeft() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
}
function ChevronRight() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
}
function RefreshIcon() {
  return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="23 4 23 10 17 10" /><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" /></svg>
}
