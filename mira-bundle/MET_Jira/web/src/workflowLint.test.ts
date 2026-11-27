import { describe, expect, it } from 'vitest'
import { findingsForStatus, lintFlow, namesInMessage, statusesNeedingTransition } from './workflowLint'

const statuses = [
  { name: '待办', category: 'todo' },
  { name: '研发中', category: 'doing' },
  { name: '已完成', category: 'done' },
  { name: '已驳回', category: 'done' },
]

describe('workflow lint', () => {
  it('reports unknown and unreachable statuses', () => {
    const findings = lintFlow(statuses, {
      待办: ['研发中', '不存在'],
      研发中: ['已完成'],
      已驳回: ['已完成'],
    })
    expect(findings).toEqual(expect.arrayContaining([
      expect.objectContaining({ level: 'error', code: 'unknown_status', statuses: ['不存在'] }),
      expect.objectContaining({ level: 'error', code: 'unreachable', statuses: ['已驳回'] }),
    ]))
  })

  it('detects an empty workflow', () => {
    expect(lintFlow(statuses, {})).toContainEqual({
      level: 'error',
      code: 'empty',
      message: '工作流为空',
      statuses: [],
    })
  })

  it('extracts status names from audit messages', () => {
    expect(namesInMessage('从初始状态无法到达：研发中、已完成')).toEqual(['研发中', '已完成'])
    expect(namesInMessage('进入「研发中」后是死胡同')).toEqual(['研发中'])
  })

  it('marks isolated statuses and produces a local warning', () => {
    const rules = { 待办: ['研发中'], 研发中: ['已完成'], 已驳回: [] }
    expect(statusesNeedingTransition(statuses, rules)).toEqual(['已驳回'])
    expect(findingsForStatus([], '已驳回', rules)).toContainEqual({
      level: 'warning',
      code: 'needs_transition',
      message: '这一状态需要转换。',
      statuses: ['已驳回'],
    })
  })
})
