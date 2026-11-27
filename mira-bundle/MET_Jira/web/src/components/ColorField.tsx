import { useRef, useState } from 'react'
import { t } from '../i18n'
import { Popover } from './ui'

/** Shared palette — matches project create / status accents. */
export const THEME_COLORS = [
  '#0a84ff', '#5e5ce6', '#bf5af2', '#ff375f',
  '#ff9f0a', '#30d158', '#64d2ff', '#8e8e93',
  '#ac8e68', '#ff453a', '#32ade6', '#af52de',
]

type ColorFieldProps = {
  value: string
  onChange: (color: string) => void
  disabled?: boolean
  /** Called when the popover closes (status editors that save on blur). */
  onCommit?: (color: string) => void
  colors?: string[]
  className?: string
  size?: 'default' | 'sm'
}

export function ColorField({
  value,
  onChange,
  disabled,
  onCommit,
  colors = THEME_COLORS,
  className = '',
  size = 'default',
}: ColorFieldProps) {
  const anchor = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState(value)

  const close = (commit = true) => {
    setOpen(false)
    if (commit) onCommit?.(draft)
  }

  const pick = (color: string) => {
    setDraft(color)
    onChange(color)
    setOpen(false)
    onCommit?.(color)
  }

  return (
    <div className={`color-field ${size} ${className}`.trim()}>
      <button
        ref={anchor}
        type="button"
        className={`color-field-trigger ${open ? 'open' : ''}`}
        disabled={disabled}
        aria-label={t('选择颜色')}
        aria-expanded={open}
        onClick={() => {
          if (disabled) return
          setDraft(value)
          setOpen((v) => !v)
        }}
      >
        <span className="color-field-swatch" style={{ background: value }} />
        <span className="color-field-hex mono">{value.toUpperCase()}</span>
        <span className="color-field-caret faint">▾</span>
      </button>
      {open && !disabled && (
        <Popover anchor={anchor} onClose={() => close(true)} fit="content" className="color-pop" minWidth={220}>
          <div className="color-pop-grid">
            {colors.map((c) => (
              <button
                key={c}
                type="button"
                className={`color-pop-swatch ${c.toLowerCase() === value.toLowerCase() ? 'on' : ''}`}
                style={{ background: c }}
                title={c}
                onClick={() => pick(c)}
              />
            ))}
          </div>
          <div className="color-pop-custom">
            <label className="tiny muted">{t('自定义')}</label>
            <div className="row" style={{ gap: 8 }}>
              <input
                className="input sm mono"
                value={draft}
                maxLength={7}
                onChange={(e) => {
                  const next = e.target.value
                  setDraft(next)
                  if (/^#[0-9a-fA-F]{6}$/.test(next)) onChange(next)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && /^#[0-9a-fA-F]{6}$/.test(draft)) pick(draft)
                }}
              />
              <button
                type="button"
                className="btn primary sm"
                disabled={!/^#[0-9a-fA-F]{6}$/.test(draft)}
                onClick={() => pick(draft)}
              >
                {t('应用')}
              </button>
            </div>
          </div>
        </Popover>
      )}
    </div>
  )
}
