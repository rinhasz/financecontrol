import { Component, ReactNode } from 'react'

interface Props { nome: string; children: ReactNode }
interface State { erro: Error | null }

/**
 * Sem isto, qualquer erro de render derruba a árvore inteira do React e o
 * app vira uma tela preta, sem nenhuma pista do que aconteceu — foi
 * exatamente o que ocorreu quando a tela de email tentou ler um campo de
 * um status que ainda não tinha chegado. Aqui o estrago fica contido na
 * aba com problema, com a mensagem do erro à vista.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { erro: null }

  static getDerivedStateFromError(erro: Error): State {
    return { erro }
  }

  componentDidCatch(erro: Error) {
    console.error('[ErrorBoundary]', this.props.nome, erro)
  }

  render() {
    if (!this.state.erro) return this.props.children
    return (
      <div className="p-6">
        <div className="rounded-lg border border-red-900/50 bg-red-950/20 p-4 max-w-xl">
          <p className="text-sm text-red-300 mb-1">Algo quebrou nesta tela ({this.props.nome}).</p>
          <p className="text-xs text-zinc-500 mb-3 font-mono break-words">{this.state.erro.message}</p>
          <button onClick={() => this.setState({ erro: null })}
            className="px-3 py-1.5 rounded-md border border-zinc-700 text-zinc-300 text-sm hover:border-emerald-600 hover:text-emerald-400 transition-colors">
            Tentar de novo
          </button>
        </div>
      </div>
    )
  }
}
