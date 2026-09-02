import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { formatBRL, cn } from '../lib/utils'

const PRODUTOS = ['LCI', 'LCA', 'CDB', 'COFRINHOS', 'LIG', 'LF']

type Metodo = 'extrato' | 'credito' | 'proporcional'

/** Como recalcular o valor do papel depois do resgate.
 *
 *  Três respostas que discordam, e a diferença é informação — por isso as três
 *  aparecem com o número do banco ao lado, em vez de a escolha ficar escondida
 *  atrás de uma fórmula. Medido no resgate real de 01/09/2026 numa LCA:
 *  extrato acerta na bucha, crédito erra R$ 13,60 (o CDI do dia que o BCB ainda
 *  não publicou) e proporcional erra R$ 187,38 — e também erra o fluxo. */
const METODOS: { id: Metodo; titulo: string; ajuda: string }[] = [
  {
    id: 'extrato', titulo: 'Valor do extrato',
    ajuda: 'Usa o número que o próprio banco publica na seção "Posição em" do arquivo. ' +
      'Não é estimativa: é o fechamento do dia, com o rendimento dentro.'
  },
  {
    id: 'credito', titulo: 'Ontem menos o creditado',
    ajuda: 'Tira do valor de ontem exatamente o que caiu na conta, e deixa o rendimento ' +
      'do dia incidir sobre o que sobrou. É a conta que o banco faz.'
  },
  {
    id: 'proporcional', titulo: 'Proporcional ao principal',
    ajuda: 'Tira do valor a mesma fatia que saiu do principal. Supõe resgate pro-rata, ' +
      'o que não é o que o banco fez — e registra um fluxo de caixa diferente do valor creditado.'
  },
]

interface Item {
  data: string; historico: string; n_operacao: string
  data_aplicacao: string; data_vencimento: string; ativo: string
  valor_principal: number; valor_bruto: number
  valor_aplicacao_antes: number; valor_aplicacao_depois: number
  valor_anterior: number
  metodos: Record<Metodo, number | null>
  ja_importado: boolean
}
interface Preview { ok: boolean; itens: Item[]; erros: string[]; periodo: { ini: string; fim: string } | null }

const d = (iso: string) => iso ? new Date(iso + 'T00:00:00').toLocaleDateString('pt-BR') : '—'

export function ExtratoInvestimento({ onConcluido }: { onConcluido: () => void }) {
  const [aberto, setAberto] = useState(false)
  const [produto, setProduto] = useState('LCA')
  const [file, setFile] = useState<File | null>(null)
  const [prev, setPrev] = useState<Preview | null>(null)
  const [metodo, setMetodo] = useState<Metodo>('extrato')
  const [msg, setMsg] = useState('')
  const [erro, setErro] = useState('')
  const [ocupado, setOcupado] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  function limpar() {
    setFile(null); setPrev(null); setMsg(''); setErro('')
    if (fileRef.current) fileRef.current.value = ''
  }

  async function analisar(f: File) {
    setFile(f); setPrev(null); setErro(''); setMsg(''); setOcupado(true)
    try {
      const r = await api.investimentos.extratoPreview(f, produto)
      if (!r.ok) setErro(r.msg || 'Não consegui ler o extrato')
      else setPrev(r)
    } catch (e) {
      setErro(String(e))
    } finally {
      setOcupado(false)
    }
  }

  async function confirmar() {
    if (!file) return
    setOcupado(true); setErro('')
    try {
      const r = await api.investimentos.extratoConfirmar(file, produto, metodo)
      if (!r.ok) { setErro(r.msg || 'Falhou'); return }
      setMsg(r.msg)
      limpar()
      onConcluido()
    } finally {
      setOcupado(false)
    }
  }

  if (!aberto) {
    return (
      <button onClick={() => setAberto(true)}
        className="px-4 py-2 rounded-md border border-zinc-700 text-sm text-zinc-300
          hover:border-zinc-500 hover:text-zinc-100 transition-colors">
        Importar extrato
      </button>
    )
  }

  const podeConfirmar = !!prev?.itens.length && !prev.erros.length && !ocupado

  return (
    <div className="fixed inset-0 z-40 bg-black/60 flex items-start justify-center overflow-auto py-10">
      <div className="w-full max-w-3xl mx-4 rounded-lg border border-zinc-700 bg-zinc-900 p-5 space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-zinc-100">Importar extrato de investimento</h2>
            <p className="text-xs text-zinc-500 mt-1 max-w-lg">
              Lê as linhas de <strong className="text-zinc-400">RESGATE</strong> e ajusta a posição.
              O movimento fica gravado à parte — dá para excluir depois e a posição volta ao que era.
            </p>
          </div>
          <button onClick={() => { setAberto(false); limpar() }}
            className="text-zinc-500 hover:text-zinc-200 text-lg leading-none px-1">&times;</button>
        </div>

        <div className="flex items-end gap-3 flex-wrap">
          <label className="text-sm text-zinc-400">
            Produto
            <select value={produto}
              onChange={e => { setProduto(e.target.value); if (file) analisar(file) }}
              className="ml-2 bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-sm text-zinc-200
                outline-none focus:border-emerald-500">
              {PRODUTOS.map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <input ref={fileRef} type="file" accept=".xls,.xlsx,.xlsm,.htm,.html" className="hidden"
            onChange={e => { const f = e.target.files?.[0]; if (f) analisar(f) }} />
          <button onClick={() => fileRef.current?.click()} disabled={ocupado}
            className="px-3 py-1.5 rounded-md border border-zinc-700 text-sm text-zinc-300
              disabled:opacity-40 hover:border-zinc-500 transition-colors">
            {ocupado ? 'Lendo...' : file ? 'Trocar arquivo' : 'Selecionar arquivo'}
          </button>
          {file && <span className="text-xs text-zinc-500 truncate max-w-[16rem]">{file.name}</span>}
        </div>

        {erro && <p className="text-sm text-red-400">{erro}</p>}
        {msg && <p className="text-sm text-emerald-400">{msg}</p>}

        {prev?.erros?.map((e, i) => (
          <p key={i} className="text-sm text-red-400 border border-red-900/50 bg-red-950/30 rounded px-3 py-2">
            {e}
          </p>
        ))}

        {!!prev?.itens.length && (
          <>
            <div className="space-y-3">
              {prev.itens.map((it, i) => (
                <div key={i} className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-3 space-y-2">
                  <div className="flex items-baseline justify-between gap-3 flex-wrap">
                    <span className="text-sm text-zinc-200 font-medium">
                      {it.historico} em {d(it.data)} · {it.ativo}
                    </span>
                    <span className="text-xs text-zinc-500">
                      aplicado {d(it.data_aplicacao)} · vence {d(it.data_vencimento)} · op {it.n_operacao}
                    </span>
                  </div>
                  {it.ja_importado && (
                    <p className="text-xs text-amber-400">
                      Este movimento já foi importado antes — confirmar de novo apenas o atualiza.
                    </p>
                  )}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                    {[
                      ['Principal resgatado', it.valor_principal],
                      ['Creditado na conta', it.valor_bruto],
                      ['Aplicação antes', it.valor_aplicacao_antes],
                      ['Aplicação depois', it.valor_aplicacao_depois],
                    ].map(([r, v]) => (
                      <div key={r as string}>
                        <p className="text-zinc-600">{r}</p>
                        <p className="text-zinc-300 tabular-nums">{formatBRL(v as number)}</p>
                      </div>
                    ))}
                  </div>
                  <div>
                    <p className="text-xs text-zinc-600 mb-1">
                      Valor do papel depois do resgate — de {formatBRL(it.valor_anterior)} (ontem) para:
                    </p>
                    <div className="grid sm:grid-cols-3 gap-2">
                      {METODOS.map(m => {
                        const v = it.metodos[m.id]
                        const ref = it.metodos.extrato
                        const dif = v !== null && ref !== null && m.id !== 'extrato' ? v - ref : null
                        return (
                          <button key={m.id} onClick={() => setMetodo(m.id)}
                            disabled={v === null} title={m.ajuda}
                            className={cn('text-left rounded-md border px-2.5 py-2 transition-colors disabled:opacity-40',
                              metodo === m.id
                                ? 'border-emerald-600 bg-emerald-600/10'
                                : 'border-zinc-800 hover:border-zinc-600')}>
                            <p className="text-[11px] text-zinc-500">{m.titulo}</p>
                            <p className="text-sm tabular-nums text-zinc-200">
                              {v === null ? 'indisponível' : formatBRL(v)}
                            </p>
                            {dif !== null && Math.abs(dif) >= 0.005 && (
                              <p className="text-[11px] text-amber-400/80 tabular-nums">
                                {dif > 0 ? '+' : '−'}{formatBRL(Math.abs(dif))} vs extrato
                              </p>
                            )}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                </div>
              ))}
            </div>

            <p className="text-xs text-zinc-500">
              {METODOS.find(m => m.id === metodo)?.ajuda}
            </p>

            <div className="flex items-center gap-3">
              <button onClick={confirmar} disabled={!podeConfirmar}
                className="px-4 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium
                  disabled:opacity-40 hover:bg-emerald-500 transition-colors">
                {ocupado ? 'Gravando...' : `Confirmar ${prev.itens.length} movimento(s)`}
              </button>
              <button onClick={() => { setAberto(false); limpar() }}
                className="px-4 py-2 rounded-md border border-zinc-700 text-sm text-zinc-400
                  hover:border-zinc-500 hover:text-zinc-200 transition-colors">
                Cancelar
              </button>
              <span className="text-xs text-zinc-600">
                nada é gravado até aqui — e depois de gravado dá para excluir
              </span>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

interface Movimento {
  id: number; data: string; tipo: string; produto: string
  data_aplicacao: string; data_vencimento: string; n_operacao: string
  valor_principal: number; valor_bruto: number
  valor_anterior: number; valor_novo: number; metodo: string; arquivo: string
}

/** Movimentos considerados num período, com o botão de desfazer.
 *
 *  Existe para a posição ser auditável e reversível: cada linha diz o que foi
 *  tirado, por qual regra, e some inteira ao ser excluída — a posição
 *  importada nunca foi tocada, então não há nada mais a desfazer. */
export function MovimentosInvestimento({ recarregar }: { recarregar: number }) {
  const [movs, setMovs] = useState<Movimento[]>([])
  const [total, setTotal] = useState(0)
  const [ini, setIni] = useState('')
  const [fim, setFim] = useState('')
  const [msg, setMsg] = useState('')

  async function load() {
    const r = await api.investimentos.movimentos(ini, fim)
    setMovs(r.movimentos || [])
    setTotal(r.total_resgatado || 0)
  }
  useEffect(() => { load() }, [ini, fim, recarregar])

  async function excluir(m: Movimento) {
    if (!window.confirm(
      `Excluir o resgate de ${formatBRL(m.valor_bruto)} em ${d(m.data)}?\n\n` +
      'A posição volta ao que era antes deste movimento. ' +
      'Rode "Atualizar Posições" depois para o valor na tela acompanhar.')) return
    const r = await api.investimentos.excluirMovimento(m.id)
    setMsg(r.msg || '')
    load()
  }

  if (!movs.length && !ini && !fim) return null

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="text-sm font-semibold text-zinc-200">Movimentos considerados</h2>
        <label className="text-xs text-zinc-500 flex items-center gap-1">
          de <input type="date" value={ini} onChange={e => setIni(e.target.value)}
            className="bg-zinc-800 border border-zinc-700 rounded px-1.5 py-1 text-xs text-zinc-200" />
          até <input type="date" value={fim} onChange={e => setFim(e.target.value)}
            className="bg-zinc-800 border border-zinc-700 rounded px-1.5 py-1 text-xs text-zinc-200" />
        </label>
        {(ini || fim) && (
          <button onClick={() => { setIni(''); setFim('') }}
            className="text-xs text-zinc-500 hover:text-zinc-300">limpar período</button>
        )}
        <span className="ml-auto text-xs text-zinc-500 tabular-nums">
          {movs.length} movimento(s) · {formatBRL(total)} resgatado
        </span>
      </div>

      {msg && <p className="text-xs text-emerald-400">{msg}</p>}

      <div className="rounded-lg border border-zinc-800/60 overflow-x-auto">
        <table className="w-full text-sm min-w-[52rem]">
          <thead>
            <tr className="bg-zinc-900/40 border-b border-zinc-800">
              {['Data', 'Produto', 'Operação', 'Principal', 'Creditado', 'De', 'Para', 'Método', ''].map((h, i) => (
                <th key={h + i} className={cn('px-3 py-2 text-xs text-zinc-500 font-medium',
                  i >= 3 && i <= 6 ? 'text-right' : 'text-left')}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {movs.map((m, i) => (
              <tr key={m.id} className={cn('hover:bg-zinc-800/40', i > 0 && 'border-t border-zinc-800/40')}>
                <td className="px-3 py-2 text-zinc-300 tabular-nums whitespace-nowrap">{d(m.data)}</td>
                <td className="px-3 py-2 text-zinc-400">{m.produto}</td>
                <td className="px-3 py-2 text-zinc-600 text-xs">
                  {m.n_operacao}
                  <span className="block text-zinc-700">
                    apl {d(m.data_aplicacao)} · venc {d(m.data_vencimento)}
                  </span>
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-zinc-400">{formatBRL(m.valor_principal)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-zinc-200">{formatBRL(m.valor_bruto)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-zinc-600">{formatBRL(m.valor_anterior)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-zinc-300">{formatBRL(m.valor_novo)}</td>
                <td className="px-3 py-2 text-xs text-zinc-500">{m.metodo}</td>
                <td className="px-3 py-2 text-right">
                  <button onClick={() => excluir(m)}
                    className="text-xs px-2 py-0.5 rounded border border-zinc-700 text-zinc-500
                      hover:border-red-800 hover:text-red-400 transition-colors">
                    excluir
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
