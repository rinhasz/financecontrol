import { useState, useRef, Fragment } from 'react'
import { api } from '../lib/api'
import { cn, formatBRL } from '../lib/utils'

type Step = 'selecionar' | 'revisar' | 'concluido'

interface ParsedTx { data: string; descricao: string; valor: number }

interface DetalheMatch {
  lancamento_id: number
  despesa_id: number
  despesa_nome: string
  transacao_id: number
  descricao_transacao: string
  valor: number
  data: string
  status: 'pago' | 'agendado'
}

interface NaoEncontrado {
  lancamento_id: number
  despesa_id: number
  despesa_nome: string
  valor_esperado: number
}

interface TransacaoSobrando {
  id: number
  data: string
  descricao: string
  valor: number
  situacao: 'efetivada' | 'agendada'
}

interface Despesa { id: number; nome: string }

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
  const [resultado, setResultado] = useState<{
    matched: number; total: number; detalhes: DetalheMatch[]
    nao_encontrados: NaoEncontrado[]; transacoes_sobrando: TransacaoSobrando[]
  } | null>(null)
  const [despesas, setDespesas] = useState<Despesa[]>([])
  const [corrigindo, setCorrigindo] = useState<number | null>(null)
  const [selecionada, setSelecionada] = useState('')
  const [novaDespesaNome, setNovaDespesaNome] = useState('')
  const [associando, setAssociando] = useState<number | null>(null)
  const [selecionadaAssoc, setSelecionadaAssoc] = useState('')
  const [novaDespesaNomeAssoc, setNovaDespesaNomeAssoc] = useState('')
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
      const [res, cat] = await Promise.all([api.batimento.rodar(mesRef), api.catalogo.list()])
      setResultado(res)
      setDespesas(cat)
      setStep('concluido')
    } finally {
      setLoading(false)
    }
  }

  function abrirCorrecao(transacaoId: number) {
    setCorrigindo(corrigindo === transacaoId ? null : transacaoId)
    setSelecionada('')
    setNovaDespesaNome('')
  }

  async function aplicarCorrecao(d: DetalheMatch) {
    let despesaId: number
    let despesaNome: string
    if (selecionada === 'nova') {
      const nome = novaDespesaNome.trim()
      if (!nome) return
      const keywords = nome.toLowerCase().split(/\s+/).filter(w => w.length >= 3)
      const res = await api.catalogo.upsert({
        nome,
        tipo_valor: 'variavel',
        padrao_variabilidade: 'variavel_nao_sazonal',
        valor_padrao: d.valor,
        regras_match: JSON.stringify({ palavras_chave: keywords, faixa_valor: null, janela_dias: 5, banco: null })
      })
      despesaId = res.id
      despesaNome = nome
    } else {
      if (!selecionada) return
      despesaId = Number(selecionada)
      despesaNome = despesas.find(ds => ds.id === despesaId)?.nome ?? '?'
    }

    await api.batimento.corrigir(mesRef, d.transacao_id, despesaId)

    setResultado(r => r ? {
      ...r,
      detalhes: r.detalhes.map(x => x.transacao_id === d.transacao_id ? { ...x, despesa_id: despesaId, despesa_nome: despesaNome } : x)
    } : r)
    setCorrigindo(null)
  }

  function abrirAssociacao(transacaoId: number) {
    setAssociando(associando === transacaoId ? null : transacaoId)
    setSelecionadaAssoc('')
    setNovaDespesaNomeAssoc('')
  }

  async function aplicarAssociacao(t: TransacaoSobrando) {
    let despesaId: number
    let despesaNome: string
    if (selecionadaAssoc === 'nova') {
      const nome = novaDespesaNomeAssoc.trim()
      if (!nome) return
      const keywords = nome.toLowerCase().split(/\s+/).filter(w => w.length >= 3)
      const res = await api.catalogo.upsert({
        nome,
        tipo_valor: 'variavel',
        padrao_variabilidade: 'variavel_nao_sazonal',
        valor_padrao: Math.abs(t.valor),
        regras_match: JSON.stringify({ palavras_chave: keywords, faixa_valor: null, janela_dias: 5, banco: null })
      })
      despesaId = res.id
      despesaNome = nome
      setDespesas(ds => [...ds, { id: despesaId, nome: despesaNome }])
    } else {
      if (!selecionadaAssoc) return
      despesaId = Number(selecionadaAssoc)
      despesaNome = despesas.find(ds => ds.id === despesaId)?.nome ?? '?'
    }

    await api.batimento.corrigir(mesRef, t.id, despesaId)

    setResultado(r => {
      if (!r) return r
      const status: 'pago' | 'agendado' = t.situacao === 'efetivada' ? 'pago' : 'agendado'
      return {
        ...r,
        nao_encontrados: r.nao_encontrados.filter(x => x.despesa_id !== despesaId),
        transacoes_sobrando: r.transacoes_sobrando.filter(x => x.id !== t.id),
        detalhes: [...r.detalhes, {
          lancamento_id: 0, despesa_id: despesaId, despesa_nome: despesaNome,
          transacao_id: t.id, descricao_transacao: t.descricao, valor: Math.abs(t.valor), data: t.data, status
        }]
      }
    })
    setAssociando(null)
  }

  const STEPS: [Step, string][] = [['selecionar', '1. Selecionar'], ['revisar', '2. Revisar'], ['concluido', '3. Concluído']]
  const stepIdx = STEPS.findIndex(([s]) => s === step)

  return (
    <div className="flex flex-col h-full pt-3">
      <div className="px-6 pb-4 flex-none">
        <h1 className="text-xl font-semibold text-zinc-100">Importar Extrato</h1>
        <p className="text-sm text-zinc-500 mt-0.5">OFX, CSV ou Excel — importa e bate automaticamente com os lançamentos do mês. Use Excel para capturar lançamentos futuros/agendados.</p>
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
              <label className="text-sm text-zinc-400 block mb-2">Arquivo (.ofx, .csv, .xls ou .xlsx)</label>
              <input ref={fileRef} type="file" accept=".ofx,.csv,.txt,.xls,.xlsx,.xlsm" className="hidden"
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
          <div className="space-y-4 max-w-3xl">
            <div className="rounded-lg border border-emerald-800/40 bg-emerald-950/20 p-8 text-center">
              <div className="text-5xl font-bold text-emerald-400 mb-2">{resultado.matched}/{resultado.total}</div>
              <p className="text-zinc-400 text-sm">lançamentos casados automaticamente</p>
            </div>

            {resultado.detalhes.length > 0 && (
              <div>
                <p className="text-sm text-zinc-400 mb-2">Confira os casamentos — se alguma despesa estiver errada, corrija abaixo:</p>
                <div className="rounded-lg overflow-hidden border border-zinc-800/60">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-zinc-800 bg-zinc-900/40">
                        <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Despesa</th>
                        <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Transação no extrato</th>
                        <th className="px-4 py-2 text-right text-xs text-zinc-500 font-medium">Valor</th>
                        <th className="px-4 py-2 text-center text-xs text-zinc-500 font-medium">Status</th>
                        <th className="px-4 py-2"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {resultado.detalhes.map((d, i) => (
                        <Fragment key={d.transacao_id}>
                          <tr className={cn('hover:bg-zinc-800/40', i > 0 && 'border-t border-zinc-800/40')}>
                            <td className="px-4 py-2 text-zinc-200 font-medium">{d.despesa_nome}</td>
                            <td className="px-4 py-2 text-zinc-400">{d.descricao_transacao}</td>
                            <td className="px-4 py-2 text-right tabular-nums text-zinc-300">{formatBRL(d.valor)}</td>
                            <td className="px-4 py-2 text-center">
                              <span className={cn('text-xs px-2 py-0.5 rounded',
                                d.status === 'pago' ? 'text-emerald-400 bg-emerald-950/40' : 'text-blue-400 bg-blue-950/40')}>
                                {d.status === 'pago' ? 'Pago' : 'Agendado'}
                              </span>
                            </td>
                            <td className="px-4 py-2 text-right">
                              <button onClick={() => abrirCorrecao(d.transacao_id)}
                                className="text-xs text-zinc-500 hover:text-amber-400 transition-colors whitespace-nowrap">
                                Não é essa despesa
                              </button>
                            </td>
                          </tr>
                          {corrigindo === d.transacao_id && (
                            <tr className="bg-zinc-900/60 border-t border-zinc-800/40">
                              <td colSpan={5} className="px-4 py-3">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <select value={selecionada} onChange={e => setSelecionada(e.target.value)}
                                    className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500">
                                    <option value="">Selecionar despesa correta...</option>
                                    {despesas.map(ds => <option key={ds.id} value={ds.id}>{ds.nome}</option>)}
                                    <option value="nova">+ Nova despesa</option>
                                  </select>
                                  {selecionada === 'nova' && (
                                    <input value={novaDespesaNome} onChange={e => setNovaDespesaNome(e.target.value)}
                                      placeholder="Nome da nova despesa" autoFocus
                                      className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500" />
                                  )}
                                  <button onClick={() => aplicarCorrecao(d)}
                                    disabled={!selecionada || (selecionada === 'nova' && !novaDespesaNome.trim())}
                                    className="px-3 py-1 rounded bg-emerald-600 text-white text-xs font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
                                    Confirmar
                                  </button>
                                </div>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {resultado.transacoes_sobrando.length > 0 && (
              <div>
                <p className="text-sm text-zinc-400 mb-2">
                  Transações do extrato sem despesa correspondente — associe a uma despesa existente ou crie uma nova:
                </p>
                <div className="rounded-lg overflow-hidden border border-zinc-800/60">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-zinc-800 bg-zinc-900/40">
                        <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Data</th>
                        <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Descrição no extrato</th>
                        <th className="px-4 py-2 text-right text-xs text-zinc-500 font-medium">Valor</th>
                        <th className="px-4 py-2"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {resultado.transacoes_sobrando.map((t, i) => (
                        <Fragment key={t.id}>
                          <tr className={cn('hover:bg-zinc-800/40', i > 0 && 'border-t border-zinc-800/40')}>
                            <td className="px-4 py-2 text-zinc-500 text-xs tabular-nums">
                              {new Date(t.data + 'T00:00:00').toLocaleDateString('pt-BR')}
                            </td>
                            <td className="px-4 py-2 text-zinc-300">{t.descricao}</td>
                            <td className="px-4 py-2 text-right tabular-nums text-zinc-300">{formatBRL(Math.abs(t.valor))}</td>
                            <td className="px-4 py-2 text-right">
                              <button onClick={() => abrirAssociacao(t.id)}
                                className="text-xs text-zinc-500 hover:text-emerald-400 transition-colors whitespace-nowrap">
                                Associar despesa
                              </button>
                            </td>
                          </tr>
                          {associando === t.id && (
                            <tr className="bg-zinc-900/60 border-t border-zinc-800/40">
                              <td colSpan={4} className="px-4 py-3">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <select value={selecionadaAssoc} onChange={e => setSelecionadaAssoc(e.target.value)}
                                    className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500">
                                    <option value="">Selecionar despesa...</option>
                                    {despesas.map(ds => <option key={ds.id} value={ds.id}>{ds.nome}</option>)}
                                    <option value="nova">+ Nova despesa</option>
                                  </select>
                                  {selecionadaAssoc === 'nova' && (
                                    <input value={novaDespesaNomeAssoc} onChange={e => setNovaDespesaNomeAssoc(e.target.value)}
                                      placeholder="Nome da nova despesa" autoFocus
                                      className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500" />
                                  )}
                                  <button onClick={() => aplicarAssociacao(t)}
                                    disabled={!selecionadaAssoc || (selecionadaAssoc === 'nova' && !novaDespesaNomeAssoc.trim())}
                                    className="px-3 py-1 rounded bg-emerald-600 text-white text-xs font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
                                    Confirmar
                                  </button>
                                </div>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <p className="text-sm text-zinc-500">
              Vá para <strong className="text-zinc-300">Mês Atual</strong> para revisar os lançamentos em aberto.
            </p>
            <button onClick={() => { setStep('selecionar'); setFile(null); setMsg(''); setTransacoes([]); setResultado(null) }}
              className="px-4 py-2 rounded-md border border-zinc-700 text-sm text-zinc-300 hover:border-zinc-500 transition-colors">
              Nova importação
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
