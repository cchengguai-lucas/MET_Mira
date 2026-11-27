import { useMemo, type CSSProperties } from 'react'
import { Select } from './ui'

function buildSlots(stepMinutes: number): string[] {
  const out: string[] = []
  for (let minutes = 0; minutes < 24 * 60; minutes += stepMinutes) {
    const h = Math.floor(minutes / 60)
    const m = minutes % 60
    out.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`)
  }
  return out
}

type TimeFieldProps = {
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  className?: string
  style?: CSSProperties
  /** Slot size in minutes (default 30). */
  stepMinutes?: number
}

/** Themed time picker — uses the shared Select popover instead of native ``type="time"``. */
export function TimeField({
  value,
  onChange,
  disabled,
  className = 'select sm',
  style,
  stepMinutes = 30,
}: TimeFieldProps) {
  const slots = useMemo(() => {
    const base = buildSlots(stepMinutes)
    if (value && !base.includes(value)) return [...base, value].sort()
    return base
  }, [stepMinutes, value])

  return (
    <Select
      className={className}
      style={style}
      disabled={disabled}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {slots.map((slot) => (
        <option key={slot} value={slot}>{slot}</option>
      ))}
    </Select>
  )
}
