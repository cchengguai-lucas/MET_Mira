import { locale, pick, t, tServer } from './i18n'
import type { IssueType, Priority, ProjectProfile, ProjectSettings, Section } from './types'

export const TYPE_META: Record<IssueType, { zh: string; en: string; color: string }> = {
  requirement: { zh: '需求', en: 'Requirement', color: '#0a84ff' },
  bug: { zh: '缺陷', en: 'Bug', color: '#ff3b30' },
  task: { zh: '任务', en: 'Task', color: '#30b0c7' },
  story: { zh: '故事', en: 'Story', color: '#34c759' },
  epic: { zh: '史诗', en: 'Epic', color: '#af52de' },
  subtask: { zh: '子任务', en: 'Sub-task', color: '#8e8e93' },
}

export const ISSUE_TYPES = Object.keys(TYPE_META) as IssueType[]

/** Stable accent for an epic (no DB color field) — panel dots, tags, board chips. */
const EPIC_ACCENTS = [
  '#af52de', '#5856d6', '#007aff', '#32ade6', '#30b0c7',
  '#34c759', '#ff9500', '#ff2d55', '#a2845e', '#8e8e93',
]

export function epicAccent(id: number | null | undefined): string {
  if (id == null || id <= 0) return EPIC_ACCENTS[0]
  return EPIC_ACCENTS[id % EPIC_ACCENTS.length]
}

/** Jira / Atlaskit priority palette (icon + chart color). */
export const PRIORITY_META: Record<Priority, { zh: string; en: string; color: string; arrow: string }> = {
  highest: { zh: '最高', en: 'Highest', color: '#ff5630', arrow: '⇈' },
  high: { zh: '高', en: 'High', color: '#ff7452', arrow: '↑' },
  medium: { zh: '中', en: 'Medium', color: '#ffab00', arrow: '=' },
  low: { zh: '低', en: 'Low', color: '#0065ff', arrow: '↓' },
  lowest: { zh: '最低', en: 'Lowest', color: '#2684ff', arrow: '⇊' },
}

export const PRIORITIES = Object.keys(PRIORITY_META) as Priority[]

export const SECTION_META: Record<string, { zh: string; en: string; color: string }> = {
  requester: { zh: '需求方填写', en: 'Requester', color: 'var(--sec-requester)' },
  electrical: { zh: '电气组填写', en: 'Electrical team', color: 'var(--sec-electrical)' },
  scheme: { zh: '方案确认', en: 'Scheme confirmation', color: 'var(--sec-scheme)' },
  software: { zh: '软件组填写', en: 'Software', color: 'var(--sec-software)' },
  tester: { zh: '测试方填写', en: 'Tester', color: 'var(--sec-tester)' },
  mechanical: { zh: '机械设计组填写', en: 'Mechanical design', color: 'var(--sec-mechanical)' },
  inspection: { zh: '工艺/装配确认', en: 'Process / assembly check', color: 'var(--sec-inspection)' },
  production: { zh: '生产执行组填写', en: 'Production', color: 'var(--sec-production)' },
  quality: { zh: '质量确认', en: 'Quality', color: 'var(--sec-quality)' },
}

export const ROLE_LABELS: Record<string, string> = {
  admin: '项目管理员',
  requester: '需求提出人',
  electrical: '电气组',
  process: '工艺工程师',
  tester: '测试人员',
  mechanical: '机械设计组',
  production: '生产执行组',
  quality: '质量确认',
  member: '成员',
}

export const FIELD_LABELS: Record<string, string> = {
  type: '类型',
  title: '标题',
  description: '描述',
  status_id: '状态',
  priority: '优先级',
  assignee_id: '负责人',
  tester_id: '测试人',
  parent_id: '父任务',
  epic_id: '史诗',
  sprint_id: '冲刺',
  labels: '标签',
  due_date: '截止日期',
  story_points: '故事点',
  project_name: '项目名称',
  machine_ids: '机台',
  update_scope: '影响范围',
  involves_software: '是否涉及软件',
  software_owner_id: '软件负责人',
  software_ready: '软件是否准备好',
  software_note: '软件交付说明',
  software_jira_key: '软件 Jira 单号',
  scheme_note: '方案说明',
  electrical_scheme_ok: '电气确认',
  process_scheme_ok: '工艺确认',
  software_scheme_ok: '软件确认',
  scheme_locked: '发起方案确认',
  verify_method: '验证方法',
  verify_criteria: '验证标准',
  update_date: '更新日期',
  test_passed: '是否通过',
  remark: '备注',
}

export function typeName(type: string): string {
  const meta = TYPE_META[type as IssueType]
  return meta ? pick(meta) : type
}

export function priorityName(priority: string): string {
  const meta = PRIORITY_META[priority as Priority]
  return meta ? pick(meta) : priority
}

const PROFILE_META: Record<string, { zh: string; en: string }> = {
  electrical: { zh: '电气', en: 'Electrical' },
  mechanical: { zh: '机械', en: 'Mechanical' },
  production: { zh: '生产', en: 'Production' },
}

/** An English label that merely repeats the Chinese one (older template versions) is no translation. */
function hasEnglish(labels: Record<string, string> | undefined): boolean {
  return !!labels?.en && labels.en !== labels.zh
}

/** Picks the current language from template ``labels``, falling back to the shipped translation. */
export function localized(labels: Record<string, string> | undefined, fallback: string): string {
  const zh = labels?.zh || fallback
  return pick({ zh, en: hasEnglish(labels) ? labels!.en : tServer(zh) })
}

/** Template-defined section names win; built-in keys fall back to the shipped translations. */
export function sectionName(section: string, profile?: ProjectProfile): string {
  const defined = profile?.sections.find((s) => s.key === section)
  if (hasEnglish(defined?.labels)) return localized(defined!.labels, defined!.label)
  const meta = SECTION_META[section as Exclude<Section, 'general'>]
  if (meta) return pick(meta)
  return defined ? localized(defined.labels, defined.label) : section
}

export function customFieldLabel(field: { label: string; labels?: Record<string, string> }): string {
  return localized(field.labels, field.label)
}

export function profileName(profile: ProjectProfile): string {
  const meta = PROFILE_META[profile.key]
  return meta ? pick(meta) : profile.label
}

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ? t(ROLE_LABELS[role]) : role
}

export function fieldLabel(field: string, customLabels?: Record<string, string>): string {
  if (FIELD_LABELS[field]) return t(FIELD_LABELS[field])
  if (field.startsWith('custom_fields.')) {
    const key = field.slice('custom_fields.'.length)
    return customLabels?.[key] || key
  }
  return field
}

export function initials(name?: string | null): string {
  if (!name) return '?'
  const trimmed = name.trim()
  if (/^[\u4e00-\u9fa5]/.test(trimmed)) return trimmed.slice(-2)
  const parts = trimmed.split(/[\s._-]+/).filter(Boolean)
  return (parts[0]?.[0] ?? '?').toUpperCase() + (parts[1]?.[0] ?? '').toUpperCase()
}

export const AVATAR_COLORS = ['#0a84ff', '#5e5ce6', '#bf5af2', '#ff375f', '#ff9f0a', '#30d158', '#64d2ff', '#ac8e68']

export function avatarColor(seed?: string | null): string {
  let hash = 0
  for (const ch of seed ?? '') hash = (hash * 31 + ch.charCodeAt(0)) | 0
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length]
}

export function timeAgo(iso?: string | null): string {
  if (!iso) return ''
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 60) return t('刚刚')
  const rtf = new Intl.RelativeTimeFormat(locale(), { numeric: 'always' })
  if (diff < 3600) return rtf.format(-Math.floor(diff / 60), 'minute')
  if (diff < 86400) return rtf.format(-Math.floor(diff / 3600), 'hour')
  if (diff < 86400 * 30) return rtf.format(-Math.floor(diff / 86400), 'day')
  return new Date(iso).toLocaleDateString(locale())
}

export function fmtDate(iso?: string | null): string {
  if (!iso) return ''
  return iso.slice(0, 10)
}

export function fmtDateTime(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return `${d.toLocaleDateString(locale())} ${d.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' })}`
}

export function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export type HelpTopic = 'sprint' | 'epic' | 'parent' | 'story_points' | 'labels' | 'backlog' | 'sprint_goal' | 'swimlane' | 'wip' | 'jql' | 'quick_filters' | 'burnup' | 'burndown' | 'sla'

/** Plain-language explanations of Jira concepts, shown by `<InfoTip>`. */
export function helpText(topic: HelpTopic): string {
  switch (topic) {
    case 'sprint':
      return t('冲刺（Sprint）：固定时长（通常 1–2 周）的迭代周期。加入冲刺表示计划在这段时间内完成，未加入的问题留在待办事项中。')
    case 'epic':
      return t('史诗（Epic）：主题容器，用「史诗」字段归类需求/任务并跟踪进度；不是父任务，不能嵌套史诗，也不计入看板/冲刺工作量。详见名词页「规划模型」。')
    case 'parent':
      return t('父任务：设置后本问题变为子任务（仅一层）；可随父任务换冲刺，父任务关闭前常需先完成子任务。史诗不能当父任务——主题归类请用史诗字段。')
    case 'story_points':
      return t('故事点：衡量工作量的相对估算单位（常用 1、2、3、5、8…），不等同于工时。用于冲刺燃起图、燃尽图和团队速度统计。')
    case 'labels':
      return t('标签：项目共享的关键词目录，可在问题中点选或新建（新建会自动加入目录）。筛选列可按标签过滤，JQL 用 label = "标签名"。')
    case 'backlog':
      return t('待办事项（Backlog）：尚未排入任何冲刺的问题。先在这里排好优先级，再分批拖入冲刺。')
    case 'sprint_goal':
      return t('冲刺目标：用一句话说明本次冲刺要达成的成果，帮助团队在取舍时保持方向一致。')
    case 'swimlane':
      return t('泳道：把看板按行分组（按负责人、机台组、史诗或优先级），每一行是一条泳道，便于分别查看各组进度。')
    case 'wip':
      return t('在制品上限（WIP）：该列最多同时容纳的问题数。超出时列会标红，提醒团队先完成手头工作再开始新工作。留空表示不限制。')
    case 'jql':
      return t('JQL：类似 SQL 的查询语句，例如 type = 需求 AND 机台组 = "CMB-1"。看板只显示满足条件的问题，留空则显示全部。')
    case 'quick_filters':
      return t('快速筛选：显示在看板顶部的一键筛选按钮，每个按钮对应一条 JQL 条件，点击后只显示匹配的问题。')
    case 'burnup':
      return t('燃起图：展示冲刺范围与已完成工作量随时间的变化。范围线上升说明中途加入了工作，完成线与范围线重合即全部完成。')
    case 'burndown':
      return t('燃尽图：展示冲刺内剩余工作量随时间下降的情况。剩余线高于理想线，说明进度落后。')
    case 'sla':
      return t('处理时限（SLA）：从创建或重新打开起，按优先级小时数自动计时，与「截止日期」无关。进入完成类状态后停止；临近时限为“即将超时”，超时为“SLA 已超时”，并通知经办人、报告人和项目管理员。')
  }
}

const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

export function weekdayName(day: number): string {
  return t(WEEKDAYS[day - 1])
}

export const SLA_STATE_LABELS: Record<string, string> = {
  ok: '正常',
  warning: 'SLA 即将超时',
  breached: 'SLA 已超时',
  completed: '已完成',
  paused: '已暂停',
  none: '未设置',
}

/** The SLA definition followed by how this project measures it. */
export function slaHelp(settings?: ProjectSettings): string {
  const lines = [helpText('sla')]
  if (!settings) return lines[0]
  const hours = settings.sla_hours
  if (hours) {
    lines.push(t('本项目时限：{rules}', {
      rules: PRIORITIES.filter((p) => hours[p]).map((p) => `${priorityName(p)} ${hours[p]}h`).join(' · '),
    }))
  }
  if (settings.sla_calendar === 'business') {
    const business = settings.sla_business_hours ?? { start: '09:00', end: '18:00', workdays: [1, 2, 3, 4, 5] }
    lines.push(t('计时方式：仅工作时间 {start}–{end}（{days}），节假日不计', {
      start: business.start, end: business.end, days: business.workdays.map(weekdayName).join('、'),
    }))
  } else {
    lines.push(t('计时方式：自然时间，夜间和周末也计时'))
  }
  lines.push(t('提前 {hours} 小时提醒', { hours: settings.sla_warning_hours ?? 4 }))
  if (settings.sla_paused_statuses?.length) {
    lines.push(t('暂停计时的状态：{statuses}', { statuses: settings.sla_paused_statuses.map((name) => t(name)).join('、') }))
  }
  return [lines[0], lines.slice(1).join('\n')].join('\n\n')
}

/** Brand greeting stays English in both UI languages. */
export function englishGreeting(now = new Date()): string {
  const hour = now.getHours()
  if (hour < 11) return 'Good morning'
  if (hour < 14) return 'Good day'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
}

/** Localize schedule-confidence enums from the API. */
export function confidenceLabel(value: string | null | undefined): string {
  if (value === 'high') return t('高')
  if (value === 'medium') return t('中')
  if (value === 'low') return t('低')
  return value ? t(value) : '—'
}

/** Localize Gate / Milestone style issue labels for the active UI language. */
export function scheduleTagLabel(value: string | null | undefined): string {
  const raw = (value ?? '').trim()
  if (!raw) return t('门节点')
  const key = raw.toLowerCase()
  if (key === 'gate' || raw === '门节点') return t('门节点')
  if (key === 'milestone' || raw === '里程碑') return t('里程碑')
  if (raw === '关键门') return t('关键门')
  return t(raw)
}

/** Signed day count without English ``d`` suffix. */
export function signedDaysLabel(days: number): string {
  const sign = days > 0 ? '+' : ''
  return t('{n} 天', { n: `${sign}${days}` })
}
