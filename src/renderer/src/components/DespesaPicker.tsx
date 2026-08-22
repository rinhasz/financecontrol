import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '../lib/utils'

interface Despesa { id: number; nome: string }

interface Props {
  despesas: Despesa[]
  value: string
  onChange: (id: string) => void
  placeholder?: string
  allowNova?: boolean
  onSelectNova?: (query: string) => void
  // Texto do estado vazio. A lista nem sempre é de despesas: na tela de
  // importação o mesmo seletor lista transações do extrato (o inverso da
  // associação), e "Nenhuma despesa encontrada" ali estaria errado.
  vazio?: string
  className?: string
}

/** Reduz o texto ao que importa para busca: sem acento, sem caixa, com
 *  pontuação virando espaço. É o que faz "educacao" achar "EDUCAÇÃO" e
 *  "america 10/08" achar "PIX QRS SUL AMERICA10/08" — descrição de extrato
 *  vem grudada em data e número de documento. */
function normalizar(s: string) {
  return s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
}

/** Além do texto normalizado, uma versão só com letras e dígitos, sem
 *  separador nenhum: deixa "1550 8" achar "R$ 155,08" e "038397" achar
 *  "FINANC IMOBILIARIO 038/397". */
function alvos(s: string) {
  const n = normalizar(s)
  return [n.replace(/[^a-z0-9]+/g, ' '), n.replace(/[^a-z0-9]+/g, '')]
}

const MIN_LARGURA = 460
const MAX_ALTURA = 420

export function DespesaPicker({ despesas, value, onChange, placeholder = 'Buscar despesa...', allowNova, onSelectNova, vazio = 'Nenhuma despesa encontrada', className }: Props) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [cursor, setCursor] = useState(0)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listaRef = useRef<HTMLDivElement>(null)

  const selecionada = despesas.find(d => String(d.id) === value)

  const termos = normalizar(query).split(/[^a-z0-9]+/).filter(Boolean)
  const filtradas = termos.length === 0
    ? despesas
    : despesas.filter(d => {
        const [texto, compacto] = alvos(d.nome)
        return termos.every(t => texto.includes(t) || compacto.includes(t))
      })

  // A lista é renderizada em portal com position:fixed porque o seletor vive
  // dentro de tabelas com `overflow-hidden` — ancorada no fluxo normal, ela
  // era cortada na borda da tabela e ficava impossível de rolar.
  useLayoutEffect(() => {
    if (!open) return
    const medir = () => inputRef.current && setRect(inputRef.current.getBoundingClientRect())
    medir()
    // captura: o scroll que importa é o do container interno, não o da janela
    window.addEventListener('scroll', medir, true)
    window.addEventListener('resize', medir)
    return () => {
      window.removeEventListener('scroll', medir, true)
      window.removeEventListener('resize', medir)
    }
  }, [open])

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      const alvo = e.target as Node
      if (inputRef.current?.contains(alvo) || listaRef.current?.contains(alvo)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  useEffect(() => { setCursor(0) }, [query, open])

  function selecionar(id: number) {
    onChange(String(id))
    setQuery('')
    setOpen(false)
  }

  function escolherNova() {
    setOpen(false)
    onSelectNova?.(query)
    setQuery('')
  }

  const totalOpcoes = filtradas.length + (allowNova ? 1 : 0)

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Escape') { setOpen(false); return }
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter')) { setOpen(true); return }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (!totalOpcoes) return
      const passo = e.key === 'ArrowDown' ? 1 : -1
      setCursor(c => (c + passo + totalOpcoes) % totalOpcoes)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (cursor < filtradas.length) selecionar(filtradas[cursor].id)
      else if (allowNova) escolherNova()
    }
  }

  // rola o item destacado para dentro da caixa quando se navega pelo teclado
  useEffect(() => {
    listaRef.current?.querySelector('[data-cursor="1"]')?.scrollIntoView({ block: 'nearest' })
  }, [cursor])

  function painel() {
    if (!rect) return null
    const largura = Math.max(rect.width, MIN_LARGURA)
    const abaixo = window.innerHeight - rect.bottom - 12
    const acima = rect.top - 12
    // abre para cima quando a linha está perto do rodapé, senão a lista nasce
    // com 40px de altura útil e não dá para rolar
    const paraCima = abaixo < 200 && acima > abaixo
    const altura = Math.min(MAX_ALTURA, Math.max(160, paraCima ? acima : abaixo))

    return createPortal(
      <div ref={listaRef}
        style={{
          position: 'fixed',
          left: Math.max(8, Math.min(rect.left, window.innerWidth - largura - 8)),
          top: paraCima ? undefined : rect.bottom + 4,
          bottom: paraCima ? window.innerHeight - rect.top + 4 : undefined,
          width: largura,
          maxHeight: altura
        }}
        className="z-50 overflow-y-auto overscroll-contain rounded-md border border-zinc-700 bg-zinc-900 shadow-xl">
        {filtradas.length === 0 && (
          <div className="px-3 py-2 text-xs text-zinc-600">{vazio}</div>
        )}
        {filtradas.map((d, i) => (
          <button key={d.id} type="button" data-cursor={i === cursor ? '1' : undefined}
            onClick={() => selecionar(d.id)} onMouseEnter={() => setCursor(i)}
            className={cn('block w-full text-left px-3 py-1.5 text-sm transition-colors',
              i === cursor && 'bg-zinc-800',
              String(d.id) === value ? 'text-emerald-400' : 'text-zinc-200')}>
            {d.nome}
          </button>
        ))}
        {allowNova && (
          <button type="button" data-cursor={cursor === filtradas.length ? '1' : undefined}
            onClick={escolherNova} onMouseEnter={() => setCursor(filtradas.length)}
            className={cn('block w-full text-left px-3 py-1.5 text-sm text-zinc-400 border-t border-zinc-800 transition-colors',
              cursor === filtradas.length && 'bg-zinc-800')}>
            + Nova despesa{query ? `: "${query}"` : ''}
          </button>
        )}
      </div>,
      document.body
    )
  }

  return (
    <div className={cn('relative', className)}>
      <input ref={inputRef}
        value={open ? query : (selecionada?.nome ?? '')}
        onChange={e => { setQuery(e.target.value); setOpen(true) }}
        onFocus={() => { setQuery(''); setOpen(true) }}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500 w-full"
      />
      {open && painel()}
    </div>
  )
}
