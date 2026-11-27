import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { ActivityText } from '../components/ActivityText'
import { CascadeDrawer, useCascade } from '../components/CascadeDrawer'
import { CompletionCurveChart } from '../components/CompletionCurve'
import { CountCard, Glyph, PriorityColumns, StatusDonut } from '../components/summaryCharts'
import { Avatar, Empty, Spinner, TypeIcon } from '../components/ui'
import { useApp } from '../context'
import { t } from '../i18n'
import { timeAgo, typeName } from '../labels'
import type { Activity, IssueRef, IssueType, Priority, Status, UserBrief } from '../types'
import { projectWorkMode } from '../types'
import { useProjectCtx } from './ProjectLayout'

interface Overview {
  total: number
  open: number
  counts: { completed: number; updated: number; created: number; due_soon: number; overdue: number }
  statuses: (Status & { count: number })[]
  priorities: { priority: Priority; count: number }[]
  types: { type: IssueType; count: number }[]
  workload: { user: UserBrief | null; count: number }[]
  epics: (IssueRef & { status: Status; total: number; done: number })[]
}

interface ActivityPage {
  total: number
  page: number
  size: number
  items: (Activity & { issue: { key: string; title: string; type: IssueType } | null })[]
}

const pct = (n: number, total: number) => (total ? Math.round((n / total) * 100) : 0)

function Panel({ title, sub, children, className = '' }: { title: string; sub?: string; children: ReactNode; className?: string }) {
  return (
    <div className={`card summary-panel ${className}`}>
      <div className="summary-panel-head">
        <h3>{title}</h3>
        {sub && <div className="small faint">{sub}</div>}
      </div>
      <div className="summary-panel-body">{children}</div>
    </div>
  )
}

function ShareRow({ label, value, total, color }: { label: ReactNode; value: number; total: number; color: string }) {
  return (
    <div className="summary-share">
      <div className="summary-share-label">{label}</div>
      <div className="summary-share-track">
        <div style={{ width: `${pct(value, total)}%`, background: color }} />
        <span>{pct(value, total)}%</span>
      </div>
      <span className="small faint" style={{ textAlign: 'right' }}>
        {value}
      </span>
    </div>
  )
}

function RecentActivity({ projectKey }: { projectKey: string }) {
  const { openIssue } = useApp()
  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading } = useInfiniteQuery({
    queryKey: ['project-activity', projectKey],
    queryFn: ({ pageParam }) => api.get<ActivityPage>(`/api/projects/${projectKey}/activity?page=${pageParam}`),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.page * last.size < last.total ? last.page + 1 : undefined),
  })
  const items = data?.pages.flatMap((p) => p.items) ?? []
  const sentinel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = sentinel.current
    if (!el || !hasNextPage) return
    const io = new IntersectionObserver(([entry]) => entry.isIntersecting && !isFetchingNextPage && fetchNextPage(), { rootMargin: '120px' })
    io.observe(el)
    return () => io.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage, items.length])
  if (isLoading) return <Spinner center />
  if (items.length === 0) return <Empty title={t('暂无动态')} />
  return (
    <div className="summary-activity">
      {items.map((a) => (
        <div key={a.id} className="activity-item compact" style={{ cursor: a.issue ? 'pointer' : undefined }} onClick={() => a.issue && openIssue(a.issue.key)}>
          <Avatar user={a.actor} size={24} />
          <div className="activity-lines">
            <div className="activity-line">
              <span className="ellipsis">
                <ActivityText activity={a} />
              </span>
              <span className="tiny faint">{timeAgo(a.created_at)}</span>
            </div>
            {a.issue && (
              <div className="small ellipsis muted row" style={{ gap: 6 }}>
                <TypeIcon type={a.issue.type} size={14} />
                <span className="issue-key">{a.issue.key}</span>
                <span className="ellipsis">{a.issue.title}</span>
              </div>
            )}
          </div>
        </div>
      ))}
      {hasNextPage && <div ref={sentinel} className="activity-more tiny faint">{isFetchingNextPage ? t('加载中…') : ''}</div>}
    </div>
  )
}

export function SummaryPage() {
  const project = useProjectCtx()
  const key = project.project.key
  const delivery = projectWorkMode(project.project) === 'delivery'
  const { openIssue } = useApp()
  const [cascadeOpen, setCascadeOpen] = useState(false)
  const { data, isLoading } = useQuery({
    queryKey: ['overview', key],
    queryFn: () => api.get<Overview>(`/api/projects/${key}/overview`),
  })
  const cascade = useCascade(key, delivery)
  const curve = useQuery({
    queryKey: ['completion-curve', key],
    queryFn: () => api.get(`/api/projects/${key}/reports/completion-curve?days=90`),
    enabled: delivery,
  })
  if (isLoading || !data) return <Spinner center />
  const { counts } = data
  const typeTotal = data.types.reduce((n, r) => n + r.count, 0)
  const cascadeN = cascade.data?.suggestion_count ?? 0
  const newRisks = cascade.data?.suggestions.filter((row) => row.risk_status === 'new').length ?? 0
  const worseningRisks = cascade.data?.suggestions.filter((row) => row.risk_status === 'worsening').length ?? 0
  return (
    <div className="summary-page">
      {delivery && (
        <div className="summary-risk-strip">
          <div className="summary-risk-chip">
            <span className="tiny faint">{t('目标预测')}</span>
            <b className={(cascade.data?.target_impact_days ?? 0) > 0 ? 'danger' : ''}>
              {cascade.data?.target_date ?? '—'} → {cascade.data?.forecast_target_date ?? '—'}
            </b>
          </div>
          <div className="summary-risk-chip">
            <span className="tiny faint">{t('新增 / 恶化风险')}</span>
            <b className={worseningRisks ? 'danger' : newRisks ? 'cascade' : ''}>{newRisks} / {worseningRisks}</b>
          </div>
          <div className="summary-risk-chip">
            <span className="tiny faint">{t('受影响 Gate')}</span>
            <b>{cascade.data?.impacted_gates.length ?? 0}</b>
          </div>
          <div className="summary-risk-chip">
            <span className="tiny faint">{t('待决策')}</span>
            <b className={cascadeN ? 'cascade' : ''}>{cascadeN}</b>
          </div>
          <span className="spacer" />
          <button
            type="button"
            className={`btn sm${cascadeN ? ' warning solid' : ''}`}
            disabled={!cascadeN}
            onClick={() => setCascadeOpen(true)}
          >
            {cascadeN ? t('改期建议 {n}', { n: cascadeN }) : t('查看改期建议')}
          </button>
          <Link className="btn sm ghost" to={`/projects/${key}/timeline?cascade=1`}>
            {t('时间线')}
          </Link>
        </div>
      )}

      <div className="summary-counts">
        <CountCard color="#30d158" value={counts.completed} label={t('过去 7 天完成')} icon={<Glyph d="M5 12.5l4.2 4.2L19 7" />} />
        <CountCard color="#0a84ff" value={counts.updated} label={t('过去 7 天更新')} icon={<Glyph d="M4 20h4L19 9l-4-4L4 16v4zM13.5 6.5l4 4" />} />
        <CountCard color="#af52de" value={counts.created} label={t('过去 7 天创建')} icon={<Glyph d="M12 5v14M5 12h14" />} />
        <CountCard
          color="#ff9f0a"
          value={counts.due_soon}
          label={t('未来 7 天到期')}
          sub={counts.overdue ? t('另有 {n} 项已逾期', { n: counts.overdue }) : undefined}
          icon={<Glyph d="M12 7v5l3 2M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17z" />}
        />
      </div>

      <div className="summary-grid">
        {delivery && (
          <Panel title={t('项目进展')} sub={t('横坐标时间 · 纵坐标完成度')} className="summary-curve-panel">
            {curve.data ? (
              <CompletionCurveChart data={curve.data as { actual_pct: number; plan_pct: number; points: { date: string; actual_pct: number; plan_pct: number }[] }} />
            ) : (
              <Spinner center />
            )}
          </Panel>
        )}
        <Panel title={t('状态概览')} sub={t('按类别与状态的分布')}>
          {data.total ? <StatusDonut statuses={data.statuses} total={data.total} /> : <Empty title={t('暂无问题')} />}
        </Panel>
        <Panel title={t('最近动态')} sub={t('项目内所有问题的最新变更')} className="summary-activity-panel">
          <RecentActivity projectKey={key} />
        </Panel>
        <Panel title={t('优先级分布')} sub={t('所有问题按优先级的数量')}>
          <PriorityColumns rows={data.priorities} />
        </Panel>
        <Panel title={t('工作类型')} sub={t('各类型问题占比')}>
          {data.types.map((r) => (
            <ShareRow
              key={r.type}
              total={typeTotal}
              value={r.count}
              color="var(--accent)"
              label={
                <>
                  <TypeIcon type={r.type} size={16} />
                  <span className="ellipsis">{typeName(r.type)}</span>
                </>
              }
            />
          ))}
        </Panel>
        <Panel title={t('团队工作量')} sub={t('未完成问题按负责人分布，共 {n} 项', { n: data.open })}>
          {data.workload.length === 0 && <div className="faint small">{t('没有未完成的问题')}</div>}
          <div className="summary-scroll">
            {data.workload.map((r) => (
              <ShareRow
                key={r.user?.id ?? 'none'}
                total={data.open}
                value={r.count}
                color={r.user ? 'var(--accent)' : 'var(--text-3)'}
                label={
                  r.user ? (
                    <Link to={`/people/${r.user.username}`} className="row" style={{ gap: 7, minWidth: 0, color: 'inherit' }}>
                      <Avatar user={r.user} size={20} />
                      <span className="ellipsis">{r.user.display_name}</span>
                    </Link>
                  ) : (
                    <>
                      <span className="summary-nobody">?</span>
                      <span className="ellipsis faint">{t('未分配')}</span>
                    </>
                  )
                }
              />
            ))}
          </div>
        </Panel>
        <Panel title={t('史诗进度')} sub={t('按史诗下已完成的问题计算')}>
          {data.epics.length === 0 && <div className="faint small">{t('还没有史诗')}</div>}
          <div className="summary-scroll">
            {data.epics.map((e) => (
              <button key={e.id} type="button" className="summary-epic" onClick={() => openIssue(e.key)}>
                <div className="row" style={{ gap: 7, minWidth: 0 }}>
                  <TypeIcon type="epic" size={16} />
                  <span className="issue-key">{e.key}</span>
                  <span className="ellipsis">{e.title}</span>
                  <span className="spacer" />
                  <span className="tiny faint">{t('{done}/{total} 完成', { done: e.done, total: e.total })}</span>
                </div>
                <div className="bar-track thin" style={{ marginTop: 6 }}>
                  <div className="bar-fill" style={{ width: `${pct(e.done, e.total)}%`, background: e.status.category === 'done' ? '#30d158' : 'var(--accent)' }} />
                </div>
              </button>
            ))}
          </div>
        </Panel>
      </div>
      <CascadeDrawer projectKey={key} open={cascadeOpen} onClose={() => setCascadeOpen(false)} />
    </div>
  )
}
