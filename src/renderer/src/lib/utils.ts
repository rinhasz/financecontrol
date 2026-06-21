import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

export function formatBRL(value: number): string {
  return new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency: 'BRL'
  }).format(value)
}

export function mesRefLabel(mesRef: string): string {
  const [ano, mes] = mesRef.split('-')
  const meses = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
  return `${meses[parseInt(mes) - 1]}/${ano}`
}

export function currentMesRef(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

export function prevMesRef(mesRef: string): string {
  const [ano, mes] = mesRef.split('-').map(Number)
  if (mes === 1) return `${ano - 1}-12`
  return `${ano}-${String(mes - 1).padStart(2, '0')}`
}

export function nextMesRef(mesRef: string): string {
  const [ano, mes] = mesRef.split('-').map(Number)
  if (mes === 12) return `${ano + 1}-01`
  return `${ano}-${String(mes + 1).padStart(2, '0')}`
}
