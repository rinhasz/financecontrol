import { useEffect, useState, useRef, Fragment } from 'react'
import { api } from '../lib/api'
import { cn, formatBRL } from '../lib/utils'
import { DespesaPicker } from '../components/DespesaPicker'

type Step = 'selecionar' | 'revisar' | 'concluido'

interface ParsedTx { data: string; descricao: string; valor: number }

interface DetalheMatch {
  lancamento_id: number
  despesa_id: number
  despesa_id_sugerido: number | null
  despesa_nome: string
  transacao_id: number
  descricao_transacao: string
  valor: number
  data: string
  status: 'pago' | 'agendado'
  // presente só quando o casamento já existia e o status mudou desde então
  // (ex: estava agendado e o débito caiu) — ver rodar_batimento
  status_anterior?: 'pago' | 'agendado' | 'nao_encontrado'
}

const STATUS_LABEL: Record<string, string> = {
  pago: 'Pago', agendado: 'Agendado', nao_encontrado: 'Em aberto'
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

/** Cabeçalho das três partes da revisão. A ordem é fixa e numerada de
 *  propósito: casadas, depois o que faltou de cada lado. */
function Secao({ n, titulo, qtd, ajuda }: { n: number; titulo: string; qtd: number; ajuda: string }) {
  return (
    <div className="mb-2">
      <div className="flex items-baseline gap-2">
        <span className="text-xs font-semibold text-zinc-600 tabular-nums">{n}.</span>
        <h2 className="text-sm font-semibold text-zinc-200">{titulo}</h2>
        <span className="text-xs text-zinc-500 tabular-nums">({qtd})</span>
      </div>
      <p className="text-xs text-zinc-500 ml-5">{ajuda}</p>
    </div>
  )
}

export function Importacao({ active }: { active: boolean }) {
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
  // seção 2 — o inverso da 3: parte da despesa e escolhe a transação
  const [buscandoTx, setBuscandoTx] = useState<number | null>(null)
  const [txSelecionada, setTxSelecionada] = useState('')
  const [confirmando, setConfirmando] = useState(false)
  const [confirmado, setConfirmado] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  // a tela fica sempre montada (ver App.tsx) — sem isso, uma despesa criada
  // no Catálogo enquanto essa aba já estava aberta nunca apareceria aqui
  useEffect(() => {
    if (active) api.catalogo.list().then(setDespesas)
  }, [active])

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
      setConfirmado(null)
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
      despesaId = await criarDespesa(nome, d.valor)
      despesaNome = nome
    } else {
      if (!selecionada) return
      despesaId = Number(selecionada)
      despesaNome = despesas.find(ds => ds.id === despesaId)?.nome ?? '?'
    }

    // Só atualiza o estado local — nada é gravado até "Confirmar tudo"
    setResultado(r => r ? {
      ...r,
      detalhes: r.detalhes.map(x => x.transacao_id === d.transacao_id ? { ...x, despesa_id: despesaId, despesa_nome: despesaNome } : x)
    } : r)
    setCorrigindo(null)
  }

  /** Casa transação + despesa e move o par das seções 2 e 3 para a 1.
   *  As duas direções de associação terminam aqui — só muda por qual ponta o
   *  usuário começou. Só mexe no estado local; nada é gravado até "Confirmar tudo". */
  function associarPar(t: TransacaoSobrando, despesaId: number, despesaNome: string) {
    setResultado(r => {
      if (!r) return r
      const status: 'pago' | 'agendado' = t.situacao === 'efetivada' ? 'pago' : 'agendado'
      return {
        ...r,
        nao_encontrados: r.nao_encontrados.filter(x => x.despesa_id !== despesaId),
        transacoes_sobrando: r.transacoes_sobrando.filter(x => x.id !== t.id),
        detalhes: [...r.detalhes, {
          lancamento_id: 0, despesa_id: despesaId, despesa_id_sugerido: null, despesa_nome: despesaNome,
          transacao_id: t.id, descricao_transacao: t.descricao, valor: Math.abs(t.valor), data: t.data, status
        }]
      }
    })
  }

  async function criarDespesa(nome: string, valor: number) {
    const keywords = nome.toLowerCase().split(/\s+/).filter(w => w.length >= 3)
    const res = await api.catalogo.upsert({
      nome,
      tipo_valor: 'variavel',
      padrao_variabilidade: 'variavel_nao_sazonal',
      valor_padrao: valor,
      regras_match: JSON.stringify({ palavras_chave: keywords, faixa_valor: null, janela_dias: 5, banco: null })
    })
    setDespesas(ds => [...ds, { id: res.id, nome }])
    return res.id as number
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
      despesaId = await criarDespesa(nome, Math.abs(t.valor))
      despesaNome = nome
    } else {
      if (!selecionadaAssoc) return
      despesaId = Number(selecionadaAssoc)
      despesaNome = resultado?.nao_encontrados.find(x => x.despesa_id === despesaId)?.despesa_nome
        ?? despesas.find(ds => ds.id === despesaId)?.nome ?? '?'
    }
    associarPar(t, despesaId, despesaNome)
    setAssociando(null)
  }

  function abrirBuscaTx(despesaId: number) {
    setBuscandoTx(buscandoTx === despesaId ? null : despesaId)
    setTxSelecionada('')
  }

  function aplicarBuscaTx(n: NaoEncontrado) {
    const t = resultado?.transacoes_sobrando.find(x => String(x.id) === txSelecionada)
    if (!t) return
    associarPar(t, n.despesa_id, n.despesa_nome)
    setBuscandoTx(null)
  }

  async function confirmarTudo() {
    if (!resultado || resultado.detalhes.length === 0) return
    setConfirmando(true)
    try {
      const res = await api.batimento.confirmar(mesRef, resultado.detalhes.map(d => ({
        transacao_id: d.transacao_id, despesa_id: d.despesa_id, despesa_id_sugerido: d.despesa_id_sugerido
      })))
      setConfirmado(res.confirmados ?? resultado.detalhes.length)
    } finally {
      setConfirmando(false)
    }
  }

  const STEPS: [Step, string][] = [['selecionar', '1. Selecionar'], ['revisar', '2. Revisar'], ['concluido', '3. Concluído']]
  const stepIdx = STEPS.findIndex(([s]) => s === step)

  // As duas seções de associação são espelho uma da outra e comem da mesma
  // lista: o que sobrou de um lado é a opção do outro. Ambas encolhem sozinhas
  // conforme os pares vão sendo montados.
  const opcoesTransacao = (resultado?.transacoes_sobrando ?? []).map(t => ({
    id: t.id,
    nome: `${new Date(t.data + 'T00:00:00').toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })}  ·  ${t.descricao}  ·  ${formatBRL(Math.abs(t.valor))}`
  }))
  const opcoesDespesa = (resultado?.nao_encontrados ?? []).map(n => ({
    id: n.despesa_id,
    nome: n.valor_esperado > 0 ? `${n.despesa_nome}  ·  ${formatBRL(n.valor_esperado)}` : n.despesa_nome
  }))

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
          <div className="space-y-6 max-w-4xl">
            {confirmado !== null ? (
              <div className="rounded-lg border border-emerald-800/40 bg-emerald-950/20 p-8 text-center">
                <div className="text-5xl font-bold text-emerald-400 mb-2">{confirmado}</div>
                <p className="text-zinc-400 text-sm">lançamentos confirmados e gravados</p>
              </div>
            ) : (
              <div className="rounded-lg border border-zinc-700/60 bg-zinc-900/60 p-8 text-center">
                <div className="text-5xl font-bold text-zinc-200 mb-2">{resultado.matched}/{resultado.total}</div>
                <p className="text-zinc-500 text-sm">sugestões automáticas — revise abaixo e clique em "Confirmar tudo" para gravar. Nada é salvo antes disso.</p>
              </div>
            )}

            {/* ---- 1. casadas ---- */}
            {resultado.detalhes.length > 0 && (
              <div>
                <Secao n={1} titulo="Despesas casadas" qtd={resultado.detalhes.length}
                  ajuda="Se alguma despesa estiver errada, corrija na linha." />
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
                            <td className="px-4 py-2 text-center whitespace-nowrap">
                              {d.status_anterior && (
                                <span className="text-xs text-zinc-600 mr-1.5 line-through">
                                  {STATUS_LABEL[d.status_anterior] ?? d.status_anterior}
                                </span>
                              )}
                              <span className={cn('text-xs px-2 py-0.5 rounded',
                                d.status === 'pago' ? 'text-emerald-400 bg-emerald-950/40' : 'text-blue-400 bg-blue-950/40')}>
                                {d.status === 'pago' ? 'Pago' : 'Agendado'}
                              </span>
                            </td>
                            <td className="px-4 py-2 text-right">
                              {confirmado === null && (
                                <button onClick={() => abrirCorrecao(d.transacao_id)}
                                  className="text-xs text-zinc-500 hover:text-amber-400 transition-colors whitespace-nowrap">
                                  Não é essa despesa
                                </button>
                              )}
                            </td>
                          </tr>
                          {corrigindo === d.transacao_id && (
                            <tr className="bg-zinc-900/60 border-t border-zinc-800/40">
                              <td colSpan={5} className="px-4 py-3">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <DespesaPicker despesas={despesas} value={selecionada} onChange={setSelecionada}
                                    placeholder="Digite pra buscar a despesa certa..." allowNova
                                    onSelectNova={q => { setSelecionada('nova'); setNovaDespesaNome(q) }}
                                    className="w-56" />
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

            {/* ---- 2. despesas ativas que não foram encontradas no extrato ----
                 O combo aqui lista as TRANSAÇÕES sobrando: parte-se da despesa
                 e procura-se o débito. É o inverso exato da seção 3. */}
            {resultado.nao_encontrados.length > 0 && (
              <div>
                <Secao n={2} titulo="Despesas ativas que não encontrei no extrato" qtd={resultado.nao_encontrados.length}
                  ajuda="Se o débito existe e eu não achei, escolha a transação correspondente." />
                <div className="rounded-lg overflow-hidden border border-zinc-800/60">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-zinc-800 bg-zinc-900/40">
                        <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Despesa</th>
                        <th className="px-4 py-2 text-right text-xs text-zinc-500 font-medium">Previsto</th>
                        <th className="px-4 py-2"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {resultado.nao_encontrados.map((n, i) => (
                        <Fragment key={n.despesa_id}>
                          <tr className={cn('hover:bg-zinc-800/40', i > 0 && 'border-t border-zinc-800/40')}>
                            <td className="px-4 py-2 text-zinc-200 font-medium">{n.despesa_nome}</td>
                            <td className="px-4 py-2 text-right tabular-nums text-zinc-500">{formatBRL(n.valor_esperado)}</td>
                            <td className="px-4 py-2 text-right">
                              {confirmado === null && (
                                <button onClick={() => abrirBuscaTx(n.despesa_id)}
                                  className="text-xs text-zinc-500 hover:text-emerald-400 transition-colors whitespace-nowrap">
                                  Associar transação
                                </button>
                              )}
                            </td>
                          </tr>
                          {buscandoTx === n.despesa_id && (
                            <tr className="bg-zinc-900/60 border-t border-zinc-800/40">
                              <td colSpan={3} className="px-4 py-3">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <DespesaPicker despesas={opcoesTransacao} value={txSelecionada} onChange={setTxSelecionada}
                                    placeholder="Digite pra buscar no extrato..." vazio="Nenhuma transação sobrando"
                                    className="w-96" />
                                  <button onClick={() => aplicarBuscaTx(n)} disabled={!txSelecionada}
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

            {/* ---- 3. transações do extrato sem despesa ----
                 O combo lista só as despesas da seção 2 (ativas e ainda não
                 associadas neste mês) — oferecer o catálogo inteiro deixava
                 escolher uma despesa que já casou com outra transação. */}
            {resultado.transacoes_sobrando.length > 0 && (
              <div>
                <Secao n={3} titulo="Transações do extrato sem despesa" qtd={resultado.transacoes_sobrando.length}
                  ajuda="Associe a uma despesa ativa ainda em aberto, ou crie uma despesa nova." />
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
                              {confirmado === null && (
                                <button onClick={() => abrirAssociacao(t.id)}
                                  className="text-xs text-zinc-500 hover:text-emerald-400 transition-colors whitespace-nowrap">
                                  Associar despesa
                                </button>
                              )}
                            </td>
                          </tr>
                          {associando === t.id && (
                            <tr className="bg-zinc-900/60 border-t border-zinc-800/40">
                              <td colSpan={4} className="px-4 py-3">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <DespesaPicker despesas={opcoesDespesa} value={selecionadaAssoc} onChange={setSelecionadaAssoc}
                                    placeholder="Digite pra buscar a despesa..." allowNova
                                    vazio="Nenhuma despesa em aberto neste mês"
                                    onSelectNova={q => { setSelecionadaAssoc('nova'); setNovaDespesaNomeAssoc(q) }}
                                    className="w-72" />
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

            {confirmado === null ? (
              <div className="flex items-center gap-3 pt-2">
                <button onClick={confirmarTudo} disabled={confirmando || resultado.detalhes.length === 0}
                  className="px-5 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
                  {confirmando ? 'Gravando...' : `Confirmar tudo (${resultado.detalhes.length})`}
                </button>
                <p className="text-sm text-zinc-500">Nada é gravado até você clicar aqui — pode sair e voltar sem perder o que já revisou.</p>
              </div>
            ) : (
              <>
                <p className="text-sm text-zinc-500">
                  Vá para <strong className="text-zinc-300">Mês Atual</strong> para revisar os lançamentos em aberto.
                </p>
                <button onClick={() => { setStep('selecionar'); setFile(null); setMsg(''); setTransacoes([]); setResultado(null); setConfirmado(null) }}
                  className="px-4 py-2 rounded-md border border-zinc-700 text-sm text-zinc-300 hover:border-zinc-500 transition-colors">
                  Nova importação
                </button>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
