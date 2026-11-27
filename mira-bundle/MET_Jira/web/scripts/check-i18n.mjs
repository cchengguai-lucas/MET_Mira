#!/usr/bin/env node
// Verifies every Chinese UI string in web/src has an English translation in src/locales/en.
// Usage: node scripts/check-i18n.mjs [file ...]   (defaults to all of src)
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import ts from 'typescript'

const ROOT = resolve(new URL('..', import.meta.url).pathname)
const SRC = join(ROOT, 'src')
const LOCALES = join(SRC, 'locales', 'en')
const CJK = /[\u3400-\u9fff]/
const SKIP_FILES = new Set([join(SRC, 'i18n.tsx')])
const ALLOWED_PROPS = new Set(['zh', 'glyph'])
const TEST_FILE = /\.test\.tsx?$/

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return walk(path)
    return /\.(ts|tsx)$/.test(name) ? [path] : []
  })
}

function parse(path) {
  const kind = path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  return ts.createSourceFile(path, readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true, kind)
}

function placeholders(text) {
  return [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(',')
}

const dict = new Map()
const problems = []

for (const path of walk(LOCALES)) {
  const source = parse(path)
  const visit = (node) => {
    if (ts.isPropertyAssignment(node) && (ts.isStringLiteral(node.name) || ts.isIdentifier(node.name))) {
      const key = node.name.text
      const init = node.initializer
      if (ts.isStringLiteral(init) || ts.isNoSubstitutionTemplateLiteral(init)) {
        const value = init.text
        const where = `${relative(ROOT, path)}:${source.getLineAndCharacterOfPosition(node.getStart()).line + 1}`
        if (CJK.test(value)) problems.push(`${where}  translation still contains Chinese: ${key}`)
        if (placeholders(key) !== placeholders(value)) problems.push(`${where}  placeholder mismatch: ${key} -> ${value}`)
        const prev = dict.get(key)
        if (prev && prev.value !== value) problems.push(`${where}  conflicting translation for "${key}" (also ${prev.where})`)
        dict.set(key, { value, where })
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
}

const targets = (process.argv.slice(2).length
  ? process.argv.slice(2).map((p) => resolve(process.cwd(), p))
  : walk(SRC).filter((p) => !p.startsWith(LOCALES) && !SKIP_FILES.has(p)))
  .filter((p) => !TEST_FILE.test(p))

for (const path of targets) {
  const source = parse(path)
  const report = (node, message) => {
    const line = source.getLineAndCharacterOfPosition(node.getStart()).line + 1
    problems.push(`${relative(ROOT, path)}:${line}  ${message}`)
  }
  const visit = (node) => {
    if (ts.isJsxText(node) && CJK.test(node.text)) {
      report(node, `JSX text must be wrapped in t(): ${node.text.trim()}`)
    } else if (ts.isTemplateExpression(node)) {
      const parts = [node.head.text, ...node.templateSpans.map((s) => s.literal.text)].join('')
      if (CJK.test(parts)) report(node, `template literal with Chinese; use t('…{name}…', { name }): ${node.getText().slice(0, 80)}`)
    } else if ((ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) && CJK.test(node.text)) {
      const parent = node.parent
      const allowed = parent && ts.isPropertyAssignment(parent) && parent.initializer === node && ALLOWED_PROPS.has(parent.name.getText())
      if (!allowed && !dict.has(node.text)) report(node, `missing English translation: ${node.text}`)
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
}

if (problems.length) {
  console.error(problems.join('\n'))
  console.error(`\n${problems.length} i18n problem(s).`)
  process.exit(1)
}
console.log(`i18n OK: ${targets.length} file(s), ${dict.size} translations.`)
