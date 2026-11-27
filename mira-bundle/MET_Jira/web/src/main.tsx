import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { App } from './App'
import { I18nProvider } from './i18n'
import { reloadOnceForStaleChunk } from './lazyRoute'
import './styles/tokens.css'
import './styles.css'

// Vite emits this event when a dynamic import's dependency cannot be fetched,
// most commonly because a deployment replaced the asset map in an open tab.
// Prevent the rejected preload from blanking the React tree and refresh once.
window.addEventListener('vite:preloadError', (event) => {
  event.preventDefault()
  reloadOnceForStaleChunk((event as Event & { payload?: unknown }).payload)
})

const saved = localStorage.getItem('metjira.theme')
document.documentElement.dataset.theme =
  saved || (window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      staleTime: 20_000,
      retry: (count, error) => count < 1 && !(error as { status?: number }).status,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </I18nProvider>
    </QueryClientProvider>
  </StrictMode>,
)
