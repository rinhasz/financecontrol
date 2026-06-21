import Database from 'better-sqlite3'
import { createReadStream } from 'fs'
import { join } from 'path'
import Papa from 'papaparse'
import { createInterface } from 'readline'
import { Readable } from 'stream'

const SEED_DIR = join(process.cwd(), 'seed')

const CATEGORIAS = [
  'Financiamento imóvel',
  'Casa/Utilidades',
  'Saúde',
  'Cartões',
  'Filhos/Educação',
  'Funcionária',
  'Outros'
]

const PADRAO_MAP: Record<string, string> = {
  'reajuste anual': 'reajuste_anual',
  'variavel sazonal': 'variavel_sazonal',
  'variavel nao-sazonal': 'variavel_nao_sazonal',
  'nao-sazonal': 'variavel_nao_sazonal',
  fixa: 'fixa',
  anual: 'anual',
  'sem dados': 'sem_dados'
}

function parseBrNumber(s: string): number {
  if (!s) return 0
  return parseFloat(s.replace(/\./g, '').replace(',', '.')) || 0
}

async function readCsv(filename: string): Promise<Record<string, string>[]> {
  const content = await new Promise<string>((resolve, reject) => {
    const chunks: Buffer[] = []
    const stream = createReadStream(join(SEED_DIR, filename))
    stream.on('data', (c) => chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c)))
    stream.on('end', () => resolve(Buffer.concat(chunks).toString('utf-8')))
    stream.on('error', reject)
  })
  const result = Papa.parse<Record<string, string>>(content, {
    header: true,
    skipEmptyLines: true
  })
  return result.data
}

export async function runSeed(db: Database.Database): Promise<{ ok: boolean; msg: string }> {
  const already = db.prepare('SELECT COUNT(*) as n FROM despesa').get() as { n: number }
  if (already.n > 0) return { ok: true, msg: 'Seed já executado' }

  try {
    // Categorias
    const insertCat = db.prepare('INSERT OR IGNORE INTO categoria (nome) VALUES (?)')
    for (const cat of CATEGORIAS) insertCat.run(cat)

    const catMap = new Map<string, number>()
    for (const row of db.prepare('SELECT id, nome FROM categoria').all() as {
      id: number
      nome: string
    }[]) {
      catMap.set(row.nome, row.id)
    }

    // Catálogo de despesas
    const catalog = await readCsv('catalogo_despesas.csv')

    function resolveCategoria(nome: string): number {
      const n = nome.toLowerCase()
      if (n.includes('financ')) return catMap.get('Financiamento imóvel')!
      if (n.includes('saúde') || n.includes('saude')) return catMap.get('Saúde')!
      if (n.includes('cartão') || n.includes('cartao') || n.includes('cart')) return catMap.get('Cartões')!
      if (n.includes('filhos') || n.includes('educat') || n.includes('educ')) return catMap.get('Filhos/Educação')!
      if (n.includes('função') || n.includes('funcionaria') || n.includes('funcionária')) return catMap.get('Funcionária')!
      if (n.includes('casa') || n.includes('util')) return catMap.get('Casa/Utilidades')!
      return catMap.get('Outros')!
    }

    function resolveKeywords(contaNorm: string): string[] {
      return contaNorm
        .toLowerCase()
        .replace(/[()]/g, '')
        .split(/[\s\/,]+/)
        .filter((w) => w.length >= 3)
    }

    const insertDespesa = db.prepare(`
      INSERT OR IGNORE INTO despesa
        (nome, categoria_id, padrao_variabilidade, valor_padrao, regras_match, ativo)
      VALUES (?, ?, ?, ?, ?, 1)
    `)

    const despesaNomeMap = new Map<string, number>()

    for (const row of catalog) {
      const padrao = PADRAO_MAP[row.padrao?.toLowerCase()?.trim()] ?? 'variavel_nao_sazonal'
      const valorPadrao = parseBrNumber(row.previsao_jul26 || row.media)
      const catId = resolveCategoria(row.categoria_sugerida || '')
      const keywords = resolveKeywords(row.conta_norm)
      const regras = JSON.stringify({ palavras_chave: keywords, faixa_valor: null, janela_dias: 5, banco: null })

      insertDespesa.run(row.conta_norm, catId, padrao, valorPadrao, regras)
    }

    for (const row of db.prepare('SELECT id, nome FROM despesa').all() as { id: number; nome: string }[]) {
      despesaNomeMap.set(row.nome, row.id)
    }

    // Base histórica → lancamentos
    const baseHist = await readCsv('base_historica.csv')
    const insertLanc = db.prepare(`
      INSERT OR IGNORE INTO lancamento
        (mes_ref, despesa_id, valor_esperado, status)
      VALUES (?, ?, ?, 'nao_encontrado')
    `)

    const insertMany = db.transaction((rows: typeof baseHist) => {
      for (const row of rows) {
        const despesaId = despesaNomeMap.get(row.conta_norm)
        if (!despesaId) continue
        const valor = parseBrNumber(row.valor)
        insertLanc.run(row.ano_mes, despesaId, valor)
      }
    })
    insertMany(baseHist)

    return { ok: true, msg: `Seed concluído: ${catalog.length} despesas, ${baseHist.length} lançamentos históricos` }
  } catch (e) {
    return { ok: false, msg: String(e) }
  }
}
