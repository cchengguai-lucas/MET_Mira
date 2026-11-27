import { useEffect } from 'react'
import { NavLink, Outlet, useOutletContext, useParams } from 'react-router-dom'
import { TabIcon, type TabIconName } from '../components/NavIcons'
import { Empty, Spinner, slidingThumb } from '../components/ui'
import { useApp } from '../context'
import { getLang, t } from '../i18n'
import { roleLabel } from '../labels'
import { useProjectLiveSync } from '../liveSync'
import { ProjectBadge } from '../projectIcons'
import { useProject } from '../queries'
import type { ProjectDetail } from '../types'

export function useProjectCtx(): ProjectDetail {
  return useOutletContext<ProjectDetail>()
}

export function ProjectLayout() {
  const { key } = useParams()
  const { setCurrentProject } = useApp()
  const project = useProject(key)
  useProjectLiveSync(key)

  useEffect(() => {
    setCurrentProject(key ?? null)
    return () => setCurrentProject(null)
  }, [key, setCurrentProject])

  if (project.isLoading) return <Spinner center />
  if (!project.data) return <Empty title={t('项目不存在')}>{(project.error as Error)?.message}</Empty>
  const p = project.data
  const tabs: [TabIconName, string][] = [
    ['summary', '摘要'],
    ['board', '看板'],
    ['backlog', '待办事项'],
    ['list', '列表视图'],
    ['timeline', '时间线'],
    ['reports', '报表'],
    ['settings', '设置'],
  ]
  return (
    <div className="page page-wide page-full">
      <div className="project-hero">
        <div className="project-header">
          <ProjectBadge icon={p.project.icon} color={p.project.color} label={p.project.key.slice(0, 3)} size={40} />
          <div style={{ minWidth: 0 }}>
            <h1 className="project-hero-title">{p.project.name}</h1>
            <div className="tiny faint project-hero-meta">
              {p.project.key}
              {p.my_roles.length > 0 && ' · ' + t('我的角色：{roles}', { roles: p.my_roles.map((r) => roleLabel(r)).join(getLang() === 'en' ? ', ' : '、') })}
            </div>
          </div>
        </div>
      </div>
      <div className="tabs" ref={slidingThumb}>
        {tabs.map(([path, label]) => (
          <NavLink key={path} to={path} className={({ isActive }) => `tab tab-with-icon ${isActive ? 'active' : ''}`}>
            <TabIcon name={path} />
            {t(label)}
          </NavLink>
        ))}
      </div>
      <Outlet context={p} />
    </div>
  )
}
