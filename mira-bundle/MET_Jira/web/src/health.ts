import { t } from './i18n'

export type HealthLevel = 'good' | 'attention' | 'risk'
export type SignalKey = 'overdue' | 'blocked' | 'stale' | 'unassigned' | 'no_due' | 'net_flow'

/** Project health as computed by `reports.health_core` on the backend. */
export interface HealthCore {
  score: number
  level: HealthLevel
  signals: { key: SignalKey; penalty: number; count: number }[]
  counts: Record<'total' | 'open' | 'doing' | 'overdue' | 'due_soon' | 'blocked' | 'stale' | 'unassigned' | 'no_due', number>
  flow: { days: number; created: number; completed: number; rejected: number }
}

export const LEVEL_COLOR: Record<HealthLevel, string> = { good: 'var(--success)', attention: 'var(--warning)', risk: 'var(--danger)' }

export function levelName(level: HealthLevel): string {
  return { good: t('健康'), attention: t('需关注'), risk: t('有风险') }[level]
}
