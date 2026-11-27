import { useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { Avatar, Empty, Spinner } from '../components/ui'
import { useApp } from '../context'
import { LEVEL_COLOR, levelName, type HealthLevel } from '../health'
import { t } from '../i18n'
import { englishGreeting, fmtDate, timeAgo } from '../labels'
import { activityPreview, PortfolioRecentActivity, type PortfolioActivityItem } from './PortfolioActivity'
import {
  PortfolioScopeSelect,
  ProjectFolderRack,
  WorkspaceSwitch,
  type PortfolioScope,
  type WorkspaceView,
} from './portfolioShared'
import './PortfolioHome.css'

interface QueueIssue {
  id: number
  key: string
  title: string
  priority: string
  due_date: string | null
  sla_state: string
  status: { id: number; name: string; category: string; color: string }
  assignee: { id: number; username: string; display_name: string; avatar_url?: string | null; avatar_color?: string | null } | null
  updated_at: string | null
}

interface OngoingCard {
  id: number
  key: string
  name: string
  color: string
  icon?: string | null
  cover_url: string | null
  work_mode: 'ongoing'
  health: { score: number | null; level: HealthLevel; counts: Record<string, number> }
  pressure_rank: number
  flow: { days: number; created: number; completed: number; net: number }
  sla: { warning: number; breached: number }
  backlog: { overdue: number; blocked: number; stale: number; unassigned: number }
  flow_status: { todo: number; doing: number; sla_warning: number; sla_breached: number }
  throughput: { start: string; end: string; created: number; completed: number }[]
  assignee_load: { user: { id: number; username: string; display_name: string; avatar_url?: string | null; avatar_color?: string | null } | null; open: number }[]
  queues: { urgent: QueueIssue[]; stuck: QueueIssue[]; unassigned: QueueIssue[] }
  goal: { title: string; blurb: string; open: number; doing: number; todo: number }
  last_activity?: PortfolioActivityItem | null
}

interface OngoingOverview {
  projects: OngoingCard[]
  recent_activity?: PortfolioActivityItem[]
  totals: {
    projects: number
    open: number
    doing: number
    overdue: number
    blocked: number
    sla_warning: number
    sla_breached: number
    stale: number
    unassigned: number
    created: number
    completed: number
  }
  scope: 'all' | 'lead' | 'managed'
  generated_at: string
  flow_days: number
}

export function OngoingPortfolioHome({
  onSwitchWorkspace,
}: {
  onSwitchWorkspace: (view: WorkspaceView) => void
}) {
  const { user, openAi, openIssue } = useApp()
  const [params, setParams] = useSearchParams()
  const scope = (params.get('scope') as PortfolioScope | null) ?? (user.is_admin ? 'all' : 'lead')
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['portfolio', 'ongoing', scope],
    queryFn: () => api.get<OngoingOverview>(`/api/portfolio/ongoing?scope=${scope}`),
  })
  const projects = data?.projects ?? []
  const requestedProject = params.get('project')

  useEffect(() => {
    if (!projects.length || (requestedProject && projects.some((project) => project.key === requestedProject))) return
    const next = new URLSearchParams(params)
    next.set('project', projects[0].key)
    next.set('scope', scope)
    next.set('view', 'ongoing')
    setParams(next, { replace: true })
  }, [params, projects, requestedProject, scope, setParams])

  const active = useMemo(
    () => projects.find((project) => project.key === requestedProject) ?? projects[0] ?? null,
    [projects, requestedProject],
  )

  if (isLoading) return <Spinner center />
  if (isError || !data) {
    return (
      <div className="page page-wide">
        <Empty title={t('页面加载失败')}>
          <div className="muted small">{(error as Error)?.message || t('请稍后重试')}</div>
          <button type="button" className="btn" style={{ marginTop: 12 }} onClick={() => refetch()}>
            {t('重试')}
          </button>
        </Empty>
      </div>
    )
  }

  const selectProject = (key: string) => {
    const next = new URLSearchParams(params)
    next.set('project', key)
    next.set('scope', scope)
    next.set('view', 'ongoing')
    setParams(next)
  }
  const setScope = (value: PortfolioScope) => {
    const next = new URLSearchParams(params)
    next.set('scope', value)
    next.set('view', 'ongoing')
    next.delete('project')
    setParams(next)
  }
  const scopeSelect = (
    <PortfolioScopeSelect scope={scope} isAdmin={user.is_admin} onChange={setScope} />
  )
  const greeting = englishGreeting()
  const openOpsBrief = (project: OngoingCard) => openAi([
    t('请根据项目 {key} 的积压、SLA 与近 {days} 天吞吐，简短评估持续运维健康度。', {
      key: project.key,
      days: project.flow.days,
    }),
    t('用不超过 3 条要点回答：积压是否在恶化、谁或哪类事项最需要优先处理、管理者现在应做什么。不要编造工具结果之外的数据。'),
  ].join('\n'))

  return (
    <div className="page page-wide portfolio-home ongoing-home">
      <div className="page-header home-header">
        <div>
          <h1 className="home-greet">{`${greeting}, ${user.display_name}`}</h1>
        </div>
        <div className="home-header-actions">
          <WorkspaceSwitch value="ongoing" onChange={onSwitchWorkspace} />
        </div>
      </div>

      {projects.length === 0 ? (
        <Empty title={t('当前范围没有持续项目')}>
          <div className="row portfolio-empty-scope" style={{ marginBottom: 12 }}>{scopeSelect}</div>
          {t('调整项目范围，或新建持续项目以跟踪运维与杂项工作')}
          <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" className="btn" onClick={() => onSwitchWorkspace('delivery')}>{t('交付管理')}</button>
            <button type="button" className="btn ghost" onClick={() => onSwitchWorkspace('user')}>{t('用户工作台')}</button>
            <Link to="/projects" className="btn ghost">{t('项目列表')}</Link>
          </div>
        </Empty>
      ) : (
        <>
          {active && (
            <div className="control-tower-layout">
              <ProjectFolderRack
                title={t('持续项目排行')}
                ariaLabel={t('按积压压力排序的项目')}
                scopeSelect={scopeSelect}
                activeKey={active.key}
                onSelect={selectProject}
                projects={projects.map((project) => ({
                  id: project.id,
                  key: project.key,
                  name: project.name,
                  color: project.color,
                  score: project.health.score ?? '—',
                  scoreClass: healthTone(project.health.level),
                  meta: (
                    <>
                      {t('{n} 项未完结', { n: project.goal.open })}
                      {' · '}
                      {t('近{days}天 +{created} / -{completed}', {
                        days: project.flow.days,
                        created: project.flow.created,
                        completed: project.flow.completed,
                      })}
                      {project.last_activity ? ` · ${timeAgo(project.last_activity.created_at)} · ${activityPreview(project.last_activity)}` : ''}
                    </>
                  ),
                }))}
              />

              <section className="control-project-detail">
                <div className="card control-target-head">
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="tiny faint">{active.key} · {t('持续项目')}</div>
                    <h2>{active.name}</h2>
                    <p className="muted">{active.goal.blurb || t('尚未填写项目说明')}</p>
                    <div className="callout row" style={{ marginTop: 10, padding: '8px 10px', gap: 8 }}>
                      <b className="small" style={{ whiteSpace: 'nowrap' }}>✦ {t('AI 运维简评')}</b>
                      <span className="small" style={{ flex: 1 }}>{opsBrief(active)}</span>
                      <button type="button" className="btn ghost sm" data-ai-toggle onClick={() => openOpsBrief(active)}>
                        {t('深入解读')}
                      </button>
                    </div>
                  </div>
                  <div className={`target-confidence ${healthTone(active.health.level)}`}>
                    <b>{levelName(active.health.level)}</b>
                    <span>{t('健康分')} {active.health.score ?? '—'}</span>
                    <span>{netFlowLabel(active.flow.net)}</span>
                  </div>
                </div>

                <div className="control-visual-grid">
                  <ThroughputVisual project={active} />
                  <BacklogVisual project={active} />
                  <SlaFlowVisual project={active} />
                  <AssigneeLoadVisual project={active} />
                </div>

                <div className="decision-story-grid" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
                  <QueueCard
                    title={t('已逾期 / SLA 超时')}
                    meta={t('{n} 项', { n: active.queues.urgent.length })}
                    issues={active.queues.urgent}
                    empty={t('暂无紧急积压')}
                    onOpen={openIssue}
                  />
                  <QueueCard
                    title={t('被阻塞 / 久未更新')}
                    meta={t('{n} 项', { n: active.queues.stuck.length })}
                    issues={active.queues.stuck}
                    empty={t('暂无卡住的事项')}
                    onOpen={openIssue}
                  />
                  <QueueCard
                    title={t('待认领')}
                    meta={t('{n} 项', { n: active.queues.unassigned.length })}
                    issues={active.queues.unassigned}
                    empty={t('没有未指派工作')}
                    onOpen={openIssue}
                  />
                </div>

                <PortfolioRecentActivity
                  items={data.recent_activity ?? []}
                  activeKey={active.key}
                  summaryHref={`/projects/${active.key}/summary`}
                />

                <div className="row control-actions">
                  <span className="tiny faint">{t('更新于')} {new Date(data.generated_at).toLocaleString()}</span>
                  <span className="spacer" />
                  <Link to={`/projects/${active.key}/summary`} className="btn ghost">{t('项目摘要')}</Link>
                  <Link to={`/projects/${active.key}/board`} className="btn ghost">{t('进入看板')}</Link>
                  <Link to={`/projects/${active.key}/list`} className="btn primary">{t('问题列表')}</Link>
                </div>
              </section>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function healthTone(level: HealthLevel): string {
  return level === 'risk' ? 'at_risk' : level === 'attention' ? 'no_baseline' : ''
}

function netFlowLabel(net: number): string {
  if (net > 0) return t('净流入 +{n}', { n: net })
  if (net < 0) return t('净消化 {n}', { n: net })
  return t('吞吐平衡')
}

function opsBrief(project: OngoingCard): string {
  const parts = [
    t('当前未完结 {open} 项，近 {days} 天新增 {created}、完成 {completed}', {
      open: project.goal.open,
      days: project.flow.days,
      created: project.flow.created,
      completed: project.flow.completed,
    }),
  ]
  if (project.flow.net > 0) parts.push(t('积压仍在增加'))
  else if (project.flow.net < 0) parts.push(t('积压正在消化'))
  else parts.push(t('积压平稳'))
  const hot = [
    project.backlog.overdue ? t('{n} 项逾期', { n: project.backlog.overdue }) : '',
    project.sla.breached ? t('{n} 项 SLA 超时', { n: project.sla.breached }) : '',
    project.backlog.unassigned ? t('{n} 项未指派', { n: project.backlog.unassigned }) : '',
  ].filter(Boolean)
  if (hot.length) parts.push(t('需优先处理：{items}', { items: hot.join('、') }))
  else parts.push(t('暂无突出的紧急积压'))
  return `${parts[0]}；${parts.slice(1).join('；')}。`
}

function ThroughputVisual({ project }: { project: OngoingCard }) {
  const points = project.throughput.slice(-6)
  const max = Math.max(1, ...points.flatMap((point) => [point.created, point.completed]))
  const width = 320
  const height = 104
  const barWidth = points.length ? Math.min(18, (width / points.length) * 0.28) : 12
  return (
    <article className="card visual-card">
      <div className="visual-title">
        <span>{t('吞吐趋势图')}</span>
        <b className={project.flow.net > 0 ? 'danger' : 'ok'}>{netFlowLabel(project.flow.net)}</b>
      </div>
      <div className="visual-chart">
        <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-label={t('每周新增与完成对比')}>
          {[25, 50, 75].map((value) => (
            <line
              key={value}
              x1="0"
              x2={width}
              y1={height - (value / 100) * height}
              y2={height - (value / 100) * height}
              className="visual-grid-line"
            />
          ))}
          {points.map((point, index) => {
            const groupX = ((index + 0.5) / Math.max(1, points.length)) * width
            const createdH = (point.created / max) * height
            const completedH = (point.completed / max) * height
            return (
              <g key={`${point.start}-${point.end}`}>
                <rect
                  x={groupX - barWidth - 2}
                  y={height - createdH}
                  width={barWidth}
                  height={createdH}
                  rx="2"
                  fill="color-mix(in srgb, #ff9f0a 72%, transparent)"
                />
                <rect
                  x={groupX + 2}
                  y={height - completedH}
                  width={barWidth}
                  height={completedH}
                  rx="2"
                  fill="color-mix(in srgb, #30d158 78%, transparent)"
                />
              </g>
            )
          })}
        </svg>
      </div>
      <div className="visual-legend">
        <span><i className="forecast" />{t('新增')}</span>
        <span><i className="actual" />{t('完成')}</span>
      </div>
    </article>
  )
}

function BacklogVisual({ project }: { project: OngoingCard }) {
  const rows = [
    { label: t('逾期'), value: project.backlog.overdue, tone: 'overdue' },
    { label: t('阻塞'), value: project.backlog.blocked, tone: 'critical' },
    { label: t('久未更新'), value: project.backlog.stale, tone: 'chain' },
    { label: t('未指派'), value: project.backlog.unassigned, tone: 'new' },
  ]
  const max = Math.max(1, ...rows.map((row) => row.value))
  return (
    <article className="card visual-card">
      <div className="visual-title"><span>{t('积压健康分布')}</span><b>{project.pressure_rank}</b></div>
      <div className="risk-bars">
        {rows.map((row) => (
          <div className="risk-bar-row" key={row.label}>
            <span>{row.label}</span>
            <div><i className={row.tone} style={{ width: `${row.value ? Math.max(10, row.value / max * 100) : 0}%` }} /></div>
            <b>{row.value}</b>
          </div>
        ))}
      </div>
      <div className="visual-foot">{t('压力分')} {project.pressure_rank}</div>
    </article>
  )
}

function SlaFlowVisual({ project }: { project: OngoingCard }) {
  const rows = [
    { label: t('待办'), value: project.flow_status.todo, tone: 'new' },
    { label: t('进行中'), value: project.flow_status.doing, tone: 'chain' },
    { label: t('SLA 预警'), value: project.flow_status.sla_warning, tone: 'overdue' },
    { label: t('SLA 超时'), value: project.flow_status.sla_breached, tone: 'critical' },
  ]
  const max = Math.max(1, ...rows.map((row) => row.value))
  return (
    <article className="card visual-card">
      <div className="visual-title">
        <span>{t('SLA 与响应状态')}</span>
        <b>{project.sla.breached + project.sla.warning}</b>
      </div>
      <div className="risk-bars">
        {rows.map((row) => (
          <div className="risk-bar-row" key={row.label}>
            <span>{row.label}</span>
            <div><i className={row.tone} style={{ width: `${row.value ? Math.max(10, row.value / max * 100) : 0}%` }} /></div>
            <b>{row.value}</b>
          </div>
        ))}
      </div>
      <div className="visual-foot" style={{ color: LEVEL_COLOR[project.health.level] }}>
        {levelName(project.health.level)} · {t('健康分')} {project.health.score ?? '—'}
      </div>
    </article>
  )
}

function AssigneeLoadVisual({ project }: { project: OngoingCard }) {
  const rows = project.assignee_load
  const max = Math.max(1, ...rows.map((row) => row.open), 1)
  return (
    <article className="card visual-card">
      <div className="visual-title"><span>{t('负责人负载')}</span><b>{rows.length}</b></div>
      {rows.length ? (
        <div className="risk-bars">
          {rows.map((row) => {
            const label = row.user?.display_name ?? t('未指派')
            return (
              <div className="risk-bar-row" key={row.user?.id ?? 'none'}>
                <span title={label} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                  <Avatar user={row.user} size={16} />
                  <em className="ellipsis" style={{ fontStyle: 'normal', minWidth: 0 }}>{label}</em>
                </span>
                <div><i className="chain" style={{ width: `${Math.max(10, row.open / max * 100)}%` }} /></div>
                <b>{row.open}</b>
              </div>
            )
          })}
        </div>
      ) : <div className="visual-empty">{t('当前没有未完结工作')}</div>}
      <div className="visual-foot">{t('未完结 Top {n}', { n: Math.min(5, rows.length || 5) })}</div>
    </article>
  )
}

function QueueCard({
  title,
  meta,
  issues,
  empty,
  onOpen,
}: {
  title: string
  meta: string
  issues: QueueIssue[]
  empty: string
  onOpen: (key: string) => void
}) {
  return (
    <article className="card decision-story-card">
      <div className="story-header">
        <span className="story-step">•</span>
        <h3>{title}</h3>
        <span className="story-meta">{meta}</span>
      </div>
      {issues.length ? (
        <div className="priority-decision-list">
          {issues.map((issue) => (
            <button key={issue.id} type="button" className="priority-decision-row" onClick={() => onOpen(issue.key)}>
              <span className="decision-main">
                <span><b>{issue.key}</b><span className="ellipsis">{issue.title}</span></span>
                <small>
                  {issue.sla_state === 'breached' ? t('SLA 超时')
                    : issue.sla_state === 'warning' ? t('SLA 预警')
                      : issue.due_date ? t('截止 {date}', { date: fmtDate(issue.due_date) })
                        : issue.status.name}
                </small>
              </span>
              <Avatar user={issue.assignee} size={22} />
            </button>
          ))}
        </div>
      ) : (
        <div className="story-empty safe"><b>✓</b><span>{empty}</span></div>
      )}
    </article>
  )
}
