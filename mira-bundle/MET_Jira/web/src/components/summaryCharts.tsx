import { useState, type MouseEvent, type ReactNode } from 'react'
import { t } from '../i18n'
import { PRIORITY_META, priorityName } from '../labels'
import type { Priority, Status } from '../types'
import { PriorityIcon } from './PriorityIcon'

export type StatusCount = Status & { count: number }
export type PriorityCount = { priority: Priority; count: number }

export function Glyph({ d, size = 20 }: { d: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  )
}

export function CountCard({
  icon,
  color,
  value,
  label,
  sub,
  compact,
  onClick,
}: {
  icon: ReactNode
  color: string
  value: number
  label: string
  sub?: string
  compact?: boolean
  onClick?: () => void
}) {
  const press = (e: MouseEvent<HTMLButtonElement>) => {
    if (!onClick) return
    const btn = e.currentTarget
    const rect = btn.getBoundingClientRect()
    const dot = document.createElement('span')
    dot.className = 'stat-ripple'
    dot.style.left = `${e.clientX - rect.left}px`
    dot.style.top = `${e.clientY - rect.top}px`
    btn.appendChild(dot)
    dot.addEventListener('animationend', () => dot.remove())
    onClick()
  }
  if (onClick) {
    return (
      <button type="button" className={`card summary-count${compact ? ' compact' : ''} summary-count-btn`} onClick={press}>
        <span className="summary-count-icon" style={{ color, background: `color-mix(in srgb, ${color} 14%, transparent)` }}>
          {icon}
        </span>
        <div style={{ minWidth: 0 }}>
          <div className="summary-count-value">{t('{n} 项', { n: value })}</div>
          <div className="small muted">{label}</div>
          {sub && (
            <div className="tiny" style={{ color: 'var(--danger)' }}>
              {sub}
            </div>
          )}
        </div>
      </button>
    )
  }
  return (
    <div className={`card summary-count${compact ? ' compact' : ''}`}>
      <span className="summary-count-icon" style={{ color, background: `color-mix(in srgb, ${color} 14%, transparent)` }}>
        {icon}
      </span>
      <div style={{ minWidth: 0 }}>
        <div className="summary-count-value">{t('{n} 项', { n: value })}</div>
        <div className="small muted">{label}</div>
        {sub && (
          <div className="tiny" style={{ color: 'var(--danger)' }}>
            {sub}
          </div>
        )}
      </div>
    </div>
  )
}

const CATEGORY_META: Record<Status['category'], { label: string; color: string }> = {
  todo: { label: '待办', color: '#8e8e93' },
  doing: { label: '进行中', color: '#0a84ff' },
  done: { label: '完成', color: '#30d158' },
}

/** Ring chart of issues per status, with category chips and denser legend bars. */
export function StatusDonut({
  statuses,
  total,
  size = 'lg',
  selectedName,
  onSelect,
}: {
  statuses: StatusCount[]
  total: number
  size?: 'md' | 'lg'
  selectedName?: string | null
  onSelect?: (name: string) => void
}) {
  const [hover, setHover] = useState<number | null>(null)
  // Larger ring so center labels (status names) fit; legend bars stay compact.
  const px = size === 'md' ? 168 : 200
  const r = size === 'md' ? 56 : 68
  const stroke = size === 'md' ? 18 : 22
  const fat = size === 'md' ? 24 : 28
  // Keep the center label inside the hole even when a hovered arc thickens.
  const holePx = Math.floor(((2 * (r - fat / 2) - 4) / 180) * px)
  const circumference = 2 * Math.PI * r
  const shown = statuses.filter((s) => s.count > 0)
  let offset = 0
  const focus = shown.find((s) => s.id === hover) ?? shown.find((s) => s.name === selectedName)
  const interactive = !!onSelect
  const byCategory = (['todo', 'doing', 'done'] as const).map((category) => ({
    category,
    ...CATEGORY_META[category],
    count: statuses.filter((s) => s.category === category).reduce((n, s) => n + s.count, 0),
  }))
  const rows = interactive ? shown : statuses
  const pctOf = (n: number) => (total ? Math.round((n / total) * 100) : 0)
  const centerLabel = focus ? t(focus.name) : t('问题总数')

  return (
    <div className={`summary-donut${size === 'md' ? ' md' : ''}${interactive ? ' interactive' : ''}`}>
      <div className="summary-category-chips" aria-label={t('按类别汇总')}>
        {byCategory.map((c) => (
          <div key={c.category} className="summary-category-chip">
            <i style={{ background: c.color }} />
            <span className="summary-category-label">{t(c.label)}</span>
            <b>{c.count}</b>
            <span className="summary-category-pct">{pctOf(c.count)}%</span>
          </div>
        ))}
      </div>
      <div className="summary-donut-body">
        <div className="summary-donut-ring" style={{ width: px, height: px, ['--donut-hole' as string]: `${holePx}px` }}>
          <svg viewBox="0 0 180 180" width={px} height={px} role="img" aria-label={t('状态概览')} className="summary-donut-svg">
            <circle cx="90" cy="90" r={r} fill="none" stroke="var(--surface-3)" strokeWidth={stroke} />
            {shown.map((s) => {
              const length = (s.count / (total || 1)) * circumference
              const active = hover === s.id || selectedName === s.name
              const arc = (
                <circle
                  key={s.id}
                  cx="90"
                  cy="90"
                  r={r}
                  fill="none"
                  stroke={s.color}
                  strokeWidth={active ? fat : stroke}
                  strokeDasharray={`${Math.max(0, length - 1.5)} ${circumference}`}
                  strokeDashoffset={-offset}
                  transform="rotate(-90 90 90)"
                  className="summary-donut-arc"
                  style={{ cursor: interactive ? 'pointer' : 'default' }}
                  onMouseEnter={() => setHover(s.id)}
                  onMouseLeave={() => setHover(null)}
                  onClick={() => onSelect?.(s.name)}
                />
              )
              offset += length
              return arc
            })}
          </svg>
          <div className="summary-donut-center" aria-hidden={!focus}>
            <strong className="summary-donut-value">{focus ? focus.count : total}</strong>
            <span className="summary-donut-label" title={centerLabel}>{centerLabel}</span>
          </div>
        </div>
        <div className="summary-legend">
          {rows.map((s) => {
            const share = pctOf(s.count)
            return (
              <div
                key={s.id}
                className={`summary-legend-row${hover === s.id || selectedName === s.name ? ' on' : ''}${interactive ? ' clickable' : ''}`}
                onMouseEnter={() => setHover(s.id)}
                onMouseLeave={() => setHover(null)}
                onClick={() => onSelect?.(s.name)}
                role={interactive ? 'button' : undefined}
                tabIndex={interactive ? 0 : undefined}
                onKeyDown={(e) => {
                  if (interactive && (e.key === 'Enter' || e.key === ' ')) {
                    e.preventDefault()
                    onSelect?.(s.name)
                  }
                }}
              >
                <i style={{ background: s.color }} />
                <span className="ellipsis">{t(s.name)}</span>
                <div className="summary-legend-track" aria-hidden="true">
                  <div style={{ width: `${share}%`, background: s.color }} />
                </div>
                <span className="summary-legend-pct">{share}%</span>
                <b>{s.count}</b>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export function PriorityColumns({
  rows,
  selected,
  onSelect,
}: {
  rows: PriorityCount[]
  selected?: Priority | null
  onSelect?: (priority: Priority) => void
}) {
  const top = Math.max(1, ...rows.map((r) => r.count))
  const interactive = !!onSelect
  return (
    <div className={`summary-columns${interactive ? ' interactive' : ''}`}>
      {rows.map((r) => {
        const body = (
          <>
            <span className="small bold">{r.count}</span>
            <div className="summary-column-bar" style={{ height: `${Math.max(2, (r.count / top) * 100)}%`, background: PRIORITY_META[r.priority].color }} />
            <span className="tiny muted summary-prio-label">
              <PriorityIcon priority={r.priority} size={14} /> {priorityName(r.priority)}
            </span>
          </>
        )
        if (!interactive) {
          return (
            <div key={r.priority} className="summary-column" title={`${priorityName(r.priority)} ${r.count}`}>
              {body}
            </div>
          )
        }
        return (
          <button
            key={r.priority}
            type="button"
            className={`summary-column${selected === r.priority ? ' on' : ''}`}
            title={`${priorityName(r.priority)} ${r.count}`}
            onClick={() => onSelect(r.priority)}
          >
            {body}
          </button>
        )
      })}
    </div>
  )
}
