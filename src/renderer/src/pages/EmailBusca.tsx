import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { cn, currentMesRef, mesRefLabel } from '../lib/utils'
import { DespesaPicker } from '../components/DespesaPicker'

/** Um código de pagamento. Um email pode ter **vários**: o carnê de um
 *  financiamento traz o cronograma inteiro num PDF só, e a versão anterior
 *  desta tela mostrava um código por email — as outras parcelas eram
 *  descartadas sem ninguém ver. */
interface Codigo {
  /** o código sem pontuação — identidade estável entre buscas */
  chave: string
  codigo: string
  tipo_codigo: 'boleto' | 'pix'
  /** do próprio código, quando boleto (não é chute de IA) */
  valor: string | null
  vencimento: string | null
  /** os 4 dígitos verificadores conferem? */
  dv_ok: boolean
  /** competência do **vencimento** da parcela, não da data do email */
  mes_ref_sugerido: string | null
  ja_associado: {
    despesa_id: number
    despesa_nome: string
    mes_ref: string
    valor_esperado: number
    /** null quando não há valor dos dois lados para comparar */
    valor_bate: boolean | null
  } | null
}

interface EmailAchado {
  id: string
  assunto: string
  remetente: string
  data: string | null
  mes_ref_sugerido: string | null
  despesa_sugerida_id: number | null
  despesa_sugerida_nome: string | null
  origem_sugestao: 'regra' | 'ia' | 'palavra_chave' | null
  codigos: Codigo[]
  /** por que não saiu código, quando não saiu — nada silencioso */
  notas: string[]
}

interface Despesa { id: number; nome: string }

interface Conflito {
  mes_ref: string
  despesa_id: number
  codigo_atual: string
  codigo_novo: string
}

interface BuscaStatus {
  rodando: boolean
  cancelado: boolean
  concluido: boolean
  erro: string | null
  avisos: string[]
  emails: EmailAchado[]
  total_codigos: number
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

function dataBR(iso: string | null): string {
  return iso ? new Date(iso.slice(0, 10) + 'T12:00:00').toLocaleDateString('pt-BR') : ''
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
  const [emails, setEmails] = useState<EmailAchado[]>([])
  const [totalCodigos, setTotalCodigos] = useState(0)
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

  // Escolhas da tela. A despesa é por **email** (todas as parcelas de um carnê
  // são da mesma despesa) e o mês é por **código** (cada parcela tem o seu).
  // Guardadas por id, não sincronizadas por efeito: o polling troca a lista a
  // cada 1,2s e um efeito de sincronia apagaria o que você acabou de escolher.
  const [despesaPorEmail, setDespesaPorEmail] = useState<Record<string, string>>({})
  const [novaDespesaPorEmail, setNovaDespesaPorEmail] = useState<Record<string, string>>({})
  const [mesPorCodigo, setMesPorCodigo] = useState<Record<string, string>>({})

  // Gravado nesta sessão — some da fila sem precisar refazer a busca.
  const [gravados, setGravados] = useState<Record<string, { despesaNome: string; mesRef: string }>>({})
  const [conflitos, setConflitos] = useState<Record<string, Conflito>>({})
  const [gravando, setGravando] = useState<string | null>(null)

  const [ultimaBusca, setUltimaBusca] = useState<{ ini: string; fim: string } | null>(null)

  const [codigoDispositivo, setCodigoDispositivo] = useState<{ verification_uri: string; user_code: string } | null>(null)
  const [aguardandoLogin, setAguardandoLogin] = useState(false)
  const [erroConexao, setErroConexao] = useState('')
  const [carregandoStatus, setCarregandoStatus] = useState(true)
  const [statusErro, setStatusErro] = useState('')

  function carregarStatus() {
    setStatusErro('')
    setCarregandoStatus(true)
    api.email.status()
      .then(setStatus)
      // erro aqui não pode virar tela vazia (nem "Azure não configurado",
      // que seria enganoso) — mostra o que houve e deixa tentar de novo
      .catch(e => setStatusErro(e instanceof Error ? e.message : String(e)))
      .finally(() => setCarregandoStatus(false))
  }

  function pararPolling() {
    if (pollRef.current) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  function aplicarStatus(s: BuscaStatus) {
    setEmails(s.emails || [])
    setTotalCodigos(s.total_codigos || 0)
    setTotalEmails(s.total_emails)
    setPesquisadas(s.despesas_pesquisadas)
    setTotalDias(s.total_dias)
    setDiasProcessados(s.dias_processados)
    setDiaAtual(s.dia_atual)
    setAvisos(s.avisos || [])
    setBuscaCancelada(s.cancelado)
    if (s.erro) setErro(s.erro)

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

  async function buscar(iniParam?: string, fimParam?: string) {
    const ini = iniParam ?? dataIni
    const fim = fimParam ?? dataFim
    setErro('')
    setAvisos([])
    setEmails([])
    setTotalCodigos(0)
    setTotalEmails(0)
    setDiasProcessados(0)
    setBuscaCancelada(false)
    setDespesaPorEmail({})
    setNovaDespesaPorEmail({})
    setMesPorCodigo({})
    setGravados({})
    setConflitos({})
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
    buscar(novaIni, novaFim)
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

  // ---------------------------------------------------------------- escolhas

  function despesaDoEmail(e: EmailAchado): string {
    const escolhido = despesaPorEmail[e.id]
    if (escolhido !== undefined) return escolhido
    return e.despesa_sugerida_id ? String(e.despesa_sugerida_id) : ''
  }

  function mesDoCodigo(e: EmailAchado, c: Codigo): string {
    const escolhido = mesPorCodigo[c.chave]
    if (escolhido !== undefined) return escolhido
    // o vencimento do próprio boleto manda; a data do email é só reserva
    return c.mes_ref_sugerido || e.mes_ref_sugerido || currentMesRef()
  }

  function nomeDaDespesa(id: string): string {
    return despesas.find(d => String(d.id) === id)?.nome || ''
  }

  /** Códigos que ainda faltam confirmar neste email. */
  function pendentesDo(e: EmailAchado): Codigo[] {
    return e.codigos.filter(c => !c.ja_associado && !gravados[c.chave])
  }

  // ---------------------------------------------------------------- gravação

  /** Cria a despesa nova quando o email está marcado como "nova despesa",
   *  devolvendo o id. Feito uma vez por email, não por parcela. */
  async function resolverDespesaId(e: EmailAchado): Promise<number | null> {
    const sel = despesaDoEmail(e)
    if (sel !== 'nova') return sel ? Number(sel) : null

    const nome = (novaDespesaPorEmail[e.id] || '').trim()
    if (!nome) return null
    const keywords = nome.toLowerCase().split(/\s+/).filter(w => w.length >= 3)
    const primeiro = e.codigos.find(c => c.valor)
    const valorPadrao = primeiro?.valor
      ? Number(primeiro.valor.replace(/\./g, '').replace(',', '.'))
      : undefined
    const res = await api.catalogo.upsert({
      nome,
      tipo_valor: 'variavel',
      padrao_variabilidade: 'variavel_nao_sazonal',
      valor_padrao: valorPadrao,
      regras_match: JSON.stringify({ palavras_chave: keywords, faixa_valor: null, janela_dias: 5, banco: null })
    })
    setDespesas(ds => [...ds, { id: res.id, nome }])
    setDespesaPorEmail(prev => ({ ...prev, [e.id]: String(res.id) }))
    return res.id
  }

  /** Grava um conjunto de códigos de um mesmo email. É o único caminho que
   *  persiste: até clicar aqui, a busca é só leitura. */
  async function confirmar(e: EmailAchado, alvos: Codigo[], rotulo: string, substituir = false) {
    if (alvos.length === 0) return
    setErro('')
    setGravando(rotulo)
    try {
      const despesaId = await resolverDespesaId(e)
      if (!despesaId) {
        setErro('Escolha a despesa antes de confirmar.')
        return
      }
      const itens = alvos.map(c => ({
        despesa_id: despesaId,
        mes_ref: mesDoCodigo(e, c),
        linha_digitavel: c.codigo,
        tipo_codigo: c.tipo_codigo,
        valor: c.valor,
        remetente: e.remetente,
        substituir,
      }))
      const res = await api.email.associarLote(itens)
      if (!res.ok) {
        setErro(res.msg || 'Erro ao confirmar')
        return
      }

      // o backend recusa trocar código já gravado — o que voltou como
      // conflito não foi gravado e precisa aparecer nomeado na tela
      const recusados = new Map<string, Conflito>()
      for (const cf of (res.conflitos || []) as Conflito[]) {
        const alvo = alvos.find(c => c.chave === cf.codigo_novo.replace(/\D/g, ''))
        if (alvo) recusados.set(alvo.chave, cf)
      }
      const nome = nomeDaDespesa(String(despesaId))
      setGravados(prev => {
        const next = { ...prev }
        for (const c of alvos) {
          if (recusados.has(c.chave)) continue
          next[c.chave] = { despesaNome: nome, mesRef: mesDoCodigo(e, c) }
        }
        return next
      })
      // conflito resolvido (ou que não se repetiu) sai da tela; o que voltou
      // recusado entra/permanece, com o botão "Substituir"
      setConflitos(prev => {
        const next = { ...prev }
        for (const c of alvos) {
          const cf = recusados.get(c.chave)
          if (cf) next[c.chave] = cf
          else delete next[c.chave]
        }
        return next
      })

      const periodo = { ini: dataIni, fim: dataFim }
      localStorage.setItem(ULTIMA_BUSCA_KEY, JSON.stringify(periodo))
      setUltimaBusca(periodo)
    } catch (err) {
      setErro(String(err))
    } finally {
      setGravando(null)
    }
  }

  /** Tudo que tem despesa sugerida e ainda não está gravado, de todos os
   *  emails. Existe para o caso volumoso (carnê com dezenas de parcelas). */
  async function confirmarTodosSugeridos() {
    for (const e of emails) {
      if (!despesaDoEmail(e) || despesaDoEmail(e) === 'nova') continue
      const alvos = pendentesDo(e).filter(c => !conflitos[c.chave])
      if (alvos.length) await confirmar(e, alvos, 'tudo')
    }
  }

  const totalPendentes = emails.reduce((n, e) => n + pendentesDo(e).length, 0)
  const prontosParaConfirmar = emails.reduce((n, e) => {
    const sel = despesaDoEmail(e)
    if (!sel || sel === 'nova') return n
    return n + pendentesDo(e).filter(c => !conflitos[c.chave]).length
  }, 0)
  const totalGravados = Object.keys(gravados).length

  // ------------------------------------------------------------------ render

  function renderCodigo(e: EmailAchado, c: Codigo) {
    const ja = c.ja_associado
    const gravado = gravados[c.chave]
    const conflito = conflitos[c.chave]
    const mes = mesDoCodigo(e, c)
    const rotulo = `${e.id}:${c.chave}`

    return (
      <div key={c.chave} className="px-4 py-2.5 text-sm">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-[10px] uppercase tracking-wide text-zinc-500 flex-none w-10">
            {c.tipo_codigo === 'pix' ? 'Pix' : 'Boleto'}
          </span>

          {c.vencimento && (
            <span className="text-xs text-zinc-400 tabular-nums flex-none" title="vencimento lido do próprio código">
              vence {dataBR(c.vencimento)}
            </span>
          )}

          {c.valor && (
            <span className="text-emerald-400 font-medium tabular-nums flex-none">R$ {c.valor}</span>
          )}

          <code className="text-xs text-zinc-400 bg-zinc-900/60 px-2 py-1 rounded flex-1 min-w-[12rem] truncate">
            {c.codigo}
          </code>

          <button onClick={() => copiar(c.codigo)}
            className={cn('text-xs px-2 py-1 rounded border transition-colors whitespace-nowrap flex-none',
              copiado === c.codigo ? 'border-emerald-700 text-emerald-400' : 'border-zinc-700 text-zinc-400 hover:border-zinc-500')}>
            {copiado === c.codigo ? 'Copiado!' : 'Copiar'}
          </button>

          {/* Já associado numa busca anterior: só a indicação, sem pedir
              confirmação de novo — é o que torna repetir a busca indolor. */}
          {ja && !gravado ? (
            <span className={cn('text-xs whitespace-nowrap flex-none',
              ja.valor_bate === false ? 'text-amber-400' : 'text-zinc-500')}>
              {ja.valor_bate === false
                ? `⚠ associado em ${ja.despesa_nome} / ${mesRefLabel(ja.mes_ref)}, mas o valor gravado é R$ ${ja.valor_esperado.toFixed(2).replace('.', ',')}`
                : `já associado — ${ja.despesa_nome} / ${mesRefLabel(ja.mes_ref)} ✓`}
            </span>
          ) : gravado ? (
            <span className="text-xs text-emerald-400 whitespace-nowrap flex-none">
              gravado em {gravado.despesaNome} / {mesRefLabel(gravado.mesRef)} ✓
            </span>
          ) : (
            <>
              <input type="month" value={mes}
                onChange={ev => setMesPorCodigo(prev => ({ ...prev, [c.chave]: ev.target.value }))}
                className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-200 outline-none focus:border-emerald-500 flex-none" />
              <button onClick={() => confirmar(e, [c], rotulo)}
                disabled={gravando !== null || !despesaDoEmail(e)}
                className="px-3 py-1 rounded bg-emerald-600 text-white text-xs font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors whitespace-nowrap flex-none">
                {gravando === rotulo ? 'Gravando...' : 'Confirmar'}
              </button>
            </>
          )}
        </div>

        {!c.dv_ok && (
          <p className="mt-1 text-xs text-amber-500/90">
            ⚠ Dígito verificador não confere — a extração do PDF pode ter trocado um dígito.
            Confira contra o email antes de pagar.
          </p>
        )}

        {conflito && (
          <div className="mt-1.5 flex items-center gap-2 flex-wrap text-xs text-amber-300 bg-amber-950/20 border border-amber-800/40 rounded px-2 py-1.5">
            <span className="flex-1">
              {mesRefLabel(conflito.mes_ref)} já tem outro código gravado
              (<code className="text-amber-200/80">{conflito.codigo_atual}</code>) — nada foi alterado.
            </span>
            <button onClick={() => confirmar(e, [c], rotulo, true)} disabled={gravando !== null}
              className="px-2 py-1 rounded border border-amber-700 text-amber-300 hover:bg-amber-900/30 transition-colors whitespace-nowrap disabled:opacity-40">
              Substituir
            </button>
          </div>
        )}
      </div>
    )
  }

  function renderEmail(e: EmailAchado) {
    const sel = despesaDoEmail(e)
    const pendentes = pendentesDo(e)
    const jaAssociados = e.codigos.filter(c => c.ja_associado && !gravados[c.chave]).length

    return (
      <div key={e.id} className="rounded-lg border border-zinc-800/60 overflow-hidden">
        <div className="px-4 py-3 bg-zinc-900/40">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm text-zinc-200 truncate">{e.assunto}</p>
              <p className="text-zinc-600 text-xs truncate">
                {e.remetente} — {dataBR(e.data)}
                {e.codigos.length > 0 && (
                  <> — {e.codigos.length} código{e.codigos.length > 1 ? 's' : ''} de pagamento
                    {jaAssociados > 0 && <span className="text-zinc-500"> ({jaAssociados} já associado{jaAssociados > 1 ? 's' : ''})</span>}
                  </>
                )}
              </p>
            </div>
            {pendentes.length > 1 && (
              <button onClick={() => confirmar(e, pendentes.filter(c => !conflitos[c.chave]), e.id)}
                disabled={gravando !== null || !sel}
                className="px-3 py-1.5 rounded-md bg-emerald-600 text-white text-xs font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors whitespace-nowrap flex-none">
                {gravando === e.id ? 'Gravando...' : `Confirmar os ${pendentes.length}`}
              </button>
            )}
          </div>

          {/* Despesa por email: todas as parcelas de um carnê são da mesma
              despesa. O mês é que varia, e fica em cada linha. */}
          {pendentes.length > 0 && (
            <div className="mt-2 flex items-center gap-2 flex-wrap">
              <span className="text-xs text-zinc-500">Associar a</span>
              <DespesaPicker despesas={despesas} value={sel}
                onChange={v => setDespesaPorEmail(prev => ({ ...prev, [e.id]: v }))}
                placeholder="Digite pra buscar a despesa..." allowNova
                onSelectNova={q => {
                  setDespesaPorEmail(prev => ({ ...prev, [e.id]: 'nova' }))
                  setNovaDespesaPorEmail(prev => ({ ...prev, [e.id]: q }))
                }}
                className="w-56" />
              {sel === 'nova' && (
                <input value={novaDespesaPorEmail[e.id] || ''}
                  onChange={ev => setNovaDespesaPorEmail(prev => ({ ...prev, [e.id]: ev.target.value }))}
                  placeholder="Nome da nova despesa" autoFocus
                  className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500" />
              )}
              {e.despesa_sugerida_nome && (
                <span className="text-xs text-zinc-600">
                  sugestão{e.origem_sugestao === 'regra' ? ' (associação anterior)' : e.origem_sugestao === 'ia' ? ' (IA)' : ''}: {e.despesa_sugerida_nome}
                </span>
              )}
            </div>
          )}
        </div>

        {e.codigos.length > 0 && (
          <div className="divide-y divide-zinc-800/40">
            {e.codigos.map(c => renderCodigo(e, c))}
          </div>
        )}

        {e.notas.map((n, i) => (
          <p key={i} className="px-4 py-2 text-xs text-amber-500/80 border-t border-zinc-800/40">⚠ {n}</p>
        ))}
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full pt-3">
      <div className="px-6 pb-4 flex-none">
        <h1 className="text-xl font-semibold text-zinc-100">Procurar em Emails</h1>
        <p className="text-sm text-zinc-500 mt-0.5">
          Busca boletos, Pix e faturas no período escolhido. Cada email aparece com <strong className="text-zinc-400">todos</strong> os
          códigos de pagamento que tem — um carnê de financiamento traz o cronograma inteiro num PDF só. Escolha a
          despesa do email, confira o mês de cada parcela e confirme. <strong className="text-zinc-400">Confirmar grava</strong>;
          até clicar, a busca não altera nada. Código já associado numa busca anterior aparece só como indicação.
        </p>
        <p className="text-xs text-amber-500/80 mt-1">
          Valor e vencimento de boleto são lidos do próprio código. Mesmo assim, confira contra o email
          original antes de pagar.
        </p>
      </div>

      <div className="px-6 pb-6 flex-1 overflow-auto space-y-4">
        {carregandoStatus && !status && !statusErro && (
          <p className="text-sm text-zinc-500">Verificando conexão com o email...</p>
        )}

        {statusErro && (
          <div className="rounded-lg border border-red-900/50 bg-red-950/20 p-4 max-w-xl">
            <p className="text-sm text-red-300 mb-1">Não consegui verificar a conexão com o email.</p>
            <p className="text-xs text-zinc-500 mb-3">{statusErro}</p>
            <button onClick={carregarStatus}
              className="px-3 py-1.5 rounded-md border border-zinc-700 text-zinc-300 text-sm hover:border-emerald-600 hover:text-emerald-400 transition-colors">
              Tentar de novo
            </button>
          </div>
        )}

        {status && !status.conectado && !codigoDispositivo && (
          <div className="rounded-lg border border-zinc-700/60 bg-zinc-900/60 p-4 max-w-xl">
            <p className="text-sm text-zinc-400 mb-3">Email ainda não conectado.</p>
            <button onClick={conectar} disabled={!status.configurado}
              className="px-4 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
              Conectar com Microsoft
            </button>
            {!status.configurado && (
              <p className="text-xs text-amber-500/80 mt-2">
                Para conectar, configure <code className="text-amber-400">EMAIL_CLIENT_ID</code> no arquivo{' '}
                <code className="text-amber-400">.env</code> (veja <code>.env.example</code>) e reinicie o app.
              </p>
            )}
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
                : `Buscando dia ${Math.min(diasProcessados + 1, totalDias)} de ${totalDias}${diaAtual ? ` (${dataBR(diaAtual)})` : ''} — ${totalCodigos} código(s) em ${emails.length} email(s) até agora`}
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
              {totalEmails} emails {buscando ? 'verificados até agora' : 'no período'}, {pesquisadas} despesas no
              catálogo — {emails.length} email(s) com cobrança, {totalCodigos} código(s) de pagamento.
              {totalGravados > 0 && <span className="text-emerald-400"> {totalGravados} gravado(s) nesta sessão.</span>}
            </p>

            {prontosParaConfirmar > 1 && (
              <div className="sticky top-0 z-10 flex items-center gap-3 bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-3 shadow-lg">
                <p className="text-sm text-zinc-300 flex-1">
                  {totalPendentes} código(s) por confirmar — {prontosParaConfirmar} já com despesa definida.
                </p>
                <button onClick={confirmarTodosSugeridos} disabled={gravando !== null}
                  className="px-4 py-1.5 rounded-md bg-emerald-600 text-white text-sm font-medium hover:bg-emerald-500 transition-colors disabled:opacity-40">
                  {gravando === 'tudo' ? 'Gravando...' : `Confirmar todos (${prontosParaConfirmar})`}
                </button>
              </div>
            )}

            {!buscando && emails.length === 0 && (
              <p className="text-sm text-zinc-600">Nada encontrado nesse período (de {totalEmails} emails verificados) — tente um intervalo maior ou confira se o email não está em outra pasta.</p>
            )}

            {emails.map(renderEmail)}
          </div>
        )}
      </div>
    </div>
  )
}
