/** Mirror of backend.services.workflow_audit.lint_flow for live diagram editing. */

import { t } from './i18n'
import { REJECTED } from './workflow'

export type LintLevel = 'error' | 'warning' | 'info'

export interface LintFinding {
  level: LintLevel
  /** Stable diagnostic id. Suggestions are chosen from this, not from the message text. */
  code: string
  message: string
  /** Status names this finding is about (for filtering in the status inspector). */
  statuses: string[]
}

function finding(level: LintLevel, code: string, message: string, statuses: string[]): LintFinding {
  return { level, code, message, statuses }
}

function reach(rules: Record<string, string[]>, start: string): Set<string> {
  const seen = new Set([start])
  const queue = [start]
  while (queue.length) {
    const cur = queue.pop()!
    for (const target of rules[cur] ?? []) {
      if (!seen.has(target)) {
        seen.add(target)
        queue.push(target)
      }
    }
  }
  return seen
}

/** Parse status names mentioned after a Chinese colon/list in known message shapes. */
export function namesInMessage(message: string): string[] {
  const m =
    message.match(/无法到达：(.+)$/)
    ?? message.match(/引用了不存在的状态：(.+)$/)
    ?? message.match(/主线（每步第一个目标）未到达完成状态：(.+)$/)
  if (m) {
    return m[1].split(/[、→]/).map((s) => s.trim()).filter(Boolean)
  }
  const one = message.match(/「([^」]+)」/g)
  return one ? one.map((s) => s.slice(1, -1)) : []
}

export function lintFlow(
  statuses: { name: string; category: string }[],
  rules: Record<string, string[]>,
): LintFinding[] {
  const category = Object.fromEntries(statuses.map((s) => [s.name, s.category]))
  const out: LintFinding[] = []
  const unknown = [
    ...new Set([
      ...Object.keys(rules).filter((s) => !(s in category)),
      ...Object.values(rules).flat().filter((t) => !(t in category)),
    ]),
  ].sort()
  if (unknown.length) {
    out.push(finding(
      'error',
      'unknown_status',
      t('引用了不存在的状态：{statuses}', { statuses: unknown.map((name) => t(name)).join(', ') }),
      unknown,
    ))
  }
  const used = new Set([
    ...Object.keys(rules).filter((s) => s in category),
    ...Object.values(rules).flat().filter((t) => t in category),
  ])
  if (!used.size) {
    return [...out, finding('error', 'empty', t('工作流为空'), [])]
  }
  const order = statuses.map((s) => s.name).filter((n) => used.has(n))
  const first = order[0]
  if (category[first] !== 'todo') {
    out.push(finding(
      'warning',
      'initial_not_todo',
      t('初始状态「{status}」不是待办类状态', { status: t(first) }),
      [first],
    ))
  }
  for (const [source, targets] of Object.entries(rules)) {
    if (targets.includes(source)) {
      out.push(finding(
        'warning',
        'self_loop',
        t('「{status}」流转到自身', { status: t(source) }),
        [source],
      ))
    }
  }
  const reachable = reach(rules, first)
  const lost = order.filter((name) => !reachable.has(name))
  if (lost.length) {
    out.push(finding(
      'error',
      'unreachable',
      t('从初始状态无法到达：{statuses}', { statuses: lost.map((name) => t(name)).join(', ') }),
      lost,
    ))
  }
  const done = new Set([...used].filter((name) => category[name] === 'done'))
  const finish = new Set([...done].filter((n) => n !== REJECTED))
  if (!finish.size) for (const n of done) finish.add(n)
  if (![...reachable].some((n) => finish.has(n))) {
    out.push(finding('error', 'no_completion', t('从初始状态无法到达完成状态'), [...finish]))
  }
  for (const name of order) {
    if (category[name] !== 'done' && reachable.has(name) && ![...reach(rules, name)].some((n) => done.has(n))) {
      out.push(finding(
        'error',
        'dead_end',
        t('进入「{status}」后再也无法完成或驳回（死胡同）', { status: t(name) }),
        [name],
      ))
    }
  }
  for (const name of [...finish].sort()) {
    const exits = rules[name] ?? []
    if (!exits.length) {
      out.push(finding(
        'warning',
        'no_reopen',
        t('「{status}」之后没有任何流转，完成后无法重新打开', { status: t(name) }),
        [name],
      ))
    } else if (exits.every((t) => category[t] === 'done')) {
      out.push(finding(
        'warning',
        'reopen_stuck',
        t('「{status}」只能流转到「{targets}」，完成的工作无法重新打开', {
          status: t(name),
          targets: exits.map((target) => t(target)).join(', '),
        }),
        [name, ...exits],
      ))
    }
  }
  const main = [first]
  let step = first
  while (rules[step]?.length) {
    step = rules[step][0]
    if (main.includes(step)) break
    main.push(step)
  }
  if (![...finish].some((n) => main.includes(n))) {
    out.push(finding(
      'warning',
      'main_not_done',
      t('主线（每步第一个目标）未到达完成状态：{statuses}', {
        statuses: main.map((status) => t(status)).join(' → '),
      }),
      main,
    ))
  }
  const stepwise = order.filter((name) => category[name] === 'doing' && name in rules)
  const noBack = stepwise.filter(
    (name) =>
      !(rules[name] ?? []).some(
        (t) => t !== REJECTED && order.includes(t) && order.indexOf(t) < order.indexOf(name),
      ),
  )
  if (stepwise.length && noBack.length === stepwise.length) {
    out.push(finding('info', 'no_step_back', t('进行中的步骤都不能退回上一步，只能驳回'), stepwise))
  }
  return out
}

/** Statuses that appear in the graph but have no incoming and no outgoing edges (orphans in flow). */
export function statusesNeedingTransition(
  statuses: { name: string }[],
  rules: Record<string, string[]>,
): string[] {
  const used = new Set([
    ...Object.keys(rules),
    ...Object.values(rules).flat(),
  ])
  if (!used.size) return []
  const incoming = new Set(Object.values(rules).flat())
  return statuses
    .map((s) => s.name)
    .filter((name) => used.has(name) && !(rules[name]?.length) && !incoming.has(name))
}

export function warnStatusNames(findings: LintFinding[], rules: Record<string, string[]>, statuses: { name: string }[]): Set<string> {
  const names = new Set<string>()
  for (const f of findings) {
    if (f.level === 'error' || f.level === 'warning') {
      for (const s of f.statuses) names.add(s)
    }
  }
  for (const s of statusesNeedingTransition(statuses, rules)) names.add(s)
  return names
}

export function findingsForStatus(findings: LintFinding[], statusName: string, rules: Record<string, string[]>): LintFinding[] {
  const related = findings.filter((f) => f.statuses.includes(statusName))
  const degreeIn = Object.values(rules).filter((tos) => tos.includes(statusName)).length
  const degreeOut = (rules[statusName] ?? []).length
  const inGraph = degreeIn + degreeOut > 0 || statusName in rules
  if (inGraph && degreeIn === 0 && degreeOut === 0 && !related.some((f) => f.message === t('这一状态需要转换。'))) {
    related.push(finding('warning', 'needs_transition', t('这一状态需要转换。'), [statusName]))
  }
  return related
}
