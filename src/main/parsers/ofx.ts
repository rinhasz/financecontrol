interface ParsedTx {
  data: string
  descricao: string
  valor: number
}

export async function parseOfx(content: string): Promise<ParsedTx[]> {
  const txRegex = /<STMTTRN>([\s\S]*?)<\/STMTTRN>/g
  const results: ParsedTx[] = []
  let match: RegExpExecArray | null

  while ((match = txRegex.exec(content)) !== null) {
    const block = match[1]
    const dtposted = extract(block, 'DTPOSTED')
    const trnamt = extract(block, 'TRNAMT')
    const memo = extract(block, 'MEMO') || extract(block, 'NAME') || ''

    if (!dtposted || !trnamt) continue

    const data = parseOfxDate(dtposted)
    const valor = parseFloat(trnamt.replace(',', '.'))
    if (!data || isNaN(valor)) continue

    results.push({ data, descricao: memo.trim(), valor })
  }

  // fallback: SGML format without closing tags
  if (!results.length) {
    const lines = content.split(/\r?\n/)
    let inTrn = false
    let current: Partial<ParsedTx> & { raw: Record<string, string> } = { raw: {} }

    for (const line of lines) {
      const l = line.trim()
      if (l === '<STMTTRN>') { inTrn = true; current = { raw: {} }; continue }
      if (l === '</STMTTRN>') {
        inTrn = false
        if (current.raw.DTPOSTED && current.raw.TRNAMT) {
          const data = parseOfxDate(current.raw.DTPOSTED)
          const valor = parseFloat(current.raw.TRNAMT.replace(',', '.'))
          if (data && !isNaN(valor)) {
            results.push({ data, descricao: (current.raw.MEMO || current.raw.NAME || '').trim(), valor })
          }
        }
        continue
      }
      if (inTrn) {
        const m = /^<([A-Z]+)>(.*)$/.exec(l)
        if (m) current.raw[m[1]] = m[2]
      }
    }
  }

  return results
}

function extract(block: string, tag: string): string {
  const m = new RegExp(`<${tag}>([^<]*)`).exec(block)
  return m ? m[1].trim() : ''
}

function parseOfxDate(s: string): string | null {
  const clean = s.slice(0, 8)
  if (clean.length !== 8) return null
  return `${clean.slice(0, 4)}-${clean.slice(4, 6)}-${clean.slice(6, 8)}`
}
