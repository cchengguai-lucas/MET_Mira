import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { t, tServer } from '../i18n'
import { typeName } from '../labels'
import type { IssueType, Status } from '../types'
import {
  adviceIsStale, applyWorkflowEdits, draftHasErrors, edgeId, flowSignature, mergeSuggestionEdits, omitStatuses, sameFlow,
  type FlowEdge,
} from '../workflowDraft'
import { lintFlow } from '../workflowLint'

interface Advice {
  id: string
  code: string
  level: 'required' | 'recommended' | 'optional'
  statuses: string[]
  add: FlowEdge[]
  remove: FlowEdge[]
  create?: NewStatus[]
  issue_count: number
}

interface NewStatus {
  name: string
  category: 'todo' | 'doing' | 'done'
  color: string
}

interface DraftResult {
  explanation: string
  question: string | null
  transitions: Record<string, string[]> | null
  new_statuses?: NewStatus[]
  diff: { add: FlowEdge[]; remove: FlowEdge[] }
  findings: { level: string }[]
  valid: boolean
  issue_count: number
  suggestions?: Advice[]
  notes?: string[]
}

/** Natural-language edits and automatic repairs for the workflow currently on the canvas. */
export function WorkflowAgentBar({
  projectKey,
  issueType,
  statuses,
  transitions,
  onApply,
}: {
  projectKey: string
  issueType: IssueType
  statuses: Status[]
  transitions: Record<string, string[]>
  onApply: (next: Record<string, string[]>, created: NewStatus[]) => void | Promise<void>
}) {
  const signature = flowSignature(transitions)
  const signatureRef = useRef(signature)
  const transitionsRef = useRef(transitions)
  signatureRef.current = signature
  transitionsRef.current = transitions
  const appliedSig = useRef<string | null>(null)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [checking, setChecking] = useState(true)
  const [error, setError] = useState('')
  const [placedNote, setPlacedNote] = useState(false)
  const [turns, setTurns] = useState<{ role: 'user' | 'assistant'; content: string }[]>([])
  const [proposal, setProposal] = useState<DraftResult | null>(null)
  const [draftSelection, setDraftSelection] = useState('')
  const [skipped, setSkipped] = useState<Set<string>>(new Set())
  const [skippedStatuses, setSkippedStatuses] = useState<Set<string>>(new Set())
  const [suggestions, setSuggestions] = useState<Advice[]>([])
  const [notes, setNotes] = useState<string[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [adviceSignature, setAdviceSignature] = useState('')
  const suggestionHold = useRef<Advice[]>([])
  const draftHold = useRef<DraftResult | null>(null)
  if (suggestions.length) suggestionHold.current = suggestions
  if (proposal) draftHold.current = proposal

  const pairs = useMemo(
    () => statuses.map((status) => ({ name: status.name, category: status.category })),
    [statuses],
  )
  const panel = proposal ?? draftHold.current
  const created = (panel?.new_statuses ?? []).filter((status) => !skippedStatuses.has(status.name))
  const materialized = useMemo(() => {
    if (!panel?.transitions) return null
    return omitStatuses(
      applyWorkflowEdits(panel.transitions, panel.diff.remove, skipped),
      skippedStatuses,
    )
  }, [panel, skipped, skippedStatuses])
  const reviewPairs = useMemo(
    () => [...pairs, ...created.map((status) => ({ name: status.name, category: status.category }))],
    [pairs, created],
  )
  const findings = useMemo(
    () => (materialized ? lintFlow(reviewPairs, materialized).filter((item) => item.level !== 'info') : []),
    [materialized, reviewPairs],
  )
  const errors = findings.filter((item) => item.level === 'error')
  const unchanged = materialized != null && sameFlow(materialized, transitions) && created.length === 0
  const orderOnly = panel != null
    && panel.transitions != null
    && panel.diff.add.length === 0
    && panel.diff.remove.length === 0
    && !sameFlow(panel.transitions, transitions)
  const selectionKey = [...selected].sort().join('\0')
  const draftStale = proposal != null && draftSelection !== selectionKey
  const stale = adviceSignature !== '' && adviceIsStale(adviceSignature, signature)
  const applicable = suggestions.filter(suggestionEnabled)
  const requiredIds = applicable.filter((item) => item.level === 'required').map((item) => item.id)
  const preview = mergeSuggestionEdits(transitions, suggestions, selected)
  const createsChange = suggestions.some((item) => selected.has(item.id) && (item.create?.length ?? 0) > 0)
  const previewChanged = !sameFlow(preview, transitions) || createsChange

  useEffect(() => {
    if (appliedSig.current === signature) setPlacedNote(true)
    else {
      setPlacedNote(false)
      appliedSig.current = null
      setProposal(null)
    }
    let cancelled = false
    const timer = window.setTimeout(() => {
      if (suggestionHold.current.length === 0) setChecking(true)
      void api.post<DraftResult>('/api/agent/workflow-draft', {
        project: projectKey,
        issue_type: issueType,
        transitions: transitionsRef.current,
        mode: 'optimize',
        messages: [],
      }).then((result) => {
        if (cancelled || signatureRef.current !== signature) return
        const next = result.suggestions ?? []
        setSuggestions(next)
        setNotes(result.notes ?? [])
        setAdviceSignature(signature)
        setSelected(new Set(
          next.filter((item) => item.level === 'required' && suggestionEnabled(item)).map((item) => item.id),
        ))
      }).catch((err: Error) => {
        if (!cancelled) setError(tServer(err.message))
      }).finally(() => {
        if (!cancelled) setChecking(false)
      })
    }, 350)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [issueType, projectKey, signature])

  const toggle = (id: string) => {
    setSkipped((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleSuggestion = (id: string) => {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const generate = async (ids: string[]) => {
    if (!ids.length || busy) return
    const sig = signatureRef.current
    setBusy(true)
    setError('')
    try {
      const result = await api.post<DraftResult>('/api/agent/workflow-draft', {
        project: projectKey,
        issue_type: issueType,
        transitions,
        mode: 'optimize',
        suggestion_ids: ids,
        messages: [],
      })
      if (signatureRef.current !== sig) return
      setSkipped(new Set())
      setSkippedStatuses(new Set())
      setDraftSelection([...ids].sort().join('\0'))
      setProposal(result)
    } catch (err) {
      setError(tServer((err as Error).message))
    } finally {
      setBusy(false)
    }
  }

  const send = async (text: string) => {
    const content = text.trim()
    if (!content || busy) return
    const history = [...turns, { role: 'user' as const, content }].slice(-8)
    setTurns(history)
    setInput('')
    setBusy(true)
    setError('')
    try {
      const result = await api.post<DraftResult>('/api/agent/workflow-draft', {
        project: projectKey,
        issue_type: issueType,
        transitions,
        mode: 'instruction',
        messages: history,
      })
      const reply = [
        result.explanation,
        result.question,
        result.transitions ? `transitions: ${JSON.stringify(result.transitions)}` : '',
      ].filter(Boolean).join('\n')
      const next: { role: 'user' | 'assistant'; content: string }[] = [
        ...history,
        { role: 'assistant', content: reply || 'no draft' },
      ]
      setTurns(next.slice(-8))
      setSkipped(new Set())
      setSkippedStatuses(new Set())
      setDraftSelection('')
      setProposal(result)
    } catch (err) {
      setError(tServer((err as Error).message))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="wf-agent">
      <div className="wf-advice">
        <div className="wf-advice-head">
          <span className="wf-agent-kicker">{t('当前检查')}</span>
          <b style={{ minWidth: 108, fontVariantNumeric: 'tabular-nums' }}>
            {t('检测到 {n} 项', { n: suggestions.length })}
          </b>
          <span className="tiny faint" style={{ minWidth: 108, visibility: checking ? 'visible' : 'hidden' }}>
            {t('正在检查当前流程')}
          </span>
          <span className="spacer" />
          <button
            type="button"
            className="btn sm"
            style={{ minWidth: 112 }}
            disabled={busy || stale || requiredIds.length === 0}
            onClick={() => {
              setSelected(new Set(requiredIds))
              void generate(requiredIds)
            }}
          >
            {t('修复全部错误')}
          </button>
          <button
            type="button"
            className="btn primary sm"
            style={{ minWidth: 124 }}
            disabled={busy || stale || selected.size === 0 || !previewChanged}
            onClick={() => void generate([...selected])}
          >
            {busy ? t('生成中…') : t('生成优化草稿')}
          </button>
        </div>
        <div style={{
          minHeight: (placedNote || stale || (suggestions.length === 0 && notes.length === 0)) ? 18 : 0,
          transition: 'min-height 0.32s ease',
        }}>
          {placedNote ? (
            <p className="tiny ok-text" style={{ margin: 0 }}>{t('已放到画布，记得保存工作流')}</p>
          ) : stale ? (
            <p className="tiny faint" style={{ margin: 0 }}>{t('流程已变化，正在重新检查')}</p>
          ) : suggestions.length === 0 && notes.length === 0 ? (
            <p className="tiny faint" style={{ margin: 0 }}>
              {checking ? t('正在检查当前流程') : t('当前流程没有需要调整的地方')}
            </p>
          ) : null}
        </div>
        <div style={{
          display: 'grid',
          gridTemplateRows: notes.length || suggestions.length ? '1fr' : '0fr',
          transition: 'grid-template-rows 0.32s ease',
        }}>
          <div style={{ overflow: 'hidden', minHeight: 0 }}>
          {notes.length > 0 && (
            <ul className="wf-notes">
              {notes.map((note) => <li key={note} className="tiny">{t(note)}</li>)}
            </ul>
          )}
          {(suggestions.length ? suggestions : suggestionHold.current).length > 0 && (
            <ul className="wf-advice-list">
              {(suggestions.length ? suggestions : suggestionHold.current).map((item) => {
                const copy = adviceText(item)
                const enabled = suggestionEnabled(item)
                return (
                  <li key={item.id} className={`wf-advice-item ${item.level}`}>
                    <label>
                      <input
                        type="checkbox"
                        disabled={!enabled || busy}
                        checked={enabled && selected.has(item.id)}
                        onChange={() => toggleSuggestion(item.id)}
                      />
                      <span className={`wf-advice-level ${item.level}`}>{levelLabel(item.level)}</span>
                      <span className="wf-advice-title">{copy.title}</span>
                    </label>
                    <span className="tiny faint wf-advice-reason">
                      {copy.reason}
                      {item.issue_count > 0 && item.code !== 'unused_status' && (
                        <> · {t('有 {n} 个此类型问题在相关状态', { n: item.issue_count })}</>
                      )}
                    </span>
                  </li>
                )
              })}
            </ul>
          )}
          </div>
        </div>
      </div>

      <div className="wf-agent-bar">
        <span className="ai-orb sm" aria-hidden />
        <label className="wf-agent-field">
          <span className="wf-agent-kicker">{t('AI 改流程')}</span>
          <input
            className="wf-agent-input"
            value={input}
            disabled={busy}
            placeholder={t('例如：已完成可以重新打开到待评估')}
            aria-label={t('用一句话修改{type}流程', { type: typeName(issueType) })}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              event.stopPropagation()
              if (event.key === 'Enter') {
                event.preventDefault()
                void send(input)
              }
            }}
          />
        </label>
        <button
          type="button"
          className="btn primary sm"
          style={{ minWidth: 96 }}
          disabled={busy || !input.trim()}
          onClick={() => void send(input)}
        >
          {busy ? t('生成中…') : t('生成草稿')}
        </button>
      </div>

      {error && <p className="small err-text">{error}</p>}

      <div style={{
        display: 'grid',
        gridTemplateRows: proposal ? '1fr' : '0fr',
        transition: 'grid-template-rows 0.32s ease',
      }}>
        <div style={{ overflow: 'hidden', minHeight: 0, pointerEvents: proposal ? 'auto' : 'none' }}>
        {panel && (
        <div className="wf-agent-draft">
          <div className="wf-agent-draft-head">
            <div>
              <span className="wf-agent-kicker">{t('建议结果')}</span>
              <p className="small">
                {panel.explanation || panel.question || t('按所选建议生成的草稿')}
              </p>
            </div>
            <button type="button" className="btn ghost sm" disabled={busy} onClick={() => setProposal(null)}>
              {t('放弃')}
            </button>
          </div>
          {panel.question && panel.explanation && (
            <p className="small">{panel.question}</p>
          )}
          {draftStale && <p className="tiny faint">{t('所选建议已变化，请重新生成草稿')}</p>}
          {panel.issue_count > 0 && (
            <p className="tiny faint">
              {t('此类型有 {n} 个问题，不会自动迁移它们的状态', { n: panel.issue_count })}
            </p>
          )}
          {orderOnly && created.length === 0 && <p className="tiny faint">{t('没有增删连线，只调整了先后顺序')}</p>}
          {panel.new_statuses && panel.new_statuses.length > 0 && (
            <div className="wf-agent-edges">
              <span className="tiny faint">{t('将新增状态')}</span>
              {panel.new_statuses.map((status) => {
                const off = skippedStatuses.has(status.name)
                return (
                  <button
                    key={status.name}
                    type="button"
                    className={`wf-agent-edge add${off ? ' off' : ''}`}
                    aria-pressed={!off}
                    title={off ? t('点击恢复这条改动') : t('点击取消这条改动')}
                    onClick={() => setSkippedStatuses((current) => {
                      const next = new Set(current)
                      if (next.has(status.name)) next.delete(status.name)
                      else next.add(status.name)
                      return next
                    })}
                  >
                    {status.name} · {categoryLabel(status.category)}
                  </button>
                )
              })}
            </div>
          )}
          {panel.diff.add.length > 0 && (
            <EdgeRow label={t('将增加')} kind="add" edges={panel.diff.add} skipped={skipped} onToggle={toggle} />
          )}
          {panel.diff.remove.length > 0 && (
            <EdgeRow label={t('将去掉')} kind="remove" edges={panel.diff.remove} skipped={skipped} onToggle={toggle} />
          )}
          {findings.length > 0 && (
            <ul className="wf-finding-list compact">
              {findings.map((finding, index) => (
                <li key={`${finding.message}-${index}`} className={`wf-finding ${finding.level}`}>
                  <div className="small">{finding.message}</div>
                </li>
              ))}
            </ul>
          )}
          <div className="row wrap" style={{ gap: 8, alignItems: 'center' }}>
            <button
              type="button"
              className="btn primary sm"
              disabled={
                busy || !materialized || unchanged || draftStale
                || draftHasErrors(findings)
                || (panel.transitions != null && !panel.valid && created.length === 0)
              }
              onClick={() => {
                if (!materialized) return
                const next = materialized
                const adding = created
                setBusy(true)
                setError('')
                void Promise.resolve(onApply(next, adding))
                  .then(() => {
                    appliedSig.current = flowSignature(next)
                    setPlacedNote(true)
                    setProposal(null)
                  })
                  .catch((failure: unknown) => {
                    setError(failure instanceof Error ? failure.message : t('请求失败'))
                  })
                  .finally(() => setBusy(false))
              }}
            >
              {unchanged ? t('没有需要应用的改动') : t('应用到画布')}
            </button>
            <span className="tiny faint">
              {errors.length > 0
                ? t('草稿有错误，还不能应用到画布')
                : created.length > 0
                  ? t('新状态会立即加入项目，流转仍需保存工作流')
                  : t('放到画布后可以继续拖线，点保存工作流后才生效')}
            </span>
          </div>
        </div>
        )}
        </div>
      </div>
    </section>
  )
}

function categoryLabel(category: NewStatus['category']): string {
  if (category === 'doing') return t('进行中')
  if (category === 'done') return t('已完成')
  return t('待办')
}

function suggestionEnabled(item: Advice): boolean {
  return item.add.length > 0 || item.remove.length > 0 || (item.create?.length ?? 0) > 0
}

function levelLabel(level: Advice['level']): string {
  if (level === 'required') return t('必须修复')
  if (level === 'recommended') return t('推荐优化')
  return t('可选策略')
}

function adviceText(item: Advice): { title: string; reason: string } {
  const status = item.statuses[0] ? t(item.statuses[0]) : ''
  const names = item.statuses.map((name) => t(name)).join('、')
  const to = item.add[0] ? t(item.add[0].to) : ''
  const from = item.add[0] ? t(item.add[0].from) : ''
  switch (item.code) {
    case 'empty':
      return { title: t('流程是空的'), reason: t('按模板和现有状态补一条到完成的主线') }
    case 'unknown_status':
      return { title: t('去掉不存在的状态'), reason: t('这些连线引用了已经删除的状态') }
    case 'restore_status':
      return {
        title: t('补回被删掉但连线还在用的状态'),
        reason: t('这些状态属于模板，删除后连线还指向它们'),
      }
    case 'add_completion':
      return { title: t('补一个完成状态'), reason: t('当前流程没有可以结束的状态') }
    case 'self_loop':
      return { title: t('去掉流转到自身'), reason: names }
    case 'unreachable':
      return {
        title: t('「{status}」从起点到不了', { status }),
        reason: t('从「{from}」连到「{status}」', { from, status }),
      }
    case 'dead_end':
      return { title: t('「{status}」之后无法完成', { status }), reason: t('补一条到「{to}」', { to }) }
    case 'no_completion':
      return { title: t('到不了完成状态'), reason: t('从「{from}」连到「{to}」', { from, to }) }
    case 'main_not_done':
      return { title: t('主线没有走到完成'), reason: t('把完成状态放到主线的下一步') }
    case 'no_reopen':
      return { title: t('完成后不能重新打开'), reason: t('可选：从完成状态回到「{to}」', { to }) }
    case 'reopen_stuck':
      return { title: t('完成状态只能去其他完成状态'), reason: t('可选：增加回到「{to}」', { to }) }
    case 'no_step_back':
      return { title: t('进行中的步骤不能退回'), reason: t('可选：允许退回上一步') }
    case 'bring_forward':
      return { title: t('把更早的状态接到起点前'), reason: names }
    case 'unused_status':
      return {
        title: item.statuses.length > 1
          ? t('还有 {n} 个状态没接到这条流程', { n: item.statuses.length })
          : t('「{status}」还没接到这条流程', { status }),
        reason: item.issue_count
          ? t('有 {n} 个此类型问题在相关状态', { n: item.issue_count })
          : t('不会新建或改名状态'),
      }
    case 'template_edges':
      return { title: t('模板里还有未使用的连线'), reason: t('可按模板补齐，不会新建状态') }
    default:
      return { title: names || item.code, reason: t('不会新建或改名状态') }
  }
}

function EdgeRow({
  label,
  kind,
  edges,
  skipped,
  onToggle,
}: {
  label: string
  kind: 'add' | 'remove'
  edges: FlowEdge[]
  skipped: Set<string>
  onToggle: (id: string) => void
}) {
  return (
    <div className="wf-agent-edges">
      <span className="tiny faint">{label}</span>
      {edges.map((edge) => {
        const id = edgeId(kind, edge)
        const off = skipped.has(id)
        return (
          <button
            key={id}
            type="button"
            className={`wf-agent-edge ${kind}${off ? ' off' : ''}`}
            aria-pressed={!off}
            title={off ? t('点击恢复这条改动') : t('点击取消这条改动')}
            onClick={() => onToggle(id)}
          >
            {t(edge.from)} → {t(edge.to)}
          </button>
        )
      })}
    </div>
  )
}
