/** Lightweight self-check mirroring web/src/workflowLint.ts (no vitest in package). */
import assert from 'node:assert/strict'

const REJECTED = '已驳回'

function reach(rules, start) {
  const seen = new Set([start])
  const queue = [start]
  while (queue.length) {
    const cur = queue.pop()
    for (const target of rules[cur] ?? []) {
      if (!seen.has(target)) {
        seen.add(target)
        queue.push(target)
      }
    }
  }
  return seen
}

function lintFlow(statuses, rules) {
  const category = Object.fromEntries(statuses.map((s) => [s.name, s.category]))
  const out = []
  const used = new Set([
    ...Object.keys(rules).filter((s) => s in category),
    ...Object.values(rules).flat().filter((t) => t in category),
  ])
  if (!used.size) return [{ level: 'error', message: '工作流为空' }]
  const order = statuses.map((s) => s.name).filter((n) => used.has(n))
  const first = order[0]
  const reachable = reach(rules, first)
  const lost = order.filter((name) => !reachable.has(name))
  if (lost.length) out.push({ level: 'error', message: `从初始状态无法到达：${lost.join('、')}`, statuses: lost })
  const done = new Set([...used].filter((name) => category[name] === 'done'))
  for (const name of order) {
    if (category[name] !== 'done' && reachable.has(name) && ![...reach(rules, name)].some((n) => done.has(n))) {
      out.push({ level: 'error', message: `进入「${name}」后再也无法完成或驳回（死胡同）`, statuses: [name] })
    }
  }
  return out
}

const statuses = [
  { name: '待办', category: 'todo' },
  { name: '进行中', category: 'doing' },
  { name: '已完成', category: 'done' },
  { name: REJECTED, category: 'done' },
]

assert.equal(lintFlow(statuses, {})[0].message, '工作流为空')

const unreachable = lintFlow(statuses, { 待办: ['进行中'], 已完成: [REJECTED] })
assert.ok(unreachable.some((f) => f.message.includes('无法到达') && f.statuses.includes('已完成')))

const dead = lintFlow(statuses, { 待办: ['进行中'] })
assert.ok(dead.some((f) => f.message.includes('死胡同')))

console.log('workflowLint self-check ok')
