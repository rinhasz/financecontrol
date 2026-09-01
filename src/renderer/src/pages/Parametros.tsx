import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { formatBRL, cn } from '../lib/utils'

/** Um parâmetro do app: o que é, como se escreve e por que existe.
 *
 *  A descrição não é enfeite. Estes números mudam o resultado de telas
 *  inteiras — o dia do salário move a janela do mês, a reserva move o quanto
 *  resgatar — e um campo sem explicação vira um número que ninguém ousa mexer.
 */
interface Param {
  chave: string
  label: string
  ajuda: string
  tipo: 'dinheiro' | 'inteiro'
  min?: number
  max?: number
  sufixo?: string
}

const PARAMS: Param[] = [
  {
    chave: 'reserva_desejada', label: 'Reserva mínima', tipo: 'dinheiro',
    ajuda: 'Piso que o saldo da conta nunca deve furar. É o que a calculadora ' +
      'de resgate protege e o que o plano de resgates mantém coberto todos os dias.'
  },
  {
    chave: 'dia_recebimento_salario', label: 'Dia do salário', tipo: 'inteiro',
    min: 1, max: 31, sufixo: 'do mês',
    ajuda: 'Abre o mês de competência: o mês vai deste dia até a véspera do mesmo ' +
      'dia no mês seguinte. Se cair em fim de semana ou feriado, o app antecipa ' +
      'para o dia útil anterior, porque é quando o crédito entra.'
  },
  {
    chave: 'resgates_por_mes', label: 'Resgates por mês', tipo: 'inteiro',
    min: 1, max: 10, sufixo: 'resgates',
    ajuda: 'Quantas vezes você quer resgatar no mês. Não é limite físico — um ' +
      'resgate só no começo do mês sempre resolveria. Mais resgates deixam o ' +
      'dinheiro investido por mais tempo, ao custo de mais operações. ' +
      'Investimento que vence no mês já ocupa uma dessas vagas.'
  },
]

export function Parametros() {
  const [valores, setValores] = useState<Record<string, string>>({})
  const [salvo, setSalvo] = useState<string | null>(null)
  const [carregando, setCarregando] = useState(true)

  useEffect(() => {
    api.config.get().then((c: Record<string, string>) => {
      setValores(Object.fromEntries(PARAMS.map(p => [p.chave, String(c[p.chave] ?? '')])))
      setCarregando(false)
    })
  }, [])

  async function salvar(p: Param) {
    const bruto = (valores[p.chave] || '').replace(',', '.')
    const n = p.tipo === 'inteiro' ? parseInt(bruto, 10) : parseFloat(bruto)
    if (isNaN(n)) return
    const limitado = Math.min(p.max ?? Infinity, Math.max(p.min ?? -Infinity, n))
    await api.config.set({ [p.chave]: limitado })
    setValores(v => ({ ...v, [p.chave]: String(limitado) }))
    setSalvo(p.chave)
    setTimeout(() => setSalvo(s => (s === p.chave ? null : s)), 1800)
  }

  return (
    <div className="h-full overflow-auto">
      <div className="max-w-2xl mx-auto px-6 py-8 space-y-6">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">Parâmetros</h1>
          <p className="text-sm text-zinc-500 mt-1">
            Os números que governam o comportamento do app. Cada campo salva ao sair dele.
          </p>
        </div>

        {carregando ? (
          <p className="text-sm text-zinc-500">Carregando...</p>
        ) : (
          <div className="space-y-3">
            {PARAMS.map(p => (
              <div key={p.chave}
                className="rounded-lg border border-zinc-800/60 bg-zinc-900/40 p-4 space-y-2">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <label htmlFor={p.chave} className="text-sm font-medium text-zinc-200">
                      {p.label}
                    </label>
                    <p className="text-xs text-zinc-500 mt-1 leading-relaxed">{p.ajuda}</p>
                  </div>
                  <div className="flex items-center gap-2 flex-none">
                    <input id={p.chave} value={valores[p.chave] ?? ''}
                      onChange={e => setValores(v => ({ ...v, [p.chave]: e.target.value }))}
                      onBlur={() => salvar(p)}
                      onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
                      className="w-28 bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-sm
                        text-zinc-100 text-right tabular-nums outline-none focus:border-emerald-500" />
                    {p.sufixo && <span className="text-xs text-zinc-600 w-16">{p.sufixo}</span>}
                  </div>
                </div>
                <div className="flex items-center gap-2 h-4">
                  {p.tipo === 'dinheiro' && !isNaN(parseFloat(valores[p.chave])) && (
                    <span className="text-xs text-zinc-600 tabular-nums">
                      {formatBRL(parseFloat(valores[p.chave]))}
                    </span>
                  )}
                  <span className={cn('text-xs text-emerald-400 transition-opacity',
                    salvo === p.chave ? 'opacity-100' : 'opacity-0')}>salvo</span>
                </div>
              </div>
            ))}
          </div>
        )}

        <p className="text-xs text-zinc-600 leading-relaxed">
          O saldo da conta não fica aqui de propósito: ele é lido do extrato a cada
          importação, e digitá-lo à mão é a exceção — feita na própria tela do Mês Atual,
          onde dá para ver a data a que ele se refere.
        </p>
      </div>
    </div>
  )
}
