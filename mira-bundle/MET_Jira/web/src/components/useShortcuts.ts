import { useCallback, useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useApp } from '../context'
import { t } from '../i18n'
import { ACTION_BY_ID, type ActionId, type Bindings, comboFromEvent } from '../shortcuts'

/** Runs a shortcut action; project views use the project in the URL, else the last opened one. */
export function useRunAction(openPalette: () => void) {
  const { user, openCreate, aiOpen, toggleAi, closeAi, toggleTheme, openShortcuts, currentProject, toast } = useApp()
  const navigate = useNavigate()
  const location = useLocation()
  return useCallback((id: ActionId) => {
    const project = location.pathname.match(/^\/projects\/([^/]+)/)?.[1] ?? currentProject
    const view = (path: string) => {
      if (project) navigate(`/projects/${project}/${path}`)
      else toast(t('先打开一个项目，再用快捷键切换到「{view}」', { view: t(ACTION_BY_ID[id].label) }))
    }
    if (id === 'ai') return toggleAi()
    // Any other shortcut means moving on, so the assistant tucks itself away first.
    if (aiOpen && id !== 'theme') closeAi()
    switch (id) {
      case 'create': return openCreate()
      case 'ai_create': return openCreate({ aiText: '' })
      case 'palette': return openPalette()
      case 'shortcuts': return openShortcuts()
      case 'my_work': return navigate('/')
      case 'projects': return navigate('/projects')
      case 'search': return navigate('/search')
      case 'org': return navigate('/org')
      case 'profile': return navigate(`/people/${user.username}`)
      case 'guide': return navigate('/guide')
      case 'admin': return user.is_admin ? navigate('/admin') : undefined
      case 'summary': return view('summary')
      case 'board': return view('board')
      case 'backlog': return view('backlog')
      case 'list': return view('list')
      case 'timeline': return view('timeline')
      case 'reports': return view('reports')
      case 'theme': return toggleTheme()
    }
  }, [location.pathname, currentProject, navigate, toast, openCreate, aiOpen, toggleAi, closeAi, openPalette, openShortcuts, user, toggleTheme])
}

/**
 * Global key listener. Ctrl/⌘+K always toggles quick jump; personal shortcuts fire only when
 * nobody is typing and no modal is open, so they never steal keys from inputs or dialogs.
 */
export function useGlobalShortcuts(bindings: Bindings, run: (id: ActionId) => void, togglePalette: () => void) {
  const latest = useRef({ bindings, run, togglePalette })
  latest.current = { bindings, run, togglePalette }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.code === 'KeyK') {
        e.preventDefault()
        latest.current.togglePalette()
        return
      }
      const target = e.target as HTMLElement
      const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable
      if (typing || e.isComposing || e.repeat || document.querySelector('.overlay:not(.leaving), .lightbox')) return
      const combo = comboFromEvent(e)
      if (!combo) return
      const entry = Object.entries(latest.current.bindings).find(([, value]) => value === combo)
      if (!entry) return
      e.preventDefault()
      latest.current.run(entry[0] as ActionId)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
}
