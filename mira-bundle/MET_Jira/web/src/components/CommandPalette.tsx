import { useQuery } from '@tanstack/react-query'
import { useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useApp } from '../context'
import { t } from '../i18n'
import { useProjects } from '../queries'
import { ACTION_BY_ID, type ActionId, type Bindings, comboText } from '../shortcuts'
import { ProjectBadge } from '../projectIcons'
import type { IssueType, UserBrief } from '../types'
import { Avatar, FieldIcon, StatusPill, TypeIcon, useDebounced, useEscape } from './ui'
import { leaveAnimation } from './leave'

interface Item {
  id: string
  group: string
  label: ReactNode
  hint?: string
  run: () => void
}

interface QuickResult {
  issues: { key: string; title: string; type: IssueType; status: { name: string; color: string } }[]
  users: UserBrief[]
}

export function CommandPalette({ onClose, bindings, runAction }: {
  onClose: () => void
  bindings: Bindings
  runAction: (id: ActionId) => void
}) {
  const [q, setQ] = useState('')
  const [index, setIndex] = useState(0)
  const debounced = useDebounced(q.trim(), 150)
  const navigate = useNavigate()
  const { openIssue, openAi, user } = useApp()
  const projects = useProjects()
  useEscape(onClose)

  const quick = useQuery({
    queryKey: ['quick', debounced],
    queryFn: ({ signal }) => api.get<QuickResult>(`/api/search/quick?q=${encodeURIComponent(debounced)}`, signal),
    enabled: debounced.length > 0,
    staleTime: 15_000,
  })

  const items = useMemo<Item[]>(() => {
    const go = (fn: () => void) => () => {
      onClose()
      fn()
    }
    const lower = q.trim().toLowerCase()
    const list: Item[] = []
    if (q.trim()) {
      list.push({
        id: 'ai',
        group: 'AI',
        label: (
          <>
            <span className="ai-orb" style={{ width: 18, height: 18 }} /> {t('问 AI：“{q}”', { q: q.trim() })}
          </>
        ),
        run: go(() => openAi(q.trim())),
      })
    }
    for (const issue of quick.data?.issues ?? []) {
      list.push({
        id: `i-${issue.key}`,
        group: t('问题'),
        label: (
          <>
            <TypeIcon type={issue.type} />
            <span className="issue-key">{issue.key}</span>
            <span className="ellipsis" style={{ flex: 1 }}>
              {issue.title}
            </span>
            <StatusPill status={issue.status} />
          </>
        ),
        run: go(() => openIssue(issue.key)),
      })
    }
    for (const u of quick.data?.users ?? []) {
      list.push({
        id: `u-${u.id}`,
        group: t('人员'),
        label: (
          <>
            <Avatar user={u} size={22} /> {u.display_name} <span className="faint tiny">{u.title ?? u.username}</span>
          </>
        ),
        run: go(() => navigate(`/people/${u.username}`)),
      })
    }
    for (const p of projects.data ?? []) {
      if (!lower || p.name.toLowerCase().includes(lower) || p.key.toLowerCase().includes(lower)) {
        list.push({
          id: `p-${p.id}`,
          group: t('项目'),
          label: (
            <>
              <ProjectBadge icon={p.icon} color={p.color} label={p.key.slice(0, 2)} size={18} className="project-dot" />
              {p.name} <span className="faint tiny">{p.key}</span>
            </>
          ),
          run: go(() => navigate(`/projects/${p.key}/board`)),
        })
      }
    }
    const commands: { id: ActionId; run?: () => void }[] = [
      { id: 'create' },
      { id: 'ai_create' },
      { id: 'ai' },
      { id: 'my_work' },
      { id: 'projects' },
      { id: 'search', run: () => navigate(`/search?jql=${encodeURIComponent(lower ? `text ~ "${q.trim()}"` : '')}`) },
      { id: 'org' },
      { id: 'theme' },
      { id: 'shortcuts' },
      { id: 'guide' },
    ]
    if (user.is_admin) commands.push({ id: 'admin' })
    for (const command of commands) {
      const label = t(ACTION_BY_ID[command.id].label)
      if (!lower || label.toLowerCase().includes(lower)) {
        list.push({
          id: `c-${command.id}`, group: t('命令'), label, hint: comboText(bindings[command.id]),
          run: go(command.run ?? (() => runAction(command.id))),
        })
      }
    }
    const glossaryLabel = t('名词与图表')
    if (!lower || glossaryLabel.toLowerCase().includes(lower) || 'glossary'.includes(lower)) {
      list.push({
        id: 'c-glossary',
        group: t('命令'),
        label: glossaryLabel,
        hint: '/guide/glossary',
        run: go(() => navigate('/guide/glossary')),
      })
    }
    return list
  }, [q, quick.data, projects.data, onClose, openAi, openIssue, navigate, user.is_admin, bindings, runAction])

  const active = Math.min(index, Math.max(items.length - 1, 0))
  let lastGroup = ''

  return (
    <div className="overlay" ref={leaveAnimation} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="palette">
        <div className="palette-search">
          <FieldIcon name="search" size={18} />
          <input
            autoFocus
            placeholder={t('搜索问题、人员、项目，或直接向 AI 提问…')}
            value={q}
            onChange={(e) => {
              setQ(e.target.value)
              setIndex(0)
            }}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') {
                e.preventDefault()
                setIndex((i) => Math.min(i + 1, items.length - 1))
              } else if (e.key === 'ArrowUp') {
                e.preventDefault()
                setIndex((i) => Math.max(i - 1, 0))
              } else if (e.key === 'Enter') {
                e.preventDefault()
                items[active]?.run()
              }
            }}
          />
        </div>
        <div className="palette-list">
          {items.map((item, i) => {
            const header = item.group !== lastGroup ? <div className="palette-group">{item.group}</div> : null
            lastGroup = item.group
            return (
              <div key={item.id}>
                {header}
                <button className={`palette-item ${i === active ? 'active' : ''}`} onMouseEnter={() => setIndex(i)} onClick={item.run}>
                  {item.label}
                  {item.hint && (
                    <>
                      <span className="spacer" />
                      <kbd>{item.hint}</kbd>
                    </>
                  )}
                </button>
              </div>
            )
          })}
          {items.length === 0 && <div className="empty small">{t('没有匹配结果')}</div>}
        </div>
      </div>
    </div>
  )
}
