import { useEffect, useState } from 'react'
import { api } from '../api'
import '@fontsource/silkscreen/latin-400.css'
import { Segmented } from '../components/ui'
import { LANGUAGES, t, useI18n } from '../i18n'
import { useAuthConfig } from '../queries'
import './mira-login/tokens.css'
import './mira-login/login.css'
import './mira-login/integration.css'

declare global {
  interface Window {
    __miraTraceBg?: { destroy?: () => void }
  }
}

function EyeIcon({ off }: { off: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z" />
      <circle cx="12" cy="12" r="3" />
      {off && <path d="M3 3l18 18" />}
    </svg>
  )
}

export function LoginPage({ onLogin }: { onLogin: () => void }) {
  const { lang, setLang } = useI18n()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [remember, setRemember] = useState(() => {
    try {
      return localStorage.getItem('mira-login-remember') === '1'
    } catch {
      return false
    }
  })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const config = useAuthConfig()

  useEffect(() => {
    const script = document.createElement('script')
    script.src = '/trace-background.js'
    document.body.appendChild(script)
    return () => {
      window.__miraTraceBg?.destroy?.()
      script.remove()
    }
  }, [])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api.post('/api/auth/login', { username, password, remember })
      onLogin()
    } catch (err) {
      setError((err as Error).message || t('登录失败'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="mira-login">
      <div className="mira-login-hero" aria-hidden="true">
        <h1>MIRA</h1>
        <p className="mira-login-subtitle">Project Tracking System</p>
      </div>

      <section className="mira-login-panel">
        <div className="mira-login-toolbar">
          {config.data?.deploy_env === 'staging' && <span className="mira-login-env">{t('测试环境')}</span>}
          <span className="spacer" />
          <div title={t('语言设置')}>
            <Segmented value={lang} onChange={setLang} items={LANGUAGES.map((l) => ({ value: l.value, label: l.label }))} />
          </div>
        </div>

        <form onSubmit={submit}>
          <div className="mira-login-mark" aria-hidden="true">
            MET.Ai
          </div>

          <label className="field">
            <span>{t('用户名')}</span>
            <span className="capsule">
              <input
                type="text"
                autoFocus
                autoComplete="username"
                placeholder={t('输入用户名')}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </span>
          </label>

          <label className="field">
            <span>{t('密码')}</span>
            <span className="capsule mira-login-password">
              <input
                type={showPassword ? 'text' : 'password'}
                autoComplete="current-password"
                placeholder={t('输入密码')}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <button
                type="button"
                className="mira-login-eye"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? t('隐藏密码') : t('显示密码')}
                title={showPassword ? t('隐藏密码') : t('显示密码')}
              >
                <EyeIcon off={showPassword} />
              </button>
            </span>
          </label>

          <p className="hint">
            {config.data?.ldap_label
              ? t('使用 {label} 域账号登录', { label: config.data.ldap_label })
              : t('使用电脑登录用户名/密码')}
          </p>
          <label className="mira-login-remember">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => {
                const next = e.target.checked
                setRemember(next)
                try {
                  localStorage.setItem('mira-login-remember', next ? '1' : '0')
                } catch {
                  /* private mode */
                }
              }}
            />
            <span>{t('7 天免密登录')}</span>
          </label>
          <button type="submit" disabled={busy || !username || !password}>
            {busy ? t('登录中…') : t('登录')}
          </button>
          {error ? (
            <p className="error" role="alert">
              {error}
            </p>
          ) : null}
          <p className="mira-login-note">{t('域账号首次登录将自动创建资料，密码不会被保存')}</p>
        </form>

        <div className="mira-login-foot">{t('宸微设备科技 · 仅限内部使用')}</div>
      </section>
    </main>
  )
}
