import { getLang, tServer } from './i18n'

export class ApiError extends Error {
  status: number
  code?: string
  /** Field keys that must be filled (e.g. gate failures). */
  fields?: string[]
  constructor(status: number, message: string, code?: string, fields?: string[]) {
    super(message)
    this.status = status
    this.code = code
    this.fields = fields
  }
}

async function parseError(response: Response): Promise<ApiError> {
  let message = `${response.status} ${response.statusText}`
  let code: string | undefined
  let fields: string[] | undefined
  try {
    const body = await response.json()
    if (typeof body.code === 'string') code = body.code
    if (Array.isArray(body.fields)) {
      fields = body.fields.filter((f: unknown): f is string => typeof f === 'string')
    }
    if (typeof body.detail === 'string') message = body.detail
    else if (Array.isArray(body.detail)) {
      message = body.detail.map((d: { msg?: string; loc?: string[] }) => `${d.loc?.slice(-1)[0] ?? ''} ${d.msg ?? ''}`).join('; ')
    }
  } catch {
    /* non-JSON error body */
  }
  if (response.status === 401) window.dispatchEvent(new Event('metjira:unauthorized'))
  if (response.status === 409 && code === 'stale_write') {
    window.dispatchEvent(new CustomEvent('metjira:stale-write'))
  }
  return new ApiError(response.status, tServer(message), code, fields)
}

/** Lets the backend answer AI requests in the UI language. */
function langHeader(): Record<string, string> {
  return { 'X-MetJira-Lang': getLang() }
}

const issueVersions = new Map<string, number>()

function rememberVersions(value: unknown): void {
  if (Array.isArray(value)) {
    value.forEach(rememberVersions)
    return
  }
  if (!value || typeof value !== 'object') return
  const row = value as Record<string, unknown>
  if (typeof row.key === 'string' && typeof row.lock_version === 'number') {
    issueVersions.set(row.key, row.lock_version)
  }
  Object.values(row).forEach(rememberVersions)
}

function withExpectedIssueVersion(method: string, url: string, body: unknown): unknown {
  if (!body || typeof body !== 'object' || body instanceof FormData || Array.isArray(body)) return body
  const match = url.match(/^\/api\/issues\/([^/?]+)(?:\/rank)?$/)
  const isWrite = method === 'PATCH' || (method === 'POST' && url.endsWith('/rank'))
  if (!match || !isWrite) return body
  const row = body as Record<string, unknown>
  const version = issueVersions.get(decodeURIComponent(match[1]))
  return row.expected_version === undefined && version !== undefined
    ? { ...row, expected_version: version }
    : body
}

async function request<T>(method: string, url: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const init: RequestInit = { method, credentials: 'same-origin', headers: langHeader(), signal }
  if (body instanceof FormData) init.body = body
  else if (body !== undefined) {
    init.body = JSON.stringify(withExpectedIssueVersion(method, url, body))
    ;(init.headers as Record<string, string>)['Content-Type'] = 'application/json'
  }
  const response = await fetch(url, init)
  if (!response.ok) throw await parseError(response)
  if (response.status === 204) return undefined as T
  const value = (await response.json()) as T
  rememberVersions(value)
  return value
}

export const api = {
  get: <T>(url: string, signal?: AbortSignal) => request<T>('GET', url, undefined, signal),
  post: <T>(url: string, body?: unknown, signal?: AbortSignal) => request<T>('POST', url, body ?? {}, signal),
  put: <T>(url: string, body?: unknown, signal?: AbortSignal) => request<T>('PUT', url, body ?? {}, signal),
  patch: <T>(url: string, body?: unknown, signal?: AbortSignal) => request<T>('PATCH', url, body ?? {}, signal),
  del: <T>(url: string, signal?: AbortSignal) => request<T>('DELETE', url, undefined, signal),
  upload: <T>(url: string, form: FormData, signal?: AbortSignal) => request<T>('POST', url, form, signal),
}

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export interface StreamEvent {
  type: 'delta' | 'done' | 'error' | 'tool_call' | 'tool_result' | 'action' | 'report' | 'conversation' | 'message'
  id?: number
  text?: string
  message?: string
  name?: string
  args?: Record<string, unknown>
  summary?: string
  ok?: boolean
  action?: import('./types').AgentAction
}

/** POST and consume a text/event-stream response. */
export async function streamPost(
  url: string,
  body: unknown,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...langHeader() },
    body: JSON.stringify(body),
    signal,
  })
  if (!response.ok || !response.body) throw await parseError(response)
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let index = buffer.indexOf('\n\n')
    while (index !== -1) {
      const chunk = buffer.slice(0, index)
      buffer = buffer.slice(index + 2)
      for (const line of chunk.split('\n')) {
        if (line.startsWith('data: ')) {
          try {
            onEvent(JSON.parse(line.slice(6)) as StreamEvent)
          } catch {
            /* ignore malformed chunk */
          }
        }
      }
      index = buffer.indexOf('\n\n')
    }
  }
}
