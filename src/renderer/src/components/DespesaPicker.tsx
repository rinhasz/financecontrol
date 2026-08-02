import { useEffect, useRef, useState } from 'react'
import { cn } from '../lib/utils'

interface Despesa { id: number; nome: string }

interface Props {
  despesas: Despesa[]
  value: string
  onChange: (id: string) => void
  placeholder?: string
  allowNova?: boolean
  onSelectNova?: (query: string) => void
  className?: string
}

export function DespesaPicker({ despesas, value, onChange, placeholder = 'Buscar despesa...', allowNova, onSelectNova, className }: Props) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  const selecionada = despesas.find(d => String(d.id) === value)

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  const termos = query.toLowerCase().trim().split(/\s+/).filter(Boolean)
  const filtradas = termos.length === 0
    ? despesas
    : despesas.filter(d => {
        const nome = d.nome.toLowerCase()
        return termos.every(t => nome.includes(t))
      })

  function selecionar(id: number) {
    onChange(String(id))
    setQuery('')
    setOpen(false)
  }

  return (
    <div ref={ref} className={cn('relative', className)}>
      <input
        value={open ? query : (selecionada?.nome ?? '')}
        onChange={e => { setQuery(e.target.value); setOpen(true) }}
        onFocus={() => { setQuery(''); setOpen(true) }}
        placeholder={placeholder}
        className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 outline-none focus:border-emerald-500 w-full"
      />
      {open && (
        <div className="absolute z-10 mt-1 w-64 max-h-56 overflow-auto rounded-md border border-zinc-700 bg-zinc-900 shadow-lg">
          {filtradas.length === 0 && (
            <div className="px-3 py-2 text-xs text-zinc-600">Nenhuma despesa encontrada</div>
          )}
          {filtradas.map(d => (
            <button key={d.id} type="button" onClick={() => selecionar(d.id)}
              className={cn('block w-full text-left px-3 py-1.5 text-sm hover:bg-zinc-800 transition-colors',
                String(d.id) === value ? 'text-emerald-400' : 'text-zinc-200')}>
              {d.nome}
            </button>
          ))}
          {allowNova && (
            <button type="button" onClick={() => { setOpen(false); onSelectNova?.(query); setQuery('') }}
              className="block w-full text-left px-3 py-1.5 text-sm text-zinc-400 hover:bg-zinc-800 border-t border-zinc-800 transition-colors">
              + Nova despesa{query ? `: "${query}"` : ''}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
