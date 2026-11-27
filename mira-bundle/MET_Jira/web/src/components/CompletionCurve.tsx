import { LineChart } from './Charts'
import { t } from '../i18n'

export interface CurvePoint {
  date: string
  actual_pct: number
  plan_pct: number
}

export interface CompletionCurveData {
  actual_pct: number
  plan_pct: number
  points: CurvePoint[]
  spark?: CurvePoint[]
}

/** Mini sparkline for project cover / portfolio cards (SVG, no ECharts). */
export function CompletionSpark({
  points,
  height = 36,
  className = '',
}: {
  points: CurvePoint[]
  height?: number
  className?: string
}) {
  if (points.length < 2) return null
  const w = 120
  const h = height
  const pad = 2
  const xs = points.map((_, i) => pad + (i / (points.length - 1)) * (w - pad * 2))
  const y = (pct: number) => h - pad - (pct / 100) * (h - pad * 2)
  const line = (key: 'actual_pct' | 'plan_pct') =>
    points.map((p, i) => `${i === 0 ? 'M' : 'L'}${xs[i].toFixed(1)},${y(p[key]).toFixed(1)}`).join(' ')
  return (
    <svg className={`completion-spark ${className}`} viewBox={`0 0 ${w} ${h}`} width={w} height={h} aria-hidden>
      <path d={line('plan_pct')} fill="none" stroke="rgba(255,255,255,0.45)" strokeWidth="1.5" strokeDasharray="3 2" />
      <path d={line('actual_pct')} fill="none" stroke="#fff" strokeWidth="2" />
    </svg>
  )
}

export function CompletionCurveChart({ data, height = 220 }: { data: CompletionCurveData; height?: number }) {
  const points = data.points?.length ? data.points : data.spark ?? []
  if (points.length < 2) {
    return <div className="faint small">{t('暂无完成度数据')}</div>
  }
  return (
    <div className="completion-curve" style={{ height }}>
      <LineChart
        labels={points.map((p) => p.date)}
        series={[
          { name: t('实际完成度'), color: '#0a84ff', values: points.map((p) => p.actual_pct), area: true },
          { name: t('计划完成度'), color: '#8e8e93', values: points.map((p) => p.plan_pct), dashed: true },
        ]}
      />
    </div>
  )
}
