import {
  closestCenter,
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MeasuringStrategy,
  PointerSensor,
  pointerWithin,
  rectIntersection,
  getFirstCollision,
  useDroppable,
  useSensor,
  useSensors,
  type CollisionDetection,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
  type UniqueIdentifier,
} from '@dnd-kit/core'
import {
  SortableContext,
  arrayMove,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiError, qs } from '../api'
import { UserPicker } from '../components/Pickers'
import { PersonHover } from '../components/PersonHover'
import { ReopenReasonModal } from '../components/ReopenReasonModal'
import { Avatar, Dropdown, Empty, InfoTip, PriorityIcon, Spinner, SubtaskBadge, TypeIcon, useDebounced, Select } from '../components/ui'
import { useApp } from '../context'
import { locale, t, tServer } from '../i18n'
import { epicAccent, fmtDate, PRIORITIES, PRIORITY_META, priorityName } from '../labels'
import { useInvalidateIssues } from '../queries'
import { copyText, useUrlState, viewLink } from '../share'
import { withSubtaskClose } from '../subtasks'
import type { Board, BoardColumn, Issue, Priority, Sprint, Status, UserBrief } from '../types'
import { allowedStatusIds, editableSections, REOPENED } from '../workflow'
import { useProjectCtx } from './ProjectLayout'

/** Match dnd-kit MultipleContainers: keep layout transitions while sorting. */
const cardTransition = { duration: 200, easing: 'cubic-bezier(0.25, 1, 0.5, 1)' }
const dropAnimation = { duration: 200, easing: 'cubic-bezier(0.25, 1, 0.5, 1)' }

type CellLists = Record<string, string[]>

function cellId(col: number, lane: string) {
  return `cell:${col}:${lane}`
}

function parseCellId(id: string): [number, string] | null {
  if (!id.startsWith('cell:')) return null
  const rest = id.slice(5)
  const i = rest.indexOf(':')
  if (i < 0) return Number.isFinite(Number(rest)) ? [Number(rest), 'all'] : null
  const col = Number(rest.slice(0, i))
  return Number.isFinite(col) ? [col, rest.slice(i + 1)] : null
}

function cloneCells(cells: CellLists): CellLists {
  return Object.fromEntries(Object.entries(cells).map(([k, v]) => [k, [...v]]))
}

function buildCellLists(issues: Issue[], columns: BoardColumn[], swimlane: Board['swimlane']): CellLists {
  const cells: CellLists = {}
  for (const issue of issues) {
    const col = columns.findIndex((c) => c.status_ids.includes(issue.status.id))
    if (col < 0) continue
    const key = cellId(col, laneOf(issue, swimlane).id)
    ;(cells[key] ??= []).push(issue.key)
  }
  return cells
}

function findContainer(items: CellLists, id: UniqueIdentifier): string | undefined {
  const sid = String(id)
  if (sid in items) return sid
  return Object.keys(items).find((key) => items[key].includes(sid))
}

function placeIssue(
  old: BoardData,
  issue: Issue,
  status: Status,
  beforeKey: string | null,
  afterKey: string | null,
): BoardData {
  const others = old.issues.filter((i) => i.key !== issue.key)
  const moved = { ...issue, status }
  const anchor = afterKey
    ? others.findIndex((i) => i.key === afterKey)
    : beforeKey
      ? others.findIndex((i) => i.key === beforeKey) + 1
      : others.length
  others.splice(anchor < 0 ? others.length : anchor, 0, moved)
  return { ...old, issues: others }
}

interface BoardData {
  board: Board
  statuses: Status[]
  sprint: Sprint | null
  issues: Issue[]
  can_configure: boolean
  aging?: { warn: number; danger: number; since: Record<string, string> }
}

type Stay = { days: number; level: 'warn' | 'danger' }

const DAY_MS = 86_400_000

function stayOf(issue: Issue, aging: BoardData['aging']): Stay | null {
  const since = aging?.since[issue.id]
  if (!aging || !since || issue.status.category === 'done') return null
  const days = Math.floor((Date.now() - Date.parse(since)) / DAY_MS)
  if (days >= aging.danger) return { days, level: 'danger' }
  return days >= aging.warn ? { days, level: 'warn' } : null
}

type Lane = { id: string; title: string; user?: UserBrief | null }

function laneOf(issue: Issue, swimlane: Board['swimlane']): Lane {
  switch (swimlane) {
    case 'assignee':
      return { id: `a${issue.assignee?.id ?? 0}`, title: issue.assignee?.display_name ?? t('未分配'), user: issue.assignee }
    case 'group': {
      const group = issue.machines[0]?.group
      return { id: `g${group ?? ''}`, title: group ?? t('未指定机台') }
    }
    case 'epic':
      return { id: `e${issue.epic?.id ?? 0}`, title: issue.epic ? `⚡ ${issue.epic.title}` : t('无史诗') }
    case 'priority':
      return { id: `p${issue.priority}`, title: `${PRIORITY_META[issue.priority].arrow} ${priorityName(issue.priority)}` }
    default:
      return { id: 'all', title: '' }
  }
}

const SUBTASKS_STORAGE = 'metjira.board.subtasks'
const SWIMLANES: Board['swimlane'][] = ['none', 'assignee', 'group', 'epic', 'priority']
const EMPTY_COLUMNS: BoardColumn[] = []

function boardLabel(name: string): string {
  const base = name.match(/^(.+) 看板$/)?.[1]
  return base ? t('{name} 看板', { name: base }) : name
}

export function BoardPage() {
  const project = useProjectCtx()
  const { openIssue, openCreate, toast, user } = useApp()
  const navigate = useNavigate()
  const client = useQueryClient()
  const invalidate = useInvalidateIssues()
  const [params, update] = useUrlState()
  const boardId = Number(params.get('board')) || project.boards.find((b) => b.kind === 'kanban')?.id || project.boards[0]?.id
  const active = params.getAll('qf')
  const who = params.get('who') ?? ''
  const people = useMemo(() => who.split(',').filter(Boolean).map(Number), [who])
  const lane = params.get('lane') as Board['swimlane'] | null
  const swimOverride = lane && SWIMLANES.includes(lane) ? lane : null
  const hideSubtasks = params.has('sub') ? params.get('sub') === 'hide' : localStorage.getItem(SUBTASKS_STORAGE) === 'hide'
  const [text, setText] = useState(() => params.get('q') ?? '')
  /** Snapshot of cell→keys for the active drag; card stays in the source list until drop. */
  const [items, setItems] = useState<CellLists | null>(null)
  const [dragging, setDragging] = useState<Issue | null>(null)
  /** Cross-column insert marker — thin line, not a full card slot (avoids column height thrash). */
  const [insertHint, setInsertHint] = useState<{ container: string; index: number } | null>(null)
  /** Freeze column pixel widths while dragging so "→" titles / content cannot reflow the board. */
  const [frozenWidths, setFrozenWidths] = useState<number[] | null>(null)
  const [reopenDrop, setReopenDrop] = useState<{
    issue: Issue
    statusId: number
    before: string | null
    after: string | null
    newStatus: Status
  } | null>(null)
  const boardRowRef = useRef<HTMLDivElement | null>(null)
  const clonedItems = useRef<CellLists | null>(null)
  /** Full board payload at drag start — restore on failed writes without refetch flash. */
  const boardSnapshot = useRef<BoardData | null>(null)
  const lastOverId = useRef<UniqueIdentifier | null>(null)
  const recentlyMovedToNewContainer = useRef(false)
  const debouncedText = useDebounced(text, 250)
  const urlText = params.get('q') ?? ''
  useEffect(() => {
    if (debouncedText.trim() !== urlText) update({ q: debouncedText.trim() })
  }, [debouncedText, urlText, update])
  const setPeople = (ids: number[]) => update({ who: ids.join(',') })
  const setHideSubtasks = (hide: boolean) => {
    localStorage.setItem(SUBTASKS_STORAGE, hide ? 'hide' : 'show')
    update({ sub: hide ? 'hide' : 'show' })
  }
  const copyView = async () => {
    const ok = await copyText(viewLink(params.has('sub') ? {} : { sub: hideSubtasks ? 'hide' : 'show' }))
    toast(ok ? t('已复制视图链接，打开即可看到相同的筛选') : t('复制失败，请手动复制地址栏中的链接'), ok ? 'success' : 'error')
  }

  const queryKey = ['board', boardId, active]
  const { data, isLoading, isFetching, error } = useQuery({
    queryKey,
    queryFn: async ({ client: qc, signal }) => {
      // Reuse quick_filters from any cached board payload so filter toggles need only one GET.
      let filterDefs: BoardData['board']['quick_filters'] | undefined
      for (const [, cached] of qc.getQueriesData<BoardData>({ queryKey: ['board', boardId] })) {
        if (cached?.board.quick_filters) {
          filterDefs = cached.board.quick_filters
          break
        }
      }
      const jqlFrom = (defs: BoardData['board']['quick_filters']) => {
        const parts = defs.filter((f) => active.includes(f.name)).map((f) => `(${f.jql})`)
        return parts.length ? parts.join(' AND ') : undefined
      }
      if (filterDefs) {
        return api.get<BoardData>(`/api/boards/${boardId}${qs({ jql: jqlFrom(filterDefs) })}`, signal)
      }
      const board = await api.get<BoardData>(`/api/boards/${boardId}`, signal)
      const jql = jqlFrom(board.board.quick_filters)
      if (!jql) return board
      return api.get<BoardData>(`/api/boards/${boardId}${qs({ jql })}`, signal)
    },
    enabled: !!boardId,
    placeholderData: (previous) => previous,
  })

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }), useSensor(KeyboardSensor))
  const swimlane = swimOverride ?? data?.board.swimlane ?? 'none'

  const visible = useMemo(() => {
    const q = debouncedText.trim().toLowerCase()
    return (data?.issues ?? []).filter(
      (i) =>
        (!hideSubtasks || i.type !== 'subtask') &&
        (!q || i.title.toLowerCase().includes(q) || i.key.toLowerCase().includes(q)) &&
        (people.length === 0 || people.includes(i.assignee?.id ?? 0)),
    )
  }, [data?.issues, debouncedText, people, hideSubtasks])
  const subtaskCount = useMemo(() => (data?.issues ?? []).filter((i) => i.type === 'subtask').length, [data?.issues])

  const assignees = useMemo(() => {
    const map = new Map<number, UserBrief | null>()
    for (const i of data?.issues ?? []) map.set(i.assignee?.id ?? 0, i.assignee)
    return [...map.entries()].slice(0, 14)
  }, [data?.issues])

  const lanes = useMemo(() => {
    const map = new Map<string, Lane>()
    for (const issue of visible) {
      const lane = laneOf(issue, swimlane)
      if (!map.has(lane.id)) map.set(lane.id, lane)
    }
    const list = [...map.values()]
    if (swimlane === 'priority') list.sort((a, b) => PRIORITIES.indexOf(a.id.slice(1) as never) - PRIORITIES.indexOf(b.id.slice(1) as never))
    else list.sort((a, b) => (a.id.endsWith('0') || a.id === 'g' ? 1 : 0) - (b.id.endsWith('0') || b.id === 'g' ? 1 : 0) || a.title.localeCompare(b.title, locale()))
    return list.length ? list : [{ id: 'all', title: '' }]
  }, [visible, swimlane])

  const allowed = useMemo(
    () => (dragging && data ? allowedStatusIds(project, dragging, data.statuses, user) : null),
    [dragging, data, project, user],
  )

  const columns = data?.board.columns ?? EMPTY_COLUMNS
  const byKey = useMemo(() => new Map(visible.map((i) => [i.key, i])), [visible])
  const savedCells = useMemo(() => buildCellLists(visible, columns, swimlane), [visible, columns, swimlane])
  const liveCells = items ?? savedCells

  // Ensure every visible column/lane has a droppable list key (empty columns included).
  const boardItems = useMemo(() => {
    const next = cloneCells(liveCells)
    for (const lane of lanes) {
      for (let col = 0; col < columns.length; col++) {
        const id = cellId(col, lane.id)
        if (!(id in next)) next[id] = []
      }
    }
    return next
  }, [liveCells, lanes, columns])

  /** One pass → O(1) cell / column / lane lookups while rendering (honours live drag order). */
  const index = useMemo(() => {
    const byCell = new Map<string, Issue[]>()
    const byCol = new Map<number, number>()
    const byLane = new Map<string, number>()
    const stayById = new Map<number, Stay>()
    for (const [key, keys] of Object.entries(boardItems)) {
      const issues = keys.flatMap((k) => {
        const issue = byKey.get(k)
        return issue ? [issue] : []
      })
      byCell.set(key, issues)
      const parsed = parseCellId(key)
      if (!parsed) continue
      const [col, lane] = parsed
      byCol.set(col, (byCol.get(col) ?? 0) + issues.length)
      byLane.set(lane, (byLane.get(lane) ?? 0) + issues.length)
      for (const issue of issues) {
        const stay = stayOf(issue, data?.aging)
        if (stay) stayById.set(issue.id, stay)
      }
    }
    return { byCell, byCol, byLane, stayById }
  }, [boardItems, byKey, data?.aging])

  useEffect(() => {
    requestAnimationFrame(() => {
      recentlyMovedToNewContainer.current = false
    })
  }, [boardItems])

  /** Official multi-container collision: hit column, then closest card inside it. */
  const collisionDetection: CollisionDetection = useCallback(
    (args) => {
      const pointerIntersections = pointerWithin(args)
      const intersections = pointerIntersections.length > 0 ? pointerIntersections : rectIntersection(args)
      let overId = getFirstCollision(intersections, 'id')
      if (overId != null) {
        if (String(overId) in boardItems) {
          const containerItems = boardItems[String(overId)]
          if (containerItems.length > 0) {
            overId =
              closestCenter({
                ...args,
                droppableContainers: args.droppableContainers.filter(
                  (c) => c.id !== overId && containerItems.includes(String(c.id)),
                ),
              })[0]?.id ?? overId
          }
        }
        lastOverId.current = overId
        return [{ id: overId }]
      }
      if (recentlyMovedToNewContainer.current) {
        lastOverId.current = dragging?.key ?? null
      }
      return lastOverId.current ? [{ id: lastOverId.current }] : []
    },
    [boardItems, dragging],
  )

  /** Inline card edits (assignee / priority) — keep board cache, soft-sync other views. */
  const patchCard = useCallback(
    async (issue: Issue, changes: Record<string, unknown>) => {
      try {
        const updated = await api.patch<Issue>(`/api/issues/${issue.key}`, changes)
        client.setQueryData<BoardData>(queryKey, (old) =>
          old ? { ...old, issues: old.issues.map((i) => (i.key === issue.key ? { ...i, ...updated } : i)) } : old,
        )
        for (const qk of ['backlog', 'search', 'dashboard', 'list', 'reports', 'project-issues'] as const) {
          client.invalidateQueries({ queryKey: [qk] })
        }
        client.invalidateQueries({ queryKey: ['issue', issue.key] })
      } catch (err) {
        toast((err as Error).message, 'error')
      }
    },
    [client, queryKey, toast],
  )

  if (!boardId) return <Empty title={t('项目没有看板')} />
  if (isLoading && !data) return <Spinner center />
  if (error && !data) return <Empty title={t('看板加载失败')}>{(error as Error)?.message}</Empty>
  if (!data) return <Spinner center />

  const canConfigure = data.can_configure
  const cellIssues = (col: number, lane: string) => index.byCell.get(cellId(col, lane)) ?? []

  const patchColumns = async (next: BoardColumn[]) => {
    const previous = columns
    client.setQueryData(queryKey, (old: BoardData | undefined) =>
      old ? { ...old, board: { ...old.board, columns: next } } : old,
    )
    try {
      await api.put(`/api/boards/${boardId}`, { columns: next })
      client.invalidateQueries({ queryKey: ['boards-config'] })
    } catch (err) {
      client.setQueryData(queryKey, (old: BoardData | undefined) =>
        old ? { ...old, board: { ...old.board, columns: previous } } : old,
      )
      toast((err as Error).message, 'error')
    }
  }

  const moveColumn = (from: number, to: number) => {
    if (to < 0 || to >= columns.length || from === to) return
    void patchColumns(arrayMove(columns, from, to))
  }

  const setColumnWip = (index: number) => {
    const current = columns[index]?.wip
    const raw = window.prompt(
      t('列 WIP 上限（留空表示不限制）'),
      current == null ? '' : String(current),
    )
    if (raw === null) return
    const trimmed = raw.trim()
    const wip = trimmed === '' ? null : Math.max(0, Number(trimmed))
    if (trimmed !== '' && !Number.isFinite(wip)) {
      toast(t('请输入有效数字'), 'error')
      return
    }
    void patchColumns(columns.map((c, i) => (i === index ? { ...c, wip } : c)))
  }

  const removeColumn = (index: number) => {
    if (columns.length <= 1) {
      toast(t('至少保留一列'), 'error')
      return
    }
    if (!window.confirm(t('从看板移除列「{name}」？状态本身不会被删除。', { name: t(columns[index].name) }))) return
    void patchColumns(columns.filter((_, i) => i !== index))
  }

  const clearDrag = (opts?: { keepSnapshot?: boolean }) => {
    setDragging(null)
    setItems(null)
    setInsertHint(null)
    setFrozenWidths(null)
    clonedItems.current = null
    lastOverId.current = null
    recentlyMovedToNewContainer.current = false
    if (!opts?.keepSnapshot) boardSnapshot.current = null
  }

  const restoreBoardSnapshot = () => {
    if (boardSnapshot.current) {
      client.setQueryData(queryKey, boardSnapshot.current)
    }
    boardSnapshot.current = null
  }

  const onDragStart = (e: DragStartEvent) => {
    const issue = byKey.get(String(e.active.id))
    if (!issue) return
    const start = cloneCells(boardItems)
    clonedItems.current = cloneCells(start)
    boardSnapshot.current = data ?? null
    recentlyMovedToNewContainer.current = false
    lastOverId.current = e.active.id
    setInsertHint(null)
    setItems(start)
    setDragging(issue)
    // Lock each column's current width so header/content changes cannot stretch the board.
    const row = boardRowRef.current
    if (row) {
      const widths = [...row.querySelectorAll<HTMLElement>(':scope > .column')].map(
        (el) => el.getBoundingClientRect().width,
      )
      if (widths.length) setFrozenWidths(widths)
    }
  }

  /**
   * Same-column: sortable transforms open the gap (no React reorder).
   * Cross-column: keep the card in the source list; only move a thin insert hint
   * so column heights do not thrash and the board does not "breathe".
   */
  const onDragOver = ({ active, over }: DragOverEvent) => {
    if (!over || !dragging || !data) {
      setInsertHint(null)
      return
    }
    const overId = over.id
    const activeContainer = findContainer(boardItems, active.id)
    const overContainer = findContainer(boardItems, overId)
    if (!activeContainer || !overContainer) {
      setInsertHint(null)
      return
    }
    if (activeContainer === overContainer) {
      setInsertHint(null)
      return
    }

    const parsed = parseCellId(overContainer)
    if (!parsed) return
    const [toCol] = parsed
    const column = columns[toCol]
    if (!column) return
    const allowedIds = allowedStatusIds(project, dragging, data.statuses, user)
    const statusOk =
      column.status_ids.includes(dragging.status.id) ||
      column.status_ids.some((sid) => allowedIds.has(sid))
    if (!statusOk) {
      setInsertHint(null)
      return
    }
    const peers = boardItems[overContainer].filter((k) => k !== String(active.id))
    if (column.wip != null && peers.length >= column.wip) {
      setInsertHint(null)
      return
    }

    const overIndex = peers.indexOf(String(overId))
    let index: number
    if (String(overId) in boardItems) {
      // Pointer is over the empty column body — place by vertical position among N+1 slots.
      const rect = active.rect.current.translated
      if (rect && over.rect.height > 0) {
        const center = rect.top + rect.height / 2
        const slot = (center - over.rect.top) / over.rect.height
        index = Math.max(0, Math.min(peers.length, Math.floor(slot * (peers.length + 1))))
      } else {
        index = peers.length
      }
    } else {
      const isBelow =
        active.rect.current.translated != null &&
        active.rect.current.translated.top > over.rect.top + over.rect.height / 2
      index = overIndex >= 0 ? overIndex + (isBelow ? 1 : 0) : peers.length
    }
    recentlyMovedToNewContainer.current = true
    setInsertHint((prev) =>
      prev?.container === overContainer && prev.index === index ? prev : { container: overContainer, index },
    )
  }

  const onDragCancel = () => clearDrag()

  const onDragEnd = async ({ active, over }: DragEndEvent) => {
    const issue = dragging
    const origin = clonedItems.current
    const hint = insertHint
    let lists = items ?? boardItems

    if (!issue) {
      clearDrag()
      return
    }

    const activeContainer = findContainer(lists, active.id)
    if (!activeContainer) {
      clearDrag()
      return
    }

    // Cross-column: apply the insert hint (card was never moved in React during drag).
    if (hint && hint.container !== activeContainer) {
      const fromKeys = lists[activeContainer].filter((k) => k !== issue.key)
      const toKeys = [...(lists[hint.container] ?? [])].filter((k) => k !== issue.key)
      const at = Math.max(0, Math.min(hint.index, toKeys.length))
      toKeys.splice(at, 0, issue.key)
      lists = { ...lists, [activeContainer]: fromKeys, [hint.container]: toKeys }
    } else if (over?.id != null) {
      // Same-column final order from sortable over target.
      const overContainer = findContainer(lists, over.id)
      if (overContainer && overContainer === activeContainer) {
        const activeIndex = lists[activeContainer].indexOf(String(active.id))
        const overIndex = lists[overContainer].indexOf(String(over.id))
        if (activeIndex >= 0 && overIndex >= 0 && activeIndex !== overIndex) {
          lists = {
            ...lists,
            [overContainer]: arrayMove(lists[overContainer], activeIndex, overIndex),
          }
        }
      }
    }

    const dest = findContainer(lists, issue.key)
    if (!dest) {
      clearDrag()
      return
    }
    const parsed = parseCellId(dest)
    if (!parsed) {
      clearDrag()
      return
    }
    const [col] = parsed
    const column = columns[col]
    const keys = lists[dest] ?? []
    const at = keys.indexOf(issue.key)
    const before = keys[at - 1] ?? null
    const after = keys[at + 1] ?? null
    const originKeys = origin?.[dest] ?? savedCells[dest] ?? []
    const originAt = originKeys.indexOf(issue.key)
    const unchanged =
      originAt >= 0 &&
      (originKeys[originAt - 1] ?? null) === before &&
      (originKeys[originAt + 1] ?? null) === after

    const allowedIds = allowedStatusIds(project, issue, data.statuses, user)
    const statusId = column.status_ids.includes(issue.status.id)
      ? issue.status.id
      : column.status_ids.find((sid) => allowedIds.has(sid))
    if (statusId === undefined) {
      clearDrag()
      toast(t('工作流不允许从「{from}」直接流转到「{to}」', { from: t(issue.status.name), to: t(column.name) }), 'error')
      return
    }
    const prevStatusId = issue.status.id
    if (statusId === prevStatusId && unchanged) {
      clearDrag()
      return
    }
    if (!column.status_ids.includes(prevStatusId) && column.wip != null) {
      const peers = keys.filter((k) => k !== issue.key)
      if (peers.length >= column.wip) {
        clearDrag()
        toast(t('「{column}」已达 WIP 上限 {wip}，无法再移入', { column: t(column.name), wip: column.wip }), 'error')
        return
      }
    }

    const newStatus = data.statuses.find((s) => s.id === statusId)!
    const statusChange = statusId !== prevStatusId
    const commitLocal = () => {
      client.setQueryData<BoardData>(queryKey, (old) => {
        if (!old) return old
        const next = placeIssue(old, issue, newStatus, before, after)
        if (!old.aging || !statusChange) return next
        return {
          ...next,
          aging: { ...old.aging, since: { ...old.aging.since, [issue.id]: new Date().toISOString() } },
        }
      })
    }

    // Status changes can fail gates (e.g. missing tester) — wait for the server so a reject
    // does not optimistic-move then refetch-flash the whole board.
    // Same-column reorder is safe to apply immediately.
    if (!statusChange) commitLocal()
    clearDrag({ keepSnapshot: true })

    if (statusChange && newStatus.name === REOPENED) {
      setReopenDrop({ issue, statusId, before, after, newStatus })
      return
    }

    await commitRankMove({
      issue, statusId, before, after, newStatus, statusChange, commitLocal,
    })
  }

  const commitRankMove = async ({
    issue, statusId, before, after, newStatus, statusChange, commitLocal, reopenReason,
  }: {
    issue: Issue
    statusId: number
    before: string | null
    after: string | null
    newStatus: Status
    statusChange: boolean
    commitLocal: () => void
    reopenReason?: string
  }) => {
    try {
      const moved = await withSubtaskClose((close) =>
        api.post(`/api/issues/${issue.key}/rank`, {
          before_key: before,
          after_key: after,
          status_id: statusId,
          close_subtasks: close,
          ...(reopenReason ? { reopen_reason: reopenReason } : {}),
        }),
      )
      if (moved === null) {
        // User cancelled the subtask-close prompt — undo optimistic reorder if any.
        if (!statusChange) restoreBoardSnapshot()
        else boardSnapshot.current = null
        return
      }
      if (statusChange) commitLocal()
      boardSnapshot.current = null
      if (moved && statusChange) toast(`${issue.key} → ${t(newStatus.name)}`, 'success')
      // Soft-sync other views only; keep the board cache to avoid a refetch flash.
      for (const qk of ['backlog', 'search', 'dashboard', 'list', 'reports', 'project-issues'] as const) {
        client.invalidateQueries({ queryKey: [qk] })
      }
      client.invalidateQueries({ queryKey: ['issue', issue.key] })
    } catch (err) {
      if (!statusChange) restoreBoardSnapshot()
      else boardSnapshot.current = null
      if (err instanceof ApiError && err.code === 'software_pending') {
        toast(tServer(err.message), 'info')
        client.invalidateQueries({ queryKey: ['issue', issue.key] })
        return
      }
      toast((err as Error).message, 'error')
      if (err instanceof ApiError && err.code === 'transition_gate' && err.fields?.length) {
        openIssue(issue.key, { highlightFields: err.fields })
      }
      if (err instanceof ApiError && err.code === 'reopen_reason') {
        openIssue(issue.key)
      }
      // Never invalidate `board` here — that remounts columns and flashes the screen.
      invalidate({ skip: ['board'] })
      client.invalidateQueries({ queryKey: ['issue', issue.key] })
    }
  }

  const toggle = (name: string) => update({ qf: active.includes(name) ? active.filter((n) => n !== name) : [...active, name] })
  const boards = project.boards

  const reopenModal = reopenDrop ? (
    <ReopenReasonModal
      issueKey={reopenDrop.issue.key}
      onCancel={() => {
        restoreBoardSnapshot()
        setReopenDrop(null)
      }}
      onConfirm={async (reason) => {
        const drop = reopenDrop
        setReopenDrop(null)
        const commitLocal = () => {
          client.setQueryData<BoardData>(queryKey, (old) => {
            if (!old) return old
            const next = placeIssue(old, drop.issue, drop.newStatus, drop.before, drop.after)
            if (!old.aging) return next
            return {
              ...next,
              aging: { ...old.aging, since: { ...old.aging.since, [drop.issue.id]: new Date().toISOString() } },
            }
          })
        }
        await commitRankMove({
          issue: drop.issue,
          statusId: drop.statusId,
          before: drop.before,
          after: drop.after,
          newStatus: drop.newStatus,
          statusChange: true,
          commitLocal,
          reopenReason: reason,
        })
      }}
    />
  ) : null

  return (
    <div className="board-page">
      <div className={`board-toolbar${isFetching && !isLoading ? ' is-fetching' : ''}`}>
        {boards.length > 1 && (
          <Select
            className="select sm"
            style={{ width: 'auto' }}
            value={boardId}
            onChange={(e) => update({ board: e.target.value, qf: null })}
          >
            {boards.map((b) => (
              <option key={b.id} value={b.id}>
                {b.kind === 'scrum' ? '🏃 ' : '▦ '}
                {boardLabel(b.name)}
              </option>
            ))}
          </Select>
        )}
        <input className="input sm" style={{ width: 180 }} placeholder={t('筛选卡片')} value={text} onChange={(e) => setText(e.target.value)} />
        <div className="row" style={{ gap: 0 }}>
          {assignees.map(([id, u]) => (
            <span
              key={id}
              onClick={() => setPeople(people.includes(id) ? people.filter((x) => x !== id) : [...people, id])}
              style={{ display: 'inline-flex', cursor: 'pointer', marginLeft: -4, borderRadius: '50%', outline: people.includes(id) ? '2px solid var(--accent)' : '2px solid var(--bg)' }}
            >
              <Avatar user={u} size={28} />
            </span>
          ))}
        </div>
        {data.board.quick_filters.map((f) => (
          <button key={f.name} className={`quick-filter ${active.includes(f.name) ? 'active' : ''}`} onClick={() => toggle(f.name)}>
            {t(f.name)}
          </button>
        ))}
        {data.board.quick_filters.length === 0 && (
          <button
            className={`quick-filter ${people.length === 1 && people[0] === user.id ? 'active' : ''}`}
            onClick={() => setPeople(people.length === 1 && people[0] === user.id ? [] : [user.id])}
          >
            {t('只看我的')}
          </button>
        )}
        {subtaskCount > 0 && (
          <button
            className={`quick-filter ${hideSubtasks ? 'active' : ''}`}
            title={hideSubtasks ? t('已隐藏 {n} 个子任务，点击显示', { n: subtaskCount }) : t('只看父任务，子任务进度显示在父卡片上')}
            onClick={() => setHideSubtasks(!hideSubtasks)}
          >
            {hideSubtasks ? t('子任务已隐藏 · {n}', { n: subtaskCount }) : t('隐藏子任务')}
          </button>
        )}
        <span className="spacer" />
        <label className="small muted row">
          <span className="row" style={{ gap: 0 }}>
            {t('泳道')}
            <InfoTip topic="swimlane" />
          </span>
          <Select className="select sm" style={{ width: 'auto' }} value={swimlane} onChange={(e) => update({ lane: e.target.value === data.board.swimlane ? null : e.target.value })}>
            <option value="none">{t('无')}</option>
            <option value="assignee">{t('负责人')}</option>
            <option value="group">{t('机台组')}</option>
            <option value="epic">{t('史诗')}</option>
            <option value="priority">{t('优先级')}</option>
          </Select>
        </label>
        {canConfigure ? (
          <Dropdown
            align="right"
            trigger={
              <button type="button" className="btn sm" title={t('看板设置')}>
                ⚙ {t('看板设置')}
              </button>
            }
            options={[
              { value: 'config', label: t('配置列与筛选器') },
              { value: 'copy', label: t('复制视图链接') },
            ]}
            onSelect={(v) => {
              if (v === 'copy') void copyView()
              else navigate(`../settings?section=boards&board=${boardId}`)
            }}
          />
        ) : (
          <button className="btn sm" onClick={copyView} title={t('复制包含当前筛选、泳道和子任务设置的链接')}>
            {t('复制视图链接')}
          </button>
        )}
      </div>

      {data.board.kind === 'scrum' && (
        <div className="row small" style={{ marginBottom: 10 }}>
          {data.sprint ? (
            <>
              <b>{data.sprint.name}</b>
              {data.sprint.goal && <span className="muted">· {data.sprint.goal}</span>}
              {data.sprint.end_date && <span className="faint">· {t('截止 {date}', { date: fmtDate(data.sprint.end_date) })}</span>}
              <span className="spacer" />
              <Link to="../backlog" className="btn sm">
                {t('管理冲刺')}
              </Link>
            </>
          ) : (
            <Empty icon="🏃" title={t('没有进行中的冲刺')}>
              <Link to="../backlog">{t('前往待办事项开始冲刺')}</Link>
            </Empty>
          )}
        </div>
      )}

      <div className={`board-scroll${swimlane === 'none' ? '' : ' has-lanes'}`}>
        <DndContext
          sensors={sensors}
          collisionDetection={collisionDetection}
          measuring={{
            droppable: {
              strategy: dragging ? MeasuringStrategy.Always : MeasuringStrategy.BeforeDragging,
            },
          }}
          onDragStart={onDragStart}
          onDragOver={onDragOver}
          onDragEnd={onDragEnd}
          onDragCancel={onDragCancel}
        >
          {lanes.map((lane) => (
            <div key={lane.id} className={swimlane === 'none' ? 'board-lane-flat' : 'lane'}>
              {swimlane !== 'none' && (
                <div className="lane-title">
                  {lane.user !== undefined && <Avatar user={lane.user} size={22} />}
                  {lane.title}
                  <span className="count">{index.byLane.get(lane.id) ?? 0}</span>
                </div>
              )}
              <div
                ref={lane.id === lanes[0].id ? boardRowRef : undefined}
                className={`board ${swimlane === 'none' ? 'flat' : 'lanes'}${frozenWidths ? ' widths-frozen' : ''}`}
              >
                {columns.map((column, col) => {
                  const list = cellIssues(col, lane.id)
                  const dropId = cellId(col, lane.id)
                  // Swimlanes: WIP / count are per cell. Flat board: whole column.
                  const count = swimlane === 'none' ? (index.byCol.get(col) ?? 0) : list.length
                  const blocked = !!allowed && !column.status_ids.some((id) => allowed.has(id))
                  // Flow hint overlays the header — do not replace the title (that stretched columns).
                  let flowTo: string | null = null
                  if (dragging && allowed && !blocked && !column.status_ids.includes(dragging.status.id)) {
                    const statusId = column.status_ids.find((id) => allowed.has(id))
                    const to = statusId != null ? data.statuses.find((s) => s.id === statusId) : null
                    if (to) flowTo = t(to.name)
                  }
                  const showChrome = lane.id === lanes[0].id
                  const hintHere = insertHint?.container === dropId ? insertHint.index : -1
                  const freeze = frozenWidths?.[col]
                  return (
                    <Column
                      key={`${lane.id}:${column.status_ids.join('-') || col}`}
                      droppableId={dropId}
                      title={t(column.name)}
                      flowTo={flowTo}
                      count={count}
                      wip={column.wip}
                      blocked={blocked}
                      freezeWidth={freeze}
                      canConfigure={canConfigure && showChrome}
                      onAdd={showChrome ? () => openCreate({ project: project.project.key, sprint_id: data.sprint?.id }) : undefined}
                      onSetWip={() => setColumnWip(col)}
                      onMoveLeft={() => moveColumn(col, col - 1)}
                      onMoveRight={() => moveColumn(col, col + 1)}
                      onRemove={() => removeColumn(col)}
                      onOpenSettings={() => navigate(`../settings?section=boards&board=${boardId}`)}
                      canMoveLeft={col > 0}
                      canMoveRight={col < columns.length - 1}
                    >
                      <SortableContext items={list.map((i) => i.key)} strategy={verticalListSortingStrategy}>
                        {list.map((issue, i) => (
                          <div key={issue.key}>
                            {hintHere === i && <div className="board-insert-line" aria-hidden />}
                            <SortableCard
                              issue={issue}
                              stay={index.stayById.get(issue.id) ?? null}
                              ghost={dragging?.key === issue.key}
                              canEdit={editableSections(project, user, issue).has('general')}
                              onOpen={openIssue}
                              onPatch={patchCard}
                            />
                          </div>
                        ))}
                        {hintHere === list.length && <div className="board-insert-line" aria-hidden />}
                      </SortableContext>
                    </Column>
                  )
                })}
              </div>
            </div>
          ))}
          <DragOverlay dropAnimation={insertHint ? null : dropAnimation}>
            {dragging && <CardBody issue={dragging} stay={stayOf(dragging, data.aging)} className="overlay-card" />}
          </DragOverlay>
        </DndContext>
      </div>
      {reopenModal}
    </div>
  )
}

function Column({
  droppableId,
  title,
  flowTo,
  count,
  wip,
  blocked,
  freezeWidth,
  canConfigure,
  onAdd,
  onSetWip,
  onMoveLeft,
  onMoveRight,
  onRemove,
  onOpenSettings,
  canMoveLeft,
  canMoveRight,
  children,
}: {
  droppableId: string
  title: string
  flowTo?: string | null
  count: number
  wip: number | null
  blocked: boolean
  freezeWidth?: number
  canConfigure?: boolean
  onAdd?: () => void
  onSetWip?: () => void
  onMoveLeft?: () => void
  onMoveRight?: () => void
  onRemove?: () => void
  onOpenSettings?: () => void
  canMoveLeft?: boolean
  canMoveRight?: boolean
  children: ReactNode
}) {
  const { setNodeRef, isOver } = useDroppable({ id: droppableId, disabled: blocked })
  const exceeded = !!wip && count > wip
  const frozen = freezeWidth != null && freezeWidth > 0
  return (
    <div
      className={`column${isOver && !blocked ? ' over' : ''}${exceeded ? ' wip-exceeded' : ''}${blocked ? ' blocked' : ''}${flowTo ? ' can-flow' : ''}`}
      style={
        frozen
          ? { flex: `0 0 ${freezeWidth}px`, width: freezeWidth, minWidth: freezeWidth, maxWidth: freezeWidth }
          : undefined
      }
    >
      <div className="column-header">
        <span style={{ whiteSpace: 'nowrap' }}>{title}</span>
        <span className="count" style={exceeded ? { background: 'var(--danger)', color: '#fff' } : undefined}>
          {count}
          {wip != null ? ` / ${wip}` : ''}
        </span>
        {flowTo && (
          <span className="column-flow-chip" title={t('{from} → {to}', { from: title, to: flowTo })}>
            → {flowTo}
          </span>
        )}
        <span className="spacer" />
        {canConfigure && (
          <Dropdown
            align="right"
            trigger={
              <button type="button" className="btn ghost sm icon column-menu-btn" title={t('列设置')} aria-label={t('列设置')}>
                ···
              </button>
            }
            options={[
              { value: 'wip', label: wip == null ? t('设置列限制') : t('修改列限制（当前 {n}）', { n: wip }) },
              { value: 'left', label: t('将列左移'), disabled: !canMoveLeft },
              { value: 'right', label: t('将列右移'), disabled: !canMoveRight },
              { value: 'settings', label: t('配置看板') },
              { value: 'remove', label: t('移除列'), danger: true },
            ]}
            onSelect={(v) => {
              if (v === 'wip') onSetWip?.()
              else if (v === 'left') onMoveLeft?.()
              else if (v === 'right') onMoveRight?.()
              else if (v === 'settings') onOpenSettings?.()
              else if (v === 'remove') onRemove?.()
            }}
          />
        )}
      </div>
      <div ref={setNodeRef} className="column-body">
        {children}
        {onAdd && (
          <button type="button" className="column-add" onClick={onAdd} title={t('在此列创建')}>
            <span aria-hidden>＋</span>
            <span>{t('创建')}</span>
          </button>
        )}
      </div>
    </div>
  )
}

/** Keep card open / drag from firing when using footer field controls. */
function stopCardGesture(e: { stopPropagation: () => void }) {
  e.stopPropagation()
}

const SortableCard = memo(function SortableCard({
  issue,
  stay,
  ghost,
  canEdit,
  onOpen,
  onPatch,
}: {
  issue: Issue
  stay: Stay | null
  ghost?: boolean
  canEdit: boolean
  onOpen: (key: string) => void
  onPatch: (issue: Issue, changes: Record<string, unknown>) => void
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: issue.key,
    transition: cardTransition,
  })
  const slot = isDragging || !!ghost
  return (
    <div
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
      }}
      className={slot ? 'sortable-card is-slot' : 'sortable-card'}
      {...attributes}
      {...listeners}
      onClick={() => onOpen(issue.key)}
    >
      <CardBody issue={issue} stay={stay} canEdit={canEdit} onPatch={onPatch} />
    </div>
  )
})

const CardBody = memo(function CardBody({
  issue,
  stay = null,
  className = '',
  canEdit = false,
  onPatch,
}: {
  issue: Issue
  stay?: Stay | null
  className?: string
  canEdit?: boolean
  onPatch?: (issue: Issue, changes: Record<string, unknown>) => void
}) {
  const today = new Date().toISOString().slice(0, 10)
  const editable = canEdit && !!onPatch
  const assigneeTitle = issue.assignee
    ? t('负责人：{name}', { name: issue.assignee.display_name })
    : t('设置负责人')
  const assigneeAvatar = <Avatar user={issue.assignee} size={22} title={assigneeTitle} />
  return (
    <div className={`issue-card ${className}`}>
      {issue.parent && (
        <span className="parent-ref" title={`${issue.parent.key} ${issue.parent.title}`}>
          ↳ {issue.parent.key} · {issue.parent.title}
        </span>
      )}
      <div className="title">{issue.title}</div>
      {(issue.machines.length > 0 || issue.labels.length > 0) && (
        <div className="meta">
          {issue.machines.slice(0, 3).map((m) => (
            <span key={m.id} className="machine-chip">
              {m.group}/{t(m.name)}
            </span>
          ))}
          {issue.machines.length > 3 && <span className="tiny faint">+{issue.machines.length - 3}</span>}
          {issue.labels.slice(0, 2).map((l) => (
            <span key={l} className="tag" style={{ height: 18, fontSize: 11 }}>
              {l}
            </span>
          ))}
        </div>
      )}
      {issue.epic && (
        <div className="tiny" style={{ color: epicAccent(issue.epic.id) }}>
          ⚡ {issue.epic.title}
        </div>
      )}
      <div className="meta foot">
        <TypeIcon type={issue.type} size={16} />
        <span className="issue-key">{issue.key}</span>
        {issue.status.name === '已驳回' ? (
          <span className="tiny err-text">{t('⊘ 已驳回')}</span>
        ) : (
          issue.test_passed !== null && (
            <span className={`tiny ${issue.test_passed ? 'ok-text' : 'err-text'}`}>{issue.test_passed ? t('✓ 测试通过') : t('✗ 未通过')}</span>
          )
        )}
        <span className="spacer" />
        {stay && (
          <span className={`aging-badge compact ${stay.level}`} title={t('在「{status}」已停留 {n} 天', { status: t(issue.status.name), n: stay.days })}>
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" aria-hidden="true">
              <circle cx="12" cy="12" r="9" />
              <path d="M12 7v5l3 2" />
            </svg>
            {stay.days}
          </span>
        )}
        <SubtaskBadge progress={issue.subtasks} />
        {issue.due_date && <span className={`tiny ${issue.due_date < today ? 'err-text' : 'faint'}`}>{issue.due_date.slice(5)}</span>}
        {issue.story_points !== null && <span className="count">{issue.story_points}</span>}
        {editable ? (
          <div className="card-field-wrap" onClick={stopCardGesture} onPointerDown={stopCardGesture}>
            <Dropdown
              align="right"
              value={issue.priority}
              trigger={
                <span className="card-field" title={t('优先级：{name}', { name: priorityName(issue.priority) })}>
                  <PriorityIcon priority={issue.priority} />
                </span>
              }
              options={PRIORITIES.map((p) => ({
                value: p,
                label: (
                  <>
                    <PriorityIcon priority={p} /> {priorityName(p)}
                  </>
                ),
              }))}
              onSelect={(p: Priority) => onPatch(issue, { priority: p })}
            />
          </div>
        ) : (
          <PriorityIcon priority={issue.priority} />
        )}
        {editable ? (
          <div className="card-field-wrap" onClick={stopCardGesture} onPointerDown={stopCardGesture}>
            {issue.assignee ? (
              <PersonHover user={issue.assignee}>
                <UserPicker
                  value={issue.assignee}
                  project={issue.project_key ?? undefined}
                  field="assignee_id"
                  onChange={(u) => onPatch(issue, { assignee_id: u?.id ?? null })}
                  trigger={<span className="card-field">{assigneeAvatar}</span>}
                />
              </PersonHover>
            ) : (
              <UserPicker
                value={null}
                project={issue.project_key ?? undefined}
                field="assignee_id"
                onChange={(u) => onPatch(issue, { assignee_id: u?.id ?? null })}
                trigger={<span className="card-field">{assigneeAvatar}</span>}
              />
            )}
          </div>
        ) : issue.assignee ? (
          <PersonHover user={issue.assignee}>{assigneeAvatar}</PersonHover>
        ) : (
          assigneeAvatar
        )}
      </div>
    </div>
  )
})
