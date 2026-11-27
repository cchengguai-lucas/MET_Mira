import { useMemo, useRef, useState, type ReactNode } from 'react'
import { t } from '../i18n'
import { ISSUE_TYPES, PRIORITIES, priorityName, typeName } from '../labels'
import type { IssueType, Priority, UserBrief } from '../types'
import { Avatar, Popover, PriorityIcon, StatusPill, TypeIcon } from './ui'

export const VIEW_FIELDS = ['project', 'type', 'status', 'priority', 'machine', 'assignee', 'reporter', 'label'] as const
export type ViewField = (typeof VIEW_FIELDS)[number]
export type ViewState = Partial<Record<ViewField, string[]>>

/** Backend sentinel for "no machine" / "unassigned" / "no labels". */
export const EMPTY = 'EMPTY'

export interface Facet {
  value: string
  count: number
  label?: string
  group?: string
  color?: string
  user?: UserBrief
}
export type Facets = { total: number } & Record<ViewField, Facet[]>

export const VIEW_LABELS: Record<ViewField, string> = {
  project: '项目',
  type: '类型',
  status: '状态',
  priority: '优先级',
  machine: '机台',
  assignee: '负责人',
  reporter: '报告人',
  label: '标签',
}

export function parseView(raw: string | null): ViewState {
  if (!raw) return {}
  try {
    const data = JSON.parse(raw)
    const view: ViewState = {}
    for (const field of VIEW_FIELDS) if (Array.isArray(data?.[field]) && data[field].length) view[field] = data[field].map(String)
    return view
  } catch {
    return {}
  }
}

export const isViewActive = (view: ViewState) => VIEW_FIELDS.some((f) => view[f]?.length)

/** Plain-text label of one facet value, used in chips and search. */
export function facetText(field: ViewField, value: string, facet?: Facet): string {
  if (value === EMPTY) {
    if (field === 'machine') return t('无机台')
    if (field === 'label') return t('无标签')
    return t('未分配')
  }
  if (field === 'type') return typeName(value as IssueType)
  if (field === 'priority') return priorityName(value as Priority)
  if (field === 'status') return t(value)
  if (field === 'machine') {
    const [group, name] = value.split('/', 2)
    return name ? `${group}/${t(name)}` : value
  }
  if (field === 'project') return facet?.label ? `${value} · ${facet.label}` : value
  return facet?.user?.display_name ?? value
}

function facetLabel(field: ViewField, facet: Facet): ReactNode {
  const text = facetText(field, facet.value, facet)
  if (field === 'type') return <><TypeIcon type={facet.value as IssueType} size={16} /> {text}</>
  if (field === 'priority') return <><PriorityIcon priority={facet.value as Priority} /> {text}</>
  if (field === 'status' && facet.color) return <StatusPill status={{ name: facet.value, color: facet.color }} />
  if ((field === 'assignee' || field === 'reporter')) return <><Avatar user={facet.user ?? null} size={18} /> {text}</>
  return text
}

const FunnelIcon = () => (
  <svg width="11" height="11" viewBox="0 0 16 16" aria-hidden>
    <path d="M1.5 2h13l-5 6.2V13l-3 1.5V8.2z" fill="currentColor" />
  </svg>
)

/** Column-header filter: a funnel that opens a checklist of the values present in the results. */
export function HeaderFilter({
  field,
  label,
  facets,
  selected,
  onChange,
}: {
  field: ViewField
  label?: ReactNode
  facets?: Facet[]
  selected: string[]
  onChange: (values: string[]) => void
}) {
  const anchor = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const options = useMemo(() => {
    const list = [...(facets ?? [])]
    for (const value of selected) if (!list.some((f) => f.value === value)) list.push({ value, count: 0 })
    const order: readonly string[] | null = field === 'type' ? ISSUE_TYPES : field === 'priority' ? PRIORITIES : null
    if (order) list.sort((a, b) => order.indexOf(a.value) - order.indexOf(b.value))
    const q = query.trim().toLowerCase()
    return q ? list.filter((f) => facetText(field, f.value, f).toLowerCase().includes(q) || f.value.toLowerCase().includes(q)) : list
  }, [facets, selected, query, field])
  const active = selected.length > 0
  const toggle = (value: string) => onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value])
  const title = t('按「{field}」筛选当前结果', { field: t(VIEW_LABELS[field]) })

  return (
    <span className="th-filter">
      {label}
      <button
        ref={anchor}
        type="button"
        className={`th-filter-btn ${active ? 'active' : ''} ${open ? 'open' : ''}`}
        title={title}
        aria-label={title}
        onClick={(e) => {
          e.stopPropagation()
          setOpen(!open)
        }}
      >
        <FunnelIcon />
        {active && <span className="th-filter-count">{selected.length}</span>}
      </button>
      {open && (
        <Popover anchor={anchor} onClose={() => setOpen(false)} minWidth={240} className="view-filter-pop">
          <div className="view-filter-head">
            <b>{t(VIEW_LABELS[field])}</b>
            {active && (
              <button type="button" className="btn ghost sm" onClick={() => onChange([])}>
                {t('清除')}
              </button>
            )}
          </div>
          {(facets?.length ?? 0) > 8 && (
            <input className="input sm view-filter-search" autoFocus placeholder={t('搜索…')} value={query} onChange={(e) => setQuery(e.target.value)} />
          )}
          <div className="view-filter-list">
            {facets === undefined ? (
              <div className="small faint view-filter-empty">{t('加载中…')}</div>
            ) : options.length === 0 ? (
              <div className="small faint view-filter-empty">{t('当前结果中没有可选的值')}</div>
            ) : (
              options.map((f) => (
                <label key={f.value} className={`view-filter-option ${f.count === 0 ? 'zero' : ''}`}>
                  <input type="checkbox" checked={selected.includes(f.value)} onChange={() => toggle(f.value)} />
                  <span className="view-filter-label">{facetLabel(field, f)}</span>
                  <span className="view-filter-n">{f.count}</span>
                </label>
              ))
            )}
          </div>
        </Popover>
      )}
    </span>
  )
}

/** Summary of the active view filters, each removable. */
export function ViewFilterBar({ view, facets, onChange }: { view: ViewState; facets?: Facets; onChange: (view: ViewState) => void }) {
  const fields = VIEW_FIELDS.filter((f) => view[f]?.length)
  if (!fields.length) return null
  return (
    <div className="view-filter-bar">
      <span className="small muted">{t('视图筛选')}</span>
      {fields.map((field) => {
        const values = view[field]!
        const names = values.map((v) => facetText(field, v, facets?.[field]?.find((f) => f.value === v)))
        return (
          <span key={field} className="view-chip" title={names.join(', ')}>
            <span className="muted">{t('{field}：', { field: t(VIEW_LABELS[field]) })}</span>
            <span className="ellipsis">{names.join(t('、'))}</span>
            <button type="button" aria-label={t('移除')} onClick={() => onChange({ ...view, [field]: [] })}>
              ✕
            </button>
          </span>
        )
      })}
      <button type="button" className="btn ghost sm" onClick={() => onChange({})}>
        {t('清除全部')}
      </button>
    </div>
  )
}

const quote = (value: string) => `"${value.replace(/(["\\])/g, '\\$1')}"`

/** Split off a trailing ORDER BY (outside quotes) so extra clauses can be ANDed in front of it. */
function splitOrder(jql: string): [string, string] {
  let quoteChar = ''
  for (let i = 0; i < jql.length; i++) {
    const c = jql[i]
    if (quoteChar) {
      if (c === '\\') i++
      else if (c === quoteChar) quoteChar = ''
    } else if (c === '"' || c === "'") {
      quoteChar = c
    } else if (/^order\s+by\b/i.test(jql.slice(i)) && (i === 0 || /[\s)]/.test(jql[i - 1]))) {
      return [jql.slice(0, i).trim(), jql.slice(i).trim()]
    }
  }
  return [jql.trim(), '']
}

/** The query with the view filters folded in, e.g. for saving it as a filter. */
export function composeJql(jql: string, view: ViewState): string {
  const clauses: string[] = []
  for (const field of VIEW_FIELDS) {
    const values = view[field] ?? []
    if (!values.length) continue
    if (field === 'machine' || field === 'label') {
      const named = values.filter((v) => v !== EMPTY)
      const parts = [
        ...(named.length ? [`${field} in (${named.map(quote).join(', ')})`] : []),
        ...(values.includes(EMPTY) ? [`${field} is EMPTY`] : []),
      ]
      clauses.push(parts.length > 1 ? `(${parts.join(' OR ')})` : parts[0])
    } else {
      clauses.push(`${field} in (${values.map((v) => (v === EMPTY ? 'EMPTY' : quote(v))).join(', ')})`)
    }
  }
  if (!clauses.length) return jql
  const [where, order] = splitOrder(jql)
  return [[where && `(${where})`, ...clauses].filter(Boolean).join(' AND '), order].filter(Boolean).join(' ')
}
