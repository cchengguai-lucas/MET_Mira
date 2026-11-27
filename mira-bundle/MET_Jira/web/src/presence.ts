import { useEffect, useRef } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'

const HEARTBEAT_MS = 60_000
const IDLE_MS = 5 * 60_000

export function pageFromLocation(pathname: string, hasDrawer: boolean): string {
  if (hasDrawer) return 'issue'
  const parts = pathname.split('/').filter(Boolean)
  if (parts.length === 0) return 'home'
  if (parts[0] === 'projects') return parts[2] ?? (parts[1] ? 'board' : 'projects')
  if (parts[0] === 'issues') return 'issue'
  if (parts[0] === 'people') return 'person'
  return parts[0]
}

function send(page: string) {
  fetch('/api/presence/heartbeat', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ page }),
  }).catch(() => undefined)
}

/** Report the current page to the Ops console while the user is active in a visible tab. */
export function usePresence() {
  const location = useLocation()
  const [params] = useSearchParams()
  const page = pageFromLocation(location.pathname, params.has('issue'))
  const pageRef = useRef(page)
  const lastInput = useRef(Date.now())

  useEffect(() => {
    pageRef.current = page
    if (!document.hidden) send(page)
  }, [page])

  useEffect(() => {
    let moveQueued = false
    const active = () => {
      const wasIdle = Date.now() - lastInput.current > IDLE_MS
      lastInput.current = Date.now()
      if (wasIdle && !document.hidden) send(pageRef.current)
    }
    /** mousemove is extremely chatty — coalesce to ~2 Hz. */
    const onMove = () => {
      if (moveQueued) return
      moveQueued = true
      window.setTimeout(() => {
        moveQueued = false
        active()
      }, 500)
    }
    const onVisible = () => {
      if (!document.hidden) active()
    }
    const onLeave = () => navigator.sendBeacon('/api/presence/leave', new Blob(['{}'], { type: 'application/json' }))
    const timer = window.setInterval(() => {
      if (!document.hidden && Date.now() - lastInput.current < IDLE_MS) send(pageRef.current)
    }, HEARTBEAT_MS)
    const events = ['pointerdown', 'keydown', 'wheel'] as const
    events.forEach((e) => window.addEventListener(e, active, { passive: true }))
    window.addEventListener('mousemove', onMove, { passive: true })
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('pagehide', onLeave)
    return () => {
      window.clearInterval(timer)
      events.forEach((e) => window.removeEventListener(e, active))
      window.removeEventListener('mousemove', onMove)
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('pagehide', onLeave)
    }
  }, [])
}
