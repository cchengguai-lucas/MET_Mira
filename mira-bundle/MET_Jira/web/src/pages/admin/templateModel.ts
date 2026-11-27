import { t } from '../../i18n'
import { FIELD_LABELS, ROLE_LABELS, typeName } from '../../labels'
import type { IssueType, TemplateDefinition } from '../../types'

export type Def = TemplateDefinition
export type DefRole = Def['roles'][number]
export type DefSection = Def['sections'][number]
export type DefField = Def['fields'][number]
export type DefStatus = Def['statuses'][number]
export type FieldKind = DefField['kind']

export const STEPS = ['overview', 'roles', 'form', 'statuses', 'workflow', 'gates', 'layout', 'publish'] as const
export type Step = typeof STEPS[number]

export const STEP_META: Record<Step, { label: string; hint: string }> = {
  overview: { label: '概览', hint: '名称、说明和模板如何生效' },
  roles: { label: '角色', hint: '项目成员可以被分配的身份' },
  form: { label: '表单', hint: '分区、字段，以及谁来填写' },
  statuses: { label: '状态', hint: '问题会经过的状态及其类别' },
  workflow: { label: '工作流', hint: '各类型允许的流转与测试闭环' },
  gates: { label: '门禁与审批', hint: '进入状态前的必填与会签' },
  layout: { label: '列表布局', hint: '项目列表视图的默认列' },
  publish: { label: '检查与发布', hint: '校验、版本历史与审计' },
}

export const ISSUE_TYPES: IssueType[] = ['requirement', 'bug', 'story', 'task', 'subtask', 'epic']

export const FIELD_KINDS: { value: FieldKind; label: string; hint: string }[] = [
  { value: 'text', label: '单行文本', hint: '编号、名称等短文本' },
  { value: 'textarea', label: '多行文本', hint: '说明、结论等较长文本' },
  { value: 'markdown', label: '富文本', hint: '支持格式、粘贴截图' },
  { value: 'select', label: '下拉选项', hint: '从固定选项中选择一个' },
  { value: 'master_ref', label: '主数据', hint: '从共享主数据（物料/产线/设备/服务）中选择' },
  { value: 'date', label: '日期', hint: '' },
  { value: 'number', label: '数字', hint: '' },
  { value: 'boolean', label: '是 / 否', hint: '' },
  { value: 'user', label: '人员', hint: '选择系统中的用户' },
  { value: 'machine', label: '机台', hint: '选择项目机台' },
]

export const KIND_LABELS: Record<FieldKind, string> = {
  ...Object.fromEntries(FIELD_KINDS.map((k) => [k.value, k.label])),
  builtin: '系统字段',
} as Record<FieldKind, string>

export const CATEGORY_LABELS: Record<DefStatus['category'], string> = {
  todo: '待办',
  doing: '进行中',
  done: '已完成',
}

export const ISSUE_USERS: { value: string; label: string }[] = [
  { value: 'reporter_id', label: '报告人' },
  { value: 'assignee_id', label: '负责人' },
  { value: 'tester_id', label: '测试人' },
  { value: 'software_owner_id', label: '软件负责人' },
]

const GENERAL_FIELD_LABELS: Record<string, string> = {
  ...FIELD_LABELS,
  rank: '排序',
  key: '编号',
  reporter_id: '需求提出人',
  machine_group: '机台组',
}

export const KEY_RE = /^[a-z][a-z0-9_]{0,39}$/

export function roleName(def: Def, key: string | null | undefined): string {
  if (!key) return ''
  const role = def.roles.find((r) => r.key === key)
  const zh = role?.labels.zh
  return t(zh && zh !== key ? zh : ROLE_LABELS[key] ?? key)
}

export function fieldName(def: Def, key: string): string {
  const bare = key.replace(/^custom_fields\./, '')
  const field = def.fields.find((f) => f.key === bare)
  const zh = field?.labels.zh
  return t(zh && zh !== bare ? zh : GENERAL_FIELD_LABELS[bare] ?? bare)
}

export function sectionName(def: Def, key: string | null | undefined): string {
  if (!key) return t('通用')
  return t(def.sections.find((s) => s.key === key)?.labels.zh ?? key)
}

/** Keys of the last published version: projects store data under them, so they stay fixed. */
export function lockedKeys(published: Def | null | undefined) {
  return {
    roles: new Set(published?.roles.map((r) => r.key) ?? []),
    sections: new Set(published?.sections.map((s) => s.key) ?? []),
    fields: new Set(published?.fields.map((f) => f.key) ?? []),
  }
}
export type Locked = ReturnType<typeof lockedKeys>

export function uniqueKey(base: string, taken: Iterable<string>): string {
  const used = new Set(taken)
  for (let n = 1; ; n++) {
    const key = `${base}_${n}`
    if (!used.has(key)) return key
  }
}

export function move<T>(items: T[], index: number, direction: -1 | 1): T[] {
  const target = index + direction
  if (target < 0 || target >= items.length) return items
  const next = [...items]
  ;[next[index], next[target]] = [next[target], next[index]]
  return next
}

const mapGates = (def: Def, fn: (gate: Def['default_gates'][string]) => Def['default_gates'][string]) =>
  Object.fromEntries(Object.entries(def.default_gates).map(([status, gate]) => [status, fn(gate)]))

export function renameRole(def: Def, from: string, to: string): Def {
  if (from === to) return def
  return {
    ...def,
    roles: def.roles.map((r) => (r.key === from ? { ...r, key: to } : r)),
    sections: def.sections.map((s) => (s.role === from ? { ...s, role: to } : s)),
    default_gates: mapGates(def, (g) => ({ ...g, approvals: g.approvals.map((a) => (a.role === from ? { ...a, role: to } : a)) })),
  }
}

export function removeRole(def: Def, key: string): Def {
  return {
    ...def,
    roles: def.roles.filter((r) => r.key !== key),
    sections: def.sections.map((s) => (s.role === key ? { ...s, role: null } : s)),
    default_gates: mapGates(def, (g) => ({ ...g, approvals: g.approvals.filter((a) => a.role !== key) })),
  }
}

export function roleUsage(def: Def, key: string): string[] {
  const uses = def.sections.filter((s) => s.role === key).map((s) => t('分区「{name}」', { name: s.labels.zh }))
  for (const [status, gate] of Object.entries(def.default_gates)) {
    if (gate.approvals.some((a) => a.role === key)) uses.push(t('「{status}」审批', { status }))
  }
  return uses
}

export function renameField(def: Def, from: string, to: string): Def {
  if (from === to) return def
  const swap = (key: string) => (key === from ? to : key === `custom_fields.${from}` ? `custom_fields.${to}` : key)
  return {
    ...def,
    fields: def.fields.map((f) => (f.key === from ? { ...f, key: to } : f)),
    sections: def.sections.map((s) => ({ ...s, fields: s.fields.map(swap) })),
    list_layout: { ...def.list_layout, columns: def.list_layout.columns.map((c) => ({ ...c, field: swap(c.field) })) },
    default_gates: mapGates(def, (g) => ({ ...g, required_fields: g.required_fields.map(swap) })),
  }
}

export function removeField(def: Def, key: string): Def {
  const keep = (k: string) => k !== key && k !== `custom_fields.${key}`
  return {
    ...def,
    fields: def.fields.filter((f) => f.key !== key),
    sections: def.sections.map((s) => ({ ...s, fields: s.fields.filter(keep) })),
    list_layout: { ...def.list_layout, columns: def.list_layout.columns.filter((c) => keep(c.field)) },
    default_gates: mapGates(def, (g) => ({ ...g, required_fields: g.required_fields.filter(keep) })),
  }
}

/** Move a field to ``section`` at ``index`` (end when omitted); the field's own record follows. */
export function placeField(def: Def, key: string, section: string, index?: number): Def {
  return {
    ...def,
    fields: def.fields.map((f) => (f.key === key ? { ...f, section } : f)),
    sections: def.sections.map((s) => {
      const rest = s.fields.filter((k) => k !== key)
      if (s.key !== section) return { ...s, fields: rest }
      const at = index === undefined ? rest.length : Math.max(0, Math.min(index, rest.length))
      return { ...s, fields: [...rest.slice(0, at), key, ...rest.slice(at)] }
    }),
  }
}

export function renameSection(def: Def, from: string, to: string): Def {
  if (from === to) return def
  return {
    ...def,
    sections: def.sections.map((s) => (s.key === from ? { ...s, key: to } : s)),
    fields: def.fields.map((f) => (f.section === from ? { ...f, section: to } : f)),
    list_layout: { ...def.list_layout, columns: def.list_layout.columns.map((c) => (c.section === from ? { ...c, section: to } : c)) },
  }
}

export function removeSection(def: Def, key: string): Def {
  const doomed = new Set(def.sections.find((s) => s.key === key)?.fields ?? [])
  let next: Def = { ...def, sections: def.sections.filter((s) => s.key !== key) }
  for (const field of doomed) next = removeField(next, field)
  return {
    ...next,
    list_layout: { ...next.list_layout, columns: next.list_layout.columns.map((c) => (c.section === key ? { ...c, section: undefined } : c)) },
  }
}

export function renameStatus(def: Def, from: string, to: string): Def {
  if (from === to) return def
  const swap = (name: string) => (name === from ? to : name)
  const workflows = Object.fromEntries(Object.entries(def.workflows).map(([type, rules]) => [
    type,
    Object.fromEntries(Object.entries(rules ?? {}).map(([source, targets]) => [swap(source), targets.map(swap)])),
  ])) as Def['workflows']
  const gates = Object.fromEntries(Object.entries(def.default_gates).map(([status, gate]) => [swap(status), gate]))
  const defaults = { ...def.defaults }
  for (const key of ['test_status', 'pass_status', 'fail_return_status', 'dev_complete_status'] as const) {
    if (defaults[key] === from) defaults[key] = to
  }
  return { ...def, statuses: def.statuses.map((s) => (s.name === from ? { ...s, name: to } : s)), workflows, default_gates: gates, defaults }
}

export function removeStatus(def: Def, name: string): Def {
  const workflows = Object.fromEntries(Object.entries(def.workflows).map(([type, rules]) => [
    type,
    Object.fromEntries(Object.entries(rules ?? {}).filter(([source]) => source !== name)
      .map(([source, targets]) => [source, targets.filter((x) => x !== name)])),
  ])) as Def['workflows']
  const gates = { ...def.default_gates }
  delete gates[name]
  const defaults = { ...def.defaults }
  for (const key of ['test_status', 'pass_status', 'fail_return_status', 'dev_complete_status'] as const) {
    if (defaults[key] === name) defaults[key] = null
  }
  return { ...def, statuses: def.statuses.filter((s) => s.name !== name), workflows, default_gates: gates, defaults }
}

export function statusUsage(def: Def, name: string): number {
  return ISSUE_TYPES.filter((type) => {
    const rules = def.workflows[type] ?? {}
    return name in rules || Object.values(rules).some((targets) => targets.includes(name))
  }).length
}

export function toggleTransition(def: Def, type: IssueType, from: string, to: string): Def {
  const rules = { ...(def.workflows[type] ?? {}) }
  const current = rules[from] ?? []
  const next = current.includes(to) ? current.filter((x) => x !== to) : [...current, to]
  if (next.length) rules[from] = next
  else delete rules[from]
  return { ...def, workflows: { ...def.workflows, [type]: rules } }
}

export type Issue = { step: Step; message: string }

/** Everything the publish endpoint rejects, reported all at once and attributed to an editor step. */
export function validate(def: Def): Issue[] {
  const issues: Issue[] = []
  const add = (step: Step, message: string) => issues.push({ step, message })
  if (!def.labels.zh?.trim()) add('overview', t('请填写模板中文名称'))

  const roleKeys = def.roles.map((r) => r.key)
  if (!roleKeys.includes('admin')) add('roles', t('必须保留「项目管理员」(admin) 角色'))
  for (const role of def.roles) {
    if (!KEY_RE.test(role.key)) add('roles', t('角色「{name}」的 Key 只能用小写字母、数字和下划线，且以字母开头', { name: role.labels.zh || role.key }))
    if (!role.labels.zh?.trim()) add('roles', t('角色 {key} 缺少中文名称', { key: role.key }))
  }
  if (new Set(roleKeys).size !== roleKeys.length) add('roles', t('角色 Key 不能重复'))

  const sectionKeys = def.sections.map((s) => s.key)
  if (!def.sections.length) add('form', t('至少需要一个分区'))
  if (new Set(sectionKeys).size !== sectionKeys.length) add('form', t('分区 Key 不能重复'))
  for (const section of def.sections) {
    if (!section.labels.zh?.trim()) add('form', t('有分区缺少名称'))
    if (section.role && !roleKeys.includes(section.role)) add('form', t('分区「{name}」引用了已删除的角色', { name: section.labels.zh }))
    if (!section.issue_types.length) add('form', t('分区「{name}」没有勾选适用的问题类型', { name: section.labels.zh }))
  }
  const fieldKeys = def.fields.map((f) => f.key)
  if (new Set(fieldKeys).size !== fieldKeys.length) add('form', t('字段 Key 不能重复'))
  const claimed = def.sections.flatMap((s) => s.fields)
  for (const field of def.fields) {
    const name = field.labels.zh || field.key
    if (!/^[a-z][a-z0-9_]*$/i.test(field.key)) add('form', t('字段「{name}」的 Key 无效', { name }))
    if (!field.labels.zh?.trim()) add('form', t('字段 {key} 缺少中文名称', { key: field.key }))
    if (field.kind === 'select' && !field.options.length) add('form', t('下拉字段「{name}」还没有选项', { name }))
    if (field.kind === 'master_ref' && !field.master_kind) add('form', t('主数据字段「{name}」需要选择主数据类别', { name }))
    if (!claimed.includes(field.key)) add('form', t('字段「{name}」不属于任何分区', { name }))
  }
  if (new Set(claimed).size !== claimed.length) add('form', t('有字段同时出现在多个分区'))
  const aliases = def.fields.flatMap((f) => f.aliases ?? [])
  if (aliases.some((a) => fieldKeys.includes(a)) || new Set(aliases).size !== aliases.length) add('form', t('字段别名不能与字段 Key 冲突或重复'))

  const names = def.statuses.map((s) => s.name)
  if (!names.length) add('statuses', t('至少需要一个状态'))
  if (names.some((n) => !n.trim())) add('statuses', t('有状态没有名称'))
  if (new Set(names).size !== names.length) add('statuses', t('状态名称不能重复'))
  const done = new Set(def.statuses.filter((s) => s.category === 'done').map((s) => s.name))
  if (!done.size) add('statuses', t('至少需要一个「已完成」类别的状态'))

  for (const type of ISSUE_TYPES) {
    const rules = def.workflows[type]
    const label = typeName(type)
    if (!rules || !Object.keys(rules).length) {
      add('workflow', t('「{type}」还没有配置流转', { type: label }))
      continue
    }
    if (Object.entries(rules).some(([s, targets]) => !names.includes(s) || targets.some((x) => !names.includes(x)))) {
      add('workflow', t('「{type}」的流转引用了不存在的状态', { type: label }))
      continue
    }
    const used = new Set([...Object.keys(rules), ...Object.values(rules).flat()])
    const first = names.find((n) => used.has(n))
    const reach = new Set(first ? [first] : [])
    const queue = first ? [first] : []
    while (queue.length) for (const next of rules[queue.pop()!] ?? []) if (!reach.has(next)) { reach.add(next); queue.push(next) }
    if (![...reach].some((n) => done.has(n))) add('workflow', t('「{type}」从第一个状态出发无法到达完成状态', { type: label }))
  }
  const test = [def.defaults.test_status, def.defaults.pass_status, def.defaults.fail_return_status]
  if (test.some(Boolean) && !test.every(Boolean)) add('workflow', t('测试闭环需要同时设置测试中、通过后、不通过后三个状态'))
  if (test.every(Boolean)) {
    for (const type of ISSUE_TYPES) {
      const targets = def.workflows[type]?.[test[0]!]
      if (targets && !(targets.includes(test[1]!) && targets.includes(test[2]!))) {
        add('workflow', t('「{type}」中「{test}」必须能流转到「{pass}」和「{fail}」', { type: typeName(type), test: test[0]!, pass: test[1]!, fail: test[2]! }))
      }
    }
  }

  const known = new Set([...fieldKeys, ...def.general_fields])
  for (const [status, gate] of Object.entries(def.default_gates)) {
    if (!names.includes(status)) add('gates', t('门禁引用了不存在的状态「{status}」', { status }))
    if (gate.required_fields.some((f) => !known.has(f.replace(/^custom_fields\./, '')))) add('gates', t('「{status}」的必填字段包含已删除的字段', { status }))
    if (gate.approvals.some((a) => !roleKeys.includes(a.role))) add('gates', t('「{status}」的审批引用了已删除的角色', { status }))
    if (gate.approvals.some((a) => !(a.count >= 1 && a.count <= 20))) add('gates', t('「{status}」的会签人数必须在 1–20 之间', { status }))
  }

  const columnFields = new Set([...known, 'key', 'reporter_id', 'machine_ids', 'machine_group'])
  for (const column of def.list_layout.columns) {
    if (!columnFields.has(column.field)) add('layout', t('列表列引用了不存在的字段 {field}', { field: column.field }))
    if (!(column.width > 0)) add('layout', t('列「{field}」的宽度必须大于 0', { field: fieldName(def, column.field) }))
  }
  return issues
}
