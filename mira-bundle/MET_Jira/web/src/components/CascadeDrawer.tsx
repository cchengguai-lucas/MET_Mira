import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import './CascadeDrawer.css'
import { useApp } from '../context'
import { getLang, t } from '../i18n'
import { confidenceLabel, priorityName, scheduleTagLabel, signedDaysLabel } from '../labels'
import type { Priority } from '../types'
import { Empty, Modal, PriorityIcon, Spinner } from './ui'

export interface CascadeSuggestion {
  key: string
  title: string
  current_due: string | null
  suggested_due: string | null
  current_priority: string
  suggested_priority: string | null
  reason: string | null
  reason_brief?: string | null
  depth: number
  baseline_due: string | null
  forecast_due: string | null
  variance_days: number
  schedule_urgency: 'critical' | 'high' | 'watch' | 'normal'
  target_impact_days: number
  confidence: 'high' | 'medium' | 'low'
  expected_lock_version: number
  risk_lock_version?: number
  risk_status?: string
  constraining_predecessors: string[]
  delay_sources: string[]
  evidence: string[]
  impacted_gates: string[]
}

interface CascadePayload {
  analysis_token: string
  baseline_version: number | null
  baseline_mode: 'locked' | 'shadow'
  target_date: string | null
  forecast_target_date: string | null
  target_impact_days: number
  confidence: 'high' | 'medium' | 'low'
  impacted_gates: { key: string; title: string; labels: string[]; forecast_due: string | null; variance_days: number }[]
  data_quality: { warnings: string[]; cycles: string[][]; missing_due_keys: string[] }
  suggestion_count: number
  suggestions: CascadeSuggestion[]
  edge_count: number
  at_risk_keys?: string[]
  edges?: { source_key: string; target_key: string }[]
  delay_sources?: { key: string; title: string }[]
}

const URGENCY_LABEL: Record<CascadeSuggestion['schedule_urgency'], string> = {
  critical: '紧急',
  high: '高优先',
  watch: '关注',
  normal: '常规',
}

export function useCascade(projectKey: string, enabled = true) {
  return useQuery({
    queryKey: ['cascade', projectKey],
    queryFn: () => api.post<CascadePayload>(`/api/projects/${projectKey}/schedule/analysis`, {}),
    enabled: !!projectKey && enabled,
  })
}

function briefOf(suggestion: CascadeSuggestion): string {
  // Rebuild from structured fields so English/Chinese both localize correctly.
  // Backend reason_brief is Chinese-only and must not be shown raw in EN.
  const sep = getLang() === 'en' ? ', ' : '、'
  const parts: string[] = []
  if (suggestion.delay_sources.length) {
    parts.push(t('因 {keys} 延期传导', { keys: suggestion.delay_sources.join(sep) }))
  } else if (suggestion.constraining_predecessors.length) {
    parts.push(t('受 {keys} 完成约束', { keys: suggestion.constraining_predecessors.join(sep) }))
  }
  if (suggestion.variance_days > 0) {
    parts.push(t('建议后移 {n} 个工作日', { n: suggestion.variance_days }))
  }
  if (suggestion.impacted_gates.length) {
    parts.push(t('触及 {labels}', {
      labels: suggestion.impacted_gates.map((label) => scheduleTagLabel(label)).join('/'),
    }))
  }
  if (suggestion.target_impact_days > 0) {
    parts.push(t('目标日影响 {n}', { n: signedDaysLabel(suggestion.target_impact_days) }))
  }
  return parts.join(getLang() === 'en' ? '; ' : '；') || t('顺序约束需要同步调整')
}

export function CascadeDrawer({
  projectKey,
  open,
  onClose,
  onFocusIssue,
}: {
  projectKey: string
  open: boolean
  onClose: () => void
  onFocusIssue?: (issueKey: string) => void
}) {
  const { toast, openAi } = useApp()
  const client = useQueryClient()
  const cascade = useCascade(projectKey, open)
  const suggestions = cascade.data?.suggestions ?? []
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [includePriority, setIncludePriority] = useState(false)

  useEffect(() => {
    if (cascade.data) {
      setSelected(new Set(cascade.data.suggestions.map((s) => s.key)))
    }
  }, [cascade.data])

  const selectedKeys = useMemo(() => [...selected].sort(), [selected])
  const preview = useQuery({
    queryKey: ['cascade-preview', projectKey, cascade.data?.analysis_token, selectedKeys.join(','), includePriority],
    queryFn: () => api.post<{
      stale: boolean
      target_date: string | null
      forecast_target_date: string | null
      target_impact_days: number
      items: unknown[]
    }>(`/api/projects/${projectKey}/schedule/cascade/preview`, {
      analysis_token: cascade.data!.analysis_token,
      keys: selectedKeys,
      sync_priority: includePriority,
    }),
    enabled: !!cascade.data?.analysis_token && selectedKeys.length > 0,
  })

  const apply = useMutation({
    mutationFn: async () => {
      const items = suggestions
        .filter((s) => selected.has(s.key))
        .map((s) => ({
          key: s.key,
          due_date: s.suggested_due ?? undefined,
          priority: includePriority ? s.suggested_priority ?? undefined : undefined,
          expected_lock_version: s.expected_lock_version,
        }))
        .filter((s) => s.due_date || s.priority)
      return api.post<{ count: number; stale: boolean; skipped: { key: string; reason: string }[] }>(`/api/projects/${projectKey}/schedule/cascade/apply`, {
        analysis_token: cascade.data!.analysis_token,
        note: t('进度链改期建议'),
        sync_priority: includePriority,
        items,
      })
    },
    onSuccess: (result) => {
      if (result.stale) {
        toast(t('分析结果已过期，请检查刷新后的建议'), 'error')
        void cascade.refetch()
        return
      }
      toast(t('已应用 {n} 项建议', { n: result.count }), 'success')
      client.invalidateQueries({ queryKey: ['cascade', projectKey] })
      client.invalidateQueries({ queryKey: ['timeline', projectKey] })
      client.invalidateQueries({ queryKey: ['portfolio'] })
      client.invalidateQueries({ queryKey: ['overview', projectKey] })
      onClose()
    },
    onError: (err: Error) => toast(err.message || t('应用失败'), 'error'),
  })

  const decide = useMutation({
    mutationFn: ({ suggestion, action }: { suggestion: CascadeSuggestion; action: 'reject' | 'snooze' }) =>
      api.post(`/api/projects/${projectKey}/schedule/risks/${suggestion.key}/decision`, {
        action,
        expected_version: suggestion.risk_lock_version,
        note: action === 'reject' ? t('项目经理拒绝当前建议') : t('稍后处理'),
        snoozed_until: action === 'snooze' ? new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString() : undefined,
      }),
    onSuccess: () => {
      toast(t('风险决策已记录'), 'success')
      client.invalidateQueries({ queryKey: ['cascade', projectKey] })
    },
    onError: (err: Error) => toast(err.message, 'error'),
  })

  const allKeys = useMemo(() => suggestions.map((s) => s.key), [suggestions])
  const topSources = useMemo(() => {
    const counts = new Map<string, number>()
    for (const suggestion of suggestions) {
      for (const key of suggestion.delay_sources) counts.set(key, (counts.get(key) ?? 0) + 1)
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([key]) => key)
  }, [suggestions])

  const askAi = () => {
    const lines = suggestions.slice(0, 8).map((s) =>
      `- ${s.key} ${s.title}：${briefOf(s)}（${s.current_due ?? '—'} → ${s.suggested_due ?? '—'}）`)
    openAi([
      t('请基于 schedule_analysis 工具结果，用不超过 5 条要点说明项目 {key} 的进度链改期建议。', { key: projectKey }),
      t('重点回答：主要延误源、受影响 Gate、建议先处理哪几项，以及为什么。不要编造未在工具结果中的数据。'),
      topSources.length ? t('当前可见延误源：{keys}', { keys: topSources.join('、') }) : '',
      `${t('当前建议摘要')}：`,
      ...lines,
    ].filter(Boolean).join('\n'))
  }

  if (!open) return null

  return (
    <Modal title={t('进度链改期建议')} onClose={onClose} size="wide">
      {cascade.isLoading ? (
        <Spinner center />
      ) : suggestions.length === 0 ? (
        <Empty title={t('暂无级联建议')}>
          {cascade.data?.edge_count
            ? t('当前没有因延期而需要调整的后继问题')
            : t('在问题详情添加阻塞关系后可计算顺序风险')}
        </Empty>
      ) : (
        <div className="cascade-drawer">
          <div className="cascade-toolbar">
            <div className="cascade-toolbar-group">
              <label className="cascade-check">
                <input
                  type="checkbox"
                  checked={selected.size === allKeys.length}
                  onChange={(e) => setSelected(e.target.checked ? new Set(allKeys) : new Set())}
                />
                {t('全选')}
              </label>
              <label className="cascade-check">
                <input
                  type="checkbox"
                  checked={includePriority}
                  onChange={(e) => setIncludePriority(e.target.checked)}
                />
                <PriorityIcon priority="high" size={14} />
                {t('同时提升优先级')}
              </label>
            </div>
            <div className="cascade-toolbar-group">
              <button type="button" className="btn ghost" onClick={askAi}>
                ✦ {t('AI 解读')}
              </button>
              <button
                type="button"
                className="btn primary"
                disabled={!selected.size || apply.isPending}
                onClick={() => apply.mutate()}
              >
                {t('应用所选 ({n})', { n: selected.size })}
              </button>
            </div>
          </div>

          <section className={`cascade-preview${(preview.data?.target_impact_days ?? cascade.data?.target_impact_days ?? 0) > 0 ? ' late' : ''}`}>
            <div className="cascade-preview-main">
              <div className="tiny faint">{t('方案预览')}</div>
              <div className="cascade-preview-flow">
                <span>{preview.data?.target_date ?? cascade.data?.target_date ?? '—'}</span>
                <b>→</b>
                <strong>{preview.data?.forecast_target_date ?? cascade.data?.forecast_target_date ?? '—'}</strong>
              </div>
            </div>
            <div className="cascade-preview-meta">
              <b>{signedDaysLabel((preview.data?.target_impact_days ?? cascade.data?.target_impact_days) || 0)}</b>
              <span>{t('目标日影响')}</span>
              <span>{t('可信度')} {confidenceLabel(cascade.data?.confidence)}</span>
            </div>
            {topSources.length > 0 && (
              <div className="cascade-preview-sources">
                <span className="tiny faint">{t('主要延误源')}</span>
                {topSources.map((key) => (
                  <button
                    key={key}
                    type="button"
                    className="cascade-chip"
                    onClick={() => onFocusIssue?.(key)}
                  >
                    {key}
                  </button>
                ))}
              </div>
            )}
            {(cascade.data?.data_quality.warnings.length ?? 0) > 0 && (
              <div className="cascade-notes">
                {cascade.data?.data_quality.warnings.map((warning) => (
                  <p className="small warn-text" key={warning}>{warning}</p>
                ))}
              </div>
            )}
          </section>

          <div className="cascade-list">
            {suggestions.map((s) => {
              const checked = selected.has(s.key)
              const shift = Math.max(0, s.variance_days)
              return (
                <article key={s.key} className={`cascade-card${checked ? ' selected' : ''} urgency-${s.schedule_urgency}`}>
                  <label className="cascade-card-check">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(e) => {
                        const next = new Set(selected)
                        if (e.target.checked) next.add(s.key)
                        else next.delete(s.key)
                        setSelected(next)
                      }}
                    />
                  </label>
                  <div className="cascade-card-body">
                    <div className="cascade-card-head">
                      <span className={`cascade-urgency ${s.schedule_urgency}`}>{t(URGENCY_LABEL[s.schedule_urgency])}</span>
                      <button type="button" className="cascade-card-open" onClick={() => onFocusIssue?.(s.key)}>
                        <span className="issue-key">{s.key}</span>
                        <span className="ellipsis">{s.title}</span>
                      </button>
                      {shift > 0 && <b className="cascade-shift">{signedDaysLabel(shift)}</b>}
                    </div>
                    <div className="cascade-card-dates">
                      <span>{s.current_due ?? '—'}</span>
                      <b>→</b>
                      <strong>{s.suggested_due ?? '—'}</strong>
                      {s.suggested_priority && s.suggested_priority !== s.current_priority && (
                        <span className={`cascade-priority-shift${includePriority ? ' active' : ''}`}>
                          <PriorityIcon priority={s.current_priority as Priority} size={13} />
                          <span>{priorityName(s.current_priority)}</span>
                          <b>→</b>
                          <PriorityIcon priority={s.suggested_priority as Priority} size={13} />
                          <strong>{priorityName(s.suggested_priority)}</strong>
                        </span>
                      )}
                    </div>
                    <p className="cascade-reason">{briefOf(s)}</p>
                    <div className="cascade-card-actions">
                      <button type="button" className="btn sm cascade-action-focus" onClick={() => onFocusIssue?.(s.key)}>
                        {t('看影响')}
                      </button>
                      {s.risk_lock_version && (
                        <>
                          <button
                            className="btn sm cascade-action-snooze"
                            type="button"
                            onClick={() => decide.mutate({ suggestion: s, action: 'snooze' })}
                          >
                            {t('稍后')}
                          </button>
                          <button
                            className="btn sm cascade-action-reject"
                            type="button"
                            onClick={() => decide.mutate({ suggestion: s, action: 'reject' })}
                          >
                            {t('拒绝')}
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                </article>
              )
            })}
          </div>
        </div>
      )}
    </Modal>
  )
}
