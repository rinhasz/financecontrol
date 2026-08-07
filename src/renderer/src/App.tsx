import { useState } from 'react'
import { MesAtual } from './pages/MesAtual'
import { Catalogo } from './pages/Catalogo'
import { Importacao } from './pages/Importacao'
import { EmailBusca } from './pages/EmailBusca'
import { cn } from './lib/utils'

type Page = 'mes' | 'catalogo' | 'importacao' | 'email'

const NAV = [
  { id: 'mes' as Page, label: 'Mês Atual', icon: CalendarIcon },
  { id: 'catalogo' as Page, label: 'Catálogo', icon: ListIcon },
  { id: 'importacao' as Page, label: 'Importar Extrato', icon: UploadIcon },
  { id: 'email' as Page, label: 'Procurar em Emails', icon: MailIcon }
]

export default function App(): JSX.Element {
  const [page, setPage] = useState<Page>('mes')

  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-100 overflow-hidden">
      {/* Sidebar */}
      <aside className="w-52 flex-none flex flex-col bg-zinc-900 border-r border-zinc-800">
        {/* Titlebar area */}
        <div className="h-8 titlebar-drag flex items-center px-4">
          <span className="text-xs text-zinc-600 font-medium tracking-widest uppercase select-none">
            FinanceControl
          </span>
        </div>

        <div className="flex-1 px-2 py-3 space-y-0.5">
          {NAV.map((item) => {
            const Icon = item.icon
            return (
              <button
                key={item.id}
                onClick={() => setPage(item.id)}
                className={cn(
                  'w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors',
                  page === item.id
                    ? 'bg-zinc-800 text-zinc-100 font-medium'
                    : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60'
                )}
              >
                <Icon size={16} />
                {item.label}
              </button>
            )
          })}
        </div>

        <div className="px-4 py-3 border-t border-zinc-800">
          <p className="text-xs text-zinc-600">v1.0.0</p>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-hidden flex flex-col">
        {/* Titlebar no-drag zone (right side) */}
        <div className="h-8 titlebar-drag flex-none" />

        <div className="flex-1 overflow-auto -mt-8">
          {/* As três telas ficam sempre montadas — só a visibilidade muda — para
              não perder o estado (ex: importação em andamento) ao trocar de aba */}
          <div className={cn('h-full', page !== 'mes' && 'hidden')}><MesAtual /></div>
          <div className={cn('h-full', page !== 'catalogo' && 'hidden')}><Catalogo /></div>
          <div className={cn('h-full', page !== 'importacao' && 'hidden')}><Importacao active={page === 'importacao'} /></div>
          <div className={cn('h-full', page !== 'email' && 'hidden')}><EmailBusca active={page === 'email'} /></div>
        </div>
      </main>
    </div>
  )
}

function CalendarIcon({ size = 16 }: { size?: number }): JSX.Element {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
      <line x1="16" y1="2" x2="16" y2="6" />
      <line x1="8" y1="2" x2="8" y2="6" />
      <line x1="3" y1="10" x2="21" y2="10" />
    </svg>
  )
}

function ListIcon({ size = 16 }: { size?: number }): JSX.Element {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="8" y1="6" x2="21" y2="6" />
      <line x1="8" y1="12" x2="21" y2="12" />
      <line x1="8" y1="18" x2="21" y2="18" />
      <line x1="3" y1="6" x2="3.01" y2="6" />
      <line x1="3" y1="12" x2="3.01" y2="12" />
      <line x1="3" y1="18" x2="3.01" y2="18" />
    </svg>
  )
}

function UploadIcon({ size = 16 }: { size?: number }): JSX.Element {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" y1="3" x2="12" y2="15" />
    </svg>
  )
}

function MailIcon({ size = 16 }: { size?: number }): JSX.Element {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="4" width="20" height="16" rx="2" />
      <path d="m22 7-10 5L2 7" />
    </svg>
  )
}
