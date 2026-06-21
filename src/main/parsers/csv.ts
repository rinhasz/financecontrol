import Papa from 'papaparse'

interface ParsedTx {
  data: string
  descricao: string
  valor: number
}

// Perfis de colunas por banco (detectado automaticamente pelo header)
const PROFILES = [
  {
    name: 'itau',
    detect: (h: string[]) => h.some((c) => c.toLowerCase().includes('lançamento') || c.toLowerCase().includes('lancamento')),
    date: 'Data',
    desc: ['Lançamento', 'Lancamento', 'Histórico'],
    value: ['Valor', 'valor']
  },
  {
    name: 'bradesco',
    detect: (h: string[]) => h.some((c) => c.toLowerCase().includes('histórico')),
    date: 'Data',
    desc: ['Histórico'],
    value: ['Valor']
  },
  {
    name: 'nubank',
    detect: (h: string[]) => h.some((c) => c.toLowerCase() === 'description'),
    date: 'Date',
    desc: ['Description'],
    value: ['Amount']
  },
  {
    name: 'generic',
    detect: () => true,
    date: '',
    desc: [],
    value: []
  }
]

export function parseCsv(content: string, _banco?: string): ParsedTx[] {
  const result = Papa.parse<Record<string, string>>(content, {
    header: true,
    skipEmptyLines: true,
    delimiter: ','
  })

  if (result.errors.length && !result.data.length) {
    const r2 = Papa.parse<Record<string, string>>(content, {
      header: true,
      skipEmptyLines: true,
      delimiter: ';'
    })
    if (r2.data.length) result.data = r2.data
  }

  if (!result.data.length) return []

  const headers = Object.keys(result.data[0])
  const profile = PROFILES.find((p) => p.detect(headers)) ?? PROFILES[PROFILES.length - 1]

  const dateCol = profile.date || headers.find((h) => /data|date/i.test(h)) || ''
  const descCols = profile.desc.length ? profile.desc : headers.filter((h) => /desc|hist|lança|lancam/i.test(h))
  const valueCol = profile.value[0] || headers.find((h) => /valor|amount|value/i.test(h)) || ''

  const txs: ParsedTx[] = []
  for (const row of result.data) {
    const rawDate = row[dateCol] || ''
    const data = parseDateBr(rawDate)
    if (!data) continue

    const descricao = descCols.map((c) => row[c] || '').join(' ').trim()
    const rawVal = row[valueCol] || ''
    const valor = parseBrNumber(rawVal)
    if (isNaN(valor)) continue

    txs.push({ data, descricao, valor })
  }

  return txs
}

function parseDateBr(s: string): string | null {
  if (!s) return null
  // DD/MM/YYYY
  const br = /^(\d{2})\/(\d{2})\/(\d{4})/.exec(s.trim())
  if (br) return `${br[3]}-${br[2]}-${br[1]}`
  // YYYY-MM-DD
  const iso = /^(\d{4})-(\d{2})-(\d{2})/.exec(s.trim())
  if (iso) return s.trim().slice(0, 10)
  return null
}

function parseBrNumber(s: string): number {
  if (!s) return NaN
  const clean = s.trim().replace(/\s/g, '')
  // Brazilian: 1.234,56 or -1.234,56
  if (/[\d.]+,\d{2}$/.test(clean)) {
    return parseFloat(clean.replace(/\./g, '').replace(',', '.'))
  }
  return parseFloat(clean.replace(',', '.'))
}
