import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { ColumnChart, LineChart } from '../components/Charts'
import { DateField } from '../components/DateField'
import { InfoTip, Segmented, Spinner, TypeIcon, Select } from '../components/ui'
import { useApp } from '../context'
import { t } from '../i18n'
import { fmtDate, fmtDateTime } from '../labels'
import type { IssueType } from '../types'
import { useProjectCtx } from './ProjectLayout'
import { StatTile, useReportWindow, WindowControls } from './reportKit'

type Amount = { count: number; points: number }
type Unit = 'count' | 'points'

interface SprintSummary {
  id: number
  name: string
  state: 'active' | 'closed'
  start: string
  end: string
  planned_end: string | null
  committed: Amount
  added: Amount
  removed: Amount
  scope: Amount
  completed: Amount
  committed_completed: number
  reestimated: number
}

interface Velocity {
  sprints: SprintSummary[]
  average: { sprints: number; count: number | null; points: number | null; commitment_rate: number | null; added: number | null }
}

type IssueRef = { key: string; title: string; type: IssueType }

interface SprintDetail {
  sprint: { id: number; name: string; state: string }
  summary: SprintSummary | null
  series: Record<string, number | string | undefined>[]
  changes: (IssueRef & { kind: 'added' | 'removed'; at: string; points: number | null; actor: string | null })[]
  reestimates: (IssueRef & { at: string; old: number | null; new: number | null; actor: string | null })[]
}

interface Forecast {
  days: number
  throughput: { total: number; per_week: number; daily: { date: string; count: number }[] }
  options: { sprint: { id: number; name: string } | null; epics: { id: number; key: string; title: string; open: number }[] }
  scope: { kind: 'sprint' | 'epic' | 'open'; name?: string; key?: string; end_date: string | null; total: number; remaining: number }
  runs: number
  finish: { p50: string | null; p70: string | null; p85: string | null; p95: string | null } | null
  curve: { date: string; probability: number }[]
  unreachable: number
  target: { date: string; probability: number | null; items: { p50: number; p85: number; p95: number } | null }
}

function amount(a: Amount, unit: Unit): number {
  return unit === 'count' ? a.count : a.points
}

function rate(done: number, of: number): string {
  return of ? `${Math.round((done * 100) / of)}%` : '–'
}

function shortName(name: string): string {
  return name.length > 16 ? `${name.slice(0, 15)}…` : name
}

function IssueRefLink({ issue }: { issue: IssueRef }) {
  const { openIssue } = useApp()
  return (
    <button type="button" className="ref-link" onClick={() => openIssue(issue.key)} title={issue.title}>
      <TypeIcon type={issue.type} size={14} />
      <span className="issue-key">{issue.key}</span>
      <span className="ellipsis">{issue.title}</span>
    </button>
  )
}

export function SprintView() {
  const project = useProjectCtx()
  const key = project.project.key
  const win = useReportWindow()
  const [unit, setUnit] = useState<Unit>('points')
  const [sprintId, setSprintId] = useState<number | null>(null)
  const [chart, setChart] = useState<'up' | 'down'>('up')
  const velocity = useQuery({
    queryKey: ['reports', 'velocity', key, win.withSubtasks],
    queryFn: () => api.get<Velocity>(`/api/projects/${key}/reports/velocity?subtasks=${win.withSubtasks}`),
    placeholderData: keepPreviousData,
  })
  const detail = useQuery({
    queryKey: ['reports', 'sprint', key, sprintId, win.withSubtasks],
    queryFn: () => api.get<SprintDetail>(`/api/projects/${key}/reports/sprint?subtasks=${win.withSubtasks}${sprintId ? `&sprint_id=${sprintId}` : ''}`),
    placeholderData: keepPreviousData,
    retry: false,
  })
  if (!velocity.data) return <Spinner center />
  const { sprints, average } = velocity.data
  const avg = unit === 'count' ? average.count : average.points
  const current = sprints.find((s) => s.state === 'active')
  const d = detail.data
  const selected = d?.summary?.id ?? sprintId
  const series = d?.series ?? []
  const val = (p: Record<string, number | string | undefined>, field: string) => (p[`${field}_${unit}`] === undefined ? null : Number(p[`${field}_${unit}`]))
  const committed = d?.summary ? amount(d.summary.committed, unit) : 0
  return (
    <div className="col" style={{ gap: 16 }}>
      <WindowControls win={win} days={false}>
        <span className="small muted">{t('承诺指冲刺开始时的范围；故事点按当时的估算统计，之后的修改计入「估算变更」。驳回不计入完成。')}</span>
      </WindowControls>
      <div className="grid grid-4">
        <StatTile
          label={t('平均速度')}
          value={avg === null ? '–' : unit === 'count' ? t('{n} 项', { n: avg }) : t('{n} 点', { n: avg })}
          sub={average.sprints ? t('最近 {n} 个已完成冲刺的平均完成量', { n: average.sprints }) : t('还没有已完成的冲刺')}
        />
        <StatTile label={t('承诺完成率')} value={average.commitment_rate === null ? '–' : `${average.commitment_rate}%`} sub={t('冲刺开始时承诺的工作中，在冲刺内完成的比例')} />
        <StatTile label={t('平均中途加入')} value={average.added === null ? '–' : t('{n} 项', { n: average.added })} sub={t('每个冲刺开始后新加入的工作')} />
        <StatTile
          label={t('当前冲刺')}
          value={current ? `${amount(current.completed, unit)} / ${amount(current.scope, unit)}` : '–'}
          sub={current ? t('{name} · 已完成 / 当前范围', { name: current.name }) : t('没有进行中的冲刺')}
        />
      </div>
      <div className="card card-pad">
        <div className="row" style={{ marginBottom: 6 }}>
          <h3>{t('速度与承诺')}</h3>
          <span className="spacer" />
          <Segmented
            value={unit}
            onChange={setUnit}
            items={[
              { value: 'points', label: t('故事点') },
              { value: 'count', label: t('问题数') },
            ]}
          />
        </div>
        <div className="small muted" style={{ marginBottom: 10 }}>
          {t('每个冲刺开始时承诺了多少、中途加入了多少、最终完成了多少；进行中的冲刺统计到今天。')}
        </div>
        {sprints.length === 0 ? (
          <div className="empty small">{t('还没有开始过冲刺')}</div>
        ) : (
          <>
            <ColumnChart
              labels={sprints.map((s) => s.name)}
              format={shortName}
              series={[
                { name: t('承诺'), color: '#8e8e93', values: sprints.map((s) => amount(s.committed, unit)) },
                { name: t('中途加入'), color: '#ff9f0a', values: sprints.map((s) => amount(s.added, unit)) },
                { name: t('完成'), color: '#30d158', values: sprints.map((s) => amount(s.completed, unit)) },
              ]}
            />
            <div className="sprint-table">
              <div className="sprint-row head tiny faint">
                <span>{t('冲刺')}</span>
                <span>{t('日期')}</span>
                <span className="num">{t('承诺')}</span>
                <span className="num">{t('中途加入')}</span>
                <span className="num">{t('移出')}</span>
                <span className="num">{t('完成')}</span>
                <span className="num">{t('承诺完成率')}</span>
              </div>
              {[...sprints].reverse().map((s) => (
                <button key={s.id} type="button" className={`sprint-row${selected === s.id ? ' active' : ''}`} onClick={() => setSprintId(s.id)}>
                  <span className="ellipsis">
                    {s.name}
                    {s.state === 'active' && <span className="tag" style={{ marginLeft: 6, height: 18, fontSize: 11 }}>{t('进行中')}</span>}
                  </span>
                  <span className="tiny faint">
                    {fmtDate(s.start)} – {fmtDate(s.state === 'active' ? s.planned_end ?? s.end : s.end)}
                  </span>
                  <span className="num small">{amount(s.committed, unit)}</span>
                  <span className="num small">{amount(s.added, unit) ? `+${amount(s.added, unit)}` : 0}</span>
                  <span className="num small">{amount(s.removed, unit) ? `−${amount(s.removed, unit)}` : 0}</span>
                  <span className="num small bold">{amount(s.completed, unit)}</span>
                  <span className="num small">{rate(s.committed_completed, s.committed.count)}</span>
                </button>
              ))}
            </div>
          </>
        )}
      </div>
      <div className="card card-pad">
        <div className="row wrap" style={{ marginBottom: 12 }}>
          <h3>
            {chart === 'up' ? t('燃起图') : t('燃尽图')}
            <InfoTip topic={chart === 'up' ? 'burnup' : 'burndown'} />
          </h3>
          <Select className="select sm" style={{ width: 'auto' }} value={selected ?? ''} onChange={(e) => setSprintId(e.target.value ? Number(e.target.value) : null)}>
            {selected === null && <option value="">{t('当前或最近的冲刺')}</option>}
            {[...sprints].reverse().map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} {s.state === 'active' ? t('（进行中）') : ''}
              </option>
            ))}
          </Select>
          <span className="spacer" />
          <Segmented
            value={chart}
            onChange={setChart}
            items={[
              { value: 'up', label: t('燃起') },
              { value: 'down', label: t('燃尽') },
            ]}
          />
        </div>
        {detail.isLoading && <Spinner center />}
        {detail.error && <div className="empty">{(detail.error as Error).message}</div>}
        {d && !d.summary && <div className="empty small">{t('该冲刺尚未开始')}</div>}
        {d?.summary && (
          <>
            <div className="small muted" style={{ marginBottom: 8 }}>
              {chart === 'up'
                ? t('范围线上升表示冲刺中途加入了工作；完成线与范围线重合即表示全部完成。')
                : t('剩余 = 当前范围 − 已完成；理想线按冲刺开始时的承诺匀速下降。')}
            </div>
            {chart === 'up' ? (
              <LineChart
                labels={series.map((p) => String(p.date))}
                series={[
                  { name: t('理想'), color: '#8e8e93', dashed: true, values: series.map((p) => Math.round((committed - Number(p[`ideal_${unit}`] ?? 0)) * 10) / 10) },
                  { name: t('范围'), color: '#af52de', values: series.map((p) => val(p, 'scope')) },
                  { name: t('完成'), color: '#30d158', area: true, values: series.map((p) => val(p, 'done')) },
                ]}
              />
            ) : (
              <LineChart
                labels={series.map((p) => String(p.date))}
                series={[
                  { name: t('理想'), color: '#8e8e93', dashed: true, values: series.map((p) => Number(p[`ideal_${unit}`] ?? 0)) },
                  { name: t('剩余'), color: '#ff375f', area: true, values: series.map((p) => val(p, 'remaining')) },
                ]}
              />
            )}
            <div className="grid grid-2" style={{ marginTop: 16 }}>
              <div>
                <h4 className="small bold" style={{ marginBottom: 6 }}>
                  {t('范围变更')} <span className="count">{d.changes.length}</span>
                </h4>
                {d.changes.length === 0 && <div className="faint small">{t('冲刺开始后没有加入或移出工作')}</div>}
                {d.changes.map((c, i) => (
                  <div key={i} className="change-row">
                    <span className={`change-kind ${c.kind}`}>{c.kind === 'added' ? t('加入') : t('移出')}</span>
                    <IssueRefLink issue={c} />
                    <span className="tiny faint num">{c.points === null ? '' : t('{n} 点', { n: c.points })}</span>
                    <span className="tiny faint num" title={c.actor ?? undefined}>{fmtDateTime(c.at)}</span>
                  </div>
                ))}
              </div>
              <div>
                <h4 className="small bold" style={{ marginBottom: 6 }}>
                  {t('估算变更')} <span className="count">{d.reestimates.length}</span>
                  {d.summary.reestimated !== 0 && (
                    <span className="tiny faint" style={{ marginLeft: 8, fontWeight: 400 }}>
                      {t('合计 {n} 点', { n: d.summary.reestimated > 0 ? `+${d.summary.reestimated}` : d.summary.reestimated })}
                    </span>
                  )}
                </h4>
                {d.reestimates.length === 0 && <div className="faint small">{t('期间没有修改故事点')}</div>}
                {d.reestimates.map((r, i) => (
                  <div key={i} className="change-row">
                    <span className="change-kind estimate">{`${r.old ?? '–'} → ${r.new ?? '–'}`}</span>
                    <IssueRefLink issue={r} />
                    <span className="tiny faint num">{r.actor ?? ''}</span>
                    <span className="tiny faint num">{fmtDateTime(r.at)}</span>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function percent(value: number | null): string {
  return value === null ? '–' : `${value}%`
}

export function ForecastView() {
  const project = useProjectCtx()
  const key = project.project.key
  const win = useReportWindow()
  const [scope, setScope] = useState('sprint')
  const [target, setTarget] = useState('')
  const today = new Date().toISOString().slice(0, 10)
  const { data } = useQuery({
    queryKey: ['reports', 'forecast', key, win.days, win.withSubtasks, scope, target],
    queryFn: () =>
      api.get<Forecast>(`/api/projects/${key}/reports/forecast?days=${win.days}&subtasks=${win.withSubtasks}&scope=${encodeURIComponent(scope)}${target ? `&target=${target}` : ''}`),
    placeholderData: keepPreviousData,
  })
  if (!data) return <Spinner center />
  const s = data.scope
  const scopeValue = s.kind === 'epic' ? scope : s.kind
  const deadline = s.end_date
  return (
    <div className="col" style={{ gap: 16 }}>
      <WindowControls win={win}>
        <Select className="select sm" style={{ width: 'auto', maxWidth: 320 }} value={scopeValue} onChange={(e) => setScope(e.target.value)}>
          {data.options.sprint && <option value="sprint">{t('当前冲刺 · {name}', { name: data.options.sprint.name })}</option>}
          <option value="open">{t('全部未完成的工作')}</option>
          {data.options.epics.map((e) => (
            <option key={e.id} value={`epic:${e.id}`}>
              {t('史诗 {key} · {title}（{n} 项未完成）', { key: e.key, title: e.title, n: e.open })}
            </option>
          ))}
        </Select>
        <span className="small muted">{t('基于近 {n} 天的每日完成量进行 {runs} 次蒙特卡洛模拟，估算剩余工作的完成日期。', { n: data.days, runs: data.runs })}</span>
      </WindowControls>
      <div className="grid grid-4">
        <StatTile label={t('剩余工作')} value={s.remaining} sub={t('范围内共 {n} 项', { n: s.total })} />
        <StatTile label={t('50% 概率完成于')} value={data.finish?.p50 ? fmtDate(data.finish.p50) : '–'} sub={t('一半的模拟在此之前完成')} />
        <StatTile label={t('85% 概率完成于')} value={data.finish?.p85 ? fmtDate(data.finish.p85) : '–'} color="var(--accent)" sub={data.finish?.p95 ? t('95% 概率完成于 {date}', { date: fmtDate(data.finish.p95) }) : undefined} />
        <StatTile
          label={deadline ? t('截止 {date} 前完成的概率', { date: fmtDate(deadline) }) : t('{date} 前完成的概率', { date: fmtDate(data.target.date) })}
          value={percent(data.target.probability)}
          color={data.target.probability === null ? undefined : data.target.probability >= 85 ? 'var(--success)' : data.target.probability >= 50 ? 'var(--warning)' : 'var(--danger)'}
          sub={t('近 {n} 天共完成 {total} 项，平均每周 {week} 项', { n: data.days, total: data.throughput.total, week: data.throughput.per_week })}
        />
      </div>
      {data.finish === null ? (
        <div className="card card-pad empty">
          {s.remaining === 0 ? t('范围内的工作已全部完成') : t('近 {n} 天没有完成记录，无法预测；可尝试延长统计周期。', { n: data.days })}
        </div>
      ) : (
        <div className="card card-pad">
          <h3>{t('完成概率')}</h3>
          <div className="small muted" style={{ marginBottom: 10 }}>
            {t('在某日期前完成全部剩余工作的模拟占比；曲线越陡，预测越确定。')}
            {data.unreachable > 0 && ` ${t('有 {n}% 的模拟在一年内无法完成。', { n: Math.round(data.unreachable * 100) })}`}
          </div>
          <LineChart labels={data.curve.map((p) => p.date)} series={[{ name: t('完成概率 %'), color: '#0a84ff', area: true, values: data.curve.map((p) => p.probability) }]} />
        </div>
      )}
      <div className="grid grid-2">
        <div className="card card-pad">
          <div className="row wrap" style={{ marginBottom: 10 }}>
            <h3>{t('到指定日期能完成多少')}</h3>
            <span className="spacer" />
            <DateField size="sm" className="forecast-date" min={today} value={target || data.target.date} onChange={(v) => setTarget(v >= today ? v : '')} />
          </div>
          {data.target.items === null ? (
            <div className="faint small">{t('吞吐量数据不足')}</div>
          ) : (
            <div className="forecast-items">
              {(
                [
                  ['p95', t('有 95% 把握')],
                  ['p85', t('有 85% 把握')],
                  ['p50', t('有 50% 把握')],
                ] as const
              ).map(([k, label]) => (
                <div key={k} className="card stat">
                  <div className="label">{label}</div>
                  <div className="value">{t('≥ {n} 项', { n: data.target.items![k] })}</div>
                </div>
              ))}
            </div>
          )}
          <div className="tiny faint" style={{ marginTop: 10 }}>
            {t('从今天到 {date} 预计能完成的问题数；把握越高，数字越保守。', { date: fmtDate(data.target.date) })}
          </div>
        </div>
        <div className="card card-pad">
          <h3 style={{ marginBottom: 10 }}>{t('每日吞吐量（模拟样本）')}</h3>
          <ColumnChart labels={data.throughput.daily.map((p) => p.date)} series={[{ name: t('完成'), color: '#30d158', values: data.throughput.daily.map((p) => p.count) }]} />
        </div>
      </div>
    </div>
  )
}
