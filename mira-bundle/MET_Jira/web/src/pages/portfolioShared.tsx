import type { CSSProperties, ReactNode } from 'react'
import { Segmented, Select } from '../components/ui'
import { t } from '../i18n'
import { EN } from '../locales/en'
import { ongoing as ongoingEn } from '../locales/en/ongoing'

Object.assign(EN, ongoingEn)

export type WorkspaceView = 'user' | 'delivery' | 'ongoing'
export type PortfolioScope = 'all' | 'lead' | 'managed'

export function PortfolioScopeSelect({
  scope,
  isAdmin,
  onChange,
}: {
  scope: PortfolioScope
  isAdmin: boolean
  onChange: (value: PortfolioScope) => void
}) {
  return (
    <Select
      className="select sm portfolio-scope"
      value={scope}
      onChange={(event) => onChange(event.target.value as PortfolioScope)}
    >
      {isAdmin && <option value="all">{t('全部可见项目')}</option>}
      <option value="lead">{t('我负责的项目')}</option>
      <option value="managed">{t('我管理的项目')}</option>
    </Select>
  )
}

export function WorkspaceSwitch({
  value,
  onChange,
}: {
  value: WorkspaceView
  onChange: (view: WorkspaceView) => void
}) {
  return (
    <Segmented
      className="home-view-switch"
      value={value}
      onChange={(view) => onChange(view as WorkspaceView)}
      items={[
        { value: 'user', label: t('用户工作台') },
        { value: 'delivery', label: t('交付管理') },
        { value: 'ongoing', label: t('持续运维') },
      ]}
    />
  )
}

export function folderRowStyle(color: string): CSSProperties {
  return { '--folder-color': color } as CSSProperties
}

export type ProjectFolderItem = {
  id: number
  key: string
  name: string
  color: string
  score: number | string
  scoreClass?: string
  meta?: ReactNode
}

export function ProjectFolderRack({
  title,
  ariaLabel,
  scopeSelect,
  projects,
  activeKey,
  onSelect,
}: {
  title: string
  ariaLabel: string
  scopeSelect: ReactNode
  projects: ProjectFolderItem[]
  activeKey: string
  onSelect: (key: string) => void
}) {
  return (
    <aside className="project-folder-rack" role="tablist" aria-label={ariaLabel}>
      <div className="project-folder-tab">
        <h3>{title}</h3>
        <span className="project-folder-count">{projects.length}</span>
      </div>
      <div className="project-folder-body">
        <div className="project-folder-scope">{scopeSelect}</div>
        <div className="project-folder-pocket">
          {projects.map((project, index) => {
            const open = project.key === activeKey
            return (
              <button
                key={project.id}
                type="button"
                role="tab"
                aria-selected={open}
                className={`project-folder-file${open ? ' active' : ''}`}
                style={{
                  ...folderRowStyle(project.color),
                  '--file-i': index,
                } as CSSProperties}
                onClick={() => onSelect(project.key)}
              >
                <span className="project-folder-file-face">
                  <span className="project-folder-file-key">{project.key}</span>
                  <span className="project-folder-file-name">{project.name}</span>
                  {project.meta ? <span className="project-folder-file-meta">{project.meta}</span> : null}
                </span>
                <span className={`risk-score${project.scoreClass ? ` ${project.scoreClass}` : ''}`}>
                  {project.score}
                </span>
              </button>
            )
          })}
        </div>
        <div className="project-folder-lip" aria-hidden />
      </div>
    </aside>
  )
}
