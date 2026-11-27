import type { Status } from './types'
import { flowLayout, flowStatuses } from './workflow'

/** Horizontal step between mainstream statuses. */
export const COLUMN_GAP = 196
export const ROW_Y = 72
export const DIAGRAM_NODE_W = 156
export const DIAGRAM_NODE_H = 56
const SHELF_GAP = 72
const ORIGIN_X = 96
const PITCH = 26
const HOP_R = 10

export type Hop = { x: number; y: number; bulge: -1 | 1 }
export type Point = { x: number; y: number }

export type EdgeRoute = {
  sourceHandle: string
  targetHandle: string
  offset: number
  dashed: boolean
  /** Next mainstream status, drawn as a straight step. */
  spine: boolean
  side: 'top' | 'bottom' | 'gap' | 'straight'
  laneY?: number
  sourceJog?: number
  targetJog?: number
  hops: Hop[]
}

const FANS = [
  { suffix: '-left', at: 0.16 },
  { suffix: '-mid-left', at: 0.33 },
  { suffix: '', at: 0.5 },
  { suffix: '-mid-right', at: 0.67 },
  { suffix: '-right', at: 0.84 },
] as const

function nearestFan(at: number): { suffix: string; at: number } {
  return FANS.reduce((best, fan) => (Math.abs(fan.at - at) < Math.abs(best.at - at) ? fan : best))
}

/** Statuses the diagram shows, in project order. Unused statuses follow the flow. */
export function diagramOrder(
  statuses: Status[],
  transitions: Record<string, string[]>,
): { active: Status[]; unused: Status[] } {
  const used = flowStatuses(transitions, statuses)
  const active = used.length ? used : statuses
  const ids = new Set(active.map((status) => status.id))
  return { active, unused: statuses.filter((status) => !ids.has(status.id)) }
}

/**
 * Mainstream line: follow each status's first later target until a done status.
 * Every other status used by the flow is a branch and sits on the row below.
 */
export function mainstream(
  statuses: Status[],
  transitions: Record<string, string[]>,
): { main: Status[]; branches: Status[] } {
  const byName = new Map(statuses.map((status) => [status.name, status]))
  const index = new Map(statuses.map((status, i) => [status.name, i]))
  const line: Status[] = []
  const seen = new Set<string>()
  if (statuses[0] && Object.keys(transitions).length) {
    let name: string | undefined = statuses[0].name
    while (name && !seen.has(name) && line.length < statuses.length) {
      const current = byName.get(name)
      if (!current) break
      line.push(current)
      seen.add(name)
      if (current.category === 'done') break
      const from: number = index.get(name) ?? -1
      const targets: string[] = transitions[name] ?? []
      name = targets.find((candidate): boolean => {
        const order = index.get(candidate) ?? -1
        return byName.has(candidate) && !seen.has(candidate) && order > from
      })
    }
  }
  if (line.length > 1) {
    const onLine = new Set(line)
    return { main: line, branches: statuses.filter((status) => !onLine.has(status)) }
  }
  return flowLayout(statuses, transitions)
}

type Slot = { name: string; row: 'main' | 'branch'; index: number; x: number; y: number }

function lanesFor(group: { id: string; lo: number; hi: number }[]): Map<string, number> {
  const placed: { lo: number; hi: number; lane: number }[] = []
  const sorted = [...group].sort((a, b) => (a.hi - a.lo) - (b.hi - b.lo) || a.lo - b.lo)
  const out = new Map<string, number>()
  for (const edge of sorted) {
    let lane = 0
    while (placed.some((item) => item.lane === lane && edge.lo < item.hi && edge.hi > item.lo)) lane += 1
    placed.push({ lo: edge.lo, hi: edge.hi, lane })
    out.set(edge.id, lane)
  }
  return out
}

type Draft = {
  id: string
  from: string
  to: string
  side: EdgeRoute['side']
  lane: number
  dashed: boolean
  spine: boolean
  lo: number
  hi: number
}

const V_SEP = 18

function resolveVerticalJogs(
  drafts: Draft[],
  slots: Map<string, Slot>,
  pins: Map<string, { suffix: string; jog: number; handle: string }>,
  laneYs: Map<string, number>,
): void {
  type Leg = { key: string; x: number; y1: number; y2: number; rank: number }
  const legs: Leg[] = []
  drafts.forEach((edge, index) => {
    if (edge.side === 'straight') return
    const laneY = laneYs.get(edge.id)
    if (laneY == null) return
    const source = slots.get(edge.from)!
    const target = slots.get(edge.to)!
    for (const end of ['source', 'target'] as const) {
      const slot = end === 'source' ? source : target
      const key = `${edge.id}\0${end}`
      const pin = pins.get(key)
      if (!pin) continue
      const pt = anchor({ x: slot.x, y: slot.y }, pin.handle)
      const y1 = Math.min(pt.y, laneY)
      const y2 = Math.max(pt.y, laneY)
      if (y2 - y1 < 6) continue
      legs.push({ key, x: pt.x + pin.jog, y1, y2, rank: edge.lane * 100 + index })
    }
  })
  legs.sort((a, b) => a.x - b.x || a.rank - b.rank || (a.key < b.key ? -1 : 1))
  const placed: Leg[] = []
  for (const leg of legs) {
    let x = leg.x
    const conflicts = () => placed.some(
      (other) => Math.min(leg.y2, other.y2) - Math.max(leg.y1, other.y1) > 8 && Math.abs(x - other.x) < V_SEP,
    )
    if (conflicts()) {
      for (let step = 1; step <= 24; step += 1) {
        const right = leg.x + step * (V_SEP / 2)
        x = right
        if (!conflicts()) break
        const left = leg.x - step * (V_SEP / 2)
        x = left
        if (!conflicts()) break
      }
    }
    const delta = x - leg.x
    leg.x = x
    placed.push(leg)
    const pin = pins.get(leg.key)
    if (pin && Math.abs(delta) > 0.5) pin.jog += delta
  }
}

function anchor(node: Point, handle: string): Point {
  const id = handle.replace(/-[st]$/, '')
  const fan = id.endsWith('-mid-left')
    ? 0.33
    : id.endsWith('-mid-right')
      ? 0.67
      : id.endsWith('-left')
        ? 0.16
        : id.endsWith('-right')
          ? 0.84
          : 0.5
  const side = id.replace(/-(mid-left|mid-right|left|right)$/, '')
  const x = side === 'left'
    ? node.x
    : side === 'right'
      ? node.x + DIAGRAM_NODE_W
      : node.x + DIAGRAM_NODE_W * fan
  const y = side === 'top'
    ? node.y
    : side === 'bottom'
      ? node.y + DIAGRAM_NODE_H
      : node.y + DIAGRAM_NODE_H / 2
  return { x, y }
}

/** Orthogonal polyline for one routed edge. */
export function routePolyline(source: Point, target: Point, route: EdgeRoute): Point[] {
  if (route.side === 'straight' || route.laneY == null) {
    if (Math.abs(source.y - target.y) < 1.5) return [source, target]
    const midX = (source.x + target.x) / 2
    return [source, { x: midX, y: source.y }, { x: midX, y: target.y }, target]
  }
  const laneY = route.laneY
  const sourceX = source.x + (route.sourceJog ?? 0)
  const targetX = target.x + (route.targetJog ?? 0)
  const out = 12
  const sourceOutY = source.y + Math.sign(laneY - source.y || 1) * out
  const targetOutY = target.y + Math.sign(laneY - target.y || 1) * out
  return [
    source,
    ...(Math.abs(route.sourceJog ?? 0) > 1 ? [{ x: source.x, y: sourceOutY }, { x: sourceX, y: sourceOutY }] : []),
    { x: sourceX, y: laneY },
    { x: targetX, y: laneY },
    ...(Math.abs(route.targetJog ?? 0) > 1 ? [{ x: targetX, y: targetOutY }, { x: target.x, y: targetOutY }] : []),
    target,
  ]
}

function dedupe(points: Point[]): Point[] {
  return points.filter((point, index) => (
    index === 0 || Math.hypot(point.x - points[index - 1].x, point.y - points[index - 1].y) > 0.5
  ))
}

function sweep(dir: number, bulge: number): 0 | 1 {
  const up = bulge < 0
  if (dir > 0) return up ? 0 : 1
  return up ? 1 : 0
}

/** Straight orthogonal wire, with a semicircle where another wire crosses. */
export function wirePath(points: Point[], hops: Hop[] = [], radius = HOP_R): string {
  const pts = dedupe(points)
  if (!pts.length) return ''
  if (pts.length === 1) return `M ${pts[0].x} ${pts[0].y}`
  let d = `M ${pts[0].x} ${pts[0].y}`
  for (let i = 0; i < pts.length - 1; i += 1) {
    const from = pts[i]
    const to = pts[i + 1]
    const horizontal = Math.abs(from.y - to.y) < 1 && Math.abs(from.x - to.x) > radius * 4
    const along = horizontal
      ? hops
        .filter((hop) => Math.abs(hop.y - from.y) < 2 && between(hop.x, from.x, to.x, radius))
        .sort((a, b) => (to.x - from.x) * (a.x - b.x))
      : []
    let cursorX = from.x
    for (const hop of along) {
      const dir = Math.sign(to.x - from.x) || 1
      const x1 = hop.x - dir * radius
      const x2 = hop.x + dir * radius
      if (dir * (x1 - cursorX) < 1) continue
      d += ` L ${x1} ${from.y}`
      d += ` A ${radius} ${radius} 0 0 ${sweep(dir, hop.bulge)} ${x2} ${from.y}`
      cursorX = x2
    }
    d += ` L ${to.x} ${to.y}`
  }
  return d
}

/** Solid semicircles drawn over a wire so a crossing stays visible on dashed strokes. */
export function hopMarks(points: Point[], hops: Hop[] = [], radius = HOP_R): string {
  const pts = dedupe(points)
  const parts: string[] = []
  for (let i = 0; i < pts.length - 1; i += 1) {
    const from = pts[i]
    const to = pts[i + 1]
    const horizontal = Math.abs(from.y - to.y) < 1 && Math.abs(from.x - to.x) > radius * 4
    if (!horizontal) continue
    const dir = Math.sign(to.x - from.x) || 1
    const along = hops
      .filter((hop) => Math.abs(hop.y - from.y) < 2 && between(hop.x, from.x, to.x, radius))
      .sort((a, b) => dir * (a.x - b.x))
    for (const hop of along) {
      const x1 = hop.x - dir * radius
      const x2 = hop.x + dir * radius
      parts.push(`M ${x1} ${from.y} A ${radius} ${radius} 0 0 ${sweep(dir, hop.bulge)} ${x2} ${from.y}`)
    }
  }
  return parts.join(' ')
}

function between(value: number, a: number, b: number, margin: number): boolean {
  const lo = Math.min(a, b) + margin
  const hi = Math.max(a, b) - margin
  return value > lo && value < hi
}

type Seg = { id: string; x1: number; y1: number; x2: number; y2: number; horizontal: boolean }

function segments(id: string, points: Point[]): Seg[] {
  const pts = dedupe(points)
  const out: Seg[] = []
  for (let i = 1; i < pts.length; i += 1) {
    const a = pts[i - 1]
    const b = pts[i]
    const horizontal = Math.abs(a.y - b.y) < 1 && Math.abs(a.x - b.x) > 16
    const vertical = Math.abs(a.x - b.x) < 1 && Math.abs(a.y - b.y) > 16
    if (!horizontal && !vertical) continue
    out.push({
      id,
      x1: Math.min(a.x, b.x),
      y1: Math.min(a.y, b.y),
      x2: Math.max(a.x, b.x),
      y2: Math.max(a.y, b.y),
      horizontal,
    })
  }
  return out
}

function crossingHops(polylines: { id: string; points: Point[]; bulge: -1 | 1 }[]): Map<string, Hop[]> {
  const segs = polylines.flatMap((item) => segments(item.id, item.points))
  const bulgeOf = new Map(polylines.map((item) => [item.id, item.bulge]))
  const hops = new Map<string, Hop[]>()
  const horizontals = segs.filter((seg) => seg.horizontal)
  const verticals = segs.filter((seg) => !seg.horizontal)
  for (const across of horizontals) {
    for (const down of verticals) {
      if (across.id === down.id) continue
      if (down.x1 <= across.x1 + HOP_R || down.x1 >= across.x2 - HOP_R) continue
      if (across.y1 <= down.y1 + 2 || across.y1 >= down.y2 - 2) continue
      const list = hops.get(across.id) ?? []
      if (list.some((hop) => Math.abs(hop.x - down.x1) < HOP_R * 2 && Math.abs(hop.y - across.y1) < 2)) {
        continue
      }
      list.push({ x: down.x1, y: across.y1, bulge: bulgeOf.get(across.id) ?? -1 })
      hops.set(across.id, list)
    }
  }
  return hops
}

function buildRoutes(
  main: Status[],
  branches: Status[],
  transitions: Record<string, string[]>,
  overrides?: ReadonlyMap<string, Point>,
): { routes: Map<string, EdgeRoute>; branchY: number; positions: Map<string, Point> } {
  const rank = new Map<string, number>()
  ;[...main, ...branches].forEach((status) => rank.set(status.name, status.sort_order))
  const xOf = (index: number) => ORIGIN_X + index * COLUMN_GAP
  const slots = new Map<string, Slot>()
  main.forEach((status, index) => {
    const custom = overrides?.get(status.name)
    slots.set(status.name, {
      name: status.name,
      row: 'main',
      index,
      x: custom?.x ?? xOf(index),
      y: custom?.y ?? ROW_Y,
    })
  })
  branches.forEach((status, index) => {
    const custom = overrides?.get(status.name)
    slots.set(status.name, {
      name: status.name,
      row: 'branch',
      index,
      x: custom?.x ?? xOf(index),
      y: custom?.y ?? ROW_Y,
    })
  })

  const drafts: Draft[] = []
  for (const [from, tos] of Object.entries(transitions)) {
    const source = slots.get(from)
    if (!source) continue
    for (const name of tos) {
      const target = slots.get(name)
      if (!target || target.name === source.name) continue
      const same = source.row === target.row && Math.abs(source.y - target.y) < 16
      const forward = source.index + 1 === target.index && target.x > source.x + DIAGRAM_NODE_W - 12
      const dashed = (rank.get(target.name) ?? 0) < (rank.get(source.name) ?? 0)
      let side: EdgeRoute['side'] = 'gap'
      if (same && forward) side = 'straight'
      else if (same && source.row === 'main' && target.index > source.index) side = 'top'
      else if (same && source.row === 'branch') side = 'bottom'
      else if (same && source.row === 'main') side = branches.length ? 'gap' : 'bottom'
      else if (!branches.length) side = 'bottom'
      drafts.push({
        id: `${source.name}\0${target.name}`,
        from: source.name,
        to: target.name,
        side,
        lane: 0,
        dashed,
        spine: side === 'straight',
        lo: Math.min(source.x, target.x),
        hi: Math.max(source.x, target.x) + DIAGRAM_NODE_W,
      })
    }
  }

  const laneOf = (side: EdgeRoute['side']) => lanesFor(
    drafts.filter((edge) => edge.side === side).map((edge) => ({ id: edge.id, lo: edge.lo, hi: edge.hi })),
  )
  const topLane = laneOf('top')
  const bottomLane = laneOf('bottom')
  const gapLane = laneOf('gap')
  for (const edge of drafts) {
    const table = edge.side === 'top' ? topLane : edge.side === 'bottom' ? bottomLane : gapLane
    edge.lane = table.get(edge.id) ?? 0
  }
  const gapCount = Math.max(0, ...[...gapLane.values()].map((lane) => lane + 1))
  const mainYs = [...slots.values()].filter((slot) => slot.row === 'main').map((slot) => slot.y)
  const mainTop = mainYs.length ? Math.min(...mainYs) : ROW_Y
  const mainBottom = (mainYs.length ? Math.max(...mainYs) : ROW_Y) + DIAGRAM_NODE_H
  const branchY = mainBottom + (branches.length
    ? (gapCount ? 18 + gapCount * PITCH + 22 : 52)
    : 0)
  for (const slot of slots.values()) {
    if (slot.row === 'branch' && !overrides?.has(slot.name)) slot.y = branchY
  }
  const branchYs = [...slots.values()].filter((slot) => slot.row === 'branch').map((slot) => slot.y)
  const lowerBottom = (branchYs.length ? Math.max(...branchYs) : (mainBottom - DIAGRAM_NODE_H)) + DIAGRAM_NODE_H

  const pinOf = (edge: Draft, end: 'source' | 'target', source: Slot, target: Slot): 'top' | 'bottom' => {
    if (edge.side === 'top') return 'top'
    if (edge.side === 'bottom') return 'bottom'
    const slot = end === 'source' ? source : target
    const other = end === 'source' ? target : source
    if (slot.row !== other.row) {
      if (end === 'source') return slot.row === 'branch' ? 'top' : 'bottom'
      return slot.row === 'main' ? 'bottom' : 'top'
    }
    return slot.y <= other.y + 4 ? 'bottom' : 'top'
  }
  const channelAt = (index: number, count: number) => {
    if (count <= 1) return 0.5
    if (count === 2) return index === 0 ? 0.33 : 0.67
    if (count === 3) return [0.16, 0.5, 0.84][index]
    if (count === 4) return [0.16, 0.33, 0.67, 0.84][index]
    if (count === 5) return [0.16, 0.33, 0.5, 0.67, 0.84][index]
    return 0.1 + (0.8 * index) / (count - 1)
  }
  type Pin = { edge: Draft; end: 'source' | 'target'; slot: Slot; side: 'top' | 'bottom' }
  const groups = new Map<string, Pin[]>()
  for (const edge of drafts) {
    if (edge.side === 'straight') continue
    const source = slots.get(edge.from)!
    const target = slots.get(edge.to)!
    const pins: Pin[] = [
      { edge, end: 'source', slot: source, side: pinOf(edge, 'source', source, target) },
      { edge, end: 'target', slot: target, side: pinOf(edge, 'target', source, target) },
    ]
    for (const pin of pins) {
      const key = `${pin.slot.name}\0${pin.side}`
      const list = groups.get(key) ?? []
      list.push(pin)
      groups.set(key, list)
    }
  }
  const placed = new Map<string, { suffix: string; jog: number; handle: string }>()
  for (const list of groups.values()) {
    list.sort((a, b) => {
      const other = (pin: Pin) => slots.get(pin.end === 'source' ? pin.edge.to : pin.edge.from)!.x
      return other(a) - other(b) || a.edge.lane - b.edge.lane || (a.edge.id < b.edge.id ? -1 : 1)
    })
    list.forEach((pin, index) => {
      const at = channelAt(index, list.length)
      const fan = nearestFan(at)
      const end = pin.end === 'source' ? 's' : 't'
      placed.set(`${pin.edge.id}\0${pin.end}`, {
        suffix: fan.suffix,
        jog: (at - fan.at) * DIAGRAM_NODE_W,
        handle: `${pin.side}${fan.suffix}-${end}`,
      })
    })
  }

  const laneYs = new Map<string, number>()
  for (const edge of drafts) {
    const offset = 22 + edge.lane * PITCH
    if (edge.side === 'top') laneYs.set(edge.id, mainTop - offset)
    else if (edge.side === 'bottom') laneYs.set(edge.id, lowerBottom + offset)
    else if (edge.side === 'gap') laneYs.set(edge.id, mainBottom + 16 + edge.lane * PITCH)
  }
  resolveVerticalJogs(drafts, slots, placed, laneYs)

  const routes = new Map<string, EdgeRoute>()
  const polylines: { id: string; points: Point[]; bulge: -1 | 1 }[] = []
  for (const edge of drafts) {
    const source = slots.get(edge.from)!
    const target = slots.get(edge.to)!
    const offset = 22 + edge.lane * PITCH
    const side = edge.side
    const laneY = laneYs.get(edge.id)
    const sourcePin = placed.get(`${edge.id}\0source`)
    const targetPin = placed.get(`${edge.id}\0target`)
    const route: EdgeRoute = {
      sourceHandle: side === 'straight' ? 'right-s' : (sourcePin?.handle ?? 'bottom-s'),
      targetHandle: side === 'straight' ? 'left-t' : (targetPin?.handle ?? 'top-t'),
      offset,
      dashed: edge.dashed,
      spine: edge.spine,
      side,
      laneY,
      sourceJog: sourcePin?.jog ?? 0,
      targetJog: targetPin?.jog ?? 0,
      hops: [],
    }
    routes.set(edge.id, route)
    const fromPt = anchor({ x: source.x, y: source.y }, route.sourceHandle)
    const toPt = anchor({ x: target.x, y: target.y }, route.targetHandle)
    polylines.push({
      id: edge.id,
      points: routePolyline(fromPt, toPt, route),
      bulge: side === 'bottom' ? 1 : -1,
    })
  }
  const hops = crossingHops(polylines)
  for (const [id, list] of hops) {
    const route = routes.get(id)
    if (route) route.hops = list
  }
  const positions = new Map<string, Point>()
  for (const slot of slots.values()) positions.set(slot.name, { x: slot.x, y: slot.y })
  return { routes, branchY, positions }
}

export function diagramLayout(
  statuses: Status[],
  transitions: Record<string, string[]>,
  startId: string,
  overrides?: ReadonlyMap<string, Point>,
): {
  positions: Map<string, Point>
  routes: Map<string, EdgeRoute>
  active: Status[]
  unused: Status[]
  gutterRight: number
  main: Status[]
  branches: Status[]
} {
  const { active, unused } = diagramOrder(statuses, transitions)
  const { main, branches } = mainstream(active, transitions)
  const { routes, branchY, positions } = buildRoutes(main, branches, transitions, overrides)
  const shelfY = branches.length ? branchY : ROW_Y + DIAGRAM_NODE_H + 52
  const shelfX = ORIGIN_X + Math.max(main.length, branches.length) * COLUMN_GAP + SHELF_GAP
  unused.forEach((status, index) => {
    const custom = overrides?.get(status.name)
    positions.set(
      status.name,
      custom ?? { x: shelfX + index * COLUMN_GAP, y: branches.length ? branchY : shelfY },
    )
  })
  const first = main[0] ?? active[0]
  const firstPos = first ? positions.get(first.name) : undefined
  const customStart = overrides?.get(startId)
  if (customStart) positions.set(startId, customStart)
  else if (firstPos) positions.set(startId, { x: Math.max(16, firstPos.x - 108), y: firstPos.y + 12 })
  else positions.set(startId, { x: 16, y: ROW_Y + 12 })
  let right = ORIGIN_X
  for (const point of positions.values()) right = Math.max(right, point.x + DIAGRAM_NODE_W)
  return { positions, routes, active, unused, gutterRight: right, main, branches }
}

/** Default coordinates: mainstream left to right, branches on the next row. */
export function sequencePositions(
  statuses: Status[],
  transitions: Record<string, string[]>,
  startId: string,
): Map<string, Point> {
  return diagramLayout(statuses, transitions, startId).positions
}

/** Routes when every named status is treated as mainstream (one row). */
export function planRoutes(
  transitions: Record<string, string[]>,
  order: string[],
): Map<string, EdgeRoute> {
  const fake = order.map((name, index) => ({
    id: index,
    name,
    category: 'doing' as const,
    color: '#000',
    sort_order: index,
  }))
  return buildRoutes(fake, [], transitions).routes
}

/** Extra space above the first row and below the last so outer arcs stay in view. */
export function routePadding(routes: Iterable<EdgeRoute>): { above: number; below: number } {
  let above = 28
  let below = 28
  for (const route of routes) {
    if (route.side === 'top') above = Math.max(above, route.offset + 24)
    if (route.side === 'bottom') below = Math.max(below, route.offset + 24)
  }
  return { above, below }
}
