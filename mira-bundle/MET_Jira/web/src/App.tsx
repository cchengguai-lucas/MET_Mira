import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Suspense, useEffect } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { Spinner } from './components/ui'
import { Layout } from './components/Layout'
import { AppProvider } from './context'
import { ChunkErrorBoundary, lazyRoute } from './lazyRoute'
import { LoginPage } from './pages/Login'
import { fetchMe } from './shortcuts'

const HomePage = lazyRoute(() => import('./pages/Home').then((module) => ({ default: module.HomePage })))
const ProjectsPage = lazyRoute(() =>
  import('./pages/Projects').then((module) => ({ default: module.ProjectsPage })),
)
const ProjectLayout = lazyRoute(() =>
  import('./pages/ProjectLayout').then((module) => ({ default: module.ProjectLayout })),
)
const SummaryPage = lazyRoute(() =>
  import('./pages/Summary').then((module) => ({ default: module.SummaryPage })),
)
const BoardPage = lazyRoute(() => import('./pages/Board').then((module) => ({ default: module.BoardPage })))
const BacklogPage = lazyRoute(() =>
  import('./pages/Backlog').then((module) => ({ default: module.BacklogPage })),
)
const ListPage = lazyRoute(() => import('./pages/ListView').then((module) => ({ default: module.ListPage })))
const TimelinePage = lazyRoute(() =>
  import('./pages/Timeline').then((module) => ({ default: module.TimelinePage })),
)
const IssuePage = lazyRoute(() => import('./pages/IssuePage').then((module) => ({ default: module.IssuePage })))
const SearchPage = lazyRoute(() =>
  import('./pages/Search').then((module) => ({ default: module.SearchPage })),
)
const SharedFiltersPage = lazyRoute(() =>
  import('./pages/SharedFilters').then((module) => ({ default: module.SharedFiltersPage })),
)
const SettingsLayout = lazyRoute(() =>
  import('./pages/Settings').then((module) => ({ default: module.SettingsLayout })),
)
const SettingsHub = lazyRoute(() =>
  import('./pages/Settings').then((module) => ({ default: module.SettingsHub })),
)
const NotificationsSettings = lazyRoute(() =>
  import('./pages/Settings').then((module) => ({ default: module.NotificationsSettings })),
)
const ReportsPage = lazyRoute(() => import('./pages/Reports').then((m) => ({ default: m.ReportsPage })))
const ProjectSettingsPage = lazyRoute(() =>
  import('./pages/ProjectSettings').then((m) => ({ default: m.ProjectSettingsPage })),
)
const AdminPage = lazyRoute(() => import('./pages/Admin').then((m) => ({ default: m.AdminPage })))
const OrgPage = lazyRoute(() => import('./pages/Org').then((m) => ({ default: m.OrgPage })))
const GuidePage = lazyRoute(() => import('./pages/Guide').then((m) => ({ default: m.GuidePage })))
const GuideGlossaryPage = lazyRoute(() =>
  import('./pages/GuideGlossary').then((m) => ({ default: m.GuideGlossaryPage })),
)
const PersonPage = lazyRoute(() => import('./pages/Person').then((m) => ({ default: m.PersonPage })))

export function App() {
  const client = useQueryClient()
  const me = useQuery({
    queryKey: ['me'],
    queryFn: fetchMe,
    retry: false,
    staleTime: 60_000,
  })

  useEffect(() => {
    const handler = () => client.setQueryData(['me'], null)
    window.addEventListener('metjira:unauthorized', handler)
    return () => window.removeEventListener('metjira:unauthorized', handler)
  }, [client])

  if (me.isLoading) return <Spinner center />
  if (!me.data?.user) {
    return (
      <LoginPage
        onLogin={() => {
          client.invalidateQueries({ queryKey: ['me'] })
          client.invalidateQueries({ queryKey: ['projects'] })
          client.invalidateQueries({ queryKey: ['dashboard'] })
        }}
      />
    )
  }

  return (
    <AppProvider user={me.data.user}>
      <ChunkErrorBoundary>
        <Suspense fallback={<Spinner center />}>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<HomePage />} />
              <Route path="projects" element={<ProjectsPage />} />
              <Route path="projects/:key" element={<ProjectLayout />}>
                <Route index element={<Navigate to="board" replace />} />
                <Route path="summary" element={<SummaryPage />} />
                <Route path="board" element={<BoardPage />} />
                <Route path="backlog" element={<BacklogPage />} />
                <Route path="list" element={<ListPage />} />
                <Route path="timeline" element={<TimelinePage />} />
                <Route path="reports" element={<ReportsPage />} />
                <Route path="settings" element={<ProjectSettingsPage />} />
              </Route>
              <Route path="issues/:key" element={<IssuePage />} />
              <Route path="search" element={<SearchPage />} />
              <Route path="search/shared" element={<SharedFiltersPage />} />
              <Route path="org" element={<OrgPage />} />
              <Route path="people/:username" element={<PersonPage />} />
              <Route path="admin" element={<AdminPage />} />
              <Route path="settings" element={<SettingsLayout />}>
                <Route index element={<SettingsHub />} />
                <Route path="notifications" element={<NotificationsSettings />} />
              </Route>
              <Route path="guide" element={<GuidePage />} />
              <Route path="guide/glossary" element={<GuideGlossaryPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </Suspense>
      </ChunkErrorBoundary>
    </AppProvider>
  )
}
