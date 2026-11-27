import {
  Background,
  BaseEdge,
  ConnectionMode,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from 'react'
import { t } from '../i18n'
import type { Status } from '../types'
import { flowStatuses } from '../workflow'
import {
  DIAGRAM_NODE_H,
  DIAGRAM_NODE_W,
  diagramLayout,
  hopMarks,
  routePadding,
  routePolyline,
  wirePath,
  type EdgeRoute,
  type Hop,
} from '../workflowRoutes'
import { findingsForStatus, lintFlow, warnStatusNames, type LintFinding } from '../workflowLint'
import { ColorField } from './ColorField'
import { Modal, Select } from './ui'
import '@xyflow/react/dist/style.css'

const START_ID = '__start__'
const BOUNDS_ID = '__bounds__'
const NODE_W = DIAGRAM_NODE_W
const NODE_H = DIAGRAM_NODE_H

export type StatusPropertiesMap = Record<string, Record<string, string>>

type StatusNodeData = {
  status?: Status
  dimmed?: boolean
  warn?: boolean
  selected?: boolean
  kind?: 'start' | 'status' | 'bounds'
}

/** Twelve anchors: L/R centers + 5 top/bottom slots so wires leave straight without stair-steps. */
const ANCHORS: { id: string; position: Position; style?: CSSProperties; dot?: boolean }[] = [
  { id: 'left', position: Position.Left, dot: true },
  { id: 'right', position: Position.Right, dot: true },
  { id: 'top-left', position: Position.Top, style: { left: '16%' } },
  { id: 'top-mid-left', position: Position.Top, style: { left: '33%' } },
  { id: 'top', position: Position.Top, style: { left: '50%' }, dot: true },
  { id: 'top-mid-right', position: Position.Top, style: { left: '67%' } },
  { id: 'top-right', position: Position.Top, style: { left: '84%' } },
  { id: 'bottom-left', position: Position.Bottom, style: { left: '16%' } },
  { id: 'bottom-mid-left', position: Position.Bottom, style: { left: '33%' } },
  { id: 'bottom', position: Position.Bottom, style: { left: '50%' }, dot: true },
  { id: 'bottom-mid-right', position: Position.Bottom, style: { left: '67%' } },
  { id: 'bottom-right', position: Position.Bottom, style: { left: '84%' } },
]

function NodeAnchors({ connectable }: { connectable: boolean }) {
  return (
    <>
      {ANCHORS.map((a) => (
        <Handle
          key={`${a.id}-s`}
          id={`${a.id}-s`}
          type="source"
          position={a.position}
          className="wf-handle"
          style={a.dot ? a.style : { ...a.style, opacity: 0, pointerEvents: 'none' }}
          isConnectable={connectable && !!a.dot}
        />
      ))}
      {ANCHORS.map((a) => (
        <Handle
          key={`${a.id}-t`}
          id={`${a.id}-t`}
          type="target"
          position={a.position}
          className="wf-handle"
          style={a.dot ? a.style : { ...a.style, opacity: 0, pointerEvents: 'none' }}
          isConnectable={connectable && !!a.dot}
        />
      ))}
    </>
  )
}

function StartNode() {
  return (
    <div className="wf-start-node" title={t('起始')}>
      <span className="wf-start-dot" />
      <span className="tiny faint">{t('起始')}</span>
      <Handle type="source" position={Position.Right} id="out" className="wf-handle" isConnectable={false} />
    </div>
  )
}

function StatusNode({ data }: NodeProps<Node<StatusNodeData>>) {
  const { status, dimmed, warn, selected } = data
  if (!status) return null
  return (
    <div
      className={`wf-node${dimmed ? ' dimmed' : ''}${warn ? ' warn' : ''}${selected ? ' selected' : ''}`}
      style={{
        '--wf-color': status.color,
        width: NODE_W,
        height: NODE_H,
        minHeight: NODE_H,
        boxSizing: 'border-box',
      } as CSSProperties}
    >
      <span className="wf-node-dot" />
      <span
        className="wf-node-label"
        style={{
          whiteSpace: 'normal',
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
          lineHeight: 1.3,
          flex: '1 1 auto',
          minWidth: 0,
        }}
      >
        {t(status.name)}
      </span>
      <NodeAnchors connectable />
    </div>
  )
}

type Point = { x: number; y: number }

function shift(position: Position, x: number, y: number, dist: number): Point {
  if (position === Position.Left) return { x: x - dist, y }
  if (position === Position.Right) return { x: x + dist, y }
  if (position === Position.Top) return { x, y: y - dist }
  return { x, y: y + dist }
}

/** Orthogonal points. Parallel edges keep their own track instead of sharing one stroke. */
function edgePoints(
  sx: number,
  sy: number,
  tx: number,
  ty: number,
  sourcePosition: Position,
  targetPosition: Position,
  offset: number,
  gutterX?: number,
  stub = 16,
): Point[] {
  if (gutterX != null) {
    const y1 = sy - stub
    const y2 = ty - stub
    return [
      { x: sx, y: sy },
      { x: sx, y: y1 },
      { x: gutterX, y: y1 },
      { x: gutterX, y: y2 },
      { x: tx, y: y2 },
      { x: tx, y: ty },
    ]
  }
  const sameVertical = sourcePosition === targetPosition
    && (sourcePosition === Position.Top || sourcePosition === Position.Bottom)
  if (sameVertical) {
    const leave = shift(sourcePosition, sx, sy, offset)
    const y = sourcePosition === Position.Top ? Math.min(leave.y, ty - offset) : Math.max(leave.y, ty + offset)
    return [{ x: sx, y: sy }, { x: sx, y }, { x: tx, y }, { x: tx, y: ty }]
  }
  if (Math.abs(sy - ty) < 2 && (
    (sourcePosition === Position.Right && targetPosition === Position.Left)
    || (sourcePosition === Position.Left && targetPosition === Position.Right)
  )) {
    return [{ x: sx, y: sy }, { x: tx, y: ty }]
  }
  const midX = (sx + tx) / 2
  return [{ x: sx, y: sy }, { x: midX, y: sy }, { x: midX, y: ty }, { x: tx, y: ty }]
}

function labelAt(points: Point[], hops: Hop[] = []): Point {
  let best = -1
  let at = points[0] ?? { x: 0, y: 0 }
  for (let i = 1; i < points.length; i += 1) {
    const a = points[i - 1]
    const b = points[i]
    const dx = Math.abs(b.x - a.x)
    const dy = Math.abs(b.y - a.y)
    const len = Math.hypot(dx, dy)
    if (len < 1) continue
    if (dy < 2 && dx > 16) {
      const y = (a.y + b.y) / 2
      const lo = Math.min(a.x, b.x)
      const hi = Math.max(a.x, b.x)
      const xs = hops
        .filter((hop) => Math.abs(hop.y - y) < 4 && hop.x > lo + 12 && hop.x < hi - 12)
        .map((hop) => hop.x)
        .sort((m, n) => m - n)
      const cuts = [lo, ...xs, hi]
      for (let j = 1; j < cuts.length; j += 1) {
        const span = cuts[j] - cuts[j - 1]
        const score = span + 1000
        if (score > best) {
          best = score
          at = { x: (cuts[j] + cuts[j - 1]) / 2, y: y - 9 }
        }
      }
      continue
    }
    if (len > best) {
      best = len
      at = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
    }
  }
  return at
}

function TransitionEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style,
  markerEnd,
  label,
  selected,
  data,
}: EdgeProps) {
  const extra = data as {
    onDelete?: (id: string) => void
    offset?: number
    gutterX?: number
    stub?: number
    side?: EdgeRoute['side']
    laneY?: number
    sourceJog?: number
    targetJog?: number
    hops?: Hop[]
    title?: string
  } | undefined
  const offset = extra?.offset ?? 20
  const points = extra?.side
    ? routePolyline(
      { x: sourceX, y: sourceY },
      { x: targetX, y: targetY },
      {
        sourceHandle: '',
        targetHandle: '',
        offset,
        dashed: false,
        spine: extra.side === 'straight',
        side: extra.side,
        laneY: extra.laneY,
        sourceJog: extra.sourceJog,
        targetJog: extra.targetJog,
        hops: extra.hops ?? [],
      },
    )
    : edgePoints(
      sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition,
      offset, extra?.gutterX, extra?.stub,
    )
  const path = wirePath(points, extra?.hops ?? [])
  const marks = hopMarks(points, extra?.hops ?? [])
  const { x: labelX, y: labelY } = labelAt(points, extra?.hops ?? [])
  const onDelete = extra?.onDelete
  const showLabel = !!(label || selected)
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={{
        ...style,
        strokeWidth: selected ? 2.4 : (style as CSSProperties)?.strokeWidth ?? 1.5,
        stroke: selected ? 'var(--accent)' : (style as CSSProperties)?.stroke,
      }} />
      {marks && (
        <path
          d={marks}
          fill="none"
          pointerEvents="none"
          stroke={selected ? 'var(--accent)' : ((style as CSSProperties)?.stroke ?? 'var(--text-3)')}
          strokeWidth={selected ? 2.6 : 2.2}
        />
      )}
      {/* Empty label chips look like black dashes on the path — only mount when needed. */}
      {showLabel && (
        <EdgeLabelRenderer>
          <div
            className={`wf-edge-label nodrag nopan${selected ? ' selected' : ''}`}
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              zIndex: selected ? 30 : 20,
              boxShadow: '0 1px 4px rgba(15, 23, 42, 0.12)',
              border: '1px solid var(--border)',
              whiteSpace: 'nowrap',
              maxWidth: 'none',
            }}
          >
            {(label ?? extra?.title) ? <span className="tiny">{label ?? extra?.title}</span> : null}
            {selected && onDelete && (
              <button
                type="button"
                className="wf-edge-del"
                title={t('删除转换')}
                onClick={(e) => {
                  e.stopPropagation()
                  onDelete(id)
                }}
              >
                ×
              </button>
            )}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

/** Handle choice after a status has been dragged off the ordered row. */
function pickHandles(
  from: { x: number; y: number },
  to: { x: number; y: number },
  slot = 0,
): { sourceHandle: string; targetHandle: string; offset: number } {
  const dx = to.x - from.x
  const dy = to.y - from.y
  const fan = ['', '-left', '-right'] as const
  const side = fan[Math.abs(slot) % 3]
  const offset = 18 + (Math.abs(slot) % 4) * 18

  if (dx < -80) {
    const s = side === '' ? 'bottom' : `bottom${side}`
    return { sourceHandle: `${s}-s`, targetHandle: `${s}-t`, offset }
  }
  if (dx > 60 && Math.abs(dy) < 90) {
    return { sourceHandle: 'right-s', targetHandle: 'left-t', offset: 12 }
  }
  if (Math.abs(dy) >= Math.abs(dx)) {
    if (dy >= 0) {
      const s = side === '' ? 'bottom' : `bottom${side}`
      const t = side === '' ? 'top' : `top${side}`
      return { sourceHandle: `${s}-s`, targetHandle: `${t}-t`, offset }
    }
    const s = side === '' ? 'top' : `top${side}`
    const t = side === '' ? 'bottom' : `bottom${side}`
    return { sourceHandle: `${s}-s`, targetHandle: `${t}-t`, offset }
  }
  if (dx >= 0) return { sourceHandle: 'right-s', targetHandle: 'left-t', offset: 12 }
  return { sourceHandle: 'left-s', targetHandle: 'right-t', offset: 12 }
}

function BoundsNode() {
  return <div style={{ width: 1, height: '100%' }} />
}

const nodeTypes = { status: StatusNode, start: StartNode, bounds: BoundsNode }
const edgeTypes = { transition: TransitionEdge }

export function toggleTransition(
  matrix: Record<string, string[]>,
  from: string,
  to: string,
  on: boolean,
): Record<string, string[]> {
  if (from === START_ID || to === START_ID || from === to) return matrix
  const current = matrix[from] ?? []
  const has = current.includes(to)
  if (on === has) return matrix
  const next = { ...matrix, [from]: on ? [...current, to] : current.filter((x) => x !== to) }
  if (!next[from].length) delete next[from]
  return next
}

function initialStatus(statuses: Status[], transitions: Record<string, string[]>): Status | null {
  const used = flowStatuses(transitions, statuses)
  return (used.length ? used : statuses)[0] ?? null
}

function edgeLook(
  route: Pick<EdgeRoute, 'dashed' | 'spine'> | null,
  backward: boolean,
  focus: 'active' | 'mute' | null,
): CSSProperties {
  const base: CSSProperties = route?.spine
    ? { stroke: 'var(--text-2)', strokeWidth: 1.8 }
    : route?.dashed || backward
      ? { stroke: 'var(--text-3)', strokeWidth: 1.25, strokeDasharray: '6 5' }
      : { stroke: 'var(--text-3)', strokeWidth: 1.4 }
  if (focus === 'active') return { ...base, stroke: 'var(--accent)', strokeWidth: 2.2, opacity: 1 }
  if (focus === 'mute') return { ...base, opacity: 0.25 }
  return base
}

function layoutNodes(
  statuses: Status[],
  transitions: Record<string, string[]>,
  positions: Map<string, { x: number; y: number }>,
  warn: Set<string>,
  selected: string | null,
  focusStatus?: string | null,
): Node<StatusNodeData>[] {
  const layout = diagramLayout(statuses, transitions, START_ID, positions)
  const defaults = layout.positions
  const { active, unused } = layout
  const unusedIds = new Set(unused.map((status) => status.id))
  const pad = routePadding(layout.routes.values())
  const related = new Set<string>()
  if (focusStatus) {
    related.add(focusStatus)
    for (const [from, tos] of Object.entries(transitions)) {
      if (from === focusStatus) tos.forEach((to) => related.add(to))
      else if (tos.includes(focusStatus)) related.add(from)
    }
  }

  const place = (list: Status[]) =>
    list.map((status) => {
      const pos = positions.get(status.name) ?? defaults.get(status.name) ?? { x: 0, y: 0 }
      if (!positions.has(status.name)) positions.set(status.name, pos)
      const muted = focusStatus ? !related.has(status.name) : false
      return {
        id: status.name,
        type: 'status' as const,
        position: pos,
        data: {
          status,
          kind: 'status' as const,
          dimmed: unusedIds.has(status.id) || muted,
          warn: warn.has(status.name),
          selected: selected === status.name,
        },
        draggable: true,
        selectable: true,
      }
    })

  const startPos = positions.get(START_ID) ?? defaults.get(START_ID) ?? { x: 16, y: 86 }
  if (!positions.has(START_ID)) positions.set(START_ID, startPos)
  const ys = [...defaults.values()].map((point) => point.y)
  const minY = ys.length ? Math.min(...ys) : 72
  const maxY = ys.length ? Math.max(...ys) : minY
  const anchor = defaults.get(active[0]?.name ?? '') ?? { x: 16, y: minY }

  return [
    {
      id: BOUNDS_ID,
      type: 'bounds',
      position: { x: anchor.x, y: minY - pad.above },
      data: { kind: 'bounds' },
      style: {
        width: Math.max(1, layout.gutterRight - anchor.x + 28),
        height: maxY - minY + NODE_H + pad.above + pad.below,
        opacity: 0,
        pointerEvents: 'none',
      },
      draggable: false,
      selectable: false,
      connectable: false,
      focusable: false,
    },
    {
      id: START_ID,
      type: 'start',
      position: startPos,
      data: { kind: 'start' },
      draggable: true,
      selectable: false,
    },
    ...place(active),
    ...place(unused),
  ]
}

function edgesFrom(
  transitions: Record<string, string[]>,
  statuses: Status[],
  showLabels: boolean,
  positions: Map<string, { x: number; y: number }>,
  onDeleteEdge?: (id: string) => void,
  focusStatus?: string | null,
): Edge[] {
  const layout = diagramLayout(statuses, transitions, START_ID, positions)
  const defaults = layout.positions
  const routes = layout.routes
  const posOf = (name: string) => positions.get(name) ?? defaults.get(name) ?? { x: 0, y: 0 }
  const center = (name: string) => {
    const p = posOf(name)
    return { x: p.x + NODE_W / 2, y: p.y + NODE_H / 2 }
  }

  const edges: Edge[] = []
  const first = initialStatus(statuses, transitions)
  if (first) {
    edges.push({
      id: `${START_ID}\0${first.name}`,
      source: START_ID,
      sourceHandle: 'out',
      target: first.name,
      targetHandle: 'left-t',
      type: 'transition',
      selectable: false,
      focusable: false,
      deletable: false,
      zIndex: 2,
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
      style: { stroke: 'var(--text-3)', strokeWidth: 1.4 },
      data: { offset: 12 },
      label: showLabels ? t('起始') : undefined,
    })
  }

  const outIndex = new Map<string, number>()
  const pairs: { from: string; to: string }[] = []
  for (const [from, tos] of Object.entries(transitions)) {
    for (const to of tos) {
      if (from !== to) pairs.push({ from, to })
    }
  }
  pairs.sort((a, b) => {
    const rank = (from: string, to: string) => {
      const route = routes.get(`${from}\0${to}`)
      if (!route) return 1
      if (route.dashed) return 0
      return route.spine ? 2 : 1
    }
    return rank(a.from, a.to) - rank(b.from, b.to)
  })
  for (const { from, to } of pairs) {
    const id = `${from}\0${to}`
    const route = routes.get(id) ?? null
    const planned = !!route
    const slot = outIndex.get(from) ?? 0
    outIndex.set(from, slot + 1)
    const handles = planned && route
      ? route
      : pickHandles(center(from), center(to), slot)
    const backward = center(to).x < center(from).x - 80
    const touches = !!focusStatus && (from === focusStatus || to === focusStatus)
    const focus = focusStatus ? (touches ? 'active' : 'mute') : null
    edges.push({
      id,
      source: from,
      sourceHandle: handles.sourceHandle,
      target: to,
      targetHandle: handles.targetHandle,
      type: 'transition',
      zIndex: touches ? 4 : route?.spine ? 3 : route?.dashed || backward ? 1 : 2,
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
      style: edgeLook(planned ? route : null, !planned && backward, focus),
      selectable: true,
      deletable: true,
      interactionWidth: 16,
      label: showLabels ? `${t(from)} → ${t(to)}` : undefined,
      data: {
        onDelete: onDeleteEdge,
        title: `${t(from)} → ${t(to)}`,
        offset: handles.offset,
        side: planned ? route?.side : undefined,
        laneY: planned ? route?.laneY : undefined,
        sourceJog: planned ? route?.sourceJog : undefined,
        targetJog: planned ? route?.targetJog : undefined,
        hops: planned ? route?.hops ?? [] : [],
      },
    })
  }
  return edges
}

function CreateTransitionModal({
  statuses,
  transitions,
  defaultFrom,
  onCreate,
  onClose,
}: {
  statuses: Status[]
  transitions: Record<string, string[]>
  defaultFrom?: string
  onCreate: (from: string, to: string) => void
  onClose: () => void
}) {
  const [from, setFrom] = useState(defaultFrom || statuses[0]?.name || '')
  const [to, setTo] = useState(() => {
    const rest = statuses.map((s) => s.name).filter((n) => n !== (defaultFrom || statuses[0]?.name))
    return rest[0] || ''
  })
  const exists = !!(from && to && (transitions[from] ?? []).includes(to))
  const canCreate = !!from && !!to && from !== to && !exists
  return (
    <Modal title={t('创建转换')} onClose={onClose} size="wide">
      <p className="small muted">{t('转换表示用户推进工作时执行的操作，会作为看板列之间的合法流转。')}</p>
      <div className="row wrap" style={{ gap: 12, alignItems: 'flex-end' }}>
        <label className="field" style={{ flex: 1, minWidth: 140 }}>
          <span>{t('起始状态')}</span>
          <Select className="select" value={from} onChange={(e) => setFrom(e.target.value)}>
            {statuses.map((s) => <option key={s.id} value={s.name}>{t(s.name)}</option>)}
          </Select>
        </label>
        <span className="faint" style={{ paddingBottom: 10 }}>→</span>
        <label className="field" style={{ flex: 1, minWidth: 140 }}>
          <span>{t('目标状态')}</span>
          <Select className="select" value={to} onChange={(e) => setTo(e.target.value)}>
            {statuses.map((s) => <option key={s.id} value={s.name}>{t(s.name)}</option>)}
          </Select>
        </label>
      </div>
      {exists && <p className="small" style={{ color: 'var(--danger)', marginTop: 8 }}>{t('该转换已存在')}</p>}
      <div className="row" style={{ marginTop: 16, gap: 8, justifyContent: 'flex-end' }}>
        <button type="button" className="btn" onClick={onClose}>{t('取消')}</button>
        <button
          type="button"
          className="btn primary"
          disabled={!canCreate}
          onClick={() => {
            onCreate(from, to)
            onClose()
          }}
        >
          {t('创建')}
        </button>
      </div>
    </Modal>
  )
}

function WarningsPanel({
  findings,
  collapsed,
  onToggle,
  onPickStatus,
}: {
  findings: LintFinding[]
  collapsed: boolean
  onToggle: () => void
  onPickStatus?: (name: string) => void
}) {
  const actionable = findings.filter((f) => f.level !== 'info')
  const count = actionable.length

  if (collapsed) {
    return (
      <aside className="wf-side collapsed">
        <button
          type="button"
          className="wf-side-rail"
          onClick={onToggle}
          title={t('展开警告')}
          aria-expanded={false}
        >
          <span className="wf-warn-icon" aria-hidden>⚠</span>
          <span className="wf-side-rail-label">{t('警告')}</span>
          {count > 0 && <span className="wf-side-rail-count">{count}</span>}
          <span className="wf-side-rail-chevron" aria-hidden>‹</span>
        </button>
      </aside>
    )
  }

  return (
    <aside className="wf-side">
      <div className="wf-side-body">
        <div className="wf-side-head">
          <h4 className="wf-side-title">
            <span className="wf-warn-icon" aria-hidden>⚠</span>
            {t('警告')}
            {count > 0 && <span className="count">{count}</span>}
          </h4>
          <button
            type="button"
            className="btn ghost sm icon wf-side-collapse"
            onClick={onToggle}
            title={t('收起警告')}
            aria-expanded
            aria-label={t('收起警告')}
          >
            ›
          </button>
        </div>
        <p className="tiny faint wf-side-note">
          {t('您可以在未解决以下警告的情况下保存此工作流，但您的项目可能会出错。')}
        </p>
        {count === 0 ? (
          <p className="small faint">{t('当前没有警告')}</p>
        ) : (
          <ul className="wf-finding-list">
            {actionable.map((f, i) => (
              <li key={`${f.message}-${i}`} className={`wf-finding ${f.level}`}>
                <div className="small">{f.message}</div>
                {f.statuses.length > 0 && (
                  <div className="wf-finding-tags">
                    {f.statuses.map((name) => (
                      <button key={name} type="button" className="wf-finding-tag" onClick={() => onPickStatus?.(name)}>
                        ⚠ {t(name)}
                      </button>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
        {findings.some((f) => f.level === 'info') && (
          <details className="wf-info-details">
            <summary className="tiny faint">{t('提示')}</summary>
            <ul className="wf-finding-list">
              {findings.filter((f) => f.level === 'info').map((f, i) => (
                <li key={`info-${i}`} className="wf-finding info"><div className="small">{f.message}</div></li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </aside>
  )
}

function StatusPropertiesModal({
  statusName,
  value,
  onSave,
  onClose,
  readOnly,
}: {
  statusName: string
  value: Record<string, string>
  onSave: (next: Record<string, string>) => void
  onClose: () => void
  readOnly?: boolean
}) {
  const [rows, setRows] = useState<{ key: string; value: string }[]>(() =>
    Object.entries(value).map(([key, v]) => ({ key, value: v })),
  )
  return (
    <Modal title={t('状态属性')} onClose={onClose} size="wide">
      <p className="small muted">{t('您可以使用属性来标注状态（元数据，不影响流转引擎）。')}</p>
      <p className="tiny faint">{t('必填字段标有星号 *')}</p>
      <div className="wf-props-table">
        <div className="wf-props-head">
          <span>{t('键')} *</span>
          <span>{t('值')}</span>
          <span />
        </div>
        {rows.map((row, i) => (
          <div className="wf-props-row" key={i}>
            <input
              className="input sm"
              disabled={readOnly}
              value={row.key}
              placeholder={t('键')}
              onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, key: e.target.value } : r)))}
            />
            <input
              className="input sm"
              disabled={readOnly}
              value={row.value}
              placeholder={t('值')}
              onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)))}
            />
            {!readOnly && (
              <button type="button" className="btn ghost sm danger" onClick={() => setRows(rows.filter((_, j) => j !== i))}>
                ×
              </button>
            )}
          </div>
        ))}
      </div>
      {!readOnly && (
        <button type="button" className="btn sm" style={{ marginTop: 8 }} onClick={() => setRows([...rows, { key: '', value: '' }])}>
          + {t('添加新属性')}
        </button>
      )}
      <div className="row" style={{ marginTop: 16, gap: 8, justifyContent: 'flex-end' }}>
        <button type="button" className="btn" onClick={onClose}>{t('放弃')}</button>
        {!readOnly && (
          <button
            type="button"
            className="btn primary"
            onClick={() => {
              const next: Record<string, string> = {}
              for (const row of rows) {
                const k = row.key.trim()
                if (k) next[k] = row.value
              }
              onSave(next)
              onClose()
            }}
          >
            {t('保存')}
          </button>
        )}
      </div>
      <p className="tiny faint" style={{ marginTop: 8 }}>{t('状态')}：{t(statusName)}</p>
    </Modal>
  )
}

function StatusInspector({
  status,
  transitions,
  findings,
  properties,
  readOnly,
  allStatuses,
  onChangeTransitions,
  onPatchStatus,
  onSaveProperties,
  onClose,
}: {
  status: Status
  transitions: Record<string, string[]>
  findings: LintFinding[]
  properties: Record<string, string>
  readOnly?: boolean
  allStatuses: Status[]
  onChangeTransitions: (next: Record<string, string[]>) => void
  onPatchStatus?: (changes: Partial<Pick<Status, 'name' | 'category' | 'color'>>) => void
  onSaveProperties: (next: Record<string, string>) => void
  onClose: () => void
}) {
  const [warnOpen, setWarnOpen] = useState(true)
  const [propsOpen, setPropsOpen] = useState(false)
  const [name, setName] = useState(status.name)
  const [category, setCategory] = useState(status.category)
  const [color, setColor] = useState(status.color)
  const [addIn, setAddIn] = useState(false)
  const [addOut, setAddOut] = useState(false)
  useEffect(() => {
    setName(status.name)
    setCategory(status.category)
    setColor(status.color)
  }, [status])

  const localFindings = findingsForStatus(findings, status.name, transitions)
  const incoming = allStatuses.filter((s) => (transitions[s.name] ?? []).includes(status.name))
  const outgoing = (transitions[status.name] ?? [])
    .map((n) => allStatuses.find((s) => s.name === n))
    .filter((s): s is Status => !!s)
  const needsTransition = localFindings.some((f) => f.message === t('这一状态需要转换。'))

  const commitMeta = () => {
    if (readOnly || !onPatchStatus) return
    const changes: Partial<Pick<Status, 'name' | 'category' | 'color'>> = {}
    if (name.trim() && name.trim() !== status.name) changes.name = name.trim()
    if (category !== status.category) changes.category = category
    if (color !== status.color) changes.color = color
    if (Object.keys(changes).length) onPatchStatus(changes)
  }

  return (
    <aside className="wf-side inspector">
      <div className="wf-side-body">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <h4 className="wf-side-title">{t('状态')}</h4>
            <p className="tiny faint">{t('状态捕捉了您工作流程的各个阶段。')}</p>
          </div>
          <button type="button" className="btn ghost sm icon" onClick={onClose} aria-label={t('关闭')}>✕</button>
        </div>

        {localFindings.length > 0 && (
          <button type="button" className="wf-view-warns" onClick={() => setWarnOpen(!warnOpen)}>
            <span className="wf-warn-icon">⚠</span>
            {t('查看警告 ({n})', { n: localFindings.length })}
            <span className="spacer" />
            <span className="faint">{warnOpen ? '▾' : '▸'}</span>
          </button>
        )}
        {warnOpen && localFindings.length > 0 && (
          <ul className="wf-finding-list compact">
            {localFindings.map((f, i) => (
              <li key={i} className={`wf-finding ${f.level}`}><div className="small">{f.message}</div></li>
            ))}
          </ul>
        )}

        <label className="field">
          <span>{t('名称')}</span>
          <input
            className="input sm"
            disabled={readOnly || !onPatchStatus}
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={commitMeta}
          />
        </label>
        <label className="field">
          <span>{t('类别')}</span>
          <Select
            className="select sm"
            disabled={readOnly || !onPatchStatus}
            value={category}
            onChange={(e) => {
              setCategory(e.target.value as Status['category'])
              if (!readOnly && onPatchStatus) onPatchStatus({ category: e.target.value as Status['category'] })
            }}
          >
            <option value="todo">{t('待办')}</option>
            <option value="doing">{t('进行中')}</option>
            <option value="done">{t('已完成')}</option>
          </Select>
        </label>
        <label className="field">
          <span>{t('颜色')}</span>
          <ColorField
            size="sm"
            disabled={readOnly || !onPatchStatus}
            value={color}
            onChange={setColor}
            onCommit={(next) => {
              if (!readOnly && onPatchStatus && next !== color) onPatchStatus({ color: next })
            }}
          />
        </label>

        <section className="wf-trans-section">
          <b>{t('转换')}</b>
          <p className="tiny faint">{t('转换与状态相关联，表示用户推进工作时执行的操作。')}</p>
          {needsTransition && (
            <div className="wf-inline-warn">
              <span className="wf-warn-icon">⚠</span>
              {t('这一状态需要转换。')}
            </div>
          )}
          <div className="wf-io">
            <div className="wf-io-head">
              <b className="small">{t('传入')}</b>
              {!readOnly && (
                <button type="button" className="btn ghost sm" onClick={() => { setAddIn(!addIn); setAddOut(false) }}>
                  + {t('添加转换')}
                </button>
              )}
            </div>
            {addIn && !readOnly && (
              <Select
                className="select sm"
                defaultValue=""
                onChange={(e) => {
                  const from = e.target.value
                  e.target.value = ''
                  if (!from) return
                  onChangeTransitions(toggleTransition(transitions, from, status.name, true))
                  setAddIn(false)
                }}
              >
                <option value="">{t('选择源状态…')}</option>
                {allStatuses.filter((s) => s.name !== status.name && !(transitions[s.name] ?? []).includes(status.name)).map((s) => (
                  <option key={s.id} value={s.name}>{t(s.name)}</option>
                ))}
              </Select>
            )}
            <ul className="wf-io-list">
              {incoming.map((s) => (
                <li key={s.id}>
                  <span>{t(s.name)} → {t(status.name)}</span>
                  {!readOnly && (
                    <button type="button" className="btn ghost sm danger" onClick={() => onChangeTransitions(toggleTransition(transitions, s.name, status.name, false))}>×</button>
                  )}
                </li>
              ))}
              {incoming.length === 0 && <li className="faint tiny">{t('无')}</li>}
            </ul>
          </div>
          <div className="wf-io">
            <div className="wf-io-head">
              <b className="small">{t('传出')}</b>
              {!readOnly && (
                <button type="button" className="btn ghost sm" onClick={() => { setAddOut(!addOut); setAddIn(false) }}>
                  + {t('添加转换')}
                </button>
              )}
            </div>
            {addOut && !readOnly && (
              <Select
                className="select sm"
                defaultValue=""
                onChange={(e) => {
                  const to = e.target.value
                  e.target.value = ''
                  if (!to) return
                  onChangeTransitions(toggleTransition(transitions, status.name, to, true))
                  setAddOut(false)
                }}
              >
                <option value="">{t('选择目标状态…')}</option>
                {allStatuses.filter((s) => s.name !== status.name && !(transitions[status.name] ?? []).includes(s.name)).map((s) => (
                  <option key={s.id} value={s.name}>{t(s.name)}</option>
                ))}
              </Select>
            )}
            <ul className="wf-io-list">
              {outgoing.map((s) => (
                <li key={s.id}>
                  <span>{t(status.name)} → {t(s.name)}</span>
                  {!readOnly && (
                    <button type="button" className="btn ghost sm danger" onClick={() => onChangeTransitions(toggleTransition(transitions, status.name, s.name, false))}>×</button>
                  )}
                </li>
              ))}
              {outgoing.length === 0 && <li className="faint tiny">{t('无')}</li>}
            </ul>
          </div>
        </section>

        <section className="wf-trans-section">
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <b>{t('属性')}</b>
            <button type="button" className="btn ghost sm" onClick={() => setPropsOpen(true)}>
              {readOnly ? t('查看') : `+ ${t('属性')}`}
            </button>
          </div>
          {Object.keys(properties).length === 0 ? (
            <p className="tiny faint">{t('暂无属性')}</p>
          ) : (
            <ul className="wf-io-list">
              {Object.entries(properties).map(([k, v]) => (
                <li key={k}><span className="mono tiny">{k}</span><span className="tiny faint">{v}</span></li>
              ))}
            </ul>
          )}
        </section>
      </div>
      {propsOpen && (
        <StatusPropertiesModal
          statusName={status.name}
          value={properties}
          readOnly={readOnly}
          onClose={() => setPropsOpen(false)}
          onSave={onSaveProperties}
        />
      )}
    </aside>
  )
}

function GraphInner({
  statuses,
  transitions,
  onChange,
  readOnly,
  showLabels,
  selected,
  onSelect,
  warnNames,
}: {
  statuses: Status[]
  transitions: Record<string, string[]>
  onChange: (next: Record<string, string[]>) => void
  readOnly?: boolean
  showLabels: boolean
  selected: string | null
  onSelect: (name: string | null) => void
  warnNames: Set<string>
}) {
  const positions = useRef(new Map<string, { x: number; y: number }>())
  const transitionsRef = useRef(transitions)
  transitionsRef.current = transitions
  const hoverTimer = useRef<number | null>(null)
  const [hovered, setHovered] = useState<string | null>(null)
  const focusStatus = hovered ?? selected
  const statusKey = statuses.map((s) => `${s.id}:${s.name}:${s.color}:${s.category}`).join('|')
  const transitionKey = JSON.stringify(transitions)
  const warnKey = [...warnNames].sort().join('|')

  const deleteEdgeById = useCallback(
    (edgeId: string) => {
      if (readOnly) return
      const [from, to] = edgeId.split('\0')
      if (!from || !to || from === START_ID) return
      onChange(toggleTransition(transitionsRef.current, from, to, false))
    },
    [onChange, readOnly],
  )

  const [nodes, setNodes, onNodesChange] = useNodesState(
    layoutNodes(statuses, transitions, positions.current, warnNames, selected, focusStatus),
  )
  const [edges, setEdges, onEdgesChange] = useEdgesState(
    edgesFrom(transitions, statuses, showLabels, positions.current, deleteEdgeById, focusStatus),
  )

  useEffect(() => {
    setNodes((current) => {
      const next = layoutNodes(statuses, transitions, positions.current, warnNames, selected, focusStatus)
      const prevById = new Map(current.map((node) => [node.id, node]))
      return next.map((node) => {
        const prev = prevById.get(node.id)
        if (!prev) return node
        const sameSpot = prev.position.x === node.position.x && prev.position.y === node.position.y
        const prevData = prev.data
        const nextData = node.data
        const sameData = prevData.dimmed === nextData.dimmed
          && prevData.warn === nextData.warn
          && prevData.selected === nextData.selected
          && prevData.status === nextData.status
        const sameStyle = prev.style?.width === node.style?.width && prev.style?.height === node.style?.height
        if (sameSpot && sameData && sameStyle) return prev
        return { ...prev, data: nextData, position: node.position, style: node.style }
      })
    })
  }, [statusKey, selected, focusStatus, warnKey, setNodes, statuses, transitions, warnNames])

  useEffect(() => {
    setEdges((prev) => {
      const selectedIds = new Set(prev.filter((e) => e.selected).map((e) => e.id))
      return edgesFrom(transitions, statuses, showLabels, positions.current, deleteEdgeById, focusStatus).map((e) =>
        selectedIds.has(e.id) ? { ...e, selected: true } : e,
      )
    })
  }, [transitionKey, showLabels, focusStatus, setEdges, statuses, transitions, deleteEdgeById])

  useEffect(() => () => {
    if (hoverTimer.current != null) window.clearTimeout(hoverTimer.current)
  }, [])
  const refreshRoutes = useCallback(() => {
    setEdges((prev) => {
      const selectedIds = new Set(prev.filter((e) => e.selected).map((e) => e.id))
      return edgesFrom(
        transitionsRef.current, statuses, showLabels, positions.current, deleteEdgeById, focusStatus,
      ).map((e) => (selectedIds.has(e.id) ? { ...e, selected: true } : e))
    })
  }, [deleteEdgeById, focusStatus, setEdges, showLabels, statuses])

  const onConnect = useCallback(
    (connection: Connection) => {
      if (readOnly || !connection.source || !connection.target) return
      if (connection.source === START_ID || connection.target === START_ID) return
      if (connection.source === connection.target) return
      onChange(toggleTransition(transitionsRef.current, connection.source, connection.target, true))
    },
    [onChange, readOnly],
  )

  const isValidConnection = useCallback(
    (connection: Connection | Edge) => {
      const source = connection.source
      const target = connection.target
      if (!source || !target || source === target) return false
      if (source === START_ID || target === START_ID) return false
      return true
    },
    [],
  )

  const onEdgesDelete = useCallback(
    (deleted: Edge[]) => {
      if (readOnly) return
      let next = transitionsRef.current
      for (const edge of deleted) {
        if (edge.source === START_ID) continue
        next = toggleTransition(next, edge.source, edge.target, false)
      }
      onChange(next)
    },
    [onChange, readOnly],
  )

  return (
    <div className="wf-canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        isValidConnection={isValidConnection}
        onEdgesDelete={onEdgesDelete}
        connectionMode={ConnectionMode.Loose}
        onNodeClick={(_, node) => {
          if (node.id === START_ID || node.id === BOUNDS_ID) return
          onSelect(node.id)
        }}
        onNodeMouseEnter={(_, node) => {
          if (node.id === START_ID || node.id === BOUNDS_ID) return
          if (hoverTimer.current != null) window.clearTimeout(hoverTimer.current)
          setHovered(node.id)
        }}
        onNodeMouseLeave={() => {
          if (hoverTimer.current != null) window.clearTimeout(hoverTimer.current)
          hoverTimer.current = window.setTimeout(() => setHovered(null), 90)
        }}
        onEdgeClick={() => {
          /* keep edge selection; don't clear status inspector immediately */
        }}
        onPaneClick={() => onSelect(null)}
        onNodeDrag={(_, node) => {
          positions.current.set(node.id, node.position)
        }}
        onNodeDragStop={(_, node) => {
          positions.current.set(node.id, node.position)
          refreshRoutes()
        }}
        snapToGrid
        snapGrid={[12, 12]}
        nodesDraggable
        nodesConnectable={!readOnly}
        elementsSelectable
        edgesFocusable={!readOnly}
        edgesReconnectable={false}
        deleteKeyCode={readOnly ? null : ['Backspace', 'Delete']}
        onInit={(instance) => {
          requestAnimationFrame(() => {
            void instance.fitView({ padding: 0.1 })
          })
        }}
        minZoom={0.35}
        maxZoom={1.6}
        proOptions={{ hideAttribution: true }}
        defaultEdgeOptions={{ type: 'transition' }}
      >
        <Background gap={18} size={1} color="var(--border)" />
        <Controls showInteractive={false} position="bottom-left" />
        <MiniMap
          pannable
          zoomable
          position="bottom-right"
          nodeColor={(n) => (
            n.id === BOUNDS_ID ? 'transparent' : (n.data as StatusNodeData)?.status?.color ?? '#888'
          )}
          maskColor="color-mix(in srgb, var(--bg) 70%, transparent)"
        />
      </ReactFlow>
    </div>
  )
}

/** Full Jira-style workflow diagram: canvas + warnings / status inspector. */
export function WorkflowGraphEditor({
  statuses,
  transitions,
  onChange,
  readOnly,
  toolbar,
  statusProperties = {},
  onStatusPropertiesChange,
  onPatchStatus,
}: {
  statuses: Status[]
  transitions: Record<string, string[]>
  onChange: (next: Record<string, string[]>) => void
  readOnly?: boolean
  /** Extra controls rendered next to label toggle (undo etc.). */
  toolbar?: React.ReactNode
  statusProperties?: StatusPropertiesMap
  onStatusPropertiesChange?: (next: StatusPropertiesMap) => void
  onPatchStatus?: (status: Status, changes: Partial<Pick<Status, 'name' | 'category' | 'color'>>) => void
}) {
  const [showLabels, setShowLabels] = useState(false)
  const [sideCollapsed, setSideCollapsed] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [arrangeSeq, setArrangeSeq] = useState(0)
  const findings = useMemo(
    () => lintFlow(statuses.map((s) => ({ name: s.name, category: s.category })), transitions),
    [statuses, transitions],
  )
  const warnNames = useMemo(() => warnStatusNames(findings, transitions, statuses), [findings, transitions, statuses])
  const selectedStatus = statuses.find((s) => s.name === selected) ?? null
  const resetKey = `${statuses.map((s) => s.name).join('\0')}\0${arrangeSeq}`

  return (
    <div className="wf-editor">
      <div className="wf-toolbar row wrap" style={{ gap: 10, marginBottom: 8 }}>
        {toolbar}
        {!readOnly && (
          <button type="button" className="btn sm" onClick={() => setCreateOpen(true)}>
            + {t('添加转换')}
          </button>
        )}
        <button type="button" className="btn sm" onClick={() => setArrangeSeq((n) => n + 1)}>
          {t('自动排列')}
        </button>
        <span className="spacer" />
        <label className="row small" style={{ gap: 6 }}>
          <input type="checkbox" checked={showLabels} onChange={(e) => setShowLabels(e.target.checked)} />
          {t('显示转换标签')}
        </label>
      </div>
      <p className="wf-editor-hint tiny faint">
        {readOnly
          ? t('只读：可拖动画布查看流转')
          : t('从状态锚点拖到另一状态建立转换；选中连线后按 Delete 或点 × 删除')}
      </p>
      {createOpen && !readOnly && (
        <CreateTransitionModal
          statuses={statuses}
          transitions={transitions}
          defaultFrom={selected ?? undefined}
          onClose={() => setCreateOpen(false)}
          onCreate={(from, to) => onChange(toggleTransition(transitions, from, to, true))}
        />
      )}
      <div
        className={`wf-workspace${selectedStatus ? '' : sideCollapsed ? ' side-collapsed' : ''}`}
      >
        <ReactFlowProvider key={resetKey}>
          <GraphInner
            statuses={statuses}
            transitions={transitions}
            onChange={onChange}
            readOnly={readOnly}
            showLabels={showLabels}
            selected={selected}
            onSelect={(name) => {
              setSelected(name)
              if (name) setSideCollapsed(false)
            }}
            warnNames={warnNames}
          />
        </ReactFlowProvider>
        {selectedStatus ? (
          <StatusInspector
            status={selectedStatus}
            transitions={transitions}
            findings={findings}
            properties={statusProperties[selectedStatus.name] ?? {}}
            readOnly={readOnly}
            allStatuses={statuses}
            onChangeTransitions={onChange}
            onPatchStatus={onPatchStatus ? (changes) => onPatchStatus(selectedStatus, changes) : undefined}
            onSaveProperties={(next) => {
              if (!onStatusPropertiesChange) return
              const map = { ...statusProperties }
              if (Object.keys(next).length) map[selectedStatus.name] = next
              else delete map[selectedStatus.name]
              onStatusPropertiesChange(map)
            }}
            onClose={() => setSelected(null)}
          />
        ) : (
          <WarningsPanel
            findings={findings}
            collapsed={sideCollapsed}
            onToggle={() => setSideCollapsed((v) => !v)}
            onPickStatus={setSelected}
          />
        )}
      </div>
    </div>
  )
}
