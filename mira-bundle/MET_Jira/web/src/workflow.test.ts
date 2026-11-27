import { describe, expect, it } from 'vitest'
import type { Status } from './types'
import { allowedStatusIds, flowLayout, flowStatuses, isCompleted, isRejected } from './workflow'

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

describe('workflow helpers', () => {
  it('distinguishes successful completion from rejection', () => {
    expect(isCompleted(statuses[3])).toBe(true)
    expect(isCompleted(statuses[4])).toBe(false)
    expect(isRejected(statuses[4])).toBe(true)
  })

  it('keeps only statuses referenced by configured rules', () => {
    expect(flowStatuses({ 待办: ['研发中'], 研发中: ['已完成'] }, statuses).map((item) => item.name))
      .toEqual(['待办', '研发中', '已完成'])
  })

  it('places rework and rejection outside the happy path', () => {
    const rules = {
      待办: ['研发中', '已驳回'],
      研发中: ['测试中'],
      测试中: ['已完成', '研发中'],
    }
    const layout = flowLayout(statuses, rules)
    expect(layout.main.map((item) => item.name)).toEqual(['待办', '研发中', '测试中', '已完成'])
    expect(layout.branches.map((item) => item.name)).toEqual(['已驳回'])
  })

  it('allows only the adjacent statuses when no explicit rule exists', () => {
    const project = {
      is_admin: false,
      workflows: {},
      project: { settings: {} },
    }
    const ids = allowedStatusIds(project as never, { type: 'task', status: statuses[1] }, statuses)
    expect([...ids]).toEqual([2, 1, 3])
  })

  it('lets only testers move into testing, passed and failed', () => {
    const project = {
      is_admin: false,
      my_roles: ['electrical'],
      workflows: { requirement: { 研发中: ['测试中', '测试通过', '测试未通过'] } },
      project: { settings: {} },
      profile: { sections: [{ role: 'tester', issue_users: ['tester_id'] }] },
    }
    const issue = { type: 'requirement' as const, status: statuses[1] }
    const electrical = allowedStatusIds(project as never, issue, statuses, { id: 9, is_admin: false })
    expect(electrical.has(3)).toBe(false)
    const tester = allowedStatusIds(
      { ...project, my_roles: ['tester'] } as never,
      issue,
      statuses,
      { id: 4, is_admin: false },
    )
    expect(tester.has(3)).toBe(true)
    const assigned = allowedStatusIds(
      project as never,
      { ...issue, tester: { id: 9 } } as never,
      statuses,
      { id: 9, is_admin: false },
    )
    expect(assigned.has(3)).toBe(true)
  })
})
