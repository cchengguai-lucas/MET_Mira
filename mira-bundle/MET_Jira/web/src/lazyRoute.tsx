import { Component, lazy, type ComponentType, type ReactNode } from 'react'
import { t } from './i18n'

const RELOAD_KEY = 'metjira:chunk-reload'

function isChunkLoadError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? '')
  return /ChunkLoadError|Loading chunk [\d]+ failed|Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed/i.test(
    message,
  )
}

/** One automatic full reload per tab session when a stale Vite chunk 404s after deploy. */
export function reloadOnceForStaleChunk(error: unknown): boolean {
  if (!isChunkLoadError(error)) return false
  try {
    if (sessionStorage.getItem(RELOAD_KEY)) {
      sessionStorage.removeItem(RELOAD_KEY)
      return false
    }
    sessionStorage.setItem(RELOAD_KEY, '1')
  } catch {
    /* private mode / blocked storage — still attempt a single reload */
  }
  window.location.reload()
  return true
}

/**
 * Like React.lazy, but recovers from post-deploy chunk 404s by reloading once
 * so the browser picks up the fresh index.html asset map.
 */
export function lazyRoute<T extends ComponentType<unknown>>(
  factory: () => Promise<{ default: T }>,
) {
  return lazy(() =>
    factory()
      .then((module) => {
        // A fresh entry successfully loaded its route. Clear the loop guard so
        // a later deployment can recover independently.
        try {
          sessionStorage.removeItem(RELOAD_KEY)
        } catch {
          /* storage can be unavailable in hardened/private contexts */
        }
        return module
      })
      .catch((error: unknown) => {
        if (reloadOnceForStaleChunk(error)) {
          return new Promise<{ default: T }>(() => {
            /* pending until the document unloads */
          })
        }
        throw error
      }),
  )
}

type BoundaryProps = { children: ReactNode }
type BoundaryState = { error: Error | null }

/** Catches render-time chunk failures that escape lazy()'s promise path. */
export class ChunkErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): BoundaryState {
    return { error }
  }

  componentDidCatch(error: Error) {
    if (reloadOnceForStaleChunk(error)) return
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="page" style={{ padding: 48, textAlign: 'center' }}>
        <h1 style={{ fontSize: 20, marginBottom: 8 }}>{t('页面加载失败')}</h1>
        <p className="muted" style={{ marginBottom: 16 }}>
          {t('可能是版本刚更新，请刷新后重试。')}
        </p>
        <button type="button" className="btn primary" onClick={() => window.location.reload()}>
          {t('刷新页面')}
        </button>
      </div>
    )
  }
}
