import { pick, t } from '../i18n'
import { PRIORITY_META } from '../labels'
import type { Priority } from '../types'

/** Jira-style priority glyphs (Atlaskit icon-priority chevrons / equals). */
const PRIORITY_PATHS: Record<Priority, string> = {
  highest:
    'M8 2.6 12.7 7.3 11.3 8.7 8 5.4 4.7 8.7 3.3 7.3 8 2.6zm0 4.8 4.7 4.7-1.4 1.4L8 10.2 4.7 13.5 3.3 12.1 8 7.4z',
  high: 'M8 4.2 12.7 8.9 11.3 10.3 8 7 4.7 10.3 3.3 8.9 8 4.2z',
  medium: 'M3.2 6.2h9.6v1.8H3.2V6.2zm0 3.8h9.6v1.8H3.2V10z',
  low: 'M8 11.8 3.3 7.1 4.7 5.7 8 9 11.3 5.7 12.7 7.1 8 11.8z',
  lowest:
    'M8 8.6 3.3 3.9 4.7 2.5 8 5.8 11.3 2.5 12.7 3.9 8 8.6zm0 4.8L3.3 8.7 4.7 7.3 8 10.6 11.3 7.3 12.7 8.7 8 13.4z',
}

export function PriorityIcon({ priority, size = 16 }: { priority: Priority; size?: number }) {
  const meta = PRIORITY_META[priority] ?? PRIORITY_META.medium
  const key = (PRIORITY_META[priority] ? priority : 'medium') as Priority
  return (
    <span
      className="prio"
      title={t('优先级：{name}', { name: pick(meta) })}
      style={{ color: meta.color, width: size, height: size }}
    >
      <svg viewBox="0 0 16 16" width={size} height={size} aria-hidden focusable="false">
        <path fill="currentColor" d={PRIORITY_PATHS[key]} />
      </svg>
    </span>
  )
}
