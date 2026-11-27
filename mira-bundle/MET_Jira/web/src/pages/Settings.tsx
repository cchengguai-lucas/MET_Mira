import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { Segmented, Select, SettingsGlyph, type FieldIconName } from '../components/ui'
import { useApp, type IssueChrome } from '../context'
import { t } from '../i18n'
import { fetchMe, type MeResponse } from '../shortcuts'

export type NotifyPrefs = {
  in_app: boolean
  email: boolean
  email_as_assignee: boolean
  email_as_reporter: boolean
  email_as_watcher: boolean
  email_own_changes: boolean
  email_format: 'html' | 'text'
  batch_minutes: number
  wecom: boolean
}

const DEFAULT_NOTIFY: NotifyPrefs = {
  in_app: true,
  email: true,
  email_as_assignee: true,
  email_as_reporter: true,
  email_as_watcher: true,
  email_own_changes: false,
  email_format: 'html',
  batch_minutes: 0,
  wecom: true,
}

export function readNotifyPrefs(preferences?: Record<string, unknown> | null): NotifyPrefs {
  const raw = (preferences?.notifications ?? {}) as Partial<NotifyPrefs>
  return {
    ...DEFAULT_NOTIFY,
    ...raw,
    email_format: raw.email_format === 'text' ? 'text' : 'html',
    batch_minutes: [0, 3, 10, 30, 60].includes(Number(raw.batch_minutes))
      ? Number(raw.batch_minutes)
      : 0,
    wecom: raw.wecom !== false,
  }
}

export function SettingsLayout() {
  const { user } = useApp()
  return (
    <div className="page page-wide settings-page">
      <div className="page-header">
        <div>
          <div className="breadcrumb">
            <Link to="/">{t('我的工作')}</Link>
            <span>/</span>
            <span>{t('设置')}</span>
          </div>
          <h1>{t('设置')}</h1>
          <p className="muted">{t('个人偏好与系统相关入口，集中在此管理。')}</p>
        </div>
      </div>
      <div className="settings-layout">
        <aside className="settings-nav card">
          <div className="menu-label">{t('个人')}</div>
          <NavLink to="/settings" end className={({ isActive }) => `menu-item${isActive ? ' active' : ''}`}>
            <SettingsGlyph name="settings" size={13} /> {t('概览')}
          </NavLink>
          <NavLink to="/settings/notifications" className={({ isActive }) => `menu-item${isActive ? ' active' : ''}`}>
            <SettingsGlyph name="bell" size={13} /> {t('通知设置')}
          </NavLink>
          <div className="menu-label">{t('系统')}</div>
          {user.is_admin && (
            <NavLink to="/admin" className="menu-item">
              <SettingsGlyph name="admin" size={13} /> {t('管理后台')}
            </NavLink>
          )}
          <NavLink to="/org" className="menu-item">
            <SettingsGlyph name="org" size={13} /> {t('组织架构')}
          </NavLink>
          <NavLink to="/guide" className="menu-item">
            <SettingsGlyph name="help" size={13} /> {t('使用说明')}
          </NavLink>
        </aside>
        <div className="settings-main">
          <Outlet />
        </div>
      </div>
    </div>
  )
}

export function NotificationsSettings() {
  const { toast, user } = useApp()
  const client = useQueryClient()
  const me = useQuery({ queryKey: ['me'], queryFn: fetchMe })
  const [draft, setDraft] = useState<NotifyPrefs>(() =>
    readNotifyPrefs(client.getQueryData<MeResponse>(['me'])?.preferences),
  )
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setDraft(readNotifyPrefs(me.data?.preferences))
  }, [me.data?.preferences])

  const patch = (partial: Partial<NotifyPrefs>) => setDraft((d) => ({ ...d, ...partial }))

  const save = async () => {
    setSaving(true)
    try {
      const result = await api.put<{ preferences: Record<string, unknown> }>('/api/auth/me/preferences', {
        notifications: draft,
      })
      client.setQueryData<MeResponse>(['me'], (old) =>
        old ? { ...old, preferences: result.preferences } : old,
      )
      toast(t('通知设置已保存'), 'success')
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="card card-pad settings-panel">
      <h2>{t('电子邮件和通知')}</h2>
      <p className="settings-lead">
        {t('选择何时接收站内通知、邮件与企业微信提醒。邮件与企业微信仅在系统已配置时生效。')}
      </p>

      <section className="settings-block">
        <h3>{t('站内通知')}</h3>
        <label className="checkbox">
          <input type="checkbox" checked={draft.in_app} onChange={(e) => patch({ in_app: e.target.checked })} />
          {t('在应用内显示问题动态通知')}
        </label>
      </section>

      <section className="settings-block">
        <h3>{t('企业微信')}</h3>
        <label className="checkbox">
          <input type="checkbox" checked={draft.wecom} onChange={(e) => patch({ wecom: e.target.checked })} />
          {t('通过企业微信接收问题提醒')}
        </label>
        {!user.email && (
          <div className="callout" style={{ marginTop: 6 }}>
            {t('当前账号未绑定邮箱，企业微信无法按邮箱匹配推送。')}
          </div>
        )}
        {user.email && (
          <div className="tiny muted" style={{ marginTop: 4 }}>
            {t('按此邮箱匹配企业微信：{email}', { email: user.email })}
          </div>
        )}
        <p className="tiny muted" style={{ marginTop: 6 }}>
          {t('关闭后不再推送 SLA 超时等企业微信提醒；管理后台的测试推送不受影响。')}
        </p>
      </section>

      <section className="settings-block">
        <h3>{t('电子邮件')}</h3>
        <label className="checkbox">
          <input type="checkbox" checked={draft.email} onChange={(e) => patch({ email: e.target.checked })} />
          {t('向我发送有关问题活动的电子邮件')}
        </label>
        {!user.email && (
          <div className="callout" style={{ marginTop: 6 }}>
            {t('当前账号未绑定邮箱，即使开启也不会发出邮件。')}
          </div>
        )}
        {user.email && (
          <div className="tiny muted" style={{ marginTop: 4 }}>
            {t('收件邮箱：{email}', { email: user.email })}
          </div>
        )}
        <div className={`settings-sub${draft.email ? '' : ' disabled'}`}>
          <label className="checkbox">
            <input
              type="checkbox"
              disabled={!draft.email}
              checked={draft.email_as_assignee}
              onChange={(e) => patch({ email_as_assignee: e.target.checked })}
            />
            {t('你是负责人')}
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              disabled={!draft.email}
              checked={draft.email_as_reporter}
              onChange={(e) => patch({ email_as_reporter: e.target.checked })}
            />
            {t('你是报告人')}
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              disabled={!draft.email}
              checked={draft.email_as_watcher}
              onChange={(e) => patch({ email_as_watcher: e.target.checked })}
            />
            {t('你在关注该问题')}
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              disabled={!draft.email}
              checked={draft.email_own_changes}
              onChange={(e) => patch({ email_own_changes: e.target.checked })}
            />
            {t('你对问题做了更改')}
          </label>
        </div>
      </section>

      <section className="settings-block">
        <h3>
          {t('合并通知邮件')}
          <span className="settings-h3-hint">{t('短时间内多条动态合并为一封')}</span>
        </h3>
        <label className="settings-inline">
          <span>{t('发送频率')}</span>
          <Select
            className="select w-mid"
            value={String(draft.batch_minutes)}
            disabled={!draft.email}
            onChange={(e) => patch({ batch_minutes: Number(e.target.value) })}
          >
            <option value={0}>{t('立即发送')}</option>
            <option value={3}>{t('每 3 分钟')}</option>
            <option value={10}>{t('每 10 分钟')}</option>
            <option value={30}>{t('每 30 分钟')}</option>
            <option value={60}>{t('每小时')}</option>
          </Select>
        </label>
      </section>

      <section className="settings-block">
        <h3>{t('邮件格式')}</h3>
        <Segmented
          value={draft.email_format}
          disabled={!draft.email}
          onChange={(email_format) => patch({ email_format })}
          items={[
            { value: 'html', label: 'HTML' },
            { value: 'text', label: t('纯文本') },
          ]}
        />
      </section>

      <div className="row" style={{ marginTop: 12 }}>
        <button className="btn primary" disabled={saving} onClick={save}>
          {saving ? t('保存中…') : t('保存')}
        </button>
      </div>
    </div>
  )
}

export function SettingsHub() {
  const navigate = useNavigate()
  const { theme, toggleTheme, openShortcuts, setIssueChrome, issueChromePref, openLanguage } = useSettingsActions()
  const [chromeOpen, setChromeOpen] = useState(false)

  const tiles: { icon: FieldIconName; title: string; hint: string; onClick: () => void }[] = [
    {
      icon: 'bell',
      title: t('通知设置'),
      hint: t('站内、邮件与企业微信接收方式'),
      onClick: () => navigate('/settings/notifications'),
    },
    {
      icon: theme === 'dark' ? 'theme-sun' : 'theme',
      title: theme === 'dark' ? t('浅色模式') : t('深色模式'),
      hint: t('切换界面主题'),
      onClick: toggleTheme,
    },
    {
      icon: 'language',
      title: t('语言设置'),
      hint: t('界面显示语言'),
      onClick: openLanguage,
    },
    {
      icon: 'chrome',
      title: t('问题打开方式'),
      hint: t('居中悬浮窗或侧边抽屉'),
      onClick: () => setChromeOpen(true),
    },
    {
      icon: 'shortcuts',
      title: t('快捷键设置'),
      hint: t('自定义键盘快捷键'),
      onClick: openShortcuts,
    },
  ]

  return (
    <div className="col" style={{ gap: 14 }}>
      <div className="settings-tiles">
        {tiles.map((tile) => (
          <button key={tile.title} type="button" className="settings-tile" onClick={tile.onClick}>
            <SettingsGlyph name={tile.icon} size={16} />
            <span>
              <b>{tile.title}</b>
              <span className="small muted">{tile.hint}</span>
            </span>
          </button>
        ))}
      </div>
      {chromeOpen && (
        <ChromeQuickPanel
          value={issueChromePref}
          onChange={setIssueChrome}
          onClose={() => setChromeOpen(false)}
        />
      )}
    </div>
  )
}

/** Bridge so Settings can open Layout-owned modals via a tiny event bus. */
function useSettingsActions() {
  const { theme, toggleTheme, openShortcuts, setIssueChrome, issueChromePref } = useApp()
  return {
    theme,
    toggleTheme,
    openShortcuts,
    setIssueChrome,
    issueChromePref,
    openLanguage: () => window.dispatchEvent(new Event('metjira:open-language')),
  }
}

function ChromeQuickPanel({
  value,
  onChange,
  onClose,
}: {
  value: IssueChrome
  onChange: (c: IssueChrome) => void
  onClose: () => void
}) {
  const choices: { value: IssueChrome; label: string }[] = [
    { value: 'modal', label: t('居中悬浮窗') },
    { value: 'drawer-right', label: t('右侧抽屉') },
    { value: 'drawer-left', label: t('左侧抽屉') },
  ]
  return (
    <div className="card card-pad">
      <div className="row" style={{ marginBottom: 10 }}>
        <h3 style={{ margin: 0 }}>{t('问题打开方式')}</h3>
        <span className="spacer" />
        <button className="btn ghost sm" onClick={onClose}>{t('关闭')}</button>
      </div>
      <div className="col" style={{ gap: 8 }}>
        {choices.map((c) => (
          <label key={c.value} className="checkbox">
            <input
              type="radio"
              name="chrome"
              checked={value === c.value}
              onChange={() => onChange(c.value)}
            />
            {c.label}
          </label>
        ))}
      </div>
    </div>
  )
}
