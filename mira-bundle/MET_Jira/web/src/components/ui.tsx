import {
  Children, isValidElement, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState,
  type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type ReactElement, type ReactNode,
  type RefObject,
} from 'react'
import { createPortal } from 'react-dom'
import { pick, t } from '../i18n'
import { avatarColor, helpText, initials, TYPE_META, type HelpTopic } from '../labels'
import type { IssueType, Status, SubtaskProgress, UserBrief } from '../types'
import { leaveAnimation, leaveGhost } from './leave'
import { PersonHover } from './PersonHover'

type AvatarUser = Pick<UserBrief, 'display_name' | 'username'> & Partial<Pick<UserBrief, 'avatar_url' | 'avatar_color'>>

/** Jira-style filled glyphs (white on the type color tile) — no single letters. */
const TYPE_SVG: Record<IssueType, ReactNode> = {
  // Requirements doc with folded corner + body lines
  requirement: (
    <path
      fill="currentColor"
      d="M3.2 1.6h6.1L12.8 5v9.4c0 .55-.45 1-1 1H3.2c-.55 0-1-.45-1-1V2.6c0-.55.45-1 1-1zm5.7.9v2.9h3L8.9 2.5zM4.4 7.2h7.2v1.15H4.4V7.2zm0 2.3h7.2v1.15H4.4V9.5zm0 2.3h5v1.15h-5V11.8z"
    />
  ),
  // Beetle (bug)
  bug: (
    <path
      fill="currentColor"
      d="M8 2.2c.9 0 1.55.55 1.8 1.2h1.35v1.1H10.7c.08.28.12.58.12.9v.55h1.85v1.1H10.8c0 .22-.02.43-.06.64l1.9 1.1.55-.95 1 .55-.55.95 1.15.66-.55.95-1.15-.66-.55.95-1-.55.55-.95-1.72-1a3.4 3.4 0 0 1-.92.7V13.5h-1.1v-1.85a3.4 3.4 0 0 1-.92-.7l-1.72 1 .55.95-1 .55-.55-.95-1.15.66-.55-.95 1.15-.66-.55-.95 1 .55.55-.95 1.9-1.1A3.6 3.6 0 0 1 5.2 7.05H3.35v-1.1H5.2V5.4c0-.32.04-.62.12-.9H3.85V3.4H5.2C5.45 2.75 6.1 2.2 7 2.2H8zm0 2.2c-.85 0-1.55.7-1.55 1.65v.9c0 1.1.7 1.95 1.55 1.95S9.55 8.05 9.55 6.95v-.9c0-.95-.7-1.65-1.55-1.65z"
    />
  ),
  // Checkmark in rounded square (task)
  task: (
    <path
      fill="currentColor"
      d="M3.5 2.2h9c.72 0 1.3.58 1.3 1.3v9c0 .72-.58 1.3-1.3 1.3h-9c-.72 0-1.3-.58-1.3-1.3v-9c0-.72.58-1.3 1.3-1.3zm6.55 3.05L7.1 9.35 5.55 7.8l-.95.95 2.5 2.5 4-5.25-.95-.75z"
    />
  ),
  // Bookmark (story — Jira classic)
  story: (
    <path
      fill="currentColor"
      d="M4.2 1.8h7.6c.55 0 1 .45 1 1v11.05c0 .4-.45.65-.78.42L8 11.45 3.98 14.27c-.33.23-.78-.02-.78-.42V2.8c0-.55.45-1 1-1z"
    />
  ),
  // Lightning bolt (epic)
  epic: (
    <path
      fill="currentColor"
      d="M9.6 1.5 4.2 8.4h3.35l-.95 6.1 5.85-7.4H9.2L9.6 1.5z"
    />
  ),
  // Linked branch (sub-task)
  subtask: (
    <path
      fill="currentColor"
      d="M6.2 2.4a1.8 1.8 0 1 1 0 3.6 1.8 1.8 0 0 1 0-3.6zm0 4.7v1.35c0 .72.58 1.3 1.3 1.3h2.7a1.8 1.8 0 1 1 0 1.2H7.5c-1.38 0-2.5-1.12-2.5-2.5V7.1a1.8 1.8 0 0 1 1.2 0zM12.2 11.2a1.8 1.8 0 1 1 0 3.6 1.8 1.8 0 0 1 0-3.6z"
    />
  ),
}

export function Avatar({ user, size = 24, title }: { user?: AvatarUser | null; size?: number; title?: string }) {
  if (!user) {
    return (
      <span className="avatar" style={{ width: size, height: size, background: 'var(--surface-3)', color: 'var(--text-3)', fontSize: size * 0.42 }} title={title ?? t('未分配')}>
        ?
      </span>
    )
  }
  if (user.avatar_url) {
    return <img className="avatar" src={user.avatar_url} alt={user.display_name} title={title ?? user.display_name} style={{ width: size, height: size }} loading="lazy" draggable={false} />
  }
  return (
    <span
      className="avatar"
      title={title ?? user.display_name}
      style={{ width: size, height: size, background: user.avatar_color || avatarColor(user.username), fontSize: size * 0.4 }}
    >
      {initials(user.display_name)}
    </span>
  )
}

export function UserChip({ user, size = 22 }: { user?: UserBrief | null; size?: number }) {
  if (!user) return <span className="faint">{t('未分配')}</span>
  return (
    <PersonHover user={user}>
      <span className="row user-chip">
        <Avatar user={user} size={size} />
        <span className="ellipsis">{user.display_name}</span>
      </span>
    </PersonHover>
  )
}

export function TypeIcon({ type, size = 18 }: { type: IssueType; size?: number }) {
  const meta = TYPE_META[type] ?? TYPE_META.task
  const icon = TYPE_SVG[type] ?? TYPE_SVG.task
  const glyph = Math.max(10, Math.round(size * 0.72))
  return (
    <span
      className="type-icon"
      title={pick(meta)}
      style={{ background: meta.color, width: size, height: size, fontSize: glyph }}
    >
      <svg viewBox="0 0 16 16" width="1em" height="1em" aria-hidden focusable="false">
        {icon}
      </svg>
    </span>
  )
}

export { PriorityIcon } from './PriorityIcon'

/** Shared field / chrome icons — one glyph per concept app-wide. */
export type FieldIconName =
  | 'project' | 'type' | 'title' | 'description' | 'assignee' | 'tester' | 'reporter'
  | 'priority' | 'epic' | 'labels' | 'machines' | 'due' | 'sprint' | 'filter' | 'status'
  | 'watch' | 'scope' | 'create' | 'cancel' | 'camera' | 'link' | 'parent' | 'attachment'
  | 'settings' | 'help' | 'theme' | 'theme-sun' | 'language' | 'shortcuts' | 'chrome' | 'admin' | 'org' | 'bell'
  | 'search' | 'sparkles' | 'pin'

/** Legacy single/multi path glyphs (field labels, etc.). */
const FIELD_PATHS: Partial<Record<FieldIconName, string | string[]>> = {
  project: 'M4 4h6v6H4V4zm10 0h6v6h-6V4zM4 14h6v6H4v-6zm10 0h6v6h-6v-6z',
  type: 'M5 5h14v4H5V5zm0 6h9v3H5v-3zm0 5h14v3H5v-3z',
  title: 'M5 6h14v2H5V6zm0 5h10v2H5v-2zm0 5h14v2H5v-2z',
  description: 'M6 5h12a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zm2 3h8v2H8V8zm0 4h8v2H8v-2zm0 4h5v2H8v-2z',
  assignee: 'M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4zm0 2c-4 0-7 2-7 4v1h14v-1c0-2-3-4-7-4z',
  tester: 'M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4zm0 2c-4 0-7 2-7 4v1h14v-1c0-2-3-4-7-4z',
  reporter: 'M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4zm-7 9c0-3 3-5 7-5h.5M17 16l2 2 3-3',
  priority: 'M12 3l3 7h7l-5.5 4.5L18 22l-6-4-6 4 1.5-7.5L2 10h7z',
  epic: 'M12 2l2.4 7.2H22l-6 4.8 2.2 7L12 16.8 5.8 21l2.2-7-6-4.8h7.6z',
  labels: 'M3 6h9l9 9-6 6-9-9V6zm4.5 2.5a1.5 1.5 0 1 1 0 0.01z',
  machines: 'M4 8h4V4h2v4h4V4h2v4h4v2h-4v4h4v2h-4v4h-2v-4H10v4H8v-4H4v-2h4v-4H4V8zm6 2v4h4v-4h-4z',
  due: 'M7 3v2H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2V3h-2v2H9V3H7zm12 6H5v10h14V9z',
  sprint: 'M4 12a8 8 0 0 1 13-6l2-2v6h-6l2-2a6 6 0 1 0 1.5 6.5',
  filter: 'M4 5h16l-6 7v5l-4 2v-7L4 5z',
  status: 'M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18zm0 4v5l3 2',
  watch: 'M12 5c5 0 9 4.5 10 7-1 2.5-5 7-10 7S3 14.5 2 12c1-2.5 5-7 10-7zm0 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8z',
  scope: 'M4 7h16v2H4V7zm0 5h10v2H4v-2zm0 5h16v2H4v-2z',
  create: 'M12 5v14M5 12h14',
  cancel: 'M6 6l12 12M18 6L6 18',
  camera: 'M9 6l1.5-2h3L15 6h4a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h4zm3 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8z',
  link: 'M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1',
  parent: 'M5 5h6v6H5V5zm8 4h6v10H7v-4',
  attachment: 'M14 3H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V9l-6-6zm0 0v5h5',
}

/**
 * Chrome / settings icons — Lucide (ISC) outlines, Apple-adjacent stroke geometry.
 * @see https://lucide.dev (v0.468.0)
 */
const LUCIDE_GLYPHS: Partial<Record<FieldIconName, ReactNode>> = {
  /* Lucide `cog` — reads clearly at 16–20px (unlike dense `settings` gear). */
  settings: (
    <>
      <path d="M11 10.27 7 3.34" />
      <path d="m11 13.73-4 6.93" />
      <path d="M12 22v-2" />
      <path d="M12 2v2" />
      <path d="M14 12h8" />
      <path d="m17 20.66-1-1.73" />
      <path d="m17 3.34-1 1.73" />
      <path d="M2 12h2" />
      <path d="m20.66 17-1.73-1" />
      <path d="m20.66 7-1.73 1" />
      <path d="m3.34 17 1.73-1" />
      <path d="m3.34 7 1.73 1" />
      <circle cx="12" cy="12" r="2" />
      <circle cx="12" cy="12" r="8" />
    </>
  ),
  bell: (
    <>
      <path d="M10.268 21a2 2 0 0 0 3.464 0" />
      <path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326" />
    </>
  ),
  theme: <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />,
  'theme-sun': (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2" />
      <path d="M12 20v2" />
      <path d="m4.93 4.93 1.41 1.41" />
      <path d="m17.66 17.66 1.41 1.41" />
      <path d="M2 12h2" />
      <path d="M20 12h2" />
      <path d="m6.34 17.66-1.41 1.41" />
      <path d="m19.07 4.93-1.41 1.41" />
    </>
  ),
  language: (
    <>
      <path d="m5 8 6 6" />
      <path d="m4 14 6-6 2-3" />
      <path d="M2 5h12" />
      <path d="M7 2h1" />
      <path d="m22 22-5-10-5 10" />
      <path d="M14 18h6" />
    </>
  ),
  shortcuts: (
    <>
      <rect width="20" height="16" x="2" y="4" rx="2" />
      <path d="M6 8h.01" />
      <path d="M10 8h.01" />
      <path d="M14 8h.01" />
      <path d="M18 8h.01" />
      <path d="M8 12h.01" />
      <path d="M12 12h.01" />
      <path d="M16 12h.01" />
      <path d="M7 16h10" />
    </>
  ),
  chrome: (
    <>
      <rect width="18" height="18" x="3" y="3" rx="2" />
      <path d="M15 3v18" />
    </>
  ),
  admin: (
    <>
      <rect width="7" height="9" x="3" y="3" rx="1" />
      <rect width="7" height="5" x="14" y="3" rx="1" />
      <rect width="7" height="9" x="14" y="12" rx="1" />
      <rect width="7" height="5" x="3" y="16" rx="1" />
    </>
  ),
  org: (
    <>
      <path d="M18 21a8 8 0 0 0-16 0" />
      <circle cx="10" cy="8" r="5" />
      <path d="M22 20c0-3.37-2-6.5-4-8a5 5 0 0 0-.45-8.3" />
    </>
  ),
  help: (
    <>
      <circle cx="12" cy="12" r="10" />
      <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
      <path d="M12 17h.01" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.3-4.3" />
    </>
  ),
  sparkles: (
    <>
      <path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" />
      <path d="M20 3v4" />
      <path d="M22 5h-4" />
      <path d="M4 17v2" />
      <path d="M5 18H3" />
    </>
  ),
  /* Lucide `pin` */
  pin: (
    <>
      <path d="M12 17v5" />
      <path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z" />
    </>
  ),
}

/** iOS Settings–style tint for the colored icon well. */
export const SETTINGS_ICON_TINT: Partial<Record<FieldIconName, string>> = {
  bell: '#ff9f0a',
  theme: '#5856d6',
  'theme-sun': '#ff9f0a',
  chrome: '#007aff',
  language: '#34c759',
  shortcuts: '#8e8e93',
  admin: '#af52de',
  org: '#5ac8fa',
  settings: '#636366',
  help: '#007aff',
}

export function FieldIcon({ name, size = 14 }: { name: FieldIconName; size?: number }) {
  const lucide = LUCIDE_GLYPHS[name]
  const raw = FIELD_PATHS[name]
  const paths = raw == null ? [] : Array.isArray(raw) ? raw : [raw]
  return (
    <span className="field-icon" style={{ width: size, height: size }} aria-hidden>
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={lucide ? 2 : 1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
        overflow="visible"
      >
        {lucide ?? paths.map((d, i) => <path key={i} d={d} />)}
      </svg>
    </span>
  )
}

export function SettingsGlyph({
  name,
  size = 14,
}: {
  name: FieldIconName
  size?: number
}) {
  const tint = SETTINGS_ICON_TINT[name] ?? '#8e8e93'
  return (
    <span className="settings-glyph" style={{ background: tint }} aria-hidden>
      <FieldIcon name={name} size={size} />
    </span>
  )
}

/** Icons for built-in「影响范围」scope chips — same glyph wherever the scope appears. */
export const SCOPE_ICONS: Record<string, string> = {
  硬件: '⬡',
  软件: '</>',
  工作流程: '↻',
  '程序/参数': '⚙',
  '图纸/文档': '▤',
  其他: '···',
}

export function scopeIcon(name: string): string {
  return SCOPE_ICONS[name] ?? '·'
}

export function StatusPill({ status, style }: { status?: Pick<Status, 'name' | 'color'> | null; style?: CSSProperties }) {
  if (!status) return null
  return (
    <span className="pill" title={t(status.name)} style={{ background: `${status.color}22`, color: status.color, ...style }}>
      <span style={{ width: 6, height: 6, borderRadius: 3, background: status.color, flexShrink: 0 }} />
      <span className="ellipsis">{t(status.name)}</span>
    </span>
  )
}

/** Ease a block open and closed. The first paint stays put; later toggles glide. */
export function Fold({
  open, children, className = '', gap = 0,
}: {
  open: boolean
  children: ReactNode
  className?: string
  /** Cancel a parent flex/grid gap while closed, in pixels. */
  gap?: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  const ready = useRef(false)
  useEffect(() => {
    if (!ready.current) {
      ready.current = true
      return
    }
    if (!open || !ref.current) return
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    ref.current.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'nearest' })
  }, [open])
  return (
    <div
      ref={ref}
      className={`fold${open ? ' open' : ''}${className ? ` ${className}` : ''}`}
      style={gap ? { marginTop: open ? 0 : -gap } : undefined}
    >
      <div className="fold-body" inert={!open}>{children}</div>
    </div>
  )
}

export function Spinner({ center }: { center?: boolean }) {
  if (center) {
    return (
      <div className="center">
        <span className="spinner" />
      </div>
    )
  }
  return <span className="spinner" />
}

export function Empty({ icon = '🗂', title, children }: { icon?: string; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <div className="big">{icon}</div>
      <div className="bold" style={{ color: 'var(--text)' }}>
        {title}
      </div>
      {children && <div style={{ marginTop: 6 }}>{children}</div>}
    </div>
  )
}

export function Modal({
  title,
  onClose,
  children,
  footer,
  size,
  icon,
  layer,
}: {
  title: ReactNode
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  size?: 'wide' | 'narrow' | 'full'
  icon?: ReactNode
  /** Absolute overlay inside the modal shell (e.g. discard confirm). */
  layer?: ReactNode
}) {
  useEscape(onClose)
  const titleId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  useDialogFocus(dialogRef)
  return (
    <div className="overlay" ref={leaveAnimation} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={dialogRef}
        className={`modal ${size ?? ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="modal-header">
          {icon}
          <h3 id={titleId} className="modal-title">{title}</h3>
          <button className="btn ghost icon sm" onClick={onClose} aria-label={t('关闭')}>
            ✕
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
        {layer}
      </div>
    </div>
  )
}

export function useEscape(handler: () => void) {
  const ref = useRef(handler)
  ref.current = handler
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') ref.current()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
}

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

/** Trap focus inside a dialog and restore it to the opener when the dialog unmounts. */
export function useDialogFocus(ref: RefObject<HTMLElement | null>, active = true) {
  useEffect(() => {
    if (!active) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const frame = requestAnimationFrame(() => {
      const root = ref.current
      const target = root?.querySelector<HTMLElement>('[autofocus], [data-autofocus]')
        ?? root?.querySelector<HTMLElement>(FOCUSABLE)
        ?? root
      target?.focus()
    })
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return
      const root = ref.current
      if (!root) return
      const items = [...root.querySelectorAll<HTMLElement>(FOCUSABLE)]
        .filter((item) => item.offsetParent !== null)
      if (!items.length) {
        event.preventDefault()
        root.focus()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      cancelAnimationFrame(frame)
      document.removeEventListener('keydown', onKey)
      previous?.focus()
    }
  }, [active, ref])
}

/** Full-screen image viewer; Esc is swallowed so it does not also close the drawer underneath. */
export function Lightbox({ src, alt, onClose }: { src: string; alt?: string; onClose: () => void }) {
  const [closing, setClosing] = useState(false)
  const close = useRef(() => {})
  close.current = () => {
    setClosing(true)
    setTimeout(onClose, 160)
  }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.stopImmediatePropagation()
      close.current()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [])
  const original = src.replace(/[?&]inline=true/, '')
  return createPortal(
    <div
      className={`lightbox${closing ? ' closing' : ''}`}
      onClick={(e) => {
        e.stopPropagation()
        close.current()
      }}
    >
      <img src={src} alt={alt ?? ''} onClick={(e) => e.stopPropagation()} />
      <div className="lightbox-bar" onClick={(e) => e.stopPropagation()}>
        {alt && <span className="ellipsis">{alt}</span>}
        <a href={src} target="_blank" rel="noreferrer">
          {t('新窗口打开')}
        </a>
        <a href={original}>{t('下载')}</a>
      </div>
      <button className="lightbox-close" aria-label={t('关闭')} onClick={(event) => {
        event.stopPropagation()
        close.current()
      }}>
        ✕
      </button>
    </div>,
    document.body,
  )
}

export function useClickOutside<T extends HTMLElement>(open: boolean, onClose: () => void) {
  const ref = useRef<T>(null)
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open, onClose])
  return ref
}

/**
 * Floating panel anchored to an element, rendered on the top layer so scroll containers
 * (modal bodies, table cells) neither clip it nor grow when it opens. Flips above when there is no room below.
 */
export function Popover({
  anchor,
  onClose,
  children,
  minWidth = 0,
  className = '',
  fit = 'anchor',
  align = 'left',
}: {
  anchor: RefObject<HTMLElement | null>
  onClose: () => void
  children: ReactNode
  minWidth?: number
  className?: string
  /** ``anchor``: at least as wide as the anchor; ``content``: as wide as the content needs. */
  fit?: 'anchor' | 'content'
  align?: 'left' | 'right'
}) {
  const ref = useRef<HTMLDivElement | null>(null)
  const attach = useCallback((node: HTMLDivElement | null) => {
    ref.current = node
    if (!node) return
    return () => {
      ref.current = null
      leaveGhost(node)
    }
  }, [])
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  // Transparent rather than hidden while unplaced, so an autoFocus field inside can take focus.
  const [style, setStyle] = useState<CSSProperties>({ opacity: 0, top: 0, left: 0 })

  useLayoutEffect(() => {
    const place = () => {
      const rect = anchor.current?.getBoundingClientRect()
      const el = ref.current
      if (!rect || !el) return
      const gap = 6
      const margin = 8
      const room = window.innerWidth - margin * 2
      // Grow with content up to a readable cap; ``anchor`` still floors at the trigger width.
      const cap = Math.min(room, 560)
      const floor = Math.max(fit === 'anchor' ? rect.width : 0, minWidth)
      const measured = Math.min(Math.max(el.scrollWidth, el.offsetWidth, floor), cap)
      const start = align === 'right' ? rect.right - measured : rect.left
      const left = Math.min(Math.max(margin, start), window.innerWidth - measured - margin)
      const below = window.innerHeight - rect.bottom - gap - margin
      const above = rect.top - gap - margin
      const up = el.scrollHeight > below && above > below
      // Explicit width from content avoids circular % sizing with ``width: 100%`` children.
      const size: CSSProperties = { left, width: measured, minWidth: floor, maxWidth: cap }
      setStyle(
        up
          ? { ...size, bottom: window.innerHeight - rect.top + gap, maxHeight: above }
          : { ...size, top: rect.bottom + gap, maxHeight: below },
      )
    }
    place()
    const observer = new ResizeObserver(place)
    if (ref.current) observer.observe(ref.current)
    if (anchor.current) observer.observe(anchor.current)
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [anchor, minWidth, fit, align])

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node
      if (!ref.current?.contains(target) && !anchor.current?.contains(target)) closeRef.current()
    }
    // Capture phase on window runs before Modal's Escape handler, so Esc closes only the popover.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      closeRef.current()
    }
    document.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey, true)
    }
  }, [anchor])

  return createPortal(
    <div
      ref={attach}
      className={`popover ${className}`}
      style={style}
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
    >
      {children}
    </div>,
    document.body,
  )
}

export interface MenuOption<T> {
  value: T
  label: ReactNode
  hint?: ReactNode
  danger?: boolean
  disabled?: boolean
  title?: string
}

type SelectChange = { target: { value: string } }
type MultiSelectChange = { target: { value: string[] } }

export type SelectOption = { value: string; label: ReactNode; disabled?: boolean }

function optionNodes(children: ReactNode): SelectOption[] {
  return Children.toArray(children).flatMap((child) => {
    if (!isValidElement(child)) return []
    const el = child as ReactElement<{ value?: string | number; disabled?: boolean; children?: ReactNode }>
    if (el.type !== 'option') return []
    const raw = el.props.value
    const label = el.props.children
    const value = raw !== undefined && raw !== null
      ? String(raw)
      : typeof label === 'string' || typeof label === 'number'
        ? String(label)
        : ''
    return [{ value, label, disabled: el.props.disabled }]
  })
}

type SelectBase = {
  onBlur?: () => void
  disabled?: boolean
  className?: string
  style?: CSSProperties
  autoFocus?: boolean
  title?: string
  options?: SelectOption[]
  children?: ReactNode
  placeholder?: string
  'aria-invalid'?: boolean
}

type SingleSelectProps = SelectBase & {
  multiple?: false
  value?: string | number
  defaultValue?: string | number
  onChange?: (event: SelectChange) => void
}

type MultiSelectProps = SelectBase & {
  multiple: true
  value?: readonly string[]
  defaultValue?: readonly string[]
  onChange?: (event: MultiSelectChange) => void
}

/**
 * Drop-in replacement for ``<select className="select">`` with the shared popover
 * animation, soft accent highlight, and chevron.
 * Pass ``options`` for rich labels (icons); otherwise ``<option>`` children work.
 * Set ``multiple`` to toggle several values without closing the menu.
 */
export function Select(props: SingleSelectProps): ReactElement
export function Select(props: MultiSelectProps): ReactElement
export function Select({
  value,
  defaultValue,
  onChange,
  onBlur,
  disabled,
  className = 'select',
  style,
  autoFocus,
  title,
  options: optionsProp,
  children,
  multiple = false,
  placeholder,
  'aria-invalid': ariaInvalid,
}: SingleSelectProps | MultiSelectProps) {
  const options = useMemo(() => optionsProp ?? optionNodes(children), [optionsProp, children])
  const controlled = value !== undefined
  const [innerSingle, setInnerSingle] = useState(
    () => String(!Array.isArray(defaultValue) ? (defaultValue ?? '') : ''),
  )
  const [innerMulti, setInnerMulti] = useState<string[]>(
    () => (Array.isArray(defaultValue) ? [...defaultValue] : []),
  )
  const currentSingle = controlled && !Array.isArray(value) ? String(value ?? '') : innerSingle
  const currentMulti = controlled && Array.isArray(value) ? [...value] : innerMulti
  const selectedSet = useMemo(() => new Set(multiple ? currentMulti : [currentSingle]), [multiple, currentMulti, currentSingle])
  const [open, setOpen] = useState(!!autoFocus)
  const [active, setActive] = useState(() => {
    const idx = options.findIndex((o) => selectedSet.has(o.value))
    return Math.max(0, idx)
  })
  const anchor = useRef<HTMLDivElement>(null)
  const listboxId = useId()
  const selected = options.find((o) => o.value === currentSingle)
  const selectedMulti = options.filter((o) => selectedSet.has(o.value))
  const cell = className.split(/\s+/).includes('cell-input')
  const display = multiple
    ? (selectedMulti.length
      ? selectedMulti.map((o) => (typeof o.label === 'string' || typeof o.label === 'number' ? String(o.label) : o.value)).join(t('、'))
      : (placeholder ?? t('选择…')))
    : (selected?.label ?? placeholder ?? '—')

  useEffect(() => {
    if (!open) return
    const idx = options.findIndex((o) => selectedSet.has(o.value))
    setActive(idx >= 0 ? idx : 0)
  }, [open, options, selectedSet])

  const close = (blur: boolean) => {
    setOpen(false)
    if (blur) onBlur?.()
  }

  const pickSingle = (next: string) => {
    if (!controlled) setInnerSingle(next)
    ;(onChange as SingleSelectProps['onChange'])?.({ target: { value: next } })
    close(false)
  }

  const toggleMulti = (next: string) => {
    const set = new Set(currentMulti)
    if (set.has(next)) set.delete(next)
    else set.add(next)
    const ordered = options.map((o) => o.value).filter((v) => set.has(v))
    if (!controlled) setInnerMulti(ordered)
    ;(onChange as MultiSelectProps['onChange'])?.({ target: { value: ordered } })
  }

  const pick = (next: string) => {
    if (multiple) toggleMulti(next)
    else pickSingle(next)
  }

  const onKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (disabled) return
    if (!open && (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown')) {
      e.preventDefault()
      setOpen(true)
      return
    }
    if (!open) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((i) => Math.min(options.length - 1, i + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => Math.max(0, i - 1))
    } else if (e.key === 'Home') {
      e.preventDefault()
      setActive(0)
    } else if (e.key === 'End') {
      e.preventDefault()
      setActive(Math.max(0, options.length - 1))
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      const opt = options[active]
      if (opt && !opt.disabled) pick(opt.value)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      close(true)
    }
  }

  return (
    <div
      className={`select-wrap${cell ? ' cell-select' : ''}${multiple ? ' multi' : ''}`}
      ref={anchor}
      style={style}
      onKeyDown={onKey}
    >
      <button
        type="button"
        className={`${className} select-trigger${open ? ' open' : ''}${ariaInvalid ? ' is-invalid' : ''}`}
        disabled={disabled}
        title={title ?? (typeof display === 'string' ? display : undefined)}
        role="combobox"
        aria-invalid={ariaInvalid || undefined}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-multiselectable={multiple || undefined}
        aria-activedescendant={open && options[active] ? `${listboxId}-option-${active}` : undefined}
        onClick={() => !disabled && setOpen((o) => !o)}
      >
        <span className={`select-value${multiple && !selectedMulti.length ? ' placeholder' : ''}`}>{display}</span>
        <span className="select-caret" aria-hidden>▾</span>
      </button>
      {open && (
        <Popover
          anchor={anchor}
          onClose={() => close(true)}
          fit="anchor"
          minWidth={cell ? 160 : 0}
          className="menu-pop select-pop"
        >
          <div id={listboxId} role="listbox" aria-multiselectable={multiple || undefined}>
            {options.map((opt, index) => {
              const on = selectedSet.has(opt.value)
              return (
                <button
                  id={`${listboxId}-option-${index}`}
                  key={`${opt.value}-${index}`}
                  type="button"
                  role="option"
                  aria-selected={on}
                  className={`menu-item${on ? ' selected' : ''}${index === active ? ' active' : ''}`}
                  disabled={opt.disabled}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => {
                    if (opt.disabled) return
                    pick(opt.value)
                  }}
                >
                  {multiple && <input type="checkbox" checked={on} readOnly tabIndex={-1} />}
                  <span className="select-option-label">{opt.label}</span>
                  {!multiple && on && <span className="select-check" aria-hidden>✓</span>}
                </button>
              )
            })}
            {options.length === 0 && <div className="menu-label">{t('无可选项')}</div>}
          </div>
        </Popover>
      )}
    </div>
  )
}

/** A trigger that opens a small dropdown list. */
export function Dropdown<T>({
  trigger,
  options,
  onSelect,
  align = 'left',
  disabled,
  header,
  value,
}: {
  trigger: ReactNode
  options: MenuOption<T>[]
  onSelect: (value: T) => void
  align?: 'left' | 'right'
  disabled?: boolean
  header?: ReactNode
  /** When set, marks the matching option as selected. */
  value?: T
}) {
  const [open, setOpen] = useState(false)
  const anchor = useRef<HTMLDivElement>(null)
  return (
    <div className="popover-anchor" ref={anchor}>
      <span onClick={() => !disabled && setOpen((o) => !o)} style={{ display: 'inline-flex', cursor: disabled ? 'default' : 'pointer' }}>
        {trigger}
      </span>
      {open && (
        <Popover anchor={anchor} onClose={() => setOpen(false)} minWidth={200} fit="content" align={align} className="menu-pop">
          {header}
          {options.map((option, index) => {
            const selected = value !== undefined && option.value === value
            return (
              <button
                key={index}
                type="button"
                className={`menu-item${option.danger ? ' danger' : ''}${option.disabled ? ' disabled' : ''}${selected ? ' selected' : ''}`}
                disabled={option.disabled}
                title={option.title}
                onClick={() => {
                  if (option.disabled) return
                  setOpen(false)
                  onSelect(option.value)
                }}
              >
                {option.label}
                {option.hint && <span className="spacer" />}
                {option.hint && <span className="faint tiny">{option.hint}</span>}
                {selected && !option.hint && <span className="select-check" aria-hidden>✓</span>}
              </button>
            )
          })}
          {options.length === 0 && <div className="menu-label">{t('无可选项')}</div>}
        </Popover>
      )}
    </div>
  )
}

/**
 * Ref callback that slides the ``::before`` thumb of a ``.tabs`` / ``.segmented`` group under its
 * ``.active`` child. Watches class and size changes, so router-driven ``NavLink`` tabs and changing
 * counts follow too.
 */
export function slidingThumb(el: HTMLElement | null) {
  if (!el) return
  let shown: HTMLElement | null = null
  const place = () => {
    const active = el.querySelector<HTMLElement>(':scope > .active')
    if (!active || active.offsetWidth === 0) {
      el.style.setProperty('--thumb-o', '0')
      return
    }
    // Round so subpixel glyph metrics after color invert don't nudge the whole strip.
    el.style.setProperty('--thumb-x', `${Math.round(active.offsetLeft)}px`)
    el.style.setProperty('--thumb-w', `${Math.round(active.offsetWidth)}px`)
    el.style.setProperty('--thumb-o', '1')
    if (shown && shown !== active && el.scrollWidth > el.clientWidth) {
      const left = active.offsetLeft - 16
      const right = active.offsetLeft + active.offsetWidth + 16 - el.clientWidth
      // Only scroll when the active tab is actually clipped — never for a 1px jitter.
      if (left < el.scrollLeft - 1) el.scrollTo({ left, behavior: 'smooth' })
      else if (right > el.scrollLeft + 1) el.scrollTo({ left: right, behavior: 'smooth' })
    }
    shown = active
    if (!el.dataset.thumb) requestAnimationFrame(() => (el.dataset.thumb = 'on'))
  }
  const ro = new ResizeObserver(place)
  const watch = () => [el, ...el.children].forEach((c) => ro.observe(c))
  const mo = new MutationObserver(() => {
    watch()
    place()
  })
  watch()
  mo.observe(el, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['class'] })
  place()
  return () => {
    ro.disconnect()
    mo.disconnect()
  }
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { value: T; label: ReactNode }[] }) {
  const move = (direction: -1 | 1) => {
    const index = items.findIndex((item) => item.value === value)
    const next = items[(index + direction + items.length) % items.length]
    if (next) onChange(next.value)
  }
  return (
    <div
      className="tabs"
      ref={slidingThumb}
      role="tablist"
      onKeyDown={(event) => {
        if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
          event.preventDefault()
          move(-1)
        } else if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
          event.preventDefault()
          move(1)
        }
      }}
    >
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          role="tab"
          aria-selected={value === item.value}
          tabIndex={value === item.value ? 0 : -1}
          className={`tab ${value === item.value ? 'active' : ''}`}
          onClick={() => onChange(item.value)}
        >
          {/* Ghost copy reserves the widest glyph metrics so active/inactive never reflow. */}
          <span className="tab-label">
            <span>{item.label}</span>
            {typeof item.label === 'string' && (
              <span className="tab-label-ghost" aria-hidden>
                {item.label}
              </span>
            )}
          </span>
        </button>
      ))}
    </div>
  )
}

/** Small "!" (or "?") badge next to a label; hovering or focusing it explains the concept. */
export function InfoTip({ topic, text, mark = '!' }: { topic?: HelpTopic; text?: string; mark?: '!' | '?' }) {
  const content = text ?? (topic ? helpText(topic) : '')
  const ref = useRef<HTMLSpanElement>(null)
  const [pos, setPos] = useState<{ left: number; top: number; up: boolean } | null>(null)
  const show = () => {
    const rect = ref.current?.getBoundingClientRect()
    if (!rect) return
    const up = rect.top > Math.min(150 + content.length, window.innerHeight / 2)
    const half = 150
    setPos({
      left: Math.min(Math.max(rect.left + rect.width / 2, half + 8), window.innerWidth - half - 8),
      top: up ? rect.top - 8 : rect.bottom + 8,
      up,
    })
  }
  const hide = () => setPos(null)
  useEffect(() => {
    if (!pos) return
    window.addEventListener('scroll', hide, true)
    return () => window.removeEventListener('scroll', hide, true)
  }, [pos])
  return (
    <>
      <span
        ref={ref}
        className="info-tip"
        tabIndex={0}
        role="img"
        aria-label={content}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        onClick={(e) => {
          e.preventDefault()
          e.stopPropagation()
        }}
      >
        {mark}
      </span>
      {pos &&
        createPortal(
          <div ref={leaveAnimation} className={`info-tip-bubble ${pos.up ? 'up' : 'down'}`} role="tooltip" style={{ left: pos.left, top: pos.top }}>
            {content}
          </div>,
          document.body,
        )}
    </>
  )
}

export function Segmented<T extends string>({
  value,
  onChange,
  items,
  disabled,
  className = '',
}: {
  value: T
  onChange: (v: T) => void
  items: { value: T; label: ReactNode }[]
  disabled?: boolean
  className?: string
}) {
  return (
    <div
      className={`segmented ${className}`.trim()}
      ref={slidingThumb}
      data-disabled={disabled || undefined}
      role="group"
    >
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          className={value === item.value ? 'active' : ''}
          disabled={disabled}
          aria-pressed={value === item.value}
          onClick={() => !disabled && onChange(item.value)}
        >
          {item.label}
        </button>
      ))}
    </div>
  )
}

export function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}


export function ConfirmButton({ onConfirm, children, className = 'btn danger sm', message }: { onConfirm: () => void; children: ReactNode; className?: string; message?: string }) {
  return (
    <button
      className={className}
      onClick={() => {
        if (window.confirm(message ?? t('确定要执行此操作吗？'))) onConfirm()
      }}
    >
      {children}
    </button>
  )
}

export function SubtaskBadge({ progress }: { progress?: SubtaskProgress }) {
  if (!progress?.total) return null
  const complete = progress.done === progress.total
  return (
    <span className={`subtask-badge ${complete ? 'complete' : ''}`} title={t('子任务 {done}/{total} 已完成', { done: progress.done, total: progress.total })}>
      <span className="glyph">☑</span> {progress.done}/{progress.total}
    </span>
  )
}
