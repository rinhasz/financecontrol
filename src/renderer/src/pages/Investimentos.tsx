import { useEffect, useState, useRef, useCallback, Fragment } from 'react'
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
  // fase 2: o que a valorização diária produziu
  saldo_posicao: number | null
  data_valorizacao: string | null
  pu_valorizado: number | null
  metodo_valorizacao: string | null
  detalhe_valorizacao: string | null
}

/** Um passo da valorização: como o PU andou naquele dia útil. */
interface PassoMemoria {
  data: string
  pu_anterior: number
  fator: number
  pu: number
  saldo: number
  metodo: string
  detalhe: string
}

const METODO_LABEL: Record<string, string> = {
  di: '% do CDI', pre: 'prefixado', ipca: 'IPCA + juro real',
  mercado: 'fechamento', anbima: 'PU ANBIMA', parado: 'sem valorizar'
}

interface Grupo { chave: string; total: number; itens: number }

interface Posicao {
  data_posicao: string | null
  data_valorizacao: string | null
  itens: Investimento[]
  total: number
  total_posicao: number
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
  const [atualizando, setAtualizando] = useState(false)
  const [msg, setMsg] = useState('')
  const [erro, setErro] = useState('')
  const [memoria, setMemoria] = useState<{ id: number; passos: PassoMemoria[] } | null>(null)
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

  async function atualizarPosicoes() {
    setErro(''); setMsg(''); setAtualizando(true)
    try {
      const r = await api.investimentos.atualizar()
      if (!r.ok) { setErro(r.msg || 'Não consegui atualizar'); return }
      const erros = Object.entries(r.fontes?.erros ?? {})
      setMsg(`Valorizado até ${new Date(r.data_valorizacao + 'T00:00:00').toLocaleDateString('pt-BR')}`
        + ` · ${r.dias_uteis} ${r.dias_uteis === 1 ? 'dia útil' : 'dias úteis'}`
        + (erros.length ? ` · fontes indisponíveis: ${erros.map(([k]) => k).join(', ')}` : ''))
      await load(dataSel || undefined)
    } catch (e) {
      setErro(String(e))
    } finally {
      setAtualizando(false)
    }
  }

  /** A memória é o que torna o número auditável — sem ela, "rendeu 0,10%" é
   *  um ato de fé. Buscada sob demanda: são N papéis × N dias. */
  async function verMemoria(id: number) {
    if (memoria?.id === id) { setMemoria(null); return }
    setMemoria({ id, passos: await api.investimentos.memoria(id) })
  }

  const grupos = pos?.consolidado[agrupamento] ?? []
  const total = pos?.total ?? 0
  const totalPosicao = pos?.total_posicao ?? 0
  const variacao = totalPosicao ? total - totalPosicao : 0

  return (
    <div className="flex flex-col h-full pt-3">
      <div className="px-6 pb-4 flex items-start justify-between flex-none">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">Investimentos</h1>
          {/* a data que importa é a da VALORIZAÇÃO — é a dela que são os
              números da tela. A da importação vira referência secundária, senão
              o cabeçalho diz 25/08 enquanto a tabela mostra o valor de 28/08. */}
          <p className="text-sm text-zinc-500 mt-0.5">
            {!pos?.data_posicao ? 'Nenhuma posição importada ainda.'
              : pos.data_valorizacao && pos.data_valorizacao !== pos.data_posicao
                ? <>Valores em <strong className="text-zinc-300">{data(pos.data_valorizacao)}</strong>
                    {' · '}{pos.itens.length} papéis · importada em {data(pos.data_posicao)}</>
                : <>Posição de <strong className="text-zinc-300">{data(pos.data_posicao)}</strong>
                    {' · '}{pos.itens.length} papéis · <span className="text-amber-500/80">
                    ainda não valorizada</span></>}
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
          <button onClick={() => fileRef.current?.click()} disabled={loading || atualizando}
            className="px-4 py-2 rounded-md border border-zinc-700 text-sm text-zinc-300 disabled:opacity-40 hover:border-zinc-500 hover:text-zinc-100 transition-colors">
            {loading ? 'Lendo...' : 'Importar posição'}
          </button>
          {!!pos?.itens.length && (
            <button onClick={atualizarPosicoes} disabled={loading || atualizando}
              className="px-4 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
              {atualizando ? 'Atualizando...' : 'Atualizar Posições'}
            </button>
          )}
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
            <div className="rounded-lg border border-zinc-700/60 bg-zinc-900/60 p-6 flex items-end gap-10">
              <div>
                <p className="text-xs text-zinc-500 uppercase tracking-wider mb-1">
                  {pos.data_valorizacao ? `Posição em ${data(pos.data_valorizacao)}` : 'Posição total'}
                </p>
                <p className="text-3xl font-bold text-emerald-400 tabular-nums">{formatBRL(total)}</p>
              </div>
              {pos.data_valorizacao && pos.data_valorizacao !== pos.data_posicao && (
                <>
                  <div>
                    <p className="text-xs text-zinc-500 mb-1">Importada em {data(pos.data_posicao)}</p>
                    <p className="text-lg text-zinc-400 tabular-nums">{formatBRL(totalPosicao)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-zinc-500 mb-1">Variação</p>
                    <p className={cn('text-lg font-medium tabular-nums',
                      variacao >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                      {variacao >= 0 ? '+' : ''}{formatBRL(variacao)}
                      <span className="ml-2 text-xs opacity-70">
                        {totalPosicao ? `${(variacao / totalPosicao * 100).toFixed(2)}%` : ''}
                      </span>
                    </p>
                  </div>
                </>
              )}
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
                      <th className="px-3 py-2 text-right font-medium">
                        {pos.data_posicao ? data(pos.data_posicao) : 'Na posição'}
                      </th>
                      <th className="px-3 py-2 text-right font-medium">
                        {pos.data_valorizacao && pos.data_valorizacao !== pos.data_posicao
                          ? data(pos.data_valorizacao) : 'Valorizado'}
                      </th>
                      <th className="px-3 py-2 text-left font-medium">Método</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pos.itens.map((i, k) => (
                      <Fragment key={i.id}>
                      <tr className={cn('hover:bg-zinc-800/40', k > 0 && 'border-t border-zinc-800/40')}>
                        <td className="px-3 py-2 text-zinc-300 font-medium">{i.produto}</td>
                        <td className="px-3 py-2 text-zinc-400">{i.ativo || '—'}</td>
                        <td className="px-3 py-2 text-zinc-500 text-xs">{i.emissor || '—'}</td>
                        <td className="px-3 py-2 text-zinc-400 text-xs">{remuneracao(i)}</td>
                        <td className="px-3 py-2 text-center text-zinc-500 text-xs">{data(i.data_aplicacao)}</td>
                        <td className="px-3 py-2 text-center text-zinc-500 text-xs">{data(i.data_vencimento)}</td>
                        <td className="px-3 py-2 text-center text-zinc-500 text-xs">{data(i.data_liquidez)}</td>
                        {/* o PU exibido é o valorizado: a regra do doc 16 é
                            "valor = PU x quantidade", e mostrar o PU da
                            importação ao lado do valor de hoje faz a conta não
                            fechar na tela */}
                        <td className="px-3 py-2 text-right text-zinc-500 tabular-nums text-xs"
                          title={i.pu_valorizado != null && i.pu != null && i.pu_valorizado !== i.pu
                            ? `PU na importação: ${i.pu.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 6 })}`
                            : undefined}>
                          {(i.pu_valorizado ?? i.pu)?.toLocaleString('pt-BR',
                            { minimumFractionDigits: 2, maximumFractionDigits: 6 })}
                        </td>
                        <td className="px-3 py-2 text-right text-zinc-500 tabular-nums text-xs">
                          {i.quantidade?.toLocaleString('pt-BR', { maximumFractionDigits: 2 }) ?? '—'}
                        </td>
                        <td className="px-3 py-2 text-right text-zinc-500 tabular-nums text-xs">
                          {i.saldo_posicao != null ? formatBRL(i.saldo_posicao) : '—'}
                        </td>
                        <td className="px-3 py-2 text-right text-zinc-200 tabular-nums font-medium">
                          {i.saldo != null ? formatBRL(i.saldo) : '—'}
                        </td>
                        <td className="px-3 py-2 text-xs">
                          {i.metodo_valorizacao ? (
                            <button onClick={() => verMemoria(i.id)}
                              className={cn('hover:text-emerald-400 transition-colors',
                                i.metodo_valorizacao === 'parado' ? 'text-amber-400' : 'text-zinc-500')}
                              title={i.detalhe_valorizacao ?? 'Ver a memória de cálculo'}>
                              <span className="inline-block w-3 text-zinc-600">
                                {memoria?.id === i.id ? '−' : '+'}
                              </span>
                              {METODO_LABEL[i.metodo_valorizacao] ?? i.metodo_valorizacao}
                            </button>
                          ) : <span className="text-zinc-700">—</span>}
                        </td>
                      </tr>
                      {memoria?.id === i.id && (
                        <tr className="bg-zinc-900/70">
                          <td colSpan={11} className="px-3 py-2">
                            <table className="text-xs">
                              <tbody>
                                {memoria.passos.map(p => (
                                  <tr key={p.data}>
                                    <td className="pr-4 py-0.5 text-zinc-500">{data(p.data)}</td>
                                    <td className="pr-4 py-0.5 text-zinc-500 tabular-nums">
                                      PU {p.pu_anterior?.toFixed(6)} → {p.pu?.toFixed(6)}
                                    </td>
                                    <td className="pr-4 py-0.5 text-zinc-500 tabular-nums">
                                      × {p.fator?.toFixed(8)}
                                    </td>
                                    <td className="pr-4 py-0.5 text-zinc-300 tabular-nums">{formatBRL(p.saldo)}</td>
                                    <td className="py-0.5 text-zinc-600">{p.detalhe}</td>
                                  </tr>
                                ))}
                                {!memoria.passos.length && (
                                  <tr><td className="py-0.5 text-zinc-600">
                                    Nenhum dia útil entre a posição e hoje.
                                  </td></tr>
                                )}
                              </tbody>
                            </table>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-xs text-zinc-600 mt-1.5">
                <strong className="text-zinc-500">Valorizado</strong> é a posição trazida até
                hoje dia útil a dia útil; clique no método para ver a memória de cálculo passo a
                passo. Papel em âmbar não pôde ser valorizado — passe o mouse para saber por quê.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
