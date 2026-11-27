import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api, qs } from '../api'
import { SaveFilterModal } from '../components/SaveFilterModal'
import { Empty, Segmented, Spinner, Tabs, useDebounced, UserChip, Select } from '../components/ui'
import { useApp } from '../context'
import { t } from '../i18n'
import { timeAgo } from '../labels'
import { ProjectBadge } from '../projectIcons'
import { useProjects } from '../queries'
import type { SavedFilter, SharedFilter } from '../types'

let lastSearch = ''

/** Remember the Filters page query so switching tabs brings the user back to it. */
export function rememberSearch(search: string) {
  lastSearch = search
}

export function FilterPageTabs() {
  const navigate = useNavigate()
  const shared = useLocation().pathname.startsWith('/search/shared')
  return (
    <Tabs<'mine' | 'shared'>
      value={shared ? 'shared' : 'mine'}
      onChange={(v) => navigate(v === 'shared' ? '/search/shared' : `/search${lastSearch ? `?${lastSearch}` : ''}`)}
      items={[
        { value: 'mine', label: t('筛选') },
        { value: 'shared', label: t('共享筛选器') },
      ]}
    />
  )
}

export function ScopeBadge({ filter }: { filter: Pick<SavedFilter, 'scope' | 'project'> }) {
  const projects = useProjects()
  if (filter.scope === 'everyone') return <span className="tag scope-badge everyone">{t('所有人')}</span>
  if (filter.scope === 'project') {
    const key = filter.project?.key ?? '?'
    const proj = projects.data?.find((p) => p.key === key)
    return (
      <span className="tag scope-badge project row" style={{ gap: 5 }} title={filter.project?.name ?? key}>
        <ProjectBadge
          icon={proj?.icon}
          color={proj?.color ?? '#8e8e93'}
          label={key.slice(0, 2)}
          size={14}
          className="project-dot"
        />
        {t('{key} 成员', { key })}
      </span>
    )
  }
  return null
}

function MatchCount({ jql }: { jql: string }) {
  const { data, error } = useQuery({
    queryKey: ['filter-count', jql],
    queryFn: () => api.get<{ total: number }>(`/api/search${qs({ jql, limit: 1 })}`),
    staleTime: 60_000,
    retry: false,
  })
  if (error) return <span className="err-text">{t('查询失败')}</span>
  if (!data) return <span className="faint">…</span>
  return <span title={t('按你的权限和身份计算')}>{t('匹配 {n} 条', { n: data.total })}</span>
}

function FilterCard({ filter, onEdit }: { filter: SharedFilter; onEdit: () => void }) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const { toast } = useApp()
  const [expanded, setExpanded] = useState(false)
  const [busy, setBusy] = useState(false)

  const refresh = () => {
    client.invalidateQueries({ queryKey: ['filters'] })
    client.invalidateQueries({ queryKey: ['shared-filters'] })
  }
  const act = async (fn: () => Promise<unknown>, message: string) => {
    setBusy(true)
    try {
      await fn()
      refresh()
      toast(message, 'success')
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card shared-filter-card">
      <div className="row" style={{ alignItems: 'flex-start' }}>
        <h3 className="shared-filter-name">{filter.name}</h3>
        <span className="spacer" />
        <ScopeBadge filter={filter} />
      </div>
      {filter.description && <div className="shared-filter-desc">{filter.description}</div>}
      <code className={`shared-filter-jql ${expanded ? 'expanded' : ''}`} title={expanded ? undefined : filter.jql} onClick={() => setExpanded(!expanded)}>
        {filter.jql}
      </code>
      <div className="row small muted shared-filter-meta">
        <UserChip user={filter.owner} size={20} />
        <span className="spacer" />
        <span className="faint">{timeAgo(filter.updated_at)}</span>
      </div>
      <div className="row small muted shared-filter-meta">
        <MatchCount jql={filter.jql} />
        <span className="faint">·</span>
        <span>{t('被保存 {n} 次', { n: filter.copies })}</span>
      </div>
      <div className="row shared-filter-actions">
        <button className="btn sm" onClick={() => navigate(`/search${qs({ jql: filter.jql })}`)}>
          {t('试运行')}
        </button>
        <span className="spacer" />
        {filter.mine ? (
          <>
            <button className="btn sm" onClick={onEdit}>
              {t('编辑')}
            </button>
            <button className="btn sm" disabled={busy} onClick={() => act(() => api.patch(`/api/filters/${filter.id}`, { scope: 'private' }), t('已取消共享'))}>
              {t('取消共享')}
            </button>
          </>
        ) : filter.saved ? (
          <button
            className="btn sm"
            disabled={busy}
            title={t('再保存一份副本')}
            onClick={() => act(() => api.post(`/api/filters/${filter.id}/copy`), t('已保存到我的筛选器'))}
          >
            ✓ {t('已保存')}
          </button>
        ) : (
          <button className="btn primary sm" disabled={busy} onClick={() => act(() => api.post(`/api/filters/${filter.id}/copy`), t('已保存到我的筛选器'))}>
            {t('保存到我的筛选器')}
          </button>
        )}
      </div>
    </div>
  )
}

export function SharedFiltersPage() {
  const { user } = useApp()
  const projects = useProjects()
  const [q, setQ] = useState('')
  const [project, setProject] = useState('')
  const [sort, setSort] = useState<'popular' | 'new'>('popular')
  const [editing, setEditing] = useState<SavedFilter | null>(null)
  const query = useDebounced(q.trim(), 250)
  const { data, isLoading } = useQuery({
    queryKey: ['shared-filters', query, project, sort],
    queryFn: () => api.get<{ filters: SharedFilter[] }>(`/api/filters/shared${qs({ q: query, project, sort })}`),
    placeholderData: (previous) => previous,
  })
  const choices = (projects.data ?? []).filter((p) => user.is_admin || p.is_member)
  const filtered = Boolean(query || project)

  return (
    <div className="page page-wide page-full">
      <div className="page-header">
        <div>
          <h1>{t('共享筛选器')}</h1>
          <div className="muted small">{t('浏览同事共享的筛选器，保存一份到自己的筛选器库后可随意修改。')}</div>
        </div>
        <FilterPageTabs />
      </div>
      <div className="row shared-filter-toolbar">
        <input className="input" style={{ maxWidth: 320 }} placeholder={t('搜索名称、描述或 JQL')} value={q} onChange={(e) => setQ(e.target.value)} />
        <Select className="select" style={{ width: 'auto' }} value={project} onChange={(e) => setProject(e.target.value)}>
          <option value="">{t('全部范围')}</option>
          <option value="everyone">{t('共享给所有人')}</option>
          {choices.map((p) => (
            <option key={p.key} value={p.key}>
              {t('{key} 成员', { key: p.key })} · {p.name}
            </option>
          ))}
        </Select>
        <span className="spacer" />
        <Segmented<'popular' | 'new'>
          value={sort}
          onChange={setSort}
          items={[
            { value: 'popular', label: t('最常保存') },
            { value: 'new', label: t('最新') },
          ]}
        />
      </div>
      {isLoading ? (
        <Spinner center />
      ) : data?.filters.length ? (
        <div className="shared-filter-grid">
          {data.filters.map((f) => (
            <FilterCard key={f.id} filter={f} onEdit={() => setEditing(f)} />
          ))}
        </div>
      ) : filtered ? (
        <Empty icon="🔎" title={t('没有匹配的共享筛选器')} />
      ) : (
        <Empty icon="👥" title={t('还没有共享的筛选器')}>
          {t('在筛选页保存筛选器时选择「所有人」或「指定项目」即可共享。')}
        </Empty>
      )}
      {editing && <SaveFilterModal filter={editing} onClose={() => setEditing(null)} />}
    </div>
  )
}
