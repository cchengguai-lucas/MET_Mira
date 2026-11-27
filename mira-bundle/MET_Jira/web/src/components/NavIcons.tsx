import type { ReactNode } from 'react'

function gearPath(teeth = 8, outer = 9.2, inner = 7, hole = 3.1): string {
  const points: string[] = []
  const step = (Math.PI * 2) / teeth
  for (let i = 0; i < teeth; i++) {
    const a = i * step - Math.PI / 2
    for (const [da, r] of [[-0.3, inner], [-0.17, outer], [0.17, outer], [0.3, inner]] as const) {
      const angle = a + da * step * 1.6
      points.push(`${(12 + r * Math.cos(angle)).toFixed(2)} ${(12 + r * Math.sin(angle)).toFixed(2)}`)
    }
  }
  return `M${points.join('L')}Z M${12 + hole} 12a${hole} ${hole} 0 1 0 ${-2 * hole} 0a${hole} ${hole} 0 1 0 ${2 * hole} 0Z`
}

const GEAR = gearPath()

const GLYPHS: Record<NavIconName, ReactNode> = {
  home: <path d="M12 3.8 3.4 10.9a1.1 1.1 0 0 0 1.4 1.7l.7-.6v7a1.2 1.2 0 0 0 1.2 1.2h3.6v-5.4h3.4v5.4h3.6a1.2 1.2 0 0 0 1.2-1.2v-7l.7.6a1.1 1.1 0 0 0 1.4-1.7z" />,
  projects: (
    <>
      <rect x="3.5" y="3.5" width="7.4" height="7.4" rx="2" />
      <rect x="13.1" y="3.5" width="7.4" height="7.4" rx="2" />
      <rect x="3.5" y="13.1" width="7.4" height="7.4" rx="2" />
      <rect x="13.1" y="13.1" width="7.4" height="7.4" rx="2" />
    </>
  ),
  filters: (
    <g fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 6.5h16M7.5 12h9M10.5 17.5h3" />
    </g>
  ),
  org: (
    <>
      <circle cx="16.2" cy="8.6" r="2.9" opacity="0.75" />
      <path d="M13.6 13.4c.8-.3 1.7-.4 2.6-.4 3 0 5.3 1.9 5.3 4.6 0 .6-.4 1-1 1h-4.1c0-2-1-3.9-2.8-5.2z" opacity="0.75" />
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 18.4c0-3.4 2.9-5.6 6.5-5.6s6.5 2.2 6.5 5.6c0 .7-.5 1.2-1.2 1.2H3.7c-.7 0-1.2-.5-1.2-1.2z" />
    </>
  ),
  admin: <path d={GEAR} fillRule="evenodd" />,
}

export type NavIconName = 'home' | 'projects' | 'filters' | 'org' | 'admin'

const TAB_GLYPHS: Record<TabIconName, ReactNode> = {
  summary: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M3.5 12h17M12 3.5c2.4 2.4 3.6 5.2 3.6 8.5s-1.2 6.1-3.6 8.5c-2.4-2.4-3.6-5.2-3.6-8.5s1.2-6.1 3.6-8.5z" />
    </>
  ),
  board: (
    <>
      <rect x="3.5" y="4.5" width="17" height="15" rx="2" />
      <path d="M9.2 4.5v15M14.8 4.5v15" />
    </>
  ),
  backlog: <path d="M4 6.5h16M4 12h16M4 17.5h10" />,
  list: (
    <>
      <rect x="3.5" y="4.5" width="17" height="15" rx="2" />
      <path d="M3.5 9.5h17M3.5 14.5h17M9 9.5v10" />
    </>
  ),
  timeline: (
    <>
      <rect x="3.5" y="4.5" width="10" height="4.5" rx="1.5" />
      <rect x="9.5" y="9.75" width="11" height="4.5" rx="1.5" />
      <rect x="6.5" y="15" width="8" height="4.5" rx="1.5" />
    </>
  ),
  reports: <path d="M4 20h16M7 16.5v-5M12 16.5V6.5M17 16.5v-8" />,
  settings: (
    <>
      <path d="M4 7h9M17 7h3M4 17h3M11 17h9" />
      <circle cx="15" cy="7" r="2.2" />
      <circle cx="9" cy="17" r="2.2" />
    </>
  ),
}

export type TabIconName = 'summary' | 'board' | 'backlog' | 'list' | 'timeline' | 'reports' | 'settings'

/** Outline glyph for project view tabs. */
export function TabIcon({ name }: { name: TabIconName }) {
  return (
    <svg className="tab-icon" viewBox="0 0 24 24" aria-hidden="true">
      <g fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{TAB_GLYPHS[name]}</g>
    </svg>
  )
}

/** Single-color glyph that follows the nav item's text color. */
export function NavIcon({ name, small }: { name: NavIconName; small?: boolean }) {
  return (
    <svg className={`nav-icon${small ? ' sm' : ''}`} viewBox="0 0 24 24" aria-hidden="true">
      <g fill="currentColor">{GLYPHS[name]}</g>
    </svg>
  )
}
