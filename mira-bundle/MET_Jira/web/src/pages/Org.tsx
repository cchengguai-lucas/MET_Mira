import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, qs } from '../api'
import { Avatar, Empty, Spinner, Tabs, useDebounced } from '../components/ui'
import { useApp } from '../context'
import { t } from '../i18n'
import type { OrgUnit, UserFull } from '../types'

interface Tree {
  units: OrgUnit[]
  unassigned_count: number
  user_count: number
}

interface UnitDetail {
  unit: OrgUnit
  breadcrumb: { id: number; label: string }[]
  children: OrgUnit[]
  members: UserFull[]
  subtree_member_count: number
  issue_stats: Record<string, number>
}

export function OrgPage() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'directory' ? 'directory' : 'tree'
  const setTab = (value: string) => {
    const next = new URLSearchParams(params)
    next.set('tab', value)
    setParams(next)
  }
  const tree = useQuery({ queryKey: ['org-tree'], queryFn: () => api.get<Tree>('/api/org/tree') })
  // Keep expand state across Directory round-trips so the tree footprint stays stable.
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set())
  return (
    <div className="page page-wide org-page">
      <div className="page-header">
        <div>
          <h1>{t('组织架构')}</h1>
          <p>
            {tree.data ? t('{units} 个部门 / 小组 · {users} 名在职成员', { units: tree.data.units.length, users: tree.data.user_count }) : t('加载中…')}
            {tree.data?.unassigned_count ? ' · ' + t('{n} 人未归属部门', { n: tree.data.unassigned_count }) : ''}
          </p>
        </div>
      </div>
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { value: 'tree', label: t('部门') },
          { value: 'directory', label: t('通讯录') },
        ]}
      />
      <div className="org-work">
        {tab === 'tree' && <TreeView tree={tree.data} expanded={expanded} setExpanded={setExpanded} />}
        {tab === 'directory' && <Directory />}
      </div>
    </div>
  )
}

function TreeView({
  tree,
  expanded,
  setExpanded,
}: {
  tree?: Tree
  expanded: Set<number>
  setExpanded: Dispatch<SetStateAction<Set<number>>>
}) {
  const [params, setParams] = useSearchParams()
  const units = useMemo(() => tree?.units ?? [], [tree])
  const parentOf = useMemo(() => {
    const ids = new Set(units.map((u) => u.id))
    return new Map(units.map((u) => [u.id, u.parent_id != null && ids.has(u.parent_id) ? u.parent_id : null]))
  }, [units])
  const children = useMemo(() => {
    const map = new Map<number | null, OrgUnit[]>()
    for (const u of units) {
      const parent = parentOf.get(u.id) ?? null
      map.set(parent, [...(map.get(parent) ?? []), u])
    }
    return map
  }, [units, parentOf])
  const roots = useMemo(() => children.get(null) ?? [], [children])
  const selected = Number(params.get('unit')) || roots[0]?.id
  const highlight = params.get('person')
  const treeRef = useRef<HTMLDivElement>(null)

  const ancestors = useCallback(
    (id: number) => {
      const chain: number[] = []
      for (let cur = parentOf.get(id) ?? null; cur != null; cur = parentOf.get(cur) ?? null) chain.push(cur)
      return chain
    },
    [parentOf],
  )

  useEffect(() => {
    if (!selected) return
    setExpanded((prev) => {
      const missing = [...roots.map((r) => r.id), ...ancestors(selected)].filter((id) => !prev.has(id))
      return missing.length ? new Set([...prev, ...missing]) : prev
    })
    requestAnimationFrame(() => treeRef.current?.querySelector('.tree-node.active')?.scrollIntoView({ block: 'nearest' }))
  }, [selected, ancestors, roots])

  const select = (id: number, person?: string) => {
    const next = new URLSearchParams(params)
    next.set('unit', String(id))
    if (person) next.set('person', person)
    else next.delete('person')
    setParams(next)
    if (children.get(id)?.length) setExpanded((prev) => (prev.has(id) ? prev : new Set([...prev, id])))
  }
  const toggle = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const parents = units.filter((u) => children.get(u.id)?.length)
  const allOpen = parents.length > 0 && parents.every((u) => expanded.has(u.id))

  const render = (u: OrgUnit) => {
    const kids = children.get(u.id) ?? []
    const isOpen = kids.length > 0 && expanded.has(u.id)
    return (
      <div key={u.id} role="treeitem" aria-expanded={kids.length ? isOpen : undefined} aria-selected={selected === u.id}>
        <div className={`tree-node ${selected === u.id ? 'active' : ''}`} onClick={() => select(u.id)}>
          {kids.length ? (
            <button
              type="button"
              className={`tree-caret ${isOpen ? 'open' : ''}`}
              aria-label={isOpen ? t('收起') : t('展开')}
              onClick={(e) => {
                e.stopPropagation()
                toggle(u.id)
              }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="m9 6 6 6-6 6" />
              </svg>
            </button>
          ) : (
            <span className="tree-caret leaf" aria-hidden="true" />
          )}
          <span className="ellipsis">{u.label}</span>
          {kids.length > 0 && <span className="tree-kids">{kids.length}</span>}
          <span className="count">{u.total_count}</span>
        </div>
        {isOpen && (
          <div className="tree-children" role="group">
            {kids.map(render)}
          </div>
        )}
      </div>
    )
  }

  if (!tree) return <Spinner center />
  if (units.length === 0)
    return (
      <Empty icon="◎" title={t('还没有组织架构数据')}>
        {t('管理员可在「管理后台 → AD 同步」中从公司 AD 同步部门与人员；用户登录时也会自动建立所在部门。')}
      </Empty>
    )
  return (
    <div className="org-layout">
      <div className="card org-sidebar">
        <div className="org-sidebar-head">
          <div style={{ padding: '10px 10px 0' }}>
            <PersonSearch
              onPick={(person) => {
                const unit = person.org_unit_id
                if (unit == null || !parentOf.has(unit)) return false
                select(unit, person.username)
                return true
              }}
            />
          </div>
          <div className="row tree-tools">
            <span className="tiny faint">{t('{n} 个部门', { n: units.length })}</span>
            <span className="spacer" />
            <button
              type="button"
              className="btn ghost sm"
              onClick={() => setExpanded(allOpen ? new Set(selected ? [...roots.map((r) => r.id), ...ancestors(selected)] : []) : new Set(parents.map((u) => u.id)))}
            >
              {allOpen ? t('全部收起') : t('全部展开')}
            </button>
          </div>
        </div>
        <div className="tree" role="tree" ref={treeRef}>
          {roots.map(render)}
        </div>
      </div>
      {selected ? <UnitPanel id={selected} onSelect={(id) => select(id)} highlight={highlight} /> : null}
    </div>
  )
}

/** People search: picking someone opens the department they belong to. */
function PersonSearch({ onPick }: { onPick: (person: UserFull) => boolean }) {
  const navigate = useNavigate()
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const debounced = useDebounced(q.trim(), 200)
  const { data, isFetching } = useQuery({
    queryKey: ['org-person-search', debounced],
    queryFn: () => api.get<{ total: number; users: UserFull[] }>(`/api/users${qs({ q: debounced, limit: 8 })}`),
    enabled: !!debounced,
  })
  const results = debounced ? (data?.users ?? []) : []
  const pick = (person: UserFull) => {
    setQ('')
    setOpen(false)
    if (!onPick(person)) navigate(`/people/${person.username}`)
  }
  return (
    <div className="org-search">
      <input
        className="input sm"
        placeholder={t('搜索人员，打开所在部门')}
        value={q}
        aria-label={t('搜索人员，打开所在部门')}
        aria-expanded={open && !!debounced}
        role="combobox"
        onChange={(e) => {
          setQ(e.target.value)
          setOpen(true)
          setActive(0)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setActive((i) => Math.min(i + 1, results.length - 1))
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setActive((i) => Math.max(i - 1, 0))
          } else if (e.key === 'Enter' && results[active]) {
            e.preventDefault()
            pick(results[active])
          } else if (e.key === 'Escape' && q) {
            e.stopPropagation()
            setQ('')
          }
        }}
      />
      {open && debounced && (
        <div className="org-search-results" role="listbox">
          {results.length === 0 ? (
            <div className="small faint" style={{ padding: '8px 10px' }}>
              {isFetching ? t('搜索中…') : t('没有找到相关人员')}
            </div>
          ) : (
            results.map((u, i) => (
              <div
                key={u.id}
                role="option"
                aria-selected={i === active}
                className={`org-search-item ${i === active ? 'active' : ''}`}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => {
                  e.preventDefault()
                  pick(u)
                }}
              >
                <Avatar user={u} size={26} />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div className="ellipsis">
                    <b>{u.display_name}</b> <span className="tiny faint">{u.username}</span>
                  </div>
                  <div className="tiny faint ellipsis">{[u.title, u.org_path ?? u.department ?? t('未归属部门')].filter(Boolean).join(' · ')}</div>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}

function UnitPanel({ id, onSelect, highlight }: { id: number; onSelect: (id: number) => void; highlight?: string | null }) {
  const { user, toast } = useApp()
  const client = useQueryClient()
  const navigate = useNavigate()
  const { data, isLoading } = useQuery({ queryKey: ['org-unit', id], queryFn: () => api.get<UnitDetail>(`/api/org/units/${id}`) })
  const [renaming, setRenaming] = useState<string | null>(null)
  const loaded = !!data
  useEffect(() => {
    if (!loaded || !highlight) return
    const card = document.querySelector<HTMLElement>(`.person-card[data-person="${CSS.escape(highlight)}"]`)
    if (!card) return
    card.scrollIntoView({ block: 'center', behavior: 'smooth' })
    card.classList.remove('found')
    void card.offsetWidth
    card.classList.add('found')
  }, [loaded, highlight, id])
  if (isLoading || !data) return <div className="org-detail"><Spinner center /></div>
  const update = async (body: Record<string, unknown>) => {
    try {
      await api.put(`/api/org/units/${id}`, body)
      client.invalidateQueries({ queryKey: ['org-tree'] })
      client.invalidateQueries({ queryKey: ['org-unit', id] })
      toast(t('已保存'), 'success')
    } catch (err) {
      toast((err as Error).message, 'error')
    }
  }
  return (
    <div className="org-detail col" style={{ gap: 16 }}>
      <div className="card card-pad">
        <div className="breadcrumb">
          {data.breadcrumb.map((b, i) => (
            <span key={b.id}>
              {i > 0 && ' / '}
              <a style={{ cursor: 'pointer' }} onClick={() => onSelect(b.id)}>
                {b.label}
              </a>
            </span>
          ))}
        </div>
        <div className="row">
          {renaming !== null ? (
            <>
              <input className="input" style={{ maxWidth: 320 }} autoFocus value={renaming} onChange={(e) => setRenaming(e.target.value)} placeholder={data.unit.name} />
              <button
                className="btn primary sm"
                onClick={() => {
                  update({ display_name: renaming })
                  setRenaming(null)
                }}
              >
                {t('保存')}
              </button>
              <button className="btn sm" onClick={() => setRenaming(null)}>
                {t('取消')}
              </button>
            </>
          ) : (
            <h2>{data.unit.label}</h2>
          )}
          <span className="spacer" />
          {user.is_admin && renaming === null && (
            <>
              <button className="btn ghost sm" onClick={() => setRenaming(data.unit.display_name ?? '')}>
                {t('修改显示名称')}
              </button>
              <button className="btn ghost sm" onClick={() => update({ hidden: !data.unit.hidden })}>
                {data.unit.hidden ? t('取消隐藏') : t('隐藏此部门')}
              </button>
            </>
          )}
        </div>
        {data.unit.display_name && <div className="tiny faint">{t('AD 名称：{name}', { name: data.unit.name })}</div>}
        <div className="grid grid-4" style={{ marginTop: 14 }}>
          <div>
            <div className="small muted">{t('直属成员')}</div>
            <div className="bold" style={{ fontSize: 22 }}>
              {data.members.filter((m) => m.enabled).length}
            </div>
          </div>
          <div>
            <div className="small muted">{t('含下级')}</div>
            <div className="bold" style={{ fontSize: 22 }}>
              {data.subtree_member_count}
            </div>
          </div>
          <div>
            <div className="small muted">{t('负责中的问题')}</div>
            <div className="bold" style={{ fontSize: 22 }}>
              {(data.issue_stats.todo ?? 0) + (data.issue_stats.doing ?? 0)}
            </div>
          </div>
          <div>
            <div className="small muted">{t('已完成')}</div>
            <div className="bold" style={{ fontSize: 22 }}>
              {data.issue_stats.done ?? 0}
            </div>
          </div>
        </div>
        {data.children.length > 0 && (
          <div className="row wrap" style={{ marginTop: 14 }}>
            <span className="small muted">{t('下级：')}</span>
            {data.children.map((c) => (
              <button key={c.id} className="tag" style={{ cursor: 'pointer', border: 'none' }} onClick={() => onSelect(c.id)}>
                {c.label}
              </button>
            ))}
          </div>
        )}
      </div>
      <div>
        <h3 style={{ marginBottom: 10 }}>{t('成员')}</h3>
        {data.members.length === 0 && <div className="small faint">{t('该部门没有直属成员')}</div>}
        <div className="grid grid-3">
          {data.members.map((m) => (
            <div key={m.id} className="person-card" data-person={m.username} style={{ opacity: m.enabled ? 1 : 0.5 }} onClick={() => navigate(`/people/${m.username}`)}>
              <Avatar user={m} size={40} />
              <div style={{ minWidth: 0 }}>
                <div className="bold ellipsis">
                  {m.display_name} {!m.enabled && <span className="tag">{t('已停用')}</span>}
                </div>
                <div className="small muted ellipsis">{m.title ?? m.username}</div>
                {m.manager && <div className="tiny faint ellipsis">{t('汇报给 {name}', { name: m.manager.display_name })}</div>}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function Directory() {
  const [q, setQ] = useState('')
  const [page, setPage] = useState(0)
  const debounced = useDebounced(q, 250)
  const navigate = useNavigate()
  const { data, isLoading } = useQuery({
    queryKey: ['directory', debounced, page],
    queryFn: () => api.get<{ total: number; users: UserFull[] }>(`/api/users${qs({ q: debounced, limit: 50, offset: page * 50 })}`),
  })
  return (
    <div className="card org-directory">
      <div className="card-header">
        <input className="input sm" style={{ maxWidth: 320 }} placeholder={t('搜索姓名、账号、邮箱、职位、部门')} value={q} onChange={(e) => (setQ(e.target.value), setPage(0))} />
        <span className="spacer" />
        <span className="small muted">{t('共 {n} 人', { n: data?.total ?? 0 })}</span>
      </div>
      <div className="org-directory-body">
        {isLoading ? (
          <Spinner center />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>{t('姓名')}</th>
                <th>{t('职位')}</th>
                <th>{t('部门')}</th>
                <th>{t('直属上级')}</th>
                <th>{t('邮箱')}</th>
                <th>{t('电话')}</th>
              </tr>
            </thead>
            <tbody>
              {data?.users.map((u) => (
                <tr key={u.id} className="clickable" onClick={() => navigate(`/people/${u.username}`)}>
                  <td>
                    <span className="row">
                      <Avatar user={u} size={26} />
                      <span>
                        <div className="bold">{u.display_name}</div>
                        <div className="tiny faint">{u.username}</div>
                      </span>
                    </span>
                  </td>
                  <td className="small">{u.title}</td>
                  <td className="small muted">{u.org_path ?? u.department}</td>
                  <td className="small">{u.manager?.display_name}</td>
                  <td className="small">{u.email && <a href={`mailto:${u.email}`} onClick={(e) => e.stopPropagation()}>{u.email}</a>}</td>
                  <td className="small">{u.phone}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {data && data.total > 50 && (
        <div className="row" style={{ padding: 12, justifyContent: 'center' }}>
          <button className="btn sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
            {t('上一页')}
          </button>
          <span className="small muted">
            {page + 1} / {Math.ceil(data.total / 50)}
          </span>
          <button className="btn sm" disabled={(page + 1) * 50 >= data.total} onClick={() => setPage(page + 1)}>
            {t('下一页')}
          </button>
        </div>
      )}
    </div>
  )
}
