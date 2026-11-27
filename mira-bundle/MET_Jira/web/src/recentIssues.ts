/** Browser-local recently opened issue keys (for the default「最近查看」filter). */

const STORAGE = 'metjira.recent.issues'
const MAX = 40

export function rememberIssueView(key: string): void {
  const normalized = key.trim().toUpperCase()
  if (!normalized) return
  try {
    const prev = loadRecentIssueKeys().filter((item) => item !== normalized)
    localStorage.setItem(STORAGE, JSON.stringify([normalized, ...prev].slice(0, MAX)))
  } catch {
    // private mode / quota — ignore
  }
}

export function loadRecentIssueKeys(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    return parsed.filter((item): item is string => typeof item === 'string' && !!item)
  } catch {
    return []
  }
}

/** JQL for issues the current browser has opened recently. */
export function recentlyViewedJql(): string {
  const keys = loadRecentIssueKeys()
  if (!keys.length) return 'key = "__none__"'
  return `key in (${keys.map((key) => `"${key}"`).join(', ')})`
}
