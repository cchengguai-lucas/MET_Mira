import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { api } from '../api'
import { useApp } from '../context'
import { locale, t, tServer } from '../i18n'
import { timeAgo } from '../labels'
import {
  RELEASE_ALSO, RELEASE_DATE, RELEASE_ITEMS, markReleaseSeen, releaseAutoOpen, releaseInsertIndex,
} from '../releaseNotes'
import type { Notification } from '../types'
import { Avatar, FieldIcon, useClickOutside, useEscape } from './ui'
import { leaveAnimation } from './leave'

function releaseDateLabel(): string {
  const [year, month, day] = RELEASE_DATE.split('-').map(Number)
  return new Intl.DateTimeFormat(locale(), { year: 'numeric', month: 'long', day: 'numeric' })
    .format(new Date(year, month - 1, day))
}

export function NotificationsMenu() {
  const [open, setOpen] = useState(releaseAutoOpen)
  const openRef = useRef(open)
  openRef.current = open
  const closeMenu = () => {
    if (!openRef.current) return
    setOpen(false)
    markReleaseSeen()
  }
  const ref = useClickOutside<HTMLDivElement>(open, closeMenu)
  useEscape(closeMenu)
  const client = useQueryClient()
  const { openIssue } = useApp()
  const count = useQuery({
    queryKey: ['notifications', 'unread'],
    queryFn: ({ signal }) => api.get<{ unread: number }>('/api/notifications/unread-count', signal),
    refetchInterval: () => (document.hidden ? false : 60_000),
    staleTime: 30_000,
  })
  const list = useQuery({
    queryKey: ['notifications', 'list'],
    queryFn: ({ signal }) => api.get<{ unread: number; notifications: Notification[] }>('/api/notifications?limit=40', signal),
    enabled: open,
    staleTime: 15_000,
  })
  const unread = count.data?.unread ?? list.data?.unread ?? 0
  const notifications = list.data?.notifications ?? []
  const releaseAt = list.data ? releaseInsertIndex(notifications.map((item) => item.created_at)) : 0
  const markRead = async (ids: number[] | null) => {
    await api.post('/api/notifications/read', ids ? { ids } : { all: true })
    client.invalidateQueries({ queryKey: ['notifications'] })
  }
  return (
    <div className="popover-anchor" ref={ref}>
      <button type="button" className="icon-btn notify-btn" onClick={() => (open ? closeMenu() : setOpen(true))} title={t('通知')} aria-label={t('通知')} aria-expanded={open}>
        <FieldIcon name="bell" size={20} />
        {!!unread && (
          <span className="badge-dot">{unread > 99 ? '99+' : unread}</span>
        )}
      </button>
      {open && (
        <div className="menu" ref={leaveAnimation} style={{ right: 0, top: '100%', marginTop: 6, width: 380, maxHeight: 480 }}>
          <div className="row" style={{ padding: '6px 10px' }}>
            <b>{t('通知')}</b>
            <span className="spacer" />
            {!!unread && (
              <button className="btn ghost sm" onClick={() => markRead(null)}>
                {t('全部已读')}
              </button>
            )}
          </div>
          {notifications.slice(0, releaseAt).map((n) => (
            <button
              key={n.id}
              className="menu-item"
              style={{ alignItems: 'flex-start', opacity: n.read ? 0.6 : 1 }}
              onClick={() => {
                if (!n.read) markRead([n.id])
                if (n.issue_key) openIssue(n.issue_key)
                closeMenu()
              }}
            >
              <Avatar user={n.actor} size={26} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="small" style={{ whiteSpace: 'normal' }}>
                  {tServer(n.message)}
                </div>
                <div className="tiny faint">{timeAgo(n.created_at)}</div>
              </div>
              {!n.read && <span style={{ width: 8, height: 8, borderRadius: 4, background: 'var(--accent)', marginTop: 6 }} />}
            </button>
          ))}
          <div className="release-note">
            <h3>{t('Mira 的新变化')}</h3>
            <p className="release-date">{releaseDateLabel()}</p>
            <ul>
              {RELEASE_ITEMS.map((item) => (
                <li key={item.title}>
                  <h4>{t(item.title)}</h4>
                  <p>{t(item.body)}</p>
                </li>
              ))}
            </ul>
            <p className="release-also">{t(RELEASE_ALSO)}</p>
          </div>
          {list.data?.notifications.length === 0 && <div className="empty small">{t('暂无通知')}</div>}
          {notifications.slice(releaseAt).map((n) => (
            <button
              key={n.id}
              className="menu-item"
              style={{ alignItems: 'flex-start', opacity: n.read ? 0.6 : 1 }}
              onClick={() => {
                if (!n.read) markRead([n.id])
                if (n.issue_key) openIssue(n.issue_key)
                closeMenu()
              }}
            >
              <Avatar user={n.actor} size={26} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="small" style={{ whiteSpace: 'normal' }}>
                  {tServer(n.message)}
                </div>
                <div className="tiny faint">{timeAgo(n.created_at)}</div>
              </div>
              {!n.read && <span style={{ width: 8, height: 8, borderRadius: 4, background: 'var(--accent)', marginTop: 6 }} />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
