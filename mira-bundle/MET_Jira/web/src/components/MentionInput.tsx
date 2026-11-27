import { useEffect, useMemo, useRef, useState, type ClipboardEvent, type CSSProperties, type DragEvent, type KeyboardEvent, type RefObject } from 'react'
import { t } from '../i18n'
import { useUsersSearch } from '../queries'
import type { UserBrief } from '../types'
import { Avatar } from './ui'
import { leaveAnimation } from './leave'
import {
  applySlashCommand,
  caretPoint,
  detectSlash,
  filterSlashCommands,
  SlashMenu,
  type SlashCommand,
} from './SlashCommands'

/** A person worth suggesting for this text, with why they are relevant (assignee, watcher…). */
export interface MentionPerson {
  user: UserBrief
  role: string
}

const TRIGGER = /(^|[^A-Za-z0-9._-])@([^\s@]{0,30})$/
const MAX_ITEMS = 8

function matches(user: UserBrief, query: string) {
  const q = query.toLowerCase()
  return user.username.toLowerCase().includes(q) || user.display_name.toLowerCase().includes(q)
}

/** Usernames already mentioned in the text. */
export function mentionedNames(text: string) {
  return new Set([...text.matchAll(/@([A-Za-z0-9._-]{2,64})/g)].map((m) => m[1].toLowerCase()))
}

/** Replace the `@query` that ends at `caret` (or insert at caret) with `@username `. */
export function insertMention(text: string, caret: number, username: string) {
  const head = text.slice(0, caret)
  const trigger = TRIGGER.exec(head)
  const start = trigger ? caret - trigger[2].length - 1 : caret
  const lead = start > 0 && !/\s/.test(text[start - 1]) ? ' ' : ''
  const before = `${text.slice(0, start)}${lead}@${username} `
  const rest = text.slice(caret).replace(/^ /, '')
  return { text: before + rest, caret: before.length }
}

interface Props {
  value: string
  onChange: (value: string) => void
  people?: MentionPerson[]
  textareaRef?: RefObject<HTMLTextAreaElement | null>
  className?: string
  style?: CSSProperties
  placeholder?: string
  autoFocus?: boolean
  readOnly?: boolean
  /** Enable `/` slash command menu (default true). */
  slash?: boolean
  /** Show the "insert image" slash action; caller must handle the pick. */
  onSlashImage?: () => void
  onKeyDown?: (e: KeyboardEvent<HTMLTextAreaElement>) => void
  onPaste?: (e: ClipboardEvent<HTMLTextAreaElement>) => void
  onDrop?: (e: DragEvent<HTMLTextAreaElement>) => void
}

/** Textarea with `@` people picker and optional `/` Markdown slash commands. */
export function MentionTextarea({
  value,
  onChange,
  people = [],
  textareaRef,
  className = 'textarea',
  style,
  placeholder,
  autoFocus,
  readOnly,
  slash = true,
  onSlashImage,
  onKeyDown,
  onPaste,
  onDrop,
}: Props) {
  const ownRef = useRef<HTMLTextAreaElement>(null)
  const ref = textareaRef ?? ownRef
  const [mentionQuery, setMentionQuery] = useState<string | null>(null)
  const [slashQuery, setSlashQuery] = useState<string | null>(null)
  const [point, setPoint] = useState({ left: 0, top: 0 })
  const [active, setActive] = useState(0)
  const search = useUsersSearch(mentionQuery ?? '', mentionQuery !== null && mentionQuery.length >= 1)
  const allowImage = !!onSlashImage

  const mentionOptions = useMemo(() => {
    if (mentionQuery === null) return []
    const seen = new Set<number>()
    const out: MentionPerson[] = []
    for (const person of people) {
      if (!seen.has(person.user.id) && (!mentionQuery || matches(person.user, mentionQuery))) {
        seen.add(person.user.id)
        out.push(person)
      }
    }
    if (mentionQuery) {
      for (const user of search.data?.users ?? []) {
        if (!seen.has(user.id)) {
          seen.add(user.id)
          out.push({ user, role: '' })
        }
      }
    }
    return out.slice(0, MAX_ITEMS)
  }, [mentionQuery, people, search.data])

  const slashOptions = useMemo(
    () => (slashQuery === null ? [] : filterSlashCommands(slashQuery, allowImage)),
    [slashQuery, allowImage],
  )

  useEffect(() => setActive(0), [mentionQuery, slashQuery])

  const detect = (el: HTMLTextAreaElement) => {
    if (el.selectionStart !== el.selectionEnd) {
      setMentionQuery(null)
      setSlashQuery(null)
      return
    }
    const head = el.value.slice(0, el.selectionStart)
    const mention = TRIGGER.exec(head)
    if (mention) {
      const at = caretPoint(el, el.selectionStart - mention[2].length - 1)
      setPoint({ left: Math.min(at.left, Math.max(0, el.clientWidth - 280)), top: at.top + 4 })
      setMentionQuery(mention[2])
      setSlashQuery(null)
      return
    }
    if (slash) {
      const hit = detectSlash(el.value, el.selectionStart)
      if (hit) {
        const at = caretPoint(el, hit.start)
        setPoint({ left: Math.min(at.left, Math.max(0, el.clientWidth - 260)), top: at.top + 4 })
        setSlashQuery(hit.query)
        setMentionQuery(null)
        return
      }
    }
    setMentionQuery(null)
    setSlashQuery(null)
  }

  const pickMention = (username: string) => {
    const el = ref.current
    if (!el) return
    const next = insertMention(value, el.selectionStart, username)
    onChange(next.text)
    setMentionQuery(null)
    requestAnimationFrame(() => {
      el.focus()
      el.selectionStart = el.selectionEnd = next.caret
    })
  }

  const pickSlash = (cmd: SlashCommand) => {
    const el = ref.current
    if (!el) return
    const next = applySlashCommand(value, el.selectionStart, cmd)
    setSlashQuery(null)
    if (!next) return
    onChange(next.text)
    requestAnimationFrame(() => {
      el.focus()
      el.selectionStart = el.selectionEnd = next.caret
      if (cmd.id === 'image') onSlashImage?.()
    })
  }

  const mentionOpen = mentionQuery !== null
  const slashOpen = slashQuery !== null
  const searching = !!mentionQuery && search.isFetching && mentionOptions.length === 0

  return (
    <div className="mention-field">
      <textarea
        ref={ref}
        className={className}
        style={style}
        placeholder={placeholder}
        autoFocus={autoFocus}
        readOnly={readOnly}
        value={value}
        onChange={(e) => {
          onChange(e.target.value)
          detect(e.target)
        }}
        onSelect={(e) => detect(e.currentTarget)}
        onBlur={() => {
          setMentionQuery(null)
          setSlashQuery(null)
        }}
        onKeyDown={(e) => {
          if (readOnly || e.nativeEvent.isComposing || e.ctrlKey || e.metaKey) {
            onKeyDown?.(e)
            return
          }
          if (slashOpen) {
            if (e.key === 'Escape') {
              e.preventDefault()
              e.stopPropagation()
              setSlashQuery(null)
              return
            }
            if (slashOptions.length > 0) {
              if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault()
                const step = e.key === 'ArrowDown' ? 1 : -1
                setActive((i) => (i + step + slashOptions.length) % slashOptions.length)
                return
              }
              if (e.key === 'Enter' || e.key === 'Tab') {
                e.preventDefault()
                pickSlash(slashOptions[Math.min(active, slashOptions.length - 1)])
                return
              }
            }
          }
          if (mentionOpen) {
            if (e.key === 'Escape') {
              e.preventDefault()
              e.stopPropagation()
              setMentionQuery(null)
              return
            }
            if (mentionOptions.length > 0) {
              if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault()
                const step = e.key === 'ArrowDown' ? 1 : -1
                setActive((i) => (i + step + mentionOptions.length) % mentionOptions.length)
                return
              }
              if (e.key === 'Enter' || e.key === 'Tab') {
                e.preventDefault()
                pickMention(mentionOptions[Math.min(active, mentionOptions.length - 1)].user.username)
                return
              }
            }
          }
          onKeyDown?.(e)
        }}
        onPaste={onPaste}
        onDrop={onDrop}
      />
      {mentionOpen && (mentionOptions.length > 0 || mentionQuery === '' || searching || search.isFetched) && (
        <div className="menu mention-menu" ref={leaveAnimation} style={{ left: point.left, top: point.top }} onMouseDown={(e) => e.preventDefault()}>
          {mentionOptions.map((person, index) => (
            <button
              key={person.user.id}
              type="button"
              className={`menu-item${index === active ? ' active' : ''}`}
              onMouseEnter={() => setActive(index)}
              onClick={() => pickMention(person.user.username)}
            >
              <Avatar user={person.user} size={22} />
              <span className="mention-name">{person.user.display_name}</span>
              <span className="faint tiny mention-handle">@{person.user.username}</span>
              {person.role && <span className="mention-role">{person.role}</span>}
            </button>
          ))}
          {mentionOptions.length === 0 && (
            <div className="faint small mention-empty">
              {mentionQuery === '' ? t('输入姓名或用户名搜索同事') : searching ? t('搜索中…') : t('没有匹配的人员')}
            </div>
          )}
          {mentionOptions.length > 0 && <div className="mention-hint tiny faint">{t('↑↓ 选择 · Enter 确认 · Esc 关闭')}</div>}
        </div>
      )}
      {slashOpen && (
        <SlashMenu
          query={slashQuery ?? ''}
          point={point}
          active={active}
          onActive={setActive}
          allowImage={allowImage}
          onPick={pickSlash}
        />
      )}
    </div>
  )
}

/** Quick-pick row: one click mentions a participant who is not mentioned yet. */
export function MentionChips({ people, text, onPick }: { people: MentionPerson[]; text: string; onPick: (username: string) => void }) {
  const mentioned = mentionedNames(text)
  const rest = people.filter((p) => !mentioned.has(p.user.username.toLowerCase()))
  if (rest.length === 0) return null
  return (
    <div className="mention-chips" aria-label={t('快速提及')}>
      <span className="mention-chips-icon" title={t('快速提及')}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <circle cx="9" cy="8" r="4" />
          <path d="M2 21c0-3.9 3.1-7 7-7s7 3.1 7 7" />
          <path d="M19 8v6M16 11h6" />
        </svg>
      </span>
      {rest.map((person) => (
        <button
          key={person.user.id}
          type="button"
          className="mention-chip"
          title={`${person.role ? `${person.role} · ` : ''}@${person.user.username}`}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => onPick(person.user.username)}
        >
          <span className="mention-chip-plus">+</span>
          <Avatar user={person.user} size={18} />
          {person.user.display_name}
        </button>
      ))}
    </div>
  )
}
