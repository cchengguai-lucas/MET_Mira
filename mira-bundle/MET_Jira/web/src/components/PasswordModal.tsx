import { useState } from 'react'
import { api } from '../api'
import { useApp } from '../context'
import { t } from '../i18n'
import type { UserFull } from '../types'
import { Modal } from './ui'

/** Change own password (no `target`) or, as an admin, reset a local user's password. */
export function PasswordModal({ target, onClose }: { target?: UserFull; onClose: () => void }) {
  const { toast } = useApp()
  const [oldPassword, setOldPassword] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [saving, setSaving] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const mismatch = confirm.length > 0 && confirm !== password
  const shortPassword = password.length > 0 && password.length < 6
  const oldMissing = !target && submitted && !oldPassword
  const passwordMissing = submitted && !password
  const confirmMissing = submitted && !confirm
  const passwordError = shortPassword
    ? t('密码至少 6 位')
    : passwordMissing
      ? t('请填写密码')
      : ''
  const confirmError = mismatch
    ? t('两次输入不一致')
    : confirmMissing
      ? t('请确认新密码')
      : ''
  const valid = password.length >= 6 && password === confirm && (target || !!oldPassword)

  const submit = async () => {
    setSubmitted(true)
    if (!valid) {
      toast(
        (!target && !oldPassword ? t('请填写当前密码') : '')
          || (!password ? t('请填写密码') : password.length < 6 ? t('密码至少 6 位') : '')
          || (!confirm ? t('请确认新密码') : mismatch ? t('两次输入不一致') : '')
          || t('请检查标红的输入项'),
        'error',
      )
      return
    }
    setSaving(true)
    try {
      if (target) await api.put(`/api/admin/users/${target.id}`, { password })
      else await api.post('/api/auth/me/password', { old_password: oldPassword, new_password: password })
      toast(t('密码已更新'), 'success')
      onClose()
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title={target ? t('重置密码 · {name}', { name: target.display_name }) : t('修改密码')}
      size="narrow"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            {t('取消')}
          </button>
          <button className="btn primary" onClick={submit} disabled={saving}>
            {t('保存')}
          </button>
        </>
      }
    >
      <form
        className="col"
        style={{ gap: 12 }}
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        {!target && (
          <div className={`field${oldMissing ? ' is-invalid' : ''}`}>
            <label>{t('当前密码')}</label>
            <input
              className={`input${oldMissing ? ' is-invalid' : ''}`}
              type="password"
              autoFocus
              autoComplete="current-password"
              value={oldPassword}
              aria-invalid={oldMissing}
              onChange={(e) => setOldPassword(e.target.value)}
            />
            {oldMissing ? <div className="field-error" role="alert">{t('请填写当前密码')}</div> : null}
          </div>
        )}
        <div className={`field${passwordError ? ' is-invalid' : ''}`}>
          <label>{t('新密码（至少 6 位）')}</label>
          <input
            className={`input${passwordError ? ' is-invalid' : ''}`}
            type="password"
            autoFocus={!!target}
            autoComplete="new-password"
            value={password}
            aria-invalid={!!passwordError}
            onChange={(e) => setPassword(e.target.value)}
          />
          {passwordError ? <div className="field-error" role="alert">{passwordError}</div> : null}
        </div>
        <div className={`field${confirmError ? ' is-invalid' : ''}`}>
          <label>{t('确认新密码')}</label>
          <input
            className={`input${confirmError ? ' is-invalid' : ''}`}
            type="password"
            autoComplete="new-password"
            value={confirm}
            aria-invalid={!!confirmError}
            onChange={(e) => setConfirm(e.target.value)}
          />
          {confirmError ? <div className="field-error" role="alert">{confirmError}</div> : null}
        </div>
        <button type="submit" hidden />
      </form>
    </Modal>
  )
}
