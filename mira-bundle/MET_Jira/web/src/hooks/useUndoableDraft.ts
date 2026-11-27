import { useCallback, useEffect, useRef, useState } from 'react'

/** Undo/redo stack around a draft value. External resets clear history. */
export function useUndoableDraft<T>(value: T, setValue: (next: T) => void, resetKey: string) {
  const past = useRef<T[]>([])
  const future = useRef<T[]>([])
  const [tick, setTick] = useState(0)
  const valueRef = useRef(value)
  valueRef.current = value

  useEffect(() => {
    past.current = []
    future.current = []
    setTick((n) => n + 1)
  }, [resetKey])

  const push = useCallback(
    (next: T) => {
      past.current.push(structuredClone(valueRef.current))
      if (past.current.length > 80) past.current.shift()
      future.current = []
      setValue(next)
      setTick((n) => n + 1)
    },
    [setValue],
  )

  const undo = useCallback(() => {
    const prev = past.current.pop()
    if (prev === undefined) return
    future.current.push(structuredClone(valueRef.current))
    setValue(prev)
    setTick((n) => n + 1)
  }, [setValue])

  const redo = useCallback(() => {
    const next = future.current.pop()
    if (next === undefined) return
    past.current.push(structuredClone(valueRef.current))
    setValue(next)
    setTick((n) => n + 1)
  }, [setValue])

  return {
    push,
    undo,
    redo,
    canUndo: past.current.length > 0,
    canRedo: future.current.length > 0,
    /** Force re-render consumers when stack size changes. */
    revision: tick,
  }
}
