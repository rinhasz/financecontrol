import { useEffect, useState, useCallback } from 'react'
import { api } from '../lib/api'
import { formatBRL, mesRefLabel, prevMesRef, nextMesRef, cn } from '../lib/utils'

interface EventoDia {
  data: string; valor: number; rotulo: string
  especie: 'agendado' | 'previsto' | 'vencimento'
}
interface Dia {
  data: string; delta: number; resgate: number; saldo: number
  abaixo_reserva: boolean; eventos: EventoDia[]
}
interface Vencimento { data: string; valor: number; rotulo: string }
interface Plano {
  ok: boolean
  mes_ref: string
  periodo: { ini: string; fim: string }
  saldo_inicial: number; saldo_data: string; reserva: number
  resgates_por_mes: number; vagas_livres: number
  total_a_resgatar: number
  vencimentos: Vencimento[]
  resgates: { data: string; valor: number }[]
  saldo_final: number
  dias: Dia[]
}

const dia = (iso: string) =>
  new Date(iso + 'T00:00:00').toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })
const diaSemana = (iso: string) =>
  new Date(iso + 'T00:00:00').toLocaleDateString('pt-BR', { weekday: 'short' }).replace('.', '')

const COR_ESPECIE: Record<string, string> = {
  agendado: 'text-blue-400/80', previsto: 'text-amber-400/80', vencimento: 'text-violet-400/90'
}

/** Barra do saldo do dia, com a reserva marcada.
 *
 *  Os números dizem quanto; o desenho diz o quao perto do chao cada dia passa,
 *  que e a informacao para a qual o plano existe. A marca da reserva fica na
 *  mesma posicao em todas as barras, entao a folga se le de relance, sem
 *  comparar valores um a um. */
function BarraSaldo({ saldo, reserva, teto }: { saldo: number; reserva: number; teto: number }) {
  const pct = teto > 0 ? Math.max(0, Math.min(100, (saldo / teto) * 100)) : 0
  const pctReserva = teto > 0 ? Math.max(0, Math.min(100, (reserva / teto) * 100)) : 0
  const furou = saldo < reserva
  return (
    <div className="relative h-3 w-full rounded-sm bg-zinc-800/60 overflow-hidden">
      <div className={cn('h-full',
        furou ? 'bg-red-500/50' : saldo < reserva * 2 ? 'bg-amber-500/40' : 'bg-emerald-500/35')}
        style={{ width: pct + '%' }} />
      <div className="absolute inset-y-0 w-px bg-zinc-400/50" style={{ left: pctReserva + '%' }}
        title={'Reserva ' + formatBRL(reserva)} />
    </div>
  )
}

export function PlanejarResgates({ mesInicial }: { mesInicial?: string }) {
  const [mesRef, setMesRef] = useState(mesInicial || '')
  const [plano, setPlano] = useState<Plano | null>(null)
  const [loading, setLoading] = useState(false)
  const [soMovimento, setSoMovimento] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await api.resgates.plano(mesRef)
      setPlano(d)
      if (!mesRef && d?.mes_ref) setMesRef(d.mes_ref)
    } finally {
      setLoading(false)
    }
  }, [mesRef])

  useEffect(() => { load() }, [load])

  if (loading && !plano) {
    return <div className="flex items-center justify-center h-40 text-zinc-500">Calculando...</div>
  }
  if (!plano?.ok) {
    return <div className="p-6 text-sm text-zinc-500">Nao foi possivel montar o plano.</div>
  }

  const teto = Math.max(...plano.dias.map(d => d.saldo), plano.saldo_inicial, plano.reserva) || 1
  const visiveis = plano.dias.filter(d =>
    !soMovimento || Math.abs(d.delta) > 0.005 || d.resgate > 0 || d.abaixo_reserva)
  const furou = plano.dias.some(d => d.abaixo_reserva)

  return (
    <div className="h-full overflow-auto">
      <div className="max-w-4xl mx-auto px-6 py-6 space-y-5">

        <div className="flex items-center gap-3">
          <button onClick={() => setMesRef(prevMesRef(mesRef))}
            className="px-2 py-1 rounded text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800">&lsaquo;</button>
          <div>
            <h1 className="text-xl font-semibold text-zinc-100">
              Planejar resgates &mdash; {mesRefLabel(mesRef)}
            </h1>
            <p className="text-xs text-zinc-500 mt-0.5">
              {dia(plano.periodo.ini)} a {dia(plano.periodo.fim)} &middot; linha do tempo a partir
              do saldo de {dia(plano.saldo_data)}
            </p>
          </div>
          <button onClick={() => setMesRef(nextMesRef(mesRef))}
            className="px-2 py-1 rounded text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800">&rsaquo;</button>

          {/* O plano é calculado na abertura da tela e depois congela. Saldo
              novo, conta agendada, resgate importado — nada disso reaparece
              aqui sem recalcular, e um plano velho é pior que nenhum. */}
          <button onClick={load} disabled={loading}
            title="Refaz o plano com o saldo, os lançamentos e os vencimentos de agora"
            className="ml-auto px-3 py-1.5 rounded-md border border-zinc-700 text-sm text-zinc-300
              disabled:opacity-40 hover:border-zinc-500 hover:text-zinc-100 transition-colors">
            {loading ? 'Atualizando...' : 'Atualizar'}
          </button>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-zinc-800 border border-zinc-800 rounded-lg overflow-hidden">
          {[
            ['Saldo hoje', formatBRL(plano.saldo_inicial), 'text-zinc-200'],
            ['Reserva minima', formatBRL(plano.reserva), 'text-zinc-400'],
            ['A resgatar no mes', formatBRL(plano.total_a_resgatar),
              plano.total_a_resgatar > 0 ? 'text-amber-400' : 'text-emerald-400'],
            ['Saldo no fim', formatBRL(plano.saldo_final), 'text-zinc-200'],
          ].map(([rot, val, cor]) => (
            <div key={rot} className="bg-zinc-900/60 px-4 py-3">
              <p className="text-[11px] uppercase tracking-wider text-zinc-500">{rot}</p>
              <p className={cn('text-lg font-semibold tabular-nums mt-0.5', cor)}>{val}</p>
            </div>
          ))}
        </div>

        <div className="rounded-lg border border-zinc-800/60 bg-zinc-900/40 p-4 space-y-3">
          <div className="flex items-baseline justify-between gap-3 flex-wrap">
            <h2 className="text-sm font-semibold text-zinc-200">Plano sugerido</h2>
            <p className="text-xs text-zinc-500">
              {plano.resgates_por_mes} resgate{plano.resgates_por_mes > 1 ? 's' : ''} por mes
              {plano.vencimentos.length > 0 &&
                ' · ' + plano.vencimentos.length + ' ja ocupado por vencimento'}
              {' · '}<span className="text-zinc-600">ajuste em Parametros</span>
            </p>
          </div>

          {plano.vencimentos.length > 0 && (
            <div className="space-y-1">
              <p className="text-[11px] uppercase tracking-wider text-violet-400/70">
                Resgates certos &mdash; vencimentos
              </p>
              {plano.vencimentos.map((v, i) => (
                <div key={i} className="flex items-center gap-3 text-sm">
                  <span className="w-12 text-zinc-500 tabular-nums">{dia(v.data)}</span>
                  <span className="flex-1 text-zinc-300">{v.rotulo}</span>
                  <span className="tabular-nums text-violet-300">{formatBRL(v.valor)}</span>
                </div>
              ))}
              <p className="text-xs text-zinc-600 pt-1">
                Vence sozinho e cai na conta &mdash; o plano abaixo ja conta com esse dinheiro.
              </p>
            </div>
          )}

          {plano.resgates.length === 0 ? (
            <p className="text-sm text-emerald-400">
              Nenhum resgate necessario: o saldo cobre o mes inteiro sem furar a reserva.
            </p>
          ) : (
            <div className="space-y-1">
              <p className="text-[11px] uppercase tracking-wider text-amber-400/70">Programar</p>
              {plano.resgates.map((r, i) => (
                <div key={i} className="flex items-center gap-3 text-sm">
                  <span className="w-12 text-zinc-400 tabular-nums">{dia(r.data)}</span>
                  <span className="w-10 text-zinc-600 text-xs">{diaSemana(r.data)}</span>
                  <span className="flex-1 text-zinc-500 text-xs">
                    ultimo dia antes de o saldo furar a reserva
                  </span>
                  <span className="tabular-nums text-amber-300 font-medium">{formatBRL(r.valor)}</span>
                </div>
              ))}
            </div>
          )}

          {furou && (
            <p className="text-xs text-red-400">
              Mesmo com o plano ha dias abaixo da reserva &mdash; nao ha entrada suficiente no mes
              para cobri-los.
            </p>
          )}
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-zinc-200">Linha do tempo</h2>
            <button onClick={() => setSoMovimento(s => !s)}
              className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors">
              {soMovimento ? 'mostrar todos os dias' : 'so dias com movimento'}
            </button>
          </div>

          <div className="rounded-lg border border-zinc-800/60 overflow-hidden">
            {visiveis.map((d, i) => (
              <div key={d.data}
                className={cn('grid grid-cols-[3.2rem_2.2rem_1fr_7rem_7rem_8rem] gap-2 items-center px-3 py-2 text-sm',
                  i > 0 && 'border-t border-zinc-800/40',
                  d.resgate > 0 && 'bg-amber-950/20',
                  d.abaixo_reserva && 'bg-red-950/25')}>
                <span className="text-zinc-400 tabular-nums">{dia(d.data)}</span>
                <span className="text-zinc-600 text-xs">{diaSemana(d.data)}</span>
                <div className="min-w-0">
                  {d.eventos.length === 0
                    ? <span className="text-zinc-700 text-xs">&mdash;</span>
                    : (
                      <div className="flex flex-wrap gap-x-3 gap-y-0.5">
                        {d.eventos.map((e, j) => (
                          <span key={j} className={cn('text-xs truncate', COR_ESPECIE[e.especie])}
                            title={e.rotulo + ': ' + formatBRL(e.valor) + ' (' + e.especie + ')'}>
                            {e.rotulo}
                          </span>
                        ))}
                      </div>
                    )}
                </div>
                <span className={cn('text-right tabular-nums text-xs',
                  d.delta < 0 ? 'text-zinc-400' : d.delta > 0 ? 'text-emerald-400/80' : 'text-zinc-700')}>
                  {Math.abs(d.delta) > 0.005 ? formatBRL(d.delta) : ''}
                </span>
                <span className="text-right tabular-nums text-amber-300 font-medium">
                  {d.resgate > 0 ? formatBRL(d.resgate) : ''}
                </span>
                <div className="flex flex-col gap-1">
                  <span className={cn('text-right tabular-nums text-xs',
                    d.abaixo_reserva ? 'text-red-400' : 'text-zinc-300')}>
                    {formatBRL(d.saldo)}
                  </span>
                  <BarraSaldo saldo={d.saldo} reserva={plano.reserva} teto={teto} />
                </div>
              </div>
            ))}
          </div>

          <div className="flex gap-4 text-[11px] text-zinc-600">
            <span className="text-blue-400/70">agendado</span>
            <span className="text-amber-400/70">previsto</span>
            <span className="text-violet-400/80">vencimento</span>
            <span className="ml-auto">a marca clara na barra e a reserva</span>
          </div>
        </div>
      </div>
    </div>
  )
}
