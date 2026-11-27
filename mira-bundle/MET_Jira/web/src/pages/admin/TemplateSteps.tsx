import { Fragment, useCallback, type KeyboardEvent, type ReactNode, useMemo, useRef, useState } from 'react'
import { api } from '../../api'
import { ColorField } from '../../components/ColorField'
import { WorkflowGraphEditor } from '../../components/WorkflowGraphEditor'
import { Segmented, StatusPill, Tabs, TypeIcon, Select } from '../../components/ui'
import { useApp } from '../../context'
import { useUndoableDraft } from '../../hooks/useUndoableDraft'
import { t } from '../../i18n'
import { typeName } from '../../labels'
import { MASTER_KINDS, masterKindLabel } from '../../masterData'
import type { DepartmentTemplate, DepartmentTemplateAudit, DepartmentTemplateVersion, IssueType, Status } from '../../types'
import { flowStatuses } from '../../workflow'
import { lintFlow } from '../../workflowLint'
import {
  CATEGORY_LABELS, type Def, type DefField, type DefSection, FIELD_KINDS, ISSUE_TYPES, ISSUE_USERS, type Issue,
  KEY_RE, KIND_LABELS, type Locked, STEP_META, type Step, fieldName, move, placeField, removeField, removeRole,
  removeSection, removeStatus, renameField, renameRole, renameSection, renameStatus, roleName, roleUsage,
  sectionName, statusUsage, uniqueKey,
} from './templateModel'

export type StepProps = {
  def: Def
  update: (fn: (current: Def) => Def) => void
  locked: Locked
  readOnly: boolean
}

const LOCKED_KEY_HINT = '已发布版本中使用的 Key 不能修改：项目数据按 Key 保存'

function Explain({ children }: { children: ReactNode }) {
  return <div className="tpl-explain small">{children}</div>
}

/** Text input that only commits on blur/Enter; ``onCommit`` returns an error message to reject. */
function CommitInput({ value, onCommit, className = 'input sm', placeholder, ariaLabel }: {
  value: string
  onCommit: (next: string) => string | null
  className?: string
  placeholder?: string
  ariaLabel?: string
}) {
  const [text, setText] = useState(value)
  const [error, setError] = useState('')
  const [base, setBase] = useState(value)
  if (base !== value) {
    setBase(value)
    setText(value)
  }
  const reverting = useRef(false)
  const commit = () => {
    if (reverting.current) { reverting.current = false; return }
    const next = text.trim()
    if (next === value) { setError(''); return }
    const problem = onCommit(next)
    setError(problem ?? '')
    if (problem) setText(value)
  }
  return (
    <span className="tpl-commit">
      <input className={className} value={text} placeholder={placeholder} aria-label={ariaLabel}
        onChange={(e) => setText(e.target.value)} onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') e.currentTarget.blur()
          if (e.key === 'Escape') {
            e.stopPropagation()
            reverting.current = true
            setText(value)
            setError('')
            e.currentTarget.blur()
          }
        }} />
      {error && <span className="tiny err-text">{error}</span>}
    </span>
  )
}

function KeyCell({ value, locked, hint = LOCKED_KEY_HINT, onCommit }: {
  value: string
  locked: boolean
  hint?: string
  onCommit: (next: string) => string | null
}) {
  if (locked) return <code className="tpl-key" title={t(hint)}>{value} 🔒</code>
  return <CommitInput value={value} className="input sm mono" ariaLabel="Key" onCommit={onCommit} />
}

function RowActions({ onUp, onDown, onDelete, deleteDisabled, deleteTitle }: {
  onUp: () => void
  onDown: () => void
  onDelete: () => void
  deleteDisabled?: boolean
  deleteTitle?: string
}) {
  return (
    <span className="tpl-actions">
      <button type="button" className="btn ghost icon sm" title={t('上移')} onClick={onUp}>↑</button>
      <button type="button" className="btn ghost icon sm" title={t('下移')} onClick={onDown}>↓</button>
      <button type="button" className="btn ghost icon sm danger-text" title={deleteTitle ?? t('删除')} disabled={deleteDisabled} onClick={onDelete}>✕</button>
    </span>
  )
}

const keyProblem = (next: string, taken: string[]) => {
  if (!KEY_RE.test(next)) return t('只能用小写字母、数字和下划线，且以字母开头')
  if (taken.includes(next)) return t('Key 已存在')
  return null
}

// ---------------------------------------------------------------- 概览

export function OverviewStep({ def, update, template, draftVersion, onSavedMeta, goto, issues, readOnly }: StepProps & {
  template: DepartmentTemplate
  draftVersion: number
  onSavedMeta: () => void
  goto: (step: Step) => void
  issues: Issue[]
}) {
  const { toast } = useApp()
  const [description, setDescription] = useState(template.description)
  const tiles: { step: Step; label: string; value: number }[] = [
    { step: 'roles', label: '角色', value: def.roles.length },
    { step: 'form', label: '分区', value: def.sections.length },
    { step: 'form', label: '字段', value: def.fields.length },
    { step: 'statuses', label: '状态', value: def.statuses.length },
  ]
  return (
    <div className="col" style={{ gap: 18 }}>
      <section className="tpl-how">
        <b>{t('模板如何生效')}</b>
        <ol>
          <li><b>{t('编辑草稿')}</b><span>{t('你正在编辑 v{n} 草稿。修改会自动保存，但不会影响任何项目。', { n: draftVersion })}</span></li>
          <li><b>{t('发布版本')}</b><span>{t('在「检查与发布」中校验通过后发布，成为当前版本。之后新建项目会使用它。')}</span></li>
          <li><b>{t('已有项目不变')}</b><span>{t('项目固定在创建或上次迁移时的版本，发布不会改动它们的数据和流程。')}</span></li>
          <li><b>{t('按需迁移项目')}</b><span>{t('项目管理员在「项目设置 → 基本信息 → 更换模板」中映射状态、字段和角色后切换到新版本。')}</span></li>
        </ol>
      </section>
      <fieldset disabled={readOnly} className="tpl-fieldset grid grid-2">
        <label className="field">{t('中文名称')}
          <input className="input" value={def.labels.zh ?? ''} onChange={(e) => update((d) => ({ ...d, labels: { ...d.labels, zh: e.target.value } }))} />
        </label>
        <label className="field">{t('英文名称')}
          <input className="input" value={def.labels.en ?? ''} placeholder="English" onChange={(e) => update((d) => ({ ...d, labels: { ...d.labels, en: e.target.value } }))} />
        </label>
        <label className="field">{t('模板 Key')}
          <input className="input mono" value={def.key} disabled title={t('模板 Key 创建后不可修改')} />
        </label>
        <label className="field span-all">{t('模板说明')}
          <textarea className="textarea" rows={2} value={description} placeholder={t('例如：适用于机械设计部门的图纸与试装流程')}
            onChange={(e) => setDescription(e.target.value)}
            onBlur={async () => {
              if (description === template.description) return
              try {
                await api.put(`/api/admin/templates/${template.id}`, { description })
                onSavedMeta()
                toast(t('模板说明已保存'), 'success')
              } catch (error) { toast((error as Error).message, 'error') }
            }} />
          <span className="tiny faint">{t('说明属于模板本身，立即保存，不随版本发布。')}</span>
        </label>
      </fieldset>
      <div className="tpl-tiles">
        {tiles.map((tile) => (
          <button type="button" key={tile.label} className="tpl-tile" onClick={() => goto(tile.step)}>
            <span className="tpl-tile-value">{tile.value}</span>
            <span className="small muted">{t(tile.label)}</span>
          </button>
        ))}
        <button type="button" className={`tpl-tile ${issues.length ? 'danger' : 'ok'}`} onClick={() => goto('publish')}>
          <span className="tpl-tile-value">{issues.length || '✓'}</span>
          <span className="small muted">{issues.length ? t('待处理问题') : t('可以发布')}</span>
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- 角色

export function RolesStep({ def, update, locked, readOnly }: StepProps) {
  const remove = (key: string) => {
    const uses = roleUsage(def, key)
    if (uses.length && !window.confirm(t('「{name}」正在被使用：{uses}。删除后这些引用会被清除，继续吗？', { name: roleName(def, key), uses: uses.join('、') }))) return
    update((d) => removeRole(d, key))
  }
  return (
    <fieldset disabled={readOnly} className="tpl-fieldset col" style={{ gap: 14 }}>
      <Explain>
        {t('角色是成员在项目中的身份。项目管理员在「成员与角色」中给成员分配角色；分区可以指定由某个角色填写，状态审批也按角色会签。「项目管理员」角色必须保留，拥有项目内全部权限。')}
      </Explain>
      <table className="table tpl-table">
        <thead>
          <tr><th>{t('名称')}</th><th>{t('英文名称')}</th><th>Key</th><th>{t('被使用于')}</th><th /></tr>
        </thead>
        <tbody>
          {def.roles.map((role, index) => {
            const uses = roleUsage(def, role.key)
            return (
              <tr key={index}>
                <td><input className="input sm" value={role.labels.zh ?? ''} aria-label={t('名称')}
                  onChange={(e) => update((d) => ({ ...d, roles: d.roles.map((r, i) => (i === index ? { ...r, labels: { ...r.labels, zh: e.target.value } } : r)) }))} /></td>
                <td><input className="input sm" value={role.labels.en ?? ''} placeholder="English" aria-label={t('英文名称')}
                  onChange={(e) => update((d) => ({ ...d, roles: d.roles.map((r, i) => (i === index ? { ...r, labels: { ...r.labels, en: e.target.value } } : r)) }))} /></td>
                <td><KeyCell value={role.key} locked={role.key === 'admin' || locked.roles.has(role.key)}
                  onCommit={(next) => keyProblem(next, def.roles.filter((_, i) => i !== index).map((r) => r.key)) ?? (update((d) => renameRole(d, role.key, next)), null)} /></td>
                <td>{uses.length ? <span className="row wrap" style={{ gap: 4 }}>{uses.map((u) => <span className="tag" key={u}>{u}</span>)}</span> : <span className="faint">—</span>}</td>
                <td>
                  <RowActions
                    onUp={() => update((d) => ({ ...d, roles: move(d.roles, index, -1) }))}
                    onDown={() => update((d) => ({ ...d, roles: move(d.roles, index, 1) }))}
                    onDelete={() => remove(role.key)}
                    deleteDisabled={role.key === 'admin'}
                    deleteTitle={role.key === 'admin' ? t('项目管理员角色不能删除') : undefined}
                  />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div>
        <button type="button" className="btn" onClick={() => update((d) => ({ ...d, roles: [...d.roles, { key: uniqueKey('role', d.roles.map((r) => r.key)), labels: { zh: t('新角色'), en: '' } }] }))}>
          ＋ {t('添加角色')}
        </button>
      </div>
    </fieldset>
  )
}

// ---------------------------------------------------------------- 表单（分区与字段）

export function FormStep({ def, update, locked, readOnly }: StepProps) {
  const [open, setOpen] = useState<string | null>(null)
  const addSection = () => update((d) => ({
    ...d,
    sections: [...d.sections, { key: uniqueKey('section', d.sections.map((s) => s.key)), labels: { zh: t('新分区'), en: '' }, fields: [], role: null, issue_users: [], issue_types: ['requirement'] }],
  }))
  return (
    <fieldset disabled={readOnly} className="tpl-fieldset col" style={{ gap: 16 }}>
      <Explain>
        {t('问题详情按分区显示，每个分区是一组由同一批人填写的字段。可以编辑某分区的人 = 指定角色的成员 + 勾选的问题相关人；项目管理员始终可以编辑全部分区。')}
        <br />
        {t('通用字段（类型、状态、优先级、负责人、父任务、史诗、冲刺、标签、截止日期、故事点）由系统提供，不在这里配置。')}
      </Explain>
      {def.sections.map((section, index) => (
        <SectionCard key={index} index={index} section={section} def={def} update={update} locked={locked}
          open={open} setOpen={setOpen} />
      ))}
      <div>
        <button type="button" className="btn" onClick={addSection}>＋ {t('添加分区')}</button>
      </div>
    </fieldset>
  )
}

function SectionCard({ index, section, def, update, locked, open, setOpen }: {
  index: number
  section: DefSection
  def: Def
  update: StepProps['update']
  locked: Locked
  open: string | null
  setOpen: (key: string | null) => void
}) {
  const patch = (fn: (s: DefSection) => DefSection) =>
    update((d) => ({ ...d, sections: d.sections.map((s, i) => (i === index ? fn(s) : s)) }))
  const editors = [
    section.role ? t('「{role}」角色成员', { role: roleName(def, section.role) }) : null,
    ...ISSUE_USERS.filter((u) => section.issue_users.includes(u.value)).map((u) => t('该问题的{who}', { who: t(u.label) })),
  ].filter(Boolean)
  const addField = () => {
    const key = uniqueKey('field', def.fields.map((f) => f.key))
    update((d) => ({
      ...d,
      fields: [...d.fields, { key, labels: { zh: t('新字段'), en: '' }, kind: 'text', required: false, options: [], section: section.key, storage: 'extra', excel: true, report: false }],
      sections: d.sections.map((s, i) => (i === index ? { ...s, fields: [...s.fields, key] } : s)),
    }))
    setOpen(key)
  }
  const removeThis = () => {
    const message = section.fields.length
      ? t('删除分区「{name}」会同时删除其中的 {n} 个字段，继续吗？', { name: section.labels.zh, n: section.fields.length })
      : t('删除分区「{name}」？', { name: section.labels.zh })
    if (window.confirm(message)) update((d) => removeSection(d, section.key))
  }
  return (
    <section className="tpl-section">
      <header className="tpl-section-head">
        <span className="tpl-section-index">{index + 1}</span>
        <input className="input tpl-title-input" value={section.labels.zh} aria-label={t('分区名称')}
          onChange={(e) => patch((s) => ({ ...s, labels: { ...s.labels, zh: e.target.value } }))} />
        <input className="input sm" style={{ maxWidth: 180 }} value={section.labels.en ?? ''} placeholder="English" aria-label={t('英文名称')}
          onChange={(e) => patch((s) => ({ ...s, labels: { ...s.labels, en: e.target.value } }))} />
        <KeyCell value={section.key} locked={locked.sections.has(section.key)}
          onCommit={(next) => keyProblem(next, def.sections.filter((_, i) => i !== index).map((s) => s.key)) ?? (update((d) => renameSection(d, section.key, next)), null)} />
        <span className="spacer" />
        <RowActions
          onUp={() => update((d) => ({ ...d, sections: move(d.sections, index, -1) }))}
          onDown={() => update((d) => ({ ...d, sections: move(d.sections, index, 1) }))}
          onDelete={removeThis}
          deleteTitle={t('删除分区')}
        />
      </header>
      <div className="tpl-section-rules">
        <div className="tpl-rule">
          <span className="tpl-rule-label">{t('谁来填写')}</span>
          <div className="row wrap" style={{ gap: 8 }}>
            <Select className="select sm" value={section.role ?? ''} onChange={(e) => patch((s) => ({ ...s, role: e.target.value || null }))}>
              <option value="">{t('不指定角色')}</option>
              {def.roles.filter((r) => r.key !== 'admin').map((r) => <option key={r.key} value={r.key}>{roleName(def, r.key)}</option>)}
            </Select>
            <span className="small muted">{t('以及该问题的')}</span>
            {ISSUE_USERS.map((u) => (
              <label key={u.value} className="tag tpl-check">
                <input type="checkbox" checked={section.issue_users.includes(u.value)}
                  onChange={(e) => patch((s) => ({ ...s, issue_users: e.target.checked ? [...s.issue_users, u.value] : s.issue_users.filter((v) => v !== u.value) }))} />
                {t(u.label)}
              </label>
            ))}
          </div>
          <span className="tiny faint">
            {editors.length
              ? t('可编辑：{who}，以及项目管理员。', { who: editors.join('、') })
              : t('未指定任何人：只有项目管理员可以编辑这个分区。')}
          </span>
        </div>
        <div className="tpl-rule">
          <span className="tpl-rule-label">{t('适用类型')}</span>
          <div className="row wrap" style={{ gap: 6 }}>
            {ISSUE_TYPES.map((type) => (
              <label key={type} className={`tag tpl-check ${section.issue_types.includes(type) ? 'on' : ''}`}>
                <input type="checkbox" checked={section.issue_types.includes(type)}
                  onChange={(e) => patch((s) => ({ ...s, issue_types: e.target.checked ? [...s.issue_types, type] : s.issue_types.filter((v) => v !== type) }))} />
                <TypeIcon type={type} size={14} /> {typeName(type)}
              </label>
            ))}
          </div>
          <span className="tiny faint">{t('按分区分工填写主要用于「需求」；其他类型的问题由项目成员和相关人整体编辑。')}</span>
        </div>
      </div>
      <div className="tpl-fields">
        {section.fields.length === 0 && <div className="small faint tpl-empty">{t('这个分区还没有字段')}</div>}
        {section.fields.map((key, position) => {
          const field = def.fields.find((f) => f.key === key)
          if (!field) return null
          return (
            <Fragment key={key}>
              <FieldRow field={field} def={def} open={open === key}
                onToggle={() => setOpen(open === key ? null : key)}
                onUp={() => update((d) => placeField(d, key, section.key, position - 1))}
                onDown={() => update((d) => placeField(d, key, section.key, position + 1))}
                onDelete={() => {
                  if (key === 'title') return
                  const message = locked.fields.has(key)
                    ? t('「{name}」已在发布版本中使用。删除后，项目迁移到新版本时需要决定这些数据如何处理（保留为历史数据或清空）。继续吗？', { name: field.labels.zh })
                    : t('删除字段「{name}」？', { name: field.labels.zh })
                  if (window.confirm(message)) update((d) => removeField(d, key))
                }} />
              {open === key && <FieldEditor field={field} def={def} update={update} locked={locked} />}
            </Fragment>
          )
        })}
        <button type="button" className="btn ghost sm tpl-add-field" onClick={addField}>＋ {t('添加字段')}</button>
      </div>
    </section>
  )
}

function fieldSummary(field: DefField): string {
  if (field.kind === 'select') return t('{n} 个选项', { n: field.options.length })
  if (field.kind === 'master_ref') return field.master_kind ? masterKindLabel(field.master_kind) : t('未选类别')
  return ''
}

function FieldRow({ field, def, open, onToggle, onUp, onDown, onDelete }: {
  field: DefField
  def: Def
  open: boolean
  onToggle: () => void
  onUp: () => void
  onDown: () => void
  onDelete: () => void
}) {
  const summary = fieldSummary(field)
  return (
    <div className={`tpl-field-row ${open ? 'open' : ''}`}>
      <button type="button" className="tpl-field-main" onClick={onToggle} aria-expanded={open}>
        <span className="tpl-caret">{open ? '▾' : '▸'}</span>
        <b>{fieldName(def, field.key)}</b>
        {field.required && <span className="err-text">*</span>}
        <span className="badge">{t(KIND_LABELS[field.kind])}</span>
        {summary && <span className="tiny muted">{summary}</span>}
        <span className="spacer" />
        {field.excel === false && <span className="tiny faint">{t('不进 Excel')}</span>}
        {field.report && <span className="tiny faint">{t('报表统计')}</span>}
        <code className="tiny faint">{field.key}</code>
      </button>
      <RowActions onUp={onUp} onDown={onDown} onDelete={onDelete}
        deleteDisabled={field.key === 'title'} deleteTitle={field.key === 'title' ? t('标题是必需的系统字段') : undefined} />
    </div>
  )
}

function FieldEditor({ field, def, update, locked }: {
  field: DefField
  def: Def
  update: StepProps['update']
  locked: Locked
}) {
  const patch = (fn: (f: DefField) => DefField) =>
    update((d) => ({ ...d, fields: d.fields.map((f) => (f.key === field.key ? fn(f) : f)) }))
  const builtin = field.kind === 'builtin'
  const [option, setOption] = useState('')
  const addOption = () => {
    const value = option.trim()
    if (value && !field.options.includes(value)) patch((f) => ({ ...f, options: [...f.options, value] }))
    setOption('')
  }
  return (
    <div className="tpl-field-editor">
      <div className="grid grid-3">
        <label className="field">{t('名称')}
          <input className="input sm" value={field.labels.zh} onChange={(e) => patch((f) => ({ ...f, labels: { ...f.labels, zh: e.target.value } }))} />
        </label>
        <label className="field">{t('英文名称')}
          <input className="input sm" value={field.labels.en ?? ''} placeholder="English" onChange={(e) => patch((f) => ({ ...f, labels: { ...f.labels, en: e.target.value } }))} />
        </label>
        <div className="field">{t('Key（Excel、JQL 中使用 cf_Key）')}
          <KeyCell value={field.key} locked={builtin || locked.fields.has(field.key)}
            hint={builtin ? '系统字段的 Key 固定' : LOCKED_KEY_HINT}
            onCommit={(next) => keyProblem(next, def.fields.filter((f) => f.key !== field.key).map((f) => f.key)) ?? (update((d) => renameField(d, field.key, next)), null)} />
        </div>
        <label className="field">{t('类型')}
          {builtin
            ? <input className="input sm" value={t('系统字段（类型固定）')} disabled />
            : (
              <Select className="select sm" value={field.kind} onChange={(e) => {
                const kind = e.target.value as DefField['kind']
                patch((f) => ({
                  ...f, kind,
                  storage: kind === 'master_ref' ? 'master_ref' : f.storage === 'master_ref' ? 'extra' : f.storage,
                  master_kind: kind === 'master_ref' ? f.master_kind ?? null : null,
                  multiple: kind === 'master_ref' ? f.multiple : false,
                  report: kind === 'select' || kind === 'boolean' || kind === 'master_ref' ? f.report ?? true : f.report,
                }))
              }}>
                {FIELD_KINDS.map((k) => <option key={k.value} value={k.value} title={t(k.hint || k.label)}>{t(k.label)}</option>)}
              </Select>
            )}
          {!builtin && locked.fields.has(field.key) && <span className="tiny faint">{t('已发布字段改类型后，历史数据会在项目迁移时按新类型转换')}</span>}
        </label>
        <label className="field">{t('所在分区')}
          <Select className="select sm" value={field.section} onChange={(e) => update((d) => placeField(d, field.key, e.target.value))}>
            {def.sections.map((s) => <option key={s.key} value={s.key}>{s.labels.zh}</option>)}
          </Select>
        </label>
        <label className="field">{t('别名（逗号分隔，用于识别 Excel 表头）')}
          <CommitInput value={(field.aliases ?? []).join(', ')} placeholder={t('例如：图纸编号, 图号')}
            onCommit={(next) => { patch((f) => ({ ...f, aliases: next.split(/[,，]/).map((v) => v.trim()).filter(Boolean) })); return null }} />
        </label>
      </div>
      {field.kind === 'select' && (
        <div className="field">
          {t('选项')}
          <div className="row wrap" style={{ gap: 6 }}>
            {field.options.map((value, i) => (
              <span className="tag" key={value}>
                {value}
                <button type="button" className="btn ghost icon sm" title={t('左移')} onClick={() => patch((f) => ({ ...f, options: move(f.options, i, -1) }))}>‹</button>
                <button type="button" className="btn ghost icon sm" title={t('删除')} onClick={() => patch((f) => ({ ...f, options: f.options.filter((v) => v !== value) }))}>×</button>
              </span>
            ))}
            <input className="input sm w-mid" value={option} placeholder={t('输入选项后回车')}
              onChange={(e) => setOption(e.target.value)} onBlur={addOption}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addOption() } }} />
          </div>
        </div>
      )}
      {field.kind === 'master_ref' && (
        <div className="row wrap" style={{ gap: 12 }}>
          <label className="field" style={{ minWidth: 220 }}>{t('主数据类别')}
            <Select className="select sm" value={field.master_kind ?? ''} onChange={(e) => patch((f) => ({ ...f, storage: 'master_ref', master_kind: (e.target.value || null) as DefField['master_kind'] }))}>
              <option value="">{t('选择类别…')}</option>
              {MASTER_KINDS.map((kind) => <option key={kind} value={kind}>{masterKindLabel(kind)}</option>)}
            </Select>
          </label>
          <label className="row small tpl-check-line">
            <input type="checkbox" checked={!!field.multiple} onChange={(e) => patch((f) => ({ ...f, multiple: e.target.checked }))} />
            {t('允许选择多个')}
          </label>
          <span className="tiny faint">{t('选项来自「管理后台 → 主数据」，停用的条目不会出现在新建选项中。')}</span>
        </div>
      )}
      <div className="row wrap" style={{ gap: 18 }}>
        {field.key === 'title' ? (
          <label className="row small tpl-check-line">
            <input type="checkbox" checked disabled />
            <span><b>{t('必填')}</b> <span className="muted">{t('标题创建时就必须填写，不能取消')}</span></span>
          </label>
        ) : (
          <label className="row small tpl-check-line">
            <input type="checkbox" checked={field.required} onChange={(e) => patch((f) => ({ ...f, required: e.target.checked }))} />
            <span><b>{t('必填')}</b> <span className="muted">{t('问题离开第一个状态前必须填写')}</span></span>
          </label>
        )}
        <label className="row small tpl-check-line">
          <input type="checkbox" checked={field.excel !== false} onChange={(e) => patch((f) => ({ ...f, excel: e.target.checked }))} />
          <span><b>Excel</b> <span className="muted">{t('出现在导入导出表格中')}</span></span>
        </label>
        <label className="row small tpl-check-line">
          <input type="checkbox" checked={!!field.report} onChange={(e) => patch((f) => ({ ...f, report: e.target.checked }))} />
          <span><b>{t('报表')}</b> <span className="muted">{t('在报表概览中统计取值分布')}</span></span>
        </label>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- 状态

export function StatusesStep({ def, update, readOnly }: StepProps) {
  const remove = (name: string) => {
    const used = statusUsage(def, name)
    if (used && !window.confirm(t('「{name}」用于 {n} 种问题类型的流程，删除后相关流转和门禁会一并移除，继续吗？', { name, n: used }))) return
    update((d) => removeStatus(d, name))
  }
  return (
    <fieldset disabled={readOnly} className="tpl-fieldset col" style={{ gap: 14 }}>
      <Explain>
        {t('状态由所有问题类型共享，每种类型在「工作流」中选择自己经过的状态。顺序决定看板列的顺序，各类型流程中排在最前的状态是新问题的初始状态。类别决定统计口径：「已完成」类别计入完成，但名为「已驳回」的状态单独统计为驳回。')}
      </Explain>
      <table className="table tpl-table">
        <thead>
          <tr><th style={{ width: 54 }}>{t('颜色')}</th><th>{t('名称')}</th><th>{t('类别')}</th><th>{t('使用情况')}</th><th /></tr>
        </thead>
        <tbody>
          {def.statuses.map((status, index) => {
            const used = statusUsage(def, status.name)
            return (
              <tr key={index}>
                <td>
                  <ColorField
                    size="sm"
                    value={status.color}
                    onChange={(color) => update((d) => ({
                      ...d,
                      statuses: d.statuses.map((s, i) => (i === index ? { ...s, color } : s)),
                    }))}
                  />
                </td>
                <td>
                  <CommitInput value={status.name} ariaLabel={t('名称')} onCommit={(next) => {
                    if (!next) return t('名称不能为空')
                    if (def.statuses.some((s, i) => i !== index && s.name === next)) return t('已有同名状态')
                    update((d) => renameStatus(d, status.name, next))
                    return null
                  }} />
                </td>
                <td>
                  <Segmented value={status.category} onChange={(category) => update((d) => ({ ...d, statuses: d.statuses.map((s, i) => (i === index ? { ...s, category } : s)) }))}
                    items={(['todo', 'doing', 'done'] as const).map((c) => ({ value: c, label: t(CATEGORY_LABELS[c]) }))} />
                </td>
                <td className="small">{used ? t('{n} 种类型', { n: used }) : <span className="faint">{t('未使用')}</span>}</td>
                <td>
                  <RowActions
                    onUp={() => update((d) => ({ ...d, statuses: move(d.statuses, index, -1) }))}
                    onDown={() => update((d) => ({ ...d, statuses: move(d.statuses, index, 1) }))}
                    onDelete={() => remove(status.name)}
                  />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div>
        <button type="button" className="btn" onClick={() => update((d) => {
          let n = d.statuses.length + 1
          while (d.statuses.some((s) => s.name === t('新状态 {n}', { n }))) n++
          return { ...d, statuses: [...d.statuses, { name: t('新状态 {n}', { n }), category: 'doing', color: '#0a84ff' }] }
        })}>＋ {t('添加状态')}</button>
      </div>
    </fieldset>
  )
}

// ---------------------------------------------------------------- 工作流

export function WorkflowStep({ def, update, readOnly }: StepProps) {
  const [type, setType] = useState<IssueType>('requirement')
  const [flowView, setFlowView] = useState<'diagram' | 'matrix'>('diagram')
  const statuses: Status[] = useMemo(
    () => def.statuses.map((s, i) => ({ id: i, name: s.name, category: s.category, color: s.color, sort_order: i })),
    [def.statuses],
  )
  const rules = def.workflows[type] ?? {}
  const empty = !Object.keys(rules).length
  const used = empty ? [] : flowStatuses(rules, statuses)
  const findings = useMemo(
    () => lintFlow(statuses.map((s) => ({ name: s.name, category: s.category })), rules),
    [statuses, rules],
  )
  const testValues = [def.defaults.test_status, def.defaults.pass_status, def.defaults.fail_return_status]
  const setDefault = (key: 'test_status' | 'pass_status' | 'fail_return_status', value: string) =>
    update((d) => ({ ...d, defaults: { ...d.defaults, [key]: value || null } }))
  const setRulesRaw = useCallback(
    (next: Record<string, string[]>) => update((d) => ({ ...d, workflows: { ...d.workflows, [type]: next } })),
    [type, update],
  )
  const history = useUndoableDraft(rules, setRulesRaw, type)
  const setRules = (next: Record<string, string[]>) => history.push(next)
  const statusSelect = (key: 'test_status' | 'pass_status' | 'fail_return_status') => (
    <Select className="select sm" value={String(def.defaults[key] ?? '')} onChange={(e) => setDefault(key, e.target.value)}>
      <option value="">{t('未设置')}</option>
      {def.statuses.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
    </Select>
  )
  const onKeyDown = (e: KeyboardEvent) => {
    if (readOnly) return
    const mod = e.metaKey || e.ctrlKey
    if (mod && e.key.toLowerCase() === 'z' && !e.shiftKey) {
      e.preventDefault()
      history.undo()
    } else if (mod && (e.key.toLowerCase() === 'y' || (e.key.toLowerCase() === 'z' && e.shiftKey))) {
      e.preventDefault()
      history.redo()
    }
  }
  return (
    <fieldset disabled={readOnly} className="tpl-fieldset col" style={{ gap: 16 }} onKeyDown={onKeyDown}>
      <Explain>
        {t('每种问题类型有独立的流程。在示意图中从状态拖线到另一状态添加流转，或切换到矩阵勾选。问题只能按路径一步一步流转；未出现在流程中的状态，该类型不会经过。')}
      </Explain>
      <div className="row wrap" style={{ gap: 10 }}>
        <Tabs value={type} onChange={setType} items={ISSUE_TYPES.map((tp) => ({
          value: tp,
          label: (
            <span className="row" style={{ gap: 6 }}>
              <TypeIcon type={tp} size={15} />{typeName(tp)}
              {!Object.keys(def.workflows[tp] ?? {}).length && <span className="tiny err-text">●</span>}
            </span>
          ),
        }))} />
        <span className="spacer" />
        <div className="wf-view-toggle" role="group" aria-label={t('工作流视图')}>
          <button type="button" className={flowView === 'diagram' ? 'active' : ''} onClick={() => setFlowView('diagram')}>{t('示意图')}</button>
          <button type="button" className={flowView === 'matrix' ? 'active' : ''} onClick={() => setFlowView('matrix')}>{t('矩阵')}</button>
        </div>
        <Select className="select sm" value="" onChange={(e) => {
          const source = e.target.value as IssueType
          if (source && window.confirm(t('用「{from}」的流程覆盖「{to}」？', { from: typeName(source), to: typeName(type) }))) {
            update((d) => ({ ...d, workflows: { ...d.workflows, [type]: structuredClone(d.workflows[source] ?? {}) } }))
          }
        }}>
          <option value="">{t('从其他类型复制…')}</option>
          {ISSUE_TYPES.filter((tp) => tp !== type).map((tp) => <option key={tp} value={tp}>{typeName(tp)}</option>)}
        </Select>
      </div>
      {empty && (
        <span className="small err-text">{t('「{type}」还没有任何流转，请在示意图中拖线或在矩阵中勾选。', { type: typeName(type) })}</span>
      )}
      {flowView === 'diagram' ? (
        <WorkflowGraphEditor
          key={type}
          statuses={statuses}
          transitions={rules}
          onChange={setRules}
          readOnly={readOnly}
          statusProperties={def.status_properties ?? {}}
          onStatusPropertiesChange={(next) => update((d) => ({ ...d, status_properties: next }))}
          onPatchStatus={(s, changes) => {
            update((d) => {
              let next = d
              if (changes.name && changes.name !== s.name) {
                next = renameStatus(next, s.name, changes.name)
                const props = { ...(next.status_properties ?? {}) }
                if (props[s.name]) {
                  props[changes.name] = props[s.name]
                  delete props[s.name]
                  next = { ...next, status_properties: props }
                }
              }
              next = {
                ...next,
                statuses: next.statuses.map((row) =>
                  row.name === (changes.name ?? s.name)
                    ? {
                        ...row,
                        category: (changes.category ?? row.category) as Def['statuses'][number]['category'],
                        color: changes.color ?? row.color,
                      }
                    : row,
                ),
              }
              return next
            })
          }}
          toolbar={(
            <>
              <button type="button" className="btn sm" disabled={readOnly || !history.canUndo} onClick={history.undo}>{t('撤销')}</button>
              <button type="button" className="btn sm" disabled={readOnly || !history.canRedo} onClick={history.redo}>{t('重做')}</button>
            </>
          )}
        />
      ) : (
        <div className="col" style={{ gap: 8 }}>
          {findings.filter((f) => f.level !== 'info').length > 0 && (
            <button type="button" className="wf-view-warns" onClick={() => setFlowView('diagram')}>
              <span className="wf-warn-icon">⚠</span>
              {t('查看警告 ({n})', { n: findings.filter((f) => f.level !== 'info').length })}
            </button>
          )}
          <div style={{ overflowX: 'auto' }}>
            <table className="table transition-matrix">
              <thead>
                <tr>
                  <th>{t('从 \\ 到')}</th>
                  {statuses.map((s) => <th key={s.id}><StatusPill status={s} /></th>)}
                </tr>
              </thead>
              <tbody>
                {statuses.map((from) => (
                  <tr key={from.id} className={empty || used.includes(from) ? '' : 'unused'}>
                    <td><StatusPill status={from} /></td>
                    {statuses.map((to) => (
                      <td key={to.id}>
                        {from.id === to.id ? <span className="faint">—</span> : (
                          <input type="checkbox" aria-label={`${from.name} → ${to.name}`}
                            checked={(rules[from.name] ?? []).includes(to.name)}
                            onChange={() => {
                              const current = rules[from.name] ?? []
                              const on = !current.includes(to.name)
                              const next = { ...rules, [from.name]: on ? [...current, to.name] : current.filter((x) => x !== to.name) }
                              if (!next[from.name].length) delete next[from.name]
                              setRules(next)
                            }} />
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <section className="tpl-section">
        <header className="tpl-section-head"><b>{t('测试结果自动流转')}</b><span className="small muted">{t('对所有含测试状态的类型生效')}</span></header>
        <div className="tpl-section-rules">
          <div className="row wrap small" style={{ gap: 8 }}>
            {t('问题处于')} {statusSelect('test_status')}
            {t('时，测试人记录「通过」→ 自动流转到')} {statusSelect('pass_status')}
            {t('，记录「不通过」→ 自动流转到')} {statusSelect('fail_return_status')}
          </div>
          <span className="tiny faint">
            {testValues.every(Boolean)
              ? t('进入测试状态前必须指定测试人；三个状态都必须在流程中相连（测试状态能流转到通过和不通过状态）。')
              : t('三个状态都不设置表示该模板没有测试闭环（如机械、生产模板）；要启用必须三个都设置。')}
          </span>
        </div>
      </section>
    </fieldset>
  )
}

// ---------------------------------------------------------------- 门禁与审批

export function GatesStep({ def, update, readOnly }: StepProps) {
  const general = def.general_fields.filter((f) => !['rank', 'type', 'status_id'].includes(f))
  const setGate = (status: string, fn: (gate: Def['default_gates'][string]) => Def['default_gates'][string]) =>
    update((d) => {
      const next = fn(d.default_gates[status] ?? { required_fields: [], approvals: [] })
      const gates = { ...d.default_gates }
      if (next.required_fields.length || next.approvals.length) gates[status] = next
      else delete gates[status]
      return { ...d, default_gates: gates }
    })
  const roles = def.roles
  return (
    <fieldset disabled={readOnly} className="tpl-fieldset col" style={{ gap: 14 }}>
      <Explain>
        {t('这里是新项目的默认门禁，项目管理员之后可以在「项目设置 → 状态与工作流」中调整。')}
        <br />
        {t('必填：进入该状态前这些字段必须有值。审批：进入该状态需要指定角色的成员批准，每个角色达到人数后系统自动流转；任何一人驳回即结束。')}
      </Explain>
      <div className="tpl-gates">
        {def.statuses.slice(1).map((status) => {
          const gate = def.default_gates[status.name] ?? { required_fields: [], approvals: [] }
          const active = gate.required_fields.length > 0 || gate.approvals.length > 0
          return (
            <div className={`tpl-gate ${active ? 'active' : ''}`} key={status.name}>
              <div className="tpl-gate-status">
                <StatusPill status={status} />
                {!active && <span className="tiny faint">{t('无门禁')}</span>}
              </div>
              <div className="tpl-gate-body">
                <div className="row wrap" style={{ gap: 6 }}>
                  <span className="tpl-rule-label">{t('必填字段')}</span>
                  {gate.required_fields.map((field) => (
                    <span className="tag" key={field}>
                      {fieldName(def, field)}
                      <button type="button" className="btn ghost icon sm" onClick={() => setGate(status.name, (g) => ({ ...g, required_fields: g.required_fields.filter((f) => f !== field) }))}>×</button>
                    </span>
                  ))}
                  <Select className="select sm" value="" onChange={(e) => e.target.value && setGate(status.name, (g) => ({ ...g, required_fields: [...g.required_fields, e.target.value] }))}>
                    <option value="">{t('＋ 添加字段')}</option>
                    <optgroup label={t('通用字段')}>
                      {general.filter((f) => !gate.required_fields.includes(f)).map((f) => <option key={f} value={f}>{fieldName(def, f)}</option>)}
                    </optgroup>
                    {def.sections.map((s) => (
                      <optgroup key={s.key} label={s.labels.zh}>
                        {s.fields.filter((f) => !gate.required_fields.includes(f)).map((f) => <option key={f} value={f}>{fieldName(def, f)}</option>)}
                      </optgroup>
                    ))}
                  </Select>
                </div>
                <div className="row wrap" style={{ gap: 6 }}>
                  <span className="tpl-rule-label">{t('审批')}</span>
                  {gate.approvals.map((approval, i) => (
                    <span className="tag tpl-approval" key={i}>
                      <Select className="select sm" value={approval.role} onChange={(e) => setGate(status.name, (g) => ({ ...g, approvals: g.approvals.map((a, j) => (j === i ? { ...a, role: e.target.value } : a)) }))}>
                        {roles.map((r) => <option key={r.key} value={r.key}>{roleName(def, r.key)}</option>)}
                      </Select>
                      {t('需')}
                      <input className="input sm w-num" type="number" min={1} max={20} value={approval.count}
                        onChange={(e) => setGate(status.name, (g) => ({ ...g, approvals: g.approvals.map((a, j) => (j === i ? { ...a, count: Math.max(1, Math.min(20, Number(e.target.value) || 1)) } : a)) }))} />
                      {t('人批准')}
                      <button type="button" className="btn ghost icon sm" onClick={() => setGate(status.name, (g) => ({ ...g, approvals: g.approvals.filter((_, j) => j !== i) }))}>×</button>
                    </span>
                  ))}
                  <button type="button" className="btn ghost sm" disabled={!roles.length}
                    onClick={() => setGate(status.name, (g) => ({ ...g, approvals: [...g.approvals, { role: roles.find((r) => r.key === 'admin')?.key ?? roles[0].key, count: 1 }] }))}>
                    ＋ {t('添加审批角色')}
                  </button>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </fieldset>
  )
}

// ---------------------------------------------------------------- 列表布局

const PSEUDO_COLUMNS = ['key', 'reporter_id', 'machine_group', 'machine_ids']

export function LayoutStep({ def, update, readOnly }: StepProps) {
  const columns = def.list_layout.columns
  const candidates = [
    ...PSEUDO_COLUMNS,
    ...def.sections.flatMap((s) => s.fields),
    ...def.general_fields.filter((f) => f !== 'rank'),
  ].filter((f, i, all) => all.indexOf(f) === i && !columns.some((c) => c.field === f))
  const setColumns = (fn: (cols: typeof columns) => typeof columns) =>
    update((d) => ({ ...d, list_layout: { ...d.list_layout, columns: fn(d.list_layout.columns) } }))
  const total = columns.reduce((sum, c) => sum + (c.width || 0), 0) || 1
  return (
    <fieldset disabled={readOnly} className="tpl-fieldset col" style={{ gap: 14 }}>
      <Explain>
        {t('项目「列表」视图的默认列和顺序。用户可以在列表中自行隐藏列、调整列宽，这些个人设置保存在各自浏览器中。列按所属分区显示分组表头。')}
      </Explain>
      <div className="tpl-layout-preview" aria-hidden>
        {columns.map((c, i) => (
          <span key={i} style={{ flexGrow: c.width, flexBasis: `${(c.width / total) * 100}%` }} title={fieldName(def, c.field)}>
            {fieldName(def, c.field)}
          </span>
        ))}
      </div>
      <table className="table tpl-table">
        <thead><tr><th>#</th><th>{t('列')}</th><th>{t('分组')}</th><th>{t('宽度（px）')}</th><th /></tr></thead>
        <tbody>
          {columns.map((column, index) => (
            <tr key={index}>
              <td className="faint">{index + 1}</td>
              <td><b>{fieldName(def, column.field)}</b> <code className="tiny faint">{column.field}</code></td>
              <td className="small muted">{sectionName(def, column.section)}</td>
              <td><input className="input sm w-short" type="number" min={40} value={column.width}
                onChange={(e) => setColumns((cols) => cols.map((c, i) => (i === index ? { ...c, width: Number(e.target.value) } : c)))} /></td>
              <td>
                <RowActions
                  onUp={() => setColumns((cols) => move(cols, index, -1))}
                  onDown={() => setColumns((cols) => move(cols, index, 1))}
                  onDelete={() => setColumns((cols) => cols.filter((_, i) => i !== index))}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div>
        <Select className="select" value="" onChange={(e) => {
          const field = e.target.value
          if (!field) return
          const section = def.fields.find((f) => f.key === field)?.section
          setColumns((cols) => [...cols, { field, width: 140, ...(section ? { section } : {}) }])
        }}>
          <option value="">{t('＋ 添加列…')}</option>
          {candidates.map((f) => <option key={f} value={f}>{fieldName(def, f)}</option>)}
        </Select>
      </div>
    </fieldset>
  )
}

// ---------------------------------------------------------------- 检查与发布

function diffKeys<T>(before: T[], after: T[], key: (item: T) => string) {
  const a = new Set(before.map(key))
  const b = new Set(after.map(key))
  return { added: [...b].filter((k) => !a.has(k)), removed: [...a].filter((k) => !b.has(k)) }
}

export function PublishStep({ def, template, draft, issues, goto, onPublish, onDiscard, readOnly }: StepProps & {
  template: DepartmentTemplate
  draft: DepartmentTemplateVersion
  issues: Issue[]
  goto: (step: Step) => void
  onPublish: () => void
  onDiscard?: () => void
}) {
  const published = template.current_version?.definition ?? null
  const changes: string[] = []
  if (published) {
    const describe = (label: string, d: { added: string[]; removed: string[] }) => {
      if (d.added.length) changes.push(t('新增{label}：{items}', { label: t(label), items: d.added.join('、') }))
      if (d.removed.length) changes.push(t('删除{label}：{items}', { label: t(label), items: d.removed.join('、') }))
    }
    describe('角色', diffKeys(published.roles, def.roles, (r) => roleName(def, r.key) || r.key))
    describe('分区', diffKeys(published.sections, def.sections, (s) => s.labels.zh))
    describe('字段', diffKeys(published.fields, def.fields, (f) => f.labels.zh || f.key))
    describe('状态', diffKeys(published.statuses, def.statuses, (s) => s.name))
    if (JSON.stringify(published.workflows) !== JSON.stringify(def.workflows)) changes.push(t('工作流有调整'))
    if (JSON.stringify(published.default_gates) !== JSON.stringify(def.default_gates)) changes.push(t('默认门禁与审批有调整'))
    if (JSON.stringify(published.list_layout) !== JSON.stringify(def.list_layout)) changes.push(t('列表布局有调整'))
    if (JSON.stringify(published.labels) !== JSON.stringify(def.labels)) changes.push(t('模板名称有调整'))
  }
  const serverOnly = (draft.validation_errors ?? []).filter(() => issues.length === 0)
  const versionNumber = new Map((template.versions ?? []).map((v) => [v.id, v.version]))
  const actionLabel: Record<DepartmentTemplateAudit['action'], string> = {
    create: t('创建模板'), copy: t('复制模板'), draft: t('创建草稿'), publish: t('发布了版本'),
    enable: t('启用模板'), disable: t('停用模板'), update: t('更新模板信息'), discard: t('放弃了草稿'),
  }
  const byStep = issues.reduce<Record<string, string[]>>((acc, issue) => {
    (acc[issue.step] ??= []).push(issue.message)
    return acc
  }, {})
  return (
    <div className="col" style={{ gap: 18 }}>
      {issues.length ? (
        <div className="callout danger col" style={{ gap: 10 }}>
          <b>{t('发布前还需要处理 {n} 个问题', { n: issues.length })}</b>
          {Object.entries(byStep).map(([step, messages]) => (
            <div key={step} className="col" style={{ gap: 4 }}>
              <div className="row" style={{ gap: 8 }}>
                <b className="small">{t(STEP_META[step as Step].label)}</b>
                <button type="button" className="btn ghost sm" onClick={() => goto(step as Step)}>{t('前往修改 →')}</button>
              </div>
              {messages.map((m, i) => <span className="small" key={i}>· {m}</span>)}
            </div>
          ))}
        </div>
      ) : serverOnly.length ? (
        <div className="callout danger">{serverOnly.map((e) => <div className="small" key={e}>{e}</div>)}</div>
      ) : (
        <div className="callout success"><b>{t('检查通过，可以发布 v{n}', { n: draft.version })}</b></div>
      )}
      <section className="tpl-section">
        <header className="tpl-section-head"><b>{published ? t('与当前发布版本 v{n} 相比', { n: template.current_version!.version }) : t('首次发布')}</b></header>
        <div className="tpl-section-rules">
          {published
            ? changes.length ? changes.map((c) => <span className="small" key={c}>· {c}</span>) : <span className="small muted">{t('没有变化')}</span>
            : <span className="small muted">{t('该模板还没有发布过，发布后即可在新建项目时选择。')}</span>}
        </div>
      </section>
      <section className="tpl-section">
        <header className="tpl-section-head"><b>{t('发布后会发生什么')}</b></header>
        <div className="tpl-section-rules small">
          <span>· {t('v{n} 成为当前版本，新建项目时使用它（模板已停用时不可选）。', { n: draft.version })}</span>
          <span>· {t('已经在使用旧版本的项目不受影响，需要时由项目管理员在项目设置中「更换模板」迁移。')}</span>
          <span>· {t('发布后的版本不能再修改；之后再编辑会自动创建 v{n} 草稿。', { n: draft.version + 1 })}</span>
        </div>
        <div className="row wrap" style={{ gap: 8, padding: '0 16px 14px' }}>
          <button type="button" className="btn primary" disabled={issues.length > 0 || readOnly} onClick={onPublish}>{t('发布 v{n}', { n: draft.version })}</button>
          {onDiscard && <button type="button" className="btn danger" onClick={onDiscard}>{t('放弃草稿')}</button>}
        </div>
      </section>
      <div className="grid grid-2" style={{ alignItems: 'start' }}>
        <section className="col" style={{ gap: 6 }}>
          <h4>{t('版本历史')}</h4>
          {[...(template.versions ?? [])].sort((a, b) => b.version - a.version).map((version) => (
            <div className="row tpl-history" key={version.id}>
              <b>v{version.version}</b>
              <span className={`badge ${version.status === 'published' ? 'success' : 'accent'}`}>
                {version.status === 'published' ? (version.id === template.current_version_id ? t('当前版本') : t('已发布')) : t('草稿')}
              </span>
              <span className="small muted">{new Date(version.published_at ?? version.created_at).toLocaleString()}</span>
            </div>
          ))}
        </section>
        <section className="col" style={{ gap: 6 }}>
          <h4>{t('操作审计')}</h4>
          {(template.audits ?? []).length ? (template.audits ?? []).slice(0, 30).map((audit) => (
            <div className="row tpl-history" key={audit.id} style={{ flexWrap: 'wrap' }}>
              <b className="small">{actionLabel[audit.action] ?? audit.action}</b>
              {(() => {
                const number = (audit.version_id && versionNumber.get(audit.version_id)) || audit.detail.version_number
                return number ? <span className="badge">v{String(number)}</span> : null
              })()}
              <span className="small">{audit.actor_name ?? t('未知用户')}</span>
              <span className="small muted">{new Date(audit.created_at).toLocaleString()}</span>
            </div>
          )) : <div className="small muted">{t('暂无操作记录')}</div>}
        </section>
      </div>
    </div>
  )
}
