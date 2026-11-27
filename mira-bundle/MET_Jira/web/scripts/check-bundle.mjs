import { readFile, readdir, stat } from 'node:fs/promises'
import { join } from 'node:path'

const distDir = new URL('../dist/', import.meta.url)
const assetsDir = new URL('../dist/assets/', import.meta.url)

const html = await readFile(new URL('index.html', distDir), 'utf8')
const referenced = new Set(
  [...html.matchAll(/(?:src|href)="\/?assets\/([^"]+)"/g)].map((match) => match[1]),
)

// Route CSS/JS chunks are loaded lazily and are not listed in index.html.
// Walk the current entry graph so budgets reflect this build only, not
// previous deploy-grace assets still sitting in dist/assets.
const queue = [...referenced]
const current = new Set(referenced)
const chunkRef = /(?:from\s*)?["'](?:\.\/|\/?assets\/)([\w.-]+\.(?:js|css))["']/g
while (queue.length) {
  const name = queue.pop()
  if (!name.endsWith('.js')) continue
  let source
  try {
    source = await readFile(join(assetsDir.pathname, name), 'utf8')
  } catch (error) {
    if (error?.code === 'ENOENT') continue
    throw error
  }
  for (const match of source.matchAll(chunkRef)) {
    const child = match[1]
    if (current.has(child)) continue
    current.add(child)
    queue.push(child)
  }
}

async function bytes(name) {
  return (await stat(join(assetsDir.pathname, name))).size
}

function findCurrent(pattern) {
  return [...current].find((name) => pattern.test(name))
}

const entry = findCurrent(/^index-[\w-]+\.js$/)
const reports = findCurrent(/^Reports-[\w-]+\.js$/)
const css = findCurrent(/^index-[\w-]+\.css$/)
const homeCss = findCurrent(/^Home-[\w-]+\.css$/)
const allCss = [...current].filter((name) => name.endsWith('.css'))

const budgets = [
  [entry, 580_000, 'entry JavaScript'],
  [reports, 625_000, 'reports JavaScript'],
  [css, 220_000, 'global CSS'],
  [homeCss, 18_000, 'home route CSS'],
]

let failed = false
for (const [name, limit, label] of budgets) {
  if (!name) {
    console.error(`Bundle budget: missing ${label} artifact`)
    failed = true
    continue
  }
  const size = await bytes(name)
  console.log(`${label}: ${(size / 1000).toFixed(1)} kB / ${(limit / 1000).toFixed(0)} kB`)
  if (size > limit) {
    console.error(`Bundle budget exceeded for ${name}`)
    failed = true
  }
}

const totalCss = (await Promise.all(allCss.map(bytes))).reduce((sum, size) => sum + size, 0)
console.log(`all CSS chunks: ${(totalCss / 1000).toFixed(1)} kB / 262 kB`)
if (totalCss > 262_000) {
  console.error('Bundle budget exceeded for total CSS chunks')
  failed = true
}

if (current.size === 0 || !(await readdir(assetsDir)).length) {
  console.error('Bundle budget: no current assets resolved from index.html')
  failed = true
}

if (failed) process.exit(1)
