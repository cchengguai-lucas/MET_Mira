import { useInfiniteQuery, useQueryClient, type InfiniteData } from '@tanstack/react-query'
import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError, qs } from '../api'
import { AssetPicker, ChipSelect, LabelPicker, MachinePicker, UserPicker } from '../components/Pickers'
import { SchemeSign } from '../components/SchemeSign'
import { DateField } from '../components/DateField'
import { Avatar, Dropdown, Select, PriorityIcon, Spinner, StatusPill, SubtaskBadge, TypeIcon, slidingThumb, useClickOutside, useDebounced } from '../components/ui'
import { useApp } from '../context'
import { t, tServer } from '../i18n'
import { customFieldLabel, PRIORITIES, priorityName, sectionName, typeName } from '../labels'
import { useInvalidateIssues } from '../queries'
import { copyText, useUrlState, viewLink } from '../share'
import type { Issue, ProjectDetail, Section, UserBrief } from '../types'
import { allowedStatusIds, canEditSchemeField, editableSections } from '../workflow'
import { useRowWindow } from '../components/rowWindow'
import { useProjectCtx } from './ProjectLayout'
import { leaveAnimation } from '../components/leave'

type ColKey = string

interface ColDef {
  key: ColKey
  label: string
  section: Section
  /** Preferred width in px; the sheet scrolls sideways instead of squeezing below it. */
  width: number
  /** Share of the leftover horizontal space; 0/undefined keeps the column at `width`. */
  grow?: number
  sort?: (i: Issue) => string | number
}

const SELECT_WIDTH = 36

const COLUMNS: ColDef[] = [
  { key: 'seq', label: '序号', section: 'requester', width: 160, sort: (i) => i.number },
  { key: 'reporter', label: '需求提出人', section: 'requester', width: 136, sort: (i) => i.reporter?.display_name ?? '' },
  { key: 'project_name', label: '项目名称', section: 'requester', width: 120, grow: 1, sort: (i) => i.project_name },
  { key: 'group', label: '机台组', section: 'requester', width: 90 },
  { key: 'machine', label: '机台', section: 'requester', width: 180 },
  { key: 'title', label: '需求标题', section: 'requester', width: 180, grow: 2, sort: (i) => i.title },
  { key: 'description', label: '需求说明', section: 'requester', width: 260, grow: 3 },
  { key: 'update_scope', label: '影响范围', section: 'electrical', width: 190 },
  { key: 'involves_software', label: '是否涉及软件', section: 'electrical', width: 84 },
  { key: 'software_owner', label: '软件负责人', section: 'electrical', width: 136, sort: (i) => i.software_owner?.display_name ?? '' },
  { key: 'scheme_note', label: '方案说明', section: 'scheme', width: 220, grow: 2 },
  { key: 'electrical_scheme_ok', label: '电气确认', section: 'scheme', width: 96, sort: (i) => (i.electrical_scheme_ok === true ? 1 : 0) },
  { key: 'process_scheme_ok', label: '工艺确认', section: 'scheme', width: 96, sort: (i) => (i.process_scheme_ok === true ? 1 : 0) },
  { key: 'software_scheme_ok', label: '软件确认', section: 'scheme', width: 96, sort: (i) => (i.software_scheme_ok === true ? 1 : 0) },
  { key: 'software_ready', label: '软件是否准备好', section: 'software', width: 120, sort: (i) => (i.software_ready === true ? 1 : 0) },
  { key: 'software_jira_key', label: '软件 Jira 单号', section: 'software', width: 130, sort: (i) => i.software_jira_key ?? '' },
  { key: 'verify_method', label: '验证方法', section: 'electrical', width: 170, grow: 2 },
  { key: 'verify_criteria', label: '验证标准', section: 'electrical', width: 190, grow: 2 },
  { key: 'status', label: '当前状态', section: 'general', width: 156, sort: (i) => i.status.sort_order },
  { key: 'update_date', label: '更新日期', section: 'tester', width: 104, sort: (i) => i.update_date ?? '' },
  { key: 'tester', label: '测试人', section: 'tester', width: 136 },
  { key: 'remark', label: '备注', section: 'tester', width: 150, grow: 1.5 },
  { key: 'assignee', label: '负责人', section: 'general', width: 136, sort: (i) => i.assignee?.display_name ?? '' },
  { key: 'priority', label: '优先级', section: 'general', width: 88, sort: (i) => PRIORITIES.indexOf(i.priority) },
  { key: 'due_date', label: '截止日期', section: 'general', width: 108, sort: (i) => i.due_date ?? '9999' },
  { key: 'labels', label: '标签', section: 'general', width: 160 },
]

/** Narrowest a column may be dragged to. */
const MIN_CUSTOM = 40

/**
 * Columns keep their preferred (or user-dragged) width and the sheet scrolls sideways when they do not fit;
 * on wide screens the auto-sized columns grow by `grow` weight to fill the leftover space.
 */
function fitWidths(cols: ColDef[], available: number, custom: Partial<Record<ColKey, number>>): number[] {
  const widths = cols.map((c) => custom[c.key] ?? c.width)
  if (!available) return widths
  const auto = cols.map((c, i) => (custom[c.key] === undefined ? i : -1)).filter((i) => i >= 0)
  const room = available - SELECT_WIDTH - widths.reduce((s, w) => s + w, 0)
  if (room > 0 && auto.length) {
    const weights = auto.map((i) => cols[i].grow ?? 0)
    const total = weights.reduce((s, w) => s + w, 0)
    auto.forEach((i, n) => (widths[i] += total ? (room * weights[n]) / total : room / auto.length))
  }
  const rounded = widths.map(Math.floor)
  const target = available - SELECT_WIDTH
  const sum = rounded.reduce((s, w) => s + w, 0)
  if (sum < target && sum + cols.length >= target) rounded[auto.length ? auto[auto.length - 1] : cols.length - 1] += target - sum
  return rounded
}

function useClientWidth(el: HTMLElement | null) {
  const [width, setWidth] = useState(0)
  useLayoutEffect(() => {
    if (!el) return
    const update = () => setWidth(el.clientWidth)
    update()
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => observer.disconnect()
  }, [el])
  return width
}

const HEADER_GROUPS: { id: Section; cls: string; keys: ColKey[]; label?: string }[] = [
  { id: 'requester', cls: 'sec-requester', keys: ['seq', 'reporter', 'project_name', 'group', 'machine', 'title', 'description'] },
  { id: 'electrical', cls: 'sec-electrical', keys: ['update_scope', 'involves_software', 'software_owner', 'verify_method', 'verify_criteria', 'status'] },
  { id: 'software', cls: 'sec-software', keys: ['software_ready', 'software_jira_key'] },
  { id: 'scheme', cls: 'sec-scheme', keys: ['scheme_note', 'electrical_scheme_ok', 'process_scheme_ok', 'software_scheme_ok'] },
  { id: 'tester', cls: 'sec-tester', keys: ['update_date', 'tester', 'remark'] },
  { id: 'general', cls: 'sec-general', keys: ['assignee', 'priority', 'due_date', 'labels'] },
]

function profileSheet(project: ProjectDetail) {
  const layout = project.profile.definition?.list_layout
  if (layout?.columns.length) {
    const customByKey = new Map(project.profile.custom_fields.map((field) => [field.key, field]))
    const aliases: Record<string, ColKey> = {
      key: 'seq', status_id: 'status', assignee_id: 'assignee', reporter_id: 'reporter',
      tester_id: 'tester', software_owner_id: 'software_owner', machine_ids: 'machine',
      machine_group: 'group',
    }
    const columns = layout.columns.flatMap((raw) => {
      const existingKey = aliases[raw.field] ?? raw.field as ColKey
      const existing = COLUMNS.find((column) => column.key === existingKey)
      if (existing) return [{
        ...existing, width: raw.width, grow: raw.grow,
        section: (raw.section ?? existing.section) as Section,
      }]
      const field = customByKey.get(raw.field)
      if (!field) return []
      return [{
        key: `custom:${field.key}` as ColKey,
        label: customFieldLabel(field),
        section: field.section as Section,
        width: raw.width,
        grow: raw.grow,
        sort: (issue: Issue) => String(issue.custom_fields[field.key] ?? ''),
      }]
    })
    const band: Record<string, string> = {
      requester: 'sec-profile-0', electrical: 'sec-profile-1', software: 'sec-profile-2',
      tester: 'sec-profile-0', scheme: 'sec-scheme',
    }
    const groups = project.profile.sections.map((section, index) => ({
      id: section.key as Section,
      label: sectionName(section.key, project.profile),
      cls: band[section.key] ?? `sec-profile-${index % 3}`,
      keys: columns.filter((column) => column.section === section.key).map((column) => column.key),
    })).filter((group) => group.keys.length)
    const general = columns.filter((column) => column.section === 'general').map((column) => column.key)
    if (general.length) groups.push({ id: 'general', label: t('跟踪'), cls: 'sec-general', keys: general })
    return { columns, groups }
  }
  if (project.profile.key === 'electrical') return { columns: COLUMNS, groups: HEADER_GROUPS }
  const baseKeys = ['seq', 'reporter', 'project_name', 'group', 'machine', 'title', 'description']
  const tracking = ['status', 'assignee', 'priority', 'due_date', 'labels']
  const base = COLUMNS.filter((column) => baseKeys.includes(column.key))
  const end = COLUMNS.filter((column) => tracking.includes(column.key))
  const custom: ColDef[] = project.profile.custom_fields.map((field) => ({
    key: `custom:${field.key}`,
    label: customFieldLabel(field),
    section: field.section as Section,
    width: field.kind === 'textarea' ? 220 : 140,
    grow: field.kind === 'textarea' ? 2 : undefined,
    sort: (issue) => String(issue.custom_fields[field.key] ?? ''),
  }))
  const columns = [...base, ...custom, ...end]
  const groups = project.profile.sections.map((section, index) => ({
    id: section.key as Section,
    label: sectionName(section.key, project.profile),
    cls: `sec-profile-${index % 3}`,
    keys: columns.filter((column) => column.section === section.key).map((column) => column.key),
  })).filter((group) => group.keys.length)
  groups.push({ id: 'general', label: t('跟踪'), cls: 'sec-general', keys: tracking })
  return { columns, groups }
}

function groupLabel(id: Section) {
  return id === 'general' ? t('跟踪') : sectionName(id)
}

const FIELD_OF: Partial<Record<ColKey, string>> = {
  project_name: 'project_name',
  group: 'machine_ids',
  machine: 'machine_ids',
  title: 'title',
  description: 'description',
  update_scope: 'update_scope',
  involves_software: 'involves_software',
  software_owner: 'software_owner_id',
  scheme_note: 'scheme_note',
  electrical_scheme_ok: 'electrical_scheme_ok',
  process_scheme_ok: 'process_scheme_ok',
  software_scheme_ok: 'software_scheme_ok',
  software_ready: 'software_ready',
  software_jira_key: 'software_jira_key',
  verify_method: 'verify_method',
  verify_criteria: 'verify_criteria',
  status: 'status_id',
  update_date: 'update_date',
  tester: 'tester_id',
  test_passed: 'test_passed',
  remark: 'remark',
  assignee: 'assignee_id',
  priority: 'priority',
  due_date: 'due_date',
  labels: 'labels',
}

/** Above this many rows only the rows near the viewport are mounted. */
const VIRTUAL_THRESHOLD = 150
const FETCH_LIMIT = 250
type ListResult = { total: number; issues: Issue[] }

/** Fresh users see title; hide lower-signal tracking columns by default. */
const DEFAULT_HIDDEN: ColKey[] = ['priority', 'due_date']

function triLabel(v: boolean | null, yes = t('是'), no = t('否')) {
  return v === null ? '' : v ? yes : no
}

export function ListPage() {
  const project = useProjectCtx()
  const { columns, groups: headerGroups } = useMemo(() => profileSheet(project), [project])
  const key = project.project.key
  const preferenceScope = `${key}.${project.profile.version_id ?? project.profile.key}`
  const hiddenStorage = `metjira.list.columns.${preferenceScope}`
  const widthStorage = `metjira.list.widths.${preferenceScope}`
  const { user, openIssue, toast } = useApp()
  const client = useQueryClient()
  const invalidate = useInvalidateIssues()
  const [params, update] = useUrlState()
  const onlyReq = params.get('types') === 'requirement'
  const hideDone = params.get('done') === 'hide'
  const hideSubtasks = params.get('sub') === 'hide'
  const grouped = params.get('group') !== 'none'
  const statusParam = params.get('status') ?? ''
  const statusFilter = useMemo(() => statusParam.split(',').filter(Boolean).map(Number), [statusParam])
  const sortKey = columns.find((c) => c.key === params.get('sort') && c.sort)?.key ?? 'seq'
  const sort = useMemo(() => ({ key: sortKey, dir: (params.get('dir') === 'desc' ? -1 : 1) as 1 | -1 }), [sortKey, params])
  const [text, setText] = useState(() => params.get('q') ?? '')
  const [storedHidden, setStoredHidden] = useState<ColKey[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(hiddenStorage) ?? 'null') ?? DEFAULT_HIDDEN
    } catch {
      return DEFAULT_HIDDEN
    }
  })
  const colsParam = params.get('cols')
  const hidden = useMemo(
    () => (colsParam === null ? storedHidden : colsParam.split(',').filter((k): k is ColKey => columns.some((c) => c.key === k))),
    [colsParam, storedHidden, columns],
  )
  const setHidden = (next: ColKey[]) => {
    setStoredHidden(next)
    localStorage.setItem(hiddenStorage, JSON.stringify(next))
    update({ cols: null })
  }
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [editing, setEditing] = useState<string | null>(null)
  const debounced = useDebounced(text, 300)
  const urlText = params.get('q') ?? ''
  useEffect(() => {
    if (debounced.trim() !== urlText) update({ q: debounced.trim() })
  }, [debounced, urlText, update])
  const copyView = async () => {
    const ok = await copyText(viewLink({ cols: hidden.join(',') }))
    toast(ok ? t('已复制视图链接，打开即可看到相同的筛选和列') : t('复制失败，请手动复制地址栏中的链接'), ok ? 'success' : 'error')
  }

  const jql = [
    `project = ${key}`,
    onlyReq ? 'type = requirement' : 'type != epic',
    !onlyReq && hideSubtasks ? 'type != subtask' : '',
    hideDone ? 'category != done' : '',
    debounced.trim() ? `text ~ "${debounced.trim().replace(/"/g, '')}"` : '',
  ]
    .filter(Boolean)
    .join(' AND ')
  const queryKey = ['list', key, jql]
  const {
    data,
    isLoading,
    error,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam, signal }) => api.get<ListResult>(
      `/api/search${qs({ jql, limit: FETCH_LIMIT, offset: pageParam })}`,
      signal,
    ),
    initialPageParam: 0,
    getNextPageParam: (lastPage, pages) => {
      const loaded = pages.reduce((total, page) => total + page.issues.length, 0)
      return loaded < lastPage.total ? loaded : undefined
    },
  })
  const issues = useMemo(() => data?.pages.flatMap((page) => page.issues) ?? [], [data?.pages])
  const total = data?.pages[0]?.total ?? 0

  const groupOrder = useMemo(() => {
    const order = new Map<string, number>()
    project.machine_groups.forEach((g, i) => order.set(g.name, i))
    return order
  }, [project.machine_groups])

  const rows = useMemo(() => {
    let list = issues.filter((i) => statusFilter.length === 0 || statusFilter.includes(i.status.id))
    const col = columns.find((c) => c.key === sort.key)
    const sorter = col?.sort ?? ((i: Issue) => i.number)
    list = [...list].sort((a, b) => {
      const x = sorter(a)
      const y = sorter(b)
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir
    })
    if (grouped) {
      const g = (i: Issue) => {
        const name = i.machines[0]?.group
        return name ? (groupOrder.get(name) ?? 999) : 1000
      }
      list.sort((a, b) => g(a) - g(b))
    }
    return list
  }, [issues, statusFilter, sort, grouped, groupOrder, columns])

  const visibleCols = columns.filter((c) => !hidden.includes(c.key))
  const [wrapEl, setWrapEl] = useState<HTMLDivElement | null>(null)
  const wrapWidth = useClientWidth(wrapEl)
  const [customWidths, setCustomWidths] = useState<Partial<Record<ColKey, number>>>(() => {
    try {
      return JSON.parse(localStorage.getItem(widthStorage) ?? 'null') ?? {}
    } catch {
      return {}
    }
  })
  useEffect(() => localStorage.setItem(widthStorage, JSON.stringify(customWidths)), [customWidths, widthStorage])
  /** While dragging, other columns keep the widths they had at drag start so the edge tracks the pointer. */
  const [drag, setDrag] = useState<{ key: ColKey; frozen: number[]; width: number } | null>(null)
  const fitted = fitWidths(visibleCols, wrapWidth, customWidths)
  const widths = drag ? drag.frozen.map((w, i) => (visibleCols[i].key === drag.key ? drag.width : w)) : fitted
  const tableWidth = SELECT_WIDTH + widths.reduce((s, w) => s + w, 0)

  const startResize = (e: ReactPointerEvent<HTMLElement>, col: ColKey, index: number) => {
    e.preventDefault()
    e.stopPropagation()
    const handle = e.currentTarget
    handle.setPointerCapture(e.pointerId)
    const startX = e.clientX
    const startWidth = widths[index]
    const frozen = [...widths]
    let width = startWidth
    setDrag({ key: col, frozen, width })
    document.body.classList.add('col-resizing')
    const move = (ev: PointerEvent) => {
      width = Math.max(MIN_CUSTOM, Math.round(startWidth + ev.clientX - startX))
      setDrag({ key: col, frozen, width })
    }
    const end = () => {
      handle.removeEventListener('pointermove', move)
      handle.removeEventListener('pointerup', end)
      handle.removeEventListener('pointercancel', end)
      document.body.classList.remove('col-resizing')
      setDrag(null)
      if (width !== startWidth) setCustomWidths((w) => ({ ...w, [col]: width }))
    }
    handle.addEventListener('pointermove', move)
    handle.addEventListener('pointerup', end)
    handle.addEventListener('pointercancel', end)
  }
  const resetWidth = (col: ColKey) =>
    setCustomWidths((w) => {
      const next = { ...w }
      delete next[col]
      return next
    })
  const [headEl, setHeadEl] = useState<HTMLTableSectionElement | null>(null)
  const rowKeys = useMemo(() => rows.map((r) => r.key), [rows])
  const win = useRowWindow(wrapEl, headEl, rowKeys, VIRTUAL_THRESHOLD)
  const shown = useMemo(() => rows.slice(win.start, win.end), [rows, win.start, win.end])
  /** Merged machine-group cells span only the mounted rows, so a group cut by the window stays aligned. */
  const groupSpans = useMemo(() => {
    const spans = new Map<string, number>()
    if (!grouped) return spans
    let start = 0
    for (let i = 1; i <= shown.length; i++) {
      const prev = shown[i - 1]?.machines[0]?.group ?? ''
      const cur = shown[i]?.machines[0]?.group ?? ''
      if (i === shown.length || cur !== prev) {
        spans.set(shown[start].key, i - start)
        start = i
      }
    }
    return spans
  }, [shown, grouped])

  const save = async (issue: Issue, changes: Record<string, unknown>) => {
    try {
      const updated = await api.patch<Issue>(`/api/issues/${issue.key}`, changes)
      client.setQueryData<InfiniteData<ListResult, number>>(queryKey, (old) =>
        old
          ? {
              ...old,
              pages: old.pages.map((page) => ({
                ...page,
                issues: page.issues.map((item) => (item.key === issue.key ? { ...item, ...updated } : item)),
              })),
            }
          : old,
      )
      client.invalidateQueries({ queryKey: ['issue', issue.key] })
      if (changes.test_passed !== undefined && updated.status.id !== issue.status.id) toast(t('{key} 已自动流转到「{status}」', { key: issue.key, status: t(updated.status.name) }), 'success')
      setEditing(null)
      return true
    } catch (err) {
      if (err instanceof ApiError && err.code === 'software_pending') {
        toast(tServer(err.message), 'info')
        client.invalidateQueries({ queryKey: ['issue', issue.key] })
        return false
      }
      toast((err as Error).message, 'error')
      if (err instanceof ApiError && err.code === 'transition_gate' && err.fields?.length) {
        openIssue(issue.key, { highlightFields: err.fields })
      }
      return false
    }
  }

  const addMachine = async (groupId: number, name: string) => {
    await api.post(`/api/projects/${key}/machines`, { group_id: groupId, name })
    await client.invalidateQueries({ queryKey: ['project', key] })
  }

  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.key))
  const someSelected = selected.size > 0 && !allSelected
  const exportUrl = `/api/projects/${key}/export.xlsx${qs({ jql })}`

  return (
    <div>
      <div className="board-toolbar">
        <div className="segmented" ref={slidingThumb}>
          <button className={onlyReq ? 'active' : ''} onClick={() => update({ types: 'requirement' })}>
            {typeName('requirement')}
          </button>
          <button className={!onlyReq ? 'active' : ''} onClick={() => update({ types: null })}>
            {t('全部类型')}
          </button>
        </div>
        <input className="input sm" style={{ width: 200 }} placeholder={t('搜索标题 / 说明')} value={text} onChange={(e) => setText(e.target.value)} />
        <StatusFilter project={project} value={statusFilter} onChange={(ids) => update({ status: ids.join(',') })} />
        <label className="row small">
          <input type="checkbox" checked={hideDone} onChange={(e) => update({ done: e.target.checked ? 'hide' : null })} /> {t('隐藏已完成')}
        </label>
        {!onlyReq && (
          <label className="row small">
            <input type="checkbox" checked={hideSubtasks} onChange={(e) => update({ sub: e.target.checked ? 'hide' : null })} /> {t('隐藏子任务')}
          </label>
        )}
        <label className="row small">
          <input type="checkbox" checked={grouped} onChange={(e) => update({ group: e.target.checked ? null : 'none' })} /> {t('按机台组合并')}
        </label>
        <span className="spacer" />
        {hasNextPage && (
          <button className="btn sm" disabled={isFetchingNextPage} onClick={() => fetchNextPage()}>
            {isFetchingNextPage
              ? t('加载中…')
              : t('加载更多（已加载 {n}/{total}）', { n: issues.length, total })}
          </button>
        )}
        <span className="small muted">{t('{n} 行', { n: rows.length })}</span>
        <ColumnMenu
          columns={columns}
          groups={headerGroups}
          hidden={hidden}
          setHidden={setHidden}
          hasCustomWidths={Object.keys(customWidths).length > 0}
          resetWidths={() => setCustomWidths({})}
        />
        <Link className="btn sm" to="../settings?section=import">
          {t('导入 Excel')}
        </Link>
        <a className="btn sm" href={exportUrl}>
          {t('导出 Excel')}
        </a>
        <button className="btn sm" onClick={copyView} title={t('复制包含当前筛选、排序和列设置的链接')}>
          {t('复制视图链接')}
        </button>
      </div>

      {isLoading ? (
        <Spinner center />
      ) : error ? (
        <div className="err-text">{(error as Error).message}</div>
      ) : (
        <div className="sheet-wrap" ref={setWrapEl}>
          <table className="sheet" style={{ width: tableWidth }}>
            <colgroup>
              <col style={{ width: SELECT_WIDTH }} />
              {visibleCols.map((c, i) => (
                <col key={c.key} style={{ width: widths[i] }} />
              ))}
            </colgroup>
            <thead ref={setHeadEl}>
              <tr>
                <th rowSpan={2} className="pin-select">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    aria-checked={someSelected ? 'mixed' : allSelected}
                    aria-label={t('全选')}
                    onChange={() => setSelected(allSelected ? new Set() : new Set(rows.map((r) => r.key)))}
                  />
                </th>
                {headerGroups.map((g) => {
                  const span = g.keys.filter((k) => !hidden.includes(k)).length
                  return span ? (
                    <th key={g.id} colSpan={span} className={g.cls}>
                      {g.label ?? groupLabel(g.id)}
                    </th>
                  ) : null
                })}
              </tr>
              <tr>
                {visibleCols.map((c, i) => {
                  const group = headerGroups.find((g) => g.keys.includes(c.key))!
                  return (
                    <th
                      key={c.key}
                      className={`${c.sort ? 'sortable' : ''} sub-${group.id} ${c.key === 'seq' ? 'pin-seq' : ''} ${drag?.key === c.key ? 'resizing' : ''}`}
                      title={t(c.label)}
                      aria-sort={sort.key === c.key ? (sort.dir === 1 ? 'ascending' : 'descending') : undefined}
                      tabIndex={c.sort ? 0 : undefined}
                      onClick={() => c.sort && update({ sort: c.key === 'seq' && sort.key !== 'seq' ? null : c.key, dir: sort.key === c.key && sort.dir === 1 ? 'desc' : null })}
                      onKeyDown={(event) => {
                        if (!c.sort || (event.key !== 'Enter' && event.key !== ' ')) return
                        event.preventDefault()
                        update({ sort: c.key === 'seq' && sort.key !== 'seq' ? null : c.key, dir: sort.key === c.key && sort.dir === 1 ? 'desc' : null })
                      }}
                    >
                      <span className="th-label">
                        {t(c.label)}
                        {sort.key === c.key && <span className="sort-mark">{sort.dir === 1 ? '↑' : '↓'}</span>}
                      </span>
                      <span
                        className={`col-resizer ${customWidths[c.key] !== undefined ? 'custom' : ''}`}
                        title={t('拖动可调整列宽，双击恢复自动宽度')}
                        role="separator"
                        aria-label={t('调整 {name} 列宽', { name: t(c.label) })}
                        aria-orientation="vertical"
                        tabIndex={0}
                        onPointerDown={(e) => startResize(e, c.key, i)}
                        onClick={(e) => e.stopPropagation()}
                        onDoubleClick={(e) => {
                          e.stopPropagation()
                          resetWidth(c.key)
                        }}
                        onKeyDown={(event) => {
                          if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
                          event.preventDefault()
                          event.stopPropagation()
                          const next = Math.max(MIN_CUSTOM, widths[i] + (event.key === 'ArrowRight' ? 10 : -10))
                          setCustomWidths((current) => ({ ...current, [c.key]: next }))
                        }}
                      />
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {win.padTop > 0 && (
                <tr className="sheet-spacer" aria-hidden>
                  <td colSpan={visibleCols.length + 1} style={{ height: win.padTop }} />
                </tr>
              )}
              {shown.map((issue, offset) => {
                const rowIndex = win.start + offset
                const sections = editableSections(project, user, issue)
                const span = groupSpans.get(issue.key)
                return (
                  <tr key={issue.key} ref={win.measure(issue.key)} className={selected.has(issue.key) ? 'selected' : ''}>
                    <td className="pin-select">
                      <input
                        type="checkbox"
                        checked={selected.has(issue.key)}
                        aria-label={t('选择 {key}：{title}', { key: issue.key, title: issue.title })}
                        onChange={() => {
                          const next = new Set(selected)
                          if (next.has(issue.key)) next.delete(issue.key)
                          else next.add(issue.key)
                          setSelected(next)
                        }}
                      />
                    </td>
                    {visibleCols.map((c) => {
                      if (c.key === 'group' && grouped) {
                        if (span === undefined) return null
                        return (
                          <td key={c.key} rowSpan={span} className="group-cell">
                            <div className="group-label" style={{ top: (headEl?.offsetHeight ?? 0) + 8 }}>
                              {issue.machines[0]?.group ?? <span className="faint">{t('未指定')}</span>}
                            </div>
                          </td>
                        )
                      }
                      const field = c.key.startsWith('custom:') ? `custom_fields.${c.key.slice(7)}` : FIELD_OF[c.key]
                      const section = c.key.startsWith('custom:') ? c.section : field ? sectionOf(field) : null
                      const schemeKey = field && ['scheme_note', 'electrical_scheme_ok', 'process_scheme_ok', 'software_scheme_ok'].includes(field)
                      const canEdit = schemeKey
                        ? canEditSchemeField(project, user, issue, field)
                        : !!section && sections.has(section) && !(c.key === 'status' && !sections.has('general'))
                      const id = `${issue.key}:${c.key}`
                      return (
                        <td key={c.key} data-col={c.key} className={`${c.key === 'seq' ? 'pin-seq' : ''} ${editing === id ? 'editing' : ''}`}>
                          <Cell
                            col={c.key}
                            issue={issue}
                            index={rowIndex}
                            project={project}
                            canEdit={canEdit}
                            editing={editing === id}
                            onEdit={() => canEdit && setEditing(id)}
                            onCancel={() => setEditing(null)}
                            onSave={(changes) => save(issue, changes)}
                            onOpen={() => openIssue(issue.key)}
                            onAddMachine={addMachine}
                          />
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
              {win.padBottom > 0 && (
                <tr className="sheet-spacer" aria-hidden>
                  <td colSpan={visibleCols.length + 1} style={{ height: win.padBottom }} />
                </tr>
              )}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={visibleCols.length + 1}>
                    <div className="empty">{t('没有符合条件的问题')}</div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {selected.size > 0 && (
        <BulkBar
          project={project}
          keys={[...selected]}
          onClear={() => setSelected(new Set())}
          onDone={() => {
            invalidate()
            setSelected(new Set())
          }}
        />
      )}
    </div>
  )
}

function sectionOf(field: string): Section {
  if (['title', 'description', 'project_name', 'machine_ids'].includes(field)) return 'requester'
  if (['update_scope', 'involves_software', 'software_owner_id', 'verify_method', 'verify_criteria'].includes(field)) return 'electrical'
  if (['scheme_note', 'electrical_scheme_ok', 'process_scheme_ok', 'software_scheme_ok'].includes(field)) return 'scheme'
  if (['software_ready', 'software_note', 'software_jira_key'].includes(field)) return 'software'
  if (['update_date', 'tester_id', 'test_passed', 'remark'].includes(field)) return 'tester'
  return 'general'
}

function Cell({
  col,
  issue,
  index,
  project,
  canEdit,
  editing,
  onEdit,
  onCancel,
  onSave,
  onOpen,
  onAddMachine,
}: {
  col: ColKey
  issue: Issue
  index: number
  project: ProjectDetail
  canEdit: boolean
  editing: boolean
  onEdit: () => void
  onCancel: () => void
  onSave: (changes: Record<string, unknown>) => Promise<boolean>
  onOpen: () => void
  onAddMachine: (groupId: number, name: string) => Promise<void>
}) {
  const { user } = useApp()
  const cls = `cell ${canEdit ? 'editable' : ''}`
  /** The view stays rendered under the editor so the row keeps its height while editing. */
  const withEditor = (view: ReactNode, editor: ReactNode) => (
    <>
      {view}
      {editing && editor}
    </>
  )
  const text = (field: keyof Issue, multiline = false) => {
    const value = (issue[field] as string | null) ?? ''
    const shown = value.replace(/!\[([^\]]*)\]\([^)]*\)/g, '[$1]')
    return withEditor(
      <div className={cls} onClick={onEdit} title={shown || undefined}>
        <span className={multiline ? 'clamp' : 'clamp short'}>{shown}</span>
      </div>,
      <TextEditor value={value} multiline={multiline} onSave={(v) => (v !== value ? onSave({ [field]: v }) : onCancel())} onCancel={onCancel} />,
    )
  }
  const date = (field: 'update_date' | 'due_date') => {
    const value = issue[field]?.slice(0, 10) ?? ''
    return withEditor(
      <div className={cls} onClick={onEdit}>
        <span className="ellipsis">{value}</span>
      </div>,
      <DateField
        autoOpen
        autoFocus
        value={value}
        onChange={(next) => {
          if (next !== value) onSave({ [field]: next || null })
          else onCancel()
        }}
        onBlur={onCancel}
      />,
    )
  }
  const tri = (
    field: 'involves_software' | 'test_passed' | 'software_ready' | 'electrical_scheme_ok' | 'process_scheme_ok' | 'software_scheme_ok',
    yes: string,
    no: string,
    signer?: UserBrief | null,
  ) => {
    const value = issue[field]
    return withEditor(
      <div className={cls} onClick={onEdit} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        {value !== null && <span className={value ? 'ok-text' : 'err-text'}>{triLabel(value, yes, no)}</span>}
        {value === true && signer && <SchemeSign user={signer} size={16} />}
      </div>,
      <Select className="cell-input"
        autoFocus
        defaultValue={value === null ? '' : String(value)}
        onChange={(e) => onSave({ [field]: e.target.value === '' ? null : e.target.value === 'true' })}
        onBlur={onCancel}
      >
        <option value="">—</option>
        <option value="true">{yes}</option>
        <option value="false">{no}</option>
      </Select>,
    )
  }
  const personLabel = (value: UserBrief | null) =>
    value ? (
      <>
        <Avatar user={value} size={20} />
        <span className="ellipsis">{value.display_name}</span>
      </>
    ) : null
  const person = (field: 'tester_id' | 'assignee_id' | 'software_owner_id', value: UserBrief | null, softwareOf?: string) =>
    canEdit ? (
      <UserPicker
        block
        softwareOf={softwareOf}
        project={softwareOf ? undefined : project.project.key}
        field={softwareOf ? undefined : field === 'software_owner_id' ? undefined : field}
        value={value}
        onChange={(u) => onSave({ [field]: u?.id ?? null })}
        trigger={<span className={cls}>{personLabel(value) ?? <span className="faint">—</span>}</span>}
      />
    ) : (
      <div className="cell">{personLabel(value)}</div>
    )

  if (col.startsWith('custom:')) {
    const key = col.slice(7)
    const field = project.profile.custom_fields.find((item) => item.key === key)
    const value = issue.custom_fields[key]
    if (!field) return <div className="cell" />
    if (field.kind === 'select') {
      return withEditor(
        <div className={cls} onClick={onEdit}>{String(value ?? '')}</div>,
        <Select className="cell-input" autoFocus value={String(value ?? '')}
          onChange={(e) => onSave({ custom_fields: { [key]: e.target.value } })} onBlur={onCancel}>
          <option value="">—</option>
          {field.options.map((option) => <option key={option}>{option}</option>)}
        </Select>,
      )
    }
    if (field.kind === 'boolean') {
      return withEditor(
        <div className={cls} onClick={onEdit}>{value == null ? '' : value ? t('是') : t('否')}</div>,
        <Select className="cell-input" autoFocus value={value == null ? '' : String(value)}
          onChange={(e) => onSave({ custom_fields: { [key]: e.target.value === '' ? null : e.target.value === 'true' } })} onBlur={onCancel}>
          <option value="">—</option><option value="true">{t('是')}</option><option value="false">{t('否')}</option>
        </Select>,
      )
    }
    if (field.kind === 'master_ref' && field.master_kind) {
      return <div className="cell picker">
        <AssetPicker kind={field.master_kind} multiple={field.multiple}
          disabled={!canEdit} value={(value as number | number[] | null) ?? null}
          onChange={(next) => onSave({ custom_fields: { [key]: next } })} />
      </div>
    }
    if (field.kind === 'machine') {
      return <div className="cell picker">
        <MachinePicker wrap groups={project.machine_groups}
          disabled={!canEdit} value={(value as number[] | null) ?? []}
          onChange={(next) => onSave({ custom_fields: { [key]: next } })}
          onAddMachine={onAddMachine} />
      </div>
    }
    if (field.kind === 'user') {
      const user = issue.custom_field_users?.[key] ?? null
      return canEdit ? (
        <div className="cell picker">
          <UserPicker
            block
            project={project.project.key}
            field="member"
            value={user}
            onChange={(next) => onSave({ custom_fields: { [key]: next?.id ?? null } })}
            trigger={<span className={cls}>{personLabel(user) ?? <span className="faint">—</span>}</span>}
          />
        </div>
      ) : (
        <div className="cell">{personLabel(user)}</div>
      )
    }
    if (field.kind === 'date') {
      const current = value == null ? '' : String(value).slice(0, 10)
      return withEditor(
        <div className={cls} onClick={onEdit}><span className="ellipsis">{current}</span></div>,
        <DateField
          autoOpen
          autoFocus
          value={current}
          onChange={(next) => {
            if (next !== current) onSave({ custom_fields: { [key]: next || null } })
            else onCancel()
          }}
          onBlur={onCancel}
        />,
      )
    }
    const current = value == null ? '' : String(value)
    return withEditor(
      <div className={cls} onClick={onEdit}><span className={field.kind === 'textarea' ? 'clamp' : 'clamp short'}>{current}</span></div>,
      <TextEditor value={current} multiline={field.kind === 'textarea'}
        onSave={(next) => onSave({ custom_fields: { [key]: field.kind === 'number' && next ? Number(next) : next } })}
        onCancel={onCancel} />,
    )
  }

  switch (col) {
    case 'seq':
      return (
        <div className="cell seq-cell" onClick={onOpen} title={issue.parent ? t('父任务：{key} {title}', { key: issue.parent.key, title: issue.parent.title }) : t('打开详情')}>
          <span className="seq-num">{index + 1}</span>
          <TypeIcon type={issue.type} size={14} />
          <span className="issue-key">{issue.key}</span>
          <SubtaskBadge progress={issue.subtasks} />
        </div>
      )
    case 'reporter':
      return <div className="cell">{personLabel(issue.reporter)}</div>
    case 'project_name':
      return text('project_name')
    case 'title':
      return text('title')
    case 'description':
      return text('description', true)
    case 'verify_method':
      return text('verify_method', true)
    case 'verify_criteria':
      return text('verify_criteria', true)
    case 'remark':
      return text('remark', true)
    case 'group':
    case 'machine':
      if (canEdit && col === 'machine')
        return (
          <div className="cell picker">
            <MachinePicker wrap groups={project.machine_groups} value={issue.machines.map((m) => m.id)} onChange={(ids) => onSave({ machine_ids: ids })} onAddMachine={onAddMachine} />
          </div>
        )
      return (
        <div className="cell chips">
          {col === 'group'
            ? [...new Set(issue.machines.map((m) => m.group))].join(t('、'))
            : issue.machines.map((m) => (
                <span key={m.id} className="machine-chip">
                  {t(m.name)}
                </span>
              ))}
        </div>
      )
    case 'update_scope': {
      const scope = issue.update_scope
      return withEditor(
        <div className={`${cls} chips`} onClick={onEdit} title={scope.map((s) => t(s)).join(', ') || undefined}>
          {scope.map((s) => (
            <span key={s} className="scope-chip">
              {t(s)
                .split('/')
                .map((part, i) => (
                  <Fragment key={i}>
                    {i > 0 && (
                      <>
                        /<wbr />
                      </>
                    )}
                    {part}
                  </Fragment>
                ))}
            </span>
          ))}
        </div>,
        <ScopeEditor options={project.project.settings.update_scope_options ?? []} value={issue.update_scope} onSave={(v) => onSave({ update_scope: v })} onCancel={onCancel} />,
      )
    }
    case 'labels': {
      const labs = issue.labels ?? []
      return withEditor(
        <div className={`${cls} chips`} onClick={onEdit} title={labs.join(', ') || undefined}>
          {labs.map((lab) => (
            <span key={lab} className="scope-chip">
              {lab}
            </span>
          ))}
        </div>,
        <LabelsCellEditor
          options={project.labels ?? project.project.settings.label_options ?? []}
          value={labs}
          onSave={(v) => onSave({ labels: v })}
          onCancel={onCancel}
        />,
      )
    }
    case 'involves_software':
      return tri('involves_software', t('是'), t('否'))
    case 'software_owner':
      if (issue.involves_software !== true) {
        return (
          <div className="cell" title={t('「是否涉及软件」为「是」时可指定')}>
            <span className="faint">—</span>
          </div>
        )
      }
      return person('software_owner_id', issue.software_owner, project.project.key)
    case 'software_ready':
      if (issue.involves_software !== true) {
        return <div className="cell"><span className="faint">—</span></div>
      }
      return tri('software_ready', t('是'), t('否'))
    case 'scheme_note':
      return text('scheme_note', true)
    case 'electrical_scheme_ok':
      return tri('electrical_scheme_ok', t('是'), t('否'), issue.electrical_scheme_by)
    case 'process_scheme_ok':
      return tri('process_scheme_ok', t('是'), t('否'), issue.process_scheme_by)
    case 'software_scheme_ok':
      if (issue.involves_software !== true) {
        return <div className="cell"><span className="faint">—</span></div>
      }
      return tri('software_scheme_ok', t('是'), t('否'), issue.software_scheme_by)
    case 'software_jira_key':
      if (issue.involves_software !== true) {
        return <div className="cell"><span className="faint">—</span></div>
      }
      return text('software_jira_key')
    case 'test_passed':
      return tri('test_passed', t('通过'), t('不通过'))
    case 'update_date':
      return date('update_date')
    case 'due_date':
      return date('due_date')
    case 'tester':
      return person('tester_id', issue.tester)
    case 'assignee':
      return person('assignee_id', issue.assignee)
    case 'status': {
      const allowed = editing ? allowedStatusIds(project, issue, project.statuses, user) : null
      return withEditor(
        <div className={cls} onClick={onEdit}>
          <StatusPill status={issue.status} />
        </div>,
        <Select className="cell-input" autoFocus defaultValue={issue.status.id} onChange={(e) => onSave({ status_id: Number(e.target.value) })} onBlur={onCancel}>
          {project.statuses
            .filter((s) => allowed?.has(s.id))
            .map((s) => (
              <option key={s.id} value={s.id}>
                {t(s.name)}
              </option>
            ))}
        </Select>,
      )
    }
    case 'priority':
      return withEditor(
        <div className={cls} onClick={onEdit}>
          <PriorityIcon priority={issue.priority} /> <span className="ellipsis">{priorityName(issue.priority)}</span>
        </div>,
        <Select
          className="cell-input"
          autoFocus
          defaultValue={issue.priority}
          onChange={(e) => onSave({ priority: e.target.value })}
          onBlur={onCancel}
          options={PRIORITIES.map((p) => ({
            value: p,
            label: <><PriorityIcon priority={p} />{priorityName(p)}</>,
          }))}
        />,
      )
  }
}

function TextEditor({
  value,
  multiline,
  onSave,
  onCancel,
}: {
  value: string
  multiline: boolean
  onSave: (v: string) => boolean | void | Promise<boolean | void>
  onCancel: () => void
}) {
  const [draft, setDraft] = useState(value)
  // Unmounting a focused input fires blur, which must not save after Esc or save twice after Enter.
  const done = useRef(false)
  const finish = async (save: boolean) => {
    if (done.current) return
    done.current = true
    if (!save) {
      onCancel()
      return
    }
    const result = await onSave(draft)
    if (result === false) done.current = false
  }
  const common = {
    className: 'cell-input',
    autoFocus: true,
    value: draft,
    onBlur: () => finish(true),
  }
  return multiline ? (
    <textarea
      {...common}
      className="cell-input multi"
      rows={Math.min(12, Math.max(5, draft.split('\n').length + 1))}
      onChange={(e) => setDraft(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Escape') finish(false)
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) finish(true)
      }}
    />
  ) : (
    <input
      {...common}
      onChange={(e) => setDraft(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Escape') finish(false)
        if (e.key === 'Enter') finish(true)
      }}
    />
  )
}

function ScopeEditor({ options, value, onSave, onCancel }: { options: string[]; value: string[]; onSave: (v: string[]) => void; onCancel: () => void }) {
  const [draft, setDraft] = useState(value)
  const ref = useClickOutside<HTMLDivElement>(true, () => (JSON.stringify(draft) !== JSON.stringify(value) ? onSave(draft) : onCancel()))
  return (
    <div ref={ref} className="cell-pop">
      <ChipSelect options={options} value={draft} onChange={setDraft} />
      <div className="row" style={{ marginTop: 6 }}>
        <button className="btn primary sm" onClick={() => onSave(draft)}>
          {t('确定')}
        </button>
        <button className="btn sm" onClick={onCancel}>
          {t('取消')}
        </button>
      </div>
    </div>
  )
}

function LabelsCellEditor({ options, value, onSave, onCancel }: { options: string[]; value: string[]; onSave: (v: string[]) => void; onCancel: () => void }) {
  const [draft, setDraft] = useState(value)
  const ref = useClickOutside<HTMLDivElement>(true, () => (JSON.stringify(draft) !== JSON.stringify(value) ? onSave(draft) : onCancel()))
  return (
    <div ref={ref} className="cell-pop" style={{ minWidth: 260 }}>
      <LabelPicker options={options} value={draft} onChange={setDraft} commitDelay={0} />
      <div className="row" style={{ marginTop: 6 }}>
        <button className="btn primary sm" onClick={() => onSave(draft)}>
          {t('确定')}
        </button>
        <button className="btn sm" onClick={onCancel}>
          {t('取消')}
        </button>
      </div>
    </div>
  )
}

function StatusFilter({ project, value, onChange }: { project: ProjectDetail; value: number[]; onChange: (v: number[]) => void }) {
  const [open, setOpen] = useState(false)
  const ref = useClickOutside<HTMLDivElement>(open, () => setOpen(false))
  return (
    <div className="popover-anchor" ref={ref}>
      <button className={`quick-filter ${value.length ? 'active' : ''}`} onClick={() => setOpen((o) => !o)}>
        {t('状态')}
        {value.length ? ` (${value.length})` : ''} ▾
      </button>
      {open && (
        <div className="menu" ref={leaveAnimation} style={{ top: '100%', marginTop: 4 }}>
          {project.statuses.map((s) => (
            <label key={s.id} className="menu-item">
              <input type="checkbox" checked={value.includes(s.id)} onChange={() => onChange(value.includes(s.id) ? value.filter((v) => v !== s.id) : [...value, s.id])} />
              <StatusPill status={s} />
            </label>
          ))}
          {value.length > 0 && (
            <button className="menu-item" onClick={() => onChange([])}>
              {t('清除')}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function ColumnMenu({
  columns,
  groups,
  hidden,
  setHidden,
  hasCustomWidths,
  resetWidths,
}: {
  columns: ColDef[]
  groups: { id: Section; cls: string; keys: ColKey[]; label?: string }[]
  hidden: ColKey[]
  setHidden: (v: ColKey[]) => void
  hasCustomWidths: boolean
  resetWidths: () => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useClickOutside<HTMLDivElement>(open, () => setOpen(false))
  return (
    <div className="popover-anchor" ref={ref}>
      <button className="btn sm" onClick={() => setOpen((o) => !o)}>
        {t('列')} ▾
      </button>
      {open && (
        <div className="menu" ref={leaveAnimation} style={{ top: '100%', right: 0, marginTop: 4, width: 220 }}>
          {groups.map((g) => (
            <div key={g.id}>
              <div className="menu-label">{g.label ?? groupLabel(g.id)}</div>
              {g.keys
                .filter((k) => k !== 'seq')
                .map((k) => (
                  <label key={k} className="menu-item">
                    <input type="checkbox" checked={!hidden.includes(k)} onChange={() => setHidden(hidden.includes(k) ? hidden.filter((h) => h !== k) : [...hidden, k])} />
                    {t(columns.find((c) => c.key === k)!.label)}
                  </label>
                ))}
            </div>
          ))}
          <button className="menu-item" onClick={() => setHidden(DEFAULT_HIDDEN)}>
            {t('恢复默认')}
          </button>
          {hasCustomWidths && (
            <button className="menu-item" onClick={resetWidths}>
              {t('恢复自动列宽')}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function BulkBar({ project, keys, onClear, onDone }: { project: ProjectDetail; keys: string[]; onClear: () => void; onDone: () => void }) {
  const { toast } = useApp()
  const [busy, setBusy] = useState(false)
  const apply = async (changes: Record<string, unknown>, label: string) => {
    setBusy(true)
    try {
      const res = await api.post<{ updated: { key: string }[]; failed: { key: string; error: string }[] }>('/api/issues/bulk', { keys, changes })
      if (res.failed.length)
        toast(
          t('{label}：成功 {ok}，失败 {failed}（{error}）', { label, ok: res.updated.length, failed: res.failed.length, error: tServer(res.failed[0].error) }),
          'error',
        )
      else toast(t('{label}：已更新 {n} 个', { label, n: res.updated.length }), 'success')
      onDone()
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  const menu = (label: string, options: { value: string | number; label: ReactNode }[], field: string) => (
    <Dropdown
      trigger={<button className="btn sm" disabled={busy}>{label} ▾</button>}
      options={options}
      onSelect={(v) => apply({ [field]: v === '' ? null : v }, label)}
    />
  )
  return (
    <div className="bulk-bar">
      <b>{t('已选 {n} 项', { n: keys.length })}</b>
      {menu(t('状态'), project.statuses.map((s) => ({ value: s.id, label: <StatusPill status={s} /> })), 'status_id')}
      {menu(t('优先级'), PRIORITIES.map((p) => ({ value: p, label: <><PriorityIcon priority={p} /> {priorityName(p)}</> })), 'priority')}
      {menu(t('冲刺'), [{ value: '', label: t('待办事项') }, ...project.sprints.map((s) => ({ value: s.id, label: s.name }))], 'sprint_id')}
      <UserPicker value={null} project={project.project.key} field="assignee_id" onChange={(u) => apply({ assignee_id: u?.id ?? null }, t('负责人'))} trigger={<button className="btn sm" disabled={busy}>{t('负责人')} ▾</button>} />
      <UserPicker value={null} project={project.project.key} field="tester_id" onChange={(u) => apply({ tester_id: u?.id ?? null }, t('测试人'))} trigger={<button className="btn sm" disabled={busy}>{t('测试人')} ▾</button>} />
      <span className="spacer" />
      {busy && <Spinner />}
      <button className="btn ghost sm" onClick={onClear}>
        {t('取消选择')}
      </button>
    </div>
  )
}
