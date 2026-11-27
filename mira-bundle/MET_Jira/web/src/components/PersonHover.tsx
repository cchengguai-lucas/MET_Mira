import { useQuery } from '@tanstack/react-query'
import { useCallback, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useOptionalApp } from '../context'
import { t } from '../i18n'
import { timeAgo } from '../labels'
import type { Issue, UserBrief, UserFull } from '../types'
import { Avatar, Popover, Spinner, StatusPill, TypeIcon } from './ui'

interface PersonProfile {
  user: UserFull
  assigned: Issue[]
  reported: Issue[]
  testing: Issue[]
}

const OPEN_MS = 380
const CLOSE_MS = 220

/** Hover (or focus) a person chip to show a Jira-style profile card with recent work. */
export function PersonHover({
  user,
  children,
  disabled,
}: {
  user: UserBrief
  children: ReactNode
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const anchor = useRef<HTMLSpanElement>(null)
  const openTimer = useRef<number | null>(null)
  const closeTimer = useRef<number | null>(null)

  const clearTimers = () => {
    if (openTimer.current) window.clearTimeout(openTimer.current)
    if (closeTimer.current) window.clearTimeout(closeTimer.current)
    openTimer.current = null
    closeTimer.current = null
  }

  const scheduleOpen = useCallback(() => {
    if (disabled) return
    clearTimers()
    openTimer.current = window.setTimeout(() => setOpen(true), OPEN_MS)
  }, [disabled])

  const scheduleClose = useCallback(() => {
    clearTimers()
    closeTimer.current = window.setTimeout(() => setOpen(false), CLOSE_MS)
  }, [])

  const keepOpen = useCallback(() => {
    clearTimers()
    setOpen(true)
  }, [])

  if (disabled) return <>{children}</>

  return (
    <>
      <span
        ref={anchor}
        className="person-hover-anchor"
        onMouseEnter={scheduleOpen}
        onMouseLeave={scheduleClose}
        onFocus={scheduleOpen}
        onBlur={scheduleClose}
      >
        {children}
      </span>
      {open && (
        <Popover
          anchor={anchor}
          onClose={() => setOpen(false)}
          minWidth={320}
          fit="content"
          className="person-card-pop"
        >
          <div
            className="person-hover-card"
            onMouseEnter={keepOpen}
            onMouseLeave={scheduleClose}
          >
            <PersonCardBody username={user.username} fallback={user} onNavigate={() => setOpen(false)} />
          </div>
        </Popover>
      )}
    </>
  )
}

function PersonCardBody({
  username,
  fallback,
  onNavigate,
}: {
  username: string
  fallback: UserBrief
  onNavigate: () => void
}) {
  const navigate = useNavigate()
  const app = useOptionalApp()
  const { data, isLoading, error } = useQuery({
    queryKey: ['person', username],
    queryFn: () => api.get<PersonProfile>(`/api/users/${username}`),
    staleTime: 60_000,
  })
  const profile = data?.user
  const recent = (data?.assigned ?? []).slice(0, 5)
  const goProfile = () => {
    onNavigate()
    navigate(`/people/${username}`)
  }
  const openIssue = (key: string) => {
    onNavigate()
    if (app) app.openIssue(key)
    else navigate(`/issues/${key}`)
  }

  return (
    <>
      <div className="person-hover-head">
        <Avatar user={profile ?? fallback} size={52} />
        <div className="person-hover-actions">
          <button type="button" className="btn sm" onClick={goProfile}>
            {t('查看资料')}
          </button>
        </div>
      </div>
      <div className="person-hover-identity">
        <div className="person-hover-name">{profile?.display_name ?? fallback.display_name}</div>
        {(profile?.email ?? fallback.email) && (
          <a className="person-hover-email" href={`mailto:${profile?.email ?? fallback.email}`}>
            {profile?.email ?? fallback.email}
          </a>
        )}
        {(profile?.title || profile?.org_path) && (
          <div className="person-hover-meta tiny muted">
            {[profile?.title, profile?.org_path].filter(Boolean).join(' · ')}
          </div>
        )}
      </div>
      <div className="person-hover-recent">
        <div className="person-hover-recent-head">
          <span>{t('最近工作')}</span>
          <Link to={`/people/${username}`} className="person-hover-view-all" onClick={onNavigate}>
            {t('查看全部')}
          </Link>
        </div>
        {isLoading && (
          <div className="person-hover-empty">
            <Spinner />
          </div>
        )}
        {error && <div className="person-hover-empty faint tiny">{(error as Error).message}</div>}
        {!isLoading && !error && recent.length === 0 && (
          <div className="person-hover-empty faint tiny">{t('暂无最近工作')}</div>
        )}
        {recent.map((issue) => (
          <button
            key={issue.id}
            type="button"
            className="person-hover-issue"
            onClick={() => openIssue(issue.key)}
          >
            <TypeIcon type={issue.type} size={16} />
            <span className="person-hover-issue-main">
              <span className="person-hover-issue-title ellipsis">
                <b>{issue.key}</b>: {issue.title}
              </span>
              <span className="tiny faint">{t('更新于 {time}', { time: timeAgo(issue.updated_at) })}</span>
            </span>
            <StatusPill status={issue.status} />
          </button>
        ))}
      </div>
    </>
  )
}
