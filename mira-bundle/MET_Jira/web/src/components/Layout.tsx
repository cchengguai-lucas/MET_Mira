import { useQuery, useQueryClient } from '@tanstack/react-query'
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import '@fontsource/silkscreen/latin-400.css'
import logo from '../assets/met-jira-mark.png'
import { useApp, type IssueChrome } from '../context'
import { LANGUAGES, pick, t, useI18n } from '../i18n'
import { usePinnedProjects } from '../pinnedProjects'
import { usePresence } from '../presence'
import { useDeployEnv, useProjects } from '../queries'
import { fetchMe, type ActionId, comboText, useShortcutBindings } from '../shortcuts'
import { ProjectBadge } from '../projectIcons'
import type { Project } from '../types'
import '@fontsource/barlow/latin-600.css'
import { NavIcon } from './NavIcons'
import { NotificationsMenu } from './NotificationsMenu'
import { useGlobalShortcuts, useRunAction } from './useShortcuts'
import { Avatar, Dropdown, FieldIcon, SettingsGlyph, type FieldIconName, useClickOutside, useEscape } from './ui'
import { leaveAnimation } from './leave'

const AiPanel = lazy(() => import('./AiPanel').then((m) => ({ default: m.AiPanel })))
const CommandPalette = lazy(() => import('./CommandPalette').then((m) => ({ default: m.CommandPalette })))
const CreateIssueModal = lazy(() => import('./CreateIssueModal').then((m) => ({ default: m.CreateIssueModal })))
const IssueOverlay = lazy(() => import('./IssueDetail').then((module) => ({ default: module.IssueOverlay })))
const AvatarModal = lazy(() => import('./AvatarModal').then((module) => ({ default: module.AvatarModal })))
const LanguageModal = lazy(() => import('./LanguageModal').then((module) => ({ default: module.LanguageModal })))
const PasswordModal = lazy(() => import('./PasswordModal').then((module) => ({ default: module.PasswordModal })))
const ShortcutEditor = lazy(() => import('./ShortcutEditor').then((module) => ({ default: module.ShortcutEditor })))

type SettingsAction =
  | 'notifications'
  | 'theme'
  | 'chrome'
  | 'lang'
  | 'shortcuts'
  | 'admin'
  | 'org'
  | 'settings'
  | 'guide'

function SettingsGearMenu({
  theme,
  chromeHint,
  languageHint,
  shortcutsHint,
  isAdmin,
  onAction,
}: {
  theme: string
  chromeHint: string
  languageHint?: string
  shortcutsHint?: string
  isAdmin: boolean
  onAction: (action: SettingsAction) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useClickOutside<HTMLDivElement>(open, () => setOpen(false))
  const run = (action: SettingsAction) => {
    setOpen(false)
    onAction(action)
  }
  const themeIcon: FieldIconName = theme === 'dark' ? 'theme-sun' : 'theme'
  const item = (
    action: SettingsAction,
    icon: FieldIconName,
    label: string,
    hint?: string,
    opts?: { chevron?: boolean },
  ) => (
    <button key={action} type="button" className="settings-gear-item" onClick={() => run(action)}>
      <SettingsGlyph name={icon} />
      <span className="settings-gear-label">{label}</span>
      {hint && <span className="settings-gear-hint">{hint}</span>}
      {opts?.chevron && <span className="settings-gear-chevron" aria-hidden>›</span>}
    </button>
  )
  return (
    <div className="popover-anchor" ref={ref}>
      <button
        type="button"
        className={`icon-btn settings-gear-trigger${open ? ' open' : ''}`}
        title={t('设置')}
        aria-label={t('设置')}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <FieldIcon name="settings" size={20} />
      </button>
      {open && (
        <div className="menu settings-gear-menu" ref={leaveAnimation}>
          <div className="settings-gear-group">
            <div className="settings-gear-heading">{t('个人设置')}</div>
            {item('notifications', 'bell', t('通知设置'), undefined, { chevron: true })}
            {item('theme', themeIcon, theme === 'dark' ? t('浅色模式') : t('深色模式'))}
            {item('chrome', 'chrome', t('问题打开方式'), chromeHint, { chevron: true })}
            {item('lang', 'language', t('语言设置'), languageHint, { chevron: true })}
            {item('shortcuts', 'shortcuts', t('快捷键设置'), shortcutsHint, { chevron: true })}
          </div>
          <div className="settings-gear-group">
            <div className="settings-gear-heading">{t('系统设置')}</div>
            {isAdmin && item('admin', 'admin', t('管理后台'), undefined, { chevron: true })}
            {item('org', 'org', t('组织架构'), undefined, { chevron: true })}
            {item('settings', 'settings', t('全部设置'), undefined, { chevron: true })}
            {item('guide', 'help', t('使用说明'), undefined, { chevron: true })}
          </div>
        </div>
      )}
    </div>
  )
}

const BUILD_CHECK_INTERVAL = 60_000

/**
 * Tabs opened before a release keep running the old bundle, since in-app navigation never refetches
 * index.html. On navigation (at most once a minute) compare the loaded bundle with the deployed one and
 * reload into the new build; navigation is a safe point because no form is mid-edit.
 */
function useReloadOnNewBuild(pathname: string) {
  const [lastCheck] = useState({ at: 0 })
  useEffect(() => {
    if (import.meta.env.DEV || Date.now() - lastCheck.at < BUILD_CHECK_INTERVAL) return
    lastCheck.at = Date.now()
    const loaded = document.querySelector<HTMLScriptElement>('script[type="module"][src*="/assets/index-"]')?.src
    if (!loaded) return
    fetch('/', { cache: 'no-store' })
      .then((r) => r.text())
      .then((html) => {
        const deployed = html.match(/\/assets\/index-[\w-]+\.js/)?.[0]
        if (deployed && !loaded.endsWith(deployed)) window.location.reload()
      })
      .catch(() => {})
  }, [pathname, lastCheck])
}

export function Layout() {
  const { user, theme, toggleTheme, openCreate, toggleAi, createState, aiOpen, shortcutsOpen, openShortcuts, closeShortcuts, issueChromePref, setIssueChrome } = useApp()
  const [aiMounted, setAiMounted] = useState(false)
  if (aiOpen && !aiMounted) setAiMounted(true)
  const { lang } = useI18n()
  const projects = useProjects()
  const [params] = useSearchParams()
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [passwordOpen, setPasswordOpen] = useState(false)
  const [avatarOpen, setAvatarOpen] = useState(false)
  const [languageOpen, setLanguageOpen] = useState(false)
  useEffect(() => {
    const openLang = () => setLanguageOpen(true)
    window.addEventListener('metjira:open-language', openLang)
    return () => window.removeEventListener('metjira:open-language', openLang)
  }, [])
  const [chromeOpen, setChromeOpen] = useState(false)

  const chromeHint = (c: IssueChrome) =>
    c === 'modal' ? t('居中悬浮窗') : c === 'drawer-left' ? t('左侧抽屉') : t('右侧抽屉')
  const navigate = useNavigate()
  const location = useLocation()
  const client = useQueryClient()
  const issueKey = params.get('issue')
  const staging = useDeployEnv() === 'staging'
  usePresence()
  useReloadOnNewBuild(location.pathname)

  const { bindings } = useShortcutBindings()
  const me = useQuery({ queryKey: ['me'], queryFn: fetchMe, staleTime: 60_000 })
  const portfolioAccess = !!me.data?.portfolio_access
  const homeLabel = portfolioAccess ? t('工作台') : t('我的工作')
  const openPalette = useCallback(() => setPaletteOpen(true), [])
  const togglePalette = useCallback(() => setPaletteOpen((o) => !o), [])
  const runAction = useRunAction(openPalette)
  useGlobalShortcuts(bindings, runAction, togglePalette)
  const keyTitle = (label: string, id: ActionId) =>
    bindings[id] ? t('{label}（快捷键 {key}）', { label, key: comboText(bindings[id]) }) : label

  const logout = async () => {
    await api.post('/api/auth/logout')
    client.setQueryData(['me'], null)
    navigate('/')
  }

  const all = projects.data ?? []
  const mine = all.filter((p) => p.is_member)
  // System admins see every project; others prefer membership, else a short recent list.
  const shown = user.is_admin ? all : mine.length ? mine : all.slice(0, 8)
  const projectKey = location.pathname.match(/^\/projects\/([^/]+)/)?.[1]
  const current = all.find((p) => p.key === projectKey)
  const navClass = ({ isActive }: { isActive: boolean }) => `topnav-item ${isActive ? 'active' : ''}`

  return (
    <div className="app">
      <header className="topbar">
        <NavLink to="/" className="brand" title={t('Mira · 宸微设备科技')}>
          <img src={logo} alt="MET" />
          <b>Mira</b>
          {staging && (
            <span className="env-badge" title={t('测试环境：数据与生产隔离')}>
              {t('测试')}
            </span>
          )}
        </NavLink>
        <nav className="topnav">
          <NavLink to="/" end className={navClass} title={homeLabel}>
            <NavIcon name="home" />
            <span className="label">{homeLabel}</span>
          </NavLink>
          <ProjectsMenu
            active={location.pathname.startsWith('/projects')}
            current={current}
            projects={shown}
            mineOnly={!user.is_admin && mine.length > 0}
            label={t('项目')}
            allLabel={t('所有项目')}
          />
          <NavLink to="/search" className={navClass} title={t('筛选')}>
            <NavIcon name="filters" />
            <span className="label">{t('筛选')}</span>
          </NavLink>
          <NavLink to="/org" className={navClass} title={t('组织架构')}>
            <NavIcon name="org" />
            <span className="label">{t('组织架构')}</span>
          </NavLink>
          {user.is_admin && (
            <NavLink to="/admin" className={navClass} title={t('管理后台')}>
              <NavIcon name="admin" />
              <span className="label">{t('管理后台')}</span>
            </NavLink>
          )}
        </nav>
        <div className="spacer" />
        <button type="button" className="search-trigger" onClick={() => setPaletteOpen(true)} title="Ctrl K">
          <FieldIcon name="search" size={15} />
          <span className="label">{t('搜索问题、人员或命令…')}</span>
          <kbd>Ctrl K</kbd>
        </button>
        <button className={`btn ai sm${aiOpen ? ' active' : ''}`} data-ai-toggle onClick={toggleAi} aria-expanded={aiOpen} title={keyTitle(t('AI 助手'), 'ai')}>
          <FieldIcon name="sparkles" size={14} />
          <span className="label">{t('AI 助手')}</span>
        </button>
        <button className="btn primary sm" onClick={() => openCreate()} title={keyTitle(t('创建问题'), 'create')}>
          <FieldIcon name="create" size={14} />
          <span className="label">{t('创建')}</span>
        </button>
        <NotificationsMenu />
        <SettingsGearMenu
          theme={theme}
          chromeHint={chromeHint(issueChromePref)}
          languageHint={LANGUAGES.find((l) => l.value === lang)?.label}
          shortcutsHint={comboText(bindings.shortcuts) || undefined}
          isAdmin={user.is_admin}
          onAction={(action) => {
            if (action === 'notifications') navigate('/settings/notifications')
            if (action === 'settings') navigate('/settings')
            if (action === 'theme') toggleTheme()
            if (action === 'chrome') setChromeOpen(true)
            if (action === 'lang') setLanguageOpen(true)
            if (action === 'shortcuts') openShortcuts()
            if (action === 'admin') navigate('/admin')
            if (action === 'org') navigate('/org')
            if (action === 'guide') navigate('/guide')
          }}
        />
        <Dropdown
          align="right"
          trigger={
            <span className="user-trigger" title={user.display_name}>
              <Avatar user={user} size={30} />
              <span className="user-name ellipsis">{user.display_name}</span>
              <span className="caret">▾</span>
            </span>
          }
          header={
            <div className="user-card">
              <button className="user-card-avatar" title={t('更换头像')} onClick={() => setAvatarOpen(true)}>
                <Avatar user={user} size={48} />
                <span className="edit">✎</span>
              </button>
              <div style={{ minWidth: 0 }}>
                <div className="bold ellipsis">{user.display_name}</div>
                <div className="tiny muted ellipsis">{user.title || user.username}</div>
                <div className="tiny faint ellipsis" title={user.org_path ?? ''}>
                  {user.org_path ?? user.email ?? ''}
                </div>
              </div>
            </div>
          }
          options={[
            { value: 'profile', label: t('个人主页') },
            ...(user.auth_source === 'local' ? [{ value: 'password', label: t('修改密码') }] : []),
            { value: 'logout', label: t('退出登录'), danger: true },
          ]}
          onSelect={(v) => {
            if (v === 'profile') navigate(`/people/${user.username}`)
            if (v === 'password') setPasswordOpen(true)
            if (v === 'logout') logout()
          }}
        />
      </header>
      <main className="content">
        <div className="content-body">
          <Outlet />
        </div>
        <footer className="app-footer">
          <div className="app-footer-inner">
            <p className="app-footer-note">
              {pick({ zh: '第一次使用 Mira？阅读', en: 'New to Mira? Read the' })} <Link to="/guide">{pick({ zh: '使用说明', en: 'user guide' })}</Link>
              {pick({ zh: '，几分钟上手全部功能。', en: ' to learn the essentials in a few minutes.' })}
            </p>
            <div className="app-footer-bar">
              <span className="app-footer-legal">Copyright © {new Date().getFullYear()} Micropolaris · MET Machine Learning Group · Owner: Houcheng Fan · Questions? Feel free to reach out.</span>
              <nav className="app-footer-links">
                <Link to="/guide">{pick({ zh: '使用说明', en: 'User Guide' })}</Link>
                <Link to="/guide/glossary">{pick({ zh: '名词与图表', en: 'Glossary' })}</Link>
                <Link to="/guide#shortcuts">{pick({ zh: '快捷键', en: 'Shortcuts' })}</Link>
                <Link to="/guide#faq">{pick({ zh: '常见问题', en: 'FAQ' })}</Link>
              </nav>
              <button type="button" className="app-footer-locale" onClick={() => setLanguageOpen(true)}>
                {LANGUAGES.find((l) => l.value === lang)?.label}
              </button>
            </div>
          </div>
        </footer>
      </main>
      <Suspense fallback={null}>
        {issueKey && <IssueOverlay issueKey={issueKey} />}
        {createState && <CreateIssueModal />}
        {aiMounted && <AiPanel open={aiOpen} />}
        {paletteOpen && <CommandPalette bindings={bindings} runAction={runAction} onClose={() => setPaletteOpen(false)} />}
        {shortcutsOpen && <ShortcutEditor onClose={closeShortcuts} />}
        {passwordOpen && <PasswordModal onClose={() => setPasswordOpen(false)} />}
        {avatarOpen && <AvatarModal onClose={() => setAvatarOpen(false)} />}
        {languageOpen && <LanguageModal onClose={() => setLanguageOpen(false)} />}
      </Suspense>
      {chromeOpen && (
        <IssueChromeModal
          value={issueChromePref}
          onChange={setIssueChrome}
          onClose={() => setChromeOpen(false)}
        />
      )}
    </div>
  )
}

function IssueChromeModal({
  value,
  onChange,
  onClose,
}: {
  value: IssueChrome
  onChange: (c: IssueChrome) => void
  onClose: () => void
}) {
  useEscape(onClose)
  const choices: { value: IssueChrome; label: string; hint: string }[] = [
    { value: 'modal', label: t('居中悬浮窗'), hint: t('默认：遮罩上的大卡片，适合专注查看') },
    { value: 'drawer-right', label: t('右侧抽屉'), hint: t('从右侧滑出，可对照看板') },
    { value: 'drawer-left', label: t('左侧抽屉'), hint: t('从左侧滑出，适合对照右侧内容') },
  ]
  return (
    <div className="overlay" ref={leaveAnimation} onClick={onClose}>
      <div className="modal narrow" ref={leaveAnimation} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{t('问题打开方式')}</h2>
          <span className="spacer" />
          <button className="btn ghost icon sm" onClick={onClose} aria-label={t('关闭')}>
            ✕
          </button>
        </div>
        <div className="modal-body col" style={{ gap: 8 }}>
          <p className="small muted" style={{ margin: 0 }}>
            {t('选择从看板、列表等打开问题时的呈现方式。设置保存在当前浏览器。')}
          </p>
          {choices.map((c) => (
            <button
              key={c.value}
              type="button"
              className={`menu-item${value === c.value ? ' active' : ''}`}
              style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '10px 12px' }}
              onClick={() => {
                onChange(c.value)
                onClose()
              }}
            >
              <span>
                <div className="bold">{c.label}</div>
                <div className="tiny faint">{c.hint}</div>
              </span>
              {value === c.value && <span className="spacer" />}
              {value === c.value && <span className="tiny">✓</span>}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

function ProjectsMenu({
  active,
  current,
  projects,
  mineOnly,
  label,
  allLabel,
}: {
  active: boolean
  current?: Project
  projects: Project[]
  mineOnly: boolean
  label: string
  allLabel: string
}) {
  const [open, setOpen] = useState(false)
  const ref = useClickOutside<HTMLDivElement>(open, () => setOpen(false))
  const location = useLocation()
  const { pinned, sortPinnedFirst } = usePinnedProjects()
  const pinnedKeys = useMemo(() => new Set(pinned), [pinned])
  const pinnedProjects = useMemo(
    () => sortPinnedFirst(projects.filter((p) => pinnedKeys.has(p.key.toUpperCase()))),
    [projects, pinnedKeys, sortPinnedFirst],
  )
  const otherProjects = useMemo(
    () => projects.filter((p) => !pinnedKeys.has(p.key.toUpperCase())),
    [projects, pinnedKeys],
  )
  useEffect(() => setOpen(false), [location.pathname])

  const renderItem = (p: Project) => (
    <Link key={p.id} to={`/projects/${p.key}/board`} className={`menu-item ${current?.id === p.id ? 'active' : ''}`}>
      <ProjectBadge icon={p.icon} color={p.color} label={p.key.slice(0, 2)} size={18} className="project-dot" />
      <span className="ellipsis" style={{ flex: 1 }}>
        {p.name}
      </span>
      {pinnedKeys.has(p.key.toUpperCase()) && <FieldIcon name="pin" size={12} />}
      <span className="tiny faint mono">{p.key}</span>
      {!!p.open_count && <span className="count">{p.open_count}</span>}
    </Link>
  )

  return (
    <div className="popover-anchor" ref={ref}>
      <button className={`topnav-item ${active ? 'active' : ''}`} onClick={() => setOpen((o) => !o)} title={label}>
        <NavIcon name="projects" />
        <span className="label">{label}</span>
        {current && (
          <span className="current-project">
            <ProjectBadge icon={current.icon} color={current.color} label={current.key.slice(0, 2)} size={16} className="project-dot" />
            <span className="ellipsis">{current.name}</span>
          </span>
        )}
        <span className="caret">▾</span>
      </button>
      {open && (
        <div className="menu projects-menu" ref={leaveAnimation} style={{ top: '100%', marginTop: 6, left: 0 }}>
          <Link to="/projects" className="menu-item">
            <NavIcon name="projects" small /> {allLabel}
          </Link>
          <div className="divider" style={{ margin: '5px 0' }} />
          {pinnedProjects.length > 0 && (
            <>
              <div className="menu-label">{t('置顶项目')}</div>
              {pinnedProjects.map(renderItem)}
              <div className="divider" style={{ margin: '5px 0' }} />
            </>
          )}
          <div className="menu-label">{mineOnly ? t('我参与的项目') : t('最近项目')}</div>
          {otherProjects.map(renderItem)}
          {projects.length === 0 && <div className="menu-label">{t('暂无项目')}</div>}
        </div>
      )}
    </div>
  )
}
