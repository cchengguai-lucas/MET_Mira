import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react'
import { locale, t } from '../i18n'
import { Popover } from './ui'

function parseISO(iso: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso.trim())
  if (!match) return null
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  return Number.isNaN(date.getTime()) ? null : date
}

function toISO(date: Date): string {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate()
}

function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1)
}

function addMonths(date: Date, delta: number): Date {
  return new Date(date.getFullYear(), date.getMonth() + delta, 1)
}

function addDays(date: Date, delta: number): Date {
  const next = new Date(date)
  next.setDate(date.getDate() + delta)
  return next
}

function shiftMonths(date: Date, delta: number): Date {
  const target = new Date(date.getFullYear(), date.getMonth() + delta, 1)
  const lastDay = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate()
  target.setDate(Math.min(date.getDate(), lastDay))
  return target
}

function formatTrigger(iso: string): string {
  const date = parseISO(iso)
  if (!date) return iso
  return date.toLocaleDateString(locale(), { year: 'numeric', month: 'short', day: 'numeric' })
}

function formatMonthTitle(date: Date): string {
  return date.toLocaleDateString(locale(), { year: 'numeric', month: 'long' })
}

function weekdayLabels(): string[] {
  // Build Mon–Sun-aware labels from a known week (2024-01-07 was Sunday).
  const base = new Date(2024, 0, 7)
  return Array.from({ length: 7 }, (_, i) => {
    const day = new Date(base)
    day.setDate(base.getDate() + i)
    return day.toLocaleDateString(locale(), { weekday: 'narrow' })
  })
}

function CalendarGlyph({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden>
      <path
        fill="currentColor"
        d="M4.2 1.4h1.1v1.1h5.4V1.4h1.1v1.1H13c.55 0 1 .45 1 1v9.2c0 .55-.45 1-1 1H3c-.55 0-1-.45-1-1V3.5c0-.55.45-1 1-1h1.2V1.4zm8.7 4.1H3.1v7.2c0 .06.04.1.1.1h9.6c.06 0 .1-.04.1-.1V5.5zM5.2 7.1h1.5v1.5H5.2V7.1zm2.55 0h1.5v1.5H7.75V7.1zm2.55 0H12v1.5h-1.7V7.1zM5.2 9.7h1.5v1.5H5.2V9.7zm2.55 0h1.5v1.5H7.75V9.7zm2.55 0H12v1.5h-1.7V9.7z"
      />
    </svg>
  )
}

function Chevron({ dir }: { dir: 'prev' | 'next' }) {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
      <path
        fill="currentColor"
        d={dir === 'prev'
          ? 'M9.8 3.2 5.2 8l4.6 4.8.9-.85L6.95 8l3.75-3.95-.9-.85z'
          : 'M6.2 3.2l.9.85L10.85 8 7.1 11.95l-.9.85L11.8 8 6.2 3.2z'}
      />
    </svg>
  )
}

type DateFieldProps = {
  value: string
  onChange: (value: string) => void
  onBlur?: () => void
  disabled?: boolean
  required?: boolean
  min?: string
  max?: string
  className?: string
  /** Opens the calendar as soon as the field mounts (list / inline editors). */
  autoOpen?: boolean
  autoFocus?: boolean
  placeholder?: string
  /** Compact trigger for dense cells. */
  size?: 'default' | 'sm'
}

export function DateField({
  value,
  onChange,
  onBlur,
  disabled,
  required,
  min,
  max,
  className = '',
  autoOpen = false,
  autoFocus = false,
  placeholder,
  size = 'default',
}: DateFieldProps) {
  const anchor = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(autoOpen)
  const selected = useMemo(() => (value ? parseISO(value) : null), [value])
  const minDate = useMemo(() => (min ? parseISO(min) : null), [min])
  const maxDate = useMemo(() => (max ? parseISO(max) : null), [max])
  const [cursor, setCursor] = useState(() => startOfMonth(selected ?? new Date()))
  const lang = locale()
  const weekdays = useMemo(() => weekdayLabels(), [lang])

  useEffect(() => {
    if (open) setCursor(startOfMonth(selected ?? new Date()))
  }, [open, selected])

  useEffect(() => {
    if (autoFocus) anchor.current?.focus()
  }, [autoFocus])

  const close = () => {
    setOpen(false)
    onBlur?.()
  }

  const pick = (date: Date) => {
    onChange(toISO(date))
    setOpen(false)
    onBlur?.()
  }

  const clear = () => {
    onChange('')
    setOpen(false)
    onBlur?.()
  }

  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const todayBlocked = (minDate && today < minDate) || (maxDate && today > maxDate)

  const days = useMemo(() => {
    const first = startOfMonth(cursor)
    const start = new Date(first)
    start.setDate(1 - first.getDay())
    return Array.from({ length: 42 }, (_, i) => {
      const date = new Date(start)
      date.setDate(start.getDate() + i)
      return date
    })
  }, [cursor])

  const blocked = (date: Date) => {
    if (minDate && date < minDate) return true
    if (maxDate && date > maxDate) return true
    return false
  }

  const label = value ? formatTrigger(value) : (placeholder ?? t('选择日期'))

  return (
    <div className={`date-field ${size === 'sm' ? 'sm' : ''} ${className}`.trim()}>
      {/* Hidden native input keeps Playwright / form autofill working with ISO values. */}
      <input
        className="date-field-native"
        type="date"
        tabIndex={-1}
        aria-hidden
        disabled={disabled}
        required={required}
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <button
        ref={anchor}
        type="button"
        className={`date-field-trigger input ${size === 'sm' ? 'sm' : ''} ${open ? 'open' : ''} ${!value ? 'empty' : ''}`}
        disabled={disabled}
        aria-required={required || undefined}
        aria-expanded={open}
        onClick={() => { if (!disabled) setOpen((v) => !v) }}
      >
        <span className="date-field-value">{label}</span>
        <span className="date-field-icon"><CalendarGlyph /></span>
      </button>
      {open && !disabled && (
        <Popover anchor={anchor} onClose={close} fit="content" className="date-cal-pop" minWidth={256}>
          <DateCalendar
            cursor={cursor}
            onCursor={setCursor}
            days={days}
            weekdays={weekdays}
            selected={selected}
            today={today}
            blocked={blocked}
            onPick={pick}
            onClear={clear}
            onToday={() => !todayBlocked && pick(today)}
            canToday={!todayBlocked}
            canClear={!required}
          />
        </Popover>
      )}
    </div>
  )
}

function DateCalendar({
  cursor,
  onCursor,
  days,
  weekdays,
  selected,
  today,
  blocked,
  onPick,
  onClear,
  onToday,
  canToday,
  canClear,
}: {
  cursor: Date
  onCursor: (d: Date) => void
  days: Date[]
  weekdays: string[]
  selected: Date | null
  today: Date
  blocked: (d: Date) => boolean
  onPick: (d: Date) => void
  onClear: () => void
  onToday: () => void
  canToday: boolean
  canClear: boolean
}): ReactNode {
  const gridRef = useRef<HTMLDivElement>(null)
  const [focusDate, setFocusDate] = useState(selected ?? today)
  const focus = (date: Date) => {
    setFocusDate(date)
    if (date.getMonth() !== cursor.getMonth() || date.getFullYear() !== cursor.getFullYear()) {
      onCursor(startOfMonth(date))
    }
    requestAnimationFrame(() => {
      gridRef.current?.querySelector<HTMLButtonElement>(`[data-date="${toISO(date)}"]`)?.focus()
    })
  }
  const onDayKey = (event: ReactKeyboardEvent<HTMLButtonElement>, date: Date) => {
    let next: Date | null = null
    if (event.key === 'ArrowLeft') next = addDays(date, -1)
    else if (event.key === 'ArrowRight') next = addDays(date, 1)
    else if (event.key === 'ArrowUp') next = addDays(date, -7)
    else if (event.key === 'ArrowDown') next = addDays(date, 7)
    else if (event.key === 'Home') next = addDays(date, -date.getDay())
    else if (event.key === 'End') next = addDays(date, 6 - date.getDay())
    else if (event.key === 'PageUp') next = shiftMonths(date, event.shiftKey ? -12 : -1)
    else if (event.key === 'PageDown') next = shiftMonths(date, event.shiftKey ? 12 : 1)
    if (!next) return
    event.preventDefault()
    focus(next)
  }
  return (
    <div className="date-cal">
      <div className="date-cal-head">
        <button
          type="button"
          className="date-cal-nav"
          aria-label={t('上个月')}
          title={t('按住 Shift 可按年切换')}
          onClick={(e) => onCursor(addMonths(cursor, e.shiftKey ? -12 : -1))}
        >
          <Chevron dir="prev" />
        </button>
        <div className="date-cal-title">{formatMonthTitle(cursor)}</div>
        <button
          type="button"
          className="date-cal-nav"
          aria-label={t('下个月')}
          title={t('按住 Shift 可按年切换')}
          onClick={(e) => onCursor(addMonths(cursor, e.shiftKey ? 12 : 1))}
        >
          <Chevron dir="next" />
        </button>
      </div>
      <div className="date-cal-weekdays">
        {weekdays.map((label, i) => <span key={i}>{label}</span>)}
      </div>
      <div
        ref={gridRef}
        className="date-cal-grid"
        role="grid"
        aria-label={formatMonthTitle(cursor)}
      >
        {days.map((date) => {
          const inMonth = date.getMonth() === cursor.getMonth()
          const isSelected = selected ? sameDay(date, selected) : false
          const isToday = sameDay(date, today)
          const isBlocked = blocked(date)
          return (
            <button
              key={toISO(date)}
              data-date={toISO(date)}
              type="button"
              role="gridcell"
              disabled={isBlocked}
              tabIndex={sameDay(date, focusDate) ? 0 : -1}
              aria-selected={isSelected}
              aria-current={isToday ? 'date' : undefined}
              aria-label={date.toLocaleDateString(locale(), { dateStyle: 'long' })}
              className={[
                'date-cal-day',
                inMonth ? 'in-month' : 'out-month',
                isSelected ? 'selected' : '',
                isToday ? 'today' : '',
              ].filter(Boolean).join(' ')}
              onClick={() => onPick(date)}
              onFocus={() => setFocusDate(date)}
              onKeyDown={(event) => onDayKey(event, date)}
            >
              {date.getDate()}
            </button>
          )
        })}
      </div>
      <div className="date-cal-foot">
        {canClear
          ? <button type="button" className="date-cal-action" onClick={onClear}>{t('清空')}</button>
          : <span />}
        <button type="button" className="date-cal-action accent" disabled={!canToday} onClick={onToday}>
          {t('今天')}
        </button>
      </div>
    </div>
  )
}
