import { useEffect, useState, useCallback, Fragment } from 'react'
import { api } from '../lib/api'
import { formatBRL, mesRefLabel, currentMesRef, prevMesRef, nextMesRef, cn } from '../lib/utils'

interface Lancamento {
  id: number
  mes_ref: string
  item_id: number
  item_nome: string
  categoria_nome: string
  valor_esperado: number
  valor_real: number | null
  status: 'pago' | 'agendado' | 'nao_encontrado'
  data_pagamento: string | null
  linha_digitavel: string | null
  tipo_codigo: 'boleto' | 'pix' | null
  // esporádica não tem lançamento: a linha vem da transação (doc 14), o id é
  // negativo e o valor não é editável — quem manda é o extrato
  recorrencia: 'fixa' | 'esporadica'
  descricao_transacao?: string
  // a previsão desta linha foi corrigida à mão neste mês (api/projecao.py)
  projecao_manual?: boolean
  // vencimento esperado, calculado no servidor: a competência atravessa dois
  // meses do calendário, então a data não se monta só com o mes_ref
  data_prevista?: string | null
  dias_para_vencer?: number | null
}

interface Receita {
  id: number
  item_id: number
  item_nome: string
  tipo: string
  recorrencia: 'fixa' | 'esporadica'
  valor_esperado: number
  valor_real: number | null
  status: 'recebido' | 'previsto' | 'nao_encontrado'
  data_recebimento: string | null
  descricao_transacao?: string
  objetivo?: string | null
  projecao_manual?: boolean
  // dia previsto de recebimento, calculado no servidor (igual à despesa)
  data_prevista?: string | null
  dias_para_vencer?: number | null
}

interface Resumo {
  periodo: { ini: string; fim: string }
  pago: number; agendado: number; naoEncontrado: number
  // o que o projetado diz que ainda vai acontecer, e a soma com o agendado —
  // é isso que precisa de dinheiro, não o que já saiu da conta
  aRealizar: number; aVencer: number; aReceber: number
  saldoData: string
  total: number; reserva: number; saldo: number
  renda: number; rendaRecebida: number
  movimentacao: Record<string, number>
  saldoMes: number
  resgateNecessario: number; resgateJaFeito: number; faltaResgatar: number
}

const TIPO_LABEL: Record<string, string> = {
  salario: 'Salário', juros: 'Juros', reembolso: 'Reembolso', outra: 'Outra',
  resgate_mensal: 'Resgate mensal', resgate_esporadico: 'Resgate esporádico',
  estorno: 'Estorno', transferencia: 'Transferência'
}

// Os mesmos tipos que o backend trata como "não é renda" (api/receitas.py).
// Aqui só decide em qual bloco a linha aparece; o total vem pronto do resumo,
// para a regra não existir em dois lugares que podem discordar.
const TIPOS_MOVIMENTACAO = new Set(['resgate_mensal', 'resgate_esporadico', 'estorno', 'transferencia'])

const STATUS_RECEITA_LABEL: Record<string, string> = {
  recebido: 'Recebido', previsto: 'Previsto', nao_encontrado: 'Em aberto'
}

/** Item que não aconteceu ainda: o valor exibido não veio do extrato, veio da
 *  projeção. Sem dizer isso, um previsto se confunde com um realizado — e a
 *  diferença é justamente o que separa "já saiu da conta" de "estimativa". */
function SeloPrevisto({ manual, onLimpar }: { manual?: boolean; onLimpar?: () => void }) {
  if (manual) {
    // cor diferente porque a origem é diferente: este número foi decidido, não
    // estimado, e clicar devolve a linha para o cálculo automático
    return (
      <button onClick={onLimpar}
        className="ml-1.5 px-1.5 py-0.5 rounded text-[10px] font-semibold tracking-wide
          text-sky-400 bg-sky-950/40 border border-sky-800/40 hover:border-sky-600 transition-colors"
        title="Você corrigiu esta previsão à mão. Clique para voltar ao cálculo automático.">
        AJUSTADO
      </button>
    )
  }
  return (
    <span className="ml-1.5 px-1.5 py-0.5 rounded text-[10px] font-semibold tracking-wide
      text-amber-400 bg-amber-950/40 border border-amber-800/40"
      title="Valor estimado pelo método de projeção cadastrado, não lido do extrato. Clique no valor para corrigir.">
      PREVISTO
    </span>
  )
}

/** Quantos dias antes do vencimento uma despesa não agendada vira alerta.
 *  Três dias é o que sobra para agendar e ainda cair na data — abaixo disso já
 *  é corrida contra o relógio do banco. */
const DIAS_ALERTA = 3

/** Uma linha só precisa de um dia previsto e um status para ser julgada — por
 *  isso serve despesa e receita, que têm os dois com nomes diferentes. */
interface Vencivel {
  status: string
  dias_para_vencer?: number | null
}

/** `null` quando não há o que alertar.
 *
 *  Duas situações diferentes, e a diferença é a data:
 *
 *  - **antes** do vencimento, só preocupa o que **não está nem agendado**. Uma
 *    conta agendada que vence amanhã está resolvida — o dinheiro está
 *    comprometido e a ordem foi dada;
 *  - **depois** do vencimento, agendado também vira problema: a data passou e o
 *    débito não caiu. Ou falhou, ou o extrato ainda não foi reimportado — nos
 *    dois casos é para olhar.
 */
function urgencia(l: Vencivel): 'atrasado' | 'urgente' | null {
  const feito = l.status === 'pago' || l.status === 'recebido'
  if (feito) return null
  const d = l.dias_para_vencer
  if (d === null || d === undefined) return null
  if (d < 0) return 'atrasado'
  // ainda no prazo: agendado está resolvido, o resto não
  if (l.status !== 'nao_encontrado') return null
  return d <= DIAS_ALERTA ? 'urgente' : null
}

function SeloVencimento({ nivel, dias }: { nivel: 'atrasado' | 'urgente'; dias: number }) {
  if (nivel === 'atrasado') {
    return (
      <span className="ml-2 inline-flex items-center gap-1 px-1.5 py-0.5 rounded
        text-[10px] font-semibold tracking-wide border
        text-red-300 bg-red-950/50 border-red-800/60"
        title="Passou do dia previsto e ainda não foi pago/recebido">
        <span aria-hidden>!</span>ATRASADO
        <span className="font-normal opacity-80">
          {dias === -1 ? 'há 1 dia' : `há ${-dias} dias`}
        </span>
      </span>
    )
  }
  const texto = dias === 0 ? 'vence hoje' : dias === 1 ? 'vence amanhã' : `vence em ${dias} dias`
  return (
    <span className="ml-2 inline-flex items-center gap-1 px-1.5 py-0.5 rounded
      text-[10px] font-semibold tracking-wide border
      text-orange-300 bg-orange-950/40 border-orange-800/50"
      title="Ainda não está agendada — sem agendar, o risco é multa e juros">
      <span aria-hidden>!</span>{texto}
    </span>
  )
}

/** A célula de data das duas tabelas.
 *
 *  Mostra sempre o **dia previsto**, e não só a data em que aconteceu: sem ele
 *  não dá para saber se a linha está no prazo, e era essa a informação que
 *  faltava para o "atrasado" fazer sentido. Quando já aconteceu, a data real
 *  vem em cima e o previsto embaixo, esmaecido. */
function CelulaData({ realizada, prevista, atrasado }: {
  realizada?: string | null; prevista?: string | null; atrasado?: boolean
}) {
  const fmt = (iso: string) => new Date(iso + 'T00:00:00').toLocaleDateString('pt-BR')
  const prev = prevista
    ? new Date(prevista + 'T00:00:00').toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })
    : null
  if (realizada) {
    return (
      <span className="text-xs">
        <span className="text-zinc-400">{fmt(realizada)}</span>
        {prev && <span className="block text-zinc-700">venc. {prev}</span>}
      </span>
    )
  }
  if (!prev) return <span className="text-xs text-zinc-700">—</span>
  return (
    <span className={cn('text-xs', atrasado ? 'text-red-400' : 'text-zinc-500')}>
      venc. {prev}
    </span>
  )
}

/** Os totais de um grupo, das parcelas para os agregados.
 *
 *  `agendado` e `previsto` são as duas parcelas do que ainda não saiu da conta,
 *  e não se sobrepõem: uma tem data, a outra é estimativa da projeção. `a
 *  vencer` é a soma delas — o número que de fato precisa de dinheiro, e o mesmo
 *  sentido do card "A vencer" do resumo. `pago` é o que já saiu.
 *
 *  Um número só escondia justamente a diferença que decide o quanto dá para
 *  confiar no total: um grupo todo agendado e um grupo todo projetado somavam
 *  igual. As cores repetem os selos de status das linhas — azul agendado,
 *  âmbar previsto, verde pago.
 */
function TotaisDoGrupo({ agendado, previsto, pago, total, entrada }: {
  agendado: number; previsto: number; pago: number; total: number
  /** lado da entrada: troca "a vencer"/"pago" por "a receber"/"recebido" */
  entrada?: boolean
}) {
  const dir = entrada ? 'entrar na' : 'sair da'
  return (
    <span className="flex items-baseline gap-2.5 text-xs tabular-nums whitespace-nowrap">
      <span className="text-blue-400/70" title={`Tem data marcada e ainda não foi ${entrada ? 'creditado' : 'debitado'}`}>
        agendado <span className="font-medium">{formatBRL(agendado)}</span>
      </span>
      <span className="text-amber-400/70" title="Em aberto: estimado pela projeção, ainda não confirmado">
        previsto <span className="font-medium">{formatBRL(previsto)}</span>
      </span>
      <span className="text-zinc-400" title={`Agendado + previsto: tudo que ainda vai ${dir} conta`}>
        {entrada ? 'a receber' : 'a vencer'} <span className="font-medium text-zinc-300">{formatBRL(agendado + previsto)}</span>
      </span>
      <span className="text-emerald-400/70" title={`Já ${entrada ? 'entrou na' : 'saiu da'} conta`}>
        {entrada ? 'recebido' : 'pago'} <span className="font-medium">{formatBRL(pago)}</span>
      </span>
      <span className="text-zinc-500" title={`Tudo do grupo: ${entrada ? 'recebido + a receber' : 'pago + a vencer'}`}>
        total <span className="font-medium text-zinc-300">{formatBRL(total)}</span>
      </span>
    </span>
  )
}

type Visao = 'analitica' | 'consolidada'

/** Linha da visão consolidada: uma por item, com as ocorrências somadas e o
 *  estorno já abatido. */
interface LinhaConsolidada {
  tipo: 'gasto' | 'estorno'
  valor: number
  status: string | null
  data: string | null
  descricao: string
}
interface DespesaConsolidada {
  item_id: number; item_nome: string; categoria_nome: string | null
  bruto: number; estornado: number; liquido: number; ocorrencias: number
  linhas: LinhaConsolidada[]
}

type OrdemCons = 'valor' | 'alfabetica'
const ORDEM_CONS_LABEL: Record<OrdemCons, string> = {
  valor: 'Maior valor', alfabetica: 'Ordem alfabética'
}
interface ReceitaConsolidada {
  item_id: number; item_nome: string; tipo: string
  total: number; ocorrencias: number; renda: boolean
}
interface Consolidado {
  despesas: DespesaConsolidada[]
  // anularam por completo: fora do total, mas exibidos à parte para o gasto e
  // a devolução não sumirem do mês sem deixar rastro
  anulados: DespesaConsolidada[]
  receitas: ReceitaConsolidada[]
  totais: { despesas: number; estornado: number; renda: number; movimentacao: number }
}

const STATUS_STYLE = {
  pago:           'bg-emerald-950/50 text-emerald-400 border-emerald-800/50',
  agendado:       'bg-blue-950/50 text-blue-400 border-blue-800/50',
  nao_encontrado: 'bg-zinc-800/30 text-zinc-500 border-zinc-700/30'
}
const STATUS_LABEL = { pago: 'Pago', agendado: 'Agendado', nao_encontrado: 'Em aberto' }
const ROW_BG = {
  pago:           'bg-emerald-950/20 hover:bg-emerald-950/30',
  agendado:       'bg-blue-950/20 hover:bg-blue-950/30',
  nao_encontrado: 'hover:bg-zinc-800/40'
}

export function MesAtual({ onPlanejarResgates }: { onPlanejarResgates?: () => void }) {
  const [mesRef, setMesRef] = useState(currentMesRef())
  const [lancamentos, setLancamentos] = useState<Lancamento[]>([])
  const [receitas, setReceitas] = useState<Receita[]>([])
  const [visao, setVisao] = useState<Visao>('analitica')
  const [consolidado, setConsolidado] = useState<Consolidado | null>(null)
  const [resumo, setResumo] = useState<Resumo | null>(null)
  const [loading, setLoading] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [editVal, setEditVal] = useState('')
  const [saldoEdit, setSaldoEdit] = useState(false)
  const [saldoVal, setSaldoVal] = useState('')
  const [diaSalarioEdit, setDiaSalarioEdit] = useState(false)
  const [diaSalarioVal, setDiaSalarioVal] = useState('27')
  const [boletoCopiado, setBoletoCopiado] = useState<number | null>(null)
  // "pendências" = tudo que ainda não está nem agendado, independentemente da
  // data. O alerta de vencimento é o subconjunto urgente disso.
  const [soPendencias, setSoPendencias] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [data, res, cfg, cons] = await Promise.all([
        api.lancamentos.list(mesRef),
        api.lancamentos.resumo(mesRef),
        api.config.get(),
        api.lancamentos.consolidado(mesRef)
      ])
      setLancamentos(data.despesas ?? [])
      setReceitas(data.receitas ?? [])
      setResumo(res)
      setConsolidado(cons)
      setSaldoVal(String(res.saldo))
      setDiaSalarioVal(String(cfg.dia_recebimento_salario ?? '27'))
    } finally {
      setLoading(false)
    }
  }, [mesRef])

  useEffect(() => { load() }, [load])

  /** Editar o valor de uma linha.
   *
   *  Em aberto, o número na tela **é a projeção** — gravá-lo em
   *  `lancamento.valor_esperado` não colava, porque a projeção sobrescreve esse
   *  campo a cada carregamento. Por isso a correção de uma linha em aberto vai
   *  para `projecao_manual`, que tem precedência sobre o cálculo automático.
   *  Já pago ou agendado continua sendo edição do lançamento. */
  async function saveValor(l: Lancamento) {
    const val = parseFloat(editVal.replace(',', '.'))
    if (!isNaN(val)) {
      if (l.status === 'nao_encontrado') {
        await api.projecao.manual('despesa', l.item_id, mesRef, val)
      } else {
        await api.lancamentos.update(l.id, { valor_esperado: val })
      }
    }
    setEditId(null)
    load()
  }

  /** Devolve a linha para a projeção automática. */
  async function limparProjecao(natureza: 'despesa' | 'receita', itemId: number) {
    await api.projecao.manual(natureza, itemId, mesRef, null)
    load()
  }

  async function saveSaldo() {
    const val = parseFloat(saldoVal.replace(',', '.'))
    if (!isNaN(val)) await api.config.set({ saldo_conta: val })
    setSaldoEdit(false)
    load()
  }

  async function saveDiaSalario() {
    const val = parseInt(diaSalarioVal, 10)
    if (!isNaN(val) && val >= 1 && val <= 31) await api.config.set({ dia_recebimento_salario: val })
    setDiaSalarioEdit(false)
    load()
  }

  async function resetarMes() {
    const ok = window.confirm(
      `Resetar ${mesRefLabel(mesRef)}? Isso volta todas as despesas desse mês pra "Em aberto" e desfaz os ` +
      'casamentos com o extrato. As transações importadas continuam no banco (não precisa reimportar).'
    )
    if (!ok) return
    await api.batimento.resetar(mesRef)
    load()
  }

  async function copiarBoleto(l: Lancamento) {
    if (!l.linha_digitavel) return
    await navigator.clipboard.writeText(l.linha_digitavel.replace(/\s+/g, ''))
    setBoletoCopiado(l.id)
    setTimeout(() => setBoletoCopiado(null), 1500)
  }

  // O tipo do item decide de qual lado da linha ele entra. A soma da renda vem
  // pronta do resumo — aqui só se decide onde a linha aparece.
  const renda = receitas.filter(r => !TIPOS_MOVIMENTACAO.has(r.tipo))
  const movimentacao = receitas.filter(r => TIPOS_MOVIMENTACAO.has(r.tipo))

  const valorDaLinha = (l: Lancamento) => l.valor_real ?? l.valor_esperado
  const somaPorStatus = (itens: Lancamento[], st: Lancamento['status']) =>
    itens.filter(l => l.status === st).reduce((s, l) => s + valorDaLinha(l), 0)

  const pendentes = lancamentos.filter(l => l.status === 'nao_encontrado')
  // atrasado e urgente são avisos diferentes: um já passou do dia, o outro
  // ainda dá tempo. Somá-los num contador só apagaria a diferença.
  const atrasados = [...lancamentos, ...receitas].filter(x => urgencia(x) === 'atrasado')
  const urgentes = lancamentos.filter(l => urgencia(l) === 'urgente')
  const listadas = soPendencias ? pendentes : lancamentos

  const byCategory = listadas.reduce<Record<string, Lancamento[]>>((acc, l) => {
    const cat = l.categoria_nome || 'Outros'
    if (!acc[cat]) acc[cat] = []
    acc[cat].push(l)
    return acc
  }, {})

  return (
    <div className="flex flex-col h-full pt-3">
      {/* Header */}
      <div className="px-6 pb-4 flex items-center justify-between flex-none">
        <div className="flex items-center gap-3">
          <button onClick={() => setMesRef(prevMesRef(mesRef))}
            className="p-1.5 rounded-md text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors">
            <ChevronLeft />
          </button>
          <h1 className="text-xl font-semibold text-zinc-100 w-28 text-center">{mesRefLabel(mesRef)}</h1>
          <button onClick={() => setMesRef(nextMesRef(mesRef))}
            className="p-1.5 rounded-md text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors">
            <ChevronRight />
          </button>
        </div>
        <div className="flex items-center gap-4">
          {/* duas perguntas diferentes: a analítica mostra o mês como aconteceu,
              linha a linha, para conferir contra o extrato; a consolidada
              responde quanto cada item custou de fato, somando repetições e
              abatendo estorno. */}
          <div className="flex rounded-md border border-zinc-700 overflow-hidden">
            {(['analitica', 'consolidada'] as Visao[]).map(v => (
              <button key={v} onClick={() => setVisao(v)}
                className={cn('px-3 py-1.5 text-sm transition-colors',
                  visao === v ? 'bg-emerald-600 text-white' : 'text-zinc-400 hover:text-zinc-200')}>
                {v === 'analitica' ? 'Analítica' : 'Consolidada'}
              </button>
            ))}
          </div>
          {resumo && (
            <span className="text-xs text-zinc-500 tabular-nums"
              title="A competência vai do dia do salário até a véspera do próximo">
              {new Date(resumo.periodo.ini + 'T00:00:00').toLocaleDateString('pt-BR')}
              {' a '}
              {new Date(resumo.periodo.fim + 'T00:00:00').toLocaleDateString('pt-BR')}
            </span>
          )}
          <div className="flex items-center gap-1.5 text-sm text-zinc-500"
            title="Se o dia cair em fim de semana ou feriado, o mês começa antes — no último dia útil, que é quando o banco credita">
            <span>Recebo o salário no dia</span>
            {diaSalarioEdit ? (
              <input autoFocus type="number" min={1} max={31}
                className="bg-zinc-800 border border-zinc-600 rounded px-1.5 py-0.5 text-sm text-zinc-200 w-14 outline-none focus:border-emerald-500"
                value={diaSalarioVal} onChange={e => setDiaSalarioVal(e.target.value)}
                onBlur={saveDiaSalario}
                onKeyDown={e => { if (e.key === 'Enter') saveDiaSalario() }}
              />
            ) : (
              <button onClick={() => setDiaSalarioEdit(true)} className="text-zinc-300 font-medium hover:text-emerald-400 transition-colors">
                {diaSalarioVal}
              </button>
            )}
          </div>
          <button onClick={load}
            className="flex items-center gap-2 px-3 py-1.5 rounded-md text-sm text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors">
            <RefreshIcon /> Atualizar
          </button>
          <button onClick={resetarMes}
            className="px-3 py-1.5 rounded-md text-sm text-zinc-500 hover:text-red-400 hover:bg-red-950/20 transition-colors">
            Resetar mês
          </button>
        </div>
      </div>

      {/* Resumo cards */}
      {resumo && (
        <div className="px-6 pb-4 grid grid-cols-6 gap-3 flex-none">
          <Card label="Renda do mês"  value={resumo.renda}        color="emerald" />
          <Card label="Total do mês"  value={resumo.total}        color="zinc" />
          <Card label="Pago"          value={resumo.pago}         color="emerald" />
          <Card label="Agendado"      value={resumo.agendado}     color="blue" />
          <Card label="A realizar"    value={resumo.aRealizar}    color="amber" />
          <Card label="Recebido − pago" value={resumo.saldoMes}   color={resumo.saldoMes >= 0 ? 'emerald' : 'amber'} />
        </div>
      )}

      {/* Calculadora de resgate */}
      {resumo && (
        <div className="px-6 pb-4 flex-none">
          <div className="rounded-lg border border-zinc-700/60 bg-zinc-900/60 p-4">
            <p className="text-xs text-zinc-500 uppercase tracking-wider mb-3 font-medium">Calculadora de Resgate</p>
            <div className="grid grid-cols-5 gap-4 text-sm">
              {[
                // "a vencer" é agendado + o que a projeção diz que ainda falta.
                // O que já foi pago não entra: esse dinheiro já saiu do saldo.
                ['A vencer',     formatBRL(resumo.aVencer)],
                ['Reserva',      formatBRL(resumo.reserva)],
                ['Renda a receber', formatBRL(resumo.aReceber)],
              ].map(([lbl, val]) => (
                <div key={lbl}>
                  <p className="text-zinc-500 text-xs mb-0.5">{lbl}</p>
                  <p className="text-zinc-200 font-medium">{val}</p>
                </div>
              ))}
              <div>
                <p className="text-zinc-500 text-xs mb-0.5">
                  Saldo conta
                  {resumo.saldoData ? (() => {
                    // A resposta da calculadora depende do saldo, e o saldo só é
                    // atualizado ao importar. Saldo velho dá resposta velha sem
                    // avisar — então a defasagem fica à vista.
                    const dias = Math.floor(
                      (Date.now() - new Date(resumo.saldoData + 'T00:00:00').getTime()) / 86400000)
                    const velho = dias > 3
                    return (
                      <span className={cn('ml-1', velho ? 'text-amber-500/80' : 'text-zinc-600')}
                        title={velho
                          ? `Saldo de ${dias} dias atrás — importe o extrato novo para a conta ficar certa`
                          : 'Lido do extrato na importação, não digitado'}>
                        ({new Date(resumo.saldoData + 'T00:00:00').toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })}
                        {velho ? ` · ${dias}d` : ''})
                      </span>
                    )
                  })() : (
                    <span className="ml-1 text-amber-500/80"
                      title="Nenhum saldo lido de extrato — importe um .xls do Itaú para a calculadora usar o saldo real">
                      (digitado)
                    </span>
                  )}
                </p>
                {saldoEdit ? (
                  <input autoFocus
                    className="bg-zinc-800 border border-zinc-600 rounded px-2 py-0.5 text-sm text-zinc-200 w-28 outline-none focus:border-emerald-500"
                    value={saldoVal} onChange={e => setSaldoVal(e.target.value)}
                    onBlur={saveSaldo}
                    onKeyDown={e => { if (e.key === 'Enter') saveSaldo() }}
                  />
                ) : (
                  <button onClick={() => setSaldoEdit(true)}
                    className={cn('font-medium hover:text-emerald-400 transition-colors',
                      resumo.saldo < 0 ? 'text-red-400' : 'text-zinc-200')}>
                    {formatBRL(resumo.saldo)}
                  </button>
                )}
              </div>
              <div>
                <p className="text-zinc-500 text-xs mb-0.5"
                  title={resumo.resgateJaFeito > 0
                    ? `Já resgatou ${formatBRL(resumo.resgateJaFeito)} neste mês — esse dinheiro já está dentro do saldo acima, por isso não abate daqui`
                    : undefined}>
                  A resgatar
                </p>
                <div className="flex items-baseline gap-2">
                  <p className={cn('text-lg font-bold', resumo.faltaResgatar > 0 ? 'text-amber-400' : 'text-emerald-400')}>
                    {formatBRL(resumo.faltaResgatar)}
                  </p>
                  {/* o "quanto" fica aqui; o "quando" é outra pergunta, e tem
                      tela própria — ver api/resgates.py */}
                  {onPlanejarResgates && (
                    <button onClick={onPlanejarResgates}
                      className="text-xs text-zinc-500 hover:text-emerald-400 underline
                        underline-offset-2 decoration-zinc-700 hover:decoration-emerald-500
                        transition-colors whitespace-nowrap"
                      title="Ver a linha do tempo do mês e em que datas programar cada resgate">
                      Planejar Resgates
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Aviso do que vence sem estar agendado, e o filtro de pendências.
          O aviso vem antes da tabela porque é a única coisa da tela que tem
          prazo: o resto pode esperar, isto não. */}
      {!loading && (pendentes.length > 0 || urgentes.length > 0 || atrasados.length > 0) && (
        <div className="px-6 pb-2 flex items-center gap-3 flex-wrap">
          {atrasados.length > 0 && (
            <span className="inline-flex items-center gap-2 px-2.5 py-1 rounded-md text-xs
              text-red-300 bg-red-950/50 border border-red-800/60">
              <span aria-hidden className="font-bold">!</span>
              {atrasados.length === 1
                ? '1 lançamento passou do dia e não aconteceu'
                : `${atrasados.length} lançamentos passaram do dia e não aconteceram`}
            </span>
          )}
          {urgentes.length > 0 && (
            <span className="inline-flex items-center gap-2 px-2.5 py-1 rounded-md text-xs
              text-orange-300 bg-orange-950/40 border border-orange-800/50">
              <span aria-hidden className="font-bold">!</span>
              {urgentes.length === 1
                ? '1 despesa vence em breve e não está agendada'
                : `${urgentes.length} despesas vencem em breve e não estão agendadas`}
            </span>
          )}
          <button
            onClick={() => { setSoPendencias(p => !p); if (visao !== 'analitica') setVisao('analitica') }}
            aria-pressed={soPendencias}
            title="Tudo que ainda não está nem agendado, com data de vencimento ou sem"
            className={cn('px-2.5 py-1 rounded-md text-xs border transition-colors',
              soPendencias
                ? 'bg-amber-600/15 border-amber-600/60 text-amber-300'
                : 'border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:text-zinc-200')}>
            {soPendencias ? 'Mostrando pendências' : 'Só pendências'}
            <span className="ml-1.5 tabular-nums opacity-70">{pendentes.length}</span>
          </button>
          {soPendencias && (
            <span className="text-xs text-zinc-600">
              despesas ainda não agendadas — a visão consolidada não filtra
            </span>
          )}
        </div>
      )}

      {/* Tabela */}
      <div className="flex-1 overflow-auto px-6 pb-6">
        {loading ? (
          <div className="flex items-center justify-center h-40 text-zinc-500">Carregando...</div>
        ) : visao === 'consolidada' ? (
          <BlocoConsolidado dados={consolidado} />
        ) : (
          <div className="space-y-3">
            {/* Entradas vêm antes das saídas: é a ordem em que o mês acontece.
                Renda e movimentação ficam separadas porque resgate e estorno
                chegam na conta sem serem renda nova (doc 14). */}
            {renda.length > 0 && (
              <BlocoReceitas titulo="Receitas" itens={renda}
                onLimparProjecao={id => limparProjecao('receita', id)} />
            )}
            {movimentacao.length > 0 && (
              <BlocoReceitas titulo="Movimentação — não é renda" itens={movimentacao}
                onLimparProjecao={id => limparProjecao('receita', id)} esmaecido />
            )}

            {Object.entries(byCategory).map(([cat, items]) => (
              <div key={cat}>
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider">{cat}</span>
                  <div className="flex-1 h-px bg-zinc-800" />
                  <TotaisDoGrupo
                    agendado={somaPorStatus(items, 'agendado')}
                    previsto={somaPorStatus(items, 'nao_encontrado')}
                    pago={somaPorStatus(items, 'pago')}
                    total={items.reduce((s, l) => s + valorDaLinha(l), 0)} />
                </div>
                <div className="rounded-lg overflow-hidden border border-zinc-800/60">
                  <table className="w-full text-sm">
                    <tbody>
                      {items.map((l, i) => (
                        <tr key={l.id} className={cn('transition-colors', ROW_BG[l.status], i > 0 && 'border-t border-zinc-800/40')}>
                          <td className="px-4 py-2.5 text-zinc-300 font-medium">
                            {l.item_nome}
                            {(() => {
                              const n = urgencia(l)
                              return n && <SeloVencimento nivel={n} dias={l.dias_para_vencer as number} />
                            })()}
                            {l.recorrencia === 'esporadica' && (
                              <span className="ml-2 text-[10px] uppercase tracking-wide text-amber-500/70"
                                title={l.descricao_transacao ?? 'Despesa esporádica'}>
                                esporádica
                              </span>
                            )}
                          </td>
                          <td className="px-4 py-2.5 text-right">
                            {editId === l.id ? (
                              <input autoFocus
                                className="bg-zinc-800 border border-zinc-600 rounded px-2 py-0.5 text-sm text-zinc-200 w-28 text-right outline-none focus:border-emerald-500"
                                value={editVal} onChange={e => setEditVal(e.target.value)}
                                onBlur={() => saveValor(l)}
                                onKeyDown={e => { if (e.key === 'Enter') saveValor(l); if (e.key === 'Escape') setEditId(null) }}
                              />
                            ) : l.recorrencia === 'esporadica' ? (
                              <span className="text-zinc-200 font-medium tabular-nums" title="Valor vem do extrato">
                                {formatBRL(l.valor_real ?? l.valor_esperado)}
                              </span>
                            ) : (
                              <button onClick={() => { setEditId(l.id); setEditVal(String(l.valor_real ?? l.valor_esperado)) }}
                                className="text-zinc-200 hover:text-emerald-400 transition-colors font-medium tabular-nums">
                                {formatBRL(l.valor_real ?? l.valor_esperado)}
                              </button>
                            )}
                          </td>
                          <td className="px-4 py-2.5">
                            <CelulaData realizada={l.data_pagamento} prevista={l.data_prevista}
                              atrasado={urgencia(l) === 'atrasado'} />
                          </td>
                          <td className="px-4 py-2.5">
                            <span className={cn('inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border', STATUS_STYLE[l.status])}>
                              {STATUS_LABEL[l.status]}
                            </span>
                            {l.status === 'nao_encontrado' && (
                              <SeloPrevisto manual={l.projecao_manual}
                                onLimpar={() => limparProjecao('despesa', l.item_id)} />
                            )}
                          </td>
                          <td className="px-4 py-2.5 text-right">
                            {l.linha_digitavel && (
                              <button onClick={() => copiarBoleto(l)}
                                className={cn('text-xs px-2 py-0.5 rounded border transition-colors whitespace-nowrap',
                                  boletoCopiado === l.id
                                    ? 'border-emerald-700 text-emerald-400'
                                    : 'border-zinc-700 text-zinc-400 hover:border-zinc-500')}>
                                {boletoCopiado === l.id ? 'Copiado!' : (l.tipo_codigo === 'pix' ? 'Copiar Pix' : 'Copiar boleto')}
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

/** Detalhe de uma linha consolidada: os gastos que a formaram e os estornos
 *  que a abateram, em negativo. A soma das linhas é o líquido — é o que
 *  permite conferir de onde veio o número sem sair da tela. */
function LinhasDetalhe({ linhas }: { linhas: LinhaConsolidada[] }) {
  return (
    <>
      {linhas.map((l, j) => (
        <tr key={j} className="bg-zinc-900/60 border-t border-zinc-800/30 text-xs">
          <td className="pl-12 pr-4 py-1.5 text-zinc-500">
            {l.data ? new Date(l.data + 'T00:00:00').toLocaleDateString('pt-BR') + '  ·  ' : ''}
            {l.descricao}
            {l.tipo === 'estorno' && (
              <span className="ml-2 px-1.5 py-0.5 rounded text-[10px] text-amber-400 bg-amber-950/40">
                estorno
              </span>
            )}
          </td>
          <td colSpan={2}></td>
          <td className={cn('px-4 py-1.5 text-right tabular-nums',
            l.valor < 0 ? 'text-amber-400' : 'text-zinc-400')}>
            {formatBRL(l.valor)}
          </td>
        </tr>
      ))}
    </>
  )
}


function BlocoConsolidado({ dados }: { dados: Consolidado | null }) {
  const [ordem, setOrdem] = useState<OrdemCons>('valor')
  const [aberta, setAberta] = useState<number | null>(null)

  if (!dados) return <div className="text-zinc-500 text-sm">Sem dados.</div>
  const { despesas, anulados, receitas, totais } = dados

  // Categoria é o agrupamento fixo; a ordenação escolhida vale DENTRO de cada
  // uma. Ordenar globalmente por valor desfaria o agrupamento, que é o que dá
  // sentido à leitura ("quanto foi Saúde este mês").
  const porCategoria = despesas.reduce<Record<string, DespesaConsolidada[]>>((acc, d) => {
    const cat = d.categoria_nome || 'Outros'
    ;(acc[cat] ??= []).push(d)
    return acc
  }, {})

  const ordenar = (itens: DespesaConsolidada[]) => [...itens].sort((a, b) =>
    ordem === 'valor'
      ? (b.liquido - a.liquido) || a.item_nome.localeCompare(b.item_nome, 'pt-BR')
      : a.item_nome.localeCompare(b.item_nome, 'pt-BR'))

  // categorias com maior gasto primeiro — a ordem alfabética delas raramente
  // é o que se quer olhar antes
  // Na consolidada o total continua sendo o LÍQUIDO (estorno já abatido), que
  // é o número que a visão existe para dar. As duas parcelas novas saem das
  // linhas de gasto por status — estorno não tem status e fica de fora delas,
  // aparecendo só no líquido.
  const somaCons = (itens: DespesaConsolidada[], st: string) =>
    itens.reduce((s, d) => s + d.linhas
      .filter(l => l.tipo === 'gasto' && l.status === st)
      .reduce((a, l) => a + l.valor, 0), 0)

  const categorias = Object.entries(porCategoria)
    .map(([cat, itens]) => ({
      cat, itens: ordenar(itens),
      total: itens.reduce((s, d) => s + d.liquido, 0),
      agendado: somaCons(itens, 'agendado'),
      previsto: somaCons(itens, 'nao_encontrado'),
      pago: somaCons(itens, 'pago'),
    }))
    .sort((a, b) => b.total - a.total)

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-4 gap-3">
        <Card label="Renda"        value={totais.renda}        color="emerald" />
        <Card label="Despesas"     value={totais.despesas}     color="zinc" />
        <Card label="Estornado"    value={totais.estornado}    color="amber" />
        <Card label="Movimentação" value={totais.movimentacao} color="blue" />
      </div>

      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider">Despesas por categoria</span>
        <label className="flex items-center gap-2 text-sm text-zinc-400">
          Dentro da categoria
          <select value={ordem} onChange={e => setOrdem(e.target.value as OrdemCons)}
            className="bg-zinc-800 border border-zinc-700 rounded-md px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500">
            {Object.entries(ORDEM_CONS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
      </div>

      {categorias.map(({ cat, itens, total, agendado, previsto, pago }) => (
        <div key={cat}>
          <div className="flex items-center gap-2 mb-1.5">
            <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider">{cat}</span>
            <div className="flex-1 h-px bg-zinc-800" />
            <TotaisDoGrupo agendado={agendado} previsto={previsto} pago={pago} total={total} />
          </div>
          <div className="rounded-lg overflow-hidden border border-zinc-800/60">
            <table className="w-full text-sm">
              <tbody>
                {itens.map((d, i) => {
                  const consolidou = d.linhas.length > 1
                  const aberto = aberta === d.item_id
                  return (
                    <Fragment key={d.item_id}>
                      <tr className={cn('hover:bg-zinc-800/40', i > 0 && 'border-t border-zinc-800/40')}>
                        <td className="px-4 py-2.5 text-zinc-300 font-medium">
                          {consolidou ? (
                            <button onClick={() => setAberta(aberto ? null : d.item_id)}
                              className="text-zinc-300 hover:text-emerald-400 transition-colors"
                              title={`${d.linhas.length} lançamentos somados — clique para ver`}>
                              <span className="inline-block w-4 text-zinc-500 tabular-nums">{aberto ? '−' : '+'}</span>
                              {d.item_nome}
                              <span className="ml-2 text-[10px] text-sky-500/70">{d.linhas.length}x</span>
                            </button>
                          ) : (
                            <span><span className="inline-block w-4" />{d.item_nome}</span>
                          )}
                        </td>
                        <td className="px-4 py-2.5 text-right tabular-nums text-zinc-500 w-32">{formatBRL(d.bruto)}</td>
                        <td className={cn('px-4 py-2.5 text-right tabular-nums w-32',
                          d.estornado > 0 ? 'text-amber-400' : 'text-zinc-700')}>
                          {d.estornado > 0 ? `− ${formatBRL(d.estornado)}` : '—'}
                        </td>
                        <td className={cn('px-4 py-2.5 text-right tabular-nums font-medium w-32',
                          d.liquido < 0 ? 'text-emerald-400' : 'text-zinc-200')}
                          title={d.liquido < 0 ? 'Voltou mais do que saiu neste mês' : undefined}>
                          {formatBRL(d.liquido)}
                        </td>
                      </tr>
                      {aberto && <LinhasDetalhe linhas={d.linhas} />}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}

      {anulados.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-1.5">
            <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider">
              Anulados no mês
            </span>
            <div className="flex-1 h-px bg-zinc-800" />
            <span className="text-xs text-zinc-600 tabular-nums">{formatBRL(0)}</span>
          </div>
          <p className="text-xs text-zinc-500 mb-1.5">
            Gasto e devolução de mesmo valor — custaram zero, então ficam fora do total.
            Abra para ver as duas pontas.
          </p>
          <div className="rounded-lg overflow-hidden border border-zinc-800/60 opacity-70">
            <table className="w-full text-sm">
              <tbody>
                {anulados.map((d, i) => {
                  const aberto = aberta === d.item_id
                  return (
                    <Fragment key={d.item_id}>
                      <tr className={cn('hover:bg-zinc-800/40', i > 0 && 'border-t border-zinc-800/40')}>
                        <td className="px-4 py-2.5 text-zinc-400 font-medium">
                          <button onClick={() => setAberta(aberto ? null : d.item_id)}
                            className="hover:text-emerald-400 transition-colors"
                            title={`${d.linhas.length} lançamentos que se anulam — clique para ver`}>
                            <span className="inline-block w-4 text-zinc-500">{aberto ? '−' : '+'}</span>
                            {d.item_nome}
                            <span className="ml-2 text-[10px] text-zinc-600">{d.categoria_nome || 'Outros'}</span>
                          </button>
                        </td>
                        <td className="px-4 py-2.5 text-right tabular-nums text-zinc-500 w-32">{formatBRL(d.bruto)}</td>
                        <td className="px-4 py-2.5 text-right tabular-nums text-amber-400/70 w-32">
                          − {formatBRL(d.estornado)}
                        </td>
                        <td className="px-4 py-2.5 text-right tabular-nums text-zinc-500 w-32">{formatBRL(0)}</td>
                      </tr>
                      {aberto && <LinhasDetalhe linhas={d.linhas} />}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {receitas.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-1.5">
            <span className="text-xs font-medium text-emerald-500/80 uppercase tracking-wider">Entradas</span>
            <div className="flex-1 h-px bg-zinc-800" />
          </div>
          <div className="rounded-lg overflow-hidden border border-zinc-800/60">
            <table className="w-full text-sm">
              <tbody>
                {receitas.map((r, i) => (
                  <tr key={r.item_id} className={cn('hover:bg-zinc-800/40', i > 0 && 'border-t border-zinc-800/40',
                    !r.renda && 'opacity-70')}>
                    <td className="px-4 py-2.5 text-zinc-300 font-medium">
                      {r.item_nome}
                      {r.ocorrencias > 1 && <span className="ml-2 text-[10px] text-sky-500/70">{r.ocorrencias}x</span>}
                    </td>
                    <td className="px-4 py-2.5 text-zinc-500 text-xs">
                      {TIPO_LABEL[r.tipo] ?? r.tipo}{!r.renda && ' · não é renda'}
                    </td>
                    <td className={cn('px-4 py-2.5 text-right tabular-nums font-medium',
                      r.renda ? 'text-emerald-400' : 'text-zinc-400')}>
                      {formatBRL(r.total)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}


function BlocoReceitas({ titulo, itens, esmaecido, onLimparProjecao }: {
  titulo: string; itens: Receita[]; esmaecido?: boolean
  onLimparProjecao: (itemId: number) => void
}) {
  // Calculado das próprias linhas, e não recebido pronto: o total que vinha do
  // resumo era `renda`, que é só o **recebido** — o cabeçalho dizia "total"
  // mostrando 47 mil enquanto as linhas somavam 65 mil. Somando aqui, as cinco
  // parcelas fecham entre si e ainda reproduzem `aReceber` e `renda` do resumo.
  const valor = (r: Receita) => r.valor_real ?? r.valor_esperado
  const soma = (st: Receita['status']) =>
    itens.filter(r => r.status === st).reduce((s, r) => s + valor(r), 0)
  return (
    <div>
      <div className="flex items-center gap-2 mb-1.5">
        <span className={cn('text-xs font-medium uppercase tracking-wider',
          esmaecido ? 'text-zinc-600' : 'text-emerald-500/80')}>{titulo}</span>
        <div className="flex-1 h-px bg-zinc-800" />
        <TotaisDoGrupo entrada
          agendado={soma('previsto')}
          previsto={soma('nao_encontrado')}
          pago={soma('recebido')}
          total={itens.reduce((s, r) => s + valor(r), 0)} />
      </div>
      <div className="rounded-lg overflow-hidden border border-zinc-800/60">
        <table className="w-full text-sm">
          <tbody>
            {itens.map((r, i) => (
              <tr key={r.id} className={cn('transition-colors hover:bg-zinc-800/40',
                i > 0 && 'border-t border-zinc-800/40', esmaecido && 'opacity-70')}>
                <td className="px-4 py-2.5 text-zinc-300 font-medium">
                  {r.item_nome}
                  {(() => {
                    const n = urgencia(r)
                    return n && <SeloVencimento nivel={n} dias={r.dias_para_vencer as number} />
                  })()}
                  {r.recorrencia === 'esporadica' && (
                    <span className="ml-2 text-[10px] uppercase tracking-wide text-amber-500/70"
                      title={r.descricao_transacao ?? 'Receita esporádica'}>
                      esporádica
                    </span>
                  )}
                  {r.objetivo && <span className="ml-2 text-xs text-zinc-500">· {r.objetivo}</span>}
                </td>
                <td className="px-4 py-2.5 text-zinc-500 text-xs">{TIPO_LABEL[r.tipo] ?? r.tipo}</td>
                <td className={cn('px-4 py-2.5 text-right font-medium tabular-nums',
                  esmaecido ? 'text-zinc-400' : 'text-emerald-400')}>
                  {formatBRL(r.valor_real ?? r.valor_esperado)}
                </td>
                <td className="px-4 py-2.5 w-24">
                  <CelulaData realizada={r.data_recebimento} prevista={r.data_prevista}
                    atrasado={urgencia(r) === 'atrasado'} />
                </td>
                <td className="px-4 py-2.5 w-28">
                  <span className={cn('text-xs px-2 py-0.5 rounded border',
                    r.status === 'recebido'
                      ? 'bg-emerald-950/50 text-emerald-400 border-emerald-800/50'
                      : 'bg-zinc-800/30 text-zinc-500 border-zinc-700/30')}>
                    {STATUS_RECEITA_LABEL[r.status] ?? r.status}
                  </span>
                  {r.status === 'nao_encontrado' && (
                    <SeloPrevisto manual={r.projecao_manual}
                      onLimpar={() => onLimparProjecao(r.item_id)} />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}


function Card({ label, value, color }: { label: string; value: number; color: string }) {
  const colors: Record<string, string> = { zinc: 'text-zinc-200', emerald: 'text-emerald-400', blue: 'text-blue-400', amber: 'text-amber-400' }
  return (
    <div className="rounded-lg border border-zinc-800/60 bg-zinc-900/60 px-4 py-3">
      <p className="text-xs text-zinc-500 mb-1">{label}</p>
      <p className={cn('text-lg font-bold tabular-nums', colors[color])}>{formatBRL(value)}</p>
    </div>
  )
}

function ChevronLeft() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
}
function ChevronRight() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
}
function RefreshIcon() {
  return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="23 4 23 10 17 10" /><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" /></svg>
}
