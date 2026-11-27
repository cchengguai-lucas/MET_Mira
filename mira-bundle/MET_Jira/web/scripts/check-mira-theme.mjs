import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { relative, resolve } from 'node:path'

const root = resolve(new URL('..', import.meta.url).pathname)
const expected = new Map([
  ['public/trace-background.js', 'a414449b36df1d5046e7992082f748368ef4d0f279641e923e8753bdd7ff9196'],
  ['src/pages/mira-login/tokens.css', '5b200ab49f14b04b43f588e6b7ddddd178a7493372b6499237cc959b1512960b'],
  // Upstream login.css with its Google Fonts import replaced by one local-font comment.
  ['src/pages/mira-login/login.css', '5f5fdb7838d6536de5d5ac02ed8575df66995e1fc63e4b01fae94de4ed2a78fb'],
])

let failed = false
for (const [name, wanted] of expected) {
  const path = resolve(root, name)
  const actual = createHash('sha256').update(await readFile(path)).digest('hex')
  if (actual !== wanted) {
    console.error(`Mira theme drift: ${relative(root, path)}\n  expected ${wanted}\n  actual   ${actual}`)
    failed = true
  }
}

if (failed) {
  console.error('Keep host changes in src/pages/mira-login/integration.css; update PROVENANCE.md when intentionally syncing upstream.')
  process.exit(1)
}

console.log(`Mira theme sync OK: ${expected.size} upstream files`)
