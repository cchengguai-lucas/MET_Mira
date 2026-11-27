export interface FlowEdge {
  from: string
  to: string
  position?: 'append' | 'first'
}

export interface FlowSuggestion {
  id: string
  add: FlowEdge[]
  remove: FlowEdge[]
}

export function flowSignature(rules: Record<string, string[]>): string {
  return JSON.stringify(Object.fromEntries(
    Object.keys(rules).sort().map((key) => [key, rules[key] ?? []]),
  ))
}

export function adviceIsStale(adviceSignature: string, currentSignature: string): boolean {
  return adviceSignature !== currentSignature
}

export function draftHasErrors(findings: { level: string }[]): boolean {
  return findings.some((item) => item.level === 'error')
}

/** Apply the selected repair cards. Removals run before additions. */
export function mergeSuggestionEdits(
  current: Record<string, string[]>,
  suggestions: FlowSuggestion[],
  selected: ReadonlySet<string>,
): Record<string, string[]> {
  const next: Record<string, string[]> = {}
  for (const [source, targets] of Object.entries(current)) next[source] = [...targets]
  const chosen = suggestions.filter((item) => selected.has(item.id))
  for (const item of chosen) {
    for (const edge of item.remove) {
      const targets = (next[edge.from] ?? []).filter((target) => target !== edge.to)
      if (targets.length) next[edge.from] = targets
      else delete next[edge.from]
    }
  }
  for (const item of chosen) {
    for (const edge of item.add) {
      if (edge.from === edge.to) continue
      const targets = [...(next[edge.from] ?? [])]
      if (edge.position === 'first') {
        next[edge.from] = [edge.to, ...targets.filter((target) => target !== edge.to)]
      }
      else if (!targets.includes(edge.to)) next[edge.from] = [...targets, edge.to]
    }
  }
  return next
}

/** Identity of one proposed add or removal. Skipping an add drops it; skipping a removal keeps it. */
export function edgeId(kind: 'add' | 'remove', edge: FlowEdge): string {
  return `${kind}\0${edge.from}\0${edge.to}`
}

export function omitStatuses(
  rules: Record<string, string[]>,
  names: ReadonlySet<string>,
): Record<string, string[]> {
  const next: Record<string, string[]> = {}
  for (const [source, targets] of Object.entries(rules)) {
    if (names.has(source)) continue
    const kept = targets.filter((target) => !names.has(target))
    if (kept.length) next[source] = kept
  }
  return next
}

export function applyWorkflowEdits(
  proposed: Record<string, string[]>,
  remove: FlowEdge[],
  skipped: ReadonlySet<string>,
): Record<string, string[]> {
  const next: Record<string, string[]> = {}
  for (const [source, targets] of Object.entries(proposed)) {
    const kept = targets.filter((target) => !skipped.has(edgeId('add', { from: source, to: target })))
    if (kept.length) next[source] = [...kept]
  }
  for (const edge of remove) {
    if (!skipped.has(edgeId('remove', edge)) || edge.from === edge.to) continue
    const targets = next[edge.from] ?? []
    if (!targets.includes(edge.to)) next[edge.from] = [...targets, edge.to]
  }
  return next
}

export function sameFlow(left: Record<string, string[]>, right: Record<string, string[]>): boolean {
  const keys = new Set([...Object.keys(left), ...Object.keys(right)])
  for (const key of keys) {
    const a = left[key] ?? []
    const b = right[key] ?? []
    if (a.length !== b.length || a.some((item, index) => item !== b[index])) return false
  }
  return true
}
