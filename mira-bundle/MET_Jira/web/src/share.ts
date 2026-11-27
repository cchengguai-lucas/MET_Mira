import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

export type UrlPatch = Record<string, string | string[] | null | undefined>

/** Read the query string and patch it in place, keeping keys that belong to others (e.g. `issue`). */
export function useUrlState() {
  const [params, setParams] = useSearchParams()
  const update = useCallback(
    (patch: UrlPatch) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          for (const [key, value] of Object.entries(patch)) {
            next.delete(key)
            if (Array.isArray(value)) value.forEach((v) => next.append(key, v))
            else if (value) next.set(key, value)
          }
          return next
        },
        { replace: true },
      ),
    [setParams],
  )
  return [params, update] as const
}

/** The current page as a shareable link: no open issue drawer, plus any extra view keys. */
export function viewLink(extra: Record<string, string> = {}): string {
  const url = new URL(window.location.href)
  url.searchParams.delete('issue')
  for (const [key, value] of Object.entries(extra)) url.searchParams.set(key, value)
  return url.toString()
}

/** navigator.clipboard only exists on HTTPS or localhost, and the app is served over plain HTTP. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // fall through to the legacy path
  }
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    area.remove()
  }
}
