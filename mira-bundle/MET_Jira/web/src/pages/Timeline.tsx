import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { CascadeDrawer, useCascade, type CascadeSuggestion } from '../components/CascadeDrawer'
import { Avatar, Empty, Segmented, Spinner, TypeIcon } from '../components/ui'
import { useApp } from '../context'
import { getLang, t } from '../i18n'
import { fmtDate, signedDaysLabel, TYPE_META } from '../labels'
import { useInvalidateIssues } from '../queries'
import type { IssueType, Priority, Status, UserBrief } from '../types'
import { projectWorkMode } from '../types'
import { isClosed } from '../workflow'
import { useProjectCtx } from './ProjectLayout'
import './Timeline.css'

interface TimelineItem {
  id: number
  key: string
  title: string
  type: IssueType
  status: Status
  priority: Priority
  assignee: UserBrief | null
  epic_key: string | null
  sprint_id: number | null
  start: string
  end: string | null
  due_date: string | null
  planned_start_date: string | null
  baseline_start: string | null
  baseline_due: string | null
  date_quality: { missing_start: boolean; missing_due: boolean }
  total?: number
  done?: number
}

interface TimelineData {
  today: string
  baseline_version: number | null
  sprints: { id: number; name: string; state: 'future' | 'active' | 'closed'; start: string | null; end: string | null }[]
  items: TimelineItem[]
}

type Zoom = 'week' | 'month' | 'quarter'
const ZOOM_PX: Record<Zoom, number> = { week: 30, month: 9, quarter: 3.2 }
const ZOOM_STORAGE = 'metjira.timeline.zoom'
const DAY = 86_400_000
const LEFT = 360
const ROW = 38

const toDay = (iso: string) => {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return Date.UTC(y, m - 1, d) / DAY
}
const dateOf = (day: number) => new Date(day * DAY)

type Row =
  | { kind: 'epic'; item: TimelineItem; open: boolean; count: number }
  | { kind: 'group'; key: string; open: boolean; count: number }
  | { kind: 'item'; item: TimelineItem; nested: boolean }

function useRows(items: TimelineItem[], query: string, hideDone: boolean, collapsed: Set<string>): Row[] {
  return useMemo(() => {
    const q = query.trim().toLowerCase()
    const keep = (i: TimelineItem) => (!hideDone || !isClosed(i.status)) && (!q || i.key.toLowerCase().includes(q) || i.title.toLowerCase().includes(q))
    const byStart = (a: TimelineItem, b: TimelineItem) => a.start.localeCompare(b.start) || a.id - b.id
    const epics = items.filter((i) => i.type === 'epic')
    const epicKeys = new Set(epics.map((e) => e.key))
    const rows: Row[] = []
    for (const epic of epics) {
      const children = items.filter((i) => i.epic_key === epic.key && keep(i)).sort(byStart)
      if (!keep(epic) && children.length === 0) continue
      const open = !collapsed.has(epic.key)
      rows.push({ kind: 'epic', item: epic, open, count: children.length })
      if (open) rows.push(...children.map((item) => ({ kind: 'item' as const, item, nested: true })))
    }
    const loose = items.filter((i) => i.type !== 'epic' && (!i.epic_key || !epicKeys.has(i.epic_key)) && keep(i)).sort(byStart)
    if (loose.length > 0) {
      const open = !collapsed.has('')
      rows.push({ kind: 'group', key: '', open, count: loose.length })
      if (open) rows.push(...loose.map((item) => ({ kind: 'item' as const, item, nested: true })))
    }
    return rows
  }, [items, query, hideDone, collapsed])
}

function Ticks({ from, days, px, zoom }: { from: number; days: number; px: number; zoom: Zoom }) {
  const locale = getLang() === 'en' ? 'en-US' : 'zh-CN'
  const monthFmt = new Intl.DateTimeFormat(locale, { year: 'numeric', month: 'short', timeZone: 'UTC' })
  const months: { left: number; width: number; label: string }[] = []
  const minor: { left: number; label: string; weekend?: boolean }[] = []
  for (let d = from; d < from + days; d++) {
    const date = dateOf(d)
    const dom = date.getUTCDate()
    if (dom === 1 || d === from) {
      const next = Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1) / DAY
      months.push({ left: (d - from) * px, width: (Math.min(next, from + days) - d) * px, label: monthFmt.format(date) })
    }
    const weekday = date.getUTCDay()
    if (zoom === 'week') minor.push({ left: (d - from) * px, label: String(dom), weekend: weekday === 0 || weekday === 6 })
    else if (zoom === 'month' && weekday === 1) minor.push({ left: (d - from) * px, label: String(dom) })
  }
  return (
    <>
      <div className="tl-months">
        {months.map((m) => (
          <div key={m.left} className="tl-month" style={{ left: m.left, width: m.width }}>
            <span>{m.label}</span>
          </div>
        ))}
      </div>
      {zoom !== 'quarter' && (
        <div className="tl-minor">
          {minor.map((m) => (
            <span key={m.left} className={`tl-tick${m.weekend ? ' weekend' : ''}`} style={{ left: m.left, width: zoom === 'week' ? px : undefined }}>
              {m.label}
            </span>
          ))}
        </div>
      )}
    </>
  )
}

function isoFromDay(day: number): string {
  const d = dateOf(day)
  const y = d.getUTCFullYear()
  const m = String(d.getUTCMonth() + 1).padStart(2, '0')
  const dd = String(d.getUTCDate()).padStart(2, '0')
  return `${y}-${m}-${dd}`
}

function Bar({
  item, from, px, today, onOpen, onDueDate, canEdit, atRisk, suggestion, focused, dimmed,
}: {
  item: TimelineItem
  from: number
  px: number
  today: number
  onOpen: () => void
  onDueDate?: (iso: string) => void
  canEdit?: boolean
  atRisk?: boolean
  suggestion?: CascadeSuggestion
  focused?: boolean
  dimmed?: boolean
}) {
  const start = toDay(item.start)
  const done = isClosed(item.status)
  const openEnded = !item.end
  const end = item.end ? toDay(item.end) : Math.max(start, today)
  const overdue = !done && !!item.due_date && toDay(item.due_date) < today
  const left = (start - from) * px
  const width = Math.max(px, (end - start + 1) * px)
  const epic = item.type === 'epic'
  const progress = epic && item.total ? Math.round(((item.done ?? 0) / item.total) * 100) : 0
  const color = epic ? TYPE_META.epic.color : item.status.color
  const style = { left, width, '--bar': color } as CSSProperties
  const baselineStart = item.baseline_start ? toDay(item.baseline_start) : null
  const baselineEnd = item.baseline_due ? toDay(item.baseline_due) : null
  const forecastEnd = suggestion?.forecast_due ? toDay(suggestion.forecast_due) : null
  const tip = [
    `${item.key} ${item.title}`,
    t(item.status.name),
    `${fmtDate(item.start)} → ${item.end ? fmtDate(item.end) : t('未设截止日期')}`,
    epic && item.total ? t('{done}/{total} 完成', { done: item.done ?? 0, total: item.total }) : '',
    item.assignee ? item.assignee.display_name : '',
    atRisk ? t('进度链风险：受上游延期影响') : '',
    suggestion ? `${t('基线')} ${suggestion.baseline_due ?? '—'} · ${t('预测')} ${suggestion.forecast_due ?? '—'} · ${signedDaysLabel(suggestion.variance_days)}` : '',
    suggestion?.constraining_predecessors.length
      ? `${t('约束前置')} ${suggestion.constraining_predecessors.join(getLang() === 'en' ? ', ' : '、')}`
      : '',
    canEdit && !epic ? t('拖动右边缘可改截止日期') : '',
  ].filter(Boolean).join('\n')
  // Always render the label inside the bar (ellipsis when short). Titles also live in the
  // sticky left column, so we never place a second label outside — that mixed layout looked inconsistent.
  const label = epic ? `${progress}%` : item.title
  const showAvatar = !epic && !!item.assignee && width >= 56

  const onResize = (e: ReactPointerEvent<HTMLSpanElement>) => {
    if (!onDueDate || !canEdit || epic) return
    e.preventDefault()
    e.stopPropagation()
    const handle = e.currentTarget
    handle.setPointerCapture(e.pointerId)
    const originX = e.clientX
    const originEnd = end
    let last = originEnd
    const move = (ev: PointerEvent) => {
      const delta = Math.round((ev.clientX - originX) / px)
      last = Math.max(start, originEnd + delta)
      const bar = handle.parentElement as HTMLElement | null
      if (bar) bar.style.width = `${Math.max(px, (last - start + 1) * px)}px`
    }
    const up = () => {
      handle.releasePointerCapture(e.pointerId)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      if (last !== originEnd || openEnded) onDueDate(isoFromDay(last))
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  return (
    <>
      {baselineStart != null && baselineEnd != null && (
        <span
          className="tl-baseline-bar"
          style={{
            left: (baselineStart - from) * px,
            width: Math.max(px, (baselineEnd - baselineStart + 1) * px),
          }}
          title={`${t('基线')} ${item.baseline_start} → ${item.baseline_due}`}
        />
      )}
      {forecastEnd != null && forecastEnd > end && (
        <span
          className="tl-forecast-bar"
          style={{ left: (end - from + 1) * px, width: Math.max(3, (forecastEnd - end) * px) }}
        >
          <span className="tl-delay-label">{signedDaysLabel(suggestion?.variance_days ?? 0)}</span>
        </span>
      )}
      <button
      type="button"
      className={`tl-bar${epic ? ' epic' : ''}${done ? ' done' : ''}${openEnded ? ' open-ended' : ''}${overdue ? ' overdue' : ''}${atRisk ? ' at-risk' : ''}${focused ? ' focused' : ''}${dimmed ? ' dimmed' : ''}`}
      style={style}
      title={tip}
      onClick={onOpen}
      data-tl-key={item.key}
    >
      {epic && <span className="tl-bar-progress" style={{ width: `${progress}%` }} />}
      <span className="tl-bar-label">{label}</span>
      {showAvatar && <Avatar user={item.assignee!} size={18} />}
      {canEdit && !epic && onDueDate && (
        <span className="tl-bar-resize" onPointerDown={onResize} title={t('拖动改截止日期')} />
      )}
      </button>
    </>
  )
}

export function TimelinePage() {
  const project = useProjectCtx()
  const key = project.project.key
  const delivery = projectWorkMode(project.project) === 'delivery'
  const { openIssue, toast } = useApp()
  const client = useQueryClient()
  const invalidate = useInvalidateIssues()
  const canEdit = project.is_admin || project.my_roles.length > 0
  const [params, setParams] = useSearchParams()
  const [zoom, setZoomState] = useState<Zoom>(() => (localStorage.getItem(ZOOM_STORAGE) as Zoom) || 'month')
  const setZoom = (value: Zoom) => {
    setZoomState(value)
    localStorage.setItem(ZOOM_STORAGE, value)
  }
  const [query, setQuery] = useState('')
  const [hideDone, setHideDone] = useState(false)
  const [riskFilter, setRiskFilter] = useState<'all' | 'critical' | 'new' | 'gate'>('all')
  const [showDeps, setShowDeps] = useState(true)
  const [cascadeOpen, setCascadeOpen] = useState(() => delivery && params.get('cascade') === '1')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const scroller = useRef<HTMLDivElement>(null)
  const { data, isLoading } = useQuery({
    queryKey: ['timeline', key],
    queryFn: () => api.get<TimelineData>(`/api/projects/${key}/timeline`),
  })
  const cascade = useCascade(key, delivery)
  const atRisk = useMemo(() => new Set(cascade.data?.at_risk_keys ?? []), [cascade.data])
  const suggestionByKey = useMemo(
    () => new Map((cascade.data?.suggestions ?? []).map((row) => [row.key, row])),
    [cascade.data],
  )
  const focusKey = params.get('focus')
  const focusedKeys = useMemo(() => {
    if (!focusKey) return new Set<string>()
    const matches = (cascade.data?.suggestions ?? []).filter((suggestion) =>
      suggestion.key === focusKey
      || suggestion.delay_sources.includes(focusKey)
      || suggestion.constraining_predecessors.includes(focusKey))
    if (!matches.length) return new Set<string>()
    return new Set(matches.flatMap((suggestion) => [
      suggestion.key,
      ...suggestion.delay_sources,
      ...suggestion.constraining_predecessors,
    ]))
  }, [cascade.data?.suggestions, focusKey])
  const filteredItems = useMemo(() => (data?.items ?? []).filter((item) => {
    const suggestion = suggestionByKey.get(item.key)
    if (riskFilter === 'critical') return suggestion?.schedule_urgency === 'critical'
    if (riskFilter === 'new') return suggestion?.risk_status === 'new' || suggestion?.risk_status === 'worsening'
    if (riskFilter === 'gate') return (suggestion?.impacted_gates.length ?? 0) > 0
    return true
  }), [data?.items, riskFilter, suggestionByKey])
  const rows = useRows(filteredItems, query, hideDone, collapsed)
  const px = ZOOM_PX[zoom]

  const saveDueDate = async (issueKey: string, due: string) => {
    try {
      await api.patch(`/api/issues/${issueKey}`, { due_date: due })
      client.setQueryData<TimelineData>(['timeline', key], (old) => {
        if (!old) return old
        return {
          ...old,
          items: old.items.map((i) => (i.key === issueKey ? { ...i, due_date: due, end: due } : i)),
        }
      })
      invalidate()
      toast(t('已更新截止日期'), 'success')
    } catch (err) {
      toast((err as Error).message, 'error')
      client.invalidateQueries({ queryKey: ['timeline', key] })
    }
  }

  const range = useMemo(() => {
    if (!data) return null
    const today = toDay(data.today)
    const days = [today]
    for (const i of data.items) {
      days.push(toDay(i.start))
      if (i.end) days.push(toDay(i.end))
      if (i.baseline_start) days.push(toDay(i.baseline_start))
      if (i.baseline_due) days.push(toDay(i.baseline_due))
      const forecast = suggestionByKey.get(i.key)?.forecast_due
      if (forecast) days.push(toDay(forecast))
    }
    for (const s of data.sprints) {
      if (s.start) days.push(toDay(s.start))
      if (s.end) days.push(toDay(s.end))
    }
    const pad = zoom === 'week' ? 7 : zoom === 'month' ? 21 : 45
    const from = Math.min(...days) - pad
    return { today, from, days: Math.max(...days) + pad * 2 - from }
  }, [data, suggestionByKey, zoom])

  const scrollToToday = (smooth = true) => {
    const el = scroller.current
    if (!el || !range) return
    el.scrollTo({ left: Math.max(0, (range.today - range.from) * px - (el.clientWidth - LEFT) / 3), behavior: smooth ? 'smooth' : 'auto' })
  }
  useLayoutEffect(() => scrollToToday(false), [range?.from, px])

  if (isLoading || !data || !range) return <Spinner center />
  const width = range.days * px
  const todayX = (range.today - range.from) * px
  const toggle = (k: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })
  const groups = ['', ...data.items.filter((i) => i.type === 'epic').map((e) => e.key)]
  const allCollapsed = groups.every((g) => collapsed.has(g))

  const openCascade = () => {
    setCascadeOpen(true)
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set('cascade', '1')
      return next
    })
  }
  const closeCascade = () => {
    setCascadeOpen(false)
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      next.delete('cascade')
      return next
    })
  }
  const focusCascadeIssue = (issueKey: string) => {
    setCascadeOpen(false)
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      next.delete('cascade')
      next.set('focus', issueKey)
      return next
    })
  }
  const clearFocus = () => {
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      next.delete('focus')
      return next
    })
  }
  const focusItem = focusKey ? data.items.find((item) => item.key === focusKey) : undefined

  return (
    <div className="timeline-page">
      <div className="row wrap tl-toolbar">
        <input className="input sm" style={{ width: 220 }} placeholder={t('筛选时间线')} value={query} onChange={(e) => setQuery(e.target.value)} />
        <label className="row small muted">
          <input type="checkbox" checked={hideDone} onChange={(e) => setHideDone(e.target.checked)} />
          {t('隐藏已完成')}
        </label>
        {delivery && (
          <Segmented
            value={riskFilter}
            onChange={(value) => setRiskFilter(value as typeof riskFilter)}
            items={[
              { value: 'all', label: t('全部') },
              { value: 'critical', label: t('关键风险') },
              { value: 'new', label: t('新增/恶化') },
              { value: 'gate', label: t('门节点') },
            ]}
          />
        )}
        {delivery && (
          <label className="row small muted">
            <input type="checkbox" checked={showDeps} onChange={(e) => setShowDeps(e.target.checked)} />
            {t('显示依赖')}
          </label>
        )}
        <button type="button" className="btn sm" onClick={() => setCollapsed(allCollapsed ? new Set() : new Set(groups))}>
          {allCollapsed ? t('全部展开') : t('全部收起')}
        </button>
        {delivery && (cascade.data?.suggestion_count ?? 0) > 0 && (
          <button type="button" className="btn sm warning solid" onClick={openCascade}>
            {t('改期建议 {n}', { n: cascade.data?.suggestion_count })}
          </button>
        )}
        <span className="spacer" />
        <div className="legend tl-legend">
          <span><i className="tl-swatch open-ended" />{t('未设截止日期')}</span>
          <span><i className="tl-swatch overdue" />{t('已逾期')}</span>
          {delivery && <span><i className="tl-swatch at-risk" />{t('进度链风险')}</span>}
          {delivery && <span><i className="tl-swatch baseline" />{t('基线')}</span>}
          {delivery && <span><i className="tl-swatch forecast" />{t('预测')}</span>}
        </div>
        <button type="button" className="btn sm" onClick={() => scrollToToday()}>
          {t('今天')}
        </button>
        <Segmented
          value={zoom}
          onChange={(v) => setZoom(v as Zoom)}
          items={[
            { value: 'week', label: t('周') },
            { value: 'month', label: t('月') },
            { value: 'quarter', label: t('季度') },
          ]}
        />
      </div>

      {focusKey ? (
        <div className="tl-focus-banner" role="status">
          <div className="tl-focus-banner-text">
            <span className="tl-focus-label">{t('当前关注')}</span>
            <button type="button" className="tl-focus-issue" onClick={() => openIssue(focusKey)}>
              <span className="issue-key">{focusKey}</span>
              {focusItem?.title ? <span className="ellipsis">{focusItem.title}</span> : null}
            </button>
            {focusedKeys.size > 1 ? (
              <span className="small muted">{t('高亮影响链 {n} 项', { n: focusedKeys.size })}</span>
            ) : null}
          </div>
          <button type="button" className="btn sm" onClick={clearFocus}>{t('取消关注')}</button>
        </div>
      ) : null}

      <div className="card tl-scroller" ref={scroller}>
        <div className="tl-canvas" style={{ width: LEFT + width }}>
          <div className="tl-head">
            <div className="tl-left tl-head-left">
              <span>{t('工作')}</span>
              <span className="tiny faint">{t('{n} 项', { n: rows.filter((r) => r.kind !== 'group').length })}</span>
            </div>
            <div className="tl-right" style={{ width }}>
              <Ticks from={range.from} days={range.days} px={px} zoom={zoom} />
              <span className="tl-today-flag" style={{ left: todayX }}>{t('今天')}</span>
              <div className="tl-sprints">
                {data.sprints.filter((s) => s.start && s.end).map((s) => (
                  <span
                    key={s.id}
                    className={`tl-sprint ${s.state}`}
                    style={{ left: (toDay(s.start!) - range.from) * px, width: Math.max(px, (toDay(s.end!) - toDay(s.start!) + 1) * px) }}
                    title={`${s.name} · ${fmtDate(s.start)} → ${fmtDate(s.end)}`}
                  >
                    {s.name}
                  </span>
                ))}
              </div>
            </div>
          </div>

          {rows.length === 0 ? (
            <div style={{ position: 'sticky', left: 0, width: 'min(100%, 100vw)' }}>
              <Empty title={t('没有可显示的工作')}>{t('调整筛选条件，或为问题设置截止日期后再查看')}</Empty>
            </div>
          ) : (
            rows.map((row) => {
              if (row.kind === 'group') {
                return (
                  <div key="group-none" className="tl-row tl-group" style={{ height: ROW }}>
                    <button type="button" className="tl-left tl-label" onClick={() => toggle(row.key)}>
                      <span className={`tl-chevron${row.open ? ' open' : ''}`}>›</span>
                      <span className="ellipsis faint">{t('未归属史诗')}</span>
                      <span className="tl-count">{row.count}</span>
                    </button>
                    <div className="tl-right" style={{ width }} />
                  </div>
                )
              }
              const item = row.item
              return (
                <div key={item.key} className={`tl-row${row.kind === 'epic' ? ' tl-epic' : ''}`} style={{ height: ROW }}>
                  <div className="tl-left tl-label" style={{ paddingLeft: row.kind === 'item' && row.nested ? 34 : 10 }}>
                    {row.kind === 'epic' ? (
                      <button type="button" className={`tl-chevron${row.open ? ' open' : ''}`} onClick={() => toggle(item.key)} aria-label={row.open ? t('收起') : t('展开')}>
                        ›
                      </button>
                    ) : null}
                    <TypeIcon type={item.type} size={16} />
                    <button type="button" className="tl-title" onClick={() => openIssue(item.key)}>
                      <span className="issue-key">{item.key}</span>
                      <span className="ellipsis">{item.title}</span>
                    </button>
                    {row.kind === 'epic' ? (
                      <span className="tl-count" title={t('{done}/{total} 完成', { done: item.done ?? 0, total: item.total ?? 0 })}>
                        {item.done ?? 0}/{item.total ?? 0}
                      </span>
                    ) : (
                      <>
                        {(item.date_quality.missing_start || item.date_quality.missing_due) && <span className="tl-date-warning" title={t('缺少计划日期，时间条使用了回退日期')}>!</span>}
                        <span className="tl-status" style={{ color: item.status.color, background: `color-mix(in srgb, ${item.status.color} 13%, transparent)` }}>
                          {t(item.status.name)}
                        </span>
                      </>
                    )}
                  </div>
                  <div className="tl-right" style={{ width }}>
                    <Bar
                      item={item}
                      from={range.from}
                      px={px}
                      today={range.today}
                      onOpen={() => openIssue(item.key)}
                      canEdit={canEdit && !isClosed(item.status)}
                      onDueDate={(due) => saveDueDate(item.key, due)}
                      atRisk={atRisk.has(item.key)}
                      suggestion={suggestionByKey.get(item.key)}
                      focused={focusedKeys.has(item.key)}
                      dimmed={focusedKeys.size > 0 && !focusedKeys.has(item.key)}
                    />
                  </div>
                </div>
              )
            })
          )}
          {showDeps && cascade.data?.edges && cascade.data.edges.length > 0 && (
            <DependencyArrows
              edges={cascade.data.edges}
              items={data.items}
              rows={rows}
              from={range.from}
              px={px}
              today={range.today}
              leftOffset={LEFT}
              width={width}
              focusedKeys={focusedKeys}
            />
          )}
          <div className="tl-today" style={{ left: LEFT + todayX }} />
        </div>
      </div>
      {!!cascade.data?.data_quality.warnings.length && (
        <div className="callout warning tl-quality">
          <b>{t('计划数据质量')}</b>
          {cascade.data.data_quality.warnings.map((warning) => <span className="small" key={warning}>• {warning}</span>)}
        </div>
      )}
      <CascadeDrawer
        projectKey={key}
        open={cascadeOpen}
        onClose={closeCascade}
        onFocusIssue={focusCascadeIssue}
      />
    </div>
  )
}

function DependencyArrows({
  edges,
  items,
  rows,
  from,
  px,
  today,
  leftOffset,
  width,
  focusedKeys,
}: {
  edges: { id?: number; source_key: string; target_key: string; lag_days?: number; in_cycle?: boolean }[]
  items: TimelineItem[]
  rows: Row[]
  from: number
  px: number
  today: number
  leftOffset: number
  width: number
  focusedKeys: Set<string>
}) {
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null)
  const byKey = useMemo(() => new Map(items.map((i) => [i.key, i])), [items])
  const rowIndex = useMemo(() => {
    const map = new Map<string, number>()
    rows.forEach((row, idx) => {
      if (row.kind !== 'group') map.set(row.item.key, idx)
    })
    return map
  }, [rows])
  const height = rows.length * ROW
  const paths: {
    key: string
    d: string
    x: number
    y: number
    edge: { source_key: string; target_key: string; lag_days?: number; in_cycle?: boolean }
  }[] = []
  for (const edge of edges) {
    const src = byKey.get(edge.source_key)
    const tgt = byKey.get(edge.target_key)
    const si = rowIndex.get(edge.source_key)
    const ti = rowIndex.get(edge.target_key)
    if (!src || !tgt || si == null || ti == null) continue
    const sStart = toDay(src.start)
    const sEnd = src.end ? toDay(src.end) : Math.max(sStart, today)
    const tStart = toDay(tgt.start)
    const x1 = leftOffset + (sEnd - from + 1) * px
    const y1 = si * ROW + ROW / 2
    const x2 = leftOffset + (tStart - from) * px
    const y2 = ti * ROW + ROW / 2
    const mid = (x1 + x2) / 2
    paths.push({
      key: `${edge.source_key}:${edge.target_key}`,
      d: `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`,
      x: mid,
      y: (y1 + y2) / 2,
      edge,
    })
  }
  if (!paths.length) return null
  return (
    <svg className="tl-deps" width={leftOffset + width} height={height} style={{ position: 'absolute', left: 0, top: 78 }}>
      <defs>
        <marker id="tl-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--text-3)" />
        </marker>
      </defs>
      {paths.map((path) => {
        const highlighted = focusedKeys.size === 0 || (
          focusedKeys.has(path.edge.source_key) && focusedKeys.has(path.edge.target_key)
        )
        return <g key={path.key} className={highlighted ? 'focused' : 'dimmed'} onClick={() => setSelectedEdge(selectedEdge === path.key ? null : path.key)}>
          <path className="tl-dep-hit" d={path.d} />
          <path className={`tl-dep-line${path.edge.in_cycle ? ' cycle' : ''}`} d={path.d} markerEnd="url(#tl-arrow)" />
          {selectedEdge === path.key && <foreignObject x={path.x - 100} y={Math.max(0, path.y - 34)} width="210" height="68">
            <div className="tl-dep-popover">
              <b>{path.edge.source_key} → {path.edge.target_key}</b>
              <span>{t('Finish-to-Start')} · lag {path.edge.lag_days ?? 1}d</span>
              <span>{path.edge.in_cycle ? t('循环依赖，未参与预测') : t('前置完成后约束后继开始')}</span>
            </div>
          </foreignObject>}
        </g>
      })}
    </svg>
  )
}
