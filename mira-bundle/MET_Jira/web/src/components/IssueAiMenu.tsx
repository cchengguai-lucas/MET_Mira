import { useQuery } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { api, streamPost } from '../api'
import { useApp } from '../context'
import { t, tServer } from '../i18n'
import { priorityName } from '../labels'
import { useInvalidateIssues } from '../queries'
import type { IssueDetail, Priority } from '../types'
import { leaveAnimation } from './leave'
import { stripMdImages } from './markdownImages'
import type { IssueType } from '../types'
import { Dropdown, Spinner, TypeIcon, useEscape } from './ui'

/** Drops a ```markdown fence the model sometimes wraps its whole answer in. */
const unfence = (text: string) =>
  text.trim().replace(/^```(?:markdown|md)?\s*\n([\s\S]*?)\n?```$/i, '$1').trim()

interface SuggestItem {
  title: string
  description: string
  priority: Priority
  type?: string
  selected: boolean
}

interface SimilarHit {
  key: string
  title: string
  type?: string
  status: string | null
  relation: string
  reason: string
  score: number
}

/**
 * Unified ✦ menu on the issue detail: polish description, summarize, suggest
 * subtasks, and link similar issues.
 */
export function IssueAiMenu({
  issue,
  onSummary,
  summarizing,
  onPolished,
  canEditDescription,
  canCreateChildren,
}: {
  issue: IssueDetail
  onSummary: () => void
  summarizing: boolean
  onPolished: (next: string, previous: string) => void
  canEditDescription: boolean
  canCreateChildren: boolean
}) {
  const { toast, openIssue } = useApp()
  const invalidate = useInvalidateIssues()
  const [busy, setBusy] = useState<'polish' | 'suggest' | 'similar' | null>(null)
  const [suggest, setSuggest] = useState<SuggestItem[] | null>(null)
  const [similar, setSimilar] = useState<SimilarHit[] | null>(null)
  const abort = useRef<AbortController | null>(null)

  const ai = useQuery({
    queryKey: ['agent-status'],
    queryFn: () => api.get<{ enabled: boolean; reachable: boolean; error?: string }>('/api/agent/status'),
    staleTime: 300_000,
  })
  const aiReady = !!ai.data?.enabled && ai.data.reachable
  const aiOffReason = !ai.data?.enabled
    ? t('AI 未启用')
    : ai.data.error || t('AI 不可用')

  const runPolish = async () => {
    if (!canEditDescription) {
      toast(t('无权限编辑描述'), 'error')
      return
    }
    const original = issue.description || ''
    if (!stripMdImages(original).trim()) {
      toast(t('请先填写描述文字后再美化'), 'error')
      return
    }
    const controller = new AbortController()
    abort.current = controller
    setBusy('polish')
    let out = ''
    let failure: string | null = null
    try {
      await streamPost(
        '/api/agent/polish',
        { text: original, title: issue.title },
        (ev) => {
          if (ev.type === 'delta') out += ev.text ?? ''
          if (ev.type === 'error') failure = ev.message ? tServer(ev.message) : t('AI 不可用')
        },
        controller.signal,
      )
      const result = unfence(out)
      if (!failure && !result) failure = t('AI 未返回内容')
      if (failure) throw new Error(failure)
      onPolished(result, original)
      toast(t('已用 AI 美化并保存'), 'success')
    } catch (err) {
      if (!controller.signal.aborted) toast(t('AI 美化失败，未保存：{error}', { error: (err as Error).message }), 'error')
    } finally {
      abort.current = null
      setBusy(null)
    }
  }

  const runSuggest = async () => {
    if (issue.type === 'subtask') {
      toast(t('子任务不能再拆分子任务'), 'error')
      return
    }
    setBusy('suggest')
    try {
      const res = await api.post<{ subtasks: Omit<SuggestItem, 'selected'>[]; child_kind: string }>(
        `/api/agent/suggest-subtasks/${issue.key}`,
        {},
      )
      setSuggest(res.subtasks.map((s) => ({ ...s, selected: true })))
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(null)
    }
  }

  const commitSuggest = async () => {
    if (!suggest) return
    const picked = suggest.filter((s) => s.selected && s.title.trim())
    if (!picked.length) {
      toast(t('请至少保留一条有标题的草稿'), 'error')
      return
    }
    setBusy('suggest')
    try {
      for (const item of picked) {
        if (issue.type === 'epic') {
          await api.post('/api/issues', {
            project: issue.project_key,
            type: item.type === 'subtask' ? 'task' : (item.type || 'task'),
            title: item.title.trim(),
            description: item.description,
            priority: item.priority,
            epic_id: issue.id,
            due_date: issue.due_date || new Date().toISOString().slice(0, 10),
          })
        } else {
          await api.post('/api/issues', {
            project: issue.project_key,
            parent_id: issue.id,
            title: item.title.trim(),
            description: item.description,
            priority: item.priority,
          })
        }
      }
      toast(t('已创建 {n} 个子任务', { n: picked.length }), 'success')
      setSuggest(null)
      invalidate()
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(null)
    }
  }

  const runSimilar = async () => {
    setBusy('similar')
    try {
      const res = await api.post<{ related: SimilarHit[] }>(`/api/agent/suggest-related/${issue.key}`, {})
      setSimilar(res.related ?? [])
      if (!(res.related ?? []).length) toast(t('未找到真正相关的问题'), 'info')
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(null)
    }
  }

  const linkSimilar = async (key: string) => {
    try {
      await api.post(`/api/issues/${issue.key}/links`, { target_key: key, link_type: 'relates' })
      toast(t('已关联 {key}', { key }), 'success')
      setSimilar((list) => list?.filter((x) => x.key !== key) ?? null)
      invalidate()
    } catch (err) {
      toast((err as Error).message, 'error')
    }
  }

  const working = busy !== null || summarizing
  const off = !aiReady

  return (
    <>
      <Dropdown
        align="left"
        trigger={
          <button
            type="button"
            className="btn sm ai issue-ai-menu-btn"
            disabled={working}
            title={off ? aiOffReason : t('改进工作项')}
          >
            ✦ {busy === 'polish' ? t('美化中…') : busy === 'suggest' ? t('生成中…') : busy === 'similar' ? t('查找中…') : summarizing ? t('总结中…') : t('改进工作项')}
          </button>
        }
        options={[
          {
            value: 'polish',
            label: t('改进描述'),
            disabled: off || !canEditDescription,
            title: off ? aiOffReason : !canEditDescription ? t('无权限编辑描述') : undefined,
          },
          {
            value: 'summary',
            label: t('AI 总结'),
            disabled: off || summarizing,
            title: off ? aiOffReason : undefined,
          },
          {
            value: 'suggest',
            label: t('建议子任务'),
            disabled: off || !canCreateChildren || issue.type === 'subtask',
            title: off
              ? aiOffReason
              : issue.type === 'subtask'
                ? t('子任务不能再拆分子任务')
                : !canCreateChildren
                  ? t('无权限创建子任务')
                  : undefined,
          },
          {
            value: 'similar',
            label: t('关联相似问题'),
            disabled: off,
            title: off ? aiOffReason : undefined,
          },
        ]}
        onSelect={(v) => {
          if (v === 'polish') void runPolish()
          if (v === 'summary') onSummary()
          if (v === 'suggest') void runSuggest()
          if (v === 'similar') void runSimilar()
        }}
      />

      {suggest && (
        <SuggestModal
          items={suggest}
          setItems={setSuggest}
          busy={busy === 'suggest'}
          onClose={() => setSuggest(null)}
          onConfirm={() => void commitSuggest()}
          isEpic={issue.type === 'epic'}
        />
      )}

      {similar && similar.length > 0 && (
        <SimilarModal
          items={similar}
          onClose={() => setSimilar(null)}
          onLink={(key) => void linkSimilar(key)}
          onOpen={(key) => {
            setSimilar(null)
            openIssue(key)
          }}
        />
      )}
    </>
  )
}

function SuggestModal({
  items,
  setItems,
  busy,
  onClose,
  onConfirm,
  isEpic,
}: {
  items: SuggestItem[]
  setItems: (items: SuggestItem[]) => void
  busy: boolean
  onClose: () => void
  onConfirm: () => void
  isEpic: boolean
}) {
  useEscape(onClose)
  return (
    <div className="overlay" ref={leaveAnimation} onClick={onClose}>
      <div className="modal" ref={leaveAnimation} onClick={(e) => e.stopPropagation()} style={{ width: 'min(560px, 100%)' }}>
        <div className="modal-header">
          <h2>{isEpic ? t('建议子项') : t('建议子任务')}</h2>
          <span className="spacer" />
          <button type="button" className="btn ghost icon sm" onClick={onClose} aria-label={t('关闭')}>
            ✕
          </button>
        </div>
        <div className="modal-body">
          <p className="small muted" style={{ marginTop: 0 }}>
            {t('勾选要创建的条目，可编辑标题后再确认。')}
          </p>
          <div className="issue-ai-suggest-list">
            {items.map((item, i) => (
              <label key={i} className="issue-ai-suggest-item">
                <input
                  type="checkbox"
                  checked={item.selected}
                  disabled={busy}
                  onChange={(e) => {
                    const next = [...items]
                    next[i] = { ...item, selected: e.target.checked }
                    setItems(next)
                  }}
                />
                <div className="col" style={{ flex: 1, gap: 4, minWidth: 0 }}>
                  <input
                    className="input sm"
                    value={item.title}
                    disabled={busy}
                    onChange={(e) => {
                      const next = [...items]
                      next[i] = { ...item, title: e.target.value }
                      setItems(next)
                    }}
                  />
                  {item.description && <div className="tiny faint">{item.description.slice(0, 120)}</div>}
                  <div className="tiny faint">{priorityName(item.priority)}</div>
                </div>
              </label>
            ))}
          </div>
        </div>
        <div className="modal-footer">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            {t('取消')}
          </button>
          <button type="button" className="btn primary" onClick={onConfirm} disabled={busy}>
            {busy ? <Spinner /> : null}
            {busy ? t('创建中…') : t('创建选中项')}
          </button>
        </div>
      </div>
    </div>
  )
}

function SimilarModal({
  items,
  onClose,
  onLink,
  onOpen,
}: {
  items: SimilarHit[]
  onClose: () => void
  onLink: (key: string) => void
  onOpen: (key: string) => void
}) {
  useEscape(onClose)
  return (
    <div className="overlay" ref={leaveAnimation} onClick={onClose}>
      <div className="modal" ref={leaveAnimation} onClick={(e) => e.stopPropagation()} style={{ width: 'min(640px, 100%)' }}>
        <div className="modal-header">
          <h2>{t('相关问题')}</h2>
          <span className="spacer" />
          <button type="button" className="btn ghost icon sm" onClick={onClose} aria-label={t('关闭')}>
            ✕
          </button>
        </div>
        <div className="modal-body">
          <p className="small muted" style={{ marginTop: 0 }}>
            {t('AI 已通读标题与描述后综合判断；仅列出实质相关或可能重复的项，同机台但不同事情不会出现。')}
          </p>
          <div className="issue-ai-similar">
            {items.map((hit) => (
              <div key={hit.key} className="issue-ai-similar-card">
                <div className="issue-ai-similar-row">
                  <TypeIcon type={(hit.type as IssueType) || 'task'} />
                  <button type="button" className="issue-key" style={{ cursor: 'pointer', border: 'none', background: 'none', padding: 0 }} onClick={() => onOpen(hit.key)}>
                    {hit.key}
                  </button>
                  <span className="ellipsis" style={{ flex: 1 }} title={hit.title}>
                    {hit.title}
                  </span>
                  {hit.status && <span className="tiny muted">{t(hit.status)}</span>}
                  <span className="tiny faint">{hit.relation === 'duplicate' ? t('可能重复') : t('相关')}</span>
                  <button type="button" className="btn sm primary" onClick={() => onLink(hit.key)}>
                    {t('关联')}
                  </button>
                </div>
                {hit.reason && <div className="tiny faint issue-ai-similar-reason">{hit.reason}</div>}
              </div>
            ))}
          </div>
        </div>
        <div className="modal-footer">
          <button type="button" className="btn" onClick={onClose}>
            {t('关闭')}
          </button>
        </div>
      </div>
    </div>
  )
}
