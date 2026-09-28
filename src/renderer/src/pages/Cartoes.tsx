import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { formatBRL, cn } from '../lib/utils'

/** Análise da fatura de cartão (doc 17).
 *
 *  Três perguntas, três abas: onde gastei, o que já está comprometido e o que
 *  dá para cortar. A importação fica junto porque sem fatura importada não há
 *  nada a responder — e porque a conferência precisa ser vista **antes** de
 *  gravar: um lançamento perdido numa coluna não dá erro, só some.
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

interface Candidato {
  sinal: string
  estabelecimento: string
  valor: number
  detalhe: string
  economia_mes: number
}

interface Analise {
  ok: boolean
  vazio?: boolean
  mes_ref: string | null
  meses: string[]
  total_lancamentos: number
  total_comprometido: number
  total_encargos: number
  por_categoria: Grupo[]
  por_estabelecimento: Grupo[]
  por_portador: Grupo[]
  por_cartao: Grupo[]
  comprometido: Grupo[]
  candidatos: Candidato[]
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

/** O que torna cada candidato acionável — o critério, não só o rótulo. */
const SINAIS: Record<string, { label: string; cor: string; ajuda: string }> = {
  assinatura: {
    label: 'Assinatura', cor: 'bg-violet-500/15 text-violet-300 border-violet-500/30',
    ajuda: 'Mesmo valor, mesmo lugar, 3+ meses seguidos. Cancelável hoje, efeito permanente.'
  },
  repeticao: {
    label: 'Repetição', cor: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
    ajuda: '10+ compras no mês no mesmo lugar. É hábito, não decisão — é onde o corte pesa.'
  },
  salto: {
    label: 'Salto', cor: 'bg-rose-500/15 text-rose-300 border-rose-500/30',
    ajuda: 'Categoria 50%+ acima da média dos meses anteriores. Mudança recente, ainda reversível.'
  },
  parcela_terminando: {
    label: 'Parcela acabando', cor: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
    ajuda: 'Última parcela: esse dinheiro volta ao orçamento mês que vem sem cortar nada.'
  }
}

export function Cartoes({ active }: { active?: boolean }): JSX.Element {
  const [aba, setAba] = useState<'analise' | 'lancamentos' | 'importar'>('analise')
  const [analise, setAnalise] = useState<Analise | null>(null)
  const [itens, setItens] = useState<Item[]>([])
  const [categorias, setCategorias] = useState<string[]>([])
  const [mes, setMes] = useState<string>('')
  const [carregando, setCarregando] = useState(false)
  const [erro, setErro] = useState('')

  // importação
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
      if (a.mes_ref) setMes(a.mes_ref)
    } catch (e) {
      setErro(e instanceof Error ? e.message : String(e))
    } finally {
      setCarregando(false)
    }
  }

  async function carregarItens(mesRef: string) {
    try {
      const r = await api.cartoes.itens(mesRef)
      setItens(r.itens || [])
      setCategorias(r.categorias || [])
    } catch (e) {
      setErro(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => { if (active && !analise) carregar() }, [active])
  useEffect(() => { if (aba === 'lancamentos' && mes) carregarItens(mes) }, [aba, mes])

  async function rodarPreview() {
    if (!arquivo) return
    setOcupado(true); setMsg(''); setPreview(null)
    try {
      const p = await api.cartoes.importarPreview(arquivo, usarIa)
      if (!p.ok) { setMsg(p.msg || 'Não consegui ler a fatura.'); return }
      setPreview(p)
      setRotulo(p.cabecalho.rotulo)
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e))
    } finally {
      setOcupado(false)
    }
  }

  async function confirmar(forcar = false) {
    if (!arquivo) return
    setOcupado(true); setMsg('')
    try {
      const r = await api.cartoes.importarConfirmar(arquivo, usarIa, rotulo, forcar)
      if (!r.ok) { setMsg(r.msg || 'Não consegui gravar.'); return }
      setMsg(r.msg)
      setPreview(null); setArquivo(null)
      await carregar()
      setAba('analise')
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e))
    } finally {
      setOcupado(false)
    }
  }

  async function mudarCategoria(item: Item, categoria: string) {
    if (!item.id) return
    setItens(prev => prev.map(i => i.id === item.id
      ? { ...i, categoria, origem_categoria: 'manual' } : i))
    try {
      await api.cartoes.recategorizar(item.id, categoria)
      await carregarItens(mes)
      await carregar(mes)
    } catch (e) {
      setErro(e instanceof Error ? e.message : String(e))
    }
  }

  const maxCat = Math.max(1, ...(analise?.por_categoria || []).map(g => g.valor))

  return (
    <div className="h-full flex flex-col px-6 py-5 gap-4">
      <div className="flex items-baseline gap-4">
        <h1 className="text-xl font-semibold">Cartões de crédito</h1>
        {analise?.mes_ref && (
          <select
            value={mes}
            onChange={e => { setMes(e.target.value); carregar(e.target.value) }}
            className="bg-zinc-900 border border-zinc-800 rounded px-2 py-1 text-sm"
          >
            {(analise.meses || []).map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        )}
        <div className="ml-auto flex gap-1">
          {([['analise', 'Análise'], ['lancamentos', 'Lançamentos'], ['importar', 'Importar fatura']] as const)
            .map(([id, label]) => (
              <button
                key={id}
                onClick={() => setAba(id)}
                className={cn('px-3 py-1.5 text-sm rounded border',
                  aba === id
                    ? 'bg-zinc-800 border-zinc-700 text-zinc-100'
                    : 'border-transparent text-zinc-400 hover:text-zinc-200')}
              >{label}</button>
            ))}
        </div>
      </div>

      {erro && <div className="text-sm text-rose-400 border border-rose-500/30 rounded p-2">{erro}</div>}

      <div className="flex-1 overflow-auto">
        {aba === 'analise' && (
          <AbaAnalise analise={analise} carregando={carregando} maxCat={maxCat} />
        )}

        {aba === 'lancamentos' && (
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
                      <span className="ml-2 text-xs text-zinc-500">
                        {i.parcela_n}/{i.parcela_total}
                      </span>
                    )}
                    {i.estabelecimento_norm && (
                      <span className="ml-2 text-xs text-zinc-600">{i.estabelecimento}</span>
                    )}
                  </td>
                  <td className="text-zinc-500 text-xs">{i.portador}</td>
                  <td>
                    <select
                      value={i.categoria || ''}
                      onChange={e => mudarCategoria(i, e.target.value)}
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
                onClick={rodarPreview}
                disabled={!arquivo || ocupado}
                className="px-3 py-1.5 text-sm rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40"
              >{ocupado ? 'Lendo…' : 'Ler fatura'}</button>
            </div>

            <p className="text-xs text-zinc-500">
              A IA só normaliza o nome do estabelecimento e refina a categoria. Valor, data e
              parcela saem do parser — ela não lê valores nem inventa lançamento.
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
                  categoria:{' '}
                  {Object.entries(preview.origens).map(([k, v]) => `${k} ${v}`).join(', ')}
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

            <FaturasImportadas onMudou={() => carregar()} />
          </div>
        )}
      </div>
    </div>
  )
}

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

function AbaAnalise({ analise, carregando, maxCat }: {
  analise: Analise | null; carregando: boolean; maxCat: number
}): JSX.Element {
  if (carregando) return <div className="text-sm text-zinc-500">Carregando…</div>
  if (!analise || analise.vazio) {
    return (
      <div className="text-sm text-zinc-500">
        Nenhuma fatura importada ainda. Use a aba <strong>Importar fatura</strong>.
      </div>
    )
  }
  return (
    <div className="space-y-6">
      <div className="flex gap-8">
        <div>
          <div className="text-xs uppercase text-zinc-500">Gasto no mês</div>
          <div className="text-2xl tabular-nums">{formatBRL(analise.total_lancamentos)}</div>
          {/* Sem esta linha o total não fecha com o PDF, e um número que não
              reconcilia com o papel do banco não merece confiança. */}
          {!!analise.total_encargos && (
            <div className="text-xs text-zinc-500">
              + {formatBRL(analise.total_encargos)} de encargos ={' '}
              {formatBRL(analise.total_lancamentos + analise.total_encargos)} nas faturas
            </div>
          )}
        </div>
        <div>
          <div className="text-xs uppercase text-zinc-500">Já comprometido (próxima fatura)</div>
          <div className="text-2xl tabular-nums text-amber-300">
            {formatBRL(analise.total_comprometido)}
          </div>
          <div className="text-xs text-zinc-500">sai independente de qualquer decisão deste mês</div>
        </div>
      </div>

      {!!analise.candidatos.length && (
        <section>
          <h2 className="text-sm font-medium mb-2">Onde dá para cortar</h2>
          <div className="space-y-1">
            {analise.candidatos.map((c, n) => {
              const s = SINAIS[c.sinal] || { label: c.sinal, cor: 'bg-zinc-800 text-zinc-300 border-zinc-700', ajuda: '' }
              return (
                <div key={n} className="flex items-center gap-3 text-sm border-t border-zinc-900 py-1.5">
                  <span className={cn('text-xs px-1.5 py-0.5 rounded border shrink-0', s.cor)} title={s.ajuda}>
                    {s.label}
                  </span>
                  <span className="text-zinc-200">{c.estabelecimento}</span>
                  <span className="text-zinc-500 text-xs">{c.detalhe}</span>
                  <span className="ml-auto tabular-nums text-zinc-400">{formatBRL(c.valor)}</span>
                </div>
              )
            })}
          </div>
        </section>
      )}

      <section>
        <h2 className="text-sm font-medium mb-2">Onde estou gastando</h2>
        <div className="space-y-1">
          {analise.por_categoria.map(g => (
            <div key={g.chave} className="flex items-center gap-3 text-sm">
              <span className="w-44 shrink-0 text-zinc-300">{g.chave}</span>
              <div className="flex-1 h-4 bg-zinc-900 rounded overflow-hidden">
                <div className="h-full bg-sky-600/60" style={{ width: `${(g.valor / maxCat) * 100}%` }} />
              </div>
              <span className="w-12 text-right text-xs text-zinc-600">{g.n}x</span>
              <span className="w-28 text-right tabular-nums">{formatBRL(g.valor)}</span>
            </div>
          ))}
        </div>
      </section>

      <div className="grid grid-cols-2 gap-8">
        <section>
          <h2 className="text-sm font-medium mb-2">Por estabelecimento</h2>
          {analise.por_estabelecimento.slice(0, 15).map(g => (
            <div key={g.chave} className="flex text-sm border-t border-zinc-900 py-1">
              <span className="truncate text-zinc-300">{g.chave}</span>
              <span className="ml-auto pl-3 text-xs text-zinc-600">{g.n}x</span>
              <span className="w-24 text-right tabular-nums">{formatBRL(g.valor)}</span>
            </div>
          ))}
        </section>
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
      </div>
    </div>
  )
}
