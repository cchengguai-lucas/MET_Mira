import { Link } from 'react-router-dom'
import { ActivityText } from '../components/ActivityText'
import { Avatar, Empty, TypeIcon } from '../components/ui'
import { useApp } from '../context'
import { t } from '../i18n'
import { timeAgo } from '../labels'
import type { Activity, IssueType } from '../types'

export interface PortfolioActivityItem extends Activity {
  project_key: string
  project_name: string
  project_color: string
  issue?: { key: string; title: string; type?: string } | null
}

export function activityPreview(item: PortfolioActivityItem | null | undefined): string {
  if (!item?.issue) return ''
  return `${item.issue.key} · ${item.issue.title}`
}

export function PortfolioRecentActivity({
  items,
  activeKey,
  summaryHref,
}: {
  items: PortfolioActivityItem[]
  /** Highlight rows from the selected project; the feed itself stays cross-project. */
  activeKey?: string | null
  summaryHref?: string | null
}) {
  const { openIssue } = useApp()
  const rows = items

  return (
    <div className="card">
      <div className="card-header">
        <h3>{t('最近动态')}</h3>
        <span className="tiny faint">{t('范围内各项目')}</span>
        <span className="spacer" />
        {summaryHref && (
          <Link to={summaryHref} className="small">{t('查看更多')} →</Link>
        )}
      </div>
      {rows.length === 0 ? (
        <Empty title={t('暂无动态')} />
      ) : (
        <div>
          {rows.slice(0, 10).map((item) => (
            <div
              key={item.id}
              className="activity-item compact"
              style={{
                cursor: item.issue ? 'pointer' : undefined,
                background: item.project_key === activeKey
                  ? 'color-mix(in srgb, var(--accent) 6%, transparent)'
                  : undefined,
              }}
              onClick={() => item.issue && openIssue(item.issue.key)}
            >
              <Avatar user={item.actor} size={22} />
              <div className="activity-lines">
                <div className="activity-line">
                  <span className="ellipsis">
                    <ActivityText activity={item} />
                  </span>
                  <span className="tiny faint">{timeAgo(item.created_at)}</span>
                </div>
                <div className="small ellipsis muted row" style={{ gap: 6 }}>
                  <span style={{ color: item.project_color, fontWeight: 650 }}>{item.project_key}</span>
                  {item.issue && (
                    <>
                      <TypeIcon type={(item.issue.type as IssueType) || 'task'} size={14} />
                      <span className="issue-key">{item.issue.key}</span>
                      <span className="ellipsis">{item.issue.title}</span>
                    </>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
