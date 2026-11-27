import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { ISSUE_LIST_KEYS } from './queries'

type LiveEvent = {
  type?: string
  kind?: string
  project_key?: string
  issue_key?: string | null
  fields?: string[]
}

type Entry = {
  count: number
  source: EventSource
  timer: number | null
  pending: LiveEvent[]
}

const connections = new Map<string, Entry>()

function applyEvents(client: QueryClient, projectKey: string, events: LiveEvent[]) {
  let touchProject = false
  let touchLists = false
  const issueKeys = new Set<string>()
  for (const event of events) {
    if (event.type !== 'change') continue
    if (event.kind === 'project') {
      touchProject = true
      touchLists = true
      continue
    }
    if (event.kind === 'issue') {
      touchLists = true
      if (event.issue_key) issueKeys.add(event.issue_key)
      if (event.fields?.includes('status_id') || event.fields?.includes('create')) {
        touchProject = true
      }
    }
  }
  if (touchProject) client.invalidateQueries({ queryKey: ['project', projectKey] })
  for (const key of issueKeys) client.invalidateQueries({ queryKey: ['issue', key] })
  if (touchLists) {
    for (const key of ISSUE_LIST_KEYS) client.invalidateQueries({ queryKey: [key] })
  }
}

function ensureConnection(projectKey: string, client: QueryClient): Entry {
  const key = projectKey.toUpperCase()
  let entry = connections.get(key)
  if (entry) return entry

  const source = new EventSource(`/api/live/stream?project=${encodeURIComponent(key)}`)
  entry = { count: 0, source, timer: null, pending: [] }
  source.onmessage = (message) => {
    let data: LiveEvent
    try {
      data = JSON.parse(message.data) as LiveEvent
    } catch {
      return
    }
    if (data.type === 'hello') return
    entry!.pending.push(data)
    if (entry!.timer != null) return
    entry!.timer = window.setTimeout(() => {
      const batch = entry!.pending.splice(0, entry!.pending.length)
      entry!.timer = null
      if (batch.length) applyEvents(client, key, batch)
    }, 200)
  }
  source.onerror = () => {
    // Browser auto-reconnects EventSource; leave the socket open while refs remain.
  }
  connections.set(key, entry)
  return entry
}

function releaseConnection(projectKey: string) {
  const key = projectKey.toUpperCase()
  const entry = connections.get(key)
  if (!entry) return
  entry.count -= 1
  if (entry.count > 0) return
  if (entry.timer != null) window.clearTimeout(entry.timer)
  entry.source.close()
  connections.delete(key)
}

/** Keep issue boards / details fresh when another user changes the same project. */
export function useProjectLiveSync(projectKey: string | null | undefined) {
  const client = useQueryClient()
  useEffect(() => {
    if (!projectKey) return
    if (typeof EventSource === 'undefined') return
    const entry = ensureConnection(projectKey, client)
    entry.count += 1
    return () => releaseConnection(projectKey)
  }, [projectKey, client])
}
