import { useEffect, useState } from 'react'
import { t } from '../i18n'

/** Compact confirm sheet asking for a reopen reason before transitioning to「重新打开」. */
export function ReopenReasonModal({
  issueKey,
  onCancel,
  onConfirm,
}: {
  issueKey: string
  onCancel: () => void
  onConfirm: (reason: string) => void
}) {
  const [reason, setReason] = useState('')
  const trimmed = reason.trim()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  return (
    <div
      className="create-discard reopen-reason-overlay"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="reopen-reason-title"
      onMouseDown={(e) => e.target === e.currentTarget && onCancel()}
    >
      <div className="create-discard-card">
        <h4 id="reopen-reason-title">{t('重新打开 {key}', { key: issueKey })}</h4>
        <p>{t('请填写重新打开的原因，将记入历史并通知相关人。测试结果会重置为未设置。')}</p>
        <textarea
          className="textarea"
          autoFocus
          rows={3}
          value={reason}
          placeholder={t('例如：修复方案已确认，需重新验证')}
          onChange={(e) => setReason(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && trimmed) {
              e.preventDefault()
              onConfirm(trimmed)
            }
          }}
        />
        <div className="create-discard-actions" style={{ marginTop: 14 }}>
          <button type="button" className="btn ghost" onClick={onCancel}>{t('取消')}</button>
          <button
            type="button"
            className="btn primary"
            disabled={!trimmed}
            onClick={() => onConfirm(trimmed)}
          >
            {t('确认重新打开')}
          </button>
        </div>
      </div>
    </div>
  )
}
