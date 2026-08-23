import { useEffect, useState, useRef, Fragment } from 'react'
import { api } from '../lib/api'
import { cn, formatBRL } from '../lib/utils'
import { DespesaPicker } from '../components/DespesaPicker'

type Step = 'selecionar' | 'revisar' | 'concluido'

interface ParsedTx { data: string; descricao: string; valor: number }

type Natureza = 'despesa' | 'receita'

interface DetalheMatch {
  lancamento_id: number
  item_id: number
  item_id_sugerido: number | null
  item_nome: string
  valor_esperado: number
  transacao_id: number
  descricao_transacao: string
  valor: number
  data: string
  // o vocabulário muda de lado: uma despesa fica "Paga", uma receita "Recebida"
  status: 'pago' | 'agendado' | 'recebido' | 'previsto'
  // presente só quando o casamento já existia e o status mudou desde então
  // (ex: estava agendado e o débito caiu) — ver rodar_batimento
  status_anterior?: 'pago' | 'agendado' | 'nao_encontrado'
  // casamento já persistido numa confirmação anterior. Aparece aqui para poder
  // ser corrigido — só é regravado se a despesa for trocada.
  ja_gravado?: boolean
  // detalhes por ocorrência (doc 14 §5): objetivo do resgate esporádico e qual
  // débito este crédito anula
  objetivo?: string | null
  estorna_transacao_id?: number | null
  estorna_despesa_id?: number | null
  // vínculo que mora só na transação (item esporádico, ou 2ª ocorrência de um
  // "mais de um por mês"). Não tem lançamento, então não volta para a seção 2.
  sem_lancamento?: boolean
}

/** Rótulo do tipo da receita. A tela precisa mostrá-lo na hora de classificar:
 *  é o tipo — não o nome do item — que decide se aquilo é renda e se o app vai
 *  pedir o alvo do estorno. Um item chamado "estorno" mas tipado como
 *  "reembolso" não estorna nada, e sem exibir o tipo isso é invisível. */
const TIPO_RECEITA_LABEL: Record<string, string> = {
  salario: 'Salário', juros: 'Juros', reembolso: 'Reembolso', outra: 'Outra',
  resgate_mensal: 'Resgate mensal', resgate_esporadico: 'Resgate esporádico',
  estorno: 'Estorno', transferencia: 'Transferência'
}

const STATUS_LABEL: Record<string, string> = {
  pago: 'Pago', agendado: 'Agendado', nao_encontrado: 'Em aberto',
  recebido: 'Recebido', previsto: 'Previsto'
}

const STATUS_CONFIRMADO = new Set(['pago', 'recebido'])

/** Rótulos que mudam conforme o lado. Manter num lugar só evita a tela dizer
 *  "despesa" no bloco de entradas. */
const TEXTO: Record<Natureza, {
  item: string; itens: string; sec1: string; sec2: string; sec3: string; ajuda3: string
  associarTx: string; associarItem: string; novoItem: string; trocar: string
}> = {
  despesa: {
    item: 'despesa', itens: 'Despesas',
    sec1: 'Despesas casadas',
    sec2: 'Despesas ativas que não encontrei no extrato',
    sec3: 'Débitos do extrato sem despesa',
    ajuda3: 'Associe a uma despesa ativa ainda em aberto, ou crie uma despesa nova.',
    associarTx: 'Associar débito', associarItem: 'Associar despesa',
    novoItem: 'Nova despesa', trocar: 'Não é essa despesa'
  },
  receita: {
    item: 'receita', itens: 'Receitas',
    sec1: 'Receitas casadas',
    sec2: 'Receitas ativas que não encontrei no extrato',
    sec3: 'Créditos do extrato sem receita',
    ajuda3: 'Nem todo crédito é renda: escolha uma receita do tipo Resgate, Estorno ou Transferência '
      + 'para o valor entrar na conta sem contar como renda do mês.',
    associarTx: 'Associar crédito', associarItem: 'Associar receita',
    novoItem: 'Nova receita', trocar: 'Não é essa receita'
  }
}

interface NaoEncontrado {
  lancamento_id: number
  item_id: number
  item_nome: string
  valor_esperado: number
  tipo?: string
}

interface LadoBatimento {
  matched: number
  total: number
  detalhes: DetalheMatch[]
  nao_encontrados: NaoEncontrado[]
  transacoes_sobrando: TransacaoSobrando[]
}

interface TransacaoSobrando {
  id: number
  data: string
  descricao: string
  valor: number
  situacao: 'efetivada' | 'agendada'
  // débito que este crédito parece anular — sugestão do backend, nunca aplicada
  // sozinha (doc 14 §5)
  estorno_sugerido?: { transacao_id: number; descricao: string; data: string; valor: number }
}

interface Despesa {
  id: number
  nome: string
  ativo?: number
  varios_por_mes?: number
  recorrencia?: 'fixa' | 'esporadica'
  tipo?: string
}

const BANCOS = ['Itaú', 'Bradesco', 'Nubank', 'BTG', 'XP', 'Outro']

/** Cabeçalho das três partes da revisão. A ordem é fixa e numerada de
 *  propósito: casadas, depois o que faltou de cada lado. */
function Secao({ n, titulo, qtd, ajuda }: { n: number; titulo: string; qtd: number; ajuda: string }) {
  return (
    <div className="mb-2">
      <div className="flex items-baseline gap-2">
        <span className="text-xs font-semibold text-zinc-600 tabular-nums">{n}.</span>
        <h2 className="text-sm font-semibold text-zinc-200">{titulo}</h2>
        <span className="text-xs text-zinc-500 tabular-nums">({qtd})</span>
      </div>
      <p className="text-xs text-zinc-500 ml-5">{ajuda}</p>
    </div>
  )
}

export function Importacao({ active }: { active: boolean }) {
  const [step, setStep] = useState<Step>('selecionar')
  const [banco, setBanco] = useState('Itaú')
  const [file, setFile] = useState<File | null>(null)
  const [transacoes, setTransacoes] = useState<ParsedTx[]>([])
  const [mesRef, setMesRef] = useState(() => {
    const n = new Date()
    return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, '0')}`
  })
  // O batimento devolve os dois lados de uma vez; a tela mostra um por vez.
  const [resultado, setResultado] = useState<Record<Natureza, LadoBatimento> | null>(null)
  const [natureza, setNatureza] = useState<Natureza>('despesa')
  // Os dois catálogos, não um. A correção da seção 1 escolhe entre itens do
  // MESMO lado — oferecer despesas para corrigir um crédito produziria um
  // vínculo sem sentido, e era o que acontecia.
  const [catalogos, setCatalogos] = useState<Record<Natureza, Despesa[]>>({ despesa: [], receita: [] })
  const [corrigindo, setCorrigindo] = useState<number | null>(null)
  const [selecionada, setSelecionada] = useState('')
  const [novaDespesaNome, setNovaDespesaNome] = useState('')
  const [associando, setAssociando] = useState<number | null>(null)
  const [selecionadaAssoc, setSelecionadaAssoc] = useState('')
  const [novaDespesaNomeAssoc, setNovaDespesaNomeAssoc] = useState('')
  // seção 2 — o inverso da 3: parte da despesa e escolhe a transação
  const [buscandoTx, setBuscandoTx] = useState<number | null>(null)
  const [txSelecionada, setTxSelecionada] = useState('')
  // detalhes que só existem por ocorrência (doc 14 §5)
  const [objetivoVal, setObjetivoVal] = useState('')
  const [estornaVal, setEstornaVal] = useState('')
  // seção 1: par de estorno que ainda não diz qual débito anula
  const [definindoEstorno, setDefinindoEstorno] = useState<number | null>(null)
  const [estornaSec1, setEstornaSec1] = useState('')
  // o alvo do estorno pode ser a despesa (quando já se sabe qual é) ou a linha
  // do extrato (quando ela ainda não foi classificada). O vínculo que fica é
  // sempre com a despesa — pela linha, ele é preenchido por propagação.
  const [alvoEstorno, setAlvoEstorno] = useState<'despesa' | 'lancamento'>('despesa')
  const [confirmando, setConfirmando] = useState(false)
  const [erroConfirmar, setErroConfirmar] = useState('')
  const [confirmado, setConfirmado] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  // a tela fica sempre montada (ver App.tsx) — sem isso, uma despesa criada
  // no Catálogo enquanto essa aba já estava aberta nunca apareceria aqui
  async function carregarCatalogos() {
    const [d, r] = await Promise.all([api.catalogo.list(), api.receitas.list()])
    setCatalogos({ despesa: d, receita: r })
  }

  useEffect(() => {
    if (active) carregarCatalogos()
  }, [active])

  const lado = resultado?.[natureza] ?? null
  const t = TEXTO[natureza]
  const catalogo = catalogos[natureza]

  /** Atualiza apenas o lado que está sendo revisado. */
  function setLado(fn: (l: LadoBatimento) => LadoBatimento) {
    setResultado(r => r ? { ...r, [natureza]: fn(r[natureza]) } : r)
  }

  // trocar de lado fecha qualquer edição aberta: os ids são de outra lista
  function trocarNatureza(n: Natureza) {
    setNatureza(n)
    setCorrigindo(null); setAssociando(null); setBuscandoTx(null); setDefinindoEstorno(null)
  }

  async function processar() {
    if (!file) return
    setLoading(true)
    setMsg('')
    try {
      const res = await api.importacao.enviar(file, banco, mesRef)
      if (res.ok) {
        setTransacoes(res.transacoes)
        setMsg(res.msg)
        setStep('revisar')
      } else {
        setMsg(res.msg || 'Erro ao processar arquivo')
      }
    } catch (e) {
      setMsg(String(e))
    } finally {
      setLoading(false)
    }
  }

  async function rodarBatimento() {
    setLoading(true)
    try {
      const [res] = await Promise.all([api.batimento.rodar(mesRef), carregarCatalogos()])
      setResultado(res)
      setConfirmado(null)
      setStep('concluido')
    } finally {
      setLoading(false)
    }
  }

  function abrirCorrecao(transacaoId: number) {
    setCorrigindo(corrigindo === transacaoId ? null : transacaoId)
    setSelecionada('')
    setNovaDespesaNome('')
  }

  async function aplicarCorrecao(d: DetalheMatch) {
    let despesaId: number
    let despesaNome: string
    if (selecionada === 'nova') {
      const nome = novaDespesaNome.trim()
      if (!nome) return
      despesaId = await criarDespesa(nome, d.valor)
      despesaNome = nome
    } else {
      if (!selecionada) return
      despesaId = Number(selecionada)
      despesaNome = catalogo.find(ds => ds.id === despesaId)?.nome ?? '?'
    }

    // Só atualiza o estado local — nada é gravado até "Confirmar tudo"
    setLado(r => {
      const detalhes = r.detalhes.map(x =>
        x.transacao_id === d.transacao_id ? { ...x, item_id: despesaId, item_nome: despesaNome } : x)

      // Trocar a despesa deste casamento mexe nas outras duas seções: a nova
      // deixa de estar em aberto, e a antiga volta a estar (a não ser que
      // tenha casado com outra transação). Sem isso a despesa liberada sumia
      // da tela e a recém-escolhida continuava aparecendo como não encontrada.
      const antiga = r.detalhes.find(x => x.transacao_id === d.transacao_id)
      const liberada = antiga && !antiga.sem_lancamento && antiga.item_id !== despesaId
        && !detalhes.some(x => x.item_id === antiga.item_id)
        ? [{ lancamento_id: antiga.lancamento_id, item_id: antiga.item_id,
             item_nome: antiga.item_nome, valor_esperado: antiga.valor_esperado }]
        : []

      return {
        ...r,
        detalhes,
        nao_encontrados: [...r.nao_encontrados.filter(x => x.item_id !== despesaId), ...liberada]
          .sort((a, b) => a.item_nome.localeCompare(b.item_nome, 'pt-BR'))
      }
    })
    setCorrigindo(null)
  }

  /** Casa transação + despesa e move o par das seções 2 e 3 para a 1.
   *  As duas direções de associação terminam aqui — só muda por qual ponta o
   *  usuário começou. Só mexe no estado local; nada é gravado até "Confirmar tudo". */
  function associarPar(t: TransacaoSobrando, despesaId: number, despesaNome: string,
                       extras: { objetivo?: string; estorna_transacao_id?: number
                                 estorna_despesa_id?: number } = {}) {
    setLado(r => {
      const status = t.situacao === 'efetivada'
        ? (natureza === 'receita' ? 'recebido' as const : 'pago' as const)
        : (natureza === 'receita' ? 'previsto' as const : 'agendado' as const)
      const emAberto = r.nao_encontrados.find(x => x.item_id === despesaId)
      return {
        ...r,
        nao_encontrados: r.nao_encontrados.filter(x => x.item_id !== despesaId),
        transacoes_sobrando: r.transacoes_sobrando.filter(x => x.id !== t.id),
        detalhes: [...r.detalhes, {
          lancamento_id: emAberto?.lancamento_id ?? 0, item_id: despesaId,
          item_id_sugerido: null, item_nome: despesaNome,
          valor_esperado: emAberto?.valor_esperado ?? Math.abs(t.valor),
          transacao_id: t.id, descricao_transacao: t.descricao, valor: Math.abs(t.valor), data: t.data, status,
          ...extras
        }]
      }
    })
  }

  async function criarDespesa(nome: string, valor: number) {
    const keywords = nome.toLowerCase().split(/\s+/).filter(w => w.length >= 3)
    const comum = {
      nome,
      tipo_valor: 'variavel',
      padrao_variabilidade: 'variavel_nao_sazonal',
      valor_padrao: valor,
      regras_match: JSON.stringify({ palavras_chave: keywords, faixa_valor: null, janela_dias: 5, banco: null })
    }
    // receita criada aqui nasce como 'outra' — o tipo (salário, resgate...) é
    // escolha do usuário no Catálogo, e chutar aqui poderia classificar um
    // resgate como renda, que é justamente o erro que o tipo existe para evitar
    const res = natureza === 'receita'
      ? await api.receitas.upsert({ ...comum, tipo: 'outra' })
      : await api.catalogo.upsert(comum)
    setCatalogos(c => ({ ...c, [natureza]: [...c[natureza], { id: res.id, nome }] }))
    return res.id as number
  }

  function abrirAssociacao(tx: TransacaoSobrando) {
    setAssociando(associando === tx.id ? null : tx.id)
    setSelecionadaAssoc('')
    setNovaDespesaNomeAssoc('')
    setObjetivoVal('')
    // Quando há sugestão, ela é de uma LINHA do extrato — então o caminho
    // pré-selecionado é o do lançamento, já preenchido. Sem sugestão, o
    // provável é o usuário já saber a despesa.
    if (tx.estorno_sugerido) {
      setAlvoEstorno('lancamento')
      setEstornaVal(String(tx.estorno_sugerido.transacao_id))
    } else {
      setAlvoEstorno('despesa')
      setEstornaVal('')
    }
  }

  /** Tipo do item escolhido na seção 3 — decide se pede objetivo ou estorno. */
  function tipoDoItem(id: string): string | undefined {
    if (!id || id === 'nova') return undefined
    const n = Number(id)
    return catalogo.find(x => x.id === n)?.tipo
  }

  async function aplicarAssociacao(t: TransacaoSobrando) {
    let despesaId: number
    let despesaNome: string
    if (selecionadaAssoc === 'nova') {
      const nome = novaDespesaNomeAssoc.trim()
      if (!nome) return
      despesaId = await criarDespesa(nome, Math.abs(t.valor))
      despesaNome = nome
    } else {
      if (!selecionadaAssoc) return
      despesaId = Number(selecionadaAssoc)
      despesaNome = catalogo.find(ds => ds.id === despesaId)?.nome
        ?? lado?.nao_encontrados.find(x => x.item_id === despesaId)?.item_nome ?? '?'
    }
    const tipo = tipoDoItem(selecionadaAssoc)
    associarPar(t, despesaId, despesaNome, {
      ...(tipo === 'resgate_esporadico' && objetivoVal.trim() ? { objetivo: objetivoVal.trim() } : {}),
      ...(tipo === 'estorno' && estornaVal
        ? (alvoEstorno === 'despesa'
            ? { estorna_despesa_id: Number(estornaVal) }
            : { estorna_transacao_id: Number(estornaVal) })
        : {})
    })
    setAssociando(null)
  }

  function abrirDefinicaoEstorno(d: DetalheMatch) {
    setDefinindoEstorno(definindoEstorno === d.transacao_id ? null : d.transacao_id)
    setAlvoEstorno(d.estorna_transacao_id ? 'lancamento' : 'despesa')
    setEstornaSec1(String(d.estorna_transacao_id ?? d.estorna_despesa_id ?? ''))
  }

  function aplicarDefinicaoEstorno(d: DetalheMatch) {
    if (!estornaSec1) return
    const alvo = alvoEstorno === 'despesa'
      ? { estorna_despesa_id: Number(estornaSec1), estorna_transacao_id: null }
      : { estorna_transacao_id: Number(estornaSec1), estorna_despesa_id: null }
    setLado(r => ({
      ...r,
      detalhes: r.detalhes.map(x => x.transacao_id === d.transacao_id ? { ...x, ...alvo } : x)
    }))
    setDefinindoEstorno(null)
  }

  function abrirBuscaTx(despesaId: number) {
    setBuscandoTx(buscandoTx === despesaId ? null : despesaId)
    setTxSelecionada('')
  }

  function aplicarBuscaTx(n: NaoEncontrado) {
    const tx = lado?.transacoes_sobrando.find(x => String(x.id) === txSelecionada)
    if (!tx) return
    associarPar(tx, n.item_id, n.item_nome)
    setBuscandoTx(null)
  }

  // O que já estava gravado e não foi tocado não é reenviado: regravar o mesmo
  // par não muda nada no banco e ainda contaria como mais um acerto da regra
  // aprendida, inflando o placar a cada vez que o batimento roda.
  /** Estorno tem que dizer o que anula — sem isso o crédito vira entrada solta
   *  e o débito estornado segue contando como despesa paga. O backend recusa,
   *  então a tela cobra antes de deixar confirmar. */
  const ehEstorno = (itemId: number) => catalogos.receita.find(x => x.id === itemId)?.tipo === 'estorno'
  const itemEstorno = catalogos.receita.find(x => x.tipo === 'estorno')

  const estornosIncompletos = (['despesa', 'receita'] as Natureza[]).flatMap(n =>
    n === 'receita'
      ? pendentesDe(resultado?.receita).filter(
          d => ehEstorno(d.item_id) && !d.estorna_transacao_id && !d.estorna_despesa_id)
      : [])

  function pendentesDe(l: LadoBatimento | null | undefined) {
    return (l?.detalhes ?? []).filter(d => !d.ja_gravado || d.item_id !== d.item_id_sugerido)
  }
  const paresPendentes = pendentesDe(lado)
  // "Confirmar tudo" grava os dois lados: o usuário revisa saídas e entradas na
  // mesma passada, e ter que confirmar duas vezes seria fácil de esquecer.
  const totalPendente = pendentesDe(resultado?.despesa).length + pendentesDe(resultado?.receita).length

  async function confirmarTudo() {
    if (totalPendente === 0) return
    setConfirmando(true)
    try {
      const pares = (['despesa', 'receita'] as Natureza[]).flatMap(n =>
        pendentesDe(resultado?.[n]).map(d => ({
          natureza: n, transacao_id: d.transacao_id,
          item_id: d.item_id, item_id_sugerido: d.item_id_sugerido,
          objetivo: d.objetivo,
          estorna_transacao_id: d.estorna_transacao_id,
          estorna_despesa_id: d.estorna_despesa_id
        })))
      const res = await api.batimento.confirmar(mesRef, pares)
      setConfirmado(res.confirmados ?? pares.length)
      setErroConfirmar('')
    } catch (e) {
      let msg = String(e)
      try { msg = JSON.parse(msg.replace(/^Error:\s*/, '')).msg ?? msg } catch { /* não era JSON */ }
      setErroConfirmar(msg)
    } finally {
      setConfirmando(false)
    }
  }

  const STEPS: [Step, string][] = [['selecionar', '1. Selecionar'], ['revisar', '2. Revisar'], ['concluido', '3. Concluído']]
  const stepIdx = STEPS.findIndex(([s]) => s === step)

  // As duas seções de associação são espelho uma da outra e comem da mesma
  // lista: o que sobrou de um lado é a opção do outro. Ambas encolhem sozinhas
  // conforme os pares vão sendo montados.
  //
  // "Ainda não associada" tem três fontes, e as duas combos respeitam as três:
  // não pode ter dono gravado no banco (o backend filtra `item_id IS NULL`),
  // não pode estar num casamento sugerido nesta rodada, e não pode ter sido
  // usada num par montado aqui na tela. Só a primeira vem pronta do servidor;
  // as outras duas mudam a cada clique, então são conferidas no render — o que
  // já está na seção 1 nunca aparece como opção nas seções 2 e 3.
  const txAssociadas = new Set((lado?.detalhes ?? []).map(d => d.transacao_id))
  const despesasAssociadas = new Set((lado?.detalhes ?? []).map(d => d.item_id))

  const opcoesTransacao = (lado?.transacoes_sobrando ?? [])
    .filter(t => !txAssociadas.has(t.id))
    .map(t => ({
      id: t.id,
      nome: `${new Date(t.data + 'T00:00:00').toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })}  ·  ${t.descricao}  ·  ${formatBRL(Math.abs(t.valor))}`
    }))
  // Débitos que um estorno pode anular: **todos** os do período, não só os sem
  // despesa. Uma cobrança já casada também pode ser estornada — e nesse caso o
  // vínculo dela é desfeito na confirmação, porque o pagamento não aconteceu.
  const dataCurta = (d: string) =>
    new Date(d + 'T00:00:00').toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })

  // Alvo do estorno pela DESPESA: o catálogo de saídas inteiro. Não se limita
  // ao que apareceu no extrato deste mês — a cobrança estornada pode ter sido
  // de um mês anterior.
  const opcoesDespesaEstorno = catalogos.despesa
    .filter(d => d.ativo !== 0)
    .map(d => ({ id: d.id, nome: d.nome }))

  const opcoesDebito = [
    ...(resultado?.despesa.transacoes_sobrando ?? []).map(t => ({
      id: t.id,
      nome: `${dataCurta(t.data)}  ·  ${t.descricao}  ·  ${formatBRL(Math.abs(t.valor))}`
    })),
    ...(resultado?.despesa.detalhes ?? []).map(d => ({
      id: d.transacao_id,
      nome: `${dataCurta(d.data)}  ·  ${d.descricao_transacao}  ·  ${formatBRL(d.valor)}  ·  ${d.item_nome}`
    }))
  ]

  // Sai da lista quem já casou — a não ser que o catálogo diga que aquele item
  // pode acontecer mais de uma vez no mês. Antes isso era deduzido da
  // recorrência, o que impedia uma despesa fixa de receber duas cobranças no
  // mesmo mês (a escola cobrando mensalidade e material, por exemplo).
  //
  // A lista sai do CATÁLOGO, não das listas do batimento. O batimento é uma
  // foto do momento em que rodou: um item criado depois dele não estaria em
  // `nao_encontrados` (item fixo só entra ali quando ganha lançamento) e ficaria
  // invisível até rodar de novo. Como o catálogo é recarregado ao voltar para
  // esta aba, criar no Catálogo e vir associar aqui funciona na hora.
  //
  // `nao_encontrados` continua servindo para uma coisa: o valor previsto, que
  // só existe quando há lançamento no mês.
  const previstoPorItem = new Map((lado?.nao_encontrados ?? []).map(n => [n.item_id, n.valor_esperado]))

  const opcoesDespesa = catalogo
    .filter(d => d.ativo !== 0)
    .filter(d => !!d.varios_por_mes || !despesasAssociadas.has(d.id))
    .map(d => {
      const previsto = previstoPorItem.get(d.id)
      // no lado das entradas o tipo vem primeiro: é ele que classifica
      const marca = natureza === 'receita'
        ? (TIPO_RECEITA_LABEL[d.tipo ?? 'outra'] ?? d.tipo)
        : previsto && previsto > 0 ? formatBRL(previsto)
        : d.recorrencia === 'esporadica' ? 'esporádica'
        : d.varios_por_mes ? 'mais de um por mês' : null
      return { id: d.id, nome: marca ? `${d.nome}  ·  ${marca}` : d.nome }
    })

  return (
    <div className="flex flex-col h-full pt-3">
      <div className="px-6 pb-4 flex-none">
        <h1 className="text-xl font-semibold text-zinc-100">Importar Extrato</h1>
        <p className="text-sm text-zinc-500 mt-0.5">OFX, CSV ou Excel — importa e bate automaticamente com os lançamentos do mês. Use Excel para capturar lançamentos futuros/agendados.</p>
      </div>

      {/* Steps indicator */}
      <div className="px-6 pb-5 flex items-center gap-2 text-xs flex-none">
        {STEPS.map(([s, label], i) => {
          const done = stepIdx > i
          const active = step === s
          return (
            <div key={s} className="flex items-center gap-2">
              <span className={cn('px-2.5 py-1 rounded font-medium',
                active ? 'bg-emerald-600 text-white' : done ? 'text-emerald-400' : 'text-zinc-600')}>
                {label}
              </span>
              {i < STEPS.length - 1 && <span className="text-zinc-700">›</span>}
            </div>
          )
        })}
      </div>

      <div className="px-6 pb-6 flex-1 overflow-auto">

        {/* Step 1 — selecionar */}
        {step === 'selecionar' && (
          <div className="space-y-5 max-w-lg">
            <div>
              <label className="text-sm text-zinc-400 block mb-2">Banco</label>
              <div className="flex flex-wrap gap-2">
                {BANCOS.map(b => (
                  <button key={b} onClick={() => setBanco(b)}
                    className={cn('px-3 py-1.5 rounded-md text-sm border transition-colors',
                      banco === b ? 'bg-emerald-600/10 border-emerald-600 text-emerald-400' : 'border-zinc-700 text-zinc-400 hover:border-zinc-500')}>
                    {b}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="text-sm text-zinc-400 block mb-2">Mês de referência</label>
              <input type="month" value={mesRef} onChange={e => setMesRef(e.target.value)}
                className="bg-zinc-800 border border-zinc-700 rounded-md px-3 py-2 text-sm text-zinc-200 outline-none focus:border-emerald-500" />
            </div>

            <div>
              <label className="text-sm text-zinc-400 block mb-2">Arquivo (.ofx, .csv, .xls ou .xlsx)</label>
              <input ref={fileRef} type="file" accept=".ofx,.csv,.txt,.xls,.xlsx,.xlsm" className="hidden"
                onChange={e => setFile(e.target.files?.[0] ?? null)} />
              <div className="flex items-center gap-3">
                <button onClick={() => fileRef.current?.click()}
                  className="px-4 py-2 rounded-md border border-zinc-700 text-sm text-zinc-300 hover:border-zinc-500 hover:text-zinc-100 transition-colors">
                  Selecionar arquivo
                </button>
                {file && <span className="text-sm text-zinc-500 truncate max-w-xs">{file.name}</span>}
              </div>
            </div>

            {msg && <p className="text-sm text-red-400">{msg}</p>}

            <button onClick={processar} disabled={!file || loading}
              className="px-5 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
              {loading ? 'Processando...' : 'Importar'}
            </button>

            {/* Reassociar não precisa de arquivo: as transações do mês já estão
                no banco desde a última importação. Exigir o extrato de novo só
                para corrigir um vínculo era trabalho à toa. */}
            <div className="pt-4 border-t border-zinc-800/60">
              <p className="text-sm text-zinc-500 mb-2">
                Já importou o extrato e só quer revisar as associações?
              </p>
              <button onClick={rodarBatimento} disabled={loading}
                className="px-4 py-2 rounded-md border border-zinc-700 text-sm text-zinc-300 disabled:opacity-40 hover:border-zinc-500 hover:text-zinc-100 transition-colors">
                {loading ? 'Batendo...' : `Rebater ${mesRef} sem importar`}
              </button>
            </div>
          </div>
        )}

        {/* Step 2 — revisar */}
        {step === 'revisar' && (
          <div>
            <div className="flex items-center justify-between mb-3">
              <div>
                <p className="text-sm text-zinc-400">{msg}</p>
                {/* saídas e entradas contadas à parte: o extrato traz as duas, e
                    o batimento vai casar cada lado com seu catálogo */}
                <p className="text-xs text-zinc-500 mt-0.5">
                  {transacoes.filter(x => x.valor < 0).length} saídas ·{' '}
                  {transacoes.filter(x => x.valor > 0).length} entradas
                </p>
              </div>
              <button onClick={rodarBatimento} disabled={loading}
                className="px-4 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
                {loading ? 'Batendo...' : 'Rodar Batimento Automático'}
              </button>
            </div>
            <div className="rounded-lg overflow-hidden border border-zinc-800/60 max-h-[500px] overflow-y-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-zinc-800 bg-zinc-900/40 sticky top-0">
                    <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Data</th>
                    <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Descrição</th>
                    <th className="px-4 py-2 text-right text-xs text-zinc-500 font-medium">Valor</th>
                  </tr>
                </thead>
                <tbody>
                  {transacoes.map((t, i) => (
                    <tr key={i} className={cn('hover:bg-zinc-800/40', i > 0 && 'border-t border-zinc-800/40')}>
                      <td className="px-4 py-2 text-zinc-500 text-xs tabular-nums">
                        {new Date(t.data + 'T00:00:00').toLocaleDateString('pt-BR')}
                      </td>
                      <td className="px-4 py-2 text-zinc-300">{t.descricao}</td>
                      <td className={cn('px-4 py-2 text-right tabular-nums font-medium', t.valor < 0 ? 'text-red-400' : 'text-emerald-400')}>
                        {new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(t.valor)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Step 3 — concluído */}
        {step === 'concluido' && resultado && lado && (
          <div className="space-y-6 max-w-4xl">
            {/* Saídas e entradas são revisadas na mesma tela, uma de cada vez.
                O contador de pendências fica no botão, que grava as duas. */}
            <div className="flex rounded-md border border-zinc-700 overflow-hidden w-fit">
              {(['despesa', 'receita'] as Natureza[]).map(n => (
                <button key={n} onClick={() => trocarNatureza(n)}
                  className={cn('px-4 py-1.5 text-sm transition-colors',
                    natureza === n ? 'bg-emerald-600 text-white' : 'text-zinc-400 hover:text-zinc-200')}>
                  {n === 'despesa' ? 'Saídas' : 'Entradas'}
                  <span className="ml-2 text-xs opacity-70 tabular-nums">
                    {resultado[n].matched}/{resultado[n].total}
                  </span>
                </button>
              ))}
            </div>

            {confirmado !== null ? (
              <div className="rounded-lg border border-emerald-800/40 bg-emerald-950/20 p-8 text-center">
                <div className="text-5xl font-bold text-emerald-400 mb-2">{confirmado}</div>
                <p className="text-zinc-400 text-sm">lançamentos confirmados e gravados</p>
              </div>
            ) : (
              <div className="rounded-lg border border-zinc-700/60 bg-zinc-900/60 p-8 text-center">
                <div className="text-5xl font-bold text-zinc-200 mb-2">{lado.matched}/{lado.total}</div>
                <p className="text-zinc-500 text-sm">
                  {t.itens.toLowerCase()} casadas — revise abaixo e clique em "Confirmar tudo" para gravar.
                  Nada é salvo antes disso.
                </p>
              </div>
            )}

            {/* ---- 1. casadas ---- */}
            {lado.detalhes.length > 0 && (
              <div>
                <Secao n={1} titulo={t.sec1} qtd={lado.detalhes.length}
                  ajuda={'Inclui o que já foi gravado antes. Se alguma despesa estiver errada, corrija na linha — '
                    + 'só o que você trocar é regravado.'} />
                <div className="rounded-lg overflow-hidden border border-zinc-800/60">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-zinc-800 bg-zinc-900/40">
                        <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Despesa</th>
                        <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Transação no extrato</th>
                        <th className="px-4 py-2 text-right text-xs text-zinc-500 font-medium">Valor</th>
                        <th className="px-4 py-2 text-center text-xs text-zinc-500 font-medium">Status</th>
                        <th className="px-4 py-2"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {lado.detalhes.map((d, i) => (
                        <Fragment key={d.transacao_id}>
                          <tr className={cn('hover:bg-zinc-800/40', i > 0 && 'border-t border-zinc-800/40')}>
                            <td className="px-4 py-2 text-zinc-200 font-medium">
                              {d.item_nome}
                              {d.ja_gravado && d.item_id === d.item_id_sugerido && (
                                <span className="ml-2 text-[10px] uppercase tracking-wide text-zinc-600">gravado</span>
                              )}
                            </td>
                            <td className="px-4 py-2 text-zinc-400">{d.descricao_transacao}</td>
                            <td className="px-4 py-2 text-right tabular-nums text-zinc-300">{formatBRL(d.valor)}</td>
                            <td className="px-4 py-2 text-center whitespace-nowrap">
                              {d.status_anterior && (
                                <span className="text-xs text-zinc-600 mr-1.5 line-through">
                                  {STATUS_LABEL[d.status_anterior] ?? d.status_anterior}
                                </span>
                              )}
                              {/* o selo tinha o texto fixo em Pago/Agendado, então um
                                  salário já creditado aparecia como "Agendado" na aba
                                  de entradas. STATUS_LABEL já tem o vocabulário certo
                                  dos dois lados. */}
                              <span className={cn('text-xs px-2 py-0.5 rounded',
                                STATUS_CONFIRMADO.has(d.status)
                                  ? 'text-emerald-400 bg-emerald-950/40'
                                  : 'text-blue-400 bg-blue-950/40')}>
                                {STATUS_LABEL[d.status] ?? d.status}
                              </span>
                            </td>
                            <td className="px-4 py-2 text-right whitespace-nowrap">
                              {confirmado === null && ehEstorno(d.item_id) && (
                                <button onClick={() => abrirDefinicaoEstorno(d)}
                                  className={cn('text-xs mr-3 transition-colors',
                                    (d.estorna_transacao_id || d.estorna_despesa_id)
                                      ? 'text-zinc-500 hover:text-emerald-400'
                                      : 'text-amber-400 font-medium hover:text-amber-300')}>
                                  {(d.estorna_transacao_id || d.estorna_despesa_id) ? 'trocar estornado' : 'definir o que estorna'}
                                </button>
                              )}
                              {confirmado === null && (
                                <button onClick={() => abrirCorrecao(d.transacao_id)}
                                  className="text-xs text-zinc-500 hover:text-amber-400 transition-colors">
                                  {t.trocar}
                                </button>
                              )}
                            </td>
                          </tr>
                          {definindoEstorno === d.transacao_id && (
                            <tr className="bg-zinc-900/60 border-t border-zinc-800/40">
                              <td colSpan={5} className="px-4 py-3">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <span className="text-xs text-zinc-500">O que este crédito estorna?</span>
                                  {/* dois caminhos para o mesmo vínculo: pela despesa,
                                      quando já se sabe qual é, ou pela linha do extrato,
                                      quando ela ainda não foi classificada. O que fica
                                      gravado é sempre o vínculo com a despesa. */}
                                  <div className="flex rounded border border-zinc-700 overflow-hidden text-xs">
                                    {(['despesa', 'lancamento'] as const).map(a => (
                                      <button key={a} onClick={() => { setAlvoEstorno(a); setEstornaSec1('') }}
                                        className={cn('px-2 py-1 transition-colors',
                                          alvoEstorno === a ? 'bg-emerald-600 text-white' : 'text-zinc-400 hover:text-zinc-200')}>
                                        {a === 'despesa' ? 'Despesa' : 'Lançamento'}
                                      </button>
                                    ))}
                                  </div>
                                  <DespesaPicker
                                    despesas={alvoEstorno === 'despesa' ? opcoesDespesaEstorno : opcoesDebito}
                                    value={estornaSec1} onChange={setEstornaSec1}
                                    placeholder={alvoEstorno === 'despesa'
                                      ? 'Qual despesa foi estornada?' : 'Qual lançamento do extrato foi estornado?'}
                                    vazio={alvoEstorno === 'despesa' ? 'Nenhuma despesa ativa' : 'Nenhum débito no período'}
                                    className="w-80" />

                                  <button onClick={() => aplicarDefinicaoEstorno(d)} disabled={!estornaSec1}
                                    className="px-3 py-1 rounded bg-emerald-600 text-white text-xs font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
                                    Confirmar
                                  </button>
                                </div>
                              </td>
                            </tr>
                          )}
                          {corrigindo === d.transacao_id && (
                            <tr className="bg-zinc-900/60 border-t border-zinc-800/40">
                              <td colSpan={5} className="px-4 py-3">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <DespesaPicker despesas={catalogo} value={selecionada} onChange={setSelecionada}
                                    placeholder={`Digite pra buscar a ${t.item} certa...`} allowNova
                                    novaLabel={`+ ${t.novoItem}`}
                                    vazio={`Nenhuma ${t.item} encontrada`}
                                    onSelectNova={q => { setSelecionada('nova'); setNovaDespesaNome(q) }}
                                    className="w-72" />
                                  {selecionada === 'nova' && (
                                    <input value={novaDespesaNome} onChange={e => setNovaDespesaNome(e.target.value)}
                                      placeholder={`Nome da nova ${t.item}`} autoFocus
                                      className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500" />
                                  )}
                                  <button onClick={() => aplicarCorrecao(d)}
                                    disabled={!selecionada || (selecionada === 'nova' && !novaDespesaNome.trim())}
                                    className="px-3 py-1 rounded bg-emerald-600 text-white text-xs font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
                                    Confirmar
                                  </button>
                                </div>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* ---- 2. despesas ativas que não foram encontradas no extrato ----
                 O combo aqui lista as TRANSAÇÕES sobrando: parte-se da despesa
                 e procura-se o débito. É o inverso exato da seção 3. */}
            {lado.nao_encontrados.length > 0 && (
              <div>
                <Secao n={2} titulo={t.sec2} qtd={lado.nao_encontrados.length}
                  ajuda="Se a transação existe e eu não achei, escolha a linha correspondente do extrato." />
                <div className="rounded-lg overflow-hidden border border-zinc-800/60">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-zinc-800 bg-zinc-900/40">
                        <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">{t.itens.slice(0, -1)}</th>
                        <th className="px-4 py-2 text-right text-xs text-zinc-500 font-medium">Previsto</th>
                        <th className="px-4 py-2"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {lado.nao_encontrados.map((n, i) => (
                        <Fragment key={n.item_id}>
                          <tr className={cn('hover:bg-zinc-800/40', i > 0 && 'border-t border-zinc-800/40')}>
                            <td className="px-4 py-2 text-zinc-200 font-medium">{n.item_nome}</td>
                            <td className="px-4 py-2 text-right tabular-nums text-zinc-500">{formatBRL(n.valor_esperado)}</td>
                            <td className="px-4 py-2 text-right">
                              {confirmado === null && (
                                <button onClick={() => abrirBuscaTx(n.item_id)}
                                  className="text-xs text-zinc-500 hover:text-emerald-400 transition-colors whitespace-nowrap">
                                  {t.associarTx}
                                </button>
                              )}
                            </td>
                          </tr>
                          {buscandoTx === n.item_id && (
                            <tr className="bg-zinc-900/60 border-t border-zinc-800/40">
                              <td colSpan={3} className="px-4 py-3">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <DespesaPicker despesas={opcoesTransacao} value={txSelecionada} onChange={setTxSelecionada}
                                    placeholder="Digite pra buscar no extrato..." vazio="Nenhuma transação sobrando"
                                    className="w-96" />
                                  <button onClick={() => aplicarBuscaTx(n)} disabled={!txSelecionada}
                                    className="px-3 py-1 rounded bg-emerald-600 text-white text-xs font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
                                    Confirmar
                                  </button>
                                </div>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* ---- 3. transações do extrato sem despesa ----
                 O combo lista só as despesas da seção 2 (ativas e ainda não
                 associadas neste mês) — oferecer o catálogo inteiro deixava
                 escolher uma despesa que já casou com outra transação. */}
            {lado.transacoes_sobrando.length > 0 && (
              <div>
                <Secao n={3} titulo={t.sec3} qtd={lado.transacoes_sobrando.length} ajuda={t.ajuda3} />
                <div className="rounded-lg overflow-hidden border border-zinc-800/60">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-zinc-800 bg-zinc-900/40">
                        <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Data</th>
                        <th className="px-4 py-2 text-left text-xs text-zinc-500 font-medium">Descrição no extrato</th>
                        <th className="px-4 py-2 text-right text-xs text-zinc-500 font-medium">Valor</th>
                        <th className="px-4 py-2"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {lado.transacoes_sobrando.map((tx, i) => (
                        <Fragment key={tx.id}>
                          <tr className={cn('hover:bg-zinc-800/40', i > 0 && 'border-t border-zinc-800/40')}>
                            <td className="px-4 py-2 text-zinc-500 text-xs tabular-nums">
                              {new Date(tx.data + 'T00:00:00').toLocaleDateString('pt-BR')}
                            </td>
                            <td className="px-4 py-2 text-zinc-300">
                              {tx.descricao}
                              {tx.estorno_sugerido && confirmado === null && (
                                itemEstorno ? (
                                  // um clique faz tudo: escolhe o item de estorno e já
                                  // aponta o débito anulado. Antes era escolher o item
                                  // no combo e depois procurar o débito — dois passos
                                  // que ninguém adivinha que existem.
                                  <button
                                    onClick={() => associarPar(tx, itemEstorno.id, itemEstorno.nome,
                                      { estorna_transacao_id: tx.estorno_sugerido!.transacao_id })}
                                    className="ml-2 text-[11px] text-amber-400 hover:text-amber-300 underline decoration-dotted transition-colors"
                                    title={`Marca este crédito como estorno de "${tx.estorno_sugerido.descricao}" de ${new Date(tx.estorno_sugerido.data + 'T00:00:00').toLocaleDateString('pt-BR')}, mesmo valor`}>
                                    é estorno de "{tx.estorno_sugerido.descricao}"?
                                  </button>
                                ) : (
                                  <span className="ml-2 text-[11px] text-amber-500/80"
                                    title={`Mesmo valor de "${tx.estorno_sugerido.descricao}", poucos dias depois. Para registrar estorno, crie no Catálogo uma receita com Tipo = Estorno.`}>
                                    parece estornar "{tx.estorno_sugerido.descricao}" — falta uma receita com Tipo = Estorno
                                  </span>
                                )
                              )}
                            </td>
                            <td className="px-4 py-2 text-right tabular-nums text-zinc-300">{formatBRL(Math.abs(tx.valor))}</td>
                            <td className="px-4 py-2 text-right">
                              {confirmado === null && (
                                <button onClick={() => abrirAssociacao(tx)}
                                  className="text-xs text-zinc-500 hover:text-emerald-400 transition-colors whitespace-nowrap">
                                  {t.associarItem}
                                </button>
                              )}
                            </td>
                          </tr>
                          {associando === tx.id && (
                            <tr className="bg-zinc-900/60 border-t border-zinc-800/40">
                              <td colSpan={4} className="px-4 py-3">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <DespesaPicker despesas={opcoesDespesa} value={selecionadaAssoc} onChange={setSelecionadaAssoc}
                                    placeholder={`Digite pra buscar a ${t.item}...`} allowNova
                                    novaLabel={`+ ${t.novoItem}`}
                                    vazio={`Nenhuma ${t.item} em aberto neste mês`}
                                    onSelectNova={q => { setSelecionadaAssoc('nova'); setNovaDespesaNomeAssoc(q) }}
                                    className="w-72" />
                                  {selecionadaAssoc === 'nova' && (
                                    <input value={novaDespesaNomeAssoc} onChange={e => setNovaDespesaNomeAssoc(e.target.value)}
                                      placeholder={`Nome da nova ${t.item}`} autoFocus
                                      className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500" />
                                  )}
                                  {tipoDoItem(selecionadaAssoc) === 'resgate_esporadico' && (
                                    <input value={objetivoVal} onChange={e => setObjetivoVal(e.target.value)}
                                      placeholder="Para quê? ex: compra do carro" autoFocus
                                      className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500 w-56" />
                                  )}
                                  {tipoDoItem(selecionadaAssoc) === 'estorno' && (
                                    <>
                                  {/* dois caminhos para o mesmo vínculo: pela despesa,
                                      quando já se sabe qual é, ou pela linha do extrato,
                                      quando ela ainda não foi classificada. O que fica
                                      gravado é sempre o vínculo com a despesa. */}
                                  <div className="flex rounded border border-zinc-700 overflow-hidden text-xs">
                                    {(['despesa', 'lancamento'] as const).map(a => (
                                      <button key={a} onClick={() => { setAlvoEstorno(a); setEstornaVal('') }}
                                        className={cn('px-2 py-1 transition-colors',
                                          alvoEstorno === a ? 'bg-emerald-600 text-white' : 'text-zinc-400 hover:text-zinc-200')}>
                                        {a === 'despesa' ? 'Despesa' : 'Lançamento'}
                                      </button>
                                    ))}
                                  </div>
                                  <DespesaPicker
                                    despesas={alvoEstorno === 'despesa' ? opcoesDespesaEstorno : opcoesDebito}
                                    value={estornaVal} onChange={setEstornaVal}
                                    placeholder={alvoEstorno === 'despesa'
                                      ? 'Qual despesa foi estornada?' : 'Qual lançamento do extrato foi estornado?'}
                                    vazio={alvoEstorno === 'despesa' ? 'Nenhuma despesa ativa' : 'Nenhum débito no período'}
                                    className="w-80" />
                                    </>
                                  )}
                                  <button onClick={() => aplicarAssociacao(tx)}
                                    disabled={!selecionadaAssoc || (selecionadaAssoc === 'nova' && !novaDespesaNomeAssoc.trim())
                                      || (tipoDoItem(selecionadaAssoc) === 'estorno' && !estornaVal)}
                                    title={tipoDoItem(selecionadaAssoc) === 'estorno' && !estornaVal
                                      ? 'Escolha a despesa ou o lançamento estornado' : undefined}
                                    className="px-3 py-1 rounded bg-emerald-600 text-white text-xs font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
                                    Confirmar
                                  </button>
                                </div>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {confirmado === null ? (
              <div className="flex items-center gap-3 pt-2">
                <button onClick={confirmarTudo}
                  disabled={confirmando || totalPendente === 0 || estornosIncompletos.length > 0}
                  className="px-5 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
                  {confirmando ? 'Gravando...' : `Confirmar tudo (${totalPendente})`}
                </button>
                {erroConfirmar ? (
                  <p className="text-sm text-red-400">{erroConfirmar}</p>
                ) : estornosIncompletos.length > 0 ? (
                  <p className="text-sm text-amber-400">
                    {estornosIncompletos.length === 1 ? 'Falta dizer' : `Faltam ${estornosIncompletos.length} estornos: diga`}
                    {' '}qual débito o estorno anula (aba Entradas, seção 1).
                  </p>
                ) : (
                  <p className="text-sm text-zinc-500">Nada é gravado até você clicar aqui — pode sair e voltar sem perder o que já revisou.</p>
                )}
              </div>
            ) : (
              <>
                <p className="text-sm text-zinc-500">
                  Corrigir um vínculo costuma liberar transação para outra despesa — vale rodar de novo.
                  Depois, vá para <strong className="text-zinc-300">Mês Atual</strong> para revisar o que ficou em aberto.
                </p>
                <div className="flex items-center gap-3">
                  <button onClick={rodarBatimento} disabled={loading}
                    className="px-4 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
                    {loading ? 'Batendo...' : 'Rebater de novo'}
                  </button>
                  <button onClick={() => { setStep('selecionar'); setFile(null); setMsg(''); setTransacoes([]); setResultado(null); setConfirmado(null) }}
                    className="px-4 py-2 rounded-md border border-zinc-700 text-sm text-zinc-300 hover:border-zinc-500 transition-colors">
                    Nova importação
                  </button>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
