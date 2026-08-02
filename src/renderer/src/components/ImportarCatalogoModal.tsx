import { useState, type ReactNode } from 'react'
import { api } from '../lib/api'
import { cn, formatBRL } from '../lib/utils'

type Passo = 'selecionar' | 'mapear' | 'revisar' | 'concluido'

interface ItemNovo { nome: string; categoria: string | null; valor: number | null }
interface ItemAtualizado {
  despesa_id: number; nome: string
  categoria_nome_antiga: string | null; categoria_nome_nova: string | null
  valor_antigo: number | null; valor_novo: number | null
  reativada: boolean
}
interface ItemDesativado { despesa_id: number; nome: string }

interface Plano {
  novas: ItemNovo[]
  atualizadas: ItemAtualizado[]
  desativadas: ItemDesativado[]
  categorias_novas: string[]
  colunas_usadas: { categoria: boolean; valor: boolean }
}

interface Props {
  onClose: () => void
  onConcluido: () => void
}

export function ImportarCatalogoModal({ onClose, onConcluido }: Props) {
  const [passo, setPasso] = useState<Passo>('selecionar')
  const [arquivo, setArquivo] = useState<File | null>(null)
  const [carregando, setCarregando] = useState(false)
  const [erro, setErro] = useState('')

  const [amostra, setAmostra] = useState<{ colunas: number; linhas: (string | number)[][]; total_linhas: number } | null>(null)
  const [colNome, setColNome] = useState('')
  const [colCategoria, setColCategoria] = useState('')
  const [colValor, setColValor] = useState('')
  const [temCabecalho, setTemCabecalho] = useState(true)

  const [plano, setPlano] = useState<Plano | null>(null)
  const [resultado, setResultado] = useState<{ criadas: number; atualizadas: number; desativadas: number } | null>(null)

  async function selecionarArquivo(f: File) {
    setArquivo(f)
    setErro('')
    setCarregando(true)
    try {
      const res = await api.catalogo.importarAmostra(f)
      if (!res.ok) { setErro(res.msg || 'Erro ao ler arquivo'); return }
      setAmostra(res)
      setPasso('mapear')
    } catch (e) {
      setErro(String(e))
    } finally {
      setCarregando(false)
    }
  }

  async function analisar() {
    if (!arquivo || colNome === '') return
    setErro('')
    setCarregando(true)
    try {
      const res = await api.catalogo.importarAnalisar(
        arquivo, Number(colNome),
        colCategoria !== '' ? Number(colCategoria) : null,
        colValor !== '' ? Number(colValor) : null,
        temCabecalho
      )
      if (!res.ok) { setErro(res.msg || 'Erro ao analisar'); return }
      setPlano(res)
      setPasso('revisar')
    } catch (e) {
      setErro(String(e))
    } finally {
      setCarregando(false)
    }
  }

  async function confirmar() {
    if (!plano) return
    setErro('')
    setCarregando(true)
    try {
      const res = await api.catalogo.importarConfirmar(plano)
      if (!res.ok) { setErro(res.msg || 'Erro ao confirmar'); return }
      setResultado(res)
      setPasso('concluido')
    } catch (e) {
      setErro(String(e))
    } finally {
      setCarregando(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-6">
      <div className="bg-zinc-900 border border-zinc-700 rounded-lg w-full max-w-3xl max-h-[85vh] flex flex-col">
        <div className="px-5 py-4 border-b border-zinc-800 flex items-center justify-between flex-none">
          <h2 className="text-lg font-semibold text-zinc-100">Importar Catálogo</h2>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300 transition-colors">✕</button>
        </div>

        <div className="flex-1 overflow-auto px-5 py-4 space-y-4">
          {erro && <p className="text-sm text-red-400">{erro}</p>}

          {passo === 'selecionar' && (
            <div className="space-y-3">
              <p className="text-sm text-zinc-400">
                Envie uma planilha Excel com o nome das despesas — e, se quiser, categoria e último valor pago.
                Só o que estiver na planilha fica ativo no catálogo depois (o resto é desativado, não apagado).
              </p>
              <input type="file" accept=".xls,.xlsx,.xlsm"
                onChange={e => { const f = e.target.files?.[0]; if (f) selecionarArquivo(f) }}
                className="text-sm text-zinc-300" />
              {carregando && <p className="text-sm text-zinc-500">Lendo arquivo...</p>}
            </div>
          )}

          {passo === 'mapear' && amostra && (
            <div className="space-y-4">
              <label className="flex items-center gap-2 text-sm text-zinc-400 cursor-pointer">
                <input type="checkbox" checked={temCabecalho} onChange={e => setTemCabecalho(e.target.checked)} className="rounded" />
                A primeira linha é cabeçalho (não é uma despesa)
              </label>

              <div className="rounded-lg overflow-auto border border-zinc-800/60 max-h-64">
                <table className="text-xs">
                  <thead>
                    <tr className="bg-zinc-900/60">
                      {Array.from({ length: amostra.colunas }, (_, i) => (
                        <th key={i} className="px-3 py-1.5 text-left text-zinc-500 font-medium whitespace-nowrap border-b border-zinc-800">
                          Coluna {i + 1}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {amostra.linhas.map((linha, i) => (
                      <tr key={i} className={cn(i === 0 && temCabecalho && 'text-zinc-500 italic', i > 0 && 'border-t border-zinc-800/40')}>
                        {linha.map((c, j) => <td key={j} className="px-3 py-1.5 text-zinc-300 whitespace-nowrap">{String(c)}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-xs text-zinc-600">{amostra.total_linhas} linhas na planilha (mostrando as primeiras {amostra.linhas.length}).</p>

              <div className="flex items-end gap-4 flex-wrap">
                <ColunaSelect label="Nome da despesa" total={amostra.colunas} value={colNome} onChange={setColNome} obrigatorio />
                <ColunaSelect label="Categoria (opcional)" total={amostra.colunas} value={colCategoria} onChange={setColCategoria} />
                <ColunaSelect label="Último valor pago (opcional)" total={amostra.colunas} value={colValor} onChange={setColValor} />
              </div>

              <div className="flex items-center gap-2 pt-2">
                <button onClick={() => setPasso('selecionar')}
                  className="px-4 py-2 rounded-md border border-zinc-700 text-sm text-zinc-300 hover:border-zinc-500 transition-colors">
                  Voltar
                </button>
                <button onClick={analisar} disabled={colNome === '' || carregando}
                  className="px-4 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
                  {carregando ? 'Analisando...' : 'Analisar'}
                </button>
              </div>
            </div>
          )}

          {passo === 'revisar' && plano && (
            <div className="space-y-4">
              <div className="grid grid-cols-3 gap-3">
                <ResumoCard label="Novas despesas" value={plano.novas.length} color="emerald" />
                <ResumoCard label="Atualizadas" value={plano.atualizadas.length} color="blue" />
                <ResumoCard label="Serão desativadas" value={plano.desativadas.length} color="amber" />
              </div>

              {plano.categorias_novas.length > 0 && (
                <p className="text-sm text-zinc-400">
                  Categorias novas a criar: <span className="text-zinc-200">{plano.categorias_novas.join(', ')}</span>
                </p>
              )}

              {plano.novas.length > 0 && (
                <Secao titulo="Novas despesas">
                  {plano.novas.map((n, i) => (
                    <LinhaPlano key={i}>
                      <span className="text-zinc-200">{n.nome}</span>
                      <span className="text-zinc-500 text-xs">
                        {n.categoria ?? '—'} {n.valor != null && `· ${formatBRL(n.valor)}`}
                      </span>
                    </LinhaPlano>
                  ))}
                </Secao>
              )}

              {plano.atualizadas.length > 0 && (
                <Secao titulo="Despesas atualizadas">
                  {plano.atualizadas.map((a, i) => (
                    <LinhaPlano key={i}>
                      <span className="text-zinc-200">{a.nome}</span>
                      <span className="text-zinc-500 text-xs">
                        {a.reativada && <span className="text-emerald-400">reativada </span>}
                        {a.categoria_nome_nova && <>categoria: {a.categoria_nome_antiga ?? '—'} → {a.categoria_nome_nova} </>}
                        {a.valor_novo != null && <>valor: {a.valor_antigo != null ? formatBRL(a.valor_antigo) : '—'} → {formatBRL(a.valor_novo)}</>}
                      </span>
                    </LinhaPlano>
                  ))}
                </Secao>
              )}

              {plano.desativadas.length > 0 && (
                <Secao titulo="Vão ficar inativas (não estão na planilha)">
                  {plano.desativadas.map((d, i) => (
                    <LinhaPlano key={i}><span className="text-red-400/80">{d.nome}</span><span /></LinhaPlano>
                  ))}
                </Secao>
              )}

              {plano.novas.length === 0 && plano.atualizadas.length === 0 && plano.desativadas.length === 0 && (
                <p className="text-sm text-zinc-500">Nada muda — o catálogo já está igual à planilha.</p>
              )}

              <div className="flex items-center gap-2 pt-2">
                <button onClick={() => setPasso('mapear')}
                  className="px-4 py-2 rounded-md border border-zinc-700 text-sm text-zinc-300 hover:border-zinc-500 transition-colors">
                  Voltar
                </button>
                <button onClick={confirmar} disabled={carregando}
                  className="px-4 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium disabled:opacity-40 hover:bg-emerald-500 transition-colors">
                  {carregando ? 'Aplicando...' : 'Confirmar e aplicar'}
                </button>
              </div>
            </div>
          )}

          {passo === 'concluido' && resultado && (
            <div className="space-y-4 text-center py-6">
              <p className="text-4xl font-bold text-emerald-400">✓</p>
              <p className="text-sm text-zinc-300">
                {resultado.criadas} despesas criadas, {resultado.atualizadas} atualizadas, {resultado.desativadas} desativadas.
              </p>
              <button onClick={() => { onConcluido(); onClose() }}
                className="px-4 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium hover:bg-emerald-500 transition-colors">
                Fechar
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function ColunaSelect({ label, total, value, onChange, obrigatorio }: {
  label: string; total: number; value: string; onChange: (v: string) => void; obrigatorio?: boolean
}) {
  return (
    <div>
      <label className="text-xs text-zinc-500 block mb-1">{label}</label>
      <select value={value} onChange={e => onChange(e.target.value)}
        className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-sm text-zinc-200 outline-none focus:border-emerald-500">
        <option value="">{obrigatorio ? 'Selecionar...' : 'Nenhuma'}</option>
        {Array.from({ length: total }, (_, i) => (
          <option key={i} value={i}>Coluna {i + 1}</option>
        ))}
      </select>
    </div>
  )
}

function ResumoCard({ label, value, color }: { label: string; value: number; color: string }) {
  const colors: Record<string, string> = { emerald: 'text-emerald-400', blue: 'text-blue-400', amber: 'text-amber-400' }
  return (
    <div className="rounded-lg border border-zinc-800/60 bg-zinc-900/60 px-4 py-3">
      <p className="text-xs text-zinc-500 mb-1">{label}</p>
      <p className={cn('text-2xl font-bold tabular-nums', colors[color])}>{value}</p>
    </div>
  )
}

function Secao({ titulo, children }: { titulo: string; children: ReactNode }) {
  return (
    <div>
      <p className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-1.5">{titulo}</p>
      <div className="rounded-lg border border-zinc-800/60 divide-y divide-zinc-800/40 max-h-48 overflow-auto">
        {children}
      </div>
    </div>
  )
}

function LinhaPlano({ children }: { children: ReactNode }) {
  return <div className="px-3 py-1.5 flex items-center justify-between gap-3 text-sm">{children}</div>
}
