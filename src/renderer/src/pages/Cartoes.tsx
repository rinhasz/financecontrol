import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { formatBRL, cn } from '../lib/utils'

/** Análise da fatura de cartão (doc 17).
 *
 *  Quatro abas: onde gastei (com detalhe até o lançamento), onde economizar,
 *  os lançamentos para corrigir categoria, e a importação. A importação fica
 *  junto porque sem fatura não há nada a responder — e porque a conferência
 *  precisa ser vista **antes** de gravar: um lançamento perdido numa coluna não
 *  dá erro, só some.
 */

interface Item {
  id?: number
  secao: string
  portador: string | null
  cartao_final: string | null
  data_compra: string | null
  estabelecimento: string
  estabelecimento_norm: string | null
  valor: number
  parcela_n: number | null
  parcela_total: number | null
  categoria: string | null
  categoria_fatura: string | null
  origem_categoria: string | null
  cartao?: string
}

interface Grupo { chave: string; valor: number; n: number }

interface Parcelamento {
  estabelecimento: string
  categoria: string | null
  parcela_n: number
  parcela_total: number
  valor_mes: number
  faltam: number
  resta: number
  termina_agora: boolean
}

interface Decisao {
  avista: number; avista_n: number
  parcela_nova: number; parcela_nova_n: number
  parcela_antiga: number; parcela_antiga_n: number
}

interface FaturaResumo {
  cartao: string
  emissor: string | null
  situacao: string | null
  data_vencimento: string | null
  data_extrato: string | null
  total_fatura: number | null
}

interface Analise {
  ok: boolean
  vazio?: boolean
  mes_ref: string | null
  meses: string[]
  faturas: FaturaResumo[]
  tem_aberta: boolean
  total_lancamentos: number
  total_comprometido: number
  total_encargos: number
  total_a_chegar: number
  decisao: Decisao
  parcelamentos: Parcelamento[]
  por_categoria: Grupo[]
  por_estabelecimento: Grupo[]
  por_portador: Grupo[]
  por_cartao: Grupo[]
}

interface Sugestao {
  tipo: 'necessidade' | 'preco'
  alvo: string
  gasto_no_mes: number
  diagnostico: string
  acao: string
  recorrencia: 'recorrente' | 'pontual' | 'comprometido'
  economia_mes: number | null
  ganho_unico: number | null
  resta_parcelas: number | null
  economia_limitada_ao_gasto: boolean
  confianca: string
}

interface Sugestoes {
  ok: boolean
  vazio?: boolean
  com_ia: boolean
  meses_de_historico: number
  sugestoes: Sugestao[]
  economia_recorrente_mes: number
  ganho_pontual: number
  a_liberar_parcelas: number
}

interface Conferencia {
  ok: boolean
  cartoes: { final: string; esperado: number; lido: number; diferenca: number }[]
  soma_lancamentos: number
  total_lancamentos_pdf: number | null
  diferenca_lancamentos: number
  soma_proximas: number
  total_proxima_fatura_pdf: number | null
  diferenca_proximas: number | null
  itens_lancamento: number
  itens_proxima_fatura: number
}

interface Preview {
  ok: boolean
  msg?: string
  cabecalho: { rotulo: string; final: string | null; data_vencimento: string; total_fatura: number | null }
  itens: Item[]
  conferencia: Conferencia
  origens: Record<string, number>
  categorias: string[]
  ja_importada: { id: number; arquivo: string; criado_em: string } | null
}

/** As duas perguntas que o usuário pediu, deliberadamente separadas: cortar um
 *  hábito não é a mesma decisão que trocar de fornecedor. */
const PERGUNTAS = {
  necessidade: { titulo: 'Precisava gastar isso?', ajuda: 'O gasto podia não ter acontecido, ou acontecer menos vezes.' },
  preco: { titulo: 'Dava para gastar menos na mesma coisa?', ajuda: 'Mesmo consumo, fornecedor ou plano mais barato.' }
} as const

const RECORRENCIA = {
  recorrente: { label: 'todo mês', cor: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30', ajuda: '3+ compras no mês: a economia se repete.' },
  pontual: { label: 'uma vez', cor: 'bg-amber-500/15 text-amber-300 border-amber-500/30', ajuda: '1 ou 2 compras: ganho único, não multiplique por 12.' },
  comprometido: { label: 'já contratado', cor: 'bg-zinc-700/40 text-zinc-300 border-zinc-600', ajuda: 'Tem parcela em andamento: só volta quando terminar.' }
} as const

export function Cartoes({ active }: { active?: boolean }): JSX.Element {
  const [aba, setAba] = useState<'analise' | 'economizar' | 'lancamentos' | 'importar'>('analise')
  const [analise, setAnalise] = useState<Analise | null>(null)
  const [itens, setItens] = useState<Item[]>([])
  const [categorias, setCategorias] = useState<string[]>([])
  const [mes, setMes] = useState<string>('')
  const [carregando, setCarregando] = useState(false)
  const [erro, setErro] = useState('')

  const [sug, setSug] = useState<Sugestoes | null>(null)
  const [carregandoSug, setCarregandoSug] = useState(false)

  const [arquivo, setArquivo] = useState<File | null>(null)
  const [usarIa, setUsarIa] = useState(true)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [rotulo, setRotulo] = useState('')
  const [ocupado, setOcupado] = useState(false)
  const [msg, setMsg] = useState('')

  async function carregar(mesRef?: string) {
    setCarregando(true); setErro('')
    try {
      const a = await api.cartoes.analise(mesRef)
      setAnalise(a)
      const alvo = mesRef || a.mes_ref
      if (alvo) {
        setMes(alvo)
        const r = await api.cartoes.itens(alvo)
        setItens(r.itens || [])
        setCategorias(r.categorias || [])
      }
    } catch (e) {
      setErro(e instanceof Error ? e.message : String(e))
    } finally {
      setCarregando(false)
    }
  }

  async function carregarSugestoes(mesRef?: string) {
    setCarregandoSug(true); setErro('')
    try {
      setSug(await api.cartoes.sugestoes(mesRef || mes))
    } catch (e) {
      setErro(e instanceof Error ? e.message : String(e))
    } finally {
      setCarregandoSug(false)
    }
  }

  useEffect(() => { if (active && !analise) carregar() }, [active])
  useEffect(() => { if (aba === 'economizar' && !sug && !carregandoSug) carregarSugestoes() }, [aba])

  async function rodarPreview() {
    if (!arquivo) return
    setOcupado(true); setMsg(''); setPreview(null)
    try {
      const p = await api.cartoes.importarPreview(arquivo, usarIa)
      if (!p.ok) { setMsg(p.msg || 'Não consegui ler a fatura.'); return }
      setPreview(p); setRotulo(p.cabecalho.rotulo)
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e))
    } finally { setOcupado(false) }
  }

  async function confirmar(forcar = false) {
    if (!arquivo) return
    setOcupado(true); setMsg('')
    try {
      const r = await api.cartoes.importarConfirmar(arquivo, usarIa, rotulo, forcar)
      if (!r.ok) { setMsg(r.msg || 'Não consegui gravar.'); return }
      setMsg(r.msg); setPreview(null); setArquivo(null); setSug(null)
      await carregar()
      setAba('analise')
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e))
    } finally { setOcupado(false) }
  }

  async function mudarCategoria(item: Item, categoria: string) {
    if (!item.id) return
    setItens(prev => prev.map(i => i.id === item.id
      ? { ...i, categoria, origem_categoria: 'manual' } : i))
    try {
      await api.cartoes.recategorizar(item.id, categoria)
      setSug(null)
      await carregar(mes)
    } catch (e) {
      setErro(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="h-full flex flex-col px-6 py-5 gap-4">
      <div className="flex items-baseline gap-4">
        <h1 className="text-xl font-semibold">Cartões de crédito</h1>
        {analise?.mes_ref && (
          <select
            value={mes}
            onChange={e => { setMes(e.target.value); setSug(null); carregar(e.target.value) }}
            className="bg-zinc-900 border border-zinc-800 rounded px-2 py-1 text-sm"
          >
            {(analise.meses || []).map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        )}
        <div className="ml-auto flex gap-1">
          {([['analise', 'Onde gastei'], ['economizar', 'Onde economizar'],
             ['lancamentos', 'Lançamentos'], ['importar', 'Importar fatura']] as const)
            .map(([id, label]) => (
              <button
                key={id} onClick={() => setAba(id)}
                className={cn('px-3 py-1.5 text-sm rounded border',
                  aba === id ? 'bg-zinc-800 border-zinc-700 text-zinc-100'
                    : 'border-transparent text-zinc-400 hover:text-zinc-200')}
              >{label}</button>
            ))}
        </div>
      </div>

      {erro && <div className="text-sm text-rose-400 border border-rose-500/30 rounded p-2">{erro}</div>}

      <div className="flex-1 overflow-auto">
        {aba === 'analise' && (
          <AbaAnalise analise={analise} itens={itens} carregando={carregando} />
        )}

        {aba === 'economizar' && (
          <AbaEconomizar
            sug={sug} carregando={carregandoSug}
            onAtualizar={() => carregarSugestoes()}
          />
        )}

        {aba === 'lancamentos' && (
          <ListaLancamentos itens={itens} categorias={categorias} onMudar={mudarCategoria} />
        )}

        {aba === 'importar' && (
          <div className="max-w-3xl space-y-4">
            <div className="flex items-center gap-3">
              <input
                type="file" accept="application/pdf"
                onChange={e => { setArquivo(e.target.files?.[0] || null); setPreview(null) }}
                className="text-sm file:mr-3 file:px-3 file:py-1.5 file:rounded file:border-0
                           file:bg-zinc-800 file:text-zinc-200 text-zinc-400"
              />
              <label className="flex items-center gap-1.5 text-sm text-zinc-400">
                <input type="checkbox" checked={usarIa} onChange={e => setUsarIa(e.target.checked)} />
                usar IA
              </label>
              <button
                onClick={rodarPreview} disabled={!arquivo || ocupado}
                className="px-3 py-1.5 text-sm rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40"
              >{ocupado ? 'Lendo…' : 'Ler fatura'}</button>
            </div>

            <p className="text-xs text-zinc-500">
              Na importação a IA só normaliza o nome do estabelecimento e refina a categoria.
              Valor, data e parcela saem do parser — ela não lê valores nem inventa lançamento.
            </p>

            {msg && <div className="text-sm text-amber-300 border border-amber-500/30 rounded p-2">{msg}</div>}

            {preview && (
              <div className="space-y-4">
                <div className="flex items-end gap-4">
                  <label className="text-sm">
                    <span className="block text-xs text-zinc-500 mb-1">Nome do cartão</span>
                    <input
                      value={rotulo} onChange={e => setRotulo(e.target.value)}
                      className="bg-zinc-900 border border-zinc-800 rounded px-2 py-1"
                    />
                  </label>
                  <div className="text-sm text-zinc-400">
                    vence {preview.cabecalho.data_vencimento} ·{' '}
                    {formatBRL(preview.cabecalho.total_fatura || 0)}
                  </div>
                </div>
                <p className="text-xs text-zinc-500 -mt-2">
                  O nome forma a chave da fatura: se mudar entre duas importações da mesma
                  fatura, ela entra duas vezes e a análise conta o gasto em dobro.
                </p>

                <Conferir c={preview.conferencia} />

                <div className="text-sm text-zinc-400">
                  {preview.conferencia.itens_lancamento} lançamentos ·{' '}
                  {preview.conferencia.itens_proxima_fatura} parcelas futuras · origem da
                  categoria: {Object.entries(preview.origens).map(([k, v]) => `${k} ${v}`).join(', ')}
                </div>

                {preview.ja_importada && (
                  <div className="text-sm text-amber-300">
                    Esta fatura já foi importada em {preview.ja_importada.criado_em}.
                    Confirmar vai <strong>substituir</strong> a anterior.
                  </div>
                )}

                <div className="flex gap-2">
                  <button
                    onClick={() => confirmar(false)} disabled={ocupado}
                    className="px-3 py-1.5 text-sm rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40"
                  >Confirmar e gravar</button>
                  {!preview.conferencia.ok && (
                    <button
                      onClick={() => confirmar(true)} disabled={ocupado}
                      className="px-3 py-1.5 text-sm rounded border border-rose-500/40 text-rose-300"
                    >Gravar mesmo sem fechar</button>
                  )}
                  <button
                    onClick={() => { setPreview(null); setArquivo(null) }}
                    className="px-3 py-1.5 text-sm rounded border border-zinc-800 text-zinc-400"
                  >Cancelar</button>
                </div>
              </div>
            )}

            <FaturasImportadas onMudou={() => { setSug(null); carregar() }} />
          </div>
        )}
      </div>
    </div>
  )
}

/* ── onde gastei ─────────────────────────────────────────────────────────── */

function AbaAnalise({ analise, itens, carregando }: {
  analise: Analise | null; itens: Item[]; carregando: boolean
}): JSX.Element {
  const [aberta, setAberta] = useState<string | null>(null)
  const [abertoEst, setAbertoEst] = useState<string | null>(null)

  /** categoria → estabelecimento → lançamentos. A categoria é o resumo; a
   *  decisão acontece no estabelecimento, e a prova está no lançamento. */
  const arvore = useMemo(() => {
    const m = new Map<string, Map<string, Item[]>>()
    for (const i of itens) {
      if (i.secao !== 'lancamento') continue
      const cat = i.categoria || '—'
      const est = i.estabelecimento_norm || i.estabelecimento
      if (!m.has(cat)) m.set(cat, new Map())
      const sub = m.get(cat)!
      if (!sub.has(est)) sub.set(est, [])
      sub.get(est)!.push(i)
    }
    return m
  }, [itens])

  if (carregando) return <div className="text-sm text-zinc-500">Carregando…</div>
  if (!analise || analise.vazio) {
    return (
      <div className="text-sm text-zinc-500">
        Nenhuma fatura importada ainda. Use a aba <strong>Importar fatura</strong>.
      </div>
    )
  }

  const maxCat = Math.max(1, ...analise.por_categoria.map(g => g.valor))
  const d = analise.decisao
  const terminando = analise.parcelamentos.filter(p => p.termina_agora)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-8">
        <div>
          <div className="text-xs uppercase text-zinc-500">Gasto no mês</div>
          <div className="text-2xl tabular-nums">{formatBRL(analise.total_lancamentos)}</div>
          {!!analise.total_encargos && (
            <div className="text-xs text-zinc-500">
              + {formatBRL(analise.total_encargos)} de encargos ={' '}
              {formatBRL(analise.total_lancamentos + analise.total_encargos)} nas faturas
            </div>
          )}
        </div>
        <div>
          <div className="text-xs uppercase text-zinc-500">Próxima fatura</div>
          <div className="text-2xl tabular-nums text-amber-300">
            {formatBRL(analise.total_comprometido)}
          </div>
          <div className="text-xs text-zinc-500">parcelas que chegam no mês que vem</div>
        </div>
        <div>
          <div className="text-xs uppercase text-zinc-500">Ainda vai chegar, no total</div>
          <div className="text-2xl tabular-nums text-rose-300">
            {formatBRL(analise.total_a_chegar)}
          </div>
          <div className="text-xs text-zinc-500">
            todas as parcelas contratadas · estimativa supondo parcelas iguais
          </div>
        </div>
      </div>

      {/* Extrato em aberto é número provisório. Apresentá-lo como definitivo
          seria o mesmo erro de somar ganho pontual com economia mensal. */}
      {analise.tem_aberta && (
        <div className="text-sm text-amber-300 border border-amber-500/30 rounded p-2">
          Um dos cartões deste mês veio como <strong>extrato em aberto</strong>, não
          fatura fechada: os valores ainda mudam até o fechamento, e não há data de
          vencimento no documento.
        </div>
      )}

      {(analise.faturas || []).length > 1 && (
        <section>
          <h2 className="text-sm font-medium mb-2">
            Faturas deste mês
            <span className="text-xs text-zinc-500 font-normal"> — {analise.faturas.length} cartões</span>
          </h2>
          <table className="w-full text-sm max-w-3xl">
            <tbody>
              {analise.faturas.map((f, n) => (
                <tr key={n} className="border-t border-zinc-900">
                  <td className="py-1">{f.cartao}</td>
                  <td className="text-xs text-zinc-600">{f.emissor}</td>
                  <td className="text-xs text-zinc-500">
                    {f.situacao === 'aberta'
                      ? <span className="px-1.5 py-0.5 rounded border border-amber-500/30 bg-amber-500/15 text-amber-300">
                          aberta · extrato de {f.data_extrato}
                        </span>
                      : <>vence {f.data_vencimento}</>}
                  </td>
                  <td className="text-right tabular-nums">{formatBRL(f.total_fatura || 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {/* Quanto da fatura ainda era escolha. Sem esta conta, "precisava gastar
          isso?" é perguntado sobre dinheiro que já não estava em disputa. */}
      <section className="text-sm">
        <h2 className="font-medium mb-1">Quanto disso foi decidido neste mês</h2>
        <div className="flex flex-wrap gap-6 text-zinc-300">
          <span>à vista <strong className="tabular-nums">{formatBRL(d.avista)}</strong>
            <span className="text-zinc-600"> ({d.avista_n})</span></span>
          <span>parcelamento novo <strong className="tabular-nums">{formatBRL(d.parcela_nova)}</strong>
            <span className="text-zinc-600"> ({d.parcela_nova_n})</span></span>
          <span className="text-zinc-400">parcela de decisão antiga{' '}
            <strong className="tabular-nums">{formatBRL(d.parcela_antiga)}</strong>
            <span className="text-zinc-600"> ({d.parcela_antiga_n})</span></span>
        </div>
      </section>

      <section>
        <h2 className="text-sm font-medium mb-2">
          Onde estou gastando <span className="text-xs text-zinc-500 font-normal">— clique para abrir</span>
        </h2>
        <div className="space-y-1">
          {analise.por_categoria.map(g => {
            const sub = arvore.get(g.chave)
            const abertaAqui = aberta === g.chave
            const ests = sub ? [...sub.entries()]
              .map(([nome, its]) => ({
                nome, n: its.length,
                valor: its.reduce((s, i) => s + (i.valor || 0), 0), its
              }))
              .sort((a, b) => b.valor - a.valor) : []
            return (
              <div key={g.chave}>
                <button
                  onClick={() => { setAberta(abertaAqui ? null : g.chave); setAbertoEst(null) }}
                  className="w-full flex items-center gap-3 text-sm hover:bg-zinc-900/40 rounded px-1 py-0.5"
                >
                  <span className="w-4 text-zinc-600 text-xs">{abertaAqui ? '▾' : '▸'}</span>
                  <span className="w-40 shrink-0 text-left text-zinc-300">{g.chave}</span>
                  <div className="flex-1 h-4 bg-zinc-900 rounded overflow-hidden">
                    <div className="h-full bg-sky-600/60" style={{ width: `${(g.valor / maxCat) * 100}%` }} />
                  </div>
                  <span className="w-12 text-right text-xs text-zinc-600">{g.n}x</span>
                  <span className="w-28 text-right tabular-nums">{formatBRL(g.valor)}</span>
                </button>

                {abertaAqui && (
                  <div className="ml-8 mt-1 mb-2 border-l border-zinc-800 pl-3 space-y-0.5">
                    {ests.map(e => (
                      <div key={e.nome}>
                        <button
                          onClick={() => setAbertoEst(abertoEst === g.chave + e.nome ? null : g.chave + e.nome)}
                          className="w-full flex items-center gap-2 text-sm hover:bg-zinc-900/40 rounded px-1"
                        >
                          <span className="w-3 text-zinc-700 text-xs">
                            {abertoEst === g.chave + e.nome ? '▾' : '▸'}
                          </span>
                          <span className="text-left text-zinc-300 truncate">{e.nome}</span>
                          <span className="ml-auto text-xs text-zinc-600">{e.n}x</span>
                          <span className="w-20 text-right text-xs text-zinc-500 tabular-nums">
                            méd {formatBRL(e.valor / e.n)}
                          </span>
                          <span className="w-24 text-right tabular-nums">{formatBRL(e.valor)}</span>
                        </button>
                        {abertoEst === g.chave + e.nome && (
                          <div className="ml-6 border-l border-zinc-800 pl-3 py-1">
                            {e.its.slice().sort((a, b) => (b.data_compra || '').localeCompare(a.data_compra || ''))
                              .map(i => (
                                <div key={i.id} className="flex gap-3 text-xs text-zinc-400 py-0.5">
                                  <span className="w-12 tabular-nums">
                                    {(i.data_compra || '').slice(8, 10)}/{(i.data_compra || '').slice(5, 7)}
                                  </span>
                                  <span className="truncate">{i.estabelecimento}</span>
                                  {i.parcela_n && (
                                    <span className="text-zinc-600">{i.parcela_n}/{i.parcela_total}</span>
                                  )}
                                  <span className="text-zinc-600 truncate">{i.portador}</span>
                                  <span className="ml-auto tabular-nums text-zinc-300">{formatBRL(i.valor)}</span>
                                </div>
                              ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </section>

      <section>
        <h2 className="text-sm font-medium mb-2">
          Parcelamentos em andamento
          <span className="text-xs text-zinc-500 font-normal"> — {analise.parcelamentos.length} ativos</span>
        </h2>
        {!!terminando.length && (
          <div className="text-xs text-emerald-300 mb-2">
            {terminando.length} termina{terminando.length > 1 ? 'm' : ''} nesta fatura:{' '}
            {formatBRL(terminando.reduce((s, p) => s + p.valor_mes, 0))} por mês voltam ao
            orçamento sem você cortar nada.
          </div>
        )}
        <table className="w-full text-sm">
          <thead className="text-xs uppercase text-zinc-500">
            <tr className="text-left">
              <th className="font-medium">Estabelecimento</th>
              <th className="font-medium">Parcela</th>
              <th className="font-medium text-right">Por mês</th>
              <th className="font-medium text-right">Faltam</th>
              <th className="font-medium text-right pr-2">Ainda vai chegar</th>
            </tr>
          </thead>
          <tbody>
            {analise.parcelamentos.map((p, n) => (
              <tr key={n} className={cn('border-t border-zinc-900',
                p.termina_agora && 'text-emerald-300')}>
                <td className="py-1 truncate">{p.estabelecimento}</td>
                <td className="text-zinc-500 tabular-nums">{p.parcela_n}/{p.parcela_total}</td>
                <td className="text-right tabular-nums">{formatBRL(p.valor_mes)}</td>
                <td className="text-right tabular-nums text-zinc-500">{p.faltam}</td>
                <td className="text-right tabular-nums pr-2">{formatBRL(p.resta)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <div className="grid grid-cols-2 gap-8">
        <section>
          <h2 className="text-sm font-medium mb-2">Por portador</h2>
          {analise.por_portador.map(g => (
            <div key={g.chave} className="flex text-sm border-t border-zinc-900 py-1">
              <span className="truncate text-zinc-300">{g.chave}</span>
              <span className="ml-auto pl-3 text-xs text-zinc-600">{g.n}x</span>
              <span className="w-24 text-right tabular-nums">{formatBRL(g.valor)}</span>
            </div>
          ))}
        </section>
        <section>
          <h2 className="text-sm font-medium mb-2">Por cartão</h2>
          {analise.por_cartao.map(g => (
            <div key={g.chave} className="flex text-sm border-t border-zinc-900 py-1">
              <span className="truncate text-zinc-300">{g.chave}</span>
              <span className="ml-auto pl-3 text-xs text-zinc-600">{g.n}x</span>
              <span className="w-24 text-right tabular-nums">{formatBRL(g.valor)}</span>
            </div>
          ))}
        </section>
      </div>
    </div>
  )
}

/* ── onde economizar ─────────────────────────────────────────────────────── */

function AbaEconomizar({ sug, carregando, onAtualizar }: {
  sug: Sugestoes | null; carregando: boolean; onAtualizar: () => void
}): JSX.Element {
  if (carregando) {
    return <div className="text-sm text-zinc-500">Analisando a fatura e pedindo conselho…</div>
  }
  if (!sug || sug.vazio) {
    return <div className="text-sm text-zinc-500">Importe uma fatura para receber sugestões.</div>
  }

  return (
    <div className="space-y-6 max-w-5xl">
      {/* Três totais, nunca um só: somá-los produziria uma promessa que os
          dados não sustentam. */}
      <div className="flex flex-wrap gap-8">
        <div>
          <div className="text-xs uppercase text-zinc-500">Economia recorrente</div>
          <div className="text-2xl tabular-nums text-emerald-300">
            {formatBRL(sug.economia_recorrente_mes)}<span className="text-sm text-zinc-500">/mês</span>
          </div>
          <div className="text-xs text-zinc-500">hábito: repete todo mês</div>
        </div>
        <div>
          <div className="text-xs uppercase text-zinc-500">Ganho pontual</div>
          <div className="text-2xl tabular-nums text-amber-300">{formatBRL(sug.ganho_pontual)}</div>
          <div className="text-xs text-zinc-500">uma vez — não multiplique por 12</div>
        </div>
        {!!sug.a_liberar_parcelas && (
          <div>
            <div className="text-xs uppercase text-zinc-500">A liberar</div>
            <div className="text-2xl tabular-nums text-zinc-300">{formatBRL(sug.a_liberar_parcelas)}</div>
            <div className="text-xs text-zinc-500">quando as parcelas terminarem</div>
          </div>
        )}
        <button
          onClick={onAtualizar}
          className="self-start ml-auto px-3 py-1.5 text-sm rounded border border-zinc-800 text-zinc-400 hover:text-zinc-200"
        >Reanalisar</button>
      </div>

      {!sug.com_ia && (
        <div className="text-sm text-amber-300 border border-amber-500/30 rounded p-2">
          A IA não respondeu — sem GEMINI_API_KEY ou sem rede. As contas da aba
          "Onde gastei" continuam valendo; só o conselho ficou de fora.
        </div>
      )}
      {sug.meses_de_historico === 0 && (
        <p className="text-xs text-zinc-500">
          Só um mês importado: "repete todo mês" está inferido da frequência dentro da
          própria fatura, não medido entre meses. Importe outra fatura e a distinção entre
          hábito e evento fica muito mais firme.
        </p>
      )}

      {(['necessidade', 'preco'] as const).map(tipo => {
        const grupo = sug.sugestoes.filter(s => s.tipo === tipo)
        if (!grupo.length) return null
        return (
          <section key={tipo}>
            <h2 className="text-sm font-medium">{PERGUNTAS[tipo].titulo}</h2>
            <p className="text-xs text-zinc-500 mb-2">{PERGUNTAS[tipo].ajuda}</p>
            <div className="space-y-2">
              {grupo.map((s, n) => {
                const r = RECORRENCIA[s.recorrencia] || RECORRENCIA.pontual
                const valor = s.economia_mes !== null ? `${formatBRL(s.economia_mes)}/mês`
                  : s.ganho_unico !== null ? `${formatBRL(s.ganho_unico)} uma vez`
                    : s.resta_parcelas !== null ? `${formatBRL(s.resta_parcelas)} a liberar` : '—'
                return (
                  <div key={n} className="border border-zinc-800 rounded p-3">
                    <div className="flex items-baseline gap-2 flex-wrap">
                      <span className="font-medium text-zinc-100">{s.alvo}</span>
                      <span className={cn('text-xs px-1.5 py-0.5 rounded border', r.cor)} title={r.ajuda}>
                        {r.label}
                      </span>
                      <span className="text-xs text-zinc-600" title="confiança da sugestão">
                        confiança {s.confianca}
                      </span>
                      <span className="ml-auto text-sm tabular-nums text-emerald-300">{valor}</span>
                    </div>
                    <div className="text-sm text-zinc-400 mt-1">{s.diagnostico}</div>
                    <div className="text-sm text-zinc-200 mt-1">→ {s.acao}</div>
                    <div className="text-xs text-zinc-600 mt-1">
                      gasto no mês: {formatBRL(s.gasto_no_mes)}
                      {s.economia_limitada_ao_gasto && ' · economia limitada ao que foi gasto'}
                    </div>
                  </div>
                )
              })}
            </div>
          </section>
        )
      })}
    </div>
  )
}

/* ── lançamentos ─────────────────────────────────────────────────────────── */

function ListaLancamentos({ itens, categorias, onMudar }: {
  itens: Item[]; categorias: string[]; onMudar: (i: Item, c: string) => void
}): JSX.Element {
  return (
    <table className="w-full text-sm">
      <thead className="text-xs uppercase text-zinc-500 sticky top-0 bg-zinc-950">
        <tr className="text-left">
          <th className="py-2 font-medium">Data</th>
          <th className="font-medium">Estabelecimento</th>
          <th className="font-medium">Portador</th>
          <th className="font-medium">Categoria</th>
          <th className="font-medium text-right pr-3">Valor</th>
        </tr>
      </thead>
      <tbody>
        {itens.filter(i => i.secao === 'lancamento').map(i => (
          <tr key={i.id} className="border-t border-zinc-900 hover:bg-zinc-900/40">
            <td className="py-1.5 text-zinc-400 whitespace-nowrap">
              {(i.data_compra || '').slice(8, 10)}/{(i.data_compra || '').slice(5, 7)}
            </td>
            <td>
              <span className="text-zinc-200">{i.estabelecimento_norm || i.estabelecimento}</span>
              {i.parcela_n && (
                <span className="ml-2 text-xs text-zinc-500">{i.parcela_n}/{i.parcela_total}</span>
              )}
              {i.estabelecimento_norm && (
                <span className="ml-2 text-xs text-zinc-600">{i.estabelecimento}</span>
              )}
            </td>
            <td className="text-zinc-500 text-xs">{i.portador}</td>
            <td>
              <select
                value={i.categoria || ''}
                onChange={e => onMudar(i, e.target.value)}
                className={cn('bg-transparent border rounded px-1.5 py-0.5 text-xs',
                  i.origem_categoria === 'manual' ? 'border-emerald-600/50 text-emerald-300'
                    : i.origem_categoria === 'ia' ? 'border-violet-600/40 text-violet-300'
                      : 'border-zinc-800 text-zinc-300')}
                title={`origem: ${i.origem_categoria || '—'}` +
                  (i.categoria_fatura ? ` · fatura dizia ${i.categoria_fatura}` : '')}
              >
                {categorias.map(c => <option key={c} value={c}>{c}</option>)}
                <option value="Não classificado">Não classificado</option>
              </select>
            </td>
            <td className="text-right pr-3 tabular-nums">{formatBRL(i.valor)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/* ── importação ──────────────────────────────────────────────────────────── */

/** A fatura traz os próprios totais, então o parser pode ser provado contra ela. */
function Conferir({ c }: { c: Conferencia }): JSX.Element {
  const linha = (label: string, lido: number, pdf: number | null, dif: number | null) => (
    <tr className="border-t border-zinc-900">
      <td className="py-1 text-zinc-400">{label}</td>
      <td className="text-right tabular-nums">{formatBRL(lido)}</td>
      <td className="text-right tabular-nums text-zinc-500">{pdf === null ? '—' : formatBRL(pdf)}</td>
      <td className={cn('text-right tabular-nums pr-2',
        dif === null ? 'text-zinc-600' : Math.abs(dif) < 0.01 ? 'text-emerald-400' : 'text-rose-400')}>
        {dif === null ? '—' : formatBRL(dif)}
      </td>
    </tr>
  )
  return (
    <div className={cn('rounded border p-3',
      c.ok ? 'border-emerald-600/30 bg-emerald-500/5' : 'border-rose-600/40 bg-rose-500/5')}>
      <div className="text-sm font-medium mb-1">
        {c.ok ? 'A fatura fecha com os totais do PDF' : 'A fatura NÃO fecha — confira antes de gravar'}
      </div>
      <table className="w-full text-sm">
        <thead className="text-xs uppercase text-zinc-500">
          <tr className="text-right"><th className="text-left font-medium">conferência</th>
            <th className="font-medium">lido</th><th className="font-medium">no PDF</th>
            <th className="font-medium pr-2">dif</th></tr>
        </thead>
        <tbody>
          {linha('Total dos lançamentos', c.soma_lancamentos, c.total_lancamentos_pdf, c.diferenca_lancamentos)}
          {linha('Próxima fatura', c.soma_proximas, c.total_proxima_fatura_pdf, c.diferenca_proximas)}
          {c.cartoes.map(x => linha(`Cartão final ${x.final}`, x.lido, x.esperado, x.diferenca))}
        </tbody>
      </table>
    </div>
  )
}

function FaturasImportadas({ onMudou }: { onMudou: () => void }): JSX.Element {
  const [faturas, setFaturas] = useState<Record<string, unknown>[]>([])
  useEffect(() => { api.cartoes.faturas().then(r => setFaturas(r.faturas || [])).catch(() => {}) }, [])
  if (!faturas.length) return <div />
  return (
    <div className="pt-4 border-t border-zinc-900">
      <div className="text-xs uppercase text-zinc-500 mb-2">Faturas importadas</div>
      <table className="w-full text-sm">
        <tbody>
          {faturas.map(f => (
            <tr key={String(f.id)} className="border-t border-zinc-900">
              <td className="py-1">{String(f.cartao)}</td>
              <td className="text-zinc-500">{String(f.data_vencimento)}</td>
              <td className="text-zinc-500">{String(f.n_itens)} itens</td>
              <td className="text-right tabular-nums">{formatBRL(Number(f.total_fatura) || 0)}</td>
              <td className="text-right pr-1">
                <button
                  onClick={async () => {
                    await api.cartoes.excluirFatura(Number(f.id))
                    setFaturas(prev => prev.filter(x => x.id !== f.id))
                    onMudou()
                  }}
                  className="text-xs text-zinc-500 hover:text-rose-400"
                >excluir</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
