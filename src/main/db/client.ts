import Database from 'better-sqlite3'
import { app } from 'electron'
import { readFileSync } from 'fs'
import { join } from 'path'

const isDev = process.env['NODE_ENV'] === 'development'

function getDbPath(): string {
  if (isDev) return join(process.cwd(), 'dev.sqlite')
  return join(app.getPath('userData'), 'database.sqlite')
}

let _db: Database.Database | null = null

export function getDb(): Database.Database {
  if (_db) return _db
  _db = new Database(getDbPath())
  _db.pragma('journal_mode = WAL')
  _db.pragma('foreign_keys = ON')
  runMigrations(_db)
  return _db
}

function runMigrations(db: Database.Database): void {
  db.exec(`CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY)`)

  const migDir = isDev
    ? join(process.cwd(), 'src/main/db/migrations')
    : join((process as NodeJS.Process & { resourcesPath: string }).resourcesPath, 'migrations')

  const migrations = ['001_initial.sql']

  for (const m of migrations) {
    const already = db.prepare('SELECT 1 FROM _migrations WHERE name = ?').get(m)
    if (already) continue
    const sql = readFileSync(join(migDir, m), 'utf-8')
    db.exec(sql)
    db.prepare('INSERT INTO _migrations VALUES (?)').run(m)
  }
}
