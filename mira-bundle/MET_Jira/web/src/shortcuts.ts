import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useMemo } from 'react'
import { api } from './api'
import type { UserFull } from './types'

/**
 * Personal keyboard shortcuts. A combo is a physical key code (``KeyA``, ``Slash``) optionally
 * prefixed with ``Shift+`` or ``Alt+``; Ctrl/⌘ combinations stay with the browser and the system.
 * Only overrides of the defaults are stored, in the user's server-side preferences.
 */

export type ActionId =
  | 'create' | 'ai' | 'ai_create' | 'palette' | 'shortcuts'
  | 'my_work' | 'projects' | 'search' | 'org' | 'profile' | 'guide' | 'admin'
  | 'summary' | 'board' | 'backlog' | 'list' | 'timeline' | 'reports'
  | 'theme'

export type ActionGroup = 'common' | 'navigate' | 'project' | 'look'

export interface ShortcutAction {
  id: ActionId
  group: ActionGroup
  label: string
  short: string
  hint: string
  glyph: string
  color: string
  adminOnly?: boolean
}

export const ACTION_GROUPS: { id: ActionGroup; label: string }[] = [
  { id: 'common', label: '常用' },
  { id: 'navigate', label: '页面跳转' },
  { id: 'project', label: '当前项目' },
  { id: 'look', label: '外观' },
]

export const ACTIONS: ShortcutAction[] = [
  { id: 'create', group: 'common', label: '创建问题', short: '创建', hint: '打开创建弹窗，在项目内默认选中当前项目', glyph: '＋', color: '#ffd166' },
  { id: 'ai', group: 'common', label: '打开 / 收起 AI 助手', short: 'AI', hint: '在右侧打开 AI 助手，再按一次收起', glyph: '✦', color: '#c3b5ff' },
  { id: 'ai_create', group: 'common', label: '用 AI 创建需求', short: 'AI需求', hint: '粘贴一段描述，由 AI 生成需求草稿', glyph: '✎', color: '#f7b2d9' },
  { id: 'palette', group: 'common', label: '快速跳转', short: '跳转', hint: '搜索问题、人员、项目和命令', glyph: '⌕', color: '#9ad7ff' },
  { id: 'shortcuts', group: 'common', label: '快捷键设置', short: '快捷键', hint: '打开这个键盘编辑器', glyph: '⌘', color: '#d6dbe4' },
  { id: 'my_work', group: 'navigate', label: '我的工作', short: '首页', hint: '回到首页工作台', glyph: '⌂', color: '#a8e6cf' },
  { id: 'projects', group: 'navigate', label: '所有项目', short: '项目', hint: '打开项目列表', glyph: '▦', color: '#a8e6cf' },
  { id: 'search', group: 'navigate', label: '筛选（JQL）', short: '筛选', hint: '打开筛选页，用 JQL 查询问题', glyph: '⧩', color: '#a8e6cf' },
  { id: 'org', group: 'navigate', label: '组织架构', short: '组织', hint: '查看部门与人员', glyph: '⌬', color: '#a8e6cf' },
  { id: 'profile', group: 'navigate', label: '个人主页', short: '主页', hint: '打开我的个人主页', glyph: '☺', color: '#a8e6cf' },
  { id: 'guide', group: 'navigate', label: '使用说明', short: '说明', hint: '打开使用说明', glyph: '?', color: '#a8e6cf' },
  { id: 'admin', group: 'navigate', label: '管理后台', short: '后台', hint: '仅系统管理员', glyph: '⚙', color: '#a8e6cf', adminOnly: true },
  { id: 'summary', group: 'project', label: '摘要', short: '摘要', hint: '当前项目的摘要概览', glyph: '◎', color: '#ffb38a' },
  { id: 'board', group: 'project', label: '看板', short: '看板', hint: '当前项目的看板', glyph: '▥', color: '#ffb38a' },
  { id: 'backlog', group: 'project', label: '待办事项', short: '待办', hint: '当前项目的待办事项与冲刺', glyph: '≡', color: '#ffb38a' },
  { id: 'list', group: 'project', label: '列表视图', short: '列表', hint: '当前项目的表格视图', glyph: '☰', color: '#ffb38a' },
  { id: 'timeline', group: 'project', label: '时间线', short: '时间线', hint: '当前项目按时间排布的史诗与工作', glyph: '⊟', color: '#ffb38a' },
  { id: 'reports', group: 'project', label: '报表', short: '报表', hint: '当前项目的报表', glyph: '◔', color: '#ffb38a' },
  { id: 'theme', group: 'look', label: '切换深色 / 浅色', short: '主题', hint: '在深色和浅色模式之间切换', glyph: '◐', color: '#e2e5ea' },
]

export const ACTION_BY_ID = Object.fromEntries(ACTIONS.map((a) => [a.id, a])) as Record<ActionId, ShortcutAction>

export const DEFAULT_BINDINGS: Partial<Record<ActionId, string>> = {
  create: 'KeyC',
  ai: 'KeyA',
  palette: 'Slash',
  shortcuts: 'Shift+Slash',
}

export type Layer = '' | 'Shift' | 'Alt'
export const LAYERS: Layer[] = ['', 'Shift', 'Alt']

export interface KeyDef {
  code: string
  label: string
  shift?: string
  w?: number
  /** ``sys``: reserved by the app/browser; ``mod``: modifier key. */
  kind?: 'sys' | 'mod'
}

const chars = (codes: string[], labels: string, shifted: string): KeyDef[] =>
  codes.map((code, i) => ({ code, label: labels[i], shift: shifted[i] }))
const letters = (row: string): KeyDef[] => [...row].map((c) => ({ code: `Key${c}`, label: c }))

export const KEY_ROWS: KeyDef[][] = [
  [
    ...chars(['Backquote', 'Digit1', 'Digit2', 'Digit3', 'Digit4', 'Digit5', 'Digit6', 'Digit7', 'Digit8', 'Digit9', 'Digit0', 'Minus', 'Equal'],
      '`1234567890-=', '~!@#$%^&*()_+'),
    { code: 'Backspace', label: 'Backspace', w: 2, kind: 'sys' },
  ],
  [
    { code: 'Tab', label: 'Tab', w: 1.5, kind: 'sys' },
    ...letters('QWERTYUIOP'),
    ...chars(['BracketLeft', 'BracketRight'], '[]', '{}'),
    { code: 'Backslash', label: '\\', shift: '|', w: 1.5 },
  ],
  [
    { code: 'CapsLock', label: 'Caps', w: 1.75, kind: 'sys' },
    ...letters('ASDFGHJKL'),
    ...chars(['Semicolon', 'Quote'], ";'", ':"'),
    { code: 'Enter', label: 'Enter', w: 2.25, kind: 'sys' },
  ],
  [
    { code: 'ShiftLeft', label: 'Shift', w: 2.25, kind: 'mod' },
    ...letters('ZXCVBNM'),
    ...chars(['Comma', 'Period', 'Slash'], ',./', '<>?'),
    { code: 'ShiftRight', label: 'Shift', w: 2.75, kind: 'mod' },
  ],
  [
    { code: 'ControlLeft', label: 'Ctrl', w: 1.25, kind: 'mod' },
    { code: 'MetaLeft', label: 'Win', w: 1.25, kind: 'mod' },
    { code: 'AltLeft', label: 'Alt', w: 1.25, kind: 'mod' },
    { code: 'Space', label: '', w: 6.25, kind: 'sys' },
    { code: 'AltRight', label: 'Alt', w: 1.25, kind: 'mod' },
    { code: 'MetaRight', label: 'Win', w: 1.25, kind: 'mod' },
    { code: 'ContextMenu', label: 'Menu', w: 1.25, kind: 'sys' },
    { code: 'ControlRight', label: 'Ctrl', w: 1.25, kind: 'mod' },
  ],
]

export const ESC_KEY: KeyDef = { code: 'Escape', label: 'Esc', w: 1.25, kind: 'sys' }

export const KEY_BY_CODE = Object.fromEntries([...KEY_ROWS.flat(), ESC_KEY].map((k) => [k.code, k])) as Record<string, KeyDef>

/** Why a non-bindable key is locked; shown when someone clicks or presses it. */
export const SYSTEM_KEYS: Record<string, string> = {
  Escape: 'Esc 用于关闭弹窗、取消编辑',
  Backspace: 'Backspace 用于删除文字',
  Tab: 'Tab 用于在输入框之间切换',
  CapsLock: 'Caps Lock 用于切换大小写',
  Enter: 'Enter 用于保存和发送',
  Space: '空格用于滚动页面和勾选',
  ContextMenu: '菜单键由系统使用',
}

/** Combos the browser already owns (Chrome/Edge on Windows). */
export const BROWSER_COMBOS: Record<string, string> = {
  'Alt+KeyD': 'Alt + D 是浏览器聚焦地址栏',
  'Alt+KeyE': 'Alt + E 会打开浏览器菜单',
  'Alt+KeyF': 'Alt + F 会打开浏览器菜单',
}

export const comboOf = (layer: Layer, code: string) => (layer ? `${layer}+${code}` : code)

export function splitCombo(combo: string): { layer: Layer; code: string } {
  const [first, second] = combo.split('+')
  return second ? { layer: first as Layer, code: second } : { layer: '', code: first }
}

/** Keycap legends of a combo, e.g. ``['Shift', '/']``. */
export function comboKeys(combo: string | null | undefined): string[] {
  if (!combo) return []
  const { layer, code } = splitCombo(combo)
  const label = KEY_BY_CODE[code]?.label ?? code
  return layer ? [layer, label] : [label]
}

export const comboText = (combo: string | null | undefined) => comboKeys(combo).join(' + ')

export function comboFromEvent(e: KeyboardEvent): string | null {
  if (e.ctrlKey || e.metaKey || (e.altKey && e.shiftKey)) return null
  return comboOf(e.altKey ? 'Alt' : e.shiftKey ? 'Shift' : '', e.code)
}

export const isBindableCode = (code: string) => !!KEY_BY_CODE[code] && !KEY_BY_CODE[code].kind

export type Bindings = Record<ActionId, string>

/** Defaults overlaid with the user's overrides ('' = unbound); a custom binding wins over a default on the same combo. */
export function resolveBindings(overrides: Record<string, string> | undefined | null): Bindings {
  const result = Object.fromEntries(ACTIONS.map((a) => [a.id, ''])) as Bindings
  const custom = overrides ?? {}
  const taken = new Set<string>()
  for (const action of ACTIONS) {
    const combo = custom[action.id]
    if (typeof combo === 'string' && (combo === '' || isBindableCode(splitCombo(combo).code)) && !taken.has(combo)) {
      result[action.id] = combo
      if (combo) taken.add(combo)
    }
  }
  for (const action of ACTIONS) {
    const fallback = DEFAULT_BINDINGS[action.id]
    if (!(action.id in custom) && fallback && !taken.has(fallback)) {
      result[action.id] = fallback
      taken.add(fallback)
    }
  }
  return result
}

export function overridesOf(bindings: Bindings): Record<string, string> {
  const out: Record<string, string> = {}
  for (const action of ACTIONS) {
    if ((bindings[action.id] || '') !== (DEFAULT_BINDINGS[action.id] ?? '')) out[action.id] = bindings[action.id] || ''
  }
  return out
}

export const defaultBindings = () => resolveBindings({})

export interface MeResponse {
  user: UserFull
  preferences?: Record<string, unknown>
  portfolio_access?: boolean
  /** Home landing for managers when URL has no view. Admins prefer the larger tower. */
  default_portfolio_view?: 'delivery' | 'ongoing' | null
}

export const fetchMe = () => api.get<MeResponse>('/api/auth/me')

export function useShortcutBindings() {
  const client = useQueryClient()
  const me = useQuery({ queryKey: ['me'], queryFn: fetchMe, staleTime: 60_000 })
  const stored = me.data?.preferences?.shortcuts as Record<string, string> | undefined
  const bindings = useMemo(() => resolveBindings(stored), [stored])
  const save = useCallback(async (next: Bindings) => {
    const result = await api.put<{ preferences: Record<string, unknown> }>('/api/auth/me/preferences', { shortcuts: overridesOf(next) })
    client.setQueryData<MeResponse>(['me'], (old) => (old ? { ...old, preferences: result.preferences } : old))
  }, [client])
  return { bindings, save }
}
