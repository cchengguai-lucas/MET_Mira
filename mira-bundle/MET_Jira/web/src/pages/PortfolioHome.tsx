import { useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { Empty, InfoTip, Spinner } from '../components/ui'
import { useApp } from '../context'
import { t } from '../i18n'
import { confidenceLabel, englishGreeting, scheduleTagLabel, signedDaysLabel, timeAgo } from '../labels'
import { activityPreview, PortfolioRecentActivity, type PortfolioActivityItem } from './PortfolioActivity'
import {
  PortfolioScopeSelect,
  ProjectFolderRack,
  WorkspaceSwitch,
  type PortfolioScope,
  type WorkspaceView,
} from './portfolioShared'
import './PortfolioHome.css'

interface PortfolioAction {
  project_key: string
  project_name: string
  key: string
  title: string
  baseline_due: string | null
  current_due: string | null
  forecast_due: string | null
  suggested_due: string | null
  suggested_priority: string | null
  reason: string | null
  depth: number
  schedule_urgency: string
  urgency_score: number
  target_impact_days: number
  variance_days: number
  delay_sources: string[]
  constraining_predecessors: string[]
  confidence: string
}

interface ProjectGoal {
  title: string
  blurb: string
  birth: string
  target_end: string
  days_remaining: number
  actual_pct: number
  plan_pct: number
  delta_pct: number
  pace: 'ahead' | 'on_track' | 'behind'
  done: number
  total: number
  open: number
}

interface PortfolioCard {
  id: number
  key: string
  name: string
  color: string
  icon?: string | null
  cover_url: string | null
  health: { score: number | null; level: 'good' | 'attention' | 'risk'; counts: Record<string, number> }
  cascade_count: number
  due_slips: number
  sla_warning: number
  sla_breached: number
  risk_rank: number
  target: {
    committed_date: string | null
    forecast_date: string | null
    impact_days: number
    confidence: string
    status: 'at_risk' | 'on_track' | 'no_baseline'
    baseline_version: number | null
  }
  risk_events: { new: number; worsening: number; snoozed: number; decisions: number }
  risk_items: {
    id: number
    key: string
    title: string
    status: 'new' | 'worsening' | 'acknowledged'
    severity: string
    forecast_due: string | null
    variance_days: number
    first_seen_at: string
    last_seen_at: string
  }[]
  impacted_gates: { key: string; title: string; labels: string[]; forecast_due: string | null; variance_days: number }[]
  data_quality: { warnings: string[]; cycles: string[][]; missing_due_keys: string[] }
  delay_sources: {
    key: string
    title: string
    baseline_due: string | null
    due_date: string | null
    forecast_due: string | null
  }[]
  goal: ProjectGoal
  completion: {
    actual_pct: number
    plan_pct: number
    birth?: string
    plan_end?: string
    spark: { date: string; actual_pct: number; plan_pct: number }[]
    points: { date: string; actual_pct: number; plan_pct: number }[]
  }
  last_activity?: PortfolioActivityItem | null
}

interface PortfolioOverview {
  projects: PortfolioCard[]
  actions: PortfolioAction[]
  recent_activity?: PortfolioActivityItem[]
  totals: {
    projects: number
    target_at_risk: number
    new_risks: number
    worsening_risks: number
    decisions: number
  }
  scope: 'all' | 'lead' | 'managed'
  generated_at: string
}

export function PortfolioHome({ onSwitchWorkspace }: { onSwitchWorkspace: (view: WorkspaceView) => void }) {
  const { user, openAi } = useApp()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const scope = (params.get('scope') as PortfolioScope | null) ?? (user.is_admin ? 'all' : 'lead')
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['portfolio', 'overview', scope],
    queryFn: () => api.get<PortfolioOverview>(`/api/portfolio/overview?scope=${scope}`),
  })
  const projects = data?.projects ?? []
  const requestedProject = params.get('project')

  useEffect(() => {
    if (!projects.length || (requestedProject && projects.some((project) => project.key === requestedProject))) return
    const next = new URLSearchParams(params)
    next.set('project', projects[0].key)
    next.set('scope', scope)
    next.set('view', 'delivery')
    setParams(next, { replace: true })
  }, [params, projects, requestedProject, scope, setParams])

  const active = useMemo(
    () => projects.find((project) => project.key === requestedProject) ?? projects[0] ?? null,
    [projects, requestedProject],
  )
  const projectActions = useMemo(
    () => (data?.actions ?? []).filter((a) => !active || a.project_key === active.key),
    [data?.actions, active],
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
    next.set('view', 'delivery')
    setParams(next)
  }
  const setScope = (value: PortfolioScope) => {
    const next = new URLSearchParams(params)
    next.set('scope', value)
    next.delete('project')
    next.set('view', 'delivery')
    setParams(next)
  }
  const scopeSelect = (
    <PortfolioScopeSelect scope={scope} isAdmin={user.is_admin} onChange={setScope} />
  )
  const activeActions = projectActions.slice(0, 5)
  const greeting = englishGreeting()
  const openRiskAssessment = (project: PortfolioCard) => openAi([
    t('请使用 project_risk_snapshot 和 schedule_analysis 工具，简短评估项目 {key} 当前的目标风险。', { key: project.key }),
    t('用不超过 3 条要点回答：风险程度、主要延误源或受影响 Gate、管理者现在应优先做什么。不要编造工具结果之外的数据。'),
  ].join('\n'))

  return (
    <div className="page page-wide portfolio-home">
      <div className="page-header home-header">
        <div>
          <h1 className="home-greet">{`${greeting}, ${user.display_name}`}</h1>
        </div>
        <div className="home-header-actions">
          <WorkspaceSwitch value="delivery" onChange={onSwitchWorkspace} />
        </div>
      </div>

      {projects.length === 0 ? (
        <Empty title={t('当前范围没有交付项目')}>
          <div className="row portfolio-empty-scope" style={{ marginBottom: 12 }}>{scopeSelect}</div>
          {t('调整项目范围，或切换到持续运维查看无固定交期的项目')}
          <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" className="btn" onClick={() => onSwitchWorkspace('ongoing')}>{t('持续运维')}</button>
            <button type="button" className="btn ghost" onClick={() => onSwitchWorkspace('user')}>{t('用户工作台')}</button>
          </div>
        </Empty>
      ) : (
        <>
          {active && (
            <div className="control-tower-layout">
              <ProjectFolderRack
                title={t('项目风险排行')}
                ariaLabel={t('按风险排序的项目')}
                scopeSelect={scopeSelect}
                activeKey={active.key}
                onSelect={selectProject}
                projects={projects.map((project) => ({
                  id: project.id,
                  key: project.key,
                  name: project.name,
                  color: project.color,
                  score: project.risk_rank,
                  scoreClass: project.risk_rank >= 60 ? 'danger' : project.risk_rank ? 'warning' : undefined,
                  meta: (
                    <>
                      {project.target.impact_days > 0 ? t('目标 +{n} 天', { n: project.target.impact_days }) : t('目标暂无延期')}
                      {project.last_activity ? ` · ${timeAgo(project.last_activity.created_at)} · ${activityPreview(project.last_activity)}` : ''}
                    </>
                  ),
                }))}
              />

              <section className="control-project-detail">
                <div className="card control-target-head">
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="tiny faint">{active.key} · {t('明确目标')}</div>
                    <h2>{active.name}</h2>
                    <p className="muted">{active.goal.blurb || t('尚未填写项目目标说明')}</p>
                    <div className="callout row" style={{ marginTop: 10, padding: '8px 10px', gap: 8 }}>
                      <b className="small" style={{ whiteSpace: 'nowrap' }}>
                        ✦ {t('AI 风险简评')}
                        <InfoTip
                          mark="?"
                          text={t('短评由实时排期数据自动生成；点击深入解读后，AI 会读取授权的项目风险数据。')}
                        />
                      </b>
                      <span className="small" style={{ flex: 1 }}>{riskBrief(active)}</span>
                      <button
                        type="button"
                        className="btn ghost sm"
                        data-ai-toggle
                        onClick={() => openRiskAssessment(active)}
                      >
                        {t('深入解读')}
                      </button>
                    </div>
                  </div>
                  <div className={`target-confidence ${active.target.status}`}>
                    <b>{active.target.status === 'at_risk' ? t('目标有风险') : active.target.status === 'no_baseline' ? t('待发布基线') : t('目标可控')}</b>
                    <span>{t('预测可信度')} {confidenceLabel(active.target.confidence)}</span>
                  </div>
                </div>

                <div className="control-visual-grid">
                  <ProgressVisual project={active} />
                  <ForecastVisual project={active} />
                  <RiskVisual project={active} />
                  <TimelineMini actions={activeActions} onOpen={(issueKey) => navigate(`/projects/${active.key}/timeline?cascade=1&focus=${issueKey}`)} />
                </div>

                <DecisionStory
                  project={active}
                  actions={projectActions}
                  onOpen={(issueKey) => navigate(`/projects/${active.key}/timeline?cascade=1&focus=${issueKey}`)}
                  onOpenAll={() => navigate(`/projects/${active.key}/timeline?cascade=1`)}
                />
                <PortfolioRecentActivity
                  items={data.recent_activity ?? []}
                  activeKey={active.key}
                  summaryHref={`/projects/${active.key}/summary`}
                />
                {!!active.data_quality.warnings.length && <div className="callout warning">
                  <b>{t('数据质量')}</b>
                  {active.data_quality.warnings.map((warning) => <div className="small" key={warning}>• {warning}</div>)}
                </div>}
                <div className="row control-actions">
                  <span className="tiny faint">{t('更新于')} {new Date(data.generated_at).toLocaleString()}</span><span className="spacer" />
                  <Link to={`/projects/${active.key}/summary`} className="btn ghost">{t('项目摘要')}</Link>
                  <Link to={`/projects/${active.key}/timeline?cascade=1`} className="btn primary">{t('查看风险链与决策')}</Link>
                </div>
              </section>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function riskBrief(project: PortfolioCard): string {
  if (project.target.status === 'no_baseline') {
    return t('尚未发布计划基线，当前风险判断仅供参考；请先补齐计划日期并发布基线。')
  }
  if (project.target.status === 'at_risk') {
    const sources = project.delay_sources.slice(0, 3).map((source) => source.key).join('、')
    return sources
      ? t('目标预计延期 {days} 个工作日，风险主要从 {sources} 沿依赖链传导；{gates} 个 Gate 受影响，{decisions} 项待决策。', {
        days: project.target.impact_days,
        sources,
        gates: project.impacted_gates.length,
        decisions: project.risk_events.decisions,
      })
      : t('目标预计延期 {days} 个工作日，但尚未识别明确延误源；请优先检查依赖关系和计划日期。', {
        days: project.target.impact_days,
      })
  }
  const activeRisks = project.risk_events.new + project.risk_events.worsening
  return activeRisks
    ? t('当前预测未突破目标日，但仍有 {risks} 项新增或恶化风险、{gates} 个 Gate 受影响，建议持续跟踪。', {
      risks: activeRisks,
      gates: project.impacted_gates.length,
    })
    : t('当前预测未突破目标日，未发现新增或恶化风险；继续保持现有节奏并监控关键依赖。')
}

function DecisionStory({
  project,
  actions,
  onOpen,
  onOpenAll,
}: {
  project: PortfolioCard
  actions: PortfolioAction[]
  onOpen: (key: string) => void
  onOpenAll: () => void
}) {
  const late = project.target.impact_days > 0
  const maxGateDelay = Math.max(1, ...project.impacted_gates.map((gate) => gate.variance_days))
  const decisions = actions.slice(0, 4)
  const affectedBy = (sourceKey: string) => actions.filter((action) =>
    action.key !== sourceKey && action.delay_sources.includes(sourceKey)).length
  const urgencyLabel = (urgency: string) => urgency === 'critical'
    ? t('紧急')
    : urgency === 'high'
      ? t('高优先')
      : urgency === 'watch'
        ? t('关注')
        : t('常规')

  return <div className="decision-story-grid">
    <article className={`card decision-story-card outcome${late ? ' is-late' : ' is-safe'}`}>
      <StoryHeader step="01" title={t('能否按时完结？')} meta={late ? t('预计延期') : t('按期可控')} />
      <div className="outcome-hero">
        <div className="outcome-orb">
          <strong>{late ? `+${project.target.impact_days}` : '✓'}</strong>
          <span>{late ? t('工作日') : t('按期')}</span>
        </div>
        <div className="outcome-copy">
          <b>{late ? t('目标日存在滑移风险') : t('目标日当前可控')}</b>
          <span>{late
            ? t('预测晚于承诺 {n} 个工作日', { n: project.target.impact_days })
            : t('当前预测未突破目标日')}</span>
        </div>
      </div>
      <div className="outcome-date-path">
        <div><span>{t('承诺目标')}</span><b>{project.target.committed_date ?? '—'}</b></div>
        <i className={late ? 'late' : ''}>→</i>
        <div><span>{t('当前预测')}</span><b>{project.target.forecast_date ?? '—'}</b></div>
      </div>
      <div className="story-foot">
        <span>{t('基线')} v{project.target.baseline_version ?? '—'}</span>
        <span>{t('可信度')} {confidenceLabel(project.target.confidence)}</span>
        <span>{t('完成度')} {Math.round(project.goal.actual_pct)}%</span>
      </div>
    </article>

    <article className="card decision-story-card sources">
      <StoryHeader step="02" title={t('延误从哪里开始？')} meta={t('{n} 个源头', { n: project.delay_sources.length })} />
      {project.delay_sources.length ? <div className="source-flow-list">
        {project.delay_sources.slice(0, 4).map((source) => {
          const affected = affectedBy(source.key)
          return <button key={source.key} type="button" className="source-flow-row" onClick={() => onOpen(source.key)}>
            <span className="source-node"><i /></span>
            <span className="source-main">
              <span><b>{source.key}</b><span className="ellipsis">{source.title}</span></span>
              <small>
                {source.baseline_due ?? source.due_date ?? '—'} → <b>{source.forecast_due ?? source.due_date ?? '—'}</b>
              </small>
            </span>
            <span className="source-impact"><b>{affected}</b><small>{t('个后继')}</small></span>
          </button>
        })}
      </div> : <div className="story-empty safe"><b>✓</b><span>{t('未发现明确延误源')}</span></div>}
    </article>

    <article className="card decision-story-card gates">
      <StoryHeader step="03" title={t('哪些 Gate 受影响？')} meta={t('{n} 个节点', { n: project.impacted_gates.length })} />
      {project.impacted_gates.length ? <div className="gate-impact-list">
        {project.impacted_gates.slice(0, 4).map((gate) => (
          <button key={gate.key} type="button" className="gate-impact-row" onClick={() => onOpen(gate.key)}>
            <span className="gate-label">{scheduleTagLabel(gate.labels[0])}</span>
            <span className="gate-main">
              <span><b>{gate.key}</b><span className="ellipsis">{gate.title}</span></span>
              <i><em style={{ width: `${Math.max(14, gate.variance_days / maxGateDelay * 100)}%` }} /></i>
            </span>
            <span className="gate-delay"><b>{signedDaysLabel(gate.variance_days)}</b><small>{gate.forecast_due ?? '—'}</small></span>
          </button>
        ))}
      </div> : <div className="story-empty safe"><b>✓</b><span>{t('暂无标记为 Gate / Milestone 的受影响事项')}</span></div>}
    </article>

    <article className="card decision-story-card decisions">
      <StoryHeader step="04" title={t('现在需要什么决策？')} meta={t('{n} 项待处理', { n: actions.length })} />
      {decisions.length ? <div className="priority-decision-list">
        {decisions.map((action, index) => (
          <button key={action.key} type="button" className="priority-decision-row" onClick={() => onOpen(action.key)}>
            <span className={`decision-rank urgency-${action.schedule_urgency}`}>{index + 1}</span>
            <span className="decision-main">
              <span><b>{action.key}</b><span className="ellipsis">{action.title}</span></span>
              <small>
                {action.delay_sources.length
                  ? t('由 {keys} 延期传导', { keys: action.delay_sources.join('、') })
                  : t('受前置工作完成约束')}
              </small>
            </span>
            <span className="decision-shift">
              <small className={`urgency-text ${action.schedule_urgency}`}>{urgencyLabel(action.schedule_urgency)}</small>
              <b>{action.current_due ?? '—'} → {action.suggested_due ?? action.forecast_due ?? '—'}</b>
            </span>
          </button>
        ))}
        <button type="button" className="decision-all" onClick={onOpenAll}>
          {t('查看全部 {n} 项建议', { n: actions.length })}<span>→</span>
        </button>
      </div> : <div className="story-empty safe"><b>✓</b><span>{t('当前没有待处理的顺序决策')}</span></div>}
    </article>
  </div>
}

function StoryHeader({ step, title, meta }: { step: string; title: string; meta: string }) {
  return <div className="story-header">
    <span className="story-step">{step}</span>
    <h3>{title}</h3>
    <span className="story-meta">{meta}</span>
  </div>
}

function ProgressVisual({ project }: { project: PortfolioCard }) {
  const points = project.completion.points.slice(-45)
  const width = 320
  const height = 104
  const plot = (field: 'actual_pct' | 'plan_pct') => points.map((point, index) => {
    const x = points.length <= 1 ? 0 : (index / (points.length - 1)) * width
    const y = height - (Math.max(0, Math.min(100, point[field])) / 100) * height
    return `${x},${y}`
  }).join(' ')
  return <article className="card visual-card">
    <div className="visual-title"><span>{t('项目进展图')}</span><b>{Math.round(project.goal.actual_pct)}%</b></div>
    <div className="visual-chart">
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-label={t('项目实际与计划完成度')}>
        {[25, 50, 75].map((value) => <line key={value} x1="0" x2={width} y1={height - value / 100 * height} y2={height - value / 100 * height} className="visual-grid-line" />)}
        <polyline points={plot('plan_pct')} className="progress-plan-line" />
        <polyline points={plot('actual_pct')} className="progress-actual-line" />
      </svg>
    </div>
    <div className="visual-legend"><span><i className="actual" />{t('实际')} {Math.round(project.goal.actual_pct)}%</span><span><i className="plan" />{t('计划')} {Math.round(project.goal.plan_pct)}%</span></div>
  </article>
}

function ForecastVisual({ project }: { project: PortfolioCard }) {
  const impact = Math.max(0, project.target.impact_days)
  const committedX = 60
  const forecastX = impact
    ? Math.min(92, Math.max(committedX + 14, committedX + impact * 2.8))
    : committedX
  const crowded = impact > 0 && forecastX - committedX < 22
  return <article className="card visual-card">
    <div className="visual-title"><span>{t('目标预测图')}</span><b className={impact > 0 ? 'danger' : 'ok'}>{impact > 0 ? signedDaysLabel(impact) : t('按期')}</b></div>
    <div className={`forecast-visual${crowded ? ' crowded' : ''}${impact ? '' : ' on-time'}`}>
      <div className="forecast-track">
        <span className="forecast-safe" style={{ width: `${committedX}%` }} />
        {impact > 0 && <span className="forecast-delay-zone" style={{ left: `${committedX}%`, width: `${forecastX - committedX}%` }} />}
        <span className="forecast-marker committed" style={{ left: `${committedX}%` }}><i />{t('承诺')}</span>
        {impact > 0
          ? <span className="forecast-marker predicted late" style={{ left: `${forecastX}%` }}><i />{t('预测')}</span>
          : null}
      </div>
      <div className="forecast-dates">
        <span className="forecast-date committed" style={{ left: `${committedX}%` }}>{project.target.committed_date ?? '—'}</span>
        {impact > 0
          ? <span className="forecast-date predicted" style={{ left: `${forecastX}%` }}>{project.target.forecast_date ?? '—'}</span>
          : null}
      </div>
    </div>
    <div className="visual-foot">{t('可信度')} <b>{confidenceLabel(project.target.confidence)}</b> · {t('基线')} v{project.target.baseline_version ?? '—'}</div>
  </article>
}

function RiskVisual({ project }: { project: PortfolioCard }) {
  const rows = [
    { label: t('恶化'), value: project.risk_events.worsening, tone: 'critical' },
    { label: t('新增'), value: project.risk_events.new, tone: 'new' },
    { label: t('逾期'), value: project.health.counts.overdue ?? 0, tone: 'overdue' },
    { label: t('顺序影响'), value: project.cascade_count, tone: 'chain' },
  ]
  const max = Math.max(1, ...rows.map((row) => row.value))
  return <article className="card visual-card">
    <div className="visual-title"><span>{t('风险分布图')}</span><b>{project.risk_rank}</b></div>
    <div className="risk-bars">
      {rows.map((row) => <div className="risk-bar-row" key={row.label}>
        <span>{row.label}</span><div><i className={row.tone} style={{ width: `${row.value ? Math.max(10, row.value / max * 100) : 0}%` }} /></div><b>{row.value}</b>
      </div>)}
    </div>
    <div className="visual-foot">{project.risk_events.decisions} {t('项等待 PM 决策')}</div>
  </article>
}

function TimelineMini({ actions, onOpen }: { actions: PortfolioAction[]; onOpen: (key: string) => void }) {
  const dated = actions.filter((action) => action.baseline_due && action.forecast_due).slice(0, 5)
  const dates = dated.flatMap((action) => [action.baseline_due!, action.current_due ?? action.baseline_due!, action.forecast_due!]).map((value) => Date.parse(value))
  const min = dates.length ? Math.min(...dates) : 0
  const max = dates.length ? Math.max(...dates) : 1
  const position = (value: string | null) => value ? ((Date.parse(value) - min) / Math.max(86_400_000, max - min)) * 86 + 4 : 4
  return <article className="card visual-card timeline-mini-card">
    <div className="visual-title"><span>{t('顺序时间线缩略图')}</span><b>{dated.length}</b></div>
    {dated.length ? <div className="timeline-mini">
      {dated.map((action) => {
        const baseline = position(action.baseline_due)
        const current = position(action.current_due ?? action.baseline_due)
        const forecast = position(action.forecast_due)
        return <button key={action.key} onClick={() => onOpen(action.key)}>
          <span className="issue-key">{action.key}</span>
          <span className="mini-track">
            <i className="mini-baseline" style={{ left: `${baseline}%` }} />
            <i className="mini-current" style={{ left: `${Math.min(baseline, current)}%`, width: `${Math.max(2, Math.abs(current - baseline))}%` }} />
            <i className="mini-forecast" style={{ left: `${Math.min(current, forecast)}%`, width: `${Math.max(2, Math.abs(forecast - current))}%` }} />
          </span>
          <b>{signedDaysLabel(action.variance_days)}</b>
        </button>
      })}
    </div> : <div className="visual-empty">{t('当前没有可绘制的顺序延期')}</div>}
    <div className="visual-legend"><span><i className="baseline" />{t('基线')}</span><span><i className="current" />{t('当前')}</span><span><i className="forecast" />{t('预测')}</span></div>
  </article>
}

