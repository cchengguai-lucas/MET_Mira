import '@fontsource/caveat/latin-600.css'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Suspense, lazy, useEffect, useRef, useState, type MouseEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { ActivityText } from '../components/ActivityText'
import { CountCard, Glyph, PriorityColumns, StatusDonut } from '../components/summaryCharts'
import { Avatar, Empty, PriorityIcon, slidingThumb, Spinner, StatusPill, TypeIcon } from '../components/ui'
import { useApp } from '../context'
import { LEVEL_COLOR, levelName, type HealthCore } from '../health'
import { t } from '../i18n'
import { englishGreeting, fmtDate, fmtDateTime, ISSUE_TYPES, PRIORITIES, timeAgo, typeName } from '../labels'
import { ProjectBadge } from '../projectIcons'
import { useProjects } from '../queries'
import { fetchMe } from '../shortcuts'
import type { Activity, Issue, IssueType, Priority, Status } from '../types'
import { PortfolioHome } from './PortfolioHome'
import { WorkspaceSwitch, type WorkspaceView } from './portfolioShared'
import './Home.css'

const OngoingPortfolioHome = lazy(() =>
  import('./OngoingPortfolioHome').then((module) => ({ default: module.OngoingPortfolioHome })),
)

/** Compact, labeled time signals — replaces the silent red rail that looked like “overdue”. */
function IssueTimeSignals({ issue, today }: { issue: Issue; today: string }) {
  const dueOverdue = !!issue.due_date && issue.due_date < today
  const sla = issue.sla_state
  const slaTip =
    sla === 'breached' || sla === 'warning'
      ? t('处理时限（SLA）{when}，与截止日期无关', {
          when: issue.sla_due_at ? fmtDateTime(issue.sla_due_at) : '',
        })
      : undefined
  return (
    <>
      {sla === 'breached' && (
        <span className="signal-chip danger" title={slaTip}>
          <SignalClock />
          {t('SLA 超时')}
        </span>
      )}
      {sla === 'warning' && (
        <span className="signal-chip warning" title={slaTip}>
          <SignalClock />
          {t('即将超时')}
        </span>
      )}
      {issue.due_date && (
        <span
          className={`signal-due${dueOverdue ? ' overdue' : ''}`}
          title={dueOverdue ? t('截止日期已过') : t('截止日期')}
        >
          {dueOverdue ? t('截止逾期') : null}
          {dueOverdue ? ' ' : null}
          {fmtDate(issue.due_date)}
        </span>
      )}
    </>
  )
}

function SignalClock() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  )
}

interface Dashboard {
  assigned: Issue[]
  reported: Issue[]
  to_test: Issue[]
  testing: Issue[]
  completed: Issue[]
}

interface PersonalOverview {
  total: number
  open: number
  counts: { completed: number; updated: number; created: number; due_soon: number; overdue: number }
  statuses: (Status & { count: number })[]
  priorities: { priority: Priority; count: number }[]
  types: { type: IssueType; count: number }[]
}

interface ActivityPage {
  total: number
  page: number
  size: number
  items: Activity[]
}

type ListTab = 'assigned' | 'to_test' | 'reported' | 'testing' | 'overdue' | 'completed'

function ripple(e: MouseEvent<HTMLElement>) {
  const btn = e.currentTarget
  const rect = btn.getBoundingClientRect()
  const dot = document.createElement('span')
  dot.className = 'stat-ripple'
  dot.style.left = `${e.clientX - rect.left}px`
  dot.style.top = `${e.clientY - rect.top}px`
  btn.appendChild(dot)
  dot.addEventListener('animationend', () => dot.remove())
}

function WatchedActivity() {
  const { openIssue } = useApp()
  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading } = useInfiniteQuery({
    queryKey: ['dashboard', 'activity'],
    queryFn: ({ pageParam }) => api.get<ActivityPage>(`/api/dashboard/activity?page=${pageParam}`),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.page * last.size < last.total ? last.page + 1 : undefined),
  })
  const items = data?.pages.flatMap((p) => p.items) ?? []
  const total = data?.pages[0]?.total ?? 0
  const sentinel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = sentinel.current
    if (!el || !hasNextPage) return
    const io = new IntersectionObserver(([entry]) => entry.isIntersecting && !isFetchingNextPage && fetchNextPage(), { rootMargin: '120px' })
    io.observe(el)
    return () => io.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage, items.length])

  return (
    <div className="card home-fill home-activity">
      <div className="card-header">
        <h3>{t('关注的动态')}</h3>
        {!!total && <span className="count">{total}</span>}
      </div>
      <div className="home-scroll">
        {isLoading && <Spinner center />}
        {data && total === 0 && (
          <Empty title={t('暂无动态')}>
            {t('关注问题后，其他人的更新会显示在这里')}
          </Empty>
        )}
        {items.map((a) => (
          <div
            key={a.id}
            className="activity-item compact home-press"
            style={{ cursor: a.issue ? 'pointer' : undefined }}
            onClick={() => a.issue && openIssue(a.issue.key)}
            onMouseEnter={(e) => {
              if (!e.currentTarget.title) e.currentTarget.title = (e.currentTarget.innerText || '').replace(/\s*\n\s*/g, ' · ')
            }}
          >
            <Avatar user={a.actor} size={20} />
            <div className="activity-lines">
              <div className="activity-line">
                <span className="ellipsis">
                  <ActivityText activity={a} />
                </span>
                <span className="tiny faint">{timeAgo(a.created_at)}</span>
              </div>
              {a.issue && (
                <div className="small ellipsis muted">
                  <span className="issue-key">{a.issue.key}</span> {a.issue.title}
                </div>
              )}
            </div>
          </div>
        ))}
        {hasNextPage && (
          <div ref={sentinel} className="activity-more tiny faint">
            {isFetchingNextPage ? t('加载中…') : ''}
          </div>
        )}
        {!hasNextPage && items.length > 15 && <div className="activity-more tiny faint">{t('没有更多了')}</div>}
      </div>
    </div>
  )
}

const CATEGORY_RANK = { todo: 0, doing: 1, done: 2 }

function byPriorityProgressNumber(a: Issue, b: Issue): number {
  return (
    PRIORITIES.indexOf(a.priority) - PRIORITIES.indexOf(b.priority) ||
    CATEGORY_RANK[b.status.category] - CATEGORY_RANK[a.status.category] ||
    b.status.sort_order - a.status.sort_order ||
    b.number - a.number
  )
}

function groupByType(issues: Issue[]): [IssueType, Issue[]][] {
  return ISSUE_TYPES.map((type) => [type, issues.filter((i) => i.type === type).sort(byPriorityProgressNumber)] as [IssueType, Issue[]]).filter(
    ([, list]) => list.length > 0,
  )
}

export function IssueRows({
  issues,
  empty = t('暂无'),
  grouped,
  compact,
  dense,
}: {
  issues: Issue[]
  empty?: string
  grouped?: boolean
  compact?: boolean
  dense?: boolean
}) {
  if (issues.length === 0) return <div className="empty small home-list-fade">{empty}</div>
  if (compact) return <CompactIssueList issues={issues} />
  if (!grouped) return <IssueList issues={issues} dense={dense} />
  return (
    <div className="home-list-fade">
      {groupByType(issues).map(([type, list]) => (
        <section key={type} className="issue-group">
          <div className="issue-group-head">
            <TypeIcon type={type} />
            <span>{typeName(type)}</span>
            <span className="count">{list.length}</span>
          </div>
          <IssueList issues={list} dense={dense} />
        </section>
      ))}
    </div>
  )
}

function IssueList({ issues, dense }: { issues: Issue[]; dense?: boolean }) {
  const { openIssue } = useApp()
  const today = new Date().toISOString().slice(0, 10)
  return (
    <div>
      {issues.map((issue) => (
        <div
          key={issue.id}
          className={`issue-row brief${dense ? ' home-issue-row' : ''}`}
          onClick={() => openIssue(issue.key)}
        >
          <TypeIcon type={issue.type} />
          <span className="issue-key" style={{ width: 74 }}>
            {issue.key}
          </span>
          <span className="title">{issue.title}</span>
          {!dense &&
            issue.machines.slice(0, 2).map((m) => (
              <span key={m.id} className="machine-chip">
                {m.group}/{m.name}
              </span>
            ))}
          <IssueTimeSignals issue={issue} today={today} />
          <PriorityIcon priority={issue.priority} />
          <StatusPill status={issue.status} />
          <Avatar user={issue.assignee} size={22} />
        </div>
      ))}
    </div>
  )
}

/** Two-line rows for narrow places (popovers): title on top, details underneath. */
function CompactIssueList({ issues }: { issues: Issue[] }) {
  const { openIssue } = useApp()
  const today = new Date().toISOString().slice(0, 10)
  return (
    <div>
      {issues.map((issue) => (
        <div key={issue.id} className="issue-row compact" onClick={() => openIssue(issue.key)}>
          <TypeIcon type={issue.type} />
          <div className="issue-row-main">
            <div className="issue-row-line">
              <span className="issue-key">{issue.key}</span>
              <span className="title" title={issue.title}>
                {issue.title}
              </span>
            </div>
            <div className="issue-row-meta">
              <PriorityIcon priority={issue.priority} />
              <IssueTimeSignals issue={issue} today={today} />
              {issue.machines.slice(0, 2).map((m) => (
                <span key={m.id} className="machine-chip" title={`${m.group}/${m.name}`}>
                  {m.group}/{m.name}
                </span>
              ))}
              {issue.machines.length > 2 && <span>+{issue.machines.length - 2}</span>}
            </div>
          </div>
          <StatusPill status={issue.status} />
          <Avatar user={issue.assignee} size={22} />
        </div>
      ))}
    </div>
  )
}

type ProjectHealth = HealthCore & { key: string; name: string; color: string; is_member: boolean }

function FilterChips({
  value,
  onChange,
  counts,
}: {
  value: ListTab
  onChange: (v: ListTab) => void
  counts: Record<ListTab, number>
}) {
  const items: { value: ListTab; label: string; tone?: 'warning' | 'danger' | 'success' }[] = [
    { value: 'assigned', label: t('分配给我 {n}', { n: counts.assigned }) },
    { value: 'to_test', label: t('待我测试 {n}', { n: counts.to_test }), tone: counts.to_test ? 'warning' : undefined },
    { value: 'reported', label: t('我报告的 {n}', { n: counts.reported }) },
    { value: 'testing', label: t('由我测试 {n}', { n: counts.testing }) },
    { value: 'overdue', label: t('已逾期 {n}', { n: counts.overdue }), tone: counts.overdue ? 'danger' : undefined },
    { value: 'completed', label: t('已完成 {n}', { n: counts.completed }), tone: counts.completed ? 'success' : undefined },
  ]
  return (
    <div className="tabs home-chips" ref={slidingThumb}>
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          className={`tab home-chip${value === item.value ? ' active' : ''}${item.tone ? ` tone-${item.tone}` : ''}`}
          onClick={(e) => {
            ripple(e)
            onChange(item.value)
          }}
        >
          {item.label}
        </button>
      ))}
    </div>
  )
}

export function HomePage() {
  const { user, openCreate } = useApp()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [switchingWorkspace, setSwitchingWorkspace] = useState(false)
  const switchTimer = useRef<number | null>(null)
  const me = useQuery({ queryKey: ['me'], queryFn: fetchMe, staleTime: 60_000 })
  const portfolioAccess = !!me.data?.portfolio_access
  const viewParam = params.get('view')
  const preferredView: WorkspaceView = me.data?.default_portfolio_view === 'ongoing'
    ? 'ongoing'
    : 'delivery'
  const activeView: WorkspaceView = !portfolioAccess || viewParam === 'mine'
    ? 'user'
    : viewParam === 'ongoing'
      ? 'ongoing'
      : viewParam === 'delivery'
        ? 'delivery'
        : preferredView
  useEffect(() => () => {
    if (switchTimer.current !== null) window.clearTimeout(switchTimer.current)
  }, [])
  // Pin the preferred tower into the URL once so later switches stay explicit.
  useEffect(() => {
    if (!portfolioAccess || viewParam != null || !me.data) return
    setParams(
      { view: preferredView === 'ongoing' ? 'ongoing' : 'delivery' },
      { replace: true },
    )
  }, [portfolioAccess, viewParam, me.data, preferredView, setParams])
  const switchWorkspace = (view: WorkspaceView) => {
    if (switchingWorkspace || view === activeView) return
    const duration = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 170
    setSwitchingWorkspace(true)
    switchTimer.current = window.setTimeout(() => {
      const next = new URLSearchParams()
      if (view === 'user') next.set('view', 'mine')
      else if (view === 'ongoing') next.set('view', 'ongoing')
      else next.set('view', 'delivery')
      setParams(next)
      requestAnimationFrame(() => requestAnimationFrame(() => setSwitchingWorkspace(false)))
    }, duration)
  }
  const content = activeView === 'delivery'
    ? <PortfolioHome onSwitchWorkspace={switchWorkspace} />
    : activeView === 'ongoing'
      ? (
        <Suspense fallback={<Spinner center />}>
          <OngoingPortfolioHome onSwitchWorkspace={switchWorkspace} />
        </Suspense>
      )
      : (
        <MyWorkHome
          user={user}
          openCreate={openCreate}
          navigate={navigate}
          showWorkspaceSwitch={portfolioAccess}
          onSwitchWorkspace={switchWorkspace}
        />
      )
  return (
    <div
      style={{
        opacity: switchingWorkspace ? 0 : 1,
        transform: switchingWorkspace ? 'translateY(8px) scale(0.995)' : 'translateY(0) scale(1)',
        transition: 'opacity 170ms ease, transform 170ms cubic-bezier(0.22, 1, 0.36, 1)',
        pointerEvents: switchingWorkspace ? 'none' : undefined,
      }}
    >
      {content}
    </div>
  )
}

function MyWorkHome({
  user,
  openCreate,
  navigate,
  showWorkspaceSwitch,
  onSwitchWorkspace,
}: {
  user: { display_name: string; org_path?: string | null }
  openCreate: () => void
  navigate: ReturnType<typeof useNavigate>
  showWorkspaceSwitch?: boolean
  onSwitchWorkspace?: (view: WorkspaceView) => void
}) {
  const projects = useProjects()
  const health = useQuery({
    queryKey: ['dashboard', 'health'],
    queryFn: () => api.get<{ projects: ProjectHealth[] }>('/api/dashboard/health'),
  })
  const overview = useQuery({
    queryKey: ['dashboard', 'overview'],
    queryFn: () => api.get<PersonalOverview>('/api/dashboard/overview'),
  })
  const healthByKey = new Map((health.data?.projects ?? []).map((h) => [h.key, h]))
  const severity = { risk: 0, attention: 1, good: 2 }
  const shownProjects = [...(projects.data ?? [])]
    .sort((a, b) => {
      const x = healthByKey.get(a.key)
      const y = healthByKey.get(b.key)
      return Number(!!y) - Number(!!x) || (x && y ? severity[x.level] - severity[y.level] : 0) || a.key.localeCompare(b.key)
    })
    .slice(0, 6)
  const [tab, setTab] = useState<ListTab>('assigned')
  const [statusFilter, setStatusFilter] = useState<string | null>(null)
  const [priorityFilter, setPriorityFilter] = useState<Priority | null>(null)
  const { data, isLoading } = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => api.get<Dashboard>('/api/dashboard'),
  })
  const listRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  // Greeting stays English regardless of UI locale (handwritten Caveat look).
  const greet = englishGreeting()
  const today = new Date().toISOString().slice(0, 10)
  const overdueIssues = (data?.assigned ?? []).filter((i) => i.due_date && i.due_date < today)
  const overdue = overdueIssues.length
  const lists: Record<ListTab, Issue[]> = {
    assigned: data?.assigned ?? [],
    to_test: data?.to_test ?? [],
    reported: data?.reported ?? [],
    testing: data?.testing ?? [],
    overdue: overdueIssues,
    completed: data?.completed ?? [],
  }
  const assigned = lists.assigned
  const statusMatches = statusFilter ? assigned.filter((i) => i.status.name === statusFilter) : null
  const priorityMatches = priorityFilter ? assigned.filter((i) => i.priority === priorityFilter) : null
  let queue = lists[tab]
  if (tab === 'assigned' && statusMatches && statusMatches.length) queue = statusMatches
  else if (tab === 'assigned' && priorityMatches && priorityMatches.length) queue = priorityMatches

  const flashList = () => {
    const el = listRef.current
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    el.classList.remove('flash')
    void el.offsetWidth
    el.classList.add('flash')
  }

  const jump = (target: ListTab) => {
    setTab(target)
    if (target !== 'assigned') {
      setStatusFilter(null)
      setPriorityFilter(null)
    }
    requestAnimationFrame(() => {
      scrollRef.current?.scrollTo({ top: 0 })
      flashList()
    })
  }

  const selectStatus = (name: string) => {
    setTab('assigned')
    setPriorityFilter(null)
    setStatusFilter((cur) => (cur === name ? null : name))
    requestAnimationFrame(() => {
      scrollRef.current?.scrollTo({ top: 0 })
      flashList()
    })
  }

  const selectPriority = (priority: Priority) => {
    setTab('assigned')
    setStatusFilter(null)
    setPriorityFilter((cur) => (cur === priority ? null : priority))
    requestAnimationFrame(() => {
      scrollRef.current?.scrollTo({ top: 0 })
      flashList()
    })
  }

  const counts: Record<ListTab, number> = {
    assigned: data?.assigned.length ?? 0,
    to_test: data?.to_test.length ?? 0,
    reported: data?.reported.length ?? 0,
    testing: data?.testing.length ?? 0,
    overdue,
    completed: data?.completed.length ?? 0,
  }
  const ov = overview.data
  const showFocus = !!(counts.to_test || overdue)

  return (
    <div className="page page-wide home-page">
      <div className="page-header home-header">
        <div>
          <h1 className="home-greet">{`${greet}, ${user.display_name}`}</h1>
          <p className="muted home-org">{user.org_path ?? t('欢迎使用 Mira')}</p>
        </div>
        <span className="spacer" />
        {showWorkspaceSwitch && onSwitchWorkspace && (
          <WorkspaceSwitch value="user" onChange={onSwitchWorkspace} />
        )}
        <button className="btn primary" onClick={() => openCreate()}>
          {t('＋ 创建问题')}
        </button>
      </div>

      {showFocus && (
        <div className="home-focus home-enter">
          <span className="home-focus-label">{t('需关注')}</span>
          {!!overdue && (
            <button
              type="button"
              className={`home-focus-pill danger${tab === 'overdue' ? ' on' : ''}`}
              onClick={(e) => {
                ripple(e)
                jump('overdue')
              }}
            >
              {t('逾期 {n}', { n: overdue })}
            </button>
          )}
          {!!counts.to_test && (
            <button
              type="button"
              className={`home-focus-pill warning${tab === 'to_test' ? ' on' : ''}`}
              onClick={(e) => {
                ripple(e)
                jump('to_test')
              }}
            >
              {t('待测 {n}', { n: counts.to_test })}
            </button>
          )}
        </div>
      )}

      <div className="card home-progress home-enter" style={{ animationDelay: '60ms' }}>
        <div className="home-progress-head">
          <div>
            <h3>{t('我的工作进展')}</h3>
            <div className="small faint">{t('按我负责的问题统计')}</div>
          </div>
        </div>
        {overview.isLoading || !ov ? (
          <div className="home-progress-skeleton" />
        ) : (
          <>
            <div className="home-progress-counts">
              <CountCard compact color="#30d158" value={ov.counts.completed} label={t('过去 7 天完成')} icon={<Glyph d="M5 12.5l4.2 4.2L19 7" size={18} />} />
              <CountCard compact color="#0a84ff" value={ov.counts.updated} label={t('过去 7 天更新')} icon={<Glyph d="M4 20h4L19 9l-4-4L4 16v4zM13.5 6.5l4 4" size={18} />} />
              <CountCard compact color="#af52de" value={ov.counts.created} label={t('过去 7 天创建')} icon={<Glyph d="M12 5v14M5 12h14" size={18} />} />
              <CountCard
                compact
                color="#ff9f0a"
                value={ov.counts.due_soon}
                label={t('未来 7 天到期')}
                sub={ov.counts.overdue ? t('另有 {n} 项已逾期', { n: ov.counts.overdue }) : undefined}
                icon={<Glyph d="M12 7v5l3 2M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17z" size={18} />}
                onClick={ov.counts.overdue ? () => jump('overdue') : undefined}
              />
            </div>
            <div className="home-progress-charts">
              <div className="home-progress-panel">
                <div className="home-progress-panel-title">{t('状态概览')}</div>
                {ov.total ? (
                  <StatusDonut statuses={ov.statuses} total={ov.total} size="md" selectedName={statusFilter} onSelect={selectStatus} />
                ) : (
                  <Empty title={t('暂无问题')} />
                )}
              </div>
              <div className="home-progress-panel">
                <div className="home-progress-panel-title">{t('优先级分布')}</div>
                <PriorityColumns rows={ov.priorities} selected={priorityFilter} onSelect={selectPriority} />
              </div>
            </div>
          </>
        )}
      </div>

      <div className="grid home-panels">
        <div className="col home-col">
          <div className="card jump-target home-fill" ref={listRef}>
            <div className="home-queue-head">
              <FilterChips
                value={tab}
                counts={counts}
                onChange={(v) => {
                  setTab(v)
                  if (v !== 'assigned') {
                    setStatusFilter(null)
                    setPriorityFilter(null)
                  }
                  scrollRef.current?.scrollTo({ top: 0 })
                }}
              />
              {(statusFilter || priorityFilter) && (
                <button
                  type="button"
                  className="btn ghost sm home-clear-filter"
                  onClick={() => {
                    setStatusFilter(null)
                    setPriorityFilter(null)
                  }}
                >
                  {statusFilter ? t('清除状态') : t('清除优先级')}
                </button>
              )}
            </div>
            <div className="home-scroll" ref={scrollRef} key={`${tab}:${statusFilter ?? ''}:${priorityFilter ?? ''}`}>
              {isLoading ? (
                <Spinner center />
              ) : (
                <IssueRows
                  issues={queue}
                  empty={
                    tab === 'overdue' ? t('没有逾期的工作')
                      : tab === 'completed' ? t('还没有已完成的工作')
                      : t('这里空空如也')
                  }
                  grouped
                  dense
                />
              )}
            </div>
          </div>
        </div>

        <div className="col home-col home-rail">
          {!!shownProjects.length && (
            <div className="card home-projects">
              <div className="card-header">
                <h3>{t('项目')}</h3>
                <Link to="/projects" className="small">
                  {t('全部')} →
                </Link>
              </div>
              {shownProjects.map((p) => {
                const h = healthByKey.get(p.key)
                return (
                  <Link key={p.id} to={`/projects/${p.key}/board`} className="issue-row home-project-row home-press">
                    <ProjectBadge icon={p.icon} color={p.color} label={p.key.slice(0, 2)} size={22} className="project-dot sm" />
                    <span className="title">{p.name}</span>
                    {h ? (
                      <span className="project-health">
                        <span className="tiny faint">
                          {h.counts.overdue ? t('逾期 {n}', { n: h.counts.overdue }) : t('{n} 进行中', { n: h.counts.open })}
                        </span>
                        <span
                          className="pill"
                          role="button"
                          title={t('健康分 {score}，点击查看项目健康度', { score: h.score })}
                          style={{ background: `color-mix(in srgb, ${LEVEL_COLOR[h.level]} 16%, transparent)`, color: LEVEL_COLOR[h.level] }}
                          onClick={(e) => {
                            e.preventDefault()
                            e.stopPropagation()
                            navigate(`/projects/${p.key}/reports`)
                          }}
                        >
                          {levelName(h.level)} <span className="score">{h.score}</span>
                        </span>
                      </span>
                    ) : (
                      <span className="tiny faint">{t('{n} 进行中', { n: p.open_count })}</span>
                    )}
                  </Link>
                )
              })}
            </div>
          )}
          <WatchedActivity />
        </div>
      </div>
    </div>
  )
}
