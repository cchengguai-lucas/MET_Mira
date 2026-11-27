import { readdir, stat, unlink } from 'node:fs/promises'
import { join } from 'node:path'

// Keep a grace period for tabs that were opened before a deploy. Their entry
// bundle can still lazy-load route chunks from that generation.
const KEEP_MS = 7 * 24 * 60 * 60 * 1000
const assets = new URL('../dist/assets/', import.meta.url)
const cutoff = Date.now() - KEEP_MS
let removed = 0

try {
  for (const entry of await readdir(assets, { withFileTypes: true })) {
    if (!entry.isFile()) continue
    const path = join(assets.pathname, entry.name)
    const info = await stat(path)
    if (info.mtimeMs >= cutoff) continue
    await unlink(path)
    removed += 1
  }
} catch (error) {
  if (error?.code !== 'ENOENT') throw error
}

console.log(`old asset cleanup: ${removed} file(s) removed; 7-day deploy grace retained`)
