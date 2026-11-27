import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { lazy, Suspense, useEffect, useRef, useState, type SyntheticEvent } from 'react'
import { api, streamPost } from '../api'
import { BarList, ColumnChart, LineChart, StackedArea, StackedBar } from '../components/Charts'
import { Markdown } from '../components/Markdown'
import { Segmented, Spinner, Tabs, Select } from '../components/ui'
import { useApp } from '../context'
import { t, tServer } from '../i18n'
import { fmtDate, fmtDateTime, PRIORITIES, PRIORITY_META, priorityName, TYPE_META, typeName } from '../labels'
import { LEVEL_COLOR, levelName, type HealthCore, type SignalKey } from '../health'
import { useUrlState } from '../share'
import type { Issue, IssueType } from '../types'
import { IssueRows } from './Home'
import { useProjectCtx } from './ProjectLayout'
import { IssueListButton, StatTile, useReportWindow, WindowControls } from './reportKit'
const EfficiencyView = lazy(() => import('./ReportsFlow').then((module) => ({ default: module.EfficiencyView })))
const LoadView = lazy(() => import('./ReportsFlow').then((module) => ({ default: module.LoadView })))
const ForecastView = lazy(() => import('./ReportsPlanning').then((module) => ({ default: module.ForecastView })))
const SprintView = lazy(() => import('./ReportsPlanning').then((module) => ({ default: module.SprintView })))

const IDEOGRAPHIC_SPACE = String.fromCharCode(0x3000)

type AiReportItem = { id: number; period: string; title: string; content: string; scheduled: boolean; has_charts: boolean; created_at: string; can_delete: boolean; can_email: boolean }

type ReportUnits = {
  total: number
  open: number
  units: { id: number; label: string; path: string; depth: number; issues: number; open: number }[]
}

interface Summary {
  total: number
  open: number
  overdue: number
  by_status: { id: number; name: string; color: string; category: string; count: number }[]
  by_type: Record<string, number>
  by_priority: Record<string, number>
  by_assignee: { name: string; count: number }[]
  by_machine_group: { name: string; open: number; done: number }[]
  by_scope: Record<string, number>
  trend: { date: string; created: number; completed: number; rejected: number }[]
  test: { tested: number; passed: number; failed: number }
  test_window: { days: number; passed: number; failed: number }
}

type RiskKey = 'overdue' | 'due_soon' | 'blocked' | 'stale' | 'unassigned'

interface Health extends HealthCore {
  throughput: { start: string; end: string; created: number; completed: number; rejected: number; open: number }[]
  lists: Record<RiskKey | SignalKey, Issue[]>
}

interface Quality {
  days: number
  test_status: string
  in_test: number
  failing: number
  passed: number
  failed: number
  pass_rate: number | null
  failed_issues: number
  reopened: number
  weekly: { start: string; end: string; passed: number; failed: number; reopened: number }[]
  queue: Issue[]
}

const STALE_DAYS = 14

function signalText(key: SignalKey, n: number): string {
  switch (key) {
    case 'overdue':
      return t('{n} 项已逾期', { n })
    case 'blocked':
      return t('{n} 项被未完成的问题阻塞', { n })
    case 'stale':
      return t('{n} 项超过 {days} 天没有更新', { n, days: STALE_DAYS })
    case 'unassigned':
      return t('{n} 项未分配负责人', { n })
    case 'no_due':
      return t('{n} 项没有截止日期（超过未完成工作的一半）', { n })
    case 'net_flow':
      return t('近 4 周新建比完成多 {n} 项', { n })
  }
}

function signalListTitle(key: SignalKey): string {
  switch (key) {
    case 'overdue':
      return t('已逾期')
    case 'blocked':
      return t('被阻塞')
    case 'stale':
      return t('长期未更新')
    case 'unassigned':
      return t('未分配负责人')
    case 'no_due':
      return t('没有截止日期')
    case 'net_flow':
      return t('近 4 周新建且尚未完成')
  }
}

function SignalItem({ signal, issues, total }: { signal: HealthCore['signals'][number]; issues: Issue[]; total?: number }) {
  return (
    <IssueListButton className="health-signal" title={signalListTitle(signal.key)} issues={issues} total={total} empty={t('没有这类风险')}>
      <span className="ellipsis" style={{ flex: 1 }}>
        {signalText(signal.key, signal.count)}
      </span>
      <span className="tiny faint">−{signal.penalty}</span>
      <span className="chev">›</span>
    </IssueListButton>
  )
}

type ReportTab = 'health' | 'overview' | 'efficiency' | 'load' | 'quality' | 'flow' | 'sprint' | 'forecast' | 'ai'
const REPORT_TABS: ReportTab[] = ['health', 'overview', 'efficiency', 'load', 'quality', 'flow', 'sprint', 'forecast', 'ai']

export function ReportsPage() {
  const [params, update] = useUrlState()
  const requested = params.get('tab') === 'burndown' ? 'sprint' : params.get('tab')
  const tab = REPORT_TABS.find((x) => x === requested) ?? 'health'
  return (
    <div>
      <Tabs
        value={tab}
        onChange={(value: ReportTab) => update({ tab: value === 'health' ? null : value })}
        items={[
          { value: 'health', label: t('健康') },
          { value: 'overview', label: t('概览') },
          { value: 'efficiency', label: t('流动指标') },
          { value: 'load', label: t('工作负载') },
          { value: 'quality', label: t('质量') },
          { value: 'flow', label: t('累积流程图') },
          { value: 'sprint', label: t('冲刺') },
          { value: 'forecast', label: t('预测') },
          { value: 'ai', label: `✦ ${t('AI 周报')}` },
        ]}
      />
      <Suspense fallback={<Spinner center />}>
        {tab === 'health' && <HealthView />}
        {tab === 'overview' && <Overview />}
        {tab === 'efficiency' && <EfficiencyView />}
        {tab === 'load' && <LoadView />}
        {tab === 'quality' && <QualityView />}
        {tab === 'flow' && <Flow />}
        {tab === 'sprint' && <SprintView />}
        {tab === 'forecast' && <ForecastView />}
        {tab === 'ai' && <AiReports />}
      </Suspense>
    </div>
  )
}

function HealthView() {
  const project = useProjectCtx()
  const win = useReportWindow()
  const [riskTab, setRiskTab] = useState<RiskKey | null>(null)
  const { data } = useQuery({
    queryKey: ['reports', 'health', project.project.key, win.days, win.withSubtasks],
    queryFn: () => api.get<Health>(`/api/projects/${project.project.key}/reports/health?days=${win.days}&subtasks=${win.withSubtasks}`),
    placeholderData: keepPreviousData,
  })
  if (!data) return <Spinner center />
  const c = data.counts
  const risks: { key: RiskKey; label: string }[] = [
    { key: 'overdue', label: t('已逾期') },
    { key: 'due_soon', label: t('7 天内到期') },
    { key: 'blocked', label: t('被阻塞') },
    { key: 'stale', label: t('长期未更新') },
    { key: 'unassigned', label: t('未分配') },
  ]
  const current = riskTab ?? risks.find((r) => c[r.key] > 0)?.key ?? 'overdue'
  const color = LEVEL_COLOR[data.level]
  return (
    <div className="col" style={{ gap: 16 }}>
      <WindowControls win={win}>
        <span className="small muted">{t('健康分综合考虑逾期、阻塞、停滞、未分配，以及新建与完成的差额；史诗不计入工作量。')}</span>
      </WindowControls>
      <div className="grid health-top">
        <div className="card card-pad health-score">
          <div className="row" style={{ alignItems: 'baseline', gap: 10 }}>
            <span className="health-value" style={{ color }}>
              {data.score}
            </span>
            <span className="tiny faint">/ 100</span>
            <span className="spacer" />
            <span className="pill" style={{ background: `color-mix(in srgb, ${color} 16%, transparent)`, color }}>
              {levelName(data.level)}
            </span>
          </div>
          <div className="health-bar">
            <div style={{ width: `${data.score}%`, background: color }} />
          </div>
          {data.signals.length === 0 ? (
            <div className="small muted">{t('没有发现需要关注的风险')}</div>
          ) : (
            <div className="health-signals">
              {data.signals.map((sig) => (
                <SignalItem key={sig.key} signal={sig} issues={data.lists[sig.key] ?? []} total={sig.key === 'net_flow' ? undefined : c[sig.key]} />
              ))}
            </div>
          )}
        </div>
        <div className="grid grid-2" style={{ gap: 12 }}>
          <StatTile label={t('未完成')} value={c.open} sub={t('进行中 {n}', { n: c.doing })} />
          <StatTile label={t('已逾期')} value={c.overdue} color={c.overdue ? 'var(--danger)' : undefined} sub={t('无截止日期 {n}', { n: c.no_due })} />
          <StatTile label={t('7 天内到期')} value={c.due_soon} color={c.due_soon ? 'var(--warning)' : undefined} sub={t('被阻塞 {n}', { n: c.blocked })} />
          <StatTile
            label={t('近 {n} 天完成', { n: data.flow.days })}
            value={data.flow.completed}
            color="var(--success)"
            sub={t('新建 {created} · 驳回 {rejected}', { created: data.flow.created, rejected: data.flow.rejected })}
          />
        </div>
      </div>
      <div className="grid grid-2">
        <div className="card card-pad">
          <h3>{t('每周吞吐量')}</h3>
          <div className="small muted" style={{ marginBottom: 10 }}>
            {t('按周统计新建、完成和驳回；驳回不计入完成。横轴为每周起始日期。')}
          </div>
          <ColumnChart
            labels={data.throughput.map((w) => w.start)}
            series={[
              { name: t('新建'), color: '#0a84ff', values: data.throughput.map((w) => w.created) },
              { name: t('完成'), color: '#30d158', values: data.throughput.map((w) => w.completed) },
              { name: t('驳回'), color: '#8e8e93', values: data.throughput.map((w) => w.rejected) },
            ]}
          />
        </div>
        <div className="card card-pad">
          <h3>{t('未完成存量')}</h3>
          <div className="small muted" style={{ marginBottom: 10 }}>
            {t('每周最后一天仍未完成的问题数；持续上升说明积压在增加。')}
          </div>
          <LineChart labels={data.throughput.map((w) => w.end)} series={[{ name: t('未完成'), color: '#ff9f0a', area: true, values: data.throughput.map((w) => w.open) }]} />
        </div>
      </div>
      <div className="card">
        <div style={{ padding: '6px 16px 0' }}>
          <Tabs value={current} onChange={setRiskTab} items={risks.map((r) => ({ value: r.key, label: `${r.label} ${c[r.key]}` }))} />
        </div>
        <IssueRows issues={data.lists[current]} empty={t('没有这类风险')} />
        {c[current] > data.lists[current].length && (
          <div className="tiny faint" style={{ padding: '8px 16px' }}>
            {t('只显示前 {n} 项，共 {total} 项', { n: data.lists[current].length, total: c[current] })}
          </div>
        )}
      </div>
    </div>
  )
}

function QualityView() {
  const project = useProjectCtx()
  const win = useReportWindow()
  const { data } = useQuery({
    queryKey: ['reports', 'quality', project.project.key, win.days, win.withSubtasks],
    queryFn: () => api.get<Quality>(`/api/projects/${project.project.key}/reports/quality?days=${win.days}&subtasks=${win.withSubtasks}`),
    placeholderData: keepPreviousData,
  })
  if (!data) return <Spinner center />
  const first = data.weekly[0]?.start
  const last = data.weekly[data.weekly.length - 1]?.end
  return (
    <div className="col" style={{ gap: 16 }}>
      <WindowControls win={win}>
        <span className="small muted">{t('统计 {start} 至 {end} 的测试结果；同一问题的多轮测试分别计数。', { start: fmtDate(first), end: fmtDate(last) })}</span>
      </WindowControls>
      <div className="grid grid-4">
        <StatTile label={t('待测试')} value={data.in_test} color={data.in_test ? 'var(--warning)' : undefined} sub={t('状态「{status}」', { status: t(data.test_status) })} />
        <StatTile label={t('测试未通过')} value={data.failing} color={data.failing ? 'var(--danger)' : undefined} sub={t('期间有 {n} 个问题曾未通过测试', { n: data.failed_issues })} />
        <StatTile
          label={t('测试通过率')}
          value={data.pass_rate === null ? '–' : `${data.pass_rate}%`}
          sub={t('通过 {passed} / 未通过 {failed}', { passed: data.passed, failed: data.failed })}
        />
        <StatTile label={t('重新打开')} value={data.reopened} color={data.reopened ? 'var(--warning)' : undefined} sub={t('完成或未通过后返工的次数')} />
      </div>
      <div className="card card-pad">
        <h3 style={{ marginBottom: 12 }}>{t('每周测试结果')}</h3>
        <ColumnChart
          labels={data.weekly.map((w) => w.start)}
          series={[
            { name: t('通过'), color: '#30d158', values: data.weekly.map((w) => w.passed) },
            { name: t('未通过'), color: '#ff453a', values: data.weekly.map((w) => w.failed) },
            { name: t('重新打开'), color: '#ff9f0a', values: data.weekly.map((w) => w.reopened) },
          ]}
        />
      </div>
      <div className="card">
        <div className="card-header">
          <h3>{t('测试队列')}</h3>
          <span className="count">{data.queue.length}</span>
        </div>
        <IssueRows issues={data.queue} empty={t('当前没有待测试或未通过的问题')} />
      </div>
    </div>
  )
}

function Overview() {
  const project = useProjectCtx()
  const win = useReportWindow()
  const { days, withSubtasks } = win
  const { data } = useQuery({
    queryKey: ['reports', 'summary', project.project.key, days, withSubtasks],
    queryFn: () => api.get<Summary>(`/api/projects/${project.project.key}/reports/summary?days=${days}&subtasks=${withSubtasks}`),
    placeholderData: keepPreviousData,
  })
  if (!data) return <Spinner center />
  const tested = data.test_window.passed + data.test_window.failed
  const passRate = tested ? Math.round((data.test_window.passed / tested) * 100) : null
  return (
    <div className="col" style={{ gap: 16 }}>
      <WindowControls win={win} />
      <div className="grid grid-4">
        <div className="card stat">
          <div className="label">{t('问题总数')}</div>
          <div className="value">{data.total}</div>
        </div>
        <div className="card stat">
          <div className="label">{t('进行中')}</div>
          <div className="value">{data.open}</div>
        </div>
        <div className="card stat">
          <div className="label">{t('已逾期')}</div>
          <div className="value" style={{ color: data.overdue ? 'var(--danger)' : undefined }}>
            {data.overdue}
          </div>
        </div>
        <div className="card stat">
          <div className="label">{t('测试通过率')}</div>
          <div className="value">{passRate === null ? '–' : `${passRate}%`}</div>
          <div className="tiny faint">{t('近 {n} 天 · 通过 {passed} / 未通过 {failed}', { n: data.test_window.days, passed: data.test_window.passed, failed: data.test_window.failed })}</div>
        </div>
      </div>
      <div className="grid grid-2">
        <div className="card card-pad">
          <h3 style={{ marginBottom: 12 }}>{t('状态分布')}</h3>
          <StackedBar segments={data.by_status.map((s) => ({ label: t(s.name), value: s.count, color: s.color }))} />
        </div>
        <div className="card card-pad">
          <h3 style={{ marginBottom: 12 }}>{t('类型 / 优先级')}</h3>
          <div className="faint" style={{ fontSize: 12, marginBottom: 6 }}>{t('按类型（全部）')}</div>
          <StackedBar segments={Object.entries(data.by_type).map(([type, n]) => ({ label: typeName(type), value: n, color: TYPE_META[type as IssueType]?.color ?? '#999' }))} />
          <div className="faint" style={{ fontSize: 12, margin: '14px 0 6px' }}>{t('按优先级（未完成）')}</div>
          <StackedBar segments={PRIORITIES.filter((p) => data.by_priority[p]).map((p) => ({ label: priorityName(p), value: data.by_priority[p], color: PRIORITY_META[p].color }))} />
        </div>
      </div>
      <div className="card card-pad">
        <h3 style={{ marginBottom: 12 }}>{t('新建与完成')}</h3>
        <ColumnChart
          labels={data.trend.map((d) => d.date)}
          series={[
            { name: t('新建'), color: '#0a84ff', values: data.trend.map((d) => d.created) },
            { name: t('完成'), color: '#30d158', values: data.trend.map((d) => d.completed) },
            { name: t('驳回'), color: '#8e8e93', values: data.trend.map((d) => d.rejected) },
          ]}
        />
      </div>
      <div className="grid grid-3">
        <div className="card card-pad">
          <h3 style={{ marginBottom: 12 }}>{t('负责人工作量（未完成）')}</h3>
          <BarList items={data.by_assignee.map((a) => ({ label: a.name === '未分配' ? t('未分配') : a.name, value: a.count }))} />
        </div>
        <div className="card card-pad">
          <h3 style={{ marginBottom: 12 }}>{t('机台组')}</h3>
          {data.by_machine_group.map((g) => (
            <div key={g.name} className="bar-row">
              <span className="ellipsis">{g.name === '未指定机台' ? t('未指定机台') : g.name}</span>
              <div className="bar-track">
                <div style={{ width: `${(g.done / Math.max(1, ...data.by_machine_group.map((x) => x.open + x.done))) * 100}%`, background: 'var(--success)' }} />
                <div style={{ width: `${(g.open / Math.max(1, ...data.by_machine_group.map((x) => x.open + x.done))) * 100}%`, background: 'var(--accent)' }} />
              </div>
              <span className="small bold" style={{ textAlign: 'right' }}>
                {g.open + g.done}
              </span>
            </div>
          ))}
          <div className="legend">
            <span>
              <i style={{ background: 'var(--success)' }} />
              {t('已完成')}
            </span>
            <span>
              <i style={{ background: 'var(--accent)' }} />
              {t('进行中')}
            </span>
          </div>
        </div>
        <div className="card card-pad">
          <h3 style={{ marginBottom: 12 }}>{t('影响范围')}</h3>
          <BarList items={Object.entries(data.by_scope).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ label: t(k), value: v }))} color="#af52de" />
        </div>
      </div>
    </div>
  )
}

function Flow() {
  const project = useProjectCtx()
  const win = useReportWindow()
  const days = win.days
  const { data } = useQuery({
    queryKey: ['reports', 'cfd', project.project.key, days, win.withSubtasks],
    queryFn: () =>
      api.get<{ statuses: { id: number; name: string; color: string }[]; series: Record<string, number | string>[] }>(
        `/api/projects/${project.project.key}/reports/cfd?days=${days}&subtasks=${win.withSubtasks}`,
      ),
    placeholderData: keepPreviousData,
  })
  if (!data) return <Spinner center />
  return (
    <div className="card card-pad">
      <div className="row" style={{ marginBottom: 12 }}>
        <div>
          <h3>{t('累积流程图')}</h3>
          <div className="small muted">{t('每天各状态的问题数量；色带变宽说明该环节出现积压。')}</div>
        </div>
      </div>
      <WindowControls win={win} />
      <StackedArea
        labels={data.series.map((s) => String(s.date))}
        layers={[...data.statuses].reverse().map((s) => ({ name: t(s.name), color: s.color, values: data.series.map((p) => Number(p[s.name] ?? 0)) }))}
      />
    </div>
  )
}

function ReportPreview({
  report,
  projectKey,
  view,
  onView,
  mailing,
  onEmail,
  onIssueClick,
}: {
  report: AiReportItem
  projectKey: string
  view: 'html' | 'text'
  onView: (view: 'html' | 'text') => void
  mailing: boolean
  onEmail: () => void
  onIssueClick: (key: string) => void
}) {
  const url = `/api/projects/${projectKey}/ai-reports/${report.id}/html`
  return (
    <div className="report-preview">
      <div className="row wrap report-preview-bar">
        <div style={{ flex: 1, minWidth: 160 }}>
          <b className="ellipsis" style={{ display: 'block' }}>
            {report.title}
          </b>
          <div className="tiny faint">
            {fmtDateTime(report.created_at)}
            {report.scheduled && <> · {t('自动')}</>}
          </div>
        </div>
        <Segmented
          value={view}
          onChange={onView}
          items={[
            { value: 'html', label: t('HTML 预览') },
            { value: 'text', label: t('原文') },
          ]}
        />
        <a className="btn ghost sm" href={url} target="_blank" rel="noopener noreferrer">
          {t('新窗口打开')}
        </a>
        <a className="btn ghost sm" href={`${url}?download=true`} download>
          {t('下载 HTML')}
        </a>
        {report.can_email && (
          <button className="btn primary sm" disabled={mailing} onClick={onEmail} title={t('发送邮件给项目负责人和管理员')}>
            {mailing ? t('发送中…') : t('发送邮件')}
          </button>
        )}
      </div>
      {view === 'html' ? (
        <>
          <ReportFrame key={report.id} src={url} title={report.title} />
          <div className="tiny faint" style={{ marginTop: 6 }}>
            {report.has_charts ? t('邮件正文与此预览一致（HTML 格式，图表兼容主流邮件客户端）') : t('该报告生成于图表功能上线之前，只包含文字内容')}
          </div>
        </>
      ) : (
        <div className="ai-box">
          <Markdown text={report.content} onIssueClick={onIssueClick} />
        </div>
      )}
    </div>
  )
}

function ReportFrame({ src, title }: { src: string; title: string }) {
  const [height, setHeight] = useState<number | null>(null)
  const detach = useRef<(() => void) | null>(null)
  useEffect(() => () => detach.current?.(), [])
  const onLoad = (event: SyntheticEvent<HTMLIFrameElement>) => {
    const win = event.currentTarget.contentWindow
    const doc = event.currentTarget.contentDocument
    if (!win || !doc?.body) return
    const measure = () => setHeight(Math.ceil(doc.body.getBoundingClientRect().height))
    measure()
    detach.current?.()
    win.addEventListener('resize', measure)
    detach.current = () => win.removeEventListener('resize', measure)
  }
  return (
    <div className="report-frame">
      {height === null && (
        <div className="report-frame-loading">
          <Spinner />
        </div>
      )}
      <iframe
        src={src}
        title={title}
        sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
        style={{ height: height ?? 520 }}
        onLoad={onLoad}
      />
    </div>
  )
}

function AiReports() {
  const project = useProjectCtx()
  const key = project.project.key
  const { toast, openIssue } = useApp()
  const openLeft = (issueKey: string) => openIssue(issueKey, { side: 'left' })
  const client = useQueryClient()
  const [period, setPeriod] = useState<'weekly' | 'daily'>('weekly')
  const [unitId, setUnitId] = useState<number | ''>('')
  const [content, setContent] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [view, setView] = useState<'html' | 'text'>('html')
  const units = useQuery({
    queryKey: ['report-units', key],
    queryFn: () => api.get<ReportUnits>(`/api/projects/${key}/reports/units`),
    staleTime: 60_000,
  })
  const unitList = units.data?.units ?? []
  const selectedUnit = unitList.find((u) => u.id === unitId)
  const history = useQuery({
    queryKey: ['ai-reports', key],
    queryFn: () => api.get<{ reports: AiReportItem[] }>(`/api/projects/${key}/ai-reports`),
  })
  const [deleting, setDeleting] = useState<number | null>(null)
  const [mailing, setMailing] = useState<number | null>(null)
  const reports = history.data?.reports ?? []
  const selected = reports.find((r) => r.id === selectedId) ?? reports[0] ?? null
  const emailReport = async (r: AiReportItem) => {
    setMailing(r.id)
    try {
      const result = await api.post<{ recipients: number }>(`/api/projects/${key}/ai-reports/${r.id}/email`)
      toast(t('已排队发送给 {n} 位项目负责人/管理员', { n: result.recipients }), 'success')
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setMailing(null)
    }
  }
  const removeReport = async (r: AiReportItem) => {
    if (!window.confirm(t('删除报告「{title}」？此操作无法撤销。', { title: r.title }))) return
    setDeleting(r.id)
    try {
      await api.del(`/api/projects/${key}/ai-reports/${r.id}`)
      if (selectedId === r.id) setSelectedId(null)
      client.setQueryData<{ reports: AiReportItem[] }>(['ai-reports', key], (old) => (old ? { reports: old.reports.filter((x) => x.id !== r.id) } : old))
      toast(t('已删除'), 'success')
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setDeleting(null)
    }
  }
  const generate = async () => {
    setContent('')
    setBusy(true)
    let savedId: number | null = null
    try {
      await streamPost('/api/agent/report', { project: key, period, org_unit_id: unitId || null }, (ev) => {
        if (ev.type === 'delta') setContent((c) => (c ?? '') + (ev.text ?? ''))
        if (ev.type === 'report' && ev.id != null) savedId = ev.id
        if (ev.type === 'error') toast(ev.message != null ? tServer(ev.message) : t('AI 不可用'), 'error')
      })
      await client.invalidateQueries({ queryKey: ['ai-reports', key] })
      if (savedId != null) {
        setSelectedId(savedId)
        setView('html')
        setContent(null)
      }
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1fr) 320px', gap: 16, alignItems: 'start' }}>
      <div className="card card-pad">
        <div className="row wrap" style={{ marginBottom: 12 }}>
          <Segmented
            value={period}
            onChange={setPeriod}
            items={[
              { value: 'weekly', label: t('周报') },
              { value: 'daily', label: t('日报') },
            ]}
          />
          <Select
            className="select sm"
            style={{ width: 'auto', minWidth: 200, maxWidth: 420 }}
            value={unitId}
            title={selectedUnit?.path ?? t('全项目')}
            onChange={(e) => setUnitId(e.target.value ? Number(e.target.value) : '')}
          >
            <option value="">
              {t('全项目')}
              {units.data ? ` · ${units.data.total}` : ''}
            </option>
            {unitList.map((u) => (
              <option key={u.id} value={u.id} title={u.path}>
                {IDEOGRAPHIC_SPACE.repeat(u.depth)}
                {u.depth > 0 ? '└ ' : ''}
                {u.label} · {u.issues}
              </option>
            ))}
          </Select>
          <span className="tiny faint" title={t('只统计负责人、报告人或测试人属于该部门（含下级团队）的问题')}>
            {selectedUnit
              ? t('{count} 个相关问题，{open} 个未完成', { count: selectedUnit.issues, open: selectedUnit.open })
              : t('按部门筛选：负责人、报告人或测试人属于该部门（含下级）')}
          </span>
          <span className="spacer" />
          <button className="btn ai" disabled={busy} onClick={generate}>
            ✦ {busy ? t('生成中…') : t('生成报告')}
          </button>
        </div>
        {content !== null ? (
          <div className="ai-box">
            {busy && <div className="tiny faint" style={{ marginBottom: 6 }}>{t('正在撰写正文，完成后会生成带图表的 HTML 报告…')}</div>}
            {content ? <Markdown text={content} onIssueClick={openLeft} /> : <Spinner />}
          </div>
        ) : selected ? (
          <ReportPreview
            report={selected}
            projectKey={key}
            view={view}
            onView={setView}
            mailing={mailing === selected.id}
            onEmail={() => emailReport(selected)}
            onIssueClick={openLeft}
          />
        ) : (
          <div className="empty">
            <div className="big">✦</div>
            {period === 'weekly'
              ? t('AI 将汇总本周新建、完成、状态流转与测试情况，生成可直接发送的报告。')
              : t('AI 将汇总本日新建、完成、状态流转与测试情况，生成可直接发送的报告。')}
            <div className="tiny faint" style={{ marginTop: 6 }}>{t('每周五 17:30 会自动生成一份项目周报，并排队发送给项目负责人和管理员')}</div>
          </div>
        )}
      </div>
      <div className="card">
        <div className="card-header">
          <h3>{t('历史报告')}</h3>
        </div>
        {history.data?.reports.length === 0 && <div className="empty small">{t('暂无')}</div>}
        {history.data?.reports.map((r) => (
          <div
            key={r.id}
            className={`activity-item report-item${selected?.id === r.id && content === null ? ' active' : ''}`}
            style={{ flexDirection: 'column', cursor: 'pointer' }}
            onClick={() => {
              setSelectedId(r.id)
              setContent(null)
            }}
          >
            <div className="row">
              <b className="ellipsis" style={{ flex: 1 }}>
                {r.title}
              </b>
              {r.scheduled && <span className="tag">{t('自动')}</span>}
              {r.can_email && (
                <button
                  className="btn ghost sm icon"
                  title={t('发送邮件给项目负责人和管理员')}
                  aria-label={t('发送邮件给项目负责人和管理员')}
                  disabled={mailing === r.id}
                  onClick={(e) => {
                    e.stopPropagation()
                    emailReport(r)
                  }}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <rect x="3" y="5" width="18" height="14" rx="2" />
                    <path d="m3 7 9 6 9-6" />
                  </svg>
                </button>
              )}
              {r.can_delete && (
                <button
                  className="btn ghost sm icon danger report-del"
                  title={t('删除')}
                  aria-label={t('删除')}
                  disabled={deleting === r.id}
                  onClick={(e) => {
                    e.stopPropagation()
                    removeReport(r)
                  }}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6" />
                  </svg>
                </button>
              )}
            </div>
            <div className="tiny faint">
              {fmtDateTime(r.created_at)}
              {r.has_charts && <> · {t('含图表')}</>}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
