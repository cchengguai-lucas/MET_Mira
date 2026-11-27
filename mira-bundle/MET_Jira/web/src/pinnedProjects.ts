import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useMemo } from 'react'
import { api } from './api'
import { fetchMe, type MeResponse } from './shortcuts'

const MAX_PINNED = 40

function normalizeKeys(raw: unknown): string[] {
  if (!Array.isArray(raw)) return []
  const out: string[] = []
  const seen = new Set<string>()
  for (const item of raw) {
    if (typeof item !== 'string') continue
    const key = item.trim().toUpperCase()
    if (!key || seen.has(key)) continue
    seen.add(key)
    out.push(key)
  }
  return out
}

export function usePinnedProjects() {
  const client = useQueryClient()
  const me = useQuery({ queryKey: ['me'], queryFn: fetchMe, staleTime: 60_000 })
  const pinned = useMemo(
    () => normalizeKeys(me.data?.preferences?.pinned_projects),
    [me.data?.preferences?.pinned_projects],
  )
  const pinnedSet = useMemo(() => new Set(pinned), [pinned])

  const save = useCallback(async (next: string[]) => {
    const result = await api.put<{ preferences: Record<string, unknown> }>(
      '/api/auth/me/preferences',
      { pinned_projects: next.slice(0, MAX_PINNED) },
    )
    client.setQueryData<MeResponse>(['me'], (old) =>
      (old ? { ...old, preferences: result.preferences } : old),
    )
  }, [client])

  const isPinned = useCallback((key: string) => pinnedSet.has(key.toUpperCase()), [pinnedSet])

  const toggle = useCallback(async (key: string) => {
    const upper = key.trim().toUpperCase()
    if (!upper) return
    const next = pinnedSet.has(upper)
      ? pinned.filter((k) => k !== upper)
      : [upper, ...pinned.filter((k) => k !== upper)].slice(0, MAX_PINNED)
    await save(next)
  }, [pinned, pinnedSet, save])

  const sortPinnedFirst = useCallback(<T extends { key: string }>(items: T[]): T[] => {
    if (!pinned.length) return items
    const rank = new Map(pinned.map((k, i) => [k, i]))
    return items
      .map((item, index) => ({ item, index }))
      .sort((a, b) => {
        const ai = rank.get(a.item.key.toUpperCase()) ?? Number.POSITIVE_INFINITY
        const bi = rank.get(b.item.key.toUpperCase()) ?? Number.POSITIVE_INFINITY
        if (ai !== bi) return ai - bi
        return a.index - b.index
      })
      .map(({ item }) => item)
  }, [pinned])

  return { pinned, isPinned, toggle, sortPinnedFirst }
}
