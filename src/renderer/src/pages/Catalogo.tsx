import { useEffect, useState, Fragment, type ReactNode } from 'react'
import { api } from '../lib/api'
import { cn, formatBRL } from '../lib/utils'
import { ImportarCatalogoModal } from '../components/ImportarCatalogoModal'

type Natureza = 'despesa' | 'receita'

/** Forma comum das duas naturezas. O catálogo de receitas é espelho do de
 *  despesas (doc 14) e só diverge em dois pontos: o dia se chama
 *  `dia_recebimento` no banco, e receita tem `tipo` — que é o campo que decide
 *  se aquilo conta como renda. A normalização acontece na borda (carregar e
 *  salvar), então o resto da tela não precisa saber de qual lado está. */
interface Item {
  id: number; nome: string; categoria_id: number | null; categoria_nome: string
  dia: number | null; tipo_valor: string
  padrao_variabilidade: string; valor_padrao: number; regras_match: string; ativo: number
  recorrencia: 'fixa' | 'esporadica'
  tipo?: string
  conta_como_renda?: boolean
}

interface Categoria { id: number; nome: string }

const TIPO_RECEITA_LABEL: Record<string, string> = {
  salario: 'Salário', juros: 'Juros', reembolso: 'Reembolso', outra: 'Outra',
  resgate_mensal: 'Resgate mensal', resgate_esporadico: 'Resgate esporádico',
  estorno: 'Estorno', transferencia: 'Transferência'
}

const PADRAO_LABEL: Record<string, string> = {
  fixa: 'Fixa', variavel_sazonal: 'Sazonal', variavel_nao_sazonal: 'Variável',
  reajuste_anual: 'Reajuste anual', anual: 'Anual', sem_dados: 'Sem dados'
}

interface FormState {
  nome: string
  categoria_id: string
  tipo_valor: string
  padrao_variabilidade: string
  recorrencia: string
  tipo: string
  valor_padrao: string
  dia: string
  palavras_chave: string
}

type Ordem = 'categoria' | 'valor' | 'dia_vencimento'

const ORDEM_LABEL: Record<Ordem, string> = {
  categoria: 'Categoria',
  valor: 'Maior valor',
  dia_vencimento: 'Dia de vencimento'
}

/** Normaliza o que vem da API para a forma comum. */
function paraItem(r: Record<string, unknown>, natureza: Natureza): Item {
  return {
    ...(r as unknown as Item),
    dia: (natureza === 'receita' ? r.dia_recebimento : r.dia_vencimento) as number | null
  }
}

function itemParaForm(d: Item): FormState {
  let palavras_chave = ''
  try { palavras_chave = (JSON.parse(d.regras_match).palavras_chave ?? []).join(', ') } catch { /* regras_match inválido, deixa vazio */ }
  return {
    nome: d.nome,
    categoria_id: d.categoria_id != null ? String(d.categoria_id) : '',
    tipo_valor: d.tipo_valor,
    padrao_variabilidade: d.padrao_variabilidade,
    recorrencia: d.recorrencia ?? 'fixa',
    tipo: d.tipo ?? 'outra',
    valor_padrao: String(d.valor_padrao ?? ''),
    dia: d.dia != null ? String(d.dia) : '',
    palavras_chave
  }
}

export function Catalogo() {
  const [natureza, setNatureza] = useState<Natureza>('despesa')
  const [despesas, setDespesas] = useState<Item[]>([])
  const [categorias, setCategorias] = useState<Categoria[]>([])
  const [busca, setBusca] = useState('')
  const [apenasAtivas, setApenasAtivas] = useState(true)
  const [ordem, setOrdem] = useState<Ordem>('categoria')
  const [loading, setLoading] = useState(true)
  const [editando, setEditando] = useState<number | null>(null)
  const [form, setForm] = useState<FormState | null>(null)
  const [criando, setCriando] = useState(false)
  const [importando, setImportando] = useState(false)
  const [erro, setErro] = useState('')

  const ehReceita = natureza === 'receita'

  async function load() {
    setLoading(true)
    try {
      const [itens, cats] = await Promise.all([
        ehReceita ? api.receitas.list() : api.catalogo.list(),
        api.categorias.list()
      ])
      setDespesas((itens as Record<string, unknown>[]).map(r => paraItem(r, natureza)))
      setCategorias(cats)
    } finally {
      setLoading(false)
    }
  }

  // trocar de aba fecha qualquer edição aberta: o formulário é do outro lado
  useEffect(() => { setEditando(null); setCriando(false); setForm(null); load() }, [natureza])

  async function toggle(id: number) {
    await (ehReceita ? api.receitas.toggleAtivo(id) : api.catalogo.toggleAtivo(id))
    load()
  }

  function abrirEdicao(d: Item) {
    setErro('')
    setCriando(false)
    setEditando(editando === d.id ? null : d.id)
    setForm(itemParaForm(d))
  }

  function abrirCriacao() {
    setErro('')
    setEditando(null)
    setCriando(c => !c)
    setForm({ nome: '', categoria_id: '', tipo_valor: 'variavel', padrao_variabilidade: 'variavel_nao_sazonal', recorrencia: 'fixa', tipo: 'outra', valor_padrao: '', dia: '', palavras_chave: '' })
  }

  async function salvar(id: number | null) {
    if (!form || !form.nome.trim()) return
    const keywords = form.palavras_chave.split(',').map(k => k.trim().toLowerCase()).filter(Boolean)
    const comum = {
      id: id ?? undefined,
      nome: form.nome.trim(),
      categoria_id: form.categoria_id ? Number(form.categoria_id) : null,
      tipo_valor: form.tipo_valor,
      padrao_variabilidade: form.padrao_variabilidade,
      recorrencia: form.recorrencia,
      valor_padrao: form.valor_padrao ? parseFloat(form.valor_padrao.replace(',', '.')) : 0,
      regras_match: JSON.stringify({ palavras_chave: keywords, faixa_valor: null, janela_dias: 5, banco: null })
    }
    const dia = form.dia ? Number(form.dia) : null
    setErro('')
    try {
      await (ehReceita
        ? api.receitas.upsert({ ...comum, tipo: form.tipo, dia_recebimento: dia })
        : api.catalogo.upsert({ ...comum, dia_vencimento: dia }))
    } catch (e) {
      // nome repetido é o caso comum — mostrar na tela em vez de sumir com o
      // formulário e o texto que o usuário digitou
      let msg = String(e)
      try { msg = JSON.parse(msg.replace(/^Error:\s*/, '')).msg ?? msg } catch { /* não era JSON */ }
      setErro(msg)
      return
    }
    setEditando(null)
    setCriando(false)
    setForm(null)
    load()
  }

  const filtradas = despesas.filter(d => {
    if (apenasAtivas && !d.ativo) return false
    if (busca && !d.nome.toLowerCase().includes(busca.toLowerCase())) return false
    return true
  })

  // por categoria a lista continua agrupada; nas outras ordens vira lista
  // única, porque agrupar por categoria quebraria a ordenação pedida
  const ordenadas = [...filtradas].sort((a, b) => {
    if (ordem === 'valor') {
      const diff = (b.valor_padrao ?? 0) - (a.valor_padrao ?? 0)
      return diff !== 0 ? diff : a.nome.localeCompare(b.nome)
    }
    if (ordem === 'dia_vencimento') {
      // sem dia definido vai pro fim, senão ocuparia o topo sem informar nada
      const da = a.dia ?? Infinity
      const db = b.dia ?? Infinity
      return da !== db ? da - db : a.nome.localeCompare(b.nome)
    }
    return 0
  })

  const byCategory = filtradas.reduce<Record<string, Item[]>>((acc, d) => {
    const cat = d.categoria_nome || 'Outros'
    if (!acc[cat]) acc[cat] = []
    acc[cat].push(d)
    return acc
  }, {})

  const formFields = form && (
    <div className="flex items-end gap-2 flex-wrap">
      <Field label="Nome">
        <input value={form.nome} onChange={e => setForm(f => f && { ...f, nome: e.target.value })} autoFocus
          className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500 w-48" />
      </Field>
      <Field label="Categoria">
        <select value={form.categoria_id} onChange={e => setForm(f => f && { ...f, categoria_id: e.target.value })}
          className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500">
          <option value="">—</option>
          {categorias.map(c => <option key={c.id} value={c.id}>{c.nome}</option>)}
        </select>
      </Field>
      <Field label="Padrão">
        <select value={form.padrao_variabilidade} onChange={e => setForm(f => f && { ...f, padrao_variabilidade: e.target.value })}
          className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500">
          {Object.entries(PADRAO_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </Field>
      <Field label="Tipo valor">
        <select value={form.tipo_valor} onChange={e => setForm(f => f && { ...f, tipo_valor: e.target.value })}
          className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500">
          <option value="variavel">Variável</option>
          <option value="fixo">Fixo</option>
        </select>
      </Field>
      {ehReceita && (
        <Field label="Tipo">
          <select value={form.tipo} onChange={e => setForm(f => f && { ...f, tipo: e.target.value })}
            title="Resgate, estorno e transferência não contam como renda do mês"
            className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500">
            {Object.entries(TIPO_RECEITA_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Field>
      )}
      <Field label="Recorrência">
        <select value={form.recorrencia} onChange={e => setForm(f => f && { ...f, recorrencia: e.target.value })}
          title="Esporádica não gera previsão mensal — só aparece no mês em que acontecer"
          className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500">
          <option value="fixa">Todo mês</option>
          <option value="esporadica">Esporádica</option>
        </select>
      </Field>
      <Field label="Valor previsto">
        <input value={form.valor_padrao} onChange={e => setForm(f => f && { ...f, valor_padrao: e.target.value })}
          className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500 w-24 text-right" />
      </Field>
      <Field label={ehReceita ? 'Dia receb.' : 'Dia venc.'}>
        <input value={form.dia} onChange={e => setForm(f => f && { ...f, dia: e.target.value })}
          className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500 w-14 text-center" />
      </Field>
      <Field label="Palavras-chave (separadas por vírgula)">
        <input value={form.palavras_chave} onChange={e => setForm(f => f && { ...f, palavras_chave: e.target.value })}
          placeholder="ex: cartao, black"
          className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500 w-56" />
      </Field>
      <button onClick={() => salvar(editando)}
        className="px-3 py-1 rounded bg-emerald-600 text-white text-xs font-medium hover:bg-emerald-500 transition-colors">
        Salvar
      </button>
      {erro && <p className="text-xs text-red-400 w-full">{erro}</p>}
    </div>
  )

  function tabela(items: Item[], mostrarCategoria: boolean) {
    const nColunas = (mostrarCategoria ? 7 : 6) + (ehReceita ? 1 : 0)
    return (
      <div className="rounded-lg overflow-hidden border border-zinc-800/60">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800 bg-zinc-900/40">
              <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Nome</th>
              {mostrarCategoria && <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Categoria</th>}
              {ehReceita && <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Tipo</th>}
              <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Padrão</th>
              <th className="px-4 py-2 text-right text-xs text-zinc-500 font-medium">Previsão</th>
              <th className="px-4 py-2 text-center text-xs text-zinc-500 font-medium">{ehReceita ? 'Dia Receb.' : 'Dia Venc.'}</th>
              <th className="px-4 py-2 text-center text-xs text-zinc-500 font-medium">Status</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {items.map((d, i) => (
              <Fragment key={d.id}>
                <tr className={cn('transition-colors hover:bg-zinc-800/40', i > 0 && 'border-t border-zinc-800/40', !d.ativo && 'opacity-40')}>
                  <td className="px-4 py-2.5 text-zinc-300">
                    {d.nome}
                    {d.recorrencia === 'esporadica' && (
                      <span className="ml-2 text-[10px] uppercase tracking-wide text-amber-500/70"
                        title="Não gera previsão mensal — só aparece no mês em que acontecer">
                        esporádica
                      </span>
                    )}
                  </td>
                  {mostrarCategoria && (
                    <td className="px-4 py-2.5 text-zinc-500 text-xs">{d.categoria_nome || 'Outros'}</td>
                  )}
                  {ehReceita && (
                    <td className="px-4 py-2.5">
                      <span className={cn('text-xs px-2 py-0.5 rounded',
                        d.conta_como_renda ? 'text-emerald-400 bg-emerald-950/40' : 'text-zinc-500 bg-zinc-800')}
                        title={d.conta_como_renda ? 'Conta como renda do mês' : 'Entra na conta, mas não é renda'}>
                        {TIPO_RECEITA_LABEL[d.tipo ?? 'outra'] ?? d.tipo}
                      </span>
                    </td>
                  )}
                  <td className="px-4 py-2.5">
                    <span className="text-xs text-zinc-500 bg-zinc-800 px-2 py-0.5 rounded">
                      {PADRAO_LABEL[d.padrao_variabilidade] ?? d.padrao_variabilidade}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-right text-zinc-300 tabular-nums">
                    {d.valor_padrao > 0 ? formatBRL(d.valor_padrao) : '—'}
                  </td>
                  <td className="px-4 py-2.5 text-center text-zinc-500 text-xs">{d.dia ?? '—'}</td>
                  <td className="px-4 py-2.5 text-center">
                    <button onClick={() => toggle(d.id)}
                      className={cn('text-xs px-2 py-0.5 rounded border transition-colors',
                        d.ativo
                          ? 'border-emerald-800/50 text-emerald-400 bg-emerald-950/30 hover:bg-emerald-950/60'
                          : 'border-zinc-700 text-zinc-500 hover:bg-zinc-800')}>
                      {d.ativo ? 'Ativa' : 'Inativa'}
                    </button>
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <button onClick={() => abrirEdicao(d)}
                      className="text-xs text-zinc-500 hover:text-emerald-400 transition-colors">
                      Editar
                    </button>
                  </td>
                </tr>
                {editando === d.id && (
                  <tr className="bg-zinc-900/60 border-t border-zinc-800/40">
                    <td colSpan={nColunas} className="px-4 py-3">
                      {formFields}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full pt-3">
      <div className="px-6 pb-4 flex items-center justify-between flex-none">
        <div className="flex items-center gap-4">
          <div className="flex rounded-md border border-zinc-700 overflow-hidden">
            {(['despesa', 'receita'] as Natureza[]).map(n => (
              <button key={n} onClick={() => setNatureza(n)}
                className={cn('px-3 py-1.5 text-sm transition-colors',
                  natureza === n ? 'bg-emerald-600 text-white' : 'text-zinc-400 hover:text-zinc-200')}>
                {n === 'despesa' ? 'Despesas' : 'Receitas'}
              </button>
            ))}
          </div>
          <p className="text-sm text-zinc-500">
            {filtradas.length} {ehReceita ? 'receitas' : 'despesas'}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-zinc-400">
            Ordenar por
            <select value={ordem} onChange={e => setOrdem(e.target.value as Ordem)}
              className="bg-zinc-800 border border-zinc-700 rounded-md px-2 py-1.5 text-sm text-zinc-200 outline-none focus:border-emerald-500">
              {Object.entries(ORDEM_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm text-zinc-400 cursor-pointer">
            <input type="checkbox" checked={apenasAtivas} onChange={e => setApenasAtivas(e.target.checked)} className="rounded" />
            Apenas ativas
          </label>
          <input type="text" placeholder="Buscar..." value={busca} onChange={e => setBusca(e.target.value)}
            className="bg-zinc-800 border border-zinc-700 rounded-md px-3 py-1.5 text-sm text-zinc-200 outline-none focus:border-emerald-500 w-48 placeholder:text-zinc-600" />
          <button onClick={abrirCriacao}
            className="px-3 py-1.5 rounded-md border border-zinc-700 text-sm text-zinc-300 hover:border-emerald-600 hover:text-emerald-400 transition-colors whitespace-nowrap">
            {ehReceita ? '+ Nova receita' : '+ Nova despesa'}
          </button>
          {/* a importação de planilha é específica de despesa (desativa o que
              não está no arquivo) — não faz sentido no lado da receita */}
          {!ehReceita && (
            <button onClick={() => setImportando(true)}
              className="px-3 py-1.5 rounded-md border border-zinc-700 text-sm text-zinc-300 hover:border-zinc-500 transition-colors whitespace-nowrap">
              Importar catálogo
            </button>
          )}
        </div>
      </div>

      {importando && (
        <ImportarCatalogoModal onClose={() => setImportando(false)} onConcluido={load} />
      )}

      <div className="flex-1 overflow-auto px-6 pb-6">
        {criando && (
          <div className="mb-4 rounded-lg border border-zinc-700/60 bg-zinc-900/60 p-4">
            {formFields}
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center h-40 text-zinc-500">Carregando...</div>
        ) : ordem === 'categoria' ? (
          <div className="space-y-4">
            {Object.entries(byCategory).map(([cat, items]) => (
              <div key={cat}>
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider">{cat}</span>
                  <div className="flex-1 h-px bg-zinc-800" />
                  <span className="text-xs text-zinc-600">{items.length}</span>
                </div>
                {tabela(items, false)}
              </div>
            ))}
          </div>
        ) : (
          tabela(ordenadas, true)
        )}
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }): JSX.Element {
  return (
    <div>
      <label className="text-xs text-zinc-500 block mb-1">{label}</label>
      {children}
    </div>
  )
}
