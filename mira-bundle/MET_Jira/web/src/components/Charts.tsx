import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import { init, use, type ComposeOption, type ECharts } from 'echarts/core'
import { BarChart, LineChart as ELineChart, ScatterChart as EScatterChart, type BarSeriesOption, type LineSeriesOption, type ScatterSeriesOption } from 'echarts/charts'
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
  type DataZoomComponentOption,
  type GridComponentOption,
  type LegendComponentOption,
  type MarkAreaComponentOption,
  type MarkLineComponentOption,
  type TooltipComponentOption,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { t } from '../i18n'

use([BarChart, ELineChart, EScatterChart, DataZoomComponent, GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent, CanvasRenderer])

type Option = ComposeOption<
  BarSeriesOption | LineSeriesOption | ScatterSeriesOption | DataZoomComponentOption | GridComponentOption | LegendComponentOption | MarkAreaComponentOption | MarkLineComponentOption | TooltipComponentOption
>

export function BarList({ items, color = 'var(--accent)', max }: { items: { label: ReactNode; value: number; color?: string; key?: string }[]; color?: string; max?: number }) {
  const top = max ?? Math.max(1, ...items.map((i) => i.value))
  if (items.length === 0) return <div className="faint small">{t('暂无数据')}</div>
  return (
    <div>
      {items.map((item, idx) => (
        <div key={item.key ?? idx} className="bar-row">
          <span className="ellipsis">{item.label}</span>
          <div className="bar-track">
            <div className="bar-fill" style={{ width: `${(item.value / top) * 100}%`, background: item.color ?? color }} />
          </div>
          <span className="small bold" style={{ textAlign: 'right' }}>
            {item.value}
          </span>
        </div>
      ))}
    </div>
  )
}

export function StackedBar({ segments }: { segments: { label: string; value: number; color: string }[] }) {
  const total = segments.reduce((n, s) => n + s.value, 0) || 1
  return (
    <div>
      <div className="bar-track" style={{ height: 14 }}>
        {segments.map((s) => (
          <div key={s.label} title={`${s.label} ${s.value}`} style={{ width: `${(s.value / total) * 100}%`, background: s.color, height: '100%' }} />
        ))}
      </div>
      <div className="legend" style={{ marginTop: 8 }}>
        {segments.map((s) => (
          <span key={s.label}>
            <i style={{ background: s.color }} />
            {s.label} {s.value}
          </span>
        ))}
      </div>
    </div>
  )
}

type Palette = {
  dark: boolean
  font: string
  text: string
  text2: string
  text3: string
  border: string
  borderStrong: string
  surface: string
  accent: string
  /** Turns ``var(--x)`` into a concrete colour; canvas cannot resolve CSS variables. */
  color: (c: string) => string
}

function readPalette(el: HTMLElement): Palette {
  const style = getComputedStyle(el)
  const v = (name: string) => style.getPropertyValue(name).trim()
  return {
    dark: document.documentElement.dataset.theme === 'dark',
    font: v('--font'),
    text: v('--text'),
    text2: v('--text-2'),
    text3: v('--text-3'),
    border: v('--border'),
    borderStrong: v('--border-strong'),
    surface: v('--surface'),
    accent: v('--accent'),
    color: (c) => {
      const m = /^var\((--[\w-]+)\)$/.exec(c.trim())
      return m ? v(m[1]) || c : c
    },
  }
}

function alpha(color: string, a: number): string {
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color)?.[1]
  if (!hex) return color
  const full = hex.length === 3 ? hex.replace(/./g, (c) => c + c) : hex
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16))
  return `rgba(${r}, ${g}, ${b}, ${a})`
}

const fmt = (v: number) => (Number.isInteger(v) ? String(v) : String(Math.round(v * 10) / 10))

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)

function subscribeTheme(cb: () => void) {
  const mo = new MutationObserver(cb)
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
  return () => mo.disconnect()
}

/**
 * Canvas host shared by every chart. ``sig`` is a serialisation of the data: the option is
 * only rebuilt when it changes, so parent re-renders neither replay animations nor reset zoom.
 */
function EChart({ build, sig, height = 280, onPick }: { build: (p: Palette) => Option; sig: string; height?: number; onPick?: (id: string) => void }) {
  const box = useRef<HTMLDivElement>(null)
  const chart = useRef<ECharts | null>(null)
  const pick = useRef(onPick)
  const shownTheme = useRef('')
  const theme = useSyncExternalStore(subscribeTheme, () => document.documentElement.dataset.theme ?? '')
  const [zoomed, setZoomed] = useState(false)

  useLayoutEffect(() => {
    pick.current = onPick
  })

  useEffect(() => {
    const el = box.current!
    const c = init(el, undefined, { renderer: 'canvas' })
    chart.current = c
    const ro = new ResizeObserver(() => c.resize({ animation: { duration: 250, easing: 'cubicOut' } }))
    ro.observe(el)
    c.on('datazoom', () => {
      const zooms = (c.getOption().dataZoom ?? []) as { start?: number; end?: number }[]
      setZoomed(zooms.some((z) => (z.start ?? 0) > 0.01 || (z.end ?? 100) < 99.99))
    })
    c.on('click', (e) => {
      const id = (e.data as { id?: string } | undefined)?.id
      if (id) pick.current?.(id)
    })
    c.getZr().on('dblclick', () => c.dispatchAction({ type: 'dataZoom', start: 0, end: 100 }))
    return () => {
      ro.disconnect()
      c.dispose()
      chart.current = null
    }
  }, [])

  useEffect(() => {
    const c = chart.current
    if (!c || !box.current) return
    const rethemed = shownTheme.current !== theme
    shownTheme.current = theme
    c.setOption(build(readPalette(box.current)), rethemed ? { notMerge: true } : { replaceMerge: ['series', 'dataZoom', 'xAxis', 'yAxis'] })
    setZoomed(false)
  }, [sig, theme])

  return (
    <div className="echart" style={{ height }}>
      <div ref={box} className="echart-canvas" />
      <div className={`echart-tools${zoomed ? ' zoomed' : ''}`}>
        <span>{t('Ctrl + 滚轮缩放 · 拖动平移 · 双击还原')}</span>
        {zoomed && (
          <button type="button" onClick={() => chart.current?.dispatchAction({ type: 'dataZoom', start: 0, end: 100 })}>
            {t('重置缩放')}
          </button>
        )}
      </div>
    </div>
  )
}

function frame(p: Palette, { legend, slider, round }: { legend?: string[]; slider?: boolean; round?: boolean }): Option {
  return {
    animationDuration: 700,
    animationEasing: 'cubicOut',
    animationDurationUpdate: 450,
    animationEasingUpdate: 'cubicInOut',
    textStyle: { fontFamily: p.font, color: p.text2 },
    grid: { left: 4, right: 14, top: 26, bottom: (legend?.length ? 30 : 4) + (slider ? 30 : 0), containLabel: true },
    legend: legend?.length
      ? {
          data: legend,
          type: 'scroll',
          bottom: 0,
          left: 0,
          icon: round ? 'circle' : 'roundRect',
          itemWidth: 10,
          itemHeight: 10,
          itemGap: 16,
          textStyle: { color: p.text2, fontSize: 12 },
          inactiveColor: p.text3,
          pageIconColor: p.text2,
          pageIconInactiveColor: p.text3,
          pageTextStyle: { color: p.text2 },
        }
      : { show: false },
  }
}

function tooltip(p: Palette): TooltipComponentOption {
  return {
    backgroundColor: p.dark ? 'rgba(44, 44, 46, 0.92)' : 'rgba(255, 255, 255, 0.95)',
    borderColor: p.borderStrong,
    borderWidth: 1,
    padding: [8, 12],
    textStyle: { color: p.text, fontSize: 12, fontFamily: p.font },
    extraCssText: 'border-radius: 10px; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.18); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);',
    transitionDuration: 0.2,
    confine: true,
  }
}

function pointerLabel(p: Palette) {
  return { backgroundColor: p.dark ? '#48484a' : '#1d1d1f', color: '#fff', borderRadius: 4, padding: [3, 6], fontSize: 11, fontFamily: p.font }
}

type TipRow = { color: string; name: string; value: string }

function tipHtml(p: Palette, header: string, rows: TipRow[], footer?: TipRow) {
  const row = (r: TipRow, bold = false) =>
    `<div style="display:flex;align-items:center;gap:8px;line-height:20px">` +
    `<span style="width:8px;height:8px;border-radius:50%;flex:none;background:${r.color}"></span>` +
    `<span style="flex:1;color:${bold ? p.text : p.text2}">${esc(r.name)}</span>` +
    `<b style="margin-left:18px;font-variant-numeric:tabular-nums">${r.value}</b></div>`
  return (
    `<div style="font-weight:600;margin-bottom:4px">${esc(header)}</div>` +
    rows.map((r) => row(r)).join('') +
    (footer ? `<div style="border-top:1px solid ${p.border};margin-top:4px;padding-top:2px">${row(footer, true)}</div>` : '')
  )
}

type AxisParam = { seriesName?: string; seriesId?: string; value?: unknown; color?: unknown; axisValue?: unknown }

function axisRows(params: unknown): { header: string; rows: (TipRow & { raw: number })[] } {
  const list = (Array.isArray(params) ? params : [params]) as AxisParam[]
  const rows = list
    .filter((x) => typeof x.value === 'number')
    .map((x) => ({ color: String(x.color), name: x.seriesName ?? '', value: fmt(x.value as number), raw: x.value as number }))
  return { header: String(list[0]?.axisValue ?? ''), rows }
}

function itemTip(p: Palette) {
  return (e: unknown) => {
    const data = (e as { data?: { title?: string } }).data
    const [head, ...rest] = (data?.title ?? '').split('\n')
    return `<div style="font-weight:600;max-width:320px;white-space:normal">${esc(head)}</div>` + rest.map((l) => `<div style="color:${p.text2};margin-top:2px">${esc(l)}</div>`).join('')
  }
}

function categoryAxis(p: Palette, labels: string[], format: (label: string) => string, boundaryGap: boolean) {
  return {
    type: 'category' as const,
    data: labels,
    boundaryGap,
    axisLine: { lineStyle: { color: p.borderStrong } },
    axisTick: { show: false },
    axisLabel: { color: p.text2, fontSize: 11, margin: 10, formatter: (v: string) => format(v) },
    axisPointer: { label: { ...pointerLabel(p), formatter: (e: { value: unknown }) => String(e.value) } },
  }
}

function valueAxis(p: Palette, extra: Record<string, unknown> = {}) {
  return {
    type: 'value' as const,
    min: 0,
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: { lineStyle: { color: p.border } },
    axisLabel: { color: p.text2, fontSize: 11, formatter: (v: number) => fmt(v) },
    axisPointer: { label: { ...pointerLabel(p), formatter: (e: { value: unknown }) => fmt(Number(e.value)) } },
    ...extra,
  }
}

function insideZoom(axis: { xAxisIndex?: number; yAxisIndex?: number }, minValueSpan?: number): DataZoomComponentOption {
  return { type: 'inside', ...axis, filterMode: 'none', zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false, moveOnMouseMove: true, throttle: 16, minValueSpan }
}

function sliderZoom(p: Palette, legend: boolean, labelFormatter?: (value: number, text: string) => string): DataZoomComponentOption {
  return {
    type: 'slider',
    xAxisIndex: 0,
    filterMode: 'none',
    height: 18,
    bottom: legend ? 32 : 6,
    left: 44,
    right: 18,
    borderColor: 'transparent',
    borderRadius: 6,
    backgroundColor: alpha(p.dark ? '#ffffff' : '#000000', 0.04),
    fillerColor: alpha(p.accent, 0.14),
    dataBackground: { lineStyle: { color: p.text3, width: 1, opacity: 0.6 }, areaStyle: { color: p.text3, opacity: 0.12 } },
    selectedDataBackground: { lineStyle: { color: p.accent, width: 1, opacity: 0.8 }, areaStyle: { color: p.accent, opacity: 0.16 } },
    handleIcon: 'path://M4,0 h2 a4,4 0 0 1 4,4 v16 a4,4 0 0 1 -4,4 h-2 a4,4 0 0 1 -4,-4 v-16 a4,4 0 0 1 4,-4 z',
    handleSize: '120%',
    handleStyle: { color: p.surface, borderColor: p.borderStrong, borderWidth: 1, shadowBlur: 4, shadowColor: 'rgba(0, 0, 0, 0.18)' },
    moveHandleSize: 0,
    brushSelect: false,
    textStyle: { color: p.text2, fontSize: 11 },
    labelFormatter,
    emphasis: { handleLabel: { show: true }, handleStyle: { borderColor: p.accent } },
    throttle: 16,
  }
}

const SLIDER_AT = 14
const dayLabel = (label: string) => label.slice(5)

export function LineChart({ labels, series }: { labels: string[]; series: { name: string; color: string; values: (number | null)[]; dashed?: boolean; area?: boolean }[] }) {
  const slider = labels.length > SLIDER_AT
  return (
    <EChart
      sig={JSON.stringify([labels, series])}
      build={(p) => ({
        ...frame(p, { legend: series.map((s) => s.name), slider }),
        tooltip: {
          ...tooltip(p),
          trigger: 'axis',
          axisPointer: { type: 'cross', lineStyle: { color: p.text3, type: 'dashed' }, crossStyle: { color: p.text3, type: 'dashed' } },
          formatter: (params: unknown) => {
            const { header, rows } = axisRows(params)
            return tipHtml(p, header, rows.length > 4 ? [...rows].sort((a, b) => b.raw - a.raw) : rows)
          },
        },
        xAxis: categoryAxis(p, labels, dayLabel, false),
        yAxis: valueAxis(p),
        dataZoom: [insideZoom({ xAxisIndex: 0 }, 2), ...(slider ? [sliderZoom(p, series.length > 0, (i) => labels[i] ?? '')] : [])],
        series: series.map((s, i) => {
          const color = p.color(s.color)
          return {
            id: `${i}:${s.name}`,
            name: s.name,
            type: 'line' as const,
            data: s.values.map((v) => v ?? '-'),
            color,
            z: s.dashed ? 2 : 3,
            smooth: 0.2,
            smoothMonotone: 'x' as const,
            connectNulls: false,
            symbol: 'circle',
            symbolSize: 7,
            showSymbol: !s.dashed && labels.length <= 16,
            lineStyle: { width: s.dashed ? 1.5 : 2.2, type: s.dashed ? [5, 4] : 'solid' },
            itemStyle: { borderColor: p.surface, borderWidth: 1.5 },
            emphasis: { focus: 'none' as const, scale: 1.5, lineStyle: { width: s.dashed ? 1.5 : 2.6 } },
            areaStyle: s.area
              ? { color: { type: 'linear' as const, x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: alpha(color, 0.28) }, { offset: 1, color: alpha(color, 0.02) }] } }
              : undefined,
          }
        }),
      })}
    />
  )
}

export function StackedArea({ labels, layers }: { labels: string[]; layers: { name: string; color: string; values: number[] }[] }) {
  const slider = labels.length > SLIDER_AT
  return (
    <EChart
      sig={JSON.stringify([labels, layers])}
      build={(p) => ({
        ...frame(p, { legend: layers.map((l) => l.name), slider }),
        tooltip: {
          ...tooltip(p),
          trigger: 'axis',
          axisPointer: { type: 'line', lineStyle: { color: p.text3, type: 'dashed' } },
          formatter: (params: unknown) => {
            const { header, rows } = axisRows(params)
            const total = rows.reduce((n, r) => n + r.raw, 0)
            return tipHtml(p, header, [...rows].reverse(), { color: 'transparent', name: t('合计'), value: fmt(total) })
          },
        },
        xAxis: categoryAxis(p, labels, dayLabel, false),
        yAxis: valueAxis(p),
        dataZoom: [insideZoom({ xAxisIndex: 0 }, 2), ...(slider ? [sliderZoom(p, layers.length > 0, (i) => labels[i] ?? '')] : [])],
        series: layers.map((l, i) => {
          const color = p.color(l.color)
          return {
            id: `${i}:${l.name}`,
            name: l.name,
            type: 'line' as const,
            stack: 'total',
            data: labels.map((_, k) => l.values[k] ?? 0),
            color,
            showSymbol: false,
            symbol: 'circle',
            symbolSize: 6,
            lineStyle: { width: 1, color },
            areaStyle: { color, opacity: 0.78 },
            emphasis: { focus: 'series' as const, areaStyle: { opacity: 0.95 } },
            blur: { areaStyle: { opacity: 0.25 }, lineStyle: { opacity: 0.3 } },
          }
        }),
      })}
    />
  )
}

export function ColumnChart({ labels, series, format = dayLabel }: { labels: string[]; series: { name: string; color: string; values: number[] }[]; format?: (label: string) => string }) {
  const slider = labels.length > SLIDER_AT * 2
  return (
    <EChart
      sig={JSON.stringify([labels, series, format === dayLabel])}
      build={(p) => ({
        ...frame(p, { legend: series.map((s) => s.name), slider }),
        tooltip: {
          ...tooltip(p),
          trigger: 'axis',
          axisPointer: { type: 'shadow', shadowStyle: { color: alpha(p.dark ? '#ffffff' : '#000000', 0.05) } },
          formatter: (params: unknown) => {
            const { header, rows } = axisRows(params)
            return tipHtml(p, header, rows)
          },
        },
        xAxis: categoryAxis(p, labels, format, true),
        yAxis: valueAxis(p, { minInterval: 1 }),
        dataZoom: [insideZoom({ xAxisIndex: 0 }, 3), ...(slider ? [sliderZoom(p, series.length > 0, (i) => format(labels[i] ?? ''))] : [])],
        series: series.map((s, i) => ({
          id: `${i}:${s.name}`,
          name: s.name,
          type: 'bar' as const,
          data: labels.map((_, k) => s.values[k] ?? 0),
          color: p.color(s.color),
          barMaxWidth: 22,
          barGap: '18%',
          barCategoryGap: '32%',
          itemStyle: { borderRadius: [4, 4, 0, 0] },
          emphasis: { focus: 'none' as const, itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0, 0, 0, 0.2)' } },
        })),
      })}
    />
  )
}

export type ChartRef = { value: number; label: string; color: string }
export type ChartDot = { id: string; y: number; color: string; title: string; onClick?: () => void }

const DAY_MS = 86_400_000

/** Midnight local time, so the time axis ticks land on the right calendar day. */
function localDay(iso: string): number {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return new Date(y, m - 1, d).getTime()
}

/** Groups dots into one series per legend entry (matched by colour) so the legend can toggle them. */
function dotSeries<T extends ChartDot>(p: Palette, points: T[], legend: { name: string; color: string }[], place: (dot: T) => [number, number], extra: Partial<ScatterSeriesOption> = {}): ScatterSeriesOption[] {
  const groups = new Map<string, { name: string; dots: T[] }>()
  legend.forEach((l) => groups.set(l.color, { name: l.name, dots: [] }))
  points.forEach((dot) => {
    if (!groups.has(dot.color)) groups.set(dot.color, { name: '', dots: [] })
    groups.get(dot.color)!.dots.push(dot)
  })
  return [...groups.entries()].map(([color, g], i) => ({
    id: `${i}:${g.name || color}`,
    name: g.name || undefined,
    type: 'scatter',
    color: p.color(color),
    symbolSize: 10,
    cursor: g.dots.some((d) => d.onClick) ? 'pointer' : 'default',
    itemStyle: { borderColor: p.surface, borderWidth: 1.5, opacity: 0.9 },
    emphasis: { scale: 1.5, itemStyle: { borderColor: p.text, opacity: 1 } },
    data: g.dots.map((d) => ({ value: place(d), id: d.id, title: d.title })),
    ...extra,
  }))
}

function refLines(refs: ChartRef[], p: Palette): MarkLineComponentOption {
  return {
    silent: true,
    symbol: 'none',
    animationDuration: 500,
    data: refs.map((r) => ({
      yAxis: r.value,
      lineStyle: { color: p.color(r.color), type: [6, 4], width: 1.5 },
      label: { formatter: r.label, position: 'insideEndTop', color: p.color(r.color), fontWeight: 600, fontSize: 11 },
    })),
  }
}

function pickHandler(points: ChartDot[]) {
  return (id: string) => points.find((p) => p.id === id)?.onClick?.()
}

/** Points on a calendar axis from ``start`` to ``end`` (ISO dates); dots sharing a day fan out. */
export function ScatterChart({ start, end, points, refs = [], legend = [] }: { start: string; end: string; points: (ChartDot & { date: string })[]; refs?: ChartRef[]; legend?: { name: string; color: string }[] }) {
  const top = Math.max(1, ...points.map((p) => p.y), ...refs.map((r) => r.value))
  const sig = JSON.stringify([start, end, points.map(({ onClick: _, ...rest }) => rest), refs, legend])
  return (
    <EChart
      sig={sig}
      onPick={pickHandler(points)}
      build={(p) => {
        const seen = new Map<string, number>()
        const place = (dot: ChartDot & { date: string }): [number, number] => {
          const slot = `${dot.date}:${Math.round(dot.y / Math.max(top / 40, 0.1))}`
          const k = seen.get(slot) ?? 0
          seen.set(slot, k + 1)
          const shift = k === 0 ? 0 : (k % 2 ? 1 : -1) * Math.ceil(k / 2) * 0.28 * DAY_MS
          return [localDay(dot.date) + shift, dot.y]
        }
        const pointer = { show: true, type: 'line' as const, snap: false, triggerTooltip: false, lineStyle: { color: p.text3, type: 'dashed' as const } }
        return {
          ...frame(p, { legend: legend.map((l) => l.name), slider: true, round: true }),
          tooltip: { ...tooltip(p), trigger: 'item', formatter: itemTip(p) },
          xAxis: {
            type: 'time',
            min: localDay(start) - DAY_MS / 2,
            max: localDay(end) + DAY_MS / 2,
            axisLine: { lineStyle: { color: p.borderStrong } },
            axisTick: { show: false },
            splitLine: { show: false },
            axisLabel: { color: p.text2, fontSize: 11, hideOverlap: true, formatter: '{MM}-{dd}' },
            axisPointer: { ...pointer, label: { ...pointerLabel(p), formatter: (e: { value: unknown }) => new Date(Number(e.value)).toLocaleDateString('sv-SE') } },
          },
          yAxis: { ...valueAxis(p), axisPointer: { ...pointer, label: valueAxis(p).axisPointer.label } },
          dataZoom: [
            insideZoom({ xAxisIndex: 0 }, 2 * DAY_MS),
            insideZoom({ yAxisIndex: 0 }, 1),
            sliderZoom(p, legend.length > 0, (v) => new Date(v).toLocaleDateString('sv-SE').slice(5)),
          ],
          series: [...dotSeries(p, points, legend, place), { id: '__refs', type: 'scatter', data: [], silent: true, markLine: refLines(refs, p) }],
        }
      }}
    />
  )
}

/** Same breakpoints as before: about four even steps above the largest value. */
function niceMax(values: number[]): number {
  const max = Math.max(1, ...values)
  return Math.ceil(max / 4) * 4
}

/** One column of dots per category, e.g. open work per status by days spent there. */
export function DotStrip({ categories, points, refs = [], bands = [], legend = [] }: { categories: { key: string; label: string; color: string }[]; points: (ChartDot & { category: string })[]; refs?: ChartRef[]; bands?: { from: number; color: string }[]; legend?: { name: string; color: string }[] }) {
  const top = niceMax([...points.map((p) => p.y), ...refs.map((r) => r.value), ...bands.map((b) => b.from)])
  const sig = JSON.stringify([categories, points.map(({ onClick: _, ...rest }) => rest), refs, bands, legend])
  const chars = Math.max(4, Math.floor(130 / Math.max(categories.length, 1)))
  return (
    <EChart
      sig={sig}
      onPick={pickHandler(points)}
      build={(p) => {
        const index = new Map(categories.map((c, i) => [c.key, i]))
        const rank = new Map<string, number>()
        const lane = new Map<string, number>()
        ;[...points].sort((a, b) => a.y - b.y).forEach((d) => {
          const k = lane.get(d.category) ?? 0
          lane.set(d.category, k + 1)
          rank.set(d.id, k)
        })
        const place = (dot: ChartDot & { category: string }): [number, number] => {
          const many = (lane.get(dot.category) ?? 0) > 1
          const k = rank.get(dot.id) ?? 0
          return [(index.get(dot.category) ?? 0) + (many ? ((k % 7) / 6 - 0.5) * 0.6 : 0), dot.y]
        }
        return {
          ...frame(p, { legend: legend.map((l) => l.name), round: true }),
          tooltip: { ...tooltip(p), trigger: 'item', formatter: itemTip(p) },
          xAxis: [
            {
              type: 'category',
              data: categories.map((c) => c.label),
              axisLine: { lineStyle: { color: p.borderStrong } },
              axisTick: { show: false },
              splitLine: { show: true, lineStyle: { color: p.border } },
              axisLabel: {
                interval: 0,
                fontSize: 11,
                fontWeight: 600,
                color: ((_: unknown, i: number) => p.color(categories[i]?.color ?? p.text2)) as unknown as string,
                formatter: (v: string) => (v.length > chars ? `${v.slice(0, chars - 1)}…` : v),
              },
            },
            { type: 'value', show: false, min: -0.5, max: categories.length - 0.5 },
          ],
          yAxis: {
            ...valueAxis(p),
            max: top,
            axisPointer: { show: true, type: 'line', snap: false, triggerTooltip: false, lineStyle: { color: p.text3, type: 'dashed' }, label: valueAxis(p).axisPointer.label },
          },
          dataZoom: [insideZoom({ yAxisIndex: 0 }, 1)],
          series: [
            ...dotSeries(p, points, legend, place, { xAxisIndex: 1 }),
            {
              id: '__marks',
              type: 'scatter',
              xAxisIndex: 1,
              data: [],
              silent: true,
              markArea: {
                silent: true,
                data: bands.map((b, i) => [{ yAxis: b.from, itemStyle: { color: alpha(p.color(b.color), 0.09) } }, { yAxis: bands[i + 1]?.from ?? top }]),
              } as MarkAreaComponentOption,
              markLine: refLines(refs, p),
            },
          ],
        }
      }}
    />
  )
}
