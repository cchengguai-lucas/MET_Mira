import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import type { IssueDetail, Project, ProjectDetail, UserFull } from './types'

export const queryKeys = {
  projects: () => ['projects'] as const,
  project: (key: string | null | undefined) => ['project', key] as const,
  issue: (key: string | null | undefined) => ['issue', key] as const,
  users: (query: string, softwareOf?: string, project?: string, field?: string) =>
    ['users', query, softwareOf ?? null, project ?? null, field ?? null] as const,
}

export interface AuthConfig {
  mode: string
  ldap_label: string | null
  local_fallback: boolean
  deploy_env: string
}

export function useAuthConfig() {
  return useQuery({ queryKey: ['auth-config'], queryFn: () => api.get<AuthConfig>('/api/auth/config'), staleTime: Infinity })
}

export function useDeployEnv(): string {
  return useAuthConfig().data?.deploy_env ?? 'production'
}

export function useProjects() {
  return useQuery({
    queryKey: queryKeys.projects(),
    queryFn: ({ signal }) => api.get<{ projects: Project[] }>('/api/projects', signal).then((r) => r.projects),
    staleTime: 30_000,
  })
}

export function useArchivedProjects() {
  return useQuery({
    queryKey: ['projects', 'archived'],
    queryFn: () => api.get<{ projects: Project[] }>('/api/projects?include_archived=true')
      .then((r) => r.projects.filter((p) => p.archived)),
    staleTime: 30_000,
  })
}

export function useProject(key: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.project(key),
    queryFn: ({ signal }) => api.get<ProjectDetail>(`/api/projects/${key}`, signal),
    enabled: !!key,
    staleTime: 15_000,
  })
}

export function useIssue(key: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.issue(key),
    queryFn: ({ signal }) => api.get<IssueDetail>(`/api/issues/${key}`, signal),
    enabled: !!key,
  })
}

/** Limit a person search to a software department or to one issue field's project roles. */
export function useUsersSearch(
  q: string,
  enabled = true,
  scope?: { softwareOf?: string; project?: string; field?: string },
) {
  const softwareOf = scope?.softwareOf
  const project = scope?.project
  const field = scope?.field
  const params = new URLSearchParams({ limit: '20', q })
  if (softwareOf) params.set('software_of', softwareOf)
  if (!softwareOf && project && field) {
    params.set('project', project)
    params.set('field', field)
  }
  return useQuery({
    queryKey: queryKeys.users(q, softwareOf, project, field),
    queryFn: ({ signal }) => api.get<{ users: UserFull[]; total: number }>(`/api/users?${params}`, signal),
    enabled,
    staleTime: 30_000,
  })
}

/** Surfaces that show issue lists / cards. Prefer this over nuking the whole cache. */
export const ISSUE_LIST_KEYS = [
  'board',
  'backlog',
  'search',
  'dashboard',
  'list',
  'reports',
  'project-issues',
  'timeline',
] as const

/** Invalidate issue lists after a write. Pass `also` for detail keys like `['issue', key]`. */
export function useInvalidateIssues() {
  const client = useQueryClient()
  return (opts?: { also?: string[]; skip?: readonly string[] }) => {
    const skip = new Set(opts?.skip ?? [])
    for (const key of ISSUE_LIST_KEYS) {
      if (!skip.has(key)) client.invalidateQueries({ queryKey: [key] })
    }
    for (const key of opts?.also ?? ['issue']) {
      client.invalidateQueries({ queryKey: [key] })
    }
  }
}

export function usePatchIssue(key: string) {
  const client = useQueryClient()
  const invalidate = useInvalidateIssues()
  return useMutation({
    mutationFn: (changes: Record<string, unknown>) => api.patch<IssueDetail>(`/api/issues/${key}`, changes),
    onSuccess: (data) => {
      client.setQueryData(queryKeys.issue(key), data)
      invalidate({ also: [] })
    },
  })
}
