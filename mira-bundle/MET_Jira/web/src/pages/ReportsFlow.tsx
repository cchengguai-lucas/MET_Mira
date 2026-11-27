import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { DotStrip, LineChart, ScatterChart, type ChartRef } from '../components/Charts'
import { Avatar, Segmented, Spinner, StatusPill, TypeIcon } from '../components/ui'
import { useApp } from '../context'
import { t } from '../i18n'
import { TYPE_META, typeName } from '../labels'
import type { Issue, IssueType, Status, UserBrief } from '../types'
import { useProjectCtx } from './ProjectLayout'
import { IssueListButton, StatTile, useReportWindow, WindowControls } from './reportKit'

type Spread = { count: number; median: number | null; p85: number | null; avg: number | null }
type AgingLevel = 'warn' | 'danger' | null
type Thresholds = { warn: number; danger: number }

interface FlowData {
  days: number
  completed: { key: string; title: string; type: IssueType; completed: string; lead: number; cycle: number | null }[]
  cycle: Spread
  lead: Spread
  statuses: (Spread & { id: number; name: string; color: string; category: Status['category']; current: number; current_avg: number | null })[]
  aging: (Issue & { days_in_status: number; days_in_progress: number | null; age: number; level: AgingLevel })[]
  thresholds: Thresholds
  coverage: { with_history: number; total: number }
}

type LoadCounts = { open: number; todo: number; doing: number; overdue: number; aging: number; points: number }
type LoadRow = LoadCounts & { issues: Issue[]; user?: UserBrief; unit?: string | null }

interface LoadData {
  people: (LoadCounts & { user: UserBrief; unit: string | null; issues: Issue[] })[]
  units: (LoadCounts & { id: number | null; label: string | null; path: string | null; people: number })[]
  unassigned: LoadCounts & { issues: Issue[] }
  thresholds: Thresholds
}

interface LoadHistory {
  dates: string[]
  series: { kind: 'user' | 'others' | 'unassigned'; user: UserBrief | null; values: number[] }[]
}

const SERIES_COLORS = ['#0a84ff', '#30d158', '#ff9f0a', '#af52de', '#ff375f', '#64d2ff']

function historyName(line: LoadHistory['series'][number]): string {
  switch (line.kind) {
    case 'user':
      return line.user?.display_name ?? ''
    case 'others':
      return t('其他')
    case 'unassigned':
      return t('未分配')
  }
}

const LEVEL_DOT: Record<'warn' | 'danger' | 'ok', string> = { danger: '#ff453a', warn: '#ff9f0a', ok: '#0a84ff' }
const AGING_SHOWN = 15

function days(value: number | null): string {
  return value === null ? '–' : t('{n} 天', { n: value })
}

function isoDay(offset: number): string {
  const d = new Date()
  d.setDate(d.getDate() - offset)
  return d.toISOString().slice(0, 10)
}

export function EfficiencyView() {
  const project = useProjectCtx()
  const { openIssue } = useApp()
  const win = useReportWindow()
  const [measure, setMeasure] = useState<'cycle' | 'lead'>('cycle')
  const [showAll, setShowAll] = useState(false)
  const { data } = useQuery({
    queryKey: ['reports', 'flow', project.project.key, win.days, win.withSubtasks],
    queryFn: () => api.get<FlowData>(`/api/projects/${project.project.key}/reports/flow?days=${win.days}&subtasks=${win.withSubtasks}`),
    placeholderData: keepPreviousData,
  })
  if (!data) return <Spinner center />
  const { warn, danger } = data.thresholds
  const spread = measure === 'cycle' ? data.cycle : data.lead
  const points = data.completed.filter((p) => (measure === 'cycle' ? p.cycle : p.lead) !== null)
  const refs: ChartRef[] = []
  if (spread.median !== null) refs.push({ value: spread.median, label: t('中位数 {n} 天', { n: spread.median }), color: '#30d158' })
  if (spread.p85 !== null) refs.push({ value: spread.p85, label: t('85 分位 {n} 天', { n: spread.p85 }), color: '#ff9f0a' })
  const types = [...new Set(points.map((p) => p.type))]
  const doing = data.statuses.filter((s) => s.category === 'doing')
  const bottleneck = doing.reduce<FlowData['statuses'][number] | null>((best, s) => {
    const wait = Math.max(s.median ?? 0, s.current_avg ?? 0)
    return wait > 0 && (!best || wait > Math.max(best.median ?? 0, best.current_avg ?? 0)) ? s : best
  }, null)
  const longest = Math.max(1, ...data.statuses.map((s) => Math.max(s.avg ?? 0, s.current_avg ?? 0)))
  const aged = data.aging.filter((a) => a.level !== null)
  const overAged = aged.filter((a) => a.level === 'danger').length
  const shown = showAll ? aged : aged.slice(0, AGING_SHOWN)
  const lanes = data.statuses.filter((s) => s.current > 0 || s.category === 'doing')
  return (
    <div className="col" style={{ gap: 16 }}>
      <WindowControls win={win}>
        <span className="small muted">{t('周期时间：首次进入进行中状态到完成；前置时间：创建到完成。只统计期间内完成的工作，驳回不计入。')}</span>
      </WindowControls>
      <div className="grid grid-4">
        <StatTile label={t('周期时间（中位数）')} value={days(data.cycle.median)} sub={t('85 分位 {p85} · 共 {n} 项', { p85: days(data.cycle.p85), n: data.cycle.count })} />
        <StatTile label={t('前置时间（中位数）')} value={days(data.lead.median)} sub={t('85 分位 {p85} · 共 {n} 项', { p85: days(data.lead.p85), n: data.lead.count })} />
        <StatTile
          label={t('滞留工作')}
          value={overAged}
          color={overAged ? 'var(--danger)' : undefined}
          sub={t('在当前状态停留 ≥{danger} 天；≥{warn} 天的共 {n} 项', { danger, warn, n: aged.length })}
        />
        <StatTile
          label={t('瓶颈状态')}
          value={bottleneck ? t(bottleneck.name) : '–'}
          color={bottleneck?.color}
          sub={bottleneck ? t('停留中位数 {stay} · 当前 {n} 项', { stay: days(bottleneck.median), n: bottleneck.current }) : t('暂无进行中状态的流转记录')}
        />
      </div>
      <div className="card card-pad">
        <div className="row" style={{ marginBottom: 6 }}>
          <h3>{measure === 'cycle' ? t('周期时间分布') : t('前置时间分布')}</h3>
          <span className="spacer" />
          <Segmented
            value={measure}
            onChange={setMeasure}
            items={[
              { value: 'cycle', label: t('周期时间') },
              { value: 'lead', label: t('前置时间') },
            ]}
          />
        </div>
        <div className="small muted" style={{ marginBottom: 10 }}>
          {t('每个点代表一项已完成的工作：横轴为完成日期，纵轴为天数。85% 的工作在 85 分位线以内完成；点击圆点可打开对应问题。')}
        </div>
        {points.length === 0 ? (
          <div className="empty small">{t('期间内没有完成的工作')}</div>
        ) : (
          <ScatterChart
            start={isoDay(data.days - 1)}
            end={isoDay(0)}
            refs={refs}
            legend={types.map((type) => ({ name: typeName(type), color: TYPE_META[type]?.color ?? '#999' }))}
            points={points.map((p) => {
              const value = (measure === 'cycle' ? p.cycle : p.lead) ?? 0
              return {
                id: p.key,
                date: p.completed,
                y: value,
                color: TYPE_META[p.type]?.color ?? '#999',
                title: `${p.key} ${p.title}\n${t('完成于 {date} · {n} 天', { date: p.completed, n: value })}`,
                onClick: () => openIssue(p.key),
              }
            })}
          />
        )}
      </div>
      <div className="grid grid-2">
        <div className="card card-pad">
          <h3>{t('各状态停留时间')}</h3>
          <div className="small muted" style={{ marginBottom: 12 }}>
            {t('深色条为期间内离开该状态的工作平均停留天数，浅色条为仍处于该状态的工作平均已停留天数。')}
          </div>
          {data.statuses.map((s) => (
            <div key={s.id} className="stay-row">
              <span className="row" style={{ gap: 6, minWidth: 0 }}>
                <StatusPill status={{ name: t(s.name), color: s.color }} />
                {bottleneck?.id === s.id && <span className="tag danger-tag">{t('瓶颈')}</span>}
              </span>
              <div className="stay-bars">
                <div className="bar-track" title={t('平均 {avg} · 85 分位 {p85} · {n} 次', { avg: days(s.avg), p85: days(s.p85), n: s.count })}>
                  <div style={{ width: `${((s.avg ?? 0) / longest) * 100}%`, background: s.color }} />
                </div>
                <div className="bar-track thin" title={t('当前 {n} 项，平均已停留 {stay}', { n: s.current, stay: days(s.current_avg) })}>
                  <div style={{ width: `${((s.current_avg ?? 0) / longest) * 100}%`, background: s.color, opacity: 0.4 }} />
                </div>
              </div>
              <span className="small num">{days(s.avg)}</span>
              <span className="tiny faint num">{t('当前 {n}', { n: s.current })}</span>
            </div>
          ))}
        </div>
        <div className="card card-pad">
          <h3>{t('在制品停留时长')}</h3>
          <div className="small muted" style={{ marginBottom: 10 }}>
            {t('每个点代表一项未完成的工作，纵轴为在当前状态停留的天数；黄色表示 ≥{warn} 天，红色表示 ≥{danger} 天。', { warn, danger })}
          </div>
          {data.aging.length === 0 ? (
            <div className="empty small">{t('没有未完成的工作')}</div>
          ) : (
            <DotStrip
              categories={lanes.map((s) => ({ key: String(s.id), label: t(s.name), color: s.color }))}
              bands={[
                { from: warn, color: LEVEL_DOT.warn },
                { from: danger, color: LEVEL_DOT.danger },
              ]}
              refs={data.cycle.p85 !== null ? [{ value: data.cycle.p85, label: t('周期时间 85 分位 {n} 天', { n: data.cycle.p85 }), color: '#8e8e93' }] : []}
              points={data.aging.map((a) => ({
                id: a.key,
                category: String(a.status.id),
                y: a.days_in_status,
                color: LEVEL_DOT[a.level ?? 'ok'],
                title: `${a.key} ${a.title}\n${t('在「{status}」已停留 {n} 天', { status: t(a.status.name), n: a.days_in_status })}`,
                onClick: () => openIssue(a.key),
              }))}
            />
          )}
        </div>
      </div>
      <div className="card">
        <div className="card-header">
          <h3>{t('滞留工作项')}</h3>
          <span className="count">{aged.length}</span>
          <span className="spacer" />
          <span className="tiny faint">{t('按在当前状态停留的天数排序')}</span>
        </div>
        {aged.length === 0 ? (
          <div className="empty small">{t('没有停留超过 {n} 天的工作', { n: warn })}</div>
        ) : (
          <>
            {shown.map((a) => (
              <button key={a.key} type="button" className="aging-row" onClick={() => openIssue(a.key)}>
                <TypeIcon type={a.type} size={16} />
                <span className="issue-key">{a.key}</span>
                <span className="ellipsis">{a.title}</span>
                <StatusPill status={{ name: t(a.status.name), color: a.status.color }} />
                <Avatar user={a.assignee} size={22} />
                <span className="tiny faint num" title={t('进入进行中状态后的天数')}>
                  {a.days_in_progress === null ? '' : t('已进行 {n} 天', { n: a.days_in_progress })}
                </span>
                <span className={`aging-badge ${a.level}`}>{t('停留 {n} 天', { n: a.days_in_status })}</span>
              </button>
            ))}
            {aged.length > AGING_SHOWN && (
              <button type="button" className="btn ghost sm" style={{ margin: '8px 16px' }} onClick={() => setShowAll(!showAll)}>
                {showAll ? t('收起') : t('显示全部 {n} 项', { n: aged.length })}
              </button>
            )}
          </>
        )}
        {data.coverage.with_history < data.coverage.total && (
          <div className="tiny faint" style={{ padding: '8px 16px', borderTop: '1px solid var(--border)' }}>
            {t('{n} / {total} 个问题有状态流转记录；其余按创建和完成时间估算。', { n: data.coverage.with_history, total: data.coverage.total })}
          </div>
        )}
      </div>
    </div>
  )
}

function LoadBar({ row, max }: { row: LoadCounts; max: number }) {
  return (
    <div className="bar-track" title={t('待处理 {todo} · 进行中 {doing}', { todo: row.todo, doing: row.doing })}>
      <div style={{ width: `${(row.doing / max) * 100}%`, background: 'var(--accent)' }} />
      <div style={{ width: `${(row.todo / max) * 100}%`, background: 'color-mix(in srgb, var(--accent) 35%, transparent)' }} />
    </div>
  )
}

function Num({ value, color }: { value: number; color?: string }) {
  return (
    <span className="num small" style={{ color: value ? color : 'var(--text-3)' }}>
      {value}
    </span>
  )
}

export function LoadView() {
  const project = useProjectCtx()
  const win = useReportWindow()
  const { data } = useQuery({
    queryKey: ['reports', 'load', project.project.key, win.withSubtasks],
    queryFn: () => api.get<LoadData>(`/api/projects/${project.project.key}/reports/load?subtasks=${win.withSubtasks}`),
    placeholderData: keepPreviousData,
  })
  const history = useQuery({
    queryKey: ['reports', 'load-history', project.project.key, win.days, win.withSubtasks],
    queryFn: () => api.get<LoadHistory>(`/api/projects/${project.project.key}/reports/load/history?days=${win.days}&subtasks=${win.withSubtasks}`),
    placeholderData: keepPreviousData,
  })
  if (!data) return <Spinner center />
  const rows: LoadRow[] = [...data.people, ...(data.unassigned.open ? [data.unassigned] : [])]
  const total = (key: keyof LoadCounts) => rows.reduce((n, r) => n + r[key], 0)
  const maxOpen = Math.max(1, ...rows.map((r) => r.open))
  const maxUnit = Math.max(1, ...data.units.map((u) => u.open))
  const { danger } = data.thresholds
  return (
    <div className="col" style={{ gap: 16 }}>
      <WindowControls win={win}>
        <span className="small muted">{t('只统计当前未完成的工作，用于发现过载、需要支援或长期卡住的环节；不统计个人完成量，也不做绩效排名。')}</span>
      </WindowControls>
      <div className="grid grid-4">
        <StatTile label={t('未完成')} value={total('open')} sub={t('待处理 {n}', { n: total('todo') })} />
        <StatTile label={t('进行中')} value={total('doing')} sub={t('{n} 人有进行中的工作', { n: data.people.filter((p) => p.doing > 0).length })} />
        <StatTile label={t('已逾期')} value={total('overdue')} color={total('overdue') ? 'var(--danger)' : undefined} sub={t('未分配 {n}', { n: data.unassigned.open })} />
        <StatTile label={t('滞留')} value={total('aging')} color={total('aging') ? 'var(--warning)' : undefined} sub={t('当前状态停留 ≥{n} 天', { n: danger })} />
      </div>
      <div className="grid load-grid">
        <div className="card">
          <div className="card-header">
            <h3>{t('负责人')}</h3>
            <span className="count">{data.people.length}</span>
            <span className="spacer" />
            <span className="tiny faint">{t('点击某行可查看其未完成的问题')}</span>
          </div>
          <div className="load-row load-head tiny faint">
            <span>{t('负责人')}</span>
            <span>{t('进行中 / 待处理')}</span>
            <span className="num">{t('未完成')}</span>
            <span className="num">{t('进行中')}</span>
            <span className="num">{t('逾期')}</span>
            <span className="num">{t('滞留')}</span>
          </div>
          {rows.length === 0 && <div className="empty small">{t('没有未完成的工作')}</div>}
          {rows.map((row) => {
            const name = row.user ? row.user.display_name : t('未分配')
            return (
              <IssueListButton
                key={row.user?.id ?? 'unassigned'}
                className="load-row"
                title={t('{name} 的未完成工作', { name })}
                issues={row.issues}
                empty={t('没有未完成的工作')}
              >
                <span className="row" style={{ gap: 8, minWidth: 0 }}>
                  <Avatar user={row.user} size={24} />
                  <span style={{ minWidth: 0 }}>
                    <span className="ellipsis" style={{ display: 'block', fontWeight: 500 }}>
                      {name}
                    </span>
                    {row.unit && <span className="ellipsis tiny faint" style={{ display: 'block' }}>{row.unit}</span>}
                  </span>
                </span>
                <LoadBar row={row} max={maxOpen} />
                <Num value={row.open} />
                <Num value={row.doing} />
                <Num value={row.overdue} color="var(--danger)" />
                <Num value={row.aging} color="var(--warning)" />
              </IssueListButton>
            )
          })}
        </div>
        <div className="card card-pad">
          <h3>{t('部门')}</h3>
          <div className="small muted" style={{ marginBottom: 12 }}>
            {t('按负责人所在部门汇总未完成的工作。')}
          </div>
          {data.units.length === 0 && <div className="faint small">{t('暂无数据')}</div>}
          {data.units.map((u) => (
            <div key={u.id ?? 'none'} className="unit-row" title={u.path ?? undefined}>
              <div className="row" style={{ gap: 8 }}>
                <span className="ellipsis small" style={{ flex: 1, fontWeight: 500 }}>
                  {u.label ?? t('未设置部门')}
                </span>
                <span className="small bold">{u.open}</span>
              </div>
              <LoadBar row={u} max={maxUnit} />
              <div className="tiny faint">
                {t('{people} 人 · 进行中 {doing} · 逾期 {overdue} · 滞留 {aging}', { people: u.people, doing: u.doing, overdue: u.overdue, aging: u.aging })}
              </div>
            </div>
          ))}
          <div className="legend" style={{ marginTop: 8 }}>
            <span>
              <i style={{ background: 'var(--accent)' }} />
              {t('进行中')}
            </span>
            <span>
              <i style={{ background: 'color-mix(in srgb, var(--accent) 35%, transparent)' }} />
              {t('待处理')}
            </span>
          </div>
        </div>
      </div>
      <div className="card card-pad">
        <h3>{t('历史负载')}</h3>
        <div className="small muted" style={{ marginBottom: 10 }}>
          {t('近 {n} 天每天结束时各负责人手上未完成的问题数，按当时的负责人和状态还原。', { n: win.days })}
        </div>
        {!history.data ? (
          <Spinner center />
        ) : history.data.series.length === 0 ? (
          <div className="faint small">{t('暂无数据')}</div>
        ) : (
          <LineChart
            labels={history.data.dates}
            series={history.data.series.map((line, i) => ({
              name: historyName(line),
              color: line.kind === 'unassigned' ? '#8e8e93' : line.kind === 'others' ? '#636366' : SERIES_COLORS[i % SERIES_COLORS.length],
              dashed: line.kind !== 'user',
              values: line.values,
            }))}
          />
        )}
      </div>
    </div>
  )
}
