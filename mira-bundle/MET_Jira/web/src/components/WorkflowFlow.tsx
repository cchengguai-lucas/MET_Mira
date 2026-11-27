import { Fragment, useEffect, useState } from 'react'
import { t } from '../i18n'
import type { Status } from '../types'
import { flowLayout } from '../workflow'

type NodeState = 'current' | 'passed' | 'next' | 'idle'

type TestFlow = {
  testStatus: string
  passStatus: string
  failStatus: string
}

/**
 * The project workflow as a clickable step diagram. Only transitions allowed from the current
 * status are enabled; the bottom bar appears only for test Pass/Fail verdicts or blocked states.
 * Pending clicks paint the target immediately so the highlight can animate without waiting on the API.
 */
export function WorkflowFlow({
  issueKey,
  statuses,
  rules,
  current,
  transitions,
  canMove,
  busy,
  onMove,
  testFlow,
}: {
  issueKey: string
  statuses: Status[]
  rules?: Record<string, string[]>
  current: Status
  transitions: Status[]
  canMove: boolean
  busy?: boolean
  onMove: (statusId: number) => void
  /** When current is the test status, label outcomes as Pass / Fail. */
  testFlow?: TestFlow
}) {
  const [pendingId, setPendingId] = useState<number | null>(null)
  const visual = (pendingId != null && statuses.find((s) => s.id === pendingId)) || current

  useEffect(() => {
    if (pendingId == null) return
    if (current.id === pendingId || !busy) setPendingId(null)
  }, [busy, current.id, pendingId])

  const { main, branches } = flowLayout(statuses, rules)
  const allowed = new Set(canMove && !pendingId ? transitions.map((s) => s.id) : [])
  const currentIndex = main.findIndex((s) => s.id === visual.id)
  const onBranch = currentIndex === -1
  const underTest = !!testFlow && current.name === testFlow.testStatus
  const moving = !!busy || pendingId != null

  const move = (status: Status) => {
    if (moving || !allowed.has(status.id)) return
    if (status.category === 'done' && !window.confirm(t('确定将 {key} 流转到「{status}」？', { key: issueKey, status: t(status.name) }))) return
    setPendingId(status.id)
    onMove(status.id)
  }

  const stateOf = (status: Status, index: number | null): NodeState => {
    if (status.id === visual.id) return 'current'
    if (allowed.has(status.id)) return 'next'
    if (index !== null && !onBranch && index < currentIndex) return 'passed'
    return 'idle'
  }

  const node = (status: Status, index: number | null) => {
    const state = stateOf(status, index)
    const title =
      state === 'current'
        ? t(status.name)
        : state === 'next'
          ? t('点击流转到「{name}」', { name: t(status.name) })
          : canMove
            ? t('当前不可直接流转到「{name}」', { name: t(status.name) })
            : t('你没有流转此问题的权限')
    return (
      <button
        key={status.id}
        type="button"
        className={`flow-node ${state}${state === 'current' && moving ? ' pending' : ''}`}
        style={{ ['--node-color' as string]: status.color }}
        title={title}
        aria-current={state === 'current' ? 'step' : undefined}
        aria-busy={state === 'current' && moving ? true : undefined}
        disabled={state !== 'next' || moving}
        onClick={() => move(status)}
      >
        <span className="flow-dot" aria-hidden>
          <span className="flow-check">✓</span>
        </span>
        <span className="ellipsis">{t(status.name)}</span>
      </button>
    )
  }

  const edgeClass = (index: number) => {
    if (main[index - 1].id === visual.id && allowed.has(main[index].id)) return 'active'
    if (main[index].id === visual.id && allowed.has(main[index - 1].id)) return 'back'
    return !onBranch && index <= currentIndex ? 'passed' : ''
  }

  const outcomeOf = (s: Status): 'pass' | 'fail' | null => {
    if (!underTest || !testFlow) return null
    if (s.name === testFlow.passStatus) return 'pass'
    if (s.name === testFlow.failStatus || s.name === '测试未通过') return 'fail'
    return null
  }

  const liveTargets = canMove ? statuses.filter((s) => transitions.some((x) => x.id === s.id)) : []
  const verdicts = underTest && canMove
    ? liveTargets
      .map((status) => ({ status, outcome: outcomeOf(status) }))
      .filter((item): item is { status: Status; outcome: 'pass' | 'fail' } => !!item.outcome)
      .sort((a, b) => (a.outcome === 'pass' ? -1 : 1) - (b.outcome === 'pass' ? -1 : 1))
    : []
  // Keep footer mounted whenever it has lasting content — never for transient "busy".
  const footerNeeded = !canMove || liveTargets.length === 0 || verdicts.length > 0

  return (
    <div className={`flow${moving ? ' is-moving' : ''}`}>
      <div className="flow-track">
        {main.map((status, index) => (
          <Fragment key={status.id}>
            {index > 0 && <span className={`flow-edge ${edgeClass(index)}`} />}
            {node(status, index)}
          </Fragment>
        ))}
        {branches.length > 0 && (
          <span className="flow-branches">
            <span className="flow-branch-label">{t('分支')}</span>
            {branches.map((status) => node(status, null))}
          </span>
        )}
      </div>
      <div className={`flow-actions${footerNeeded ? ' open' : ''}`} aria-hidden={!footerNeeded}>
        <div className="flow-actions-inner">
          {verdicts.length > 0 && <b className="small">{t('工作流')}</b>}
          {!canMove ? (
            <span className="small faint">{t('你没有流转此问题的权限')}</span>
          ) : liveTargets.length === 0 ? (
            <span className="small faint">{t('当前状态没有可流转的下一步')}</span>
          ) : (
            verdicts.map(({ status, outcome }) => (
              <button
                key={status.id}
                type="button"
                className={outcome === 'pass' ? 'btn sm primary' : 'btn sm danger'}
                disabled={moving}
                title={t(status.name)}
                onClick={() => move(status)}
              >
                {outcome === 'pass' ? t('通过') : t('不通过')}
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
