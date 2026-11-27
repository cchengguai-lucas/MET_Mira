import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { t, tServer } from '../i18n'

interface Turn {
  role: 'user' | 'assistant'
  content: string
  jql?: string
  total?: number | null
  question?: string | null
  failed?: boolean
}

interface AssistResult {
  jql: string
  explanation: string
  question: string | null
  error: string | null
  total: number | null
}

const SUGGESTIONS = ['我负责的、本周到期还没完成的', 'CMB-1 机台组涉及软件的需求', '上周测试没通过的需求', '最近 3 天新建的高优先级缺陷']
const OPEN_KEY = 'metjira.ai-filter.open'

function readOpen(): boolean {
  try {
    return localStorage.getItem(OPEN_KEY) === '1'
  } catch {
    return false
  }
}

/** Chat that turns plain-language requests into a JQL filter and applies it; follow-ups refine the current filter. */
export function AiFilterAssistant({ currentJql, onApply }: { currentJql: string; onApply: (jql: string) => void }) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const [focused, setFocused] = useState(false)
  const [open, setOpen] = useState(readOpen)
  const thread = useRef<HTMLDivElement>(null)
  const area = useRef<HTMLTextAreaElement>(null)

  const lastAssist = [...turns].reverse().find((m) => m.role === 'assistant' && m.jql && !m.failed)
  const applied = lastAssist?.jql === currentJql

  useEffect(() => {
    try {
      localStorage.setItem(OPEN_KEY, open ? '1' : '0')
    } catch { /* ignore */ }
  }, [open])

  useEffect(() => {
    if (!open) return
    thread.current?.scrollTo({ top: thread.current.scrollHeight, behavior: 'smooth' })
  }, [turns, busy, open])

  useEffect(() => {
    const el = area.current
    if (!el || !open) return
    el.style.height = '0px'
    el.style.height = `${Math.min(96, Math.max(40, el.scrollHeight))}px`
  }, [input, open])

  const setExpanded = (next: boolean) => {
    setOpen(next)
    if (next) queueMicrotask(() => area.current?.focus())
  }

  const send = async (text: string) => {
    const content = text.trim()
    if (!content || busy) return
    if (!open) setExpanded(true)
    const history = [...turns, { role: 'user' as const, content }]
    setTurns(history)
    setInput('')
    setBusy(true)
    try {
      const res = await api.post<AssistResult>('/api/agent/jql', {
        messages: history.map((m) => ({ role: m.role, content: m.jql ? `${m.content}\nJQL: ${m.jql}` : m.content })),
        current_jql: currentJql,
      })
      setTurns([
        ...history,
        {
          role: 'assistant',
          content: res.error ? t('没能生成有效的筛选条件，换个说法试试？') : res.explanation,
          jql: res.jql || undefined,
          total: res.total,
          question: res.question,
          failed: !!res.error,
        },
      ])
      if (res.jql) onApply(res.jql)
    } catch (err) {
      setTurns([...history, { role: 'assistant', content: tServer((err as Error).message), failed: true }])
    } finally {
      setBusy(false)
    }
  }

  const summary = applied && lastAssist
    ? lastAssist.total != null
      ? t('已应用 · 匹配 {n} 条', { n: lastAssist.total })
      : t('已应用筛选')
    : turns.length
      ? t('{n} 轮对话', { n: Math.ceil(turns.length / 2) })
      : t('用一句话描述要找的问题')

  return (
    <section className={`ai-filter${open ? ' is-open' : ' is-collapsed'}${focused ? ' focused' : ''}${turns.length ? ' has-thread' : ''}`}>
      <button
        type="button"
        className="ai-filter-toggle"
        aria-expanded={open}
        onClick={() => setExpanded(!open)}
      >
        <span className="ai-orb sm" aria-hidden />
        <span className="ai-filter-toggle-text">
          <span className="ai-filter-kicker">{t('AI 帮你筛选')}</span>
          <span className="ai-filter-summary ellipsis">{summary}</span>
        </span>
        <span className="ai-filter-chevron" aria-hidden>{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="ai-filter-body">
          <div className="ai-filter-toolbar">
            <span className="ai-filter-title">{t('用一句话说出你要找的问题')}</span>
            {turns.length > 0 && (
              <button type="button" className="btn ghost sm" onClick={() => setTurns([])} disabled={busy}>
                {t('新对话')}
              </button>
            )}
          </div>

          {turns.length > 0 && (
            <div
              className={`ai-filter-thread${scrolled ? ' scrolled' : ''}`}
              ref={thread}
              onScroll={(e) => setScrolled(e.currentTarget.scrollTop > 4)}
            >
              {turns.map((m, i) =>
                m.role === 'user' ? (
                  <div key={i} className="bubble user">
                    {m.content}
                  </div>
                ) : (
                  <div key={i} className={`bubble assistant ${m.failed ? 'failed' : ''}`}>
                    {m.content && <div>{m.content}</div>}
                    {m.question && <div className="ai-filter-question">{m.question}</div>}
                    {m.jql && (
                      <div className="ai-filter-result">
                        <code className="mono">{m.jql}</code>
                        <div className="row tiny" style={{ gap: 8 }}>
                          {m.total != null && <span className="muted">{t('匹配 {n} 条', { n: m.total })}</span>}
                          {m.jql === currentJql ? (
                            <span className="ok-text">✓ {t('已应用')}</span>
                          ) : (
                            <button type="button" className="btn ghost sm" onClick={() => onApply(m.jql!)}>
                              {t('应用这个条件')}
                            </button>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                ),
              )}
              {busy && (
                <div className="bubble assistant">
                  <span className="typing">
                    <i />
                    <i />
                    <i />
                  </span>
                </div>
              )}
            </div>
          )}

          <div className="ai-filter-composer">
            <textarea
              ref={area}
              className="ai-filter-input"
              rows={1}
              placeholder={turns.length ? t('继续调整，例如：只看高优先级的 / 去掉已完成的') : t('例如：上周 CMB-1 测试没通过的需求')}
              value={input}
              disabled={busy}
              onChange={(e) => setInput(e.target.value)}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  send(input)
                }
              }}
            />
            <div className="ai-filter-composer-bar">
              <span className="ai-filter-hint">{t('Enter 生成 · Shift+Enter 换行')}</span>
              <button type="button" className="btn ai ai-filter-send" disabled={busy || !input.trim()} onClick={() => send(input)}>
                ✦ {busy ? t('生成中…') : t('生成筛选')}
              </button>
            </div>
          </div>

          {turns.length === 0 && (
            <div className="ai-filter-suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" className="ai-filter-chip" onClick={() => send(t(s))}>
                  {t(s)}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
