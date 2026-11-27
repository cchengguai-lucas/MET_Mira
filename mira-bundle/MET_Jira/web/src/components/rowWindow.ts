import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

const ROW_ESTIMATE = 40
const OVERSCAN_PX = 800

export interface RowWindow {
  start: number
  end: number
  /** Height of the rows above ``start`` / below ``end``, rendered as spacer rows. */
  padTop: number
  padBottom: number
  /** Ref callback for each rendered row; rows report their real height once laid out. */
  measure: (key: string) => (el: HTMLElement | null) => void | (() => void)
}

/**
 * Windowed rendering for long tables of variable-height rows inside a scrolling ``wrap``. Only the
 * rows near the viewport are mounted; heights are measured as rows render and estimated for the
 * rest. Below ``threshold`` rows everything is rendered as before.
 */
export function useRowWindow(wrap: HTMLElement | null, head: HTMLElement | null, keys: string[], threshold: number): RowWindow {
  const enabled = keys.length > threshold
  const heights = useRef(new Map<string, number>())
  const [version, setVersion] = useState(0)
  const [view, setView] = useState({ top: 0, height: 1000 })
  const pending = useRef(0)

  const bump = useCallback(() => {
    if (pending.current) return
    pending.current = requestAnimationFrame(() => {
      pending.current = 0
      setVersion((v) => v + 1)
    })
  }, [])

  const observer = useMemo(
    () =>
      typeof ResizeObserver === 'undefined'
        ? null
        : new ResizeObserver((entries) => {
            let changed = false
            for (const entry of entries) {
              const el = entry.target as HTMLElement
              const key = el.dataset.rowKey
              if (!key) continue
              const height = el.getBoundingClientRect().height
              if (height && Math.abs((heights.current.get(key) ?? 0) - height) > 0.5) {
                heights.current.set(key, height)
                changed = true
              }
            }
            if (changed) bump()
          }),
    [bump],
  )
  useEffect(() => () => {
    observer?.disconnect()
    cancelAnimationFrame(pending.current)
  }, [observer])

  useEffect(() => {
    if (!wrap || !enabled) return
    let frame = 0
    const read = () => {
      frame = 0
      setView({ top: wrap.scrollTop - (head?.offsetHeight ?? 0), height: wrap.clientHeight })
    }
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(read)
    }
    read()
    wrap.addEventListener('scroll', schedule, { passive: true })
    const resize = new ResizeObserver(schedule)
    resize.observe(wrap)
    return () => {
      wrap.removeEventListener('scroll', schedule)
      resize.disconnect()
      cancelAnimationFrame(frame)
    }
  }, [wrap, head, enabled])

  const offsets = useMemo(() => {
    const out = new Float64Array(keys.length + 1)
    for (let i = 0; i < keys.length; i++) out[i + 1] = out[i] + (heights.current.get(keys[i]) ?? ROW_ESTIMATE)
    return out
    // `version` changes whenever a measured height does.
  }, [keys, version])

  const measure = useCallback(
    (key: string) => (el: HTMLElement | null) => {
      if (!el || !observer) return
      el.dataset.rowKey = key
      observer.observe(el)
      return () => observer.unobserve(el)
    },
    [observer],
  )

  if (!enabled) return { start: 0, end: keys.length, padTop: 0, padBottom: 0, measure }
  const total = offsets[keys.length]
  const from = Math.max(0, view.top - OVERSCAN_PX)
  const to = Math.min(total, view.top + view.height + OVERSCAN_PX)
  const start = Math.max(0, upperBound(offsets, from) - 1)
  const end = Math.min(keys.length, Math.max(start + 1, lowerBound(offsets, to)))
  return { start, end, padTop: offsets[start], padBottom: total - offsets[end], measure }
}

/** First index whose offset is > ``value``. */
function upperBound(offsets: Float64Array, value: number): number {
  let lo = 0
  let hi = offsets.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (offsets[mid] <= value) lo = mid + 1
    else hi = mid
  }
  return lo
}

/** First index whose offset is >= ``value``. */
function lowerBound(offsets: Float64Array, value: number): number {
  let lo = 0
  let hi = offsets.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (offsets[mid] < value) lo = mid + 1
    else hi = mid
  }
  return lo
}
