import {
  closestCenter,
  closestCorners,
  DndContext,
  DragOverlay,
  getFirstCollision,
  PointerSensor,
  pointerWithin,
  rectIntersection,
  useDroppable,
  useSensor,
  useSensors,
  type CollisionDetection,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
  type UniqueIdentifier,
} from '@dnd-kit/core'
import { arrayMove, SortableContext, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode } from 'react'
import { api } from '../api'
import { DateField } from '../components/DateField'
import { Avatar, FieldIcon, InfoTip, Modal, PriorityIcon, Spinner, StatusPill, SubtaskBadge, TypeIcon, Select } from '../components/ui'
import { useApp } from '../context'
import { t } from '../i18n'
import { epicAccent, fmtDate } from '../labels'
import { useInvalidateIssues } from '../queries'
import type { Issue, Sprint } from '../types'
import { isCompleted } from '../workflow'
import { useProjectCtx } from './ProjectLayout'

type SprintTone = 'active' | 'future' | 'closed' | 'backlog'

interface SprintMetrics {
  total: number
  done: number
  points: number
  donePoints: number
  todo: number
  doing: number
  issuePct: number
  pointPct: number
}

function metricsOf(issues: Issue[]): SprintMetrics {
  let done = 0
  let points = 0
  let donePoints = 0
  let todo = 0
  let doing = 0
  for (const issue of issues) {
    const pts = issue.story_points ?? 0
    points += pts
    if (isCompleted(issue.status)) {
      done += 1
      donePoints += pts
    } else if (issue.status.category === 'doing') {
      doing += 1
    } else {
      todo += 1
    }
  }
  const total = issues.length
  return {
    total,
    done,
    points,
    donePoints,
    todo,
    doing,
    issuePct: total ? Math.round((done / total) * 100) : 0,
    pointPct: points ? Math.round((donePoints / points) * 100) : 0,
  }
}

/** Calendar progress through the sprint window (0–100), or null when dates missing. */
function timeProgress(start?: string | null, end?: string | null): { pct: number; day: number; days: number; left: number } | null {
  if (!start || !end) return null
  const a = new Date(start.slice(0, 10) + 'T00:00:00').getTime()
  const b = new Date(end.slice(0, 10) + 'T00:00:00').getTime()
  if (!(b > a)) return null
  const now = Date.now()
  const days = Math.max(1, Math.round((b - a) / 86400000))
  const elapsed = Math.min(days, Math.max(0, Math.round((now - a) / 86400000)))
  const left = Math.max(0, days - elapsed)
  return { pct: Math.round((elapsed / days) * 100), day: elapsed, days, left }
}

interface BacklogData {
  sprints: (Sprint & { issues: Issue[] })[]
  backlog: Issue[]
}

const BACKLOG = 'backlog'
type EpicFilter = 'all' | 'none' | number

function isEpicDrop(id: string): boolean {
  return id === 'epic:none' || /^epic:\d+$/.test(id)
}

/** Issue keys per container (`backlog` or `sprint:<id>`), in display order. */
type Lists = Record<string, string[]>

function findList(lists: Lists, id: string): string | undefined {
  return id in lists ? id : Object.keys(lists).find((c) => lists[c].includes(id))
}

/** Move `id` into container `to`, between `before` and `after`, mirroring what the rank API will do. */
function moveIssue(data: BacklogData, id: string, to: string, before: string | null, after: string | null): BacklogData {
  const issue = [...data.backlog, ...data.sprints.flatMap((s) => s.issues)].find((i) => i.key === id)
  if (!issue) return data
  const place = (list: Issue[]) => {
    const next = list.filter((i) => i.key !== id)
    const afterBefore = before ? next.findIndex((i) => i.key === before) + 1 : 0
    const atAfter = after ? next.findIndex((i) => i.key === after) : -1
    next.splice(afterBefore > 0 ? afterBefore : atAfter >= 0 ? atAfter : next.length, 0, issue)
    return next
  }
  const strip = (list: Issue[]) => list.filter((i) => i.key !== id)
  return {
    backlog: to === BACKLOG ? place(data.backlog) : strip(data.backlog),
    sprints: data.sprints.map((s) => ({ ...s, issues: to === `sprint:${s.id}` ? place(s.issues) : strip(s.issues) })),
  }
}

export function BacklogPage() {
  const project = useProjectCtx()
  const key = project.project.key
  const { openIssue, openCreate, toast } = useApp()
  const client = useQueryClient()
  const invalidate = useInvalidateIssues()
  /** While dragging, the lists are local so the row can move between sprints before anything is saved. */
  const [drag, setDrag] = useState<{ issue: Issue; lists: Lists } | null>(null)
  const [starting, setStarting] = useState<Sprint | null>(null)
  const [completing, setCompleting] = useState<Sprint | null>(null)
  const [editing, setEditing] = useState<Sprint | null>(null)
  const [q, setQ] = useState('')
  const [epicFilter, setEpicFilter] = useState<EpicFilter>('all')
  const [epicOpen, setEpicOpen] = useState(() => {
    try {
      return localStorage.getItem(`metjira.backlog.epicOpen.${key}`) !== '0'
    } catch {
      return true
    }
  })
  /** Collapsed sprint / backlog sections — closed sprints start collapsed. */
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const { data, isLoading } = useQuery({
    queryKey: ['backlog', key],
    queryFn: () => api.get<BacklogData>(`/api/projects/${key}/backlog`),
  })
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))
  const lastOverId = useRef<UniqueIdentifier | null>(null)
  const listsRef = useRef<Lists>({ [BACKLOG]: [] })
  /**
   * Prefer the sprint/backlog container under the pointer so a short future sprint
   * (few rows) isn't drowned out by a tall active sprint's many card droppables.
   */
  const collisionDetection: CollisionDetection = useCallback((args) => {
    const currentLists = listsRef.current
    const containerIds = new Set(Object.keys(currentLists))
    const pointerHits = pointerWithin(args)
    const hits = pointerHits.length > 0 ? pointerHits : rectIntersection(args)

    const containerHit = hits.find((c) => containerIds.has(String(c.id)))
    let overId = containerHit?.id ?? getFirstCollision(hits, 'id')

    if (overId != null) {
      const overStr = String(overId)
      // Pointer on a card → keep the card for insert position.
      // Pointer on empty header/padding of a container → keep the container id.
      if (containerIds.has(overStr)) {
        const items = currentLists[overStr] ?? []
        if (items.length > 0) {
          const inner = closestCenter({
            ...args,
            droppableContainers: args.droppableContainers.filter(
              (c) => c.id !== overId && items.includes(String(c.id)),
            ),
          })
          const innerId = inner[0]?.id
          if (innerId != null && pointerHits.some((h) => h.id === innerId)) overId = innerId
        }
      }
      lastOverId.current = overId
      return [{ id: overId }]
    }

    // Between sections: nearest *container*, not nearest of dozens of cards.
    const byContainer = closestCorners({
      ...args,
      droppableContainers: args.droppableContainers.filter((c) => containerIds.has(String(c.id))),
    })
    if (byContainer[0]) {
      lastOverId.current = byContainer[0].id
      return [{ id: byContainer[0].id }]
    }
    return lastOverId.current ? [{ id: lastOverId.current }] : []
  }, [])

  if (isLoading || !data) return <Spinner center />

  const allIssues = [...data.backlog, ...data.sprints.flatMap((s) => s.issues)]
  const match = (i: Issue) => {
    if (q && !i.title.includes(q) && !i.key.toLowerCase().includes(q.toLowerCase())) return false
    if (epicFilter === 'all') return true
    if (epicFilter === 'none') return !i.epic
    return i.epic?.id === epicFilter
  }
  const byKey = new Map<string, Issue>(allIssues.map((i) => [i.key, i]))
  const saved: Lists = { [BACKLOG]: data.backlog.filter(match).map((i) => i.key) }
  for (const s of data.sprints) saved[`sprint:${s.id}`] = s.issues.filter(match).map((i) => i.key)
  const lists = drag?.lists ?? saved
  listsRef.current = lists
  const issuesIn = (container: string) => (lists[container] ?? []).flatMap((k) => byKey.get(k) ?? [])
  /** Container currently holding the dragged issue (updated live in onDragOver). */
  const dragTarget = drag ? findList(drag.lists, drag.issue.key) ?? null : null

  const epicCounts = (() => {
    const counts = new Map<number, number>()
    let none = 0
    for (const issue of allIssues) {
      if (!issue.epic) none += 1
      else counts.set(issue.epic.id, (counts.get(issue.epic.id) ?? 0) + 1)
    }
    return { counts, none, total: allIssues.length }
  })()

  const refresh = () => {
    client.invalidateQueries({ queryKey: ['backlog', key] })
    client.invalidateQueries({ queryKey: ['project', key] })
    invalidate()
  }

  const onDragStart = (e: DragStartEvent) => {
    const issue = byKey.get(String(e.active.id))
    if (issue) setDrag({ issue, lists: saved })
  }

  // Crossing into another sprint moves the row there right away, so the target list opens a gap for it.
  const onDragOver = ({ active, over }: DragOverEvent) => {
    if (!over) return
    setDrag((d) => {
      if (!d) return d
      const id = String(active.id)
      const from = findList(d.lists, id)
      const to = findList(d.lists, String(over.id))
      if (!from || !to || from === to) return d
      const target = d.lists[to].filter((k) => k !== id)
      const overIndex = target.indexOf(String(over.id))
      const rect = active.rect.current.translated
      const below = rect !== null && rect.top > over.rect.top + over.rect.height / 2
      target.splice(overIndex >= 0 ? overIndex + (below ? 1 : 0) : target.length, 0, id)
      return { ...d, lists: { ...d.lists, [from]: d.lists[from].filter((k) => k !== id), [to]: target } }
    })
  }

  const onDragEnd = async ({ active, over }: DragEndEvent) => {
    const current = drag
    const id = String(active.id)
    if (!current || !over) {
      lastOverId.current = null
      return setDrag(null)
    }
    const overId = String(over.id)

    // Drop onto epic panel → change epic_id only (do not persist sprint moves from dragOver).
    if (isEpicDrop(overId)) {
      const epicId = overId === 'epic:none' ? null : Number(overId.slice(5))
      const issue = current.issue
      const same = epicId === null ? !issue.epic : issue.epic?.id === epicId
      setDrag(null)
      if (same || Number.isNaN(epicId as number)) return
      try {
        await api.patch(`/api/issues/${id}`, { epic_id: epicId })
        toast(
          epicId == null
            ? t('已移出史诗')
            : t('已归属史诗'),
          'success',
        )
      } catch (err) {
        toast((err as Error).message, 'error')
      }
      refresh()
      return
    }

    const to = findList(current.lists, id)
    if (!to) return setDrag(null)
    let list = current.lists[to]
    const oldIndex = list.indexOf(id)
    const overIndex = list.indexOf(overId)
    if (overIndex >= 0 && overIndex !== oldIndex) list = arrayMove(list, oldIndex, overIndex)
    if (findList(saved, id) === to && list.join() === saved[to].join()) return setDrag(null)
    const index = list.indexOf(id)
    const before = list[index - 1] ?? null
    const after = list[index + 1] ?? null
    const body: Record<string, unknown> = { before_key: before, after_key: after }
    if (to === BACKLOG) body.move_to_backlog = true
    else body.sprint_id = Number(to.split(':')[1])
    // Show the result immediately; the server's answer only confirms it.
    client.cancelQueries({ queryKey: ['backlog', key] })
    client.setQueryData<BacklogData>(['backlog', key], (old) => old && moveIssue(old, id, to, before, after))
    setDrag(null)
    try {
      await api.post(`/api/issues/${id}/rank`, body)
    } catch (err) {
      toast((err as Error).message, 'error')
    }
    refresh()
  }

  const createSprint = async () => {
    try {
      const n = data.sprints.length + 1
      await api.post(`/api/projects/${key}/sprints`, { name: `${key} Sprint ${n}` })
      refresh()
    } catch (err) {
      toast((err as Error).message, 'error')
    }
  }

  const deleteSprint = async (sprint: Sprint) => {
    if (!window.confirm(t('删除「{name}」？其中的问题将移回待办事项。', { name: sprint.name }))) return
    await api.del(`/api/sprints/${sprint.id}`)
    refresh()
  }

  const backlogCount = issuesIn(BACKLOG).length
  const orderedSprints = [...data.sprints].sort((a, b) => {
    const rank = { active: 0, future: 1, closed: 2 } as const
    return rank[a.state] - rank[b.state] || a.id - b.id
  })
  const isCollapsed = (id: string, tone: SprintTone) =>
    collapsed[id] ?? (tone === 'closed')
  const toggleCollapsed = (id: string, tone: SprintTone) =>
    setCollapsed((prev) => ({ ...prev, [id]: !isCollapsed(id, tone) }))
  const toggleEpic = () => {
    setEpicOpen((open) => {
      const next = !open
      try {
        localStorage.setItem(`metjira.backlog.epicOpen.${key}`, next ? '1' : '0')
      } catch { /* ignore */ }
      return next
    })
  }

  return (
    <div>
      <div className="board-toolbar">
        <button
          type="button"
          className={`btn sm${epicOpen ? ' primary' : ''}`}
          onClick={toggleEpic}
          title={t('显示或隐藏史诗面板')}
        >
          {t('史诗')}
        </button>
        <input className="input sm" style={{ width: 220 }} placeholder={t('筛选待办事项')} value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="spacer" />
        {project.is_admin && (
          <button className="btn sm" onClick={createSprint}>
            {t('＋ 创建冲刺')}
          </button>
        )}
      </div>
      <DndContext
        sensors={sensors}
        collisionDetection={collisionDetection}
        onDragStart={onDragStart}
        onDragOver={onDragOver}
        onDragEnd={onDragEnd}
        onDragCancel={() => {
          lastOverId.current = null
          setDrag(null)
        }}
      >
        <div className={`backlog-layout${epicOpen ? '' : ' epic-closed'}`}>
          {epicOpen && (
            <EpicPanel
              epics={project.epics}
              counts={epicCounts}
              filter={epicFilter}
              dragging={!!drag}
              onFilter={setEpicFilter}
              onOpen={openIssue}
              onCreate={() => openCreate({ project: key, type: 'epic' })}
            />
          )}
          <div className="backlog-main">
            {orderedSprints.map((sprint) => {
              const issues = issuesIn(`sprint:${sprint.id}`)
              const metrics = metricsOf(issues)
              const tone: SprintTone = sprint.state === 'active' ? 'active' : sprint.state === 'closed' ? 'closed' : 'future'
              const clock = timeProgress(sprint.start_date, sprint.end_date)
              const sid = `sprint:${sprint.id}`
              const folded = isCollapsed(sid, tone)
              return (
                <Container
                  key={sprint.id}
                  id={sid}
                  anchorId={`sprint-${sprint.id}`}
                  issues={issues}
                  onOpen={openIssue}
                  tone={tone}
                  collapsed={folded}
                  dropTarget={dragTarget === sid}
                  onToggleCollapse={() => toggleCollapsed(sid, tone)}
                  banner={!folded && tone === 'active' ? (
                    <ActiveSprintBanner sprint={sprint} metrics={metrics} clock={clock} />
                  ) : undefined}
                  header={
                    <>
                      <span className="sprint-fold" aria-hidden>▸</span>
                      <div className="sprint-head-main">
                        <div className="sprint-head-title">
                          <h3>{sprint.name}</h3>
                          {tone === 'active' && <span className="sprint-live">{t('进行中')}</span>}
                          {tone === 'future' && <span className="sprint-chip future">{t('未开始')}</span>}
                          {tone === 'closed' && <span className="sprint-chip closed">{t('已完成')}</span>}
                        </div>
                        <div className="sprint-head-meta">
                          <span>
                            {sprint.start_date ? `${fmtDate(sprint.start_date)} ~ ${fmtDate(sprint.end_date)}` : t('未设置日期')}
                          </span>
                          <span>·</span>
                          <span>{t('{count} 个问题 · {points} 点 · 已完成 {done}', { count: metrics.total, points: metrics.points, done: metrics.done })}</span>
                          {sprint.goal && (
                            <>
                              <span>·</span>
                              <span className="ellipsis" title={sprint.goal}>🎯 {sprint.goal}</span>
                            </>
                          )}
                        </div>
                        {tone !== 'active' && metrics.total > 0 && (
                          <SprintMiniBars metrics={metrics} />
                        )}
                      </div>
                      <span className="spacer" />
                      {project.is_admin && (
                        <div className="sprint-head-actions" onClick={(e) => e.stopPropagation()}>
                          {sprint.state === 'future' && (
                            <button className="btn primary sm" onClick={() => setStarting(sprint)} disabled={data.sprints.some((s) => s.state === 'active')}>
                              {t('开始冲刺')}
                            </button>
                          )}
                          {sprint.state === 'active' && (
                            <button className="btn primary sm" onClick={() => setCompleting(sprint)}>
                              {t('完成冲刺')}
                            </button>
                          )}
                          <button className="btn ghost sm" onClick={() => setEditing(sprint)}>
                            {t('编辑')}
                          </button>
                          {sprint.state === 'future' && (
                            <button className="btn ghost sm danger" onClick={() => deleteSprint(sprint)}>
                              {t('删除')}
                            </button>
                          )}
                        </div>
                      )}
                    </>
                  }
                  footer={
                    <button className="btn ghost sm" onClick={() => openCreate({ project: key, sprint_id: sprint.id })}>
                      <FieldIcon name="create" size={12} /> {t('创建问题')}
                    </button>
                  }
                />
              )
            })}
            {orderedSprints.length === 0 && (
              <div className="card sprint-box tone-future">
                <div className="small faint" style={{ padding: 16 }}>{t('还没有冲刺。创建冲刺后，把下方待办拖入即可规划。')}</div>
              </div>
            )}
            <Container
              id={BACKLOG}
              issues={issuesIn(BACKLOG)}
              onOpen={openIssue}
              tone="backlog"
              collapsed={isCollapsed(BACKLOG, 'backlog')}
              dropTarget={dragTarget === BACKLOG}
              onToggleCollapse={() => toggleCollapsed(BACKLOG, 'backlog')}
              header={
                <>
                  <span className="sprint-fold" aria-hidden>▸</span>
                  <div className="sprint-head-main">
                    <div className="sprint-head-title">
                      <h3>
                        {t('待办事项')}
                        <InfoTip topic="backlog" />
                      </h3>
                    </div>
                    <div className="sprint-head-meta">
                      <span>{t('{n} 个问题', { n: backlogCount })}</span>
                      <span>·</span>
                      <span>{t('拖到上方冲刺进行规划')}</span>
                    </div>
                  </div>
                </>
              }
              footer={
                <button className="btn ghost sm" onClick={() => openCreate({ project: key })}>
                  <FieldIcon name="create" size={12} /> {t('创建问题')}
                </button>
              }
            />
          </div>
        </div>
        <DragOverlay>{drag && <Row issue={drag.issue} />}</DragOverlay>
      </DndContext>

      {starting && <StartSprintModal sprint={starting} onClose={() => setStarting(null)} onDone={refresh} />}
      {editing && <StartSprintModal sprint={editing} edit onClose={() => setEditing(null)} onDone={refresh} />}
      {completing && (
        <CompleteSprintModal
          sprint={completing}
          issues={data.sprints.find((s) => s.id === completing.id)?.issues ?? []}
          targets={data.sprints.filter((s) => s.id !== completing.id)}
          onClose={() => setCompleting(null)}
          onDone={refresh}
        />
      )}
    </div>
  )
}

function EpicPanel({
  epics,
  counts,
  filter,
  dragging,
  onFilter,
  onOpen,
  onCreate,
}: {
  epics: { id: number; key: string; title: string }[]
  counts: { counts: Map<number, number>; none: number; total: number }
  filter: EpicFilter
  dragging: boolean
  onFilter: (f: EpicFilter) => void
  onOpen: (key: string) => void
  onCreate: () => void
}) {
  return (
    <aside className={`backlog-epic-panel card${dragging ? ' drop-ready' : ''}`}>
      <div className="card-header" style={{ flexWrap: 'wrap', gap: 6 }}>
        <h3>{t('史诗')}</h3>
        <InfoTip topic="epic" />
        <span className="spacer" />
        <button className="btn ghost sm" onClick={onCreate}>
          {t('＋ 创建史诗')}
        </button>
      </div>
      <div className="tiny faint" style={{ padding: '0 12px 8px' }}>
        {t('点击筛选；拖入问题可改归属')}
      </div>
      <EpicDropRow
        id="epic:all"
        droppable={false}
        active={filter === 'all'}
        onClick={() => onFilter('all')}
        label={t('全部问题')}
        count={counts.total}
      />
      <EpicDropRow
        id="epic:none"
        droppable
        active={filter === 'none'}
        onClick={() => onFilter('none')}
        label={t('无史诗')}
        count={counts.none}
        color="var(--text-3)"
      />
      {epics.length === 0 && (
        <div className="small faint" style={{ padding: '8px 12px' }}>{t('还没有史诗')}</div>
      )}
      {epics.map((epic) => (
        <EpicDropRow
          key={epic.id}
          id={`epic:${epic.id}`}
          droppable
          active={filter === epic.id}
          onClick={() => onFilter(epic.id)}
          onOpen={() => onOpen(epic.key)}
          label={epic.title}
          keyLabel={epic.key}
          count={counts.counts.get(epic.id) ?? 0}
          color={epicAccent(epic.id)}
        />
      ))}
    </aside>
  )
}

function EpicDropRow({
  id,
  droppable,
  active,
  onClick,
  onOpen,
  label,
  keyLabel,
  count,
  color,
}: {
  id: string
  droppable: boolean
  active: boolean
  onClick: () => void
  onOpen?: () => void
  label: string
  keyLabel?: string
  count: number
  color?: string
}) {
  const { setNodeRef, isOver } = useDroppable({ id, disabled: !droppable })
  return (
    <div
      ref={droppable ? setNodeRef : undefined}
      className={`backlog-epic-row${active ? ' active' : ''}${isOver ? ' over' : ''}`}
      onClick={onClick}
    >
      <span className="backlog-epic-dot" style={{ background: color ?? 'var(--border-strong)' }} />
      <div className="backlog-epic-text">
        {keyLabel && (
          <button
            type="button"
            className="issue-key backlog-epic-key"
            onClick={(e) => {
              e.stopPropagation()
              onOpen?.()
            }}
          >
            {keyLabel}
          </button>
        )}
        <span className="ellipsis" title={label}>{label}</span>
      </div>
      <span className="count">{count}</span>
    </div>
  )
}

function Container({
  id,
  anchorId,
  issues,
  header,
  footer,
  onOpen,
  tone = 'future',
  banner,
  collapsed,
  dropTarget,
  onToggleCollapse,
}: {
  id: string
  anchorId?: string
  issues: Issue[]
  header: ReactNode
  footer?: ReactNode
  onOpen: (key: string) => void
  tone?: SprintTone
  banner?: ReactNode
  collapsed?: boolean
  /** True while a dragged issue is aimed at this sprint/backlog. */
  dropTarget?: boolean
  onToggleCollapse?: () => void
}) {
  const { setNodeRef, isOver } = useDroppable({ id })
  const highlight = dropTarget || isOver
  const onHeadClick = (e: ReactMouseEvent) => {
    if (!onToggleCollapse) return
    const target = e.target as HTMLElement
    // Keep action buttons / tips interactive without toggling.
    if (target.closest('.sprint-head-actions, button, a, input, textarea, select, .info-tip, [data-tip]')) return
    onToggleCollapse()
  }
  return (
    <div
      className={`card sprint-box tone-${tone}${collapsed ? ' collapsed' : ''}${highlight ? ' drop-over' : ''}`}
      id={anchorId}
      ref={setNodeRef}
    >
      <div
        className="card-header sprint-head"
        style={{ flexWrap: 'wrap' }}
        role="button"
        tabIndex={0}
        aria-expanded={!collapsed}
        title={collapsed ? t('点击展开') : t('点击折叠')}
        onClick={onHeadClick}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            onToggleCollapse?.()
          }
        }}
      >
        {header}
      </div>
      <div className="sprint-fold-panel" aria-hidden={collapsed || undefined}>
        <div className="sprint-fold-inner">
          {banner}
          <div className={`drop-zone ${highlight ? 'over' : ''}`}>
            <SortableContext items={issues.map((i) => i.key)} strategy={verticalListSortingStrategy}>
              {issues.map((issue) => (
                <SortableRow key={issue.key} issue={issue} onOpen={() => onOpen(issue.key)} />
              ))}
            </SortableContext>
            {issues.length === 0 && <div className="small faint" style={{ padding: '12px 16px' }}>{t('将问题拖到这里')}</div>}
          </div>
          {footer && <div className="sprint-footer">{footer}</div>}
        </div>
      </div>
    </div>
  )
}

function SprintMiniBars({ metrics }: { metrics: SprintMetrics }) {
  const total = Math.max(1, metrics.total)
  return (
    <div className="sprint-mini-bars" title={t('完成 {pct}%', { pct: metrics.issuePct })}>
      <div className="sprint-mini-track">
        <i style={{ width: `${(metrics.done / total) * 100}%`, background: 'var(--success)' }} />
        <i style={{ width: `${(metrics.doing / total) * 100}%`, background: 'var(--accent)' }} />
        <i style={{ width: `${(metrics.todo / total) * 100}%`, background: 'var(--surface-3)' }} />
      </div>
      <span className="sprint-mini-pct">{metrics.issuePct}%</span>
    </div>
  )
}

function ActiveSprintBanner({
  sprint,
  metrics,
  clock,
}: {
  sprint: Sprint
  metrics: SprintMetrics
  clock: ReturnType<typeof timeProgress>
}) {
  const ahead = clock ? metrics.issuePct - clock.pct : 0
  const pace =
    !clock ? null
    : ahead >= 5 ? t('进度领先')
    : ahead <= -5 ? t('进度落后')
    : t('进度正常')
  return (
    <div className="sprint-active-banner">
      <div className="sprint-active-grid">
        <div className="sprint-stat-card">
          <div className="sprint-stat-label">{t('问题完成')}</div>
          <div className="sprint-stat-value">
            <b>{metrics.issuePct}</b><span>%</span>
          </div>
          <div className="sprint-stat-bar">
            <i style={{ width: `${metrics.issuePct}%` }} />
          </div>
          <div className="sprint-stat-sub">{metrics.done} / {metrics.total}</div>
        </div>
        <div className="sprint-stat-card">
          <div className="sprint-stat-label">{t('故事点')}</div>
          <div className="sprint-stat-value">
            <b>{metrics.pointPct}</b><span>%</span>
          </div>
          <div className="sprint-stat-bar points">
            <i style={{ width: `${metrics.pointPct}%` }} />
          </div>
          <div className="sprint-stat-sub">{metrics.donePoints} / {metrics.points} pt</div>
        </div>
        <div className="sprint-stat-card">
          <div className="sprint-stat-label">{t('时间进度')}</div>
          <div className="sprint-stat-value">
            {clock ? <><b>{clock.pct}</b><span>%</span></> : <b className="faint">—</b>}
          </div>
          <div className="sprint-stat-bar time">
            <i style={{ width: `${clock?.pct ?? 0}%` }} />
          </div>
          <div className="sprint-stat-sub">
            {clock ? t('第 {day}/{days} 天 · 剩余 {left} 天', { day: clock.day, days: clock.days, left: clock.left }) : t('未设置日期')}
          </div>
        </div>
        <div className="sprint-stat-card status">
          <div className="sprint-stat-label">{t('状态分布')}</div>
          <div className="sprint-stack-chart" aria-hidden>
            <i style={{ flex: Math.max(metrics.todo, 0.001) }} className="todo" />
            <i style={{ flex: Math.max(metrics.doing, 0.001) }} className="doing" />
            <i style={{ flex: Math.max(metrics.done, 0.001) }} className="done" />
          </div>
          <div className="sprint-stack-legend">
            <span><i className="todo" />{t('待办')} {metrics.todo}</span>
            <span><i className="doing" />{t('进行中')} {metrics.doing}</span>
            <span><i className="done" />{t('已完成')} {metrics.done}</span>
          </div>
          {pace && <div className={`sprint-pace ${ahead >= 5 ? 'ahead' : ahead <= -5 ? 'behind' : 'ok'}`}>{pace}</div>}
        </div>
      </div>
      {sprint.goal && (
        <div className="sprint-goal-line">
          <FieldIcon name="sprint" size={13} />
          <span className="ellipsis" title={sprint.goal}>{sprint.goal}</span>
        </div>
      )}
    </div>
  )
}

function SortableRow({ issue, onOpen }: { issue: Issue; onOpen: () => void }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: issue.key })
  return (
    <div ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.4 : 1 }} {...attributes} {...listeners}>
      <Row issue={issue} onOpen={onOpen} />
    </div>
  )
}

function Row({ issue, onOpen }: { issue: Issue; onOpen?: () => void }) {
  return (
    <div className="backlog-row">
      <TypeIcon type={issue.type} />
      <span className="issue-key" style={{ width: 74 }}>
        {issue.key}
      </span>
      <span className="title" onClick={onOpen}>
        {issue.title}
      </span>
      {issue.epic && (
        <span className="tag" style={{ color: epicAccent(issue.epic.id) }}>
          {issue.epic.title}
        </span>
      )}
      {issue.machines.slice(0, 2).map((m) => (
        <span key={m.id} className="machine-chip">
          {m.group}/{t(m.name)}
        </span>
      ))}
      <SubtaskBadge progress={issue.subtasks} />
      <StatusPill status={issue.status} />
      {issue.story_points !== null && <span className="count">{issue.story_points}</span>}
      <PriorityIcon priority={issue.priority} />
      <Avatar user={issue.assignee} size={22} />
    </div>
  )
}

function StartSprintModal({ sprint, edit, onClose, onDone }: { sprint: Sprint; edit?: boolean; onClose: () => void; onDone: () => void }) {
  const { toast } = useApp()
  const today = new Date()
  const plus = (days: number) => new Date(today.getTime() + days * 86400000).toISOString().slice(0, 10)
  const [name, setName] = useState(sprint.name)
  const [goal, setGoal] = useState(sprint.goal)
  const [start, setStart] = useState(sprint.start_date?.slice(0, 10) ?? plus(0))
  const [end, setEnd] = useState(sprint.end_date?.slice(0, 10) ?? plus(14))
  const submit = async () => {
    try {
      const body = { name, goal, start_date: start || null, end_date: end || null }
      if (edit) await api.put(`/api/sprints/${sprint.id}`, body)
      else await api.post(`/api/sprints/${sprint.id}/start`, body)
      toast(edit ? t('已保存') : t('{name} 已开始', { name }), 'success')
      onDone()
      onClose()
    } catch (err) {
      toast((err as Error).message, 'error')
    }
  }
  return (
    <Modal
      title={edit ? t('编辑冲刺') : t('开始冲刺')}
      size="narrow"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            {t('取消')}
          </button>
          <button className="btn primary" onClick={submit} disabled={!name.trim()}>
            {edit ? t('保存') : t('开始冲刺')}
          </button>
        </>
      }
    >
      <div className="col" style={{ gap: 12 }}>
        <div className="field">
          <label>{t('名称')}</label>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="grid grid-2" style={{ gap: 12 }}>
          <div className="field">
            <label>{t('开始日期')}</label>
            <DateField value={start} onChange={setStart} />
          </div>
          <div className="field">
            <label>{t('结束日期')}</label>
            <DateField value={end} onChange={setEnd} />
          </div>
        </div>
        <div className="field">
          <label>
            {t('冲刺目标')}
            <InfoTip topic="sprint_goal" />
          </label>
          <textarea className="textarea" value={goal} onChange={(e) => setGoal(e.target.value)} />
        </div>
      </div>
    </Modal>
  )
}

function CompleteSprintModal({ sprint, issues, targets, onClose, onDone }: { sprint: Sprint; issues: Issue[]; targets: Sprint[]; onClose: () => void; onDone: () => void }) {
  const { toast } = useApp()
  const open = issues.filter((i) => i.status.category !== 'done')
  const [target, setTarget] = useState<number | ''>('')
  const submit = async () => {
    try {
      await api.post(`/api/sprints/${sprint.id}/complete`, { move_open_to: target === '' ? null : target })
      toast(t('{name} 已完成', { name: sprint.name }), 'success')
      onDone()
      onClose()
    } catch (err) {
      toast((err as Error).message, 'error')
    }
  }
  return (
    <Modal
      title={t('完成 {name}', { name: sprint.name })}
      size="narrow"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            {t('取消')}
          </button>
          <button className="btn primary" onClick={submit}>
            {t('完成冲刺')}
          </button>
        </>
      }
    >
      <p>
        {t('已完成 {done} 个，未完成 {open} 个。')
          .split(/(\{done\}|\{open\})/)
          .map((part, i) =>
            part === '{done}' ? <b key={i}>{issues.length - open.length}</b> : part === '{open}' ? <b key={i}>{open.length}</b> : part,
          )}
      </p>
      {open.length > 0 && (
        <div className="field" style={{ marginTop: 12 }}>
          <label>{t('未完成的问题移至')}</label>
          <Select className="select" value={target} onChange={(e) => setTarget(e.target.value ? Number(e.target.value) : '')}>
            <option value="">{t('待办事项')}</option>
            {targets.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </div>
      )}
    </Modal>
  )
}
