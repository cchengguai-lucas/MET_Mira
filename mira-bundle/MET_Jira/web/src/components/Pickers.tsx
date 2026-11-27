import { useQuery } from '@tanstack/react-query'
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { api, qs } from '../api'
import { t } from '../i18n'
import { useUsersSearch } from '../queries'
import type { MachineGroup, UserBrief } from '../types'
import { Avatar, FieldIcon, Popover, Select, scopeIcon, slidingThumb, useDebounced } from './ui'

export interface MasterAsset {
  id: number
  kind: 'equipment' | 'line' | 'material' | 'service'
  code: string
  name: string
  active: boolean
  attributes: Record<string, unknown>
}

export function AssetPicker({ kind, value, onChange, disabled, multiple = false }: {
  kind: MasterAsset['kind']
  value: number | number[] | null
  onChange: (value: number | number[] | null) => void
  disabled?: boolean
  multiple?: boolean
}) {
  const { data } = useQuery({
    queryKey: ['master-assets', kind, 'active'],
    queryFn: () => api.get<{ assets: MasterAsset[] }>(
      `/api/assets${qs({ kind })}`,
    ),
  })
  const selected = new Set(Array.isArray(value) ? value : value ? [value] : [])
  const assets = data?.assets ?? []
  const options = assets.map((asset) => (
    <option key={asset.id} value={asset.id}>
      {asset.code} · {asset.name}
    </option>
  ))
  if (multiple) {
    return (
      <AssetMultiPicker
        assets={assets}
        selected={selected}
        disabled={disabled}
        onChange={(ids) => onChange(ids)}
      />
    )
  }
  return (
    <Select className="select" disabled={disabled} value={String([...selected][0] ?? '')}
      onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      <option value="">—</option>
      {options}
    </Select>
  )
}

function AssetMultiPicker({
  assets,
  selected,
  disabled,
  onChange,
}: {
  assets: MasterAsset[]
  selected: Set<number>
  disabled?: boolean
  onChange: (value: number[]) => void
}) {
  const anchor = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const chosen = assets.filter((asset) => selected.has(asset.id))
  const query = q.trim().toLowerCase()
  const matches = (asset: MasterAsset) => {
    if (!query) return true
    return asset.code.toLowerCase().includes(query) || asset.name.toLowerCase().includes(query)
  }
  const listed = assets.filter((asset) => matches(asset))
  const toggle = (id: number) => {
    const next = new Set(selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    onChange([...next])
  }
  const close = () => {
    setOpen(false)
    setQ('')
  }
  const openMenu = () => {
    if (disabled) return
    setOpen(true)
    queueMicrotask(() => searchRef.current?.focus())
  }
  const chip = (asset: MasterAsset, removable: boolean) => (
    <span
      key={asset.id}
      className={`asset-chip${asset.active ? '' : ' inactive'}`}
      title={`${asset.code} · ${asset.name}`}
    >
      <span className="ellipsis">{asset.name || asset.code}</span>
      {removable && (
        <button
          type="button"
          className="asset-chip-x"
          aria-label={t('移除 {name}', { name: asset.name || asset.code })}
          onClick={(e) => {
            e.stopPropagation()
            toggle(asset.id)
          }}
        >
          ×
        </button>
      )}
    </span>
  )
  return (
    <div className="asset-multi">
      <div
        ref={anchor}
        className={`asset-multi-trigger ${open ? 'open' : ''} ${disabled ? 'readonly' : ''}`}
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => (open ? close() : openMenu())}
        onKeyDown={(e) => {
          if (disabled) return
          if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') {
            e.preventDefault()
            openMenu()
          }
        }}
      >
        <span className="asset-multi-chips">
          {chosen.length === 0 && <span className="placeholder">{t('选择…')}</span>}
          {chosen.map((asset) => chip(asset, !disabled))}
        </span>
        {!disabled && <span className="caret faint" aria-hidden>▾</span>}
      </div>
      {open && !disabled && (
        <Popover anchor={anchor} onClose={close} fit="anchor" minWidth={280} className="menu-pop select-pop asset-multi-pop">
          <div className="asset-multi-search" onClick={(e) => e.stopPropagation()}>
            <input
              ref={searchRef}
              className="input"
              value={q}
              placeholder={t('搜索编码或名称')}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') close()
              }}
            />
          </div>
          <div className="asset-multi-head">
            <span className="tiny muted">{t('已选 {n} 个', { n: chosen.length })}</span>
            <span className="spacer" />
            <button type="button" className="btn ghost sm" disabled={!chosen.length} onClick={() => onChange([])}>
              {t('清空')}
            </button>
          </div>
          <div className="asset-multi-list">
            {listed.length === 0 ? (
              <div className="menu-empty faint small">{query ? t('没有匹配的条目') : t('暂无选项')}</div>
            ) : (
              listed.map((asset) => {
                const on = selected.has(asset.id)
                return (
                  <button
                    key={asset.id}
                    type="button"
                    className={`menu-item ${on ? 'selected' : ''}`}
                    onClick={() => toggle(asset.id)}
                  >
                    <input type="checkbox" checked={on} readOnly tabIndex={-1} />
                    <span className="select-option-label">
                      <b className="asset-code">{asset.code}</b>
                      <span className="faint"> · {asset.name}</span>
                      {!asset.active ? ` (${t('已停用')})` : ''}
                    </span>
                  </button>
                )
              })
            )}
          </div>
        </Popover>
      )}
    </div>
  )
}

/** Click-to-open user picker with server-side search. */
export function UserPicker({
  value,
  onChange,
  trigger,
  allowClear = true,
  placeholder,
  disabled,
  block,
  softwareOf,
  project,
  field,
}: {
  value?: UserBrief | null
  onChange: (user: UserBrief | null) => void
  trigger?: ReactNode
  allowClear?: boolean
  placeholder?: string
  disabled?: boolean
  /** Stretch a custom trigger to fill its container (e.g. a table cell). */
  block?: boolean
  /** Project key: only offer that project's software department. */
  softwareOf?: string
  /** Project key used with `field` to offer only the matching project role. */
  project?: string
  field?: 'assignee_id' | 'tester_id' | 'member'
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const debounced = useDebounced(q, 200)
  const { data, isFetching } = useUsersSearch(debounced, open, { softwareOf, project, field })
  const anchor = useRef<HTMLSpanElement>(null)
  const stretch = Boolean(trigger ? block : true)
  const hint = softwareOf
    ? t('搜索软件部门人员')
    : field === 'assignee_id'
      ? t('搜索本项目可负责的成员')
      : field === 'tester_id'
        ? t('搜索本项目的测试成员')
        : field === 'member'
          ? t('搜索本项目成员')
          : t('搜索姓名或账号')
  const empty = q.trim()
    ? t('没有匹配的人员')
    : softwareOf
      ? t('软件部门中没有匹配的人员')
      : field === 'assignee_id'
        ? t('本项目还没有可负责的成员')
        : field === 'tester_id'
          ? t('本项目还没有测试成员')
          : field === 'member'
            ? t('本项目还没有成员')
            : t('没有匹配的人员')
  return (
    <div
      className={`popover-anchor user-picker${block ? ' block' : ''}`}
      style={{ width: stretch ? '100%' : undefined, height: block ? '100%' : undefined }}
    >
      <span ref={anchor} onClick={() => !disabled && setOpen((o) => !o)} style={{ width: '100%', height: block ? '100%' : undefined, display: 'inline-flex' }}>
        {trigger ?? (
          <button type="button" className="input row user-picker-trigger" style={{ textAlign: 'left', cursor: disabled ? 'default' : 'pointer' }} disabled={disabled}>
            {value ? (
              <>
                <Avatar user={value} size={20} /> <span className="ellipsis">{value.display_name}</span>
              </>
            ) : (
              <span className="faint row" style={{ gap: 6 }}>
                <Avatar user={null} size={20} /> {t('未分配')}
              </span>
            )}
          </button>
        )}
      </span>
      {open && (
        <Popover anchor={anchor} onClose={() => setOpen(false)} minWidth={280} className="user-pop">
          <input
            className="input user-pop-search"
            autoFocus
            placeholder={placeholder ?? hint}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <div className="user-pop-list">
            {allowClear && (
              <button
                className="menu-item"
                onClick={() => {
                  onChange(null)
                  setOpen(false)
                }}
              >
                <Avatar user={null} size={20} /> {t('未分配')}
              </button>
            )}
            {isFetching && !data && <div className="menu-label">{t('搜索中…')}</div>}
            {data?.users.map((u) => (
              <button
                key={u.id}
                className={`menu-item ${value?.id === u.id ? 'active' : ''}`}
                onClick={() => {
                  onChange(u)
                  setOpen(false)
                  setQ('')
                }}
              >
                <Avatar user={u} size={22} />
                <span style={{ minWidth: 0 }}>
                  <div className="ellipsis">{u.display_name}</div>
                  <div className="tiny faint ellipsis">{u.org_path ?? u.username}</div>
                </span>
              </button>
            ))}
            {data && data.users.length === 0 && <div className="menu-label">{empty}</div>}
          </div>
        </Popover>
      )}
    </div>
  )
}

/** How many chips fit in `box`, measured from an invisible full-width copy so the visible row never reflows. */
function useFitCount(box: RefObject<HTMLElement | null>, measure: RefObject<HTMLElement | null>, deps: unknown[]): number {
  const [count, setCount] = useState(Infinity)
  useLayoutEffect(() => {
    const fit = () => {
      const width = box.current?.clientWidth ?? 0
      const chips = Array.from(measure.current?.children ?? []) as HTMLElement[]
      const n = chips.findIndex((chip) => chip.offsetLeft + chip.offsetWidth > width)
      setCount(n === -1 ? chips.length : Math.max(1, n))
    }
    fit()
    const observer = new ResizeObserver(fit)
    if (box.current) observer.observe(box.current)
    return () => observer.disconnect()
  }, deps)
  return count
}

/** Machine multi-select grouped by 机台组; `variant="field"` renders the trigger like a form input. */
export function MachinePicker({
  groups,
  value,
  onChange,
  disabled,
  onAddMachine,
  variant = 'inline',
  wrap = false,
}: {
  groups: MachineGroup[]
  value: number[]
  onChange: (ids: number[]) => void
  disabled?: boolean
  onAddMachine?: (groupId: number, name: string) => Promise<void>
  variant?: 'inline' | 'field'
  /** Show every chip on as many lines as needed instead of collapsing the overflow into "+N". */
  wrap?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [adding, setAdding] = useState<{ group: number; name: string } | null>(null)
  const anchor = useRef<HTMLDivElement>(null)
  const chipBox = useRef<HTMLSpanElement>(null)
  const chipMeasure = useRef<HTMLSpanElement>(null)
  const selected = new Set(value)
  const all = groups.flatMap((g) => g.machines.map((m) => ({ ...m, group: g.name })))
  const chosen = all.filter((m) => selected.has(m.id))
  const label = (m: (typeof all)[number]) => `${m.group}/${t(m.name)}`
  const fitted = useFitCount(chipBox, chipMeasure, [chosen.map((m) => m.id).join(), disabled, wrap])
  const visible = wrap ? chosen.length : fitted
  const chip = (m: (typeof all)[number], removable: boolean) => (
    <span key={m.id} className="machine-chip" title={label(m)}>
      <span className="ellipsis">
        <span className="nowrap">{m.group}/</span>
        {wrap && <wbr />}
        {t(m.name).replace(/-/g, '\u2011')}
      </span>
      {removable && (
        <button
          type="button"
          className="machine-chip-x"
          aria-label={t('移除 {name}', { name: label(m) })}
          onClick={(e) => {
            e.stopPropagation()
            toggle(m.id)
          }}
        >
          ×
        </button>
      )}
    </span>
  )
  const toggle = (id: number) => onChange(selected.has(id) ? value.filter((v) => v !== id) : [...value, id])
  const close = () => {
    setOpen(false)
    setAdding(null)
  }
  const setGroup = (ids: number[], on: boolean) => {
    const rest = value.filter((v) => !ids.includes(v))
    onChange(on ? [...rest, ...ids] : rest)
  }

  return (
    <div style={{ width: '100%', minWidth: 0 }}>
      <div
        ref={anchor}
        className={`machine-trigger as-${variant} ${wrap ? 'wrap' : ''} ${open ? 'open' : ''} ${disabled ? 'readonly' : ''}`}
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={chosen.map(label).join(', ')}
        onClick={() => !disabled && (open ? close() : setOpen(true))}
        onKeyDown={(e) => {
          if (!disabled && (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown')) {
            e.preventDefault()
            setOpen(true)
          }
        }}
      >
        <span className="machine-trigger-chips" ref={chipBox}>
          {chosen.length === 0 && (
            <span className="placeholder row" style={{ gap: 6 }}>
              <FieldIcon name="machines" size={13} />
              {t('选择机台')}
            </span>
          )}
          {chosen.slice(0, visible).map((m) => chip(m, !disabled))}
          {!wrap && (
            <span className="machine-chip-measure" ref={chipMeasure} aria-hidden>
              {chosen.map((m) => chip(m, !disabled))}
            </span>
          )}
        </span>
        {chosen.length > visible && <span className="machine-more">+{chosen.length - visible}</span>}
        {!disabled && <span className="caret faint">▾</span>}
      </div>
      {open && (
        <Popover anchor={anchor} onClose={close} minWidth={600} className="machine-pop">
          <div className="machine-pop-head">
            <b>{t('选择机台')}</b>
            <span className="faint small">{t('已选 {n} 个', { n: chosen.length })}</span>
            <span className="spacer" />
            <button type="button" className="btn ghost sm" disabled={chosen.length === 0} onClick={() => onChange([])}>
              {t('清空')}
            </button>
          </div>
          <div className="machine-pop-body">
            {groups.map((g) => {
              const shown = g.machines.filter((m) => m.active || selected.has(m.id))
              const ids = shown.map((m) => m.id)
              const full = ids.length > 0 && ids.every((id) => selected.has(id))
              return (
                <div key={g.id} className="machine-group">
                  <div className="machine-group-name ellipsis" title={g.name}>
                    {g.name}
                  </div>
                  <div className="machine-group-items">
                    {shown.map((m) => (
                      <button
                        key={m.id}
                        type="button"
                        className={`machine-option ${selected.has(m.id) ? 'on' : ''}`}
                        aria-pressed={selected.has(m.id)}
                        onClick={() => toggle(m.id)}
                      >
                        {t(m.name)}
                      </button>
                    ))}
                    {adding?.group === g.id && onAddMachine ? (
                      <form
                        className="row"
                        style={{ gap: 6 }}
                        onSubmit={async (e) => {
                          e.preventDefault()
                          if (adding.name.trim()) await onAddMachine(g.id, adding.name.trim())
                          setAdding(null)
                        }}
                      >
                        <input className="input sm w-short" autoFocus placeholder={t('新机台名称')} value={adding.name} onChange={(e) => setAdding({ ...adding, name: e.target.value })} />
                        <button className="btn sm primary">{t('添加')}</button>
                      </form>
                    ) : (
                      onAddMachine && (
                        <button type="button" className="machine-option add" title={t('+ 机台')} onClick={() => setAdding({ group: g.id, name: '' })}>
                          +
                        </button>
                      )
                    )}
                  </div>
                  <button type="button" className="btn ghost sm machine-group-all" disabled={ids.length === 0} onClick={() => setGroup(ids, !full)}>
                    {full ? t('取消全选') : t('全选')}
                  </button>
                </div>
              )
            })}
            {groups.length === 0 && <div className="menu-label">{t('项目尚未配置机台')}</div>}
          </div>
          <div className="machine-pop-foot">
            <span className="tiny faint">{t('点击即可勾选或取消，按 Esc 或点击外部关闭')}</span>
            <span className="spacer" />
            <button type="button" className="btn primary sm" onClick={close}>
              {t('完成')}
            </button>
          </div>
        </Popover>
      )}
    </div>
  )
}

/** Multi-select chips for fixed options (e.g. 更新涉及). */
export function ChipSelect({ options, value, onChange, disabled }: { options: string[]; value: string[]; onChange: (v: string[]) => void; disabled?: boolean }) {
  const set = new Set(value)
  const extra = value.filter((v) => !options.includes(v))
  return (
    <div className="row wrap" style={{ gap: 6 }}>
      {[...options, ...extra].map((option) => (
        <button
          key={option}
          type="button"
          disabled={disabled}
          className="tag"
          style={{
            cursor: disabled ? 'default' : 'pointer',
            border: 'none',
            background: set.has(option) ? 'var(--accent)' : 'var(--surface-3)',
            color: set.has(option) ? '#fff' : 'var(--text)',
            opacity: disabled && !set.has(option) ? 0.5 : 1,
          }}
          onClick={() => onChange(set.has(option) ? value.filter((v) => v !== option) : [...value, option])}
        >
          <span className="scope-icon" aria-hidden>{scopeIcon(option)}</span>
          {t(option)}
        </button>
      ))}
    </div>
  )
}

const labelsKey = (list: string[]) => list.join('\0')

/**
 * Jira-like label field: selected chips + compact searchable dropdown.
 * Edits apply locally first; parent `onChange` is debounced so remove/add feel instant.
 */
export function LabelPicker({
  options,
  value,
  onChange,
  disabled,
  allowCreate = true,
  /** ms to wait before notifying parent; 0 = immediate (create form / cell editors). */
  commitDelay = 320,
}: {
  options: string[]
  value: string[]
  onChange: (v: string[]) => void
  disabled?: boolean
  allowCreate?: boolean
  commitDelay?: number
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [draft, setDraft] = useState(value)
  const anchor = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange
  const committed = useRef(labelsKey(value))
  const valueKey = labelsKey(value)
  const draftKey = labelsKey(draft)

  // Parent caught up (save finished) or changed elsewhere — resync without fighting local edits.
  useEffect(() => {
    if (valueKey !== committed.current) {
      setDraft(value)
      committed.current = valueKey
    }
  }, [value, valueKey])

  // Debounce persistence so chip remove/add doesn't wait on the network.
  useEffect(() => {
    if (draftKey === committed.current) return
    if (commitDelay <= 0) {
      committed.current = draftKey
      onChangeRef.current(draft)
      return
    }
    const timer = window.setTimeout(() => {
      committed.current = draftKey
      onChangeRef.current(draft)
    }, commitDelay)
    return () => window.clearTimeout(timer)
  }, [draft, draftKey, commitDelay])

  const selected = new Set(draft)
  const catalog = [...new Set([...options, ...draft.filter((v) => !options.includes(v))])]
  const q = query.trim().toLowerCase()
  const available = catalog.filter((lab) => !selected.has(lab))
  const filtered = q ? available.filter((lab) => lab.toLowerCase().includes(q)) : available
  const exact = catalog.some((lab) => lab.toLowerCase() === q)
  const canCreate = allowCreate && !!q && !exact

  const add = (lab: string) => {
    const name = lab.trim()
    if (!name || selected.has(name)) return
    setDraft((prev) => [...prev, name])
    setQuery('')
    inputRef.current?.focus()
  }
  const remove = (lab: string) => setDraft((prev) => prev.filter((v) => v !== lab))

  if (disabled) {
    return value.length ? (
      <div className="row wrap" style={{ gap: 4 }}>
        {value.map((lab) => (
          <span key={lab} className="tag">
            {lab}
          </span>
        ))}
      </div>
    ) : (
      <span className="faint">—</span>
    )
  }

  return (
    <div className="label-picker col">
      {draft.length > 0 && (
        <div className="row wrap label-picker-chips">
          {draft.map((lab) => (
            <span key={lab} className="tag">
              {lab}
              <button
                type="button"
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  remove(lab)
                }}
                aria-label={t('移除')}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="popover-anchor label-picker-anchor" ref={anchor}>
        <div
          className={`label-picker-trigger input sm${open ? ' open' : ''}`}
          onClick={() => {
            setOpen(true)
            requestAnimationFrame(() => inputRef.current?.focus())
          }}
        >
          <FieldIcon name="labels" size={13} />
          <input
            ref={inputRef}
            className="label-picker-input"
            value={query}
            placeholder={t('选择标签')}
            onChange={(e) => {
              setQuery(e.target.value)
              setOpen(true)
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                setOpen(false)
                setQuery('')
              }
              if (e.key === 'Enter') {
                e.preventDefault()
                if (filtered[0]) add(filtered[0])
                else if (canCreate) add(query.trim())
              }
              if (e.key === 'Backspace' && !query && draft.length) remove(draft[draft.length - 1])
            }}
          />
          <span className="label-picker-caret" aria-hidden>
            ▾
          </span>
        </div>
        {open && (
          <Popover anchor={anchor} onClose={() => setOpen(false)} minWidth={180} fit="content" className="menu-pop label-picker-pop">
            {filtered.length === 0 && !canCreate ? (
              <div className="small faint" style={{ padding: '10px 12px' }}>
                {catalog.length === 0 ? t('暂无标签，输入后回车创建') : t('未找到匹配项')}
              </div>
            ) : (
              <div className="label-picker-list">
                {filtered.map((lab) => (
                  <button key={lab} type="button" className="menu-item" onClick={() => add(lab)}>
                    {lab}
                  </button>
                ))}
                {canCreate && (
                  <button type="button" className="menu-item" onClick={() => add(query.trim())}>
                    {t('创建「{name}」', { name: query.trim() })}
                  </button>
                )}
              </div>
            )}
          </Popover>
        )}
      </div>
    </div>
  )
}

/** Tri-state yes / no / unset selector. */
const VERDICT_ICONS: Record<string, string> = { true: 'M5 12.5l4.5 4.5L19 7.5', false: 'M7 7l10 10M17 7L7 17', null: 'M7 12h10' }

/** `verdict` colors the options as pass / fail / unset, e.g. for test results. */
export function TriState({ value, onChange, yes = t('是'), no = t('否'), disabled, verdict = false }: { value: boolean | null; onChange: (v: boolean | null) => void; yes?: string; no?: string; disabled?: boolean; verdict?: boolean }) {
  const opts: { v: boolean | null; label: string }[] = [
    { v: true, label: yes },
    { v: false, label: no },
    { v: null, label: t('未填') },
  ]
  return (
    <div className={`segmented tri${verdict ? ' verdict' : ''}`} data-value={String(value)} ref={slidingThumb}>
      {opts.map((o) => (
        <button key={String(o.v)} type="button" disabled={disabled} className={`${value === o.v ? 'active ' : ''}v-${String(o.v)}`} onClick={() => onChange(o.v)}>
          {verdict && (
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d={VERDICT_ICONS[String(o.v)]} />
            </svg>
          )}
          <span className="seg-label">{o.label}</span>
        </button>
      ))}
    </div>
  )
}
