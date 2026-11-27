import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { leaveAnimation } from './components/leave'
import { t } from './i18n'
import { rememberIssueView } from './recentIssues'
import type { UserFull } from './types'

type ToastKind = 'info' | 'success' | 'error'
export type DrawerSide = 'left' | 'right'
/** How issues open from board/list: centered modal (default) or a side drawer. */
export type IssueChrome = 'modal' | 'drawer-left' | 'drawer-right'

const CHROME_KEY = 'metjira.issue_chrome'
const readChrome = (): IssueChrome => {
  const raw = localStorage.getItem(CHROME_KEY)
  return raw === 'drawer-left' || raw === 'drawer-right' || raw === 'modal' ? raw : 'modal'
}

export interface CreateDefaults {
  project?: string
  type?: string
  status_id?: number
  sprint_id?: number
  epic_id?: number
  parent_id?: number
  title?: string
  aiText?: string
}

interface AppValue {
  user: UserFull
  theme: 'light' | 'dark'
  toggleTheme: () => void
  toast: (message: string, kind?: ToastKind) => void
  openIssue: (key: string, options?: { side?: DrawerSide; highlightFields?: string[] }) => void
  /** Fields to pulse in the open issue after a transition gate failure. */
  gateHighlight: { issueKey: string; fields: string[] } | null
  clearGateHighlight: () => void
  closeIssue: () => void
  /** Effective chrome for the current overlay (preference, or a one-shot side override). */
  issueChrome: IssueChrome
  /** Persisted preference; does not clear a one-shot override until the overlay closes. */
  issueChromePref: IssueChrome
  setIssueChrome: (chrome: IssueChrome) => void
  /** @deprecated Derived from issueChrome for callers that still expect left/right. */
  drawerSide: DrawerSide
  createState: CreateDefaults | null
  openCreate: (defaults?: CreateDefaults) => void
  closeCreate: () => void
  aiOpen: boolean
  /** A new object per request, so asking the same prompt twice still sends it. */
  aiSeed: { text: string } | null
  openAi: (prompt?: string) => void
  closeAi: () => void
  toggleAi: () => void
  shortcutsOpen: boolean
  openShortcuts: () => void
  closeShortcuts: () => void
  currentProject: string | null
  setCurrentProject: (key: string | null) => void
}

const AppContext = createContext<AppValue | null>(null)

export function useOptionalApp(): AppValue | null {
  return useContext(AppContext)
}

export function useApp(): AppValue {
  const value = useContext(AppContext)
  if (!value) throw new Error('AppContext missing')
  return value
}

interface Toast {
  id: number
  message: string
  kind: ToastKind
}

export function AppProvider({ user, children }: { user: UserFull; children: ReactNode }) {
  const [theme, setTheme] = useState<'light' | 'dark'>(
    () => (localStorage.getItem('metjira.theme') as 'light' | 'dark') ||
      (window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'),
  )
  const [toasts, setToasts] = useState<Toast[]>([])
  const [createState, setCreateState] = useState<CreateDefaults | null>(null)
  const [aiOpen, setAiOpen] = useState(false)
  const [aiSeed, setAiSeed] = useState<{ text: string } | null>(null)
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const [currentProject, setCurrentProject] = useState<string | null>(null)
  const [, setParams] = useSearchParams()
  const [issueChromePref, setIssueChromePref] = useState<IssueChrome>(readChrome)
  /** One-shot override (e.g. reports open left) — cleared when the overlay closes. */
  const [chromeOverride, setChromeOverride] = useState<IssueChrome | null>(null)
  const [gateHighlight, setGateHighlight] = useState<{ issueKey: string; fields: string[] } | null>(null)
  const seq = useRef(0)
  const issueChrome = chromeOverride ?? issueChromePref
  const drawerSide: DrawerSide = issueChrome === 'drawer-left' ? 'left' : 'right'

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('metjira.theme', theme)
  }, [theme])

  const setIssueChrome = useCallback((chrome: IssueChrome) => {
    setIssueChromePref(chrome)
    localStorage.setItem(CHROME_KEY, chrome)
    setChromeOverride(null)
  }, [])

  const toastTimers = useRef<ReturnType<typeof setTimeout>[]>([])
  useEffect(() => () => {
    for (const id of toastTimers.current) clearTimeout(id)
  }, [])

  const toast = useCallback((message: string, kind: ToastKind = 'info') => {
    const id = ++seq.current
    setToasts((list) => [...list, { id, message, kind }])
    const timer = setTimeout(() => setToasts((list) => list.filter((t) => t.id !== id)), kind === 'error' ? 5000 : 2800)
    toastTimers.current.push(timer)
  }, [])

  useEffect(() => {
    const onStaleWrite = () => {
      toast(t('内容已被其他人更新；你的编辑仍保留，请刷新后重试'), 'error')
    }
    window.addEventListener('metjira:stale-write', onStaleWrite)
    return () => window.removeEventListener('metjira:stale-write', onStaleWrite)
  }, [toast])

  const openIssue = useCallback(
    (key: string, options?: { side?: DrawerSide; highlightFields?: string[] }) => {
      rememberIssueView(key)
      if (options?.side) setChromeOverride(options.side === 'left' ? 'drawer-left' : 'drawer-right')
      else setChromeOverride(null)
      if (options?.highlightFields?.length) {
        setGateHighlight({ issueKey: key, fields: options.highlightFields })
      }
      setParams((prev) => {
        const next = new URLSearchParams(prev)
        next.set('issue', key)
        return next
      })
    },
    [setParams],
  )
  const clearGateHighlight = useCallback(() => setGateHighlight(null), [])
  const closeIssue = useCallback(() => {
    setChromeOverride(null)
    setGateHighlight(null)
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      next.delete('issue')
      return next
    })
  }, [setParams])

  const value = useMemo<AppValue>(
    () => ({
      user,
      theme,
      toggleTheme: () => setTheme((t) => (t === 'dark' ? 'light' : 'dark')),
      toast,
      openIssue,
      gateHighlight,
      clearGateHighlight,
      closeIssue,
      issueChrome,
      issueChromePref,
      setIssueChrome,
      drawerSide,
      createState,
      openCreate: (defaults) => setCreateState({ project: currentProject ?? undefined, ...defaults }),
      closeCreate: () => setCreateState(null),
      aiOpen,
      aiSeed,
      openAi: (prompt) => {
        setAiSeed(prompt ? { text: prompt } : null)
        setAiOpen(true)
      },
      closeAi: () => setAiOpen(false),
      toggleAi: () => setAiOpen((open) => !open),
      shortcutsOpen,
      openShortcuts: () => setShortcutsOpen(true),
      closeShortcuts: () => setShortcutsOpen(false),
      currentProject,
      setCurrentProject,
    }),
    [user, theme, toast, openIssue, gateHighlight, clearGateHighlight, closeIssue, issueChrome, issueChromePref, setIssueChrome, drawerSide, createState, aiOpen, aiSeed, shortcutsOpen, currentProject],
  )

  return (
    <AppContext.Provider value={value}>
      {children}
      <div className="toasts" aria-live="polite" aria-atomic="true">
        {toasts.map((t) => (
          <div
            key={t.id}
            ref={leaveAnimation}
            className={`toast ${t.kind}`}
            role={t.kind === 'error' ? 'alert' : 'status'}
          >
            {t.message}
          </div>
        ))}
      </div>
    </AppContext.Provider>
  )
}
