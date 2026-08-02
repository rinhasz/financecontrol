import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { cn, currentMesRef } from '../lib/utils'

interface Boleto {
  id: string
  assunto: string
  remetente: string
  data: string | null
  valor_encontrado: string | null
  linha_digitavel: string | null
  despesa_sugerida_id: number | null
  despesa_sugerida_nome: string | null
}

interface Despesa { id: number; nome: string }

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

export function EmailBusca() {
  const [status, setStatus] = useState<{ configurado: boolean; conectado: string | null } | null>(null)
  const [dataIni, setDataIni] = useState(() => {
    const d = new Date()
    d.setDate(d.getDate() - 60)
    return isoDate(d)
  })
  const [dataFim, setDataFim] = useState(() => isoDate(new Date()))
  const [buscando, setBuscando] = useState(false)
  const [boletos, setBoletos] = useState<Boleto[] | null>(null)
  const [pesquisadas, setPesquisadas] = useState(0)
  const [erro, setErro] = useState('')
  const [copiado, setCopiado] = useState<string | null>(null)
  const [despesas, setDespesas] = useState<Despesa[]>([])

  const [associando, setAssociando] = useState<string | null>(null)
  const [despesaEscolhida, setDespesaEscolhida] = useState('')
  const [mesEscolhido, setMesEscolhido] = useState(currentMesRef())
  const [associados, setAssociados] = useState<Set<string>>(new Set())

  const [codigoDispositivo, setCodigoDispositivo] = useState<{ verification_uri: string; user_code: string } | null>(null)
  const [aguardandoLogin, setAguardandoLogin] = useState(false)
  const [erroConexao, setErroConexao] = useState('')

  function carregarStatus() {
    api.email.status().then(setStatus)
  }

  useEffect(() => { carregarStatus() }, [])

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

  async function buscar() {
    setBuscando(true)
    setErro('')
    setBoletos(null)
    setAssociados(new Set())
    try {
      const [res, cat] = await Promise.all([api.email.buscar(dataIni, dataFim), api.catalogo.list()])
      if (res.ok) {
        setBoletos(res.boletos)
        setPesquisadas(res.despesas_pesquisadas)
        setDespesas(cat)
      } else {
        setErro(res.msg || 'Erro ao buscar emails')
      }
    } catch (e) {
      setErro(String(e))
    } finally {
      setBuscando(false)
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
    setMesEscolhido(currentMesRef())
  }

  async function aplicarAssociacao(b: Boleto) {
    if (!despesaEscolhida) return
    await api.email.associar(Number(despesaEscolhida), mesEscolhido, b.linha_digitavel, b.valor_encontrado, b.remetente)
    setAssociados(s => new Set(s).add(b.id))
    setAssociando(null)
  }

  return (
    <div className="flex flex-col h-full pt-3">
      <div className="px-6 pb-4 flex-none">
        <h1 className="text-xl font-semibold text-zinc-100">Procurar em Emails</h1>
        <p className="text-sm text-zinc-500 mt-0.5">
          Busca boletos e faturas (cartão, seguro saúde, etc.) no período escolhido — associe o que encontrar a
          uma despesa e mês, e a linha digitável fica disponível pra copiar em Mês Atual. Cada associação também
          vira uma regra: da próxima vez, o mesmo remetente já vem pré-preenchido, só pra você conferir e
          confirmar. Não cria nem altera nenhum lançamento sozinho.
        </p>
        <p className="text-xs text-amber-500/80 mt-1">
          A extração de valor/linha digitável é automática (por IA) — confira sempre contra o email
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
            <button onClick={buscar} disabled={buscando}
              className="px-5 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
              {buscando ? 'Buscando...' : 'Buscar'}
            </button>
            <p className="text-xs text-zinc-600 pb-2">conectado como {status.conectado}</p>
          </div>
        )}

        {erro && <p className="text-sm text-red-400">{erro}</p>}

        {boletos && (
          <div className="space-y-3">
            <p className="text-sm text-zinc-500">
              {pesquisadas} despesas no catálogo — {boletos.length} boletos/faturas encontrados no período.
            </p>

            {boletos.length === 0 && (
              <p className="text-sm text-zinc-600">Nada encontrado nesse período — tente um intervalo maior.</p>
            )}

            <div className="rounded-lg overflow-hidden border border-zinc-800/60 divide-y divide-zinc-800/40">
              {boletos.map(b => (
                <div key={b.id} className="px-4 py-3 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-zinc-300 truncate">{b.assunto}</p>
                      <p className="text-zinc-600 text-xs truncate">
                        {b.remetente} — {b.data ? new Date(b.data).toLocaleDateString('pt-BR') : ''}
                        {b.despesa_sugerida_nome && <> — sugestão: <span className="text-zinc-400">{b.despesa_sugerida_nome}</span></>}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 flex-none">
                      {b.valor_encontrado && (
                        <span className="text-emerald-400 font-medium tabular-nums whitespace-nowrap">R$ {b.valor_encontrado}</span>
                      )}
                      {associados.has(b.id) ? (
                        <span className="text-xs text-emerald-400 whitespace-nowrap">Associado ✓</span>
                      ) : (
                        <button onClick={() => abrirAssociacao(b)}
                          className="text-xs px-2 py-1 rounded border border-zinc-700 text-zinc-400 hover:border-emerald-600 hover:text-emerald-400 transition-colors whitespace-nowrap">
                          Associar
                        </button>
                      )}
                    </div>
                  </div>

                  {b.linha_digitavel ? (
                    <div className="mt-2 flex items-center gap-2">
                      <code className="text-xs text-zinc-400 bg-zinc-900/60 px-2 py-1 rounded flex-1 truncate">
                        {b.linha_digitavel}
                      </code>
                      <button onClick={() => copiar(b.linha_digitavel!)}
                        className={cn('text-xs px-2 py-1 rounded border transition-colors whitespace-nowrap',
                          copiado === b.linha_digitavel
                            ? 'border-emerald-700 text-emerald-400'
                            : 'border-zinc-700 text-zinc-400 hover:border-zinc-500')}>
                        {copiado === b.linha_digitavel ? 'Copiado!' : 'Copiar linha digitável'}
                      </button>
                    </div>
                  ) : (
                    <p className="mt-1 text-xs text-zinc-700">Linha digitável não encontrada — abra o email manualmente.</p>
                  )}

                  {associando === b.id && (
                    <div className="mt-3 flex items-center gap-2 flex-wrap bg-zinc-900/60 rounded p-3">
                      <select value={despesaEscolhida} onChange={e => setDespesaEscolhida(e.target.value)}
                        className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500">
                        <option value="">Selecionar despesa...</option>
                        {despesas.map(ds => <option key={ds.id} value={ds.id}>{ds.nome}</option>)}
                      </select>
                      <input type="month" value={mesEscolhido} onChange={e => setMesEscolhido(e.target.value)}
                        className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500" />
                      <button onClick={() => aplicarAssociacao(b)} disabled={!despesaEscolhida}
                        className="px-3 py-1 rounded bg-emerald-600 text-white text-xs font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
                        Confirmar
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
