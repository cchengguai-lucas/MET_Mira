import { useRef, useState, type ReactNode } from 'react'
import { Popover, Segmented } from '../components/ui'
import { t } from '../i18n'
import { useUrlState } from '../share'
import type { Issue } from '../types'
import { IssueRows } from './Home'

const DAY_OPTIONS = ['14', '30', '90']
const SUBTASK_REPORT_STORAGE = 'metjira.reports.subtasks'

export function useReportWindow() {
  const [params, update] = useUrlState()
  const raw = params.get('days')
  const days = raw && DAY_OPTIONS.includes(raw) ? raw : '30'
  const [withSubtasks, setSubtasks] = useState(() => localStorage.getItem(SUBTASK_REPORT_STORAGE) !== 'exclude')
  return {
    days,
    setDays: (value: string) => update({ days: value === '30' ? null : value }),
    withSubtasks,
    setWithSubtasks: (value: boolean) => {
      setSubtasks(value)
      localStorage.setItem(SUBTASK_REPORT_STORAGE, value ? 'include' : 'exclude')
    },
  }
}

export function WindowControls({ win, subtasks = true, days = true, children }: { win: ReturnType<typeof useReportWindow>; subtasks?: boolean; days?: boolean; children?: ReactNode }) {
  return (
    <div className="row wrap report-controls">
      {children}
      <span className="spacer" />
      {subtasks && (
        <label className="row small muted" title={t('不勾选时，只统计父任务和普通问题，子任务的进度体现在父任务上')}>
          <input type="checkbox" checked={win.withSubtasks} onChange={(e) => win.setWithSubtasks(e.target.checked)} />
          {t('统计包含子任务')}
        </label>
      )}
      {days && (
        <Segmented
          value={win.days}
          onChange={win.setDays}
          items={DAY_OPTIONS.map((n) => ({ value: n, label: t('{n} 天', { n }) }))}
        />
      )}
    </div>
  )
}

export function StatTile({ label, value, sub, color }: { label: string; value: ReactNode; sub?: ReactNode; color?: string }) {
  return (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className="value" style={{ color }}>
        {value}
      </div>
      {sub && <div className="tiny faint">{sub}</div>}
    </div>
  )
}

/** A button that pops up a list of issues; picking one closes the list and opens the issue. */
export function IssueListButton({
  className,
  title,
  issues,
  total,
  empty,
  children,
}: {
  className: string
  title: ReactNode
  issues: Issue[]
  total?: number
  empty: string
  children: ReactNode
}) {
  const anchor = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(false)
  return (
    <>
      <button ref={anchor} type="button" className={`${className}${open ? ' active' : ''}`} onClick={() => setOpen(!open)}>
        {children}
      </button>
      {open && (
        <Popover anchor={anchor} onClose={() => setOpen(false)} minWidth={560}>
          <div className="signal-pop-head">
            <strong className="small ellipsis">{title}</strong>
            <span className="count">{total ?? issues.length}</span>
          </div>
          <div className="signal-pop-body" onClick={() => setOpen(false)}>
            <IssueRows issues={issues} empty={empty} compact />
          </div>
          <div className="signal-pop-foot tiny faint">
            <span className="ellipsis">{t('点击问题可打开详情并编辑')}</span>
            {total !== undefined && total > issues.length && (
              <>
                <span className="spacer" />
                <span style={{ whiteSpace: 'nowrap' }}>{t('只显示前 {n} 项，共 {total} 项', { n: issues.length, total })}</span>
              </>
            )}
          </div>
        </Popover>
      )}
    </>
  )
}
