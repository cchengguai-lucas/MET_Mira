import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { Avatar, Empty, Spinner, Tabs } from '../components/ui'
import { t } from '../i18n'
import { fmtDateTime } from '../labels'
import type { Issue, UserBrief, UserFull } from '../types'
import { IssueRows } from './Home'

interface Profile {
  user: UserFull
  manager_chain: UserBrief[]
  direct_reports: UserBrief[]
  assigned: Issue[]
  reported: Issue[]
  testing: Issue[]
}

export function PersonPage() {
  const { username } = useParams()
  const [tab, setTab] = useState<'assigned' | 'reported' | 'testing'>('assigned')
  const { data, isLoading, error } = useQuery({
    queryKey: ['person', username],
    queryFn: () => api.get<Profile>(`/api/users/${username}`),
  })
  if (isLoading) return <Spinner center />
  if (error || !data) return <Empty title={t('用户不存在')}>{(error as Error)?.message}</Empty>
  const u = data.user
  return (
    <div className="page">
      <div className="card card-pad" style={{ marginBottom: 18 }}>
        <div className="row" style={{ gap: 18, alignItems: 'flex-start' }}>
          <Avatar user={u} size={72} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <h1 style={{ fontSize: 26 }}>
              {u.display_name} {!u.enabled && <span className="tag">{t('已停用')}</span>} {u.is_admin && <span className="tag">{t('系统管理员')}</span>} {!u.is_admin && u.can_create_projects && <span className="tag">{t('项目管理员')}</span>}
            </h1>
            <div className="muted">
              {u.title ?? ''} {u.title && u.org_path ? '·' : ''} {u.org_path ?? u.department ?? ''}
            </div>
            <div className="row wrap small" style={{ marginTop: 8, gap: 16 }}>
              <span className="faint">@{u.username}</span>
              {u.email && <a href={`mailto:${u.email}`}>✉ {u.email}</a>}
              {u.phone && <span>☏ {u.phone}</span>}
              <span className="faint">{t('来源：{source}', { source: u.auth_source === 'ldap' ? t('AD 域账号') : t('本地账号') })}</span>
              {u.last_login_at && <span className="faint">{t('最近登录 {time}', { time: fmtDateTime(u.last_login_at) })}</span>}
            </div>
          </div>
        </div>
        {(data.manager_chain.length > 0 || data.direct_reports.length > 0) && (
          <div className="grid grid-2" style={{ marginTop: 18 }}>
            <div>
              <div className="small muted" style={{ marginBottom: 6 }}>
                {t('汇报链')}
              </div>
              <div className="row wrap" style={{ gap: 6 }}>
                {[...data.manager_chain].reverse().map((m) => (
                  <span key={m.id} className="row" style={{ gap: 6 }}>
                    <Link to={`/people/${m.username}`} className="row" style={{ gap: 4 }}>
                      <Avatar user={m} size={20} /> {m.display_name}
                    </Link>
                    →
                  </span>
                ))}
                <b>{u.display_name}</b>
              </div>
            </div>
            <div>
              <div className="small muted" style={{ marginBottom: 6 }}>
                {t('直属下属 {n}', { n: data.direct_reports.length })}
              </div>
              <div className="row wrap" style={{ gap: 8 }}>
                {data.direct_reports.map((r) => (
                  <Link key={r.id} to={`/people/${r.username}`} className="row" style={{ gap: 4 }}>
                    <Avatar user={r} size={20} /> {r.display_name}
                  </Link>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
      <div className="card">
        <div style={{ padding: '6px 16px 0' }}>
          <Tabs
            value={tab}
            onChange={setTab}
            items={[
              { value: 'assigned', label: t('负责中 {n}', { n: data.assigned.length }) },
              { value: 'reported', label: t('报告的 {n}', { n: data.reported.length }) },
              { value: 'testing', label: t('测试中 {n}', { n: data.testing.length }) },
            ]}
          />
        </div>
        <IssueRows issues={data[tab]} />
      </div>
    </div>
  )
}
