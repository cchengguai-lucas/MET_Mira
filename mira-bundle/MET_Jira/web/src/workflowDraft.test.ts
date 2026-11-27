import { describe, expect, it } from 'vitest'
import {
  adviceIsStale, applyWorkflowEdits, draftHasErrors, edgeId, flowSignature, mergeSuggestionEdits, omitStatuses, sameFlow,
} from './workflowDraft'

const proposed = {
  待评估: ['研发中'],
  研发中: ['已完成', '待评估'],
  已完成: ['重新打开'],
}

describe('applyWorkflowEdits', () => {
  it('keeps the proposal when nothing is skipped', () => {
    expect(applyWorkflowEdits(proposed, [{ from: '研发中', to: '测试中' }], new Set())).toEqual(proposed)
  })

  it('drops an unchecked addition and restores an unchecked removal', () => {
    const skipped = new Set([
      edgeId('add', { from: '已完成', to: '重新打开' }),
      edgeId('remove', { from: '研发中', to: '测试中' }),
    ])
    expect(applyWorkflowEdits(proposed, [{ from: '研发中', to: '测试中' }], skipped)).toEqual({
      待评估: ['研发中'],
      研发中: ['已完成', '待评估', '测试中'],
    })
  })

  it('merges only the selected repairs and can move a target to the front', () => {
    const current = { 待办: ['研发中'], 研发中: ['待办'] }
    const suggestions = [
      { id: 'finish', add: [{ from: '研发中', to: '已完成', position: 'first' as const }], remove: [] },
      { id: 'reject', add: [{ from: '待办', to: '已驳回' }], remove: [{ from: '研发中', to: '待办' }] },
      { id: 'skip', add: [{ from: '待办', to: '测试中' }], remove: [] },
    ]
    expect(mergeSuggestionEdits(current, suggestions, new Set(['finish', 'reject']))).toEqual({
      待办: ['研发中', '已驳回'],
      研发中: ['已完成'],
    })
  })

  it('treats a changed canvas as stale and blocks an invalid draft', () => {
    const current = { 待办: ['研发中'] }
    expect(adviceIsStale(flowSignature(current), flowSignature({ 待办: ['已完成'] }))).toBe(true)
    expect(adviceIsStale(flowSignature(current), flowSignature({ 待办: ['研发中'] }))).toBe(false)
    expect(draftHasErrors([{ level: 'warning' }, { level: 'error' }])).toBe(true)
    expect(draftHasErrors([{ level: 'warning' }])).toBe(false)
  })

  it('drops a skipped new status and every transition that uses it', () => {
    expect(omitStatuses({
      待办: ['联调', '研发中'],
      联调: ['已完成'],
      研发中: ['已完成'],
    }, new Set(['联调']))).toEqual({
      待办: ['研发中'],
      研发中: ['已完成'],
    })
  })

  it('compares transition order', () => {
    expect(sameFlow({ 待评估: ['研发中', '已驳回'] }, { 待评估: ['已驳回', '研发中'] })).toBe(false)
    expect(sameFlow({ 待评估: ['研发中'] }, { 待评估: ['研发中'] })).toBe(true)
  })
})
