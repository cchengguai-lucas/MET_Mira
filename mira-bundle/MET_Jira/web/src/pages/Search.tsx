import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, qs } from '../api'
import { AiFilterAssistant } from '../components/AiFilterAssistant'
import { leaveAnimation } from '../components/leave'
import { SaveFilterModal } from '../components/SaveFilterModal'
import { composeJql, HeaderFilter, isViewActive, parseView, ViewFilterBar, type Facets, type ViewField, type ViewState } from '../components/ViewFilters'
import { FieldIcon, PriorityIcon, Spinner, StatusPill, TypeIcon, UserChip, useClickOutside } from '../components/ui'
import { useApp } from '../context'
import { t } from '../i18n'
import { fmtDate, timeAgo } from '../labels'
import { ProjectBadge } from '../projectIcons'
import { useProjects } from '../queries'
import type { Issue, SavedFilter } from '../types'
import { recentlyViewedJql } from '../recentIssues'
import { FilterPageTabs, rememberSearch } from './SharedFilters'

/** Built-in sidebar filters (Jira-style defaults). */
const EXAMPLES: { label: string; jql: string | (() => string) }[] = [
  { label: '我的未处理工作项', jql: 'assignee = currentUser() AND category != done ORDER BY priority DESC' },
  { label: '我报告的', jql: 'reporter = currentUser() ORDER BY updated DESC' },
  { label: '所有工作项', jql: 'ORDER BY updated DESC' },
  { label: '未处理工作项', jql: 'category != done ORDER BY updated DESC' },
  { label: '已完成工作项', jql: 'category = done ORDER BY resolved DESC' },
  { label: '最近查看', jql: recentlyViewedJql },
  { label: '最近创立的', jql: 'created >= -30d ORDER BY created DESC' },
  { label: '近期已解决', jql: 'resolved >= -30d ORDER BY resolved DESC' },
  { label: '最近更新', jql: 'updated >= -30d ORDER BY updated DESC' },
]

type SearchCol =
  | 'type'
  | 'key'
  | 'title'
  | 'description'
  | 'machine'
  | 'status'
  | 'priority'
  | 'assignee'
  | 'reporter'
  | 'tester'
  | 'due'
  | 'updated'
  | 'labels'

interface SearchColDef {
  key: SearchCol
  label: string
  /** Preferred width; user can drag the edge to override. */
  width: number
  locked?: boolean
  /** Column-header facet filter, when the field supports it. */
  filter?: ViewField
}

const SEARCH_COLS: SearchColDef[] = [
  { key: 'type', label: '类型', locked: true, width: 44, filter: 'type' },
  { key: 'key', label: 'Key', locked: true, width: 110, filter: 'project' },
  { key: 'title', label: '标题', locked: true, width: 260 },
  { key: 'description', label: '描述', width: 240 },
  { key: 'machine', label: '机台', width: 150, filter: 'machine' },
  { key: 'status', label: '状态', width: 120, filter: 'status' },
  { key: 'priority', label: '优先级', width: 72, filter: 'priority' },
  { key: 'assignee', label: '负责人', width: 148, filter: 'assignee' },
  { key: 'reporter', label: '报告人', width: 148, filter: 'reporter' },
  { key: 'tester', label: '测试人', width: 148 },
  { key: 'due', label: '截止', width: 104 },
  { key: 'updated', label: '更新', width: 96 },
  { key: 'labels', label: '标签', width: 150, filter: 'label' },
]

const DEFAULT_ORDER = SEARCH_COLS.map((c) => c.key)
/** Optional columns hidden by default (description and labels are shown). */
const DEFAULT_HIDDEN: SearchCol[] = ['tester']
const COL_STORAGE = 'metjira.search.columns'
const ORDER_STORAGE = 'metjira.search.colOrder'
const WIDTH_STORAGE = 'metjira.search.widths'
const MIN_COL = 48
const REORDER_THRESHOLD = 6

function loadHidden(): SearchCol[] {
  try {
    const raw = localStorage.getItem(COL_STORAGE)
    if (!raw) return DEFAULT_HIDDEN
    const parsed = JSON.parse(raw) as string[]
    return parsed.filter((k): k is SearchCol => SEARCH_COLS.some((c) => c.key === k))
  } catch {
    return DEFAULT_HIDDEN
  }
}

function loadOrder(): SearchCol[] {
  try {
    const raw = localStorage.getItem(ORDER_STORAGE)
    if (!raw) return DEFAULT_ORDER
    const parsed = (JSON.parse(raw) as string[]).filter((k): k is SearchCol => SEARCH_COLS.some((c) => c.key === k))
    const missing = DEFAULT_ORDER.filter((k) => !parsed.includes(k))
    return [...parsed, ...missing]
  } catch {
    return DEFAULT_ORDER
  }
}

function loadWidths(): Partial<Record<SearchCol, number>> {
  try {
    const raw = localStorage.getItem(WIDTH_STORAGE)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as Record<string, number>
    const out: Partial<Record<SearchCol, number>> = {}
    for (const col of SEARCH_COLS) {
      const w = parsed[col.key]
      if (typeof w === 'number' && w >= MIN_COL) out[col.key] = Math.round(w)
    }
    return out
  } catch {
    return {}
  }
}

function moveCol(order: SearchCol[], from: SearchCol, to: SearchCol, place: 'before' | 'after'): SearchCol[] {
  if (from === to) return order
  const next = order.filter((k) => k !== from)
  const at = next.indexOf(to)
  if (at < 0) return order
  next.splice(place === 'before' ? at : at + 1, 0, from)
  return next
}

function plainDescription(text: string): string {
  return text
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[`*_#>]+/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function SearchColumnMenu({
  order,
  hidden,
  setHidden,
  hasCustomWidths,
  hasCustomOrder,
  resetWidths,
  resetOrder,
  resetAll,
}: {
  order: SearchCol[]
  hidden: SearchCol[]
  setHidden: (v: SearchCol[]) => void
  hasCustomWidths: boolean
  hasCustomOrder: boolean
  resetWidths: () => void
  resetOrder: () => void
  resetAll: () => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useClickOutside<HTMLDivElement>(open, () => setOpen(false))
  const toggle = (key: SearchCol) => {
    const next = hidden.includes(key) ? hidden.filter((h) => h !== key) : [...hidden, key]
    setHidden(next)
    localStorage.setItem(COL_STORAGE, JSON.stringify(next))
  }
  const ordered = order.map((key) => SEARCH_COLS.find((c) => c.key === key)!).filter(Boolean)
  return (
    <div className="popover-anchor" ref={ref}>
      <button type="button" className="btn sm" onClick={() => setOpen((o) => !o)}>
        {t('列')} ▾
      </button>
      {open && (
        <div className="menu" ref={leaveAnimation} style={{ top: '100%', right: 0, marginTop: 4, width: 240 }}>
          <div className="menu-label">{t('显示字段')}</div>
          {ordered.map((col) => (
            <label key={col.key} className="menu-item" title={col.locked ? t('必选列') : undefined}>
              <input
                type="checkbox"
                checked={!hidden.includes(col.key)}
                disabled={col.locked}
                onChange={() => !col.locked && toggle(col.key)}
              />
              {t(col.label)}
            </label>
          ))}
          <button type="button" className="menu-item" onClick={resetAll}>
            {t('恢复默认')}
          </button>
          {hasCustomWidths && (
            <button type="button" className="menu-item" onClick={resetWidths}>
              {t('恢复自动列宽')}
            </button>
          )}
          {hasCustomOrder && (
            <button type="button" className="menu-item" onClick={resetOrder}>
              {t('恢复列顺序')}
            </button>
          )}
          <div className="menu-label" style={{ marginTop: 4 }}>{t('拖动表头可调顺序，拖边缘调列宽')}</div>
        </div>
      )}
    </div>
  )
}

export function SearchPage() {
  const [params, setParams] = useSearchParams()
  const jql = params.get('jql') ?? ''
  const view = useMemo(() => parseView(params.get('view')), [params])
  const viewParam = isViewActive(view) ? JSON.stringify(view) : ''
  const [draft, setDraft] = useState(jql)
  const [page, setPage] = useState(0)
  const [hidden, setHidden] = useState<SearchCol[]>(loadHidden)
  const [order, setOrder] = useState<SearchCol[]>(loadOrder)
  const [customWidths, setCustomWidths] = useState<Partial<Record<SearchCol, number>>>(loadWidths)
  const [resizeDrag, setResizeDrag] = useState<{ key: SearchCol; width: number } | null>(null)
  const [reorder, setReorder] = useState<{ key: SearchCol; over: SearchCol | null; place: 'before' | 'after' } | null>(null)
  const { openIssue, toast } = useApp()
  const client = useQueryClient()
  const { data, isLoading, error } = useQuery({
    queryKey: ['search', jql, viewParam, page],
    queryFn: ({ signal }) => api.get<{ total: number; issues: Issue[] }>(`/api/search${qs({ jql, view: viewParam, limit: 50, offset: page * 50 })}`, signal),
    placeholderData: (previous) => previous,
    staleTime: 10_000,
  })
  const facets = useQuery({
    queryKey: ['search-facets', jql, viewParam],
    queryFn: ({ signal }) => api.get<Facets>(`/api/search/facets${qs({ jql, view: viewParam })}`, signal),
    placeholderData: (previous) => previous,
    staleTime: 15_000,
  })
  const filters = useQuery({ queryKey: ['filters'], queryFn: () => api.get<{ filters: SavedFilter[] }>('/api/filters') })
  const projects = useProjects()
  const [saving, setSaving] = useState(false)
  const [editing, setEditing] = useState<SavedFilter | null>(null)
  const search = params.toString()
  useEffect(() => rememberSearch(search), [search])
  useEffect(() => localStorage.setItem(ORDER_STORAGE, JSON.stringify(order)), [order])
  useEffect(() => localStorage.setItem(WIDTH_STORAGE, JSON.stringify(customWidths)), [customWidths])

  const visible = useMemo(
    () => order.map((key) => SEARCH_COLS.find((c) => c.key === key)!).filter((c) => c && !hidden.includes(c.key)),
    [order, hidden],
  )
  const colWidth = (key: SearchCol) =>
    resizeDrag?.key === key ? resizeDrag.width : customWidths[key] ?? SEARCH_COLS.find((c) => c.key === key)!.width
  const tableWidth = visible.reduce((sum, c) => sum + colWidth(c.key), 0)
  const hasCustomOrder = order.some((k, i) => k !== DEFAULT_ORDER[i])

  const startResize = (e: ReactPointerEvent<HTMLElement>, key: SearchCol) => {
    e.preventDefault()
    e.stopPropagation()
    const handle = e.currentTarget
    handle.setPointerCapture(e.pointerId)
    const startX = e.clientX
    const startWidth = colWidth(key)
    let width = startWidth
    setResizeDrag({ key, width })
    document.body.classList.add('col-resizing')
    const move = (ev: PointerEvent) => {
      width = Math.max(MIN_COL, Math.round(startWidth + ev.clientX - startX))
      setResizeDrag({ key, width })
    }
    const end = () => {
      handle.removeEventListener('pointermove', move)
      handle.removeEventListener('pointerup', end)
      handle.removeEventListener('pointercancel', end)
      document.body.classList.remove('col-resizing')
      setResizeDrag(null)
      if (width !== startWidth) setCustomWidths((w) => ({ ...w, [key]: width }))
    }
    handle.addEventListener('pointermove', move)
    handle.addEventListener('pointerup', end)
    handle.addEventListener('pointercancel', end)
  }

  const startReorder = (e: ReactPointerEvent<HTMLTableCellElement>, key: SearchCol) => {
    if (e.button !== 0) return
    const target = e.target as HTMLElement
    if (target.closest('.col-resizer, .th-filter-btn, button, a, input')) return
    e.preventDefault()
    const th = e.currentTarget
    th.setPointerCapture(e.pointerId)
    const startX = e.clientX
    let active = false
    let over: SearchCol | null = null
    let place: 'before' | 'after' = 'before'
    const move = (ev: PointerEvent) => {
      if (!active) {
        if (Math.abs(ev.clientX - startX) < REORDER_THRESHOLD) return
        active = true
        document.body.classList.add('col-reordering')
        setReorder({ key, over: null, place: 'before' })
      }
      const el = document.elementFromPoint(ev.clientX, ev.clientY)?.closest('th[data-col]') as HTMLElement | null
      const hit = el?.dataset.col as SearchCol | undefined
      if (!el || !hit || hit === key) {
        over = null
        setReorder({ key, over: null, place: 'before' })
        return
      }
      const rect = el.getBoundingClientRect()
      place = ev.clientX < rect.left + rect.width / 2 ? 'before' : 'after'
      over = hit
      setReorder({ key, over, place })
    }
    const end = () => {
      th.removeEventListener('pointermove', move)
      th.removeEventListener('pointerup', end)
      th.removeEventListener('pointercancel', end)
      document.body.classList.remove('col-reordering')
      if (active && over) setOrder((prev) => moveCol(prev, key, over!, place))
      setReorder(null)
    }
    th.addEventListener('pointermove', move)
    th.addEventListener('pointerup', end)
    th.addEventListener('pointercancel', end)
  }

  const resetWidths = () => setCustomWidths({})
  const resetOrder = () => setOrder(DEFAULT_ORDER)
  const resetAll = () => {
    setHidden(DEFAULT_HIDDEN)
    localStorage.setItem(COL_STORAGE, JSON.stringify(DEFAULT_HIDDEN))
    resetWidths()
    resetOrder()
  }

  const run = (value: string) => {
    setDraft(value)
    setPage(0)
    const next = new URLSearchParams(params)
    next.set('jql', value)
    next.delete('view')
    setParams(next)
  }

  const setView = (nextView: ViewState) => {
    setPage(0)
    const next = new URLSearchParams(params)
    if (isViewActive(nextView)) next.set('view', JSON.stringify(nextView))
    else next.delete('view')
    setParams(next, { replace: true })
  }

  const filterHeader = (field: ViewField, label?: ReactNode) => (
    <HeaderFilter field={field} label={label} facets={facets.data?.[field]} selected={view[field] ?? []} onChange={(values) => setView({ ...view, [field]: values })} />
  )

  const removeFilter = async (f: SavedFilter) => {
    if (!window.confirm(t('删除筛选器「{name}」？', { name: f.name }))) return
    try {
      await api.del(`/api/filters/${f.id}`)
      client.invalidateQueries({ queryKey: ['filters'] })
      client.invalidateQueries({ queryKey: ['shared-filters'] })
    } catch (err) {
      toast((err as Error).message, 'error')
    }
  }

  const sourceHint = (f: SavedFilter) =>
    f.source ? t('来自 {owner} 共享的「{name}」', { owner: f.source.owner?.display_name ?? '—', name: f.source.name }) : ''

  const today = new Date().toISOString().slice(0, 10)
  return (
    <div className="page page-wide page-full">
      <div className="page-header">
        <div>
          <h1>{t('筛选')}</h1>
        </div>
        <FilterPageTabs />
      </div>
      <div className="grid" style={{ gridTemplateColumns: '220px minmax(0, 1fr)', gap: 18, alignItems: 'start' }}>
        <div className="col" style={{ gap: 14 }}>
          <div className="card">
            <div className="card-header">
              <h3>{t('常用')}</h3>
            </div>
            <div style={{ padding: 6 }}>
              {EXAMPLES.map((example) => (
                <button
                  key={example.label}
                  className="menu-item"
                  onClick={() => run(typeof example.jql === 'function' ? example.jql() : example.jql)}
                >
                  <FieldIcon name="filter" size={13} />
                  {t(example.label)}
                </button>
              ))}
            </div>
          </div>
          <div className="card">
            <div className="card-header">
              <h3>{t('已保存的筛选器')}</h3>
            </div>
            <div style={{ padding: 6 }}>
              {filters.data?.filters.length === 0 && <div className="small faint" style={{ padding: 8 }}>{t('暂无')}</div>}
              {filters.data?.filters.map((f) => (
                <div key={f.id} className="saved-filter-row">
                  <button
                    className={`menu-item ${f.jql === jql ? 'active' : ''}`}
                    onClick={() => run(f.jql)}
                    title={[f.description, f.jql, sourceHint(f)].filter(Boolean).join('\n')}
                  >
                    <span className="ellipsis">{f.name}</span>
                    {f.scope === 'everyone' && (
                      <span className="saved-filter-scope" title={t('共享给所有人')}>
                        👥
                      </span>
                    )}
                    {f.scope === 'project' && (() => {
                      const key = f.project?.key ?? '?'
                      const proj = projects.data?.find((p) => p.key === key)
                      return (
                        <span className="saved-filter-scope project row" style={{ gap: 4 }} title={t('共享给 {key} 成员', { key })}>
                          <ProjectBadge
                            icon={proj?.icon}
                            color={proj?.color ?? '#8e8e93'}
                            label={key.slice(0, 2)}
                            size={14}
                            className="project-dot"
                          />
                          {key}
                        </span>
                      )
                    })()}
                    {f.source && (
                      <span className="saved-filter-scope" title={sourceHint(f)}>
                        ⧉
                      </span>
                    )}
                  </button>
                  <span className="saved-filter-actions">
                    <button className="btn ghost sm icon" title={t('编辑')} onClick={() => setEditing(f)}>
                      ✎
                    </button>
                    <button className="btn ghost sm icon" title={t('删除')} onClick={() => removeFilter(f)}>
                      ✕
                    </button>
                  </span>
                </div>
              ))}
              <Link to="/search/shared" className="menu-item saved-filter-browse">
                {t('浏览共享筛选器')} →
              </Link>
            </div>
          </div>
        </div>

        <div className="col" style={{ gap: 12 }}>
          <AiFilterAssistant currentJql={jql} onApply={run} />
          <div className="row">
            <input
              className="input mono"
              style={{ height: 40 }}
              placeholder={t('例如：项目 = ELEC AND 状态 in (研发中, 测试中) AND 机台组 = "CMB-1" ORDER BY updated DESC')}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && run(draft)}
            />
            <button className="btn primary" style={{ height: 40 }} onClick={() => run(draft)}>
              {t('搜索')}
            </button>
          </div>
          <div className="row small">
            {error ? (
              <span className="err-text">{(error as Error).message}</span>
            ) : isViewActive(view) && facets.data ? (
              <span className="muted">{t('显示 {n} / {total} 条结果', { n: data?.total ?? 0, total: facets.data.total })}</span>
            ) : (
              <span className="muted">{t('共 {n} 条结果', { n: data?.total ?? 0 })}</span>
            )}
            <span className="spacer" />
            <SearchColumnMenu
              order={order}
              hidden={hidden}
              setHidden={setHidden}
              hasCustomWidths={Object.keys(customWidths).length > 0}
              hasCustomOrder={hasCustomOrder}
              resetWidths={resetWidths}
              resetOrder={resetOrder}
              resetAll={resetAll}
            />
            {(jql || isViewActive(view)) && (
              <button className="btn sm" onClick={() => setSaving(true)}>
                {t('保存为筛选器')}
              </button>
            )}
            <a className="btn sm primary" href={`/api/export.xlsx${qs({ jql, view: viewParam })}`}>
              {t('导出 Excel')}
            </a>
          </div>
          <ViewFilterBar view={view} facets={facets.data} onChange={setView} />
          <div className="card search-table-wrap">
            {isLoading ? (
              <Spinner center />
            ) : (
              <table className="table search-table" style={{ width: Math.max(tableWidth, 640) }}>
                <colgroup>
                  {visible.map((c) => (
                    <col key={c.key} style={{ width: colWidth(c.key) }} />
                  ))}
                </colgroup>
                <thead>
                  <tr>
                    {visible.map((c) => {
                      const label = c.key === 'key' ? 'Key' : t(c.label)
                      const head = c.filter ? filterHeader(c.filter, c.key === 'type' ? undefined : label) : label
                      const dropping = reorder?.over === c.key
                      return (
                        <th
                          key={c.key}
                          data-col={c.key}
                          className={[
                            c.key === 'title' ? 'col-title' : '',
                            c.key === 'description' ? 'col-desc' : '',
                            resizeDrag?.key === c.key ? 'resizing' : '',
                            reorder?.key === c.key ? 'col-dragging' : '',
                            dropping && reorder?.place === 'before' ? 'col-drop-before' : '',
                            dropping && reorder?.place === 'after' ? 'col-drop-after' : '',
                          ]
                            .filter(Boolean)
                            .join(' ')}
                          title={t('拖动可调整位置；拖边缘可调列宽')}
                          onPointerDown={(e) => startReorder(e, c.key)}
                        >
                          <span className="th-label">{head}</span>
                          <span
                            className={`col-resizer ${customWidths[c.key] !== undefined ? 'custom' : ''}`}
                            title={t('拖动可调整列宽，双击恢复')}
                            onPointerDown={(e) => startResize(e, c.key)}
                            onClick={(e) => e.stopPropagation()}
                            onDoubleClick={(e) => {
                              e.stopPropagation()
                              setCustomWidths((w) => {
                                const next = { ...w }
                                delete next[c.key]
                                return next
                              })
                            }}
                          />
                        </th>
                      )
                    })}
                  </tr>
                </thead>
                <tbody>
                  {data?.issues.map((i) => {
                    const desc = plainDescription(i.description || '')
                    return (
                      <tr key={i.id} className="clickable" onClick={() => openIssue(i.key)}>
                        {visible.map((c) => {
                          switch (c.key) {
                            case 'type':
                              return (
                                <td key={c.key}>
                                  <TypeIcon type={i.type} />
                                </td>
                              )
                            case 'key':
                              return (
                                <td key={c.key} className="issue-key">
                                  {i.key}
                                </td>
                              )
                            case 'title':
                              return (
                                <td key={c.key} className="col-title">
                                  <div className="ellipsis" title={i.title}>
                                    {i.title}
                                  </div>
                                </td>
                              )
                            case 'description':
                              return (
                                <td key={c.key} className="col-desc">
                                  {desc ? (
                                    <div className="search-desc" title={desc}>
                                      {desc}
                                    </div>
                                  ) : (
                                    <span className="faint">—</span>
                                  )}
                                </td>
                              )
                            case 'machine':
                              return (
                                <td key={c.key}>
                                  <span className="chips">
                                    {i.machines.slice(0, 2).map((m, n) => (
                                      <span key={m.id} className={`machine-chip${n ? ' wide-only' : ''}`} title={`${m.group}/${t(m.name)}`}>
                                        {m.group}/{t(m.name)}
                                      </span>
                                    ))}
                                    {[1, 2].map(
                                      (shown) =>
                                        i.machines.length > shown && (
                                          <span
                                            key={shown}
                                            className={`machine-chip more ${shown === 1 ? 'narrow-only' : 'wide-only'}`}
                                            title={i.machines.slice(shown).map((m) => `${m.group}/${t(m.name)}`).join(', ')}
                                          >
                                            +{i.machines.length - shown}
                                          </span>
                                        ),
                                    )}
                                  </span>
                                </td>
                              )
                            case 'status':
                              return (
                                <td key={c.key}>
                                  <StatusPill status={i.status} />
                                </td>
                              )
                            case 'priority':
                              return (
                                <td key={c.key}>
                                  <PriorityIcon priority={i.priority} />
                                </td>
                              )
                            case 'assignee':
                              return (
                                <td key={c.key} className="col-person">
                                  {i.assignee ? <UserChip user={i.assignee} /> : <span className="faint" title={t('未分配')}>—</span>}
                                </td>
                              )
                            case 'reporter':
                              return (
                                <td key={c.key} className="col-person">
                                  <UserChip user={i.reporter} />
                                </td>
                              )
                            case 'tester':
                              return (
                                <td key={c.key} className="col-person">
                                  {i.tester ? <UserChip user={i.tester} /> : <span className="faint">—</span>}
                                </td>
                              )
                            case 'due':
                              return (
                                <td key={c.key} className={`small ${i.due_date && i.due_date < today ? 'err-text' : ''}`}>
                                  {fmtDate(i.due_date)}
                                </td>
                              )
                            case 'updated':
                              return (
                                <td key={c.key} className="small faint">
                                  {timeAgo(i.updated_at)}
                                </td>
                              )
                            case 'labels':
                              return (
                                <td key={c.key} className="small">
                                  {i.labels?.length ? (
                                    <span className="chips">
                                      {i.labels.slice(0, 3).map((lab) => (
                                        <span key={lab} className="tag">
                                          {lab}
                                        </span>
                                      ))}
                                      {i.labels.length > 3 && <span className="faint">+{i.labels.length - 3}</span>}
                                    </span>
                                  ) : (
                                    <span className="faint">—</span>
                                  )}
                                </td>
                              )
                            default:
                              return null
                          }
                        })}
                      </tr>
                    )
                  })}
                  {data?.issues.length === 0 && (
                    <tr>
                      <td colSpan={visible.length}>
                        <div className="empty">{t('没有匹配的问题')}</div>
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            )}
          </div>
          {data && data.total > 50 && (
            <div className="row" style={{ justifyContent: 'center' }}>
              <button className="btn sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
                {t('上一页')}
              </button>
              <span className="small muted">
                {page + 1} / {Math.ceil(data.total / 50)}
              </span>
              <button className="btn sm" disabled={(page + 1) * 50 >= data.total} onClick={() => setPage(page + 1)}>
                {t('下一页')}
              </button>
            </div>
          )}
        </div>
      </div>
      {saving && <SaveFilterModal jql={composeJql(jql, view)} onClose={() => setSaving(false)} />}
      {editing && <SaveFilterModal filter={editing} onClose={() => setEditing(null)} />}
    </div>
  )
}
