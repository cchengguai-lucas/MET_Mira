import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api, streamPost } from '../api'
import { useApp } from '../context'
import { t, tServer } from '../i18n'
import { useInvalidateIssues } from '../queries'
import type { AgentAction } from '../types'
import { Markdown } from './Markdown'
import { Modal, Spinner } from './ui'

type Entry =
  | { kind: 'user'; text: string }
  | { kind: 'assistant'; text: string }
  | { kind: 'tool'; name: string; args?: Record<string, unknown>; summary?: string; ok?: boolean }
  | { kind: 'action'; action: AgentAction; error?: string }
  | { kind: 'error'; text: string }

/** ``crypto.randomUUID`` needs a secure context (HTTPS/localhost); Mira is often served over HTTP. */
function clientMessageId(): string {
  const c = globalThis.crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  const bytes = new Uint8Array(16)
  if (c?.getRandomValues) c.getRandomValues(bytes)
  else for (let i = 0; i < 16; i++) bytes[i] = (Math.random() * 256) | 0
  bytes[6] = (bytes[6]! & 0x0f) | 0x40
  bytes[8] = (bytes[8]! & 0x3f) | 0x80
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

const TOOL_LABELS: Record<string, string> = {
  search_issues: '搜索问题',
  get_issue: '查看问题',
  find_users: '查找人员',
  list_org_units: '查询组织架构',
  project_meta: '读取项目配置',
  analyze_workflow: '检查工作流',
  suggest_workflow: '生成工作流草稿',
  apply_workflow: '应用工作流草稿',
  project_stats: '统计项目数据',
  product_help: '查阅使用说明',
  create_issue: '创建问题',
  update_issue: '修改问题',
  transition_issue: '流转状态',
  assign_issue: '分配负责人',
  add_comment: '添加评论',
  open_page: '跳转页面',
}

const SUGGESTIONS = [
  '我负责的未完成需求有哪些？',
  '测试中的需求有哪些，分别是谁在测？',
  '帮我创建一个需求：CMB-1 上料模块增加真空压力报警，优先级高',
  '本周电气组完成了哪些需求？',
  '研发中超过两周未更新的问题',
  '状态门禁和会签审批是怎么工作的？',
  '检查当前项目的需求工作流，并给出优化草稿',
  '带我去工作流设置',
]

const STORAGE_KEY = 'metjira.ai.history'

function toolLabel(name: string): string {
  return TOOL_LABELS[name] ? t(TOOL_LABELS[name]) : name
}

/** Friendly breadcrumb for an open_page action (project settings → section, etc.). */
function navBreadcrumb(action: AgentAction): string {
  const summary = action.summary || ''
  const m = summary.match(/「(.+?)」/)
  if (m) return m[1]
  const dest = String(action.args?.destination || '')
  const project = String(action.args?.project || '')
  if (action.args?.issue_key) return String(action.args.issue_key).toUpperCase()
  if (dest.startsWith('project.settings')) {
    const section = dest.split('.').pop() || ''
    const sectionLabel: Record<string, string> = {
      general: '基本信息', members: '成员与角色', workflow: '状态与工作流',
      machines: '机台', fields: '字段选项', boards: '看板配置', import: 'Excel 导入',
      settings: '项目设置',
    }
    const leaf = t(sectionLabel[section] || section)
    return project
      ? t('{project} → 设置 → {section}', { project, section: leaf })
      : t('设置 → {section}', { section: leaf })
  }
  if (dest.startsWith('project.')) {
    const leaf = dest.split('.').pop() || dest
    const view: Record<string, string> = {
      board: '看板', backlog: '待办事项', list: '列表', summary: '摘要',
      timeline: '时间线', reports: '报表',
    }
    const label = t(view[leaf] || leaf)
    return project ? `${project} → ${label}` : label
  }
  return summary || dest
}

/** Clicks here never count as "outside": the panel's own popups and the buttons that toggle it. */
const KEEP_OPEN = '.ai-panel, [data-ai-toggle], .overlay, .lightbox, .toasts'

/** Stays mounted once opened so the close can animate and a streaming answer keeps arriving. */
export function AiPanel({ open }: { open: boolean }) {
  const { closeAi, aiSeed, currentProject, openIssue, toast } = useApp()
  const navigate = useNavigate()
  const location = useLocation()
  const panel = useRef<HTMLDivElement>(null)
  const invalidate = useInvalidateIssues()
  const [entries, setEntries] = useState<Entry[]>(() => {
    try {
      return JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? '[]') as Entry[]
    } catch {
      return []
    }
  })
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [conversationId, setConversationId] = useState<number | null>(null)
  const [memoryOpen, setMemoryOpen] = useState(false)
  const [memoryText, setMemoryText] = useState('')
  const abort = useRef<AbortController | null>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const seeded = useRef<{ text: string } | null>(null)
  const status = useQuery({
    queryKey: ['agent-status'],
    queryFn: () => api.get<{ enabled: boolean; reachable: boolean; tool_mode: string; model: string; error?: string }>('/api/agent/status'),
    staleTime: 300_000,
  })
  const memories = useQuery({
    queryKey: ['agent-memories', currentProject],
    queryFn: () => api.get<{ memories: { id: number; content: string; scope: string }[] }>(
      `/api/agent/memories${currentProject ? `?project=${currentProject}` : ''}`,
    ),
    enabled: memoryOpen,
  })

  useEffect(() => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(-60)))
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' })
  }, [entries])

  const send = async (text: string) => {
    const content = text.trim()
    if (!content || busy) return
    setInput('')
    const next: Entry[] = [...entries, { kind: 'user', text: content }]
    setEntries(next)
    setBusy(true)
    const controller = new AbortController()
    abort.current = controller
    let assistantOpen = false
    try {
      await streamPost(
        '/api/agent/chat',
        {
          messages: [{ role: 'user', content }],
          project: currentProject,
          conversation_id: conversationId,
          client_message_id: clientMessageId(),
          ui_context: {
            path: location.pathname,
            query: location.search,
            project: currentProject,
          },
        },
        (ev) => {
          if (ev.type === 'conversation' && ev.id) setConversationId(ev.id)
          setEntries((list) => {
            const copy = [...list]
            if (ev.type === 'tool_call') {
              copy.push({ kind: 'tool', name: ev.name ?? '', args: ev.args })
            } else if (ev.type === 'tool_result') {
              for (let i = copy.length - 1; i >= 0; i--) {
                const e = copy[i]
                if (e.kind === 'tool' && e.name === ev.name && e.summary === undefined) {
                  copy[i] = { ...e, summary: ev.summary, ok: ev.ok }
                  break
                }
              }
            } else if (ev.type === 'action' && ev.action) {
              for (let i = copy.length - 1; i >= 0; i--) {
                const e = copy[i]
                if (e.kind === 'tool' && e.name === ev.action.tool && e.summary === undefined) {
                  copy.splice(i, 1)
                  break
                }
              }
              copy.push({ kind: 'action', action: ev.action })
            } else if (ev.type === 'delta') {
              const last = copy[copy.length - 1]
              if (assistantOpen && last?.kind === 'assistant') copy[copy.length - 1] = { ...last, text: last.text + (ev.text ?? '') }
              else {
                copy.push({ kind: 'assistant', text: ev.text ?? '' })
                assistantOpen = true
              }
            } else if (ev.type === 'error') {
              copy.push({ kind: 'error', text: ev.message ?? 'AI 出错了' })
            }
            return copy
          })
        },
        controller.signal,
      )
    } catch (err) {
      if ((err as Error).name !== 'AbortError') setEntries((list) => [...list, { kind: 'error', text: (err as Error).message }])
    } finally {
      setBusy(false)
      abort.current = null
    }
  }

  useEffect(() => {
    if (open && aiSeed && seeded.current !== aiSeed) {
      seeded.current = aiSeed
      send(aiSeed.text)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aiSeed, open])

  useEffect(() => {
    if (!open) {
      if (panel.current?.contains(document.activeElement)) (document.activeElement as HTMLElement).blur()
      return
    }
    const onPointer = (e: PointerEvent) => {
      const target = e.target as Element | null
      if (target?.isConnected && !target.closest(KEEP_OPEN)) closeAi()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || document.querySelector('.overlay:not(.leaving), .lightbox')) return
      closeAi()
    }
    document.addEventListener('pointerdown', onPointer, true)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer, true)
      window.removeEventListener('keydown', onKey)
    }
  }, [open, closeAi])

  const decide = async (index: number, action: AgentAction, confirm: boolean) => {
    try {
      const res = await api.post<{ action: AgentAction; result?: Record<string, unknown> }>(`/api/agent/actions/${action.id}/${confirm ? 'confirm' : 'reject'}`)
      setEntries((list) => list.map((e, i) => (i === index ? { kind: 'action', action: res.action } : e)))
      if (!confirm) return
      const result = res.result ?? res.action.result
      if (action.tool === 'open_page' && result && typeof result.path === 'string') {
        navigate(result.path)
        closeAi()
        toast(t('已跳转到「{label}」', { label: String(result.label || result.path) }), 'success')
        return
      }
      invalidate()
      toast(t('已执行'), 'success')
    } catch (err) {
      setEntries((list) => list.map((e, i) => (i === index && e.kind === 'action' ? { ...e, action: { ...e.action, status: 'failed' }, error: (err as Error).message } : e)))
    }
  }

  const st = status.data
  return (
    <>
    <div ref={panel} className={`ai-panel ${open ? 'open' : 'closed'}`} aria-hidden={!open} inert={!open}>
      <div className="ai-header">
        <span className="ai-orb" />
        <div style={{ flex: 1, minWidth: 0 }}>
          <b>{t('MET AI 助手')}</b>
          <div className="tiny faint ellipsis">
            {st ? (st.reachable ? `${st.model} · ${st.tool_mode === 'native' ? t('原生工具调用') : t('JSON 工具协议')}` : st.enabled ? t('不可用：{error}', { error: tServer(st.error) }) : t('未启用')) : t('连接中…')}
            {currentProject ? ` · ${t('当前项目 {key}', { key: currentProject })}` : ''}
          </div>
        </div>
        <button className="btn ghost sm" onClick={() => setMemoryOpen(true)}>
          {t('记忆')}
        </button>
        <button className="btn ghost sm" onClick={() => { setEntries([]); setConversationId(null) }} disabled={busy}>
          {t('新对话')}
        </button>
        <button className="btn ghost icon sm" onClick={closeAi}>
          ✕
        </button>
      </div>
      <div className="ai-messages" ref={scroller}>
        {entries.length === 0 && (
          <div className="col" style={{ gap: 14, marginTop: 20 }}>
            <div className="muted small">
              {t('我可以帮你查询问题、统计进度、总结讨论，解答 Mira 各功能的用法和定义，也可以直接创建和修改问题，或带你跳转到对应设置页。修改与跳转都会先请你确认。')}
            </div>
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="suggestion" onClick={() => send(t(s))}>
                  {t(s)}
                </button>
              ))}
            </div>
          </div>
        )}
        {entries.map((e, i) => {
          if (e.kind === 'user') return <div key={i} className="bubble user">{e.text}</div>
          if (e.kind === 'assistant')
            return (
              <div key={i} className="bubble assistant">
                <Markdown text={e.text} onIssueClick={openIssue} />
              </div>
            )
          if (e.kind === 'error')
            return (
              <div key={i} className="bubble assistant err-text">
                ⚠ {tServer(e.text)}
              </div>
            )
          if (e.kind === 'tool')
            return (
              <div key={i} className="tool-chip" title={JSON.stringify(e.args ?? {}, null, 1)}>
                {e.summary === undefined ? <Spinner /> : e.ok === false ? '⚠' : '✓'} {toolLabel(e.name)}
                {e.summary && <span className="faint">· {tServer(e.summary)}</span>}
              </div>
            )
          const a = e.action
          const resultKey = (a.result as { key?: string } | undefined)?.key
          const resultPath = (a.result as { path?: string } | undefined)?.path
          const isNav = a.tool === 'open_page'
          const navTrail = isNav ? navBreadcrumb(a) : null
          return (
            <div key={i} className={`action-card${isNav ? ' action-nav' : ''}`}>
              <div className="row small">
                <b>✦ {isNav ? t('要去这里吗？') : toolLabel(a.tool)}</b>
                <span className="spacer" />
                {a.status === 'done' && <span className="ok-text tiny">{isNav ? t('已跳转') : t('已执行')}</span>}
                {a.status === 'rejected' && <span className="faint tiny">{t('已取消')}</span>}
                {a.status === 'failed' && <span className="err-text tiny">{t('失败')}</span>}
                {a.status === 'pending' && !isNav && <span className="warn-text tiny">{t('待确认')}</span>}
              </div>
              {isNav && navTrail ? (
                <div className="nav-trail" style={{ marginTop: 6 }}>{navTrail}</div>
              ) : (
                <div style={{ marginTop: 4 }}>{tServer(a.summary)}</div>
              )}
              {!isNav && (
                <details>
                  <summary className="tiny faint" style={{ cursor: 'pointer' }}>
                    {t('参数')}
                  </summary>
                  <pre>{JSON.stringify(a.args, null, 2)}</pre>
                </details>
              )}
              {e.error && <div className="err-text small">{e.error}</div>}
              {resultKey && (
                <a className="small" style={{ cursor: 'pointer' }} onClick={() => openIssue(resultKey)}>
                  {t('打开 {key}', { key: resultKey })} →
                </a>
              )}
              {resultPath && (
                <a className="small" style={{ cursor: 'pointer' }} onClick={() => {
                  navigate(resultPath)
                  closeAi()
                }}>
                  {t('打开工作流设置')} →
                </a>
              )}
              {a.status === 'pending' && (
                <div className="row" style={{ marginTop: 8 }}>
                  <button className="btn primary sm" onClick={() => decide(i, a, true)}>
                    {isNav ? t('去这里') : t('确认执行')}
                  </button>
                  <button className="btn sm" onClick={() => decide(i, a, false)}>
                    {t('取消')}
                  </button>
                </div>
              )}
            </div>
          )
        })}
        {busy && entries[entries.length - 1]?.kind !== 'assistant' && (
          <div className="row small muted">
            <Spinner /> {t('思考中…')}
          </div>
        )}
      </div>
      <div className="ai-input">
        <textarea
          className="textarea"
          placeholder={t('想了解什么尽管问，也可以让我创建或修改问题…（Enter 发送，Shift+Enter 换行）')}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              send(input)
            }
          }}
        />
        {busy ? (
          <button className="btn" onClick={() => abort.current?.abort()}>
            {t('停止')}
          </button>
        ) : (
          <button className="btn ai" disabled={!input.trim()} onClick={() => send(input)}>
            {t('发送')}
          </button>
        )}
      </div>
    </div>
    {memoryOpen && <Modal title={t('AI 长期记忆')} onClose={() => setMemoryOpen(false)}>
      <p className="small muted">{t('只有你明确添加的内容才会成为长期记忆。可随时删除；项目记忆只用于当前项目。')}</p>
      <div className="row">
        <input className="input" value={memoryText} onChange={(event) => setMemoryText(event.target.value)} placeholder={t('例如：风险汇报优先按 Gate 分组')} />
        <button className="btn primary" disabled={!memoryText.trim()} onClick={async () => {
          await api.post('/api/agent/memories', { content: memoryText.trim(), project: currentProject })
          setMemoryText('')
          void memories.refetch()
        }}>{t('确认记住')}</button>
      </div>
      <div className="col" style={{ gap: 8, marginTop: 12 }}>
        {memories.data?.memories.map((memory) => <div className="row card card-pad" key={memory.id}>
          <span className="small">{memory.content}</span><span className="spacer" />
          <button className="btn ghost sm" onClick={async () => {
            await api.del(`/api/agent/memories/${memory.id}`)
            void memories.refetch()
          }}>{t('删除')}</button>
        </div>)}
        {!memories.isLoading && !memories.data?.memories.length && <span className="small muted">{t('暂无长期记忆')}</span>}
      </div>
    </Modal>}
    </>
  )
}
