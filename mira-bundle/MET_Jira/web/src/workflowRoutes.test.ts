import { describe, expect, it } from 'vitest'
import type { Status } from './types'
import {
  COLUMN_GAP,
  diagramLayout,
  diagramOrder,
  planRoutes,
  routePolyline,
  sequencePositions,
  wirePath,
} from './workflowRoutes'

const status = (id: number, name: string, category: Status['category']): Status => ({
  id,
  name,
  category,
  color: '#000',
  sort_order: id,
})

const statuses = [
  status(1, '待办', 'todo'),
  status(2, '研发中', 'doing'),
  status(3, '测试中', 'doing'),
  status(4, '已完成', 'done'),
  status(5, '已驳回', 'done'),
]

const rules = {
  待办: ['研发中', '已驳回'],
  研发中: ['测试中', '待办'],
  测试中: ['已完成', '研发中'],
  已完成: ['待办'],
}

describe('workflow diagram routes', () => {
  it('keeps the mainstream on one row and parks branches below', () => {
    const laid = diagramLayout(statuses, rules, '__start__')
    expect(laid.main.map((item) => item.name)).toEqual(['待办', '研发中', '测试中', '已完成'])
    expect(laid.branches.map((item) => item.name)).toEqual(['已驳回'])
    const map = laid.positions
    expect(map.get('研发中')!.x - map.get('待办')!.x).toBe(COLUMN_GAP)
    expect(map.get('测试中')!.y).toBe(map.get('待办')!.y)
    expect(map.get('已完成')!.y).toBe(map.get('待办')!.y)
    expect(map.get('已完成')!.x).toBeGreaterThan(map.get('待办')!.x)
    expect(map.get('已驳回')!.y).toBeGreaterThan(map.get('待办')!.y)
    expect(map.get('__start__')!.x).toBeLessThan(map.get('待办')!.x)
  })

  it('parks a status that the flow does not use on the branch row', () => {
    const extra = [...statuses, status(6, '搁置', 'todo')]
    const { active, unused } = diagramOrder(extra, rules)
    expect(active.map((item) => item.name)).toEqual(['待办', '研发中', '测试中', '已完成', '已驳回'])
    expect(unused.map((item) => item.name)).toEqual(['搁置'])
    const map = sequencePositions(extra, rules, '__start__')
    expect(map.get('搁置')!.y).toBe(map.get('已驳回')!.y)
    expect(map.get('搁置')!.x).toBeGreaterThan(map.get('已驳回')!.x)
  })

  it('keeps the next step straight and stacks overlapping jumps on separate lanes', () => {
    const order = statuses.map((item) => item.name)
    const routes = planRoutes(rules, order)
    const step = routes.get('待办\0研发中')!
    expect(step.spine).toBe(true)
    expect(step.dashed).toBe(false)
    expect(step.sourceHandle).toBe('right-s')
    expect(step.targetHandle).toBe('left-t')

    const skip = routes.get('待办\0已驳回')!
    expect(skip.spine).toBe(false)
    expect(skip.dashed).toBe(false)
    expect(skip.side).toBe('top')

    const back = routes.get('研发中\0待办')!
    const longBack = routes.get('已完成\0待办')!
    expect(back.dashed).toBe(true)
    expect(longBack.dashed).toBe(true)
    expect(longBack.offset).toBeGreaterThan(back.offset)
    expect(back.side).toBe('bottom')
  })

  it('draws a hop where a return wire crosses another', () => {
    const routes = planRoutes(rules, statuses.map((item) => item.name))
    const hops = [...routes.values()].flatMap((route) => route.hops)
    expect(hops.length).toBeGreaterThan(0)
    const back = routes.get('已完成\0待办')!
    const path = wirePath(
      routePolyline({ x: 0, y: 0 }, { x: 400, y: 0 }, back),
      back.hops,
    )
    if (back.hops.length) expect(path).toContain('A ')
  })

  it('does not wrap a long mainstream onto a second row', () => {
    const many = [...statuses, status(6, '归档', 'done')]
    const rulesWithShelf = { ...rules, 已驳回: ['归档'] }
    const laid = diagramLayout(many, rulesWithShelf, '__start__')
    expect(laid.main.map((item) => item.name)).toEqual(['待办', '研发中', '测试中', '已完成'])
    expect(laid.branches.map((item) => item.name)).toEqual(['已驳回', '归档'])
    expect(laid.positions.get('已完成')!.y).toBe(laid.positions.get('待办')!.y)
    expect(laid.positions.get('归档')!.y).toBeGreaterThan(laid.positions.get('待办')!.y)
    expect(laid.routes.get('测试中\0已完成')?.side).toBe('straight')
  })

  it('keeps channels separated and updates hops after a node is dragged', () => {
    const base = diagramLayout(statuses, rules, '__start__')
    const moved = new Map(base.positions)
    const review = moved.get('研发中')!
    moved.set('研发中', { x: review.x + 24, y: review.y + 12 })
    const dragged = diagramLayout(statuses, rules, '__start__', moved)
    for (const route of dragged.routes.values()) {
      expect(['straight', 'top', 'bottom', 'gap']).toContain(route.side)
    }
    const hops = [...dragged.routes.values()].flatMap((route) => route.hops)
    expect(hops.length).toBeGreaterThan(0)
  })
})
