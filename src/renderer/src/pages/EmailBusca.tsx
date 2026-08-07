import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { cn, currentMesRef, mesRefLabel } from '../lib/utils'
import { DespesaPicker } from '../components/DespesaPicker'

interface Boleto {
  id: string
  assunto: string
  remetente: string
  data: string | null
  valor_encontrado: string | null
  linha_digitavel: string | null
  tipo_codigo: 'boleto' | 'pix' | null
  despesa_sugerida_id: number | null
  despesa_sugerida_nome: string | null
  origem_sugestao: 'regra' | 'ia' | 'palavra_chave' | null
}

interface Despesa { id: number; nome: string }

interface Pendente {
  boletoId: string
  despesaId: number
  despesaNome: string
  mesRef: string
  linhaDigitavel: string | null
  tipoCodigo: string | null
  valor: string | null
  remetente: string
  origem: 'manual' | 'regra'
}

interface BuscaStatus {
  rodando: boolean
  cancelado: boolean
  concluido: boolean
  erro: string | null
  avisos: string[]
  boletos: Boleto[]
  total_emails: number
  despesas_pesquisadas: number
  dia_atual: string | null
  total_dias: number
  dias_processados: number
}

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

function addMonths(dateStr: string, n: number): string {
  const d = new Date(dateStr + 'T12:00:00')
  d.setMonth(d.getMonth() + n)
  return isoDate(d)
}

const POLL_MS = 1200
const ULTIMA_BUSCA_KEY = 'financecontrol:ultimaBuscaEmailAssociada'

export function EmailBusca({ active }: { active: boolean }) {
  const [status, setStatus] = useState<{ configurado: boolean; conectado: string | null } | null>(null)
  const [dataIni, setDataIni] = useState(() => {
    const d = new Date()
    d.setDate(d.getDate() - 60)
    return isoDate(d)
  })
  const [dataFim, setDataFim] = useState(() => isoDate(new Date()))
  const [buscando, setBuscando] = useState(false)
  const [cancelando, setCancelando] = useState(false)
  const [buscaIniciada, setBuscaIniciada] = useState(false)
  const [boletos, setBoletos] = useState<Boleto[]>([])
  const [pesquisadas, setPesquisadas] = useState(0)
  const [totalEmails, setTotalEmails] = useState(0)
  const [totalDias, setTotalDias] = useState(0)
  const [diasProcessados, setDiasProcessados] = useState(0)
  const [diaAtual, setDiaAtual] = useState<string | null>(null)
  const [buscaCancelada, setBuscaCancelada] = useState(false)
  const [avisos, setAvisos] = useState<string[]>([])
  const [erro, setErro] = useState('')
  const [copiado, setCopiado] = useState<string | null>(null)
  const [despesas, setDespesas] = useState<Despesa[]>([])
  const pollRef = useRef<number | null>(null)

  const [associando, setAssociando] = useState<string | null>(null)
  const [despesaEscolhida, setDespesaEscolhida] = useState('')
  const [novaDespesaNome, setNovaDespesaNome] = useState('')
  const [mesEscolhido, setMesEscolhido] = useState(currentMesRef())

  // associações represadas em tela — nada é gravado até "Confirmar tudo"
  const [pendentes, setPendentes] = useState<Pendente[]>([])
  const [confirmados, setConfirmados] = useState<Set<string>>(new Set())
  const confirmadosRef = useRef<Set<string>>(new Set())
  const [confirmando, setConfirmando] = useState(false)

  // "Repetir mês anterior": enquanto ativo, boletos com sugestão vinda de
  // regra já conhecida entram sozinhos em pendentes (ver aplicarStatus)
  const [repetindo, setRepetindo] = useState(false)
  const repetindoRef = useRef(false)
  const mesAlvoRepeticaoRef = useRef(currentMesRef())
  const [ultimaBusca, setUltimaBusca] = useState<{ ini: string; fim: string } | null>(null)

  const [codigoDispositivo, setCodigoDispositivo] = useState<{ verification_uri: string; user_code: string } | null>(null)
  const [aguardandoLogin, setAguardandoLogin] = useState(false)
  const [erroConexao, setErroConexao] = useState('')

  function carregarStatus() {
    api.email.status().then(setStatus)
  }

  function pararPolling() {
    if (pollRef.current) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  function marcarConfirmados(ids: string[]) {
    setConfirmados(prev => {
      const next = new Set(prev)
      ids.forEach(id => next.add(id))
      confirmadosRef.current = next
      return next
    })
  }

  function aplicarStatus(s: BuscaStatus) {
    setBoletos(s.boletos)
    setTotalEmails(s.total_emails)
    setPesquisadas(s.despesas_pesquisadas)
    setTotalDias(s.total_dias)
    setDiasProcessados(s.dias_processados)
    setDiaAtual(s.dia_atual)
    setAvisos(s.avisos || [])
    setBuscaCancelada(s.cancelado)
    if (s.erro) setErro(s.erro)

    if (repetindoRef.current) {
      setPendentes(prev => {
        const jaTem = new Set(prev.map(p => p.boletoId))
        const novos = s.boletos.filter(b =>
          b.origem_sugestao === 'regra' && b.despesa_sugerida_id != null &&
          !jaTem.has(b.id) && !confirmadosRef.current.has(b.id)
        )
        if (novos.length === 0) return prev
        return [...prev, ...novos.map(b => ({
          boletoId: b.id,
          despesaId: b.despesa_sugerida_id as number,
          despesaNome: b.despesa_sugerida_nome || '',
          mesRef: mesAlvoRepeticaoRef.current,
          linhaDigitavel: b.linha_digitavel,
          tipoCodigo: b.tipo_codigo,
          valor: b.valor_encontrado,
          remetente: b.remetente,
          origem: 'regra' as const,
        }))]
      })
    }

    if (!s.rodando) {
      setBuscando(false)
      setCancelando(false)
      pararPolling()
    }
  }

  function iniciarPolling() {
    pararPolling()
    pollRef.current = window.setInterval(() => {
      api.email.buscarStatus().then(aplicarStatus)
    }, POLL_MS)
  }

  // a tela fica sempre montada (ver App.tsx) — sem isso, uma despesa criada
  // no Catálogo enquanto essa aba já estava aberta nunca apareceria aqui
  useEffect(() => {
    if (active) api.catalogo.list().then(setDespesas)
  }, [active])

  useEffect(() => {
    carregarStatus()
    const salvo = localStorage.getItem(ULTIMA_BUSCA_KEY)
    if (salvo) {
      try { setUltimaBusca(JSON.parse(salvo)) } catch { /* formato antigo/corrompido — ignora */ }
    }
    // se a página remontar (ou o usuário navegar de volta) com uma busca já
    // em andamento no servidor, retoma o acompanhamento em vez de perder o progresso
    api.email.buscarStatus().then((s: BuscaStatus) => {
      if (s.rodando || s.concluido) {
        setBuscaIniciada(true)
        aplicarStatus(s)
        if (s.rodando) {
          setBuscando(true)
          iniciarPolling()
        }
      }
    })
    return pararPolling
  }, [])

  async function conectar() {
    setErroConexao('')
    setCodigoDispositivo(null)
    try {
      const res = await api.email.conectarIniciar()
      if (!res.ok) {
        setErroConexao(res.msg || 'Falha ao iniciar conexão')
        return
      }
      setCodigoDispositivo({ verification_uri: res.verification_uri, user_code: res.user_code })
      setAguardandoLogin(true)
      const fin = await api.email.conectarFinalizar()
      setAguardandoLogin(false)
      if (fin.ok) {
        setCodigoDispositivo(null)
        carregarStatus()
      } else {
        setErroConexao(fin.msg || 'Login não concluído')
      }
    } catch (e) {
      setAguardandoLogin(false)
      setErroConexao(String(e))
    }
  }

  async function buscar(iniParam?: string, fimParam?: string, repetir = false) {
    const ini = iniParam ?? dataIni
    const fim = fimParam ?? dataFim
    setErro('')
    setAvisos([])
    setBoletos([])
    setTotalEmails(0)
    setDiasProcessados(0)
    setBuscaCancelada(false)
    setPendentes([])
    setConfirmados(new Set())
    confirmadosRef.current = new Set()
    setRepetindo(repetir)
    repetindoRef.current = repetir
    setBuscando(true)
    setBuscaIniciada(true)
    try {
      const res = await api.email.buscarIniciar(ini, fim)
      if (!res.ok) {
        setErro(res.msg || 'Erro ao iniciar busca')
        setBuscando(false)
        return
      }
      setTotalDias(res.total_dias)
      iniciarPolling()
    } catch (e) {
      setErro(String(e))
      setBuscando(false)
    }
  }

  function repetirMesAnterior() {
    if (!ultimaBusca) return
    const novaIni = addMonths(ultimaBusca.ini, 1)
    const novaFim = addMonths(ultimaBusca.fim, 1)
    setDataIni(novaIni)
    setDataFim(novaFim)
    mesAlvoRepeticaoRef.current = novaFim.slice(0, 7)
    buscar(novaIni, novaFim, true)
  }

  async function cancelar() {
    setCancelando(true)
    try {
      await api.email.buscarCancelar()
    } catch (e) {
      setErro(String(e))
      setCancelando(false)
    }
  }

  async function copiar(texto: string) {
    await navigator.clipboard.writeText(texto.replace(/\s+/g, ''))
    setCopiado(texto)
    setTimeout(() => setCopiado(null), 1500)
  }

  function abrirAssociacao(b: Boleto) {
    setAssociando(associando === b.id ? null : b.id)
    setDespesaEscolhida(b.despesa_sugerida_id ? String(b.despesa_sugerida_id) : '')
    setNovaDespesaNome('')
    setMesEscolhido(currentMesRef())
  }

  function adicionarPendente(b: Boleto, despesaId: number, mesRef: string, origem: 'manual' | 'regra', despesaNomeOverride?: string) {
    const despesa = despesas.find(d => d.id === despesaId)
    setPendentes(prev => {
      if (prev.some(p => p.boletoId === b.id)) return prev
      return [...prev, {
        boletoId: b.id,
        despesaId,
        despesaNome: despesaNomeOverride || despesa?.nome || b.despesa_sugerida_nome || '',
        mesRef,
        linhaDigitavel: b.linha_digitavel,
        tipoCodigo: b.tipo_codigo,
        valor: b.valor_encontrado,
        remetente: b.remetente,
        origem,
      }]
    })
  }

  async function aplicarAssociacao(b: Boleto) {
    if (despesaEscolhida === 'nova') {
      const nome = novaDespesaNome.trim()
      if (!nome) return
      const keywords = nome.toLowerCase().split(/\s+/).filter(w => w.length >= 3)
      const valorPadrao = b.valor_encontrado
        ? Number(b.valor_encontrado.replace(/\./g, '').replace(',', '.'))
        : undefined
      const res = await api.catalogo.upsert({
        nome,
        tipo_valor: 'variavel',
        padrao_variabilidade: 'variavel_nao_sazonal',
        valor_padrao: valorPadrao,
        regras_match: JSON.stringify({ palavras_chave: keywords, faixa_valor: null, janela_dias: 5, banco: null })
      })
      setDespesas(ds => [...ds, { id: res.id, nome }])
      adicionarPendente(b, res.id, mesEscolhido, 'manual', nome)
    } else {
      if (!despesaEscolhida) return
      adicionarPendente(b, Number(despesaEscolhida), mesEscolhido, 'manual')
    }
    setAssociando(null)
  }

  function desfazerPendente(boletoId: string) {
    setPendentes(prev => prev.filter(p => p.boletoId !== boletoId))
  }

  function cancelarTudo() {
    setPendentes([])
  }

  async function confirmarTudo() {
    if (pendentes.length === 0) return
    setConfirmando(true)
    try {
      const itens = pendentes.map(p => ({
        despesa_id: p.despesaId,
        mes_ref: p.mesRef,
        linha_digitavel: p.linhaDigitavel,
        tipo_codigo: p.tipoCodigo,
        valor: p.valor,
        remetente: p.remetente,
      }))
      const res = await api.email.associarLote(itens)
      if (!res.ok) {
        setErro(res.msg || 'Erro ao confirmar associações')
        return
      }
      marcarConfirmados(pendentes.map(p => p.boletoId))
      setPendentes([])
      const periodo = { ini: dataIni, fim: dataFim }
      localStorage.setItem(ULTIMA_BUSCA_KEY, JSON.stringify(periodo))
      setUltimaBusca(periodo)
    } catch (e) {
      setErro(String(e))
    } finally {
      setConfirmando(false)
    }
  }

  const pendentePorBoleto = new Map(pendentes.map(p => [p.boletoId, p]))
  const temGrupos = pendentes.length > 0 || confirmados.size > 0
  const associadosOuPendentes = boletos.filter(b => pendentePorBoleto.has(b.id) || confirmados.has(b.id))
  const naoAssociados = boletos.filter(b => !pendentePorBoleto.has(b.id) && !confirmados.has(b.id))

  function renderBoleto(b: Boleto) {
    const pendente = pendentePorBoleto.get(b.id)
    const confirmado = confirmados.has(b.id)
    return (
      <div key={b.id} className="px-4 py-3 text-sm">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-zinc-300 truncate">{b.assunto}</p>
            <p className="text-zinc-600 text-xs truncate">
              {b.remetente} — {b.data ? new Date(b.data).toLocaleDateString('pt-BR') : ''}
              {b.despesa_sugerida_nome && !pendente && !confirmado && (
                <> — sugestão{b.origem_sugestao === 'regra' ? ' (associação anterior)' : b.origem_sugestao === 'ia' ? ' (IA)' : ''}:{' '}
                  <span className="text-zinc-400">{b.despesa_sugerida_nome}</span></>
              )}
            </p>
          </div>
          <div className="flex items-center gap-2 flex-none">
            {b.valor_encontrado && (
              <span className="text-emerald-400 font-medium tabular-nums whitespace-nowrap">R$ {b.valor_encontrado}</span>
            )}
            {confirmado ? (
              <span className="text-xs text-emerald-400 whitespace-nowrap">Associado ✓</span>
            ) : pendente ? (
              <button onClick={() => desfazerPendente(b.id)}
                className="text-xs px-2 py-1 rounded border border-zinc-700 text-zinc-400 hover:border-red-600 hover:text-red-400 transition-colors whitespace-nowrap">
                Desfazer
              </button>
            ) : (
              <button onClick={() => abrirAssociacao(b)}
                className="text-xs px-2 py-1 rounded border border-zinc-700 text-zinc-400 hover:border-emerald-600 hover:text-emerald-400 transition-colors whitespace-nowrap">
                Associar
              </button>
            )}
          </div>
        </div>

        {pendente && (
          <p className="mt-1 text-xs text-zinc-500">
            Pendente: <span className="text-zinc-300">{pendente.despesaNome}</span> em {mesRefLabel(pendente.mesRef)}
            {pendente.origem === 'regra' && <span className="text-zinc-600"> (associação anterior)</span>}
          </p>
        )}

        {b.linha_digitavel ? (
          <div className="mt-2 flex items-center gap-2">
            <span className="text-[10px] uppercase tracking-wide text-zinc-500 flex-none">
              {b.tipo_codigo === 'pix' ? 'Pix' : 'Boleto'}
            </span>
            <code className="text-xs text-zinc-400 bg-zinc-900/60 px-2 py-1 rounded flex-1 truncate">
              {b.linha_digitavel}
            </code>
            <button onClick={() => copiar(b.linha_digitavel!)}
              className={cn('text-xs px-2 py-1 rounded border transition-colors whitespace-nowrap',
                copiado === b.linha_digitavel
                  ? 'border-emerald-700 text-emerald-400'
                  : 'border-zinc-700 text-zinc-400 hover:border-zinc-500')}>
              {copiado === b.linha_digitavel ? 'Copiado!' : (b.tipo_codigo === 'pix' ? 'Copiar Pix' : 'Copiar linha digitável')}
            </button>
          </div>
        ) : (
          <p className="mt-1 text-xs text-zinc-700">Código de pagamento não encontrado — abra o email manualmente.</p>
        )}

        {associando === b.id && !pendente && !confirmado && (
          <div className="mt-3 flex items-center gap-2 flex-wrap bg-zinc-900/60 rounded p-3">
            <DespesaPicker despesas={despesas} value={despesaEscolhida} onChange={setDespesaEscolhida}
              placeholder="Digite pra buscar a despesa..." allowNova
              onSelectNova={q => { setDespesaEscolhida('nova'); setNovaDespesaNome(q) }}
              className="w-56" />
            {despesaEscolhida === 'nova' && (
              <input value={novaDespesaNome} onChange={e => setNovaDespesaNome(e.target.value)}
                placeholder="Nome da nova despesa" autoFocus
                className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500" />
            )}
            <input type="month" value={mesEscolhido} onChange={e => setMesEscolhido(e.target.value)}
              className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500" />
            <button onClick={() => aplicarAssociacao(b)}
              disabled={!despesaEscolhida || (despesaEscolhida === 'nova' && !novaDespesaNome.trim())}
              className="px-3 py-1 rounded bg-emerald-600 text-white text-xs font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
              Adicionar
            </button>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full pt-3">
      <div className="px-6 pb-4 flex-none">
        <h1 className="text-xl font-semibold text-zinc-100">Procurar em Emails</h1>
        <p className="text-sm text-zinc-500 mt-0.5">
          Busca boletos, Pix e faturas (cartão, seguro saúde, etc.) no período escolhido — associe o que encontrar
          a uma despesa e mês. Nada é gravado na hora: as associações ficam pendentes pra revisão e só valem
          depois de "Confirmar tudo". Cada confirmação também vira uma regra: da próxima vez, o mesmo remetente já
          vem pré-preenchido.
        </p>
        <p className="text-xs text-amber-500/80 mt-1">
          A extração de valor/código de pagamento é automática (por IA) — confira sempre contra o email
          original antes de pagar.
        </p>
      </div>

      <div className="px-6 pb-6 flex-1 overflow-auto space-y-4">
        {status && !status.configurado && (
          <div className="rounded-lg border border-amber-800/40 bg-amber-950/20 p-4 text-sm text-amber-300 max-w-xl">
            App do Azure ainda não configurado. Veja o passo a passo em{' '}
            <code className="text-amber-200">.env.example</code> — registre um app gratuito no Azure e cole o
            "ID do aplicativo (cliente)" em <code className="text-amber-200">EMAIL_CLIENT_ID</code> no{' '}
            <code className="text-amber-200">.env</code>, depois reinicie o app.
          </div>
        )}

        {status?.configurado && !status.conectado && !codigoDispositivo && (
          <div className="rounded-lg border border-zinc-700/60 bg-zinc-900/60 p-4 max-w-xl">
            <p className="text-sm text-zinc-400 mb-3">Email ainda não conectado.</p>
            <button onClick={conectar}
              className="px-4 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium hover:bg-emerald-500 transition-colors">
              Conectar com Microsoft
            </button>
            {erroConexao && <p className="text-sm text-red-400 mt-2">{erroConexao}</p>}
          </div>
        )}

        {codigoDispositivo && (
          <div className="rounded-lg border border-emerald-800/40 bg-emerald-950/20 p-5 max-w-xl">
            <p className="text-sm text-zinc-300 mb-3">
              Abra <a href={codigoDispositivo.verification_uri} target="_blank" rel="noreferrer"
                className="text-emerald-400 underline">{codigoDispositivo.verification_uri}</a> e cole o código abaixo:
            </p>
            <div className="flex items-center gap-3">
              <code className="text-2xl font-bold text-emerald-400 tracking-widest bg-zinc-900/60 px-4 py-2 rounded">
                {codigoDispositivo.user_code}
              </code>
              <button onClick={() => navigator.clipboard.writeText(codigoDispositivo.user_code)}
                className="text-xs px-2 py-1 rounded border border-zinc-700 text-zinc-400 hover:border-zinc-500 transition-colors">
                Copiar
              </button>
            </div>
            {aguardandoLogin && (
              <p className="text-xs text-zinc-500 mt-3">
                Aguardando você confirmar no navegador... Se aparecer um aviso de segurança no final, não feche —
                deixe fechar sozinha.
              </p>
            )}
          </div>
        )}

        {status?.conectado && (
          <div className="flex items-end gap-3 flex-wrap">
            <div>
              <label className="text-sm text-zinc-400 block mb-2">De</label>
              <input type="date" value={dataIni} onChange={e => setDataIni(e.target.value)}
                className="bg-zinc-800 border border-zinc-700 rounded-md px-3 py-2 text-sm text-zinc-200 outline-none focus:border-emerald-500" />
            </div>
            <div>
              <label className="text-sm text-zinc-400 block mb-2">Até</label>
              <input type="date" value={dataFim} onChange={e => setDataFim(e.target.value)}
                className="bg-zinc-800 border border-zinc-700 rounded-md px-3 py-2 text-sm text-zinc-200 outline-none focus:border-emerald-500" />
            </div>
            <button onClick={() => buscar()} disabled={buscando}
              className="px-5 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
              {buscando ? 'Buscando...' : 'Buscar'}
            </button>
            {ultimaBusca && (
              <button onClick={repetirMesAnterior} disabled={buscando}
                className="px-4 py-2 rounded-md border border-emerald-700 text-emerald-400 text-sm font-medium disabled:opacity-40 hover:bg-emerald-950/30 transition-colors">
                Repetir mês anterior
              </button>
            )}
            {buscando && (
              <button onClick={cancelar} disabled={cancelando}
                className="px-4 py-2 rounded-md border border-zinc-700 text-zinc-300 text-sm font-medium disabled:opacity-40 hover:border-red-600 hover:text-red-400 transition-colors">
                {cancelando ? 'Parando...' : 'Parar busca'}
              </button>
            )}
            <p className="text-xs text-zinc-600 pb-2">conectado como {status.conectado}</p>
          </div>
        )}

        {buscando && (
          <div className="max-w-xl space-y-2">
            <div className="h-1.5 rounded-full bg-zinc-800 overflow-hidden">
              <div className="h-full bg-emerald-600 transition-all duration-300"
                style={{ width: `${totalDias ? (diasProcessados / totalDias) * 100 : 0}%` }} />
            </div>
            <p className="text-xs text-zinc-500">
              {cancelando
                ? 'Parando após o dia atual...'
                : `Buscando dia ${Math.min(diasProcessados + 1, totalDias)} de ${totalDias}${diaAtual ? ` (${new Date(diaAtual + 'T12:00:00').toLocaleDateString('pt-BR')})` : ''} — ${boletos.length} encontrado(s) até agora`}
            </p>
          </div>
        )}

        {erro && <p className="text-sm text-red-400">{erro}</p>}
        {avisos.map((a, i) => (
          <div key={i} className="rounded-lg border border-amber-800/40 bg-amber-950/20 p-3 text-sm text-amber-300">
            ⚠ {a}
          </div>
        ))}
        {buscaCancelada && !buscando && (
          <p className="text-sm text-zinc-500">
            Busca interrompida a pedido — mostrando o que foi encontrado até o dia {diasProcessados} de {totalDias} do período.
          </p>
        )}

        {buscaIniciada && (
          <div className="space-y-4">
            <p className="text-sm text-zinc-500">
              {totalEmails} emails {buscando ? 'verificados até agora' : 'no período'}, {pesquisadas} despesas no catálogo — {boletos.length} boletos/faturas encontrados.
            </p>

            {pendentes.length > 0 && (
              <div className="sticky top-0 z-10 flex items-center gap-3 bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-3 shadow-lg">
                <p className="text-sm text-zinc-300 flex-1">
                  {pendentes.length} associaç{pendentes.length === 1 ? 'ão pendente' : 'ões pendentes'} — nada foi salvo ainda.
                </p>
                <button onClick={cancelarTudo} disabled={confirmando}
                  className="px-3 py-1.5 rounded-md border border-zinc-700 text-zinc-300 text-sm hover:border-red-600 hover:text-red-400 transition-colors disabled:opacity-40">
                  Cancelar tudo
                </button>
                <button onClick={confirmarTudo} disabled={confirmando}
                  className="px-4 py-1.5 rounded-md bg-emerald-600 text-white text-sm font-medium hover:bg-emerald-500 transition-colors disabled:opacity-40">
                  {confirmando ? 'Confirmando...' : `Confirmar tudo (${pendentes.length})`}
                </button>
              </div>
            )}

            {!buscando && boletos.length === 0 && (
              <p className="text-sm text-zinc-600">Nada encontrado nesse período (de {totalEmails} emails verificados) — tente um intervalo maior ou confira se o email não está em outra pasta.</p>
            )}

            {associadosOuPendentes.length > 0 && (
              <div>
                <p className="text-xs uppercase tracking-wide text-zinc-600 mb-2">Associados / pendentes</p>
                <div className="rounded-lg overflow-hidden border border-zinc-800/60 divide-y divide-zinc-800/40">
                  {associadosOuPendentes.map(renderBoleto)}
                </div>
              </div>
            )}

            {naoAssociados.length > 0 && (
              <div>
                {temGrupos && <p className="text-xs uppercase tracking-wide text-zinc-600 mb-2">Não associados</p>}
                <div className="rounded-lg overflow-hidden border border-zinc-800/60 divide-y divide-zinc-800/40">
                  {naoAssociados.map(renderBoleto)}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
