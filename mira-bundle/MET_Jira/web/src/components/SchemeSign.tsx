import { useEffect } from 'react'
import caveatWoff2 from '@fontsource/caveat/files/caveat-latin-600-normal.woff2?url'
import type { UserBrief } from '../types'
import { Avatar } from './ui'

let injected = false

function ensureSignatureFont() {
  if (injected || typeof document === 'undefined') return
  injected = true
  const style = document.createElement('style')
  style.textContent = `@font-face{font-family:'Scheme Sign';font-style:normal;font-weight:600;font-display:swap;src:url(${JSON.stringify(caveatWoff2)}) format('woff2')}`
  document.head.appendChild(style)
}

export function englishSignature(user: UserBrief): string {
  const display = user.display_name.trim()
  if (/^[A-Za-z][A-Za-z .'-]*$/.test(display)) return display
  const source = user.username.includes('@') ? user.username.split('@')[0] : user.username
  const parts = source.split(/[._\-\s]+/).filter(Boolean)
  if (parts.length && parts.every((part) => /^[A-Za-z]/.test(part))) {
    return parts.map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase()).join(' ')
  }
  return display || source
}

export function SchemeSign({ user, size = 22 }: { user: UserBrief; size?: number }) {
  useEffect(() => { ensureSignatureFont() }, [])
  return (
    <span title={user.display_name} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
      <Avatar user={user} size={size} />
      <span style={{
        fontFamily: '"Scheme Sign", Caveat, "Segoe Script", "Apple Chancery", cursive',
        fontSize: size, fontWeight: 600, lineHeight: 1, color: 'var(--text)',
      }}>{englishSignature(user)}</span>
    </span>
  )
}
