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
  /** Estampado pelo servidor. A regra de segmento depende de marcação de local,
   *  flags da categoria e corte de ticket — reimplementá-la aqui faria a mesma
   *  regra existir em duas linguagens, e divergir na primeira correção. */
  segmento?: string | null
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

/** Realizado de um (categoria, segmento), com a meta ao lado.
 *
 *  A alavanca é da categoria e a decisão é do segmento: almoço de dia útil e
 *  jantar de sábado são o mesmo "Restaurante e bar" e metas completamente
 *  diferentes — 66 compras de ticket R$ 60 contra 10 de ticket R$ 116.
 */
interface Segmento {
  categoria: string
  segmento: string
  n: number
  valor: number
  ticket_medio: number
  alavanca: string
  segmentacao: string
  observacao: string | null
  /** O que o % morde: média dos meses anteriores, ou o próprio mês enquanto não
   *  há histórico. A tela mostra qual das duas — um percentual sem a base à
   *  vista não quer dizer nada. */
  baseline: number
  baseline_origem: string
  corte_sugerido: number
  taticas: string[]
  tatica: string | null
  tatica_salva: boolean
  meta_tipo: string | null
  meta_bruta: number | null
  meta_valor: number | null
  economia: number | null
  meta_indisponivel: string | null
  excedeu: number | null
}

interface FaturaResumo {
  cartao: string
  emissor: string | null
  situacao: string | null
  data_vencimento: string | null
  data_extrato: string | null
  total_fatura: number | null
  total_lancamentos: number | null
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
  segmentos: Segmento[]
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
  sem_alavanca: string[]
  aguardando_detalhe: string[]
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

  /** Grava a meta de redução de um segmento. Mexer na tática ou no % salva os
   *  dois juntos, usando o % vigente — assim qualquer dos dois controles
   *  produz um orçamento válido, sem obrigar a preencher o outro primeiro. */
  async function salvarMeta(s: Segmento, pct: number, tatica: string | null) {
    try {
      await api.cartoes.salvarMeta({
        categoria: s.categoria, segmento: s.segmento,
        tipo: 'percentual', valor: pct, tatica
      })
      await carregar(mes)
    } catch (e) {
      setErro(e instanceof Error ? e.message : String(e))
    }
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
          <AbaAnalise analise={analise} itens={itens} carregando={carregando}
                      onSalvarMeta={salvarMeta} />
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

function AbaAnalise({ analise, itens, carregando, onSalvarMeta }: {
  analise: Analise | null; itens: Item[]; carregando: boolean
  onSalvarMeta: (s: Segmento, pct: number, tatica: string | null) => void
}): JSX.Element {
  if (carregando) return <div className="text-sm text-zinc-500">Carregando…</div>
  if (!analise || analise.vazio) {
    return (
      <div className="text-sm text-zinc-500">
        Nenhuma fatura importada ainda. Use a aba <strong>Importar fatura</strong>.
      </div>
    )
  }

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
                  {/* No extrato aberto o total da fatura inclui saldo anterior
                      que o PDF não detalha; a análise conta só o consumo, e
                      mostrar o total aqui seria um número que não reconcilia. */}
                  <td className="text-right tabular-nums">
                    {f.situacao === 'aberta' && f.total_lancamentos !== null ? (
                      <span title={'consumo do período. A fatura soma '
                        + formatBRL(f.total_fatura || 0)
                        + ', incluindo saldo anterior não detalhado no PDF.'}>
                        {formatBRL(f.total_lancamentos)}
                      </span>
                    ) : formatBRL(f.total_fatura || 0)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {analise.tem_aberta && (
            <p className="text-xs text-zinc-500 mt-1">
              Para o extrato aberto está o <strong>consumo do período</strong> — é o que
              entra na análise. O total da fatura dele inclui saldo anterior que o PDF
              não detalha lançamento a lançamento.
            </p>
          )}
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

      <SecaoEconomia
        segmentos={analise.segmentos || []}
        itens={itens}
        onSalvarMeta={onSalvarMeta}
      />

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

/** A alavanca de cada categoria, e o que ela autoriza. */
const ALAVANCAS: Record<string, { label: string; cor: string; ajuda: string }> = {
  volume: { label: 'volume', cor: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
    ajuda: 'Dá para gastar menos vezes.' },
  preco: { label: 'preço', cor: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
    ajuda: 'Mesma coisa, mais barata.' },
  substituicao: { label: 'substituição', cor: 'bg-violet-500/15 text-violet-300 border-violet-500/30',
    ajuda: 'Trocar de fornecedor ou de plano.' },
  nenhum: { label: 'sem alavanca', cor: 'bg-zinc-700/40 text-zinc-400 border-zinc-600',
    ajuda: 'Não há o que cortar aqui — a categoria sai das sugestões.' },
  requer_detalhe: { label: 'aguarda detalhe', cor: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
    ajuda: 'A alavanca existe, mas depende do dado item a item da nota.' }
}

const SEGMENTO_LABEL: Record<string, string> = {
  dia_util: 'dia útil', fim_de_semana: 'fim de semana',
  grande: 'compra grande', pequeno: 'reposição'
}

/** Onde gastei e onde cortar, numa coisa só.
 *
 *  Só aparecem categorias com alavanca: Saúde, Encargos e Pagamento saem porque
 *  não há o que cortar ali, e manter linha de zero economia só gastaria atenção.
 *
 *  Cada linha é um (categoria, segmento) com dois controles — tática e % de
 *  redução — e dois expansores: `⊕` junta os segmentos da categoria num só, e
 *  `▸` abre os lançamentos daquela linha. O segmento de cada lançamento vem
 *  **estampado pelo servidor**, para a regra não existir em duas linguagens.
 */
function SecaoEconomia({ segmentos, itens, onSalvarMeta }: {
  segmentos: Segmento[]
  itens: Item[]
  onSalvarMeta: (s: Segmento, pct: number, tatica: string | null) => void
}): JSX.Element {
  const [juntas, setJuntas] = useState<Set<string>>(new Set())
  const [aberta, setAberta] = useState<string | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})

  const comAlavanca = useMemo(
    () => segmentos.filter(s => s.alavanca !== 'nenhum'), [segmentos])

  /** categoria → suas linhas, para saber quem tem segmento para juntar */
  const porCategoria = useMemo(() => {
    const m = new Map<string, Segmento[]>()
    for (const s of comAlavanca) {
      if (!m.has(s.categoria)) m.set(s.categoria, [])
      m.get(s.categoria)!.push(s)
    }
    return m
  }, [comAlavanca])

  /** Junta os segmentos de uma categoria numa linha. A meta de uma linha junta
   *  é gravada com segmento vazio — a chave da categoria inteira, que o servidor
   *  já usa como herança para os segmentos. */
  function agregar(cat: string, rs: Segmento[]): Segmento {
    const valor = rs.reduce((a, s) => a + s.valor, 0)
    const n = rs.reduce((a, s) => a + s.n, 0)
    const base = rs.reduce((a, s) => a + s.baseline, 0)
    const p = rs[0]
    const pct = p.meta_bruta
    return {
      ...p, categoria: cat, segmento: '', n, valor: Math.round(valor * 100) / 100,
      ticket_medio: n ? Math.round((valor / n) * 100) / 100 : 0,
      baseline: Math.round(base * 100) / 100,
      meta_valor: pct !== null ? Math.round(base * (1 - pct / 100) * 100) / 100 : null,
      economia: pct !== null ? Math.round(base * (pct / 100) * 100) / 100 : null,
      excedeu: null
    }
  }

  const linhas: Segmento[] = []
  for (const [cat, rs] of porCategoria) {
    if (rs.length > 1 && !juntas.has(cat)) linhas.push(...rs)
    else linhas.push(rs.length > 1 ? agregar(cat, rs) : rs[0])
  }
  linhas.sort((a, b) => b.valor - a.valor)

  if (!linhas.length) return <div />
  const max = Math.max(1, ...linhas.map(s => s.valor))
  const pctDe = (s: Segmento) =>
    s.meta_bruta !== null ? s.meta_bruta : s.corte_sugerido
  const chave = (s: Segmento) => `${s.categoria}|${s.segmento}`

  // O orçamento: só conta o que você já cravou, não as sugestões — senão a tela
  // prometeria uma economia que ninguém decidiu.
  const orcamento = linhas.reduce(
    (a, s) => a + (s.meta_bruta !== null ? (s.economia || 0) : 0), 0)
  const potencial = linhas.reduce(
    (a, s) => a + (s.meta_bruta === null ? s.baseline * (s.corte_sugerido / 100) : 0), 0)

  return (
    <section>
      <div className="flex items-baseline gap-3">
        <h2 className="text-sm font-medium">Onde gastei, e onde cortar</h2>
        <span className="text-xs text-zinc-500">
          ⊕ junta os segmentos · ▸ abre os lançamentos
        </span>
        <span className="ml-auto text-sm">
          <span className="text-zinc-500 text-xs uppercase mr-2">orçamento de redução</span>
          <strong className="tabular-nums text-emerald-300">{formatBRL(orcamento)}</strong>
          <span className="text-zinc-500">/mês</span>
          {potencial > 0.01 && (
            <span className="text-xs text-zinc-600 ml-2">
              (+{formatBRL(potencial)} nas sugestões que você ainda não cravou)
            </span>
          )}
        </span>
      </div>
      <p className="text-xs text-zinc-500 mb-2">
        O % incide sobre a baseline de cada linha. Saúde, Encargos e Pagamento não
        aparecem: não há alavanca ali.
      </p>

      <div className="space-y-1">
        {linhas.map(s => {
          const a = ALAVANCAS[s.alavanca] || ALAVANCAS.volume
          const k = chave(s)
          const rs = porCategoria.get(s.categoria) || []
          const podeJuntar = rs.length > 1
          const estaJunta = juntas.has(s.categoria)
          const pct = pctDe(s)
          const its = itens.filter(i =>
            i.secao === 'lancamento' && (i.categoria || '—') === s.categoria
            && (!s.segmento || (i.segmento || '') === s.segmento))
          return (
            <div key={k}>
              <div className="flex items-center gap-2 text-sm hover:bg-zinc-900/30 rounded px-1 py-0.5">
                <button
                  onClick={() => setAberta(aberta === k ? null : k)}
                  className="w-4 shrink-0 text-xs text-zinc-600 hover:text-zinc-300"
                  title="ver os lançamentos desta linha"
                >{aberta === k ? '▾' : '▸'}</button>

                <span className="w-36 shrink-0 truncate text-zinc-300" title={s.observacao || ''}>
                  {s.categoria}
                </span>

                <span className="w-28 shrink-0 text-xs">
                  {s.segmento
                    ? <span className="text-zinc-500">{SEGMENTO_LABEL[s.segmento] || s.segmento}</span>
                    : podeJuntar
                      ? <span className="text-zinc-600">categoria toda</span>
                      : null}
                  {podeJuntar && (
                    <button
                      onClick={() => setJuntas(p => {
                        const n = new Set(p)
                        if (estaJunta) n.delete(s.categoria); else n.add(s.categoria)
                        return n
                      })}
                      className="ml-1 text-zinc-600 hover:text-zinc-300"
                      title={estaJunta ? 'separar os segmentos' : 'juntar os segmentos numa linha'}
                    >{estaJunta ? '⊖' : '⊕'}</button>
                  )}
                </span>

                <span className={cn('shrink-0 text-xs px-1.5 py-0.5 rounded border', a.cor)}
                      title={a.ajuda}>{a.label}</span>

                <div className="flex-1 min-w-[3rem] h-4 bg-zinc-900 rounded overflow-hidden">
                  <div className="h-full bg-sky-600/60" style={{ width: `${(s.valor / max) * 100}%` }} />
                </div>

                <span className="w-9 shrink-0 text-right text-xs text-zinc-600">{s.n}x</span>
                <span className="w-24 shrink-0 text-right tabular-nums">{formatBRL(s.valor)}</span>

                {/* tática: combo, com as opções da alavanca daquele segmento */}
                <select
                  value={s.tatica || ''}
                  onChange={e => onSalvarMeta(s, pct, e.target.value || null)}
                  className={cn('w-52 shrink-0 bg-transparent border rounded px-1 py-0.5 text-xs',
                    s.tatica_salva ? 'border-emerald-600/40 text-emerald-300'
                      : 'border-zinc-800 text-zinc-400')}
                  title={s.tatica_salva ? 'tática que você escolheu' : 'sugestão — escolha para gravar'}
                >
                  {!s.taticas.includes(s.tatica || '') && (
                    <option value={s.tatica || ''}>{s.tatica || '—'}</option>
                  )}
                  {s.taticas.map(t => <option key={t} value={t}>{t}</option>)}
                </select>

                {/* % de redução: rascunho local, grava ao sair do campo ou no Enter */}
                <span className="shrink-0 flex items-center gap-0.5">
                  <input
                    value={draft[k] ?? String(pct)}
                    onChange={e => setDraft(p => ({ ...p, [k]: e.target.value }))}
                    onBlur={() => {
                      const v = parseFloat((draft[k] ?? '').replace(',', '.'))
                      setDraft(p => { const n = { ...p }; delete n[k]; return n })
                      if (!isNaN(v) && v > 0 && v < 100 && v !== s.meta_bruta) {
                        onSalvarMeta(s, v, s.tatica)
                      }
                    }}
                    onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
                    className={cn('w-12 bg-transparent border rounded px-1 py-0.5 text-xs text-right tabular-nums',
                      s.meta_bruta !== null ? 'border-emerald-600/40 text-emerald-300'
                        : 'border-zinc-800 text-zinc-500')}
                    title={s.meta_bruta !== null
                      ? 'meta que você cravou'
                      : `sugestão de ${s.corte_sugerido}% — edite para gravar`}
                  />
                  <span className="text-xs text-zinc-600">%</span>
                </span>

                {/* o que a meta vira em reais, e sobre que base */}
                <span className="w-36 shrink-0 text-right text-xs tabular-nums">
                  {s.meta_valor !== null ? (
                    <span className={s.meta_bruta !== null ? 'text-emerald-400' : 'text-zinc-600'}>
                      {formatBRL(s.meta_valor)}
                      {s.economia ? <span className="text-zinc-500"> (−{formatBRL(s.economia)})</span> : null}
                    </span>
                  ) : (
                    <span className="text-zinc-700">—</span>
                  )}
                  <span className="block text-zinc-700"
                        title={s.baseline_origem === 'mes_atual'
                          ? 'sem histórico ainda: a base é o próprio mês'
                          : 'base = média dos meses anteriores'}>
                    sobre {formatBRL(s.baseline)}
                    {s.baseline_origem === 'mes_atual' ? ' (este mês)' : ' (média)'}
                  </span>
                </span>
              </div>

              {aberta === k && (
                <div className="ml-6 mb-2 border-l border-zinc-800 pl-3 py-1">
                  {its.slice()
                    .sort((x, y) => (y.data_compra || '').localeCompare(x.data_compra || ''))
                    .map(i => (
                      <div key={i.id} className="flex gap-3 text-xs text-zinc-400 py-0.5">
                        <span className="w-12 shrink-0 tabular-nums">
                          {(i.data_compra || '').slice(8, 10)}/{(i.data_compra || '').slice(5, 7)}
                        </span>
                        <span className="truncate">
                          {i.estabelecimento_norm || i.estabelecimento}
                        </span>
                        {i.parcela_n && (
                          <span className="shrink-0 text-zinc-600">{i.parcela_n}/{i.parcela_total}</span>
                        )}
                        <span
                          className="ml-auto shrink-0 text-zinc-500"
                          title={`${i.cartao || ''}`
                            + (i.cartao_final ? ` · final ${i.cartao_final}` : '')
                            + (i.portador ? ` · ${i.portador}` : '')}
                        >
                          {i.cartao}
                          {i.cartao_final && <span className="text-zinc-600"> ·{i.cartao_final}</span>}
                        </span>
                        <span className="w-24 shrink-0 text-right tabular-nums text-zinc-300">
                          {formatBRL(i.valor)}
                        </span>
                      </div>
                    ))}
                  {!its.length && (
                    <div className="text-xs text-zinc-600">nenhum lançamento nesta linha</div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </section>
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

      {/* Dizer o que ficou de fora é parte do conselho. Saúde não recebe
          sugestão porque não há alavanca — 7 compras, mediana R$ 542, dentista
          e clínica —, e omitir isso em silêncio pareceria esquecimento. */}
      {(!!(sug.sem_alavanca || []).length || !!(sug.aguardando_detalhe || []).length) && (
        <section className="text-sm space-y-1">
          <h2 className="font-medium">O que ficou fora, e por quê</h2>
          {!!(sug.sem_alavanca || []).length && (
            <p className="text-zinc-400">
              <span className="text-zinc-300">Sem alavanca de economia:</span>{' '}
              {sug.sem_alavanca.join(', ')} — não há o que cortar nem onde trocar,
              então não entram no dossiê.
            </p>
          )}
          {!!(sug.aguardando_detalhe || []).length && (
            <p className="text-zinc-400">
              <span className="text-amber-300">Aguardando o detalhe item a item:</span>{' '}
              {sug.aguardando_detalhe.join(', ')} — a alavanca é de preço (atacado,
              genérico, outra rede), e exercê-la exige saber <em>quais produtos</em>.
            </p>
          )}
        </section>
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
