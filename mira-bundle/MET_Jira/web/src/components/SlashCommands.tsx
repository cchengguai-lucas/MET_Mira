import { useMemo } from 'react'
import { t } from '../i18n'
import { leaveAnimation } from './leave'

/** Trigger `/query` only after line start or whitespace (avoids path-like false positives). */
export const SLASH_TRIGGER = /(^|[\n\s])\/([^\s/]{0,40})$/

export type SlashAction =
  | 'h1' | 'h2' | 'h3'
  | 'bold' | 'italic'
  | 'ul' | 'ol' | 'task'
  | 'quote' | 'code' | 'codeblock' | 'hr'
  | 'image'

export interface SlashCommand {
  id: SlashAction
  /** Search keys (zh + en tokens). */
  keys: string[]
  label: () => string
  hint: () => string
  /** Replace `/query` with this; caret offset from insertion start (negative = from end). */
  template: string
  caret?: number
}

export const SLASH_COMMANDS: SlashCommand[] = [
  { id: 'h1', keys: ['h1', 'title', '标题', '一级'], label: () => t('一级标题'), hint: () => '#', template: '# ', caret: 2 },
  { id: 'h2', keys: ['h2', 'heading', '标题', '二级'], label: () => t('二级标题'), hint: () => '##', template: '## ', caret: 3 },
  { id: 'h3', keys: ['h3', '小标题', '三级'], label: () => t('三级标题'), hint: () => '###', template: '### ', caret: 4 },
  { id: 'bold', keys: ['bold', '粗体', 'b'], label: () => t('粗体'), hint: () => '**…**', template: '****', caret: 2 },
  { id: 'italic', keys: ['italic', '斜体', 'i'], label: () => t('斜体'), hint: () => '*…*', template: '**', caret: 1 },
  { id: 'ul', keys: ['ul', 'list', 'bullet', '无序', '列表'], label: () => t('无序列表'), hint: () => '-', template: '- ', caret: 2 },
  { id: 'ol', keys: ['ol', 'numbered', '有序', '数字'], label: () => t('有序列表'), hint: () => '1.', template: '1. ', caret: 3 },
  { id: 'task', keys: ['task', 'todo', 'checkbox', '任务', '待办'], label: () => t('任务列表'), hint: () => '[ ]', template: '- [ ] ', caret: 6 },
  { id: 'quote', keys: ['quote', '引用'], label: () => t('引用'), hint: () => '>', template: '> ', caret: 2 },
  { id: 'code', keys: ['code', 'inline', '行内代码'], label: () => t('行内代码'), hint: () => '`…`', template: '``', caret: 1 },
  { id: 'codeblock', keys: ['codeblock', 'fence', '代码块'], label: () => t('代码块'), hint: () => '```', template: '```\n\n```', caret: 4 },
  { id: 'hr', keys: ['hr', 'divider', '分割线', '分隔'], label: () => t('分割线'), hint: () => '---', template: '\n---\n', caret: 5 },
  { id: 'image', keys: ['image', 'pic', 'screenshot', '图片', '截图'], label: () => t('插入图片'), hint: () => '🖼', template: '', caret: 0 },
]

export function filterSlashCommands(query: string, allowImage: boolean): SlashCommand[] {
  const q = query.trim().toLowerCase()
  return SLASH_COMMANDS.filter((c) => {
    if (c.id === 'image' && !allowImage) return false
    if (!q) return true
    return c.keys.some((k) => k.includes(q) || q.includes(k)) || c.label().toLowerCase().includes(q)
  })
}

/** Pixel position of the caret inside a textarea, relative to the textarea's top-left. */
export function caretPoint(el: HTMLTextAreaElement, caret: number) {
  const style = window.getComputedStyle(el)
  const mirror = document.createElement('div')
  for (const prop of ['boxSizing', 'width', 'padding', 'border', 'fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing', 'wordSpacing', 'textIndent', 'tabSize'] as const) {
    mirror.style[prop] = style[prop]
  }
  Object.assign(mirror.style, {
    position: 'absolute', visibility: 'hidden', whiteSpace: 'pre-wrap', overflowWrap: 'break-word',
    top: '0', left: '-9999px',
  })
  mirror.textContent = el.value.slice(0, caret)
  const marker = document.createElement('span')
  marker.textContent = '\u200b'
  mirror.appendChild(marker)
  document.body.appendChild(mirror)
  const line = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.5
  const point = { left: marker.offsetLeft, top: marker.offsetTop - el.scrollTop + line }
  mirror.remove()
  return point
}

export function detectSlash(text: string, caret: number): { query: string; start: number } | null {
  const head = text.slice(0, caret)
  const match = SLASH_TRIGGER.exec(head)
  if (!match) return null
  return { query: match[2], start: caret - match[2].length - 1 }
}

/** Replace the `/query` ending at caret with the command template. */
export function applySlashCommand(text: string, caret: number, cmd: SlashCommand): { text: string; caret: number } | null {
  const hit = detectSlash(text, caret)
  if (!hit) return null
  if (cmd.id === 'image') {
    // Drop the slash token; caller opens the file picker.
    const next = text.slice(0, hit.start) + text.slice(caret)
    return { text: next, caret: hit.start }
  }
  const before = text.slice(0, hit.start) + cmd.template
  const after = text.slice(caret)
  const caretAt = hit.start + (cmd.caret ?? cmd.template.length)
  return { text: before + after, caret: caretAt }
}

export function SlashMenu({
  query,
  point,
  active,
  onActive,
  allowImage,
  onPick,
}: {
  query: string
  point: { left: number; top: number }
  active: number
  onActive: (i: number) => void
  allowImage: boolean
  onPick: (cmd: SlashCommand) => void
}) {
  const options = useMemo(() => filterSlashCommands(query, allowImage), [query, allowImage])

  if (options.length === 0) {
    return (
      <div className="menu slash-menu" ref={leaveAnimation} style={{ left: point.left, top: point.top }} onMouseDown={(e) => e.preventDefault()}>
        <div className="faint small mention-empty">{t('没有匹配的操作')}</div>
      </div>
    )
  }

  return (
    <div className="menu slash-menu" ref={leaveAnimation} style={{ left: point.left, top: point.top }} onMouseDown={(e) => e.preventDefault()}>
      <div className="slash-menu-head tiny faint">{t('插入')}</div>
      {options.map((cmd, index) => (
        <button
          key={cmd.id}
          type="button"
          className={`menu-item${index === active ? ' active' : ''}`}
          onMouseEnter={() => onActive(index)}
          onClick={() => onPick(cmd)}
        >
          <span className="slash-menu-label">{cmd.label()}</span>
          <span className="slash-menu-hint faint tiny">{cmd.hint()}</span>
        </button>
      ))}
      <div className="mention-hint tiny faint">{t('↑↓ 选择 · Enter 确认 · Esc 关闭')}</div>
    </div>
  )
}
