import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Fragment, useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import coverFab from '../assets/cover-presets/fab.jpg'
import coverLayers from '../assets/cover-presets/layers.jpg'
import coverTraces from '../assets/cover-presets/traces.jpg'
import coverWafer from '../assets/cover-presets/wafer.jpg'
import { ColorField } from '../components/ColorField'
import { DeleteProjectModal } from '../components/DeleteProjectModal'
import { UserPicker } from '../components/Pickers'
import { TimeField } from '../components/TimeField'
import { WorkflowAgentBar } from '../components/WorkflowAgentBar'
import { WorkflowGraphEditor } from '../components/WorkflowGraphEditor'
import { Avatar, ConfirmButton, Empty, InfoTip, Modal, Segmented, Spinner, StatusPill, Tabs, TypeIcon, UserChip, Select } from '../components/ui'
import { useApp } from '../context'
import { useUndoableDraft } from '../hooks/useUndoableDraft'
import { t, tServer } from '../i18n'
import { EN } from '../locales/en'
import { members as membersEn } from '../locales/en/members'

Object.assign(EN, membersEn)
import {
  customFieldLabel, fieldLabel, fmtDateTime, ISSUE_TYPES, PRIORITIES, priorityName,
  profileName, roleLabel, sectionName, slaHelp, typeName, weekdayName,
} from '../labels'
import { PROJECT_ICONS, ProjectBadge } from '../projectIcons'
import type { Board, BoardColumn, IssueType, OrgUnit, Project, ProjectDetail, ProjectProfile, ProjectVisibility, ProjectWorkMode, Status, UserBrief } from '../types'
import { projectWorkMode } from '../types'
import { flowLayout, flowStatuses } from '../workflow'
import { lintFlow } from '../workflowLint'
import { useProjectCtx } from './ProjectLayout'

const COVER_PRESETS = [
  { id: 'wafer', label: '晶圆', src: coverWafer },
  { id: 'traces', label: '电路', src: coverTraces },
  { id: 'layers', label: '叠层', src: coverLayers },
  { id: 'fab', label: '洁净间', src: coverFab },
] as const

const SECTIONS: [string, string][] = [
  ['general', '基本信息'],
  ['members', '成员与角色'],
  ['workflow', '状态与工作流'],
  ['machines', '机台'],
  ['fields', '字段选项'],
  ['boards', '看板配置'],
  ['import', 'Excel 导入'],
]

function useRefresh() {
  const client = useQueryClient()
  const project = useProjectCtx()
  return () => {
    client.invalidateQueries({ queryKey: ['project', project.project.key] })
    client.invalidateQueries({ queryKey: ['projects'] })
    client.invalidateQueries({ queryKey: ['board'] })
    client.invalidateQueries({ queryKey: ['schedule-baseline', project.project.key] })
  }
}

function useAction() {
  const { toast } = useApp()
  const refresh = useRefresh()
  return async (fn: () => Promise<unknown>, success = '已保存') => {
    try {
      await fn()
      refresh()
      if (success) toast(t(success), 'success')
      return true
    } catch (err) {
      toast((err as Error).message, 'error')
      return false
    }
  }
}

export function ProjectSettingsPage() {
  const project = useProjectCtx()
  const [params, setParams] = useSearchParams()
  const section = params.get('section') ?? 'general'
  const go = (s: string) => {
    const next = new URLSearchParams(params)
    next.set('section', s)
    setParams(next)
  }
  return (
    <div className="settings-layout">
      <nav className="settings-nav">
        {SECTIONS.map(([id, label]) => (
          <button key={id} className={`nav-item ${section === id ? 'active' : ''}`} onClick={() => go(id)}>
            {t(label)}
          </button>
        ))}
        {!project.is_admin && <div className="tiny faint" style={{ padding: 10 }}>{t('你不是项目管理员，部分设置只读。')}</div>}
      </nav>
      <div>
        {section === 'general' && <General />}
        {section === 'members' && <Members />}
        {section === 'workflow' && <Workflow />}
        {section === 'machines' && <Machines />}
        {section === 'fields' && <Fields />}
        {section === 'boards' && <Boards />}
        {section === 'import' && <ImportSection />}
      </div>
    </div>
  )
}

function CoverEditor({
  projectKey,
  coverUrl,
  coverPreset,
  color,
  readOnly,
}: {
  projectKey: string
  coverUrl?: string | null
  coverPreset?: string | null
  color: string
  readOnly: boolean
}) {
  const { toast } = useApp()
  const refresh = useRefresh()
  const inputRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)

  const apply = async (action: () => Promise<Project>) => {
    if (readOnly || busy) return
    setBusy(true)
    try {
      await action()
      refresh()
      toast(t('封面已更新'), 'success')
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const upload = (file: File | undefined) => {
    if (!file || readOnly) return
    if (!file.type.startsWith('image/')) {
      toast(t('请选择图片文件'), 'error')
      return
    }
    if (file.size > 8 * 1024 * 1024) {
      toast(t('封面图片不能超过 8 MB'), 'error')
      return
    }
    const form = new FormData()
    form.append('file', file)
    void apply(() => api.upload<Project>(`/api/projects/${projectKey}/cover`, form))
  }

  const pickPreset = (id: string) => {
    if (coverPreset === id) return
    void apply(() => api.put<Project>(`/api/projects/${projectKey}/cover-preset`, { preset: id }))
  }

  const clear = async () => {
    if (readOnly || busy || !coverUrl) return
    setBusy(true)
    try {
      await api.del(`/api/projects/${projectKey}/cover`)
      refresh()
      toast(t('封面已移除'), 'success')
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="field">
      <label>{t('项目封面')}</label>
      <div
        className={`project-cover-editor${coverUrl ? '' : ' empty'}`}
        style={coverUrl
          ? { backgroundImage: `url(${coverUrl})` }
          : { background: `linear-gradient(135deg, ${color}, color-mix(in srgb, ${color} 45%, #1d1d1f))` }}
      >
        {!coverUrl && <span className="project-cover-placeholder">{t('未设置封面')}</span>}
      </div>
      <div className="tiny faint" style={{ marginTop: 6 }}>
        {t('可选用默认封面，或上传自己的横向图片（约 1600×480）。仅在「全部项目」卡片上展示。')}
      </div>
      <div className="project-cover-presets" role="listbox" aria-label={t('默认封面')}>
        <button
          type="button"
          className={`project-cover-preset none${!coverUrl ? ' active' : ''}`}
          disabled={readOnly || busy || !coverUrl}
          title={t('无封面')}
          aria-selected={!coverUrl}
          onClick={clear}
        >
          <span>{t('无')}</span>
        </button>
        {COVER_PRESETS.map((preset) => (
          <button
            key={preset.id}
            type="button"
            className={`project-cover-preset${coverPreset === preset.id ? ' active' : ''}`}
            disabled={readOnly || busy}
            title={t(preset.label)}
            aria-label={t(preset.label)}
            aria-selected={coverPreset === preset.id}
            style={{ backgroundImage: `url(${preset.src})` }}
            onClick={() => pickPreset(preset.id)}
          />
        ))}
      </div>
      {!readOnly && (
        <div className="row" style={{ gap: 8, marginTop: 10 }}>
          <input
            ref={inputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            hidden
            onChange={(e) => upload(e.target.files?.[0])}
          />
          <button type="button" className="btn sm" disabled={busy} onClick={() => inputRef.current?.click()}>
            {busy ? t('上传中…') : coverUrl ? t('上传自定义…') : t('上传封面')}
          </button>
          {coverUrl && !coverPreset && (
            <span className="tiny faint">{t('当前为自定义上传')}</span>
          )}
        </div>
      )}
    </div>
  )
}

function IconPicker({
  value,
  color,
  projectKey,
  disabled,
  onChange,
}: {
  value: string | null
  color: string
  projectKey: string
  disabled?: boolean
  onChange: (icon: string | null) => void
}) {
  return (
    <div className="field">
      <label>{t('项目图标')}</label>
      <div className="tiny faint" style={{ marginBottom: 8 }}>
        {t('卡通徽章风格，显示在导航、项目列表和项目页标题旁。留空则显示项目 Key。')}
      </div>
      <div className="project-icon-grid">
        <button
          type="button"
          className={`project-icon-option${!value ? ' active' : ''}`}
          disabled={disabled}
          title={t('使用项目 Key')}
          onClick={() => onChange(null)}
        >
          <ProjectBadge icon={null} color={color} label={projectKey.slice(0, 3)} size={44} className="project-logo" />
        </button>
        {PROJECT_ICONS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`project-icon-option${value === item.id ? ' active' : ''}`}
            disabled={disabled}
            title={t(item.label)}
            onClick={() => onChange(item.id)}
          >
            <ProjectBadge icon={item.id} color={color} label={projectKey.slice(0, 3)} size={44} className="project-logo" />
          </button>
        ))}
      </div>
    </div>
  )
}

function General() {
  const project = useProjectCtx()
  const p = project.project
  const act = useAction()
  const [name, setName] = useState(p.name)
  const [description, setDescription] = useState(p.description)
  const [color, setColor] = useState(p.color)
  const [icon, setIcon] = useState<string | null>(p.icon ?? null)
  const [lead, setLead] = useState<UserBrief | null>(p.lead)
  const [visibility, setVisibility] = useState<ProjectVisibility>(p.settings.visibility ?? 'public')
  const [workMode, setWorkMode] = useState<ProjectWorkMode>(projectWorkMode(p))
  const [planStartDate, setPlanStartDate] = useState(p.plan_start_date ?? '')
  const [targetDate, setTargetDate] = useState(p.target_date ?? '')
  const [migrating, setMigrating] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const { user } = useApp()
  const navigate = useNavigate()
  const ro = !project.is_admin
  const delivery = workMode === 'delivery'
  const datesIncomplete = delivery && (!planStartDate || !targetDate)
  const baseline = useQuery({
    queryKey: ['schedule-baseline', p.key],
    queryFn: () => api.get<{
      ready: boolean
      blockers: string[]
      warnings: string[]
      baseline: null | { version: number; target_date: string; created_at: string; note: string }
    }>(`/api/projects/${p.key}/schedule-baseline`),
    enabled: delivery,
  })
  return (
    <div className="card card-pad col" style={{ gap: 14, maxWidth: 640 }}>
      <h3>{t('基本信息')}</h3>
      <CoverEditor
        projectKey={p.key}
        coverUrl={p.cover_url}
        coverPreset={p.cover_preset}
        color={color}
        readOnly={ro}
      />
      <div className="field">
        <label>{t('项目名称')}</label>
        <input className="input" disabled={ro} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field">
        <label>{t('描述')}</label>
        <textarea className="textarea" disabled={ro} value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>
      <div className="field">
        <label>{t('项目类型')}</label>
        <Segmented
          disabled={ro}
          value={workMode}
          onChange={setWorkMode}
          items={[
            { value: 'delivery', label: t('交付项目') },
            { value: 'ongoing', label: t('持续项目') },
          ]}
        />
        <div className="tiny faint" style={{ marginTop: 4 }}>
          {delivery
            ? t('有明确目标日期，可发布排期基线并跟踪延期风险。')
            : t('长期事务、备忘跟进或杂项清单；保留完整流转，不发布排期基线。')}
        </div>
      </div>
      {delivery && datesIncomplete && (
        <div className="callout">
          <div className="small warn-text">
            {t('请尽快补齐计划开始日期和目标日期，以便后续发布排期基线。')}
          </div>
        </div>
      )}
      {delivery && (
        <>
          <div className="grid grid-2" style={{ gap: 12 }}>
            <div className="field">
              <label>{t('计划开始日期')}</label>
              <input className="input" type="date" disabled={ro} value={planStartDate} onChange={(e) => setPlanStartDate(e.target.value)} />
            </div>
            <div className="field">
              <label>{t('项目目标日期')}</label>
              <input className="input" type="date" disabled={ro} value={targetDate} min={planStartDate || undefined} onChange={(e) => setTargetDate(e.target.value)} />
            </div>
          </div>
          <div className="callout">
            <div className="row">
              <b>{t('计划基线')}</b>
              <span className="spacer" />
              {baseline.data?.baseline
                ? <span className="small">{t('当前版本')} v{baseline.data.baseline.version} · {baseline.data.baseline.target_date}</span>
                : <span className="small muted">{t('尚未发布')}</span>}
            </div>
            <p className="small muted">{t('发布后会锁定目标、问题日期和阻塞依赖，用于区分基线、当前计划与预测延期。')}</p>
            {baseline.data?.blockers.map((item) => <div className="small err-text" key={item}>• {item}</div>)}
            {baseline.data?.warnings.map((item) => <div className="small warn-text" key={item}>• {item}</div>)}
            {!ro && <button
              className="btn sm"
              disabled={!baseline.data?.ready}
              onClick={() => act(
                () => api.post(`/api/projects/${p.key}/schedule-baseline`, { note: '项目设置发布' }),
                '计划基线已发布',
              )}
            >
              {baseline.data?.baseline ? t('发布新基线') : t('发布首个基线')}
            </button>}
          </div>
        </>
      )}
      <IconPicker value={icon} color={color} projectKey={p.key} disabled={ro} onChange={setIcon} />
      <div className="grid grid-2" style={{ gap: 12 }}>
        <div className="field">
          <label>{t('负责人（自动成为项目管理员）')}</label>
          <UserPicker value={lead} onChange={setLead} disabled={ro} allowClear={false} />
        </div>
        <div className="field">
          <label>{t('颜色')}</label>
          <ColorField disabled={ro} value={color} onChange={setColor} />
        </div>
      </div>
      <div className="field">
        <label>{t('可见范围')}</label>
        <Select className="select" disabled={ro} value={visibility} onChange={(e) => setVisibility(e.target.value as ProjectVisibility)}>
          <option value="public">{t('公开：所有登录用户可查看和提需求')}</option>
          <option value="members">{t('仅成员：只有项目成员和系统管理员可查看')}</option>
        </Select>
        <div className="tiny faint" style={{ marginTop: 4 }}>
          {t('仅成员模式下，非成员仍可查看自己报告、负责、测试或担任软件负责人的问题。')}
        </div>
      </div>
      <div className="field">
        <label>{t('项目模板')}</label>
        <div>
          {profileName(project.profile)}
          <span className="small muted"> · {project.profile.sections.map((s) => sectionName(s.key, project.profile)).join(' / ')}</span>
        </div>
        <div className="tiny faint" style={{ marginTop: 4 }}>
          {t('模板决定需求表单的分区、字段和各分区的填写人。')}
        </div>
        {!ro && <button className="btn sm" style={{ marginTop: 8 }} onClick={() => setMigrating(true)}>{t('更换模板…')}</button>}
      </div>
      {!ro && (
        <div className="row">
          <button className="btn primary" onClick={() => act(() => api.put(`/api/projects/${p.key}`, {
            name,
            description,
            color,
            icon,
            lead_id: lead?.id,
            plan_start_date: delivery ? (planStartDate || null) : null,
            target_date: delivery ? (targetDate || null) : null,
            settings: { visibility, work_mode: workMode },
          }))}>
            {t('保存')}
          </button>
          <span className="spacer" />
          <ConfirmButton
            className={p.archived ? 'btn' : 'btn danger'}
            message={p.archived ? t('恢复该项目？') : t('归档后项目将从列表中隐藏，可在「项目 → 已归档」中恢复，确定？')}
            onConfirm={() => act(() => api.put(`/api/projects/${p.key}`, { archived: !p.archived }))}
          >
            {p.archived ? t('取消归档') : t('归档项目')}
          </ConfirmButton>
          {p.archived && user.is_admin && (
            <button className="btn danger" onClick={() => setDeleting(true)}>{t('永久删除')}</button>
          )}
        </div>
      )}
      {p.archived && (
        <div className="tiny faint">
          {t('该项目已归档。')}
          {user.is_admin ? t('永久删除会连同事项、评论、附件和看板一起删除，且无法恢复。') : t('只有系统管理员可以永久删除。')}
        </div>
      )}
      {deleting && <DeleteProjectModal project={p} onClose={() => setDeleting(false)} onDeleted={() => navigate('/projects?view=archived')} />}
      {migrating && <TemplateMigrationWizard project={project} onClose={() => setMigrating(false)} />}
    </div>
  )
}

interface MigrationPreview {
  source: { version_id: number | null; key: string; label: string }
  target: { version_id: number; key: string; label: string }
  sections: { added: string[]; removed: string[] }
  fields: { key: string; label: string; kind: string; usage: number; suggested: string | null; compatible: boolean }[]
  added_fields: string[]
  statuses: { id: number; name: string; category: string; issues: number; suggested: string }[]
  target_statuses: { name: string; category: string; color: string }[]
  invalid_roles: Record<string, number>
  target_roles: string[]
  pending_approvals: number
  saved_filters: { id: number; name: string; jql: string }[]
}

function TemplateMigrationWizard({ project, onClose }: { project: ProjectDetail; onClose: () => void }) {
  const { toast } = useApp()
  const client = useQueryClient()
  const catalog = useQuery({
    queryKey: ['project-profile-catalog'],
    queryFn: () => api.get<{ profiles: ProjectProfile[] }>('/api/projects/profile-catalog'),
  })
  const [target, setTarget] = useState<number | null>(null)
  const [preview, setPreview] = useState<MigrationPreview | null>(null)
  const [statusMap, setStatusMap] = useState<Record<number, string>>({})
  const [fieldMap, setFieldMap] = useState<Record<string, string>>({})
  const [roleMap, setRoleMap] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [migrationResult, setMigrationResult] = useState<{
    id: number
    result: Record<string, number>
  } | null>(null)
  const targetProfile = catalog.data?.profiles.find((row) => row.version_id === target)
  const load = async () => {
    if (!target) return
    setBusy(true)
    try {
      const result = await api.post<MigrationPreview>(
        `/api/projects/${project.project.key}/template-migration/preview`,
        { target_version_id: target },
      )
      setPreview(result)
      setStatusMap(Object.fromEntries(result.statuses.map((row) => [row.id, row.suggested])))
      setFieldMap(Object.fromEntries(result.fields.map((row) => [row.key, row.suggested ?? 'orphan'])))
      setRoleMap(Object.fromEntries(Object.keys(result.invalid_roles).map((role) => [role, result.target_roles.includes('member') ? 'member' : result.target_roles[0]])))
    } catch (error) { toast((error as Error).message, 'error') } finally { setBusy(false) }
  }
  const apply = async () => {
    if (!target || !preview || !window.confirm(t('模板迁移会更新字段、状态和角色，确定执行吗？'))) return
    setBusy(true)
    try {
      const result = await api.post<{ id: number; result: Record<string, number> }>(
        `/api/projects/${project.project.key}/template-migration/apply`,
        { target_version_id: target, mapping: { status_map: statusMap, field_map: fieldMap, role_map: roleMap } },
      )
      toast(t('模板迁移完成：处理 {count} 个问题', { count: result.result.issues ?? 0 }), 'success')
      await client.invalidateQueries({ queryKey: ['project', project.project.key] })
      await client.invalidateQueries({ queryKey: ['issues'] })
      setMigrationResult(result)
    } catch (error) { toast((error as Error).message, 'error') } finally { setBusy(false) }
  }
  return <Modal title={t('更换项目模板')} size="wide" onClose={onClose} footer={<>
    <button className="btn" onClick={onClose}>{t('取消')}</button>
    <span className="spacer" />
    {migrationResult ? (
      <a className="btn primary"
        href={`/api/projects/${project.project.key}/template-migrations/${migrationResult.id}/report.json`}>
        {t('下载迁移报告')}
      </a>
    ) : !preview ? <button className="btn primary" disabled={!target || busy} onClick={load}>{t('预览迁移')}</button>
      : <button className="btn primary" disabled={busy} onClick={apply}>{t('确认并迁移')}</button>}
  </>}>
    <div className="col" style={{ gap: 14 }}>
      <label className="field">{t('目标模板')}
        <Select className="select" value={target ?? ''} onChange={(e) => { setTarget(e.target.value ? Number(e.target.value) : null); setPreview(null) }}>
          <option value="">—</option>
          {(catalog.data?.profiles ?? []).filter((row) => row.version_id !== project.profile.version_id).map((row) => <option key={row.version_id} value={row.version_id!}>{profileName(row)} · v{row.version}</option>)}
        </Select>
      </label>
      {targetProfile && <p className="small muted">{targetProfile.description}</p>}
      {busy && <Spinner center />}
      {migrationResult && <div className="callout success" tabIndex={-1}>
        <b>{t('模板迁移已完成')}</b>
        <div className="small">{t('处理 {count} 个问题，迁移 {moved} 个字段值，保留 {orphaned} 个历史值。', {
          count: migrationResult.result.issues ?? 0,
          moved: migrationResult.result.values_moved ?? 0,
          orphaned: migrationResult.result.values_orphaned ?? 0,
        })}</div>
      </div>}
      {preview && !busy && !migrationResult && <>
        <div className="grid grid-3">
          <div className="card card-pad"><b>{t('分区差异')}</b><div className="small ok-text">+ {preview.sections.added.join('、') || '—'}</div><div className="small err-text">− {preview.sections.removed.join('、') || '—'}</div></div>
          <div className="card card-pad"><b>{t('待处理审批')}</b><div>{preview.pending_approvals}</div></div>
          <div className="card card-pad"><b>{t('受影响过滤器')}</b><div>{preview.saved_filters.length}</div></div>
        </div>
        <h3>{t('状态映射')}</h3>
        {preview.statuses.map((row) => <div className="migration-map-row" key={row.id}>
          <span>{row.name} <small className="muted">({row.issues})</small></span><span>→</span>
          <Select className="select sm" value={statusMap[row.id] ?? ''} onChange={(e) => setStatusMap((map) => ({ ...map, [row.id]: e.target.value }))}>{preview.target_statuses.map((s) => <option key={s.name}>{s.name}</option>)}</Select>
        </div>)}
        <h3>{t('字段映射')}</h3>
        {preview.fields.map((row) => <div className="migration-map-row" key={row.key}>
          <span>{row.label} <small className="muted">({row.kind} · {row.usage})</small>
            {!row.compatible && <small className="warn-text"> {t('需要类型转换')}</small>}
          </span><span>→</span>
          <Select className="select sm" value={fieldMap[row.key] ?? 'orphan'} onChange={(e) => setFieldMap((map) => ({ ...map, [row.key]: e.target.value }))}>
            <option value="orphan">{t('保留为历史数据')}</option><option value="clear">{t('清空')}</option>
            {(targetProfile?.definition?.fields ?? []).map((field) => <option key={field.key} value={field.key}>{field.labels.zh} ({field.kind})</option>)}
          </Select>
        </div>)}
        {!!Object.keys(preview.invalid_roles).length && <><h3>{t('角色映射')}</h3>{Object.entries(preview.invalid_roles).map(([role, count]) => <div className="migration-map-row" key={role}><span>{role} ({count})</span><span>→</span><Select className="select sm" value={roleMap[role] ?? ''} onChange={(e) => setRoleMap((map) => ({ ...map, [role]: e.target.value }))}>{preview.target_roles.map((targetRole) => <option key={targetRole}>{targetRole}</option>)}</Select></div>)}</>}
      </>}
    </div>
  </Modal>
}

const ISSUE_USER_LABELS: Record<string, string> = {
  reporter_id: '报告人',
  assignee_id: '负责人',
  software_owner_id: '软件负责人',
  tester_id: '测试人',
}

/** Role blurbs driven by the project's actual roles + form sections (not a fixed electrical list). */
function roleHelpEntries(project: ProjectDetail): { role: string; text: string }[] {
  const customLabels = Object.fromEntries(
    project.profile.custom_fields.map((field) => [field.key, customFieldLabel(field)]),
  )
  const labelOf = (field: string) => fieldLabel(field, customLabels)
  return project.roles.map((role) => {
    if (role === 'admin') {
      return {
        role,
        text: t('可编辑全部字段并管理项目设置，状态同样需按工作流逐步流转。'),
      }
    }
    if (role === 'member') {
      return {
        role,
        text: t('可查看项目并编辑通用字段；分区字段需由对应角色或相关人填写。'),
      }
    }
    if (role === 'process') {
      return {
        role,
        text: t('参与电气需求的方案确认。方案在「方案讨论」里发起确认后，填写「工艺确认」（是 / 否 / 未填）；点「是」后，该项旁显示本人头像和英文签名。可以解锁已锁定的方案，以便撤回自己的确认，但不能修改方案说明。需求进入「研发中」前，必须有一名工艺工程师确认为「是」。'),
      }
    }
    const sections = project.profile.sections.filter(
      (section) => section.role === role
        || (role === 'requester' && section.key === 'requester' && !section.role),
    )
    if (!sections.length) {
      return { role, text: t('以本项目模板与门禁配置为准。') }
    }
    const chunks = sections.map((section) => {
      const fields = section.fields.map(labelOf).filter(Boolean).join(t('、'))
      const name = sectionName(section.key, project.profile)
      return fields
        ? t('填写「{section}」区（{fields}）', { section: name, fields })
        : t('填写「{section}」区', { section: name })
    })
    let text = chunks.join(t('；'))
    const extras = new Set<string>()
    for (const section of sections) {
      for (const key of section.issue_users) {
        const label = ISSUE_USER_LABELS[key]
        if (label) extras.add(t(label))
      }
    }
    if (extras.size) text += t('，{who}也可编辑', { who: [...extras].join(t('和')) })
    if (role === 'tester') text += t('；记录测试结果后需求会自动流转')
    return { role, text: `${text}。` }
  })
}

function RoleMark({ kind }: { kind: 'add' | 'remove' }) {
  return (
    <span className={kind === 'add' ? 'v-true' : 'v-false'} aria-hidden>
      <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
        {kind === 'add' ? <path d="M6 2.2v7.6M2.2 6h7.6" /> : <path d="M2.2 6h7.6" />}
      </svg>
    </span>
  )
}

function Members() {
  const project = useProjectCtx()
  const act = useAction()
  const { toast } = useApp()
  const [user, setUser] = useState<UserBrief | null>(null)
  const [roles, setRoles] = useState<string[]>(['member'])
  const [mode, setMode] = useState<'user' | 'org'>('user')
  const [unitId, setUnitId] = useState<number | null>(null)
  const [withChildren, setWithChildren] = useState(true)
  const [orgFilter, setOrgFilter] = useState('')
  const [filter, setFilter] = useState('')
  const [roleFilter, setRoleFilter] = useState('')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const memberRows = useRef<HTMLTableSectionElement>(null)
  const paint = useRef<{ selecting: boolean; y: number } | null>(null)
  const suppressChange = useRef(false)
  const paintMembers = useCallback((fromY: number, toY: number, selecting: boolean) => {
    const rows = memberRows.current?.querySelectorAll<HTMLElement>('[data-member-id]')
    if (!rows?.length) return
    const top = Math.min(fromY, toY)
    const bottom = Math.max(fromY, toY)
    const ids: number[] = []
    rows.forEach((row) => {
      const box = row.getBoundingClientRect()
      if (box.bottom >= top && box.top <= bottom) ids.push(Number(row.dataset.memberId))
    })
    if (!ids.length) return
    setSelected((current) => {
      const next = new Set(current)
      let changed = false
      for (const id of ids) {
        if (selecting === next.has(id)) continue
        changed = true
        if (selecting) next.add(id)
        else next.delete(id)
      }
      return changed ? next : current
    })
  }, [])
  useEffect(() => {
    let releaseChange = 0
    const move = (event: { clientY: number }) => {
      const drag = paint.current
      if (!drag) return
      paintMembers(drag.y, event.clientY, drag.selecting)
      drag.y = event.clientY
    }
    const end = () => {
      paint.current = null
      document.body.style.userSelect = ''
      window.clearTimeout(releaseChange)
      releaseChange = window.setTimeout(() => { suppressChange.current = false }, 50)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('mousemove', move)
    window.addEventListener('pointerup', end)
    window.addEventListener('pointercancel', end)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('mousemove', move)
      window.removeEventListener('pointerup', end)
      window.removeEventListener('pointercancel', end)
      window.clearTimeout(releaseChange)
      document.body.style.userSelect = ''
    }
  }, [paintMembers])
  const [bulkMode, setBulkMode] = useState<'add' | 'remove'>('add')
  const [bulkRoles, setBulkRoles] = useState<string[]>([])
  const [editing, setEditing] = useState<{
    user: UserBrief
    roles: string[]
  } | null>(null)
  const tree = useQuery({
    queryKey: ['org-tree-flat'],
    queryFn: () => api.get<{ units: OrgUnit[] }>('/api/org/tree'),
    staleTime: 300_000,
    enabled: project.is_admin && mode === 'org',
  })
  const orgUnits = tree.data?.units ?? []
  const unit = orgUnits.find((u) => u.id === unitId)
  const orgQuery = orgFilter.trim().toLowerCase()
  const visibleUnits = orgUnits.filter(
    (item) => !orgQuery
      || item.label.toLowerCase().includes(orgQuery)
      || item.path.toLowerCase().includes(orgQuery),
  )
  const preview = useQuery({
    queryKey: ['org-member-preview', unitId, withChildren],
    queryFn: ({ signal }) => {
      const params = new URLSearchParams({
        org_unit_id: String(unitId),
        descendants: String(withChildren),
        limit: '500',
      })
      return api.get<{ users: UserBrief[]; total: number }>(`/api/users?${params}`, signal)
    },
    enabled: project.is_admin && mode === 'org' && unitId !== null,
    staleTime: 30_000,
  })
  const bulkCount = preview.data?.total
    ?? (unit ? (withChildren ? (unit.total_count ?? unit.member_count) : unit.member_count) : 0)
  const byUser = new Map<number, { user: UserBrief; roles: { id: number; role: string }[] }>()
  for (const m of project.members) {
    const entry = byUser.get(m.user.id) ?? { user: m.user, roles: [] }
    entry.roles.push({ id: m.id, role: m.role })
    byUser.set(m.user.id, entry)
  }
  const query = filter.trim().toLowerCase()
  const members = [...byUser.values()].filter(
    ({ user: u, roles: memberRoles }) => (
      (!query
        || u.display_name.toLowerCase().includes(query)
        || u.username.toLowerCase().includes(query)
        || (u.org_path ?? '').toLowerCase().includes(query))
      && (!roleFilter || memberRoles.some((item) => item.role === roleFilter))
    ),
  )
  const roleNames = roles.map((role) => roleLabel(role)).join(t('、'))
  const roleHelp = roleHelpEntries(project)
  const allVisibleSelected = members.length > 0 && members.every(({ user: item }) => selected.has(item.id))
  const updateRoles = async (
    userIds: number[],
    changes: { set_roles?: string[]; add_roles?: string[]; remove_roles?: string[] },
    success: string,
  ) => {
    const ok = await act(
      () => api.patch(`/api/projects/${project.project.key}/members/roles`, {
        user_ids: userIds,
        ...changes,
      }),
      success,
    )
    if (ok) {
      setSelected((current) => {
        const next = new Set(current)
        userIds.forEach((id) => next.delete(id))
        return next
      })
    }
    return ok
  }

  const addUnit = async () => {
    if (!unit || !roles.length) return
    const message = t('将「{unit}」的 {n} 人以「{role}」角色加入项目？', {
      unit: unit.path || unit.label, n: bulkCount, role: roleNames,
    })
    if (!window.confirm(message)) return
    let result = { added: 0, skipped: 0 }
    const ok = await act(async () => {
      for (const role of roles) {
        const part = await api.post<{ added: number; skipped: number }>(
          `/api/projects/${project.project.key}/members/bulk`,
          { org_unit_id: unit.id, role, include_children: withChildren },
        )
        result.added += part.added
        result.skipped += part.skipped
      }
    }, '')
    if (ok) {
      toast(t('已添加 {added} 人，跳过 {skipped} 名已有该角色的成员', result), 'success')
      setUnitId(null)
    }
  }
  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="card card-pad">
        <h3>{t('角色说明')}</h3>
        <div className="small muted" style={{ marginTop: 6, lineHeight: 1.8 }}>
          {roleHelp.map((entry, index) => (
            <Fragment key={entry.role}>
              {index > 0 && <br />}
              <b>{roleLabel(entry.role)}</b>
              {t('：')}
              {entry.text}
            </Fragment>
          ))}
        </div>
      </div>
      {project.is_admin && (
        <div className="card card-pad col" style={{ gap: 12, alignItems: 'flex-start' }}>
          <Segmented
            value={mode}
            onChange={setMode}
            items={[
              { value: 'user', label: t('按人员添加') },
              { value: 'org', label: t('按组织批量添加') },
            ]}
          />
          <div className="row wrap">
            {mode === 'user' ? (
              <div style={{ width: 260 }}>
                <UserPicker value={user} onChange={setUser} placeholder={t('搜索要添加的成员')} />
              </div>
            ) : (
              <>
                <input
                  className="input"
                  style={{ width: 220 }}
                  value={orgFilter}
                  placeholder={t('搜索部门或完整路径')}
                  onChange={(event) => setOrgFilter(event.target.value)}
                />
                <Select className="select" style={{ width: 420 }} value={unitId ?? ''} onChange={(e) => setUnitId(e.target.value ? Number(e.target.value) : null)}>
                  <option value="">{t('选择组织架构中的部门…')}</option>
                  {visibleUnits.map((u) => (
                    <option key={u.id} value={u.id}>
                      {t('{path} · 直属 {direct} 人 / 含下级 {total} 人', {
                        path: u.path,
                        direct: u.member_count,
                        total: u.total_count ?? u.member_count,
                      })}
                    </option>
                  ))}
                </Select>
                <label className="row small" style={{ gap: 6 }}>
                  <input type="checkbox" checked={withChildren} onChange={(e) => setWithChildren(e.target.checked)} /> {t('包含下级组织')}
                </label>
              </>
            )}
            <Select
              multiple
              className="select"
              style={{ width: 220 }}
              value={roles}
              placeholder={t('选择角色…')}
              onChange={(e) => setRoles(e.target.value)}
            >
              {project.roles.map((r) => (
                <option key={r} value={r}>
                  {roleLabel(r)}
                </option>
              ))}
            </Select>
            {mode === 'user' ? (
              <button
                className="btn primary"
                disabled={!user || !roles.length}
                onClick={async () => {
                  if (!user || !roles.length) return
                  const ok = await act(async () => {
                    for (const role of roles) {
                      await api.post(`/api/projects/${project.project.key}/members`, { user_id: user.id, role })
                    }
                  }, '已添加')
                  if (ok) setUser(null)
                }}
              >
                {t('添加')}
              </button>
            ) : (
              <button className="btn primary" disabled={!unit || bulkCount === 0 || !roles.length} onClick={addUnit}>
                {unit ? t('批量添加 {n} 人', { n: bulkCount }) : t('批量添加')}
              </button>
            )}
          </div>
          {mode === 'org' && (
            <>
              {unit && (
                <div
                  className="col"
                  style={{
                    width: '100%',
                    gap: 8,
                    padding: 10,
                    border: '1px solid var(--border)',
                    borderRadius: 10,
                  }}
                >
                  <div className="row wrap" style={{ justifyContent: 'space-between' }}>
                    <div>
                      <b>{unit.label}</b>
                      <div className="tiny faint">{unit.path}</div>
                    </div>
                    <span className="small muted">
                      {preview.isFetching ? t('正在读取成员…') : t('本次将处理 {n} 名在职人员', { n: bulkCount })}
                    </span>
                  </div>
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
                      gap: 6,
                      maxHeight: 220,
                      overflow: 'auto',
                    }}
                  >
                    {(preview.data?.users ?? []).map((person) => (
                      <div key={person.id} className="row" style={{ minWidth: 0, gap: 7 }}>
                        <UserChip user={person} />
                        <span className="tiny faint ellipsis">@{person.username}</span>
                      </div>
                    ))}
                    {!preview.isFetching && preview.data?.users.length === 0 && (
                      <span className="tiny faint">{t('该范围内没有在职人员')}</span>
                    )}
                  </div>
                  {bulkCount > (preview.data?.users.length ?? 0) && (
                    <div className="tiny faint">
                      {t('仅预览前 {shown} 人，共 {total} 人', {
                        shown: preview.data?.users.length ?? 0,
                        total: bulkCount,
                      })}
                    </div>
                  )}
                </div>
              )}
              <div className="tiny faint">{t('将该部门当前所有在职人员以所选角色加入项目，已有该角色的成员会自动跳过；之后新入职的人员需要再次添加。')}</div>
            </>
          )}
        </div>
      )}
      <div className="card">
        <div className="card-header row wrap" style={{ gap: 8 }}>
          <input className="input sm" style={{ width: 260 }} placeholder={t('搜索姓名、账号或组织路径')} value={filter} onChange={(e) => setFilter(e.target.value)} />
          <Select className="select sm" style={{ width: 170 }} value={roleFilter} onChange={(e) => setRoleFilter(String(e.target.value))}>
            <option value="">{t('全部角色')}</option>
            {project.roles.map((role) => <option key={role} value={role}>{roleLabel(role)}</option>)}
          </Select>
          <span className="small muted">{t('显示 {shown} / {total} 名成员', { shown: members.length, total: byUser.size })}</span>
        </div>
        {project.is_admin && (
          <div className="card-header row wrap" style={{ gap: 8, background: 'var(--surface-2)' }}>
            <b className="small" style={{ width: 120, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums', color: selected.size ? undefined : 'var(--text-3)' }}>
              {t('已选择 {n} 人', { n: selected.size })}
            </b>
            <Segmented
              className="verdict"
              disabled={!selected.size}
              value={bulkMode}
              onChange={setBulkMode}
              items={[
                { value: 'add', label: <><RoleMark kind="add" />{t('新增角色')}</> },
                { value: 'remove', label: <><RoleMark kind="remove" />{t('移除角色')}</> },
              ]}
            />
            <Select
              multiple
              disabled={!selected.size}
              className="select sm"
              style={{ width: 220 }}
              value={bulkRoles}
              placeholder={t('选择角色…')}
              onChange={(event) => setBulkRoles(event.target.value)}
            >
              {project.roles.map((role) => <option key={role} value={role}>{roleLabel(role)}</option>)}
            </Select>
            <button
              className="btn primary sm"
              disabled={!selected.size || !bulkRoles.length}
              onClick={async () => {
                const ids = [...selected]
                const roleNames = bulkRoles.map((role) => roleLabel(role)).join(t('、'))
                if (
                  bulkMode === 'remove'
                  && !window.confirm(t('确认从选中的 {n} 名成员移除「{role}」？', {
                    n: selected.size,
                    role: roleNames,
                  }))
                ) return
                const changes = bulkMode === 'add'
                  ? { add_roles: bulkRoles }
                  : { remove_roles: bulkRoles }
                const ok = await updateRoles(ids, changes, '已批量更新权限')
                if (ok) setBulkRoles([])
              }}
            >
              {t('应用到所选成员')}
            </button>
            <button
              className="btn danger sm"
              disabled={!selected.size}
              onClick={async () => {
                if (!window.confirm(t('确认将选中的 {n} 名成员移出项目？', { n: selected.size }))) return
                await updateRoles([...selected], { set_roles: [] }, '已移出项目')
              }}
            >
              {t('移出项目')}
            </button>
            <button className="btn ghost sm" disabled={!selected.size} onClick={() => setSelected(new Set())}>{t('取消选择')}</button>
          </div>
        )}
        <table className="table">
          <thead>
            <tr>
              {project.is_admin && (
                <th style={{ width: 42 }}>
                  <input
                    type="checkbox"
                    aria-label={t('选择当前筛选结果')}
                    checked={allVisibleSelected}
                    onChange={() => {
                      setSelected((current) => {
                        const next = new Set(current)
                        for (const { user: item } of members) {
                          if (allVisibleSelected) next.delete(item.id)
                          else next.add(item.id)
                        }
                        return next
                      })
                    }}
                  />
                </th>
              )}
              <th>{t('成员')}</th>
              <th>{t('所属组织')}</th>
              <th>{t('角色')}</th>
              {project.is_admin && <th style={{ width: 90 }}>{t('操作')}</th>}
            </tr>
          </thead>
          <tbody ref={memberRows}>
            {members.map(({ user: u, roles }) => (
              <tr key={u.id}>
                {project.is_admin && (
                  <td data-member-id={u.id} style={{ touchAction: 'none' }}>
                    <input
                      type="checkbox"
                      aria-label={t('选择 {name}', { name: u.display_name })}
                      checked={selected.has(u.id)}
                      onPointerDown={(event) => {
                        if (event.button !== 0) return
                        const selecting = !selected.has(u.id)
                        suppressChange.current = true
                        paint.current = { selecting, y: event.clientY }
                        document.body.style.userSelect = 'none'
                        paintMembers(event.clientY, event.clientY, selecting)
                      }}
                      onClick={(event) => {
                        if (event.detail > 0) event.preventDefault()
                      }}
                      onChange={() => {
                        if (suppressChange.current) return
                        setSelected((current) => {
                          const next = new Set(current)
                          if (next.has(u.id)) next.delete(u.id)
                          else next.add(u.id)
                          return next
                        })
                      }}
                    />
                  </td>
                )}
                <td>
                  <UserChip user={u} />
                  <div className="tiny faint">@{u.username}</div>
                </td>
                <td className="small muted">{u.org_path || '—'}</td>
                <td>
                  <div className="row wrap" style={{ gap: 6 }}>
                    {roles.map((r) => (
                      <span key={r.id} className="tag">
                        {roleLabel(r.role)}
                      </span>
                    ))}
                  </div>
                </td>
                {project.is_admin && (
                  <td>
                    <button
                      className="btn ghost sm"
                      onClick={() => setEditing({ user: u, roles: roles.map((item) => item.role) })}
                    >
                      {t('编辑权限')}
                    </button>
                  </td>
                )}
              </tr>
            ))}
            {members.length === 0 && (
              <tr>
                <td colSpan={project.is_admin ? 5 : 3} className="faint">
                  {byUser.size === 0 ? t('暂无成员') : t('没有匹配的成员')}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {editing && (
        <Modal
          title={t('编辑 {name} 的权限', { name: editing.user.display_name })}
          size="narrow"
          onClose={() => setEditing(null)}
          footer={(
            <>
              <button className="btn" onClick={() => setEditing(null)}>{t('取消')}</button>
              <button
                className="btn primary"
                disabled={!editing.roles.length}
                onClick={async () => {
                  const ok = await updateRoles(
                    [editing.user.id],
                    { set_roles: editing.roles },
                    '权限已更新',
                  )
                  if (ok) setEditing(null)
                }}
              >
                {t('保存权限')}
              </button>
            </>
          )}
        >
          <div className="col" style={{ gap: 12 }}>
            <UserChip user={editing.user} />
            {editing.user.org_path && <div className="small muted">{editing.user.org_path}</div>}
            <Select
              multiple
              className="select"
              value={editing.roles}
              placeholder={t('选择角色…')}
              onChange={(event) => setEditing({ ...editing, roles: event.target.value })}
            >
              {project.roles.map((role) => <option key={role} value={role}>{roleLabel(role)}</option>)}
            </Select>
            <div className="tiny faint">{t('一个成员可以同时拥有多个角色。')}</div>
          </div>
        </Modal>
      )}
    </div>
  )
}

function GateEditor({ project, readOnly }: { project: ProjectDetail; readOnly: boolean }) {
  const act = useAction()
  const stored = project.project.settings.transition_gates ?? {}
  const [gates, setGates] = useState<Record<string, { required_fields: string[]; approvals: { role: string; count: number }[] }>>(stored)
  const fields = [
    ...project.profile.general_fields,
    ...project.profile.sections.flatMap((section) => section.fields.map((field) =>
      project.profile.custom_fields.some((item) => item.key === field) ? `custom_fields.${field}` : field)),
  ]
  const update = (status: string, value: { required_fields: string[]; approvals: { role: string; count: number }[] }) =>
    setGates({ ...gates, [status]: value })
  return (
    <div className="card">
      <div className="card-header">
        <h3>{t('状态门禁与审批')}</h3>
        <span className="small muted">
          {t('配置进入状态前的必填字段，以及需要哪些角色会签。仅对表单里能填写该字段的问题类型生效；其它类型流转时会跳过。')}
        </span>
      </div>
      <div className="card-pad col" style={{ gap: 10 }}>
        {project.statuses.slice(1).map((status) => {
          const gate = gates[status.name] ?? { required_fields: [], approvals: [] }
          const approval = gate.approvals[0] ?? { role: '', count: 1 }
          return (
            <div className="grid gate-row" key={status.id}>
              <StatusPill status={status} />
              <input className="input sm" disabled={readOnly} list={`fields-${status.id}`}
                placeholder={t('必填字段，逗号分隔')} value={gate.required_fields.join(', ')}
                onChange={(e) => update(status.name, { ...gate, required_fields: e.target.value.split(/[,，]/).map((v) => v.trim()).filter(Boolean) })} />
              <datalist id={`fields-${status.id}`}>{fields.map((field) => <option key={field}>{field}</option>)}</datalist>
              <Select className="select sm" disabled={readOnly} value={approval.role}
                onChange={(e) => update(status.name, { ...gate, approvals: e.target.value ? [{ role: e.target.value, count: approval.count }] : [] })}>
                <option value="">{t('无需审批')}</option>
                {project.roles.map((role) => <option key={role} value={role}>{roleLabel(role)}</option>)}
              </Select>
              <input className="input sm w-num" type="number" min={1} max={20} disabled={readOnly || !approval.role}
                value={approval.count} title={t('会签人数')}
                onChange={(e) => update(status.name, { ...gate, approvals: [{ ...approval, count: Number(e.target.value) || 1 }] })} />
            </div>
          )
        })}
        {!readOnly && <button className="btn primary" style={{ alignSelf: 'flex-start' }}
          onClick={() => act(() => api.put(`/api/projects/${project.project.key}`, {
            settings: { transition_gates: gates },
          }), '已保存')}>
          {t('保存门禁与审批')}
        </button>}
      </div>
    </div>
  )
}

const splitDates = (text: string) => text.split(/[\s,，、]+/).map((v) => v.trim()).filter(Boolean)

function SlaEditor({ project, readOnly }: { project: ProjectDetail; readOnly: boolean }) {
  const act = useAction()
  const settings = project.project.settings
  const [hours, setHours] = useState(settings.sla_hours ?? {
    highest: 24, high: 48, medium: 120, low: 240, lowest: 480,
  })
  const [warning, setWarning] = useState(settings.sla_warning_hours ?? 4)
  const [calendar, setCalendar] = useState<'natural' | 'business'>(settings.sla_calendar ?? 'natural')
  const [business, setBusiness] = useState(settings.sla_business_hours ?? { start: '09:00', end: '18:00', workdays: [1, 2, 3, 4, 5] })
  const [holidays, setHolidays] = useState((settings.sla_holidays ?? []).join('\n'))
  const [extraWorkdays, setExtraWorkdays] = useState((settings.sla_extra_workdays ?? []).join('\n'))
  const [paused, setPaused] = useState<string[]>(settings.sla_paused_statuses ?? [])
  const toggleDay = (day: number) => setBusiness({
    ...business,
    workdays: business.workdays.includes(day) ? business.workdays.filter((d) => d !== day) : [...business.workdays, day].sort(),
  })
  const togglePaused = (name: string) => setPaused(paused.includes(name) ? paused.filter((n) => n !== name) : [...paused, name])
  return (
    <div className="card">
      <div className="card-header">
        <h3>SLA<InfoTip mark="?" text={slaHelp(settings)} /></h3>
        <span className="small muted">{t('按优先级设定完成时限，快到期和超时都会自动提醒。')}</span>
      </div>
      <div className="card-pad col sla-editor">
        <div className="sla-row">
          <b>{t('时限（小时）')}</b>
          <div className="row wrap">
            {PRIORITIES.map((priority) => (
              <label className="row small" key={priority}>
                {priorityName(priority)}
                <input className="input sm w-hours" type="number" min={1}
                  disabled={readOnly} value={hours[priority]}
                  onChange={(e) => setHours({ ...hours, [priority]: Number(e.target.value) || 1 })} />
              </label>
            ))}
          </div>
        </div>
        <div className="sla-row">
          <b>{t('提前提醒')}</b>
          <label className="row small">
            <input className="input sm w-hours" type="number" min={0}
              disabled={readOnly} value={warning}
              onChange={(e) => setWarning(Number(e.target.value) || 0)} />
            {t('小时')}
          </label>
        </div>
        <div className="sla-row">
          <b>{t('计时方式')}</b>
          <div className="col" style={{ gap: 8 }}>
            {readOnly ? <span>{calendar === 'business' ? t('仅工作时间') : t('自然时间')}</span> : (
              <Segmented value={calendar} onChange={setCalendar} items={[
                { value: 'natural', label: t('自然时间') },
                { value: 'business', label: t('仅工作时间') },
              ]} />
            )}
            <span className="tiny faint">
              {calendar === 'business'
                ? t('只在工作日的工作时间内计时，夜间、周末和节假日不计。')
                : t('24 小时连续计时，夜间、周末和节假日也计算在内。')}
            </span>
          </div>
        </div>
        {calendar === 'business' && (
          <>
            <div className="sla-row">
              <b>{t('工作时间')}</b>
              <div className="row wrap">
                <TimeField className="select sm sla-time" disabled={readOnly} value={business.start}
                  onChange={(start) => setBusiness({ ...business, start })} />
                <span className="muted">–</span>
                <TimeField className="select sm sla-time" disabled={readOnly} value={business.end}
                  onChange={(end) => setBusiness({ ...business, end })} />
                <span className="sla-days">
                  {[1, 2, 3, 4, 5, 6, 7].map((day) => (
                    <label key={day} className={`sla-day ${business.workdays.includes(day) ? 'on' : ''}`}>
                      <input type="checkbox" disabled={readOnly} checked={business.workdays.includes(day)} onChange={() => toggleDay(day)} />
                      {weekdayName(day)}
                    </label>
                  ))}
                </span>
              </div>
            </div>
            <div className="sla-row">
              <b>{t('节假日')}</b>
              <div className="grid sla-dates">
                <label className="col small">
                  <span className="muted">{t('放假（不计时）')}</span>
                  <textarea className="input" rows={3} disabled={readOnly} placeholder="2026-10-01" value={holidays}
                    onChange={(e) => setHolidays(e.target.value)} />
                </label>
                <label className="col small">
                  <span className="muted">{t('调休上班（照常计时）')}</span>
                  <textarea className="input" rows={3} disabled={readOnly} placeholder="2026-10-10" value={extraWorkdays}
                    onChange={(e) => setExtraWorkdays(e.target.value)} />
                </label>
              </div>
            </div>
          </>
        )}
        <div className="sla-row">
          <b>{t('暂停计时')}</b>
          <div className="col" style={{ gap: 6 }}>
            <div className="row wrap">
              {project.statuses.filter((s) => s.category !== 'done').map((status) => (
                <label key={status.id} className={`sla-pause ${paused.includes(status.name) ? 'on' : ''}`}>
                  <input type="checkbox" disabled={readOnly} checked={paused.includes(status.name)} onChange={() => togglePaused(status.name)} />
                  <StatusPill status={status} />
                </label>
              ))}
            </div>
            <span className="tiny faint">{t('处于选中状态时（如等待客户、等待审批）停止计时，离开后截止时间相应顺延。')}</span>
          </div>
        </div>
        {!readOnly && <button className="btn primary" style={{ alignSelf: 'flex-start' }}
          onClick={() => act(() => api.put(`/api/projects/${project.project.key}`, {
            settings: {
              sla_hours: hours,
              sla_warning_hours: warning,
              sla_calendar: calendar,
              sla_business_hours: business,
              sla_holidays: splitDates(holidays),
              sla_extra_workdays: splitDates(extraWorkdays),
              sla_paused_statuses: paused,
            },
          }), '已保存')}>
          {t('保存 SLA')}
        </button>}
      </div>
    </div>
  )
}

function Workflow() {
  const project = useProjectCtx()
  const client = useQueryClient()
  const key = project.project.key
  const act = useAction()
  const settings = project.project.settings
  const statuses = project.statuses
  const [flowType, setFlowType] = useState<IssueType>('requirement')
  const [flowView, setFlowView] = useState<'diagram' | 'matrix'>('diagram')
  const [drafts, setDrafts] = useState(project.workflows)
  const [statusProps, setStatusProps] = useState(settings.status_properties ?? {})
  const [testStatus, setTestStatus] = useState(settings.test_status ?? '测试中')
  const [passStatus, setPassStatus] = useState(settings.pass_status ?? '测试通过')
  const [failStatus, setFailStatus] = useState(settings.fail_return_status ?? '测试未通过')
  const [adding, setAdding] = useState({ name: '', category: 'todo', color: '#8e8e93' })
  const savedWorkflows = JSON.stringify(project.workflows ?? {})
  useEffect(() => setDrafts(project.workflows), [savedWorkflows]) // eslint-disable-line react-hooks/exhaustive-deps -- reset only when saved rules change
  useEffect(() => setStatusProps(settings.status_properties ?? {}), [settings.status_properties])
  const ro = !project.is_admin
  const matrix = drafts?.[flowType] ?? {}
  const customized = (tp: IssueType) => !!project.customized_workflows?.includes(tp)
  const changed = ISSUE_TYPES.filter((tp) => JSON.stringify(drafts?.[tp] ?? {}) !== JSON.stringify(project.workflows?.[tp] ?? {}))
  const { main, branches } = flowLayout(flowStatuses(matrix, statuses), matrix)
  const matrixFindings = lintFlow(statuses.map((s) => ({ name: s.name, category: s.category })), matrix)
  const workflowErrors = ISSUE_TYPES.flatMap((issueType) =>
    lintFlow(
      statuses.map((status) => ({ name: status.name, category: status.category })),
      drafts?.[issueType] ?? {},
    )
      .filter((finding) => finding.level === 'error')
      .map((finding) => ({ issueType, finding })),
  )

  const setMatrixRaw = useCallback(
    (next: Record<string, string[]>) => setDrafts((d) => ({ ...d, [flowType]: next })),
    [flowType],
  )
  const history = useUndoableDraft(matrix, setMatrixRaw, flowType)
  const setMatrix = (next: Record<string, string[]>) => history.push(next)
  const toggle = (from: string, to: string) => {
    const current = matrix[from] ?? []
    const next = { ...matrix, [from]: current.includes(to) ? current.filter((x) => x !== to) : [...current, to] }
    if (!next[from].length) delete next[from]
    setMatrix(next)
  }

  const saveTransitions = (transitions: Record<string, Record<string, string[]>>) =>
    act(() =>
      api.put(`/api/projects/${key}`, {
        settings: {
          transitions,
          status_properties: statusProps,
          test_status: testStatus,
          pass_status: passStatus,
          fail_return_status: failStatus,
        },
      }),
    )

  const resetType = () => {
    const rest = { ...(settings.transitions ?? {}) }
    delete rest[flowType]
    for (const tp of changed) if (tp !== flowType) rest[tp] = drafts[tp]
    return saveTransitions(rest)
  }

  const saveStatus = (s: Status, changes: Partial<Status>) =>
    act(async () => {
      await api.put(`/api/projects/${key}/statuses/${s.id}`, { name: s.name, category: s.category, color: s.color, sort_order: s.sort_order, ...changes })
      if (changes.name && changes.name !== s.name && statusProps[s.name]) {
        const next = { ...statusProps, [changes.name]: statusProps[s.name] }
        delete next[s.name]
        setStatusProps(next)
        await api.put(`/api/projects/${key}`, { settings: { status_properties: next } })
      }
    })

  const onEditorKey = (e: KeyboardEvent) => {
    if (ro) return
    const mod = e.metaKey || e.ctrlKey
    if (mod && e.key.toLowerCase() === 'z' && !e.shiftKey) {
      e.preventDefault()
      history.undo()
    } else if (mod && (e.key.toLowerCase() === 'y' || (e.key.toLowerCase() === 'z' && e.shiftKey))) {
      e.preventDefault()
      history.redo()
    }
  }

  return (
    <div className="col" style={{ gap: 16 }} onKeyDown={onEditorKey}>
      <div className="card">
        <div className="card-header">
          <h3>{t('状态')}</h3>
          <span className="small muted">{t('类别决定统计口径：待办 / 进行中 / 已完成')}</span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>{t('顺序')}</th>
              <th>{t('名称')}</th>
              <th>{t('类别')}</th>
              <th>{t('颜色')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {statuses.map((s, i) => (
              <tr key={s.id}>
                <td>
                  {!ro && (
                    <span className="row" style={{ gap: 2 }}>
                      <button className="btn ghost sm icon" disabled={i === 0} onClick={() => saveStatus(s, { sort_order: statuses[i - 1].sort_order - 1 })}>
                        ↑
                      </button>
                      <button className="btn ghost sm icon" disabled={i === statuses.length - 1} onClick={() => saveStatus(s, { sort_order: statuses[i + 1].sort_order + 1 })}>
                        ↓
                      </button>
                    </span>
                  )}
                </td>
                <td>
                  <input
                    className="input sm"
                    disabled={ro}
                    defaultValue={s.name}
                    onBlur={(e) => e.target.value.trim() && e.target.value !== s.name && saveStatus(s, { name: e.target.value.trim() })}
                  />
                </td>
                <td>
                  <Select className="select sm" disabled={ro} value={s.category} onChange={(e) => saveStatus(s, { category: e.target.value as Status['category'] })}>
                    <option value="todo">{t('待办')}</option>
                    <option value="doing">{t('进行中')}</option>
                    <option value="done">{t('已完成')}</option>
                  </Select>
                </td>
                <td>
                  <ColorField
                    size="sm"
                    disabled={ro}
                    value={s.color}
                    onChange={(color) => { if (color !== s.color) saveStatus(s, { color }) }}
                  />
                </td>
                <td>
                  {!ro && (
                    <ConfirmButton
                      className="btn ghost sm danger"
                      message={t('删除状态「{name}」？（仍有问题处于该状态时无法删除）', { name: t(s.name) })}
                      onConfirm={() => act(() => api.del(`/api/projects/${key}/statuses/${s.id}`), '已删除')}
                    >
                      {t('删除')}
                    </ConfirmButton>
                  )}
                </td>
              </tr>
            ))}
            {!ro && (
              <tr>
                <td />
                <td>
                  <input className="input sm" placeholder={t('新状态名称')} value={adding.name} onChange={(e) => setAdding({ ...adding, name: e.target.value })} />
                </td>
                <td>
                  <Select className="select sm" value={adding.category} onChange={(e) => setAdding({ ...adding, category: e.target.value })}>
                    <option value="todo">{t('待办')}</option>
                    <option value="doing">{t('进行中')}</option>
                    <option value="done">{t('已完成')}</option>
                  </Select>
                </td>
                <td>
                  <ColorField size="sm" value={adding.color} onChange={(color) => setAdding({ ...adding, color })} />
                </td>
                <td>
                  <button
                    className="btn primary sm"
                    disabled={!adding.name.trim()}
                    onClick={async () => {
                      if (await act(() => api.post(`/api/projects/${key}/statuses`, adding), '已添加')) setAdding({ name: '', category: 'todo', color: '#8e8e93' })
                    }}
                  >
                    {t('添加')}
                  </button>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="card-header">
          <h3>{t('类型工作流')}</h3>
          <span className="small muted">{t('每种问题类型都有独立的流程。在示意图中拖线添加流转，或切换到矩阵勾选；项目管理员同样受此约束。')}</span>
        </div>
        <div className="card-pad col" style={{ gap: 10, paddingBottom: 0 }}>
          <div className="row wrap" style={{ gap: 10 }}>
            <Tabs
              value={flowType}
              onChange={setFlowType}
              items={ISSUE_TYPES.map((tp) => ({
                value: tp,
                label: (
                  <span className="row" style={{ gap: 6 }}>
                    <TypeIcon type={tp} />
                    {typeName(tp)}
                    {changed.includes(tp) ? <span className="tiny" style={{ color: 'var(--accent)' }}>●</span> : customized(tp) && <span className="tiny faint">{t('已自定义')}</span>}
                  </span>
                ),
              }))}
            />
            <span className="spacer" />
            <div className="wf-view-toggle" role="group" aria-label={t('工作流视图')}>
              <button type="button" className={flowView === 'diagram' ? 'active' : ''} onClick={() => setFlowView('diagram')}>{t('示意图')}</button>
              <button type="button" className={flowView === 'matrix' ? 'active' : ''} onClick={() => setFlowView('matrix')}>{t('矩阵')}</button>
            </div>
            <span className="small faint">{customized(flowType) ? t('已自定义') : t('默认流程')}</span>
          </div>
        </div>
        {!ro && (
          <div className="card-pad" style={{ paddingTop: 0, paddingBottom: 0 }}>
            <WorkflowAgentBar
              key={flowType}
              projectKey={key}
              issueType={flowType}
              statuses={statuses}
              transitions={matrix}
              onApply={async (next, created) => {
                for (const status of created) {
                  await api.post(`/api/projects/${key}/statuses`, status)
                }
                if (created.length) {
                  await client.refetchQueries({ queryKey: ['project', key] })
                }
                setMatrix(next)
              }}
            />
          </div>
        )}
        {flowView === 'diagram' ? (
          <div className="card-pad" style={{ paddingTop: 8 }}>
            <WorkflowGraphEditor
              key={flowType}
              statuses={statuses}
              transitions={matrix}
              onChange={setMatrix}
              readOnly={ro}
              statusProperties={statusProps}
              onStatusPropertiesChange={setStatusProps}
              onPatchStatus={(s, changes) => saveStatus(s, changes)}
              toolbar={(
                <>
                  <button type="button" className="btn sm" disabled={ro || !history.canUndo} onClick={history.undo}>{t('撤销')}</button>
                  <button type="button" className="btn sm" disabled={ro || !history.canRedo} onClick={history.redo}>{t('重做')}</button>
                </>
              )}
            />
          </div>
        ) : (
          <div className="card-pad col" style={{ gap: 8, paddingTop: 8 }}>
            {matrixFindings.filter((f) => f.level !== 'info').length > 0 && (
              <button type="button" className="wf-view-warns" onClick={() => setFlowView('diagram')}>
                <span className="wf-warn-icon">⚠</span>
                {t('查看警告 ({n})', { n: matrixFindings.filter((f) => f.level !== 'info').length })}
                <span className="tiny faint" style={{ marginLeft: 8 }}>{t('切换到示意图查看')}</span>
              </button>
            )}
            <div style={{ overflowX: 'auto', overflowY: 'hidden' }}>
              <table className="table transition-matrix">
                <thead>
                  <tr>
                    <th>{t('从 \\ 到')}</th>
                    {statuses.map((s) => (
                      <th key={s.id}>
                        <StatusPill status={s} />
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {statuses.map((from) => (
                    <tr key={from.id} className={main.includes(from) || branches.includes(from) ? '' : 'unused'}>
                      <td>
                        <StatusPill status={from} />
                      </td>
                      {statuses.map((to) => (
                        <td key={to.id}>
                          {from.id === to.id ? (
                            <span className="faint">—</span>
                          ) : (
                            <input type="checkbox" disabled={ro} checked={(matrix[from.name] ?? []).includes(to.name)} onChange={() => toggle(from.name, to.name)} />
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        <div className="card-pad col" style={{ gap: 12, borderTop: '1px solid var(--border)' }}>
          <b>{t('测试结果自动流转')}</b>
          <div className="row wrap small">
            {withSlots(t('处于{test}的问题（需求、缺陷、故事等含测试环节的类型），测试人记录「通过」后流转到{pass}，记录「不通过」后流转到{fail}；从{pass}仅负责人可确认完成或驳回，从{fail}由负责人重新打开后返工；再次进入测试时会清空上一轮结果'), {
              test: <StatusSelect statuses={statuses} value={testStatus} onChange={setTestStatus} disabled={ro} />,
              pass: <StatusSelect statuses={statuses} value={passStatus} onChange={setPassStatus} disabled={ro} />,
              fail: <StatusSelect statuses={statuses} value={failStatus} onChange={setFailStatus} disabled={ro} />,
            })}
          </div>
          {!ro && (
            <div className="row" style={{ gap: 8 }}>
              <button
                className="btn primary"
                disabled={workflowErrors.length > 0}
                title={workflowErrors.length ? t('请先修复工作流错误') : undefined}
                onClick={() => saveTransitions({
                  ...(settings.transitions ?? {}),
                  ...Object.fromEntries(changed.map((tp) => [tp, drafts[tp]])),
                  // Always persist current type draft even if JSON-equal, so status_properties save.
                  [flowType]: drafts[flowType],
                })}
              >
                {t('保存工作流')}
              </button>
              {workflowErrors.length > 0 && (
                <span className="small err-text">
                  {t('有 {n} 个工作流错误，修复后才能保存', { n: workflowErrors.length })}
                </span>
              )}
              {customized(flowType) && (
                <ConfirmButton className="btn" message={t('将「{type}」恢复为默认流程？', { type: typeName(flowType) })} onConfirm={resetType}>
                  {t('恢复默认流程')}
                </ConfirmButton>
              )}
            </div>
          )}
        </div>
      </div>
      <GateEditor project={project} readOnly={ro} />
      <SlaEditor project={project} readOnly={ro} />
    </div>
  )
}

function withSlots(text: string, slots: Record<string, ReactNode>) {
  return text.split(/\{(\w+)\}/).map((part, i) => <Fragment key={i}>{i % 2 ? slots[part] : part}</Fragment>)
}

function StatusSelect({ statuses, value, onChange, disabled }: { statuses: Status[]; value: string; onChange: (v: string) => void; disabled?: boolean }) {
  return (
    <Select className="select sm" style={{ width: 'auto' }} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
      {statuses.map((s) => (
        <option key={s.id} value={s.name}>
          {t(s.name)}
        </option>
      ))}
    </Select>
  )
}

function Machines() {
  const project = useProjectCtx()
  const key = project.project.key
  const act = useAction()
  const [groupName, setGroupName] = useState('')
  const [newMachine, setNewMachine] = useState<Record<number, string>>({})
  const ro = !project.is_admin
  const isMember = project.my_roles.length > 0 || project.is_admin
  return (
    <div className="col" style={{ gap: 14 }}>
      <div className="card card-pad small muted">
        {t('机台按「机台组」分组（如 CMB-1 下的 EFEM、TM、PM-A），列表视图会按机台组合并单元格展示。成员可以添加机台，修改与删除需要项目管理员。')}
        {!ro && <button className="btn sm" style={{ marginLeft: 12 }}
          title={t('把本项目的机台登记到「管理后台 › 主数据 › 设备」，供部门模板的设备字段选用；本项目的机台不会被修改。')}
          onClick={() => act(() => api.post(`/api/assets/project/${key}/map-machines`), '已登记到主数据设备清单')}>
          {t('登记到主数据设备清单')}
        </button>}
      </div>
      {project.machine_groups.map((g) => (
        <div key={g.id} className="card">
          <div className="card-header">
            <input
              className="input sm"
              style={{ maxWidth: 200, fontWeight: 600 }}
              disabled={ro}
              defaultValue={g.name}
              onBlur={(e) => e.target.value.trim() && e.target.value !== g.name && act(() => api.put(`/api/projects/${key}/machine-groups/${g.id}`, { name: e.target.value.trim() }))}
            />
            <span className="small muted">{t('{n} 台', { n: g.machines.length })}</span>
            <span className="spacer" />
            {!ro && (
              <ConfirmButton
                className="btn ghost sm danger"
                message={t('删除机台组「{name}」及其所有机台？', { name: g.name })}
                onConfirm={() => act(() => api.del(`/api/projects/${key}/machine-groups/${g.id}`), '已删除')}
              >
                {t('删除组')}
              </ConfirmButton>
            )}
          </div>
          <div className="card-pad row wrap" style={{ gap: 8 }}>
            {g.machines.map((m) => (
              <span key={m.id} className="tag" style={{ opacity: m.active ? 1 : 0.5, height: 26 }}>
                {t(m.name)}
                {!ro && (
                  <>
                    <button title={m.active ? t('停用机台') : t('启用机台')} onClick={() => act(() => api.put(`/api/projects/${key}/machines/${m.id}`, { group_id: g.id, name: m.name, active: !m.active }))}>
                      {m.active ? '⏸' : '▶'}
                    </button>
                    <button title={t('删除')} onClick={() => window.confirm(t('删除机台 {name}？', { name: t(m.name) })) && act(() => api.del(`/api/projects/${key}/machines/${m.id}`), '已删除')}>
                      ×
                    </button>
                  </>
                )}
              </span>
            ))}
            {isMember && (
              <span className="row" style={{ gap: 4 }}>
                <input
                  className="input sm w-short"
                  placeholder={t('+ 机台')}
                  value={newMachine[g.id] ?? ''}
                  onChange={(e) => setNewMachine({ ...newMachine, [g.id]: e.target.value })}
                  onKeyDown={async (e) => {
                    const name = (newMachine[g.id] ?? '').trim()
                    if (e.key === 'Enter' && name && (await act(() => api.post(`/api/projects/${key}/machines`, { group_id: g.id, name }), '已添加'))) setNewMachine({ ...newMachine, [g.id]: '' })
                  }}
                />
              </span>
            )}
          </div>
        </div>
      ))}
      {project.machine_groups.length === 0 && <Empty icon="⚙" title={t('还没有机台组')} />}
      {!ro && (
        <div className="row">
          <input className="input" style={{ maxWidth: 240 }} placeholder={t('新机台组名称，如 CMB-4')} value={groupName} onChange={(e) => setGroupName(e.target.value)} />
          <button
            className="btn primary"
            disabled={!groupName.trim()}
            onClick={async () => {
              if (await act(() => api.post(`/api/projects/${key}/machine-groups`, { name: groupName.trim() }), '已添加')) setGroupName('')
            }}
          >
            {t('添加机台组')}
          </button>
        </div>
      )}
    </div>
  )
}

function Fields() {
  const project = useProjectCtx()
  const act = useAction()
  const [options, setOptions] = useState<string[]>(project.project.settings.update_scope_options ?? [])
  const [input, setInput] = useState('')
  const [labels, setLabels] = useState<string[]>(
    () => project.labels?.length ? project.labels : (project.project.settings.label_options ?? []),
  )
  const [labelInput, setLabelInput] = useState('')
  const ro = !project.is_admin
  return (
    <div className="card card-pad col" style={{ gap: 12, maxWidth: 640 }}>
      <h3>{t('「影响范围」可选项')}</h3>
      <div className="small muted">{t('电气组填写区的多选字段。Excel 导入时会自动匹配这些选项。')}</div>
      <div className="row wrap" style={{ gap: 6 }}>
        {options.map((o, i) => (
          <span key={o} className="tag" style={{ height: 26 }}>
            {!ro && i > 0 && (
              <button onClick={() => setOptions([...options.slice(0, i - 1), o, options[i - 1], ...options.slice(i + 1)])} title={t('左移')}>
                ‹
              </button>
            )}
            {t(o)}
            {!ro && <button onClick={() => setOptions(options.filter((x) => x !== o))}>×</button>}
          </span>
        ))}
        {!ro && (
          <input
            className="input sm w-short"
            placeholder={t('+ 新选项，回车')}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && input.trim() && !options.includes(input.trim())) {
                setOptions([...options, input.trim()])
                setInput('')
              }
            }}
          />
        )}
      </div>
      {!ro && (
        <div>
          <button className="btn primary" onClick={() => act(() => api.put(`/api/projects/${project.project.key}`, { settings: { update_scope_options: options } }))}>
            {t('保存')}
          </button>
        </div>
      )}

      <h3 style={{ marginTop: 8 }}>{t('标签可选项')}</h3>
      <div className="small muted">{t('项目共享的标签目录。在问题里选用或新建标签后会自动加入；也可在此统一维护。筛选与 JQL 可用 label = 标签名。')}</div>
      <div className="row wrap" style={{ gap: 6 }}>
        {labels.map((o, i) => (
          <span key={o} className="tag" style={{ height: 26 }}>
            {!ro && i > 0 && (
              <button onClick={() => setLabels([...labels.slice(0, i - 1), o, labels[i - 1], ...labels.slice(i + 1)])} title={t('左移')}>
                ‹
              </button>
            )}
            {o}
            {!ro && <button onClick={() => setLabels(labels.filter((x) => x !== o))}>×</button>}
          </span>
        ))}
        {!ro && (
          <input
            className="input sm w-short"
            placeholder={t('+ 新标签，回车')}
            value={labelInput}
            onChange={(e) => setLabelInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && labelInput.trim() && !labels.includes(labelInput.trim())) {
                setLabels([...labels, labelInput.trim()])
                setLabelInput('')
              }
            }}
          />
        )}
      </div>
      {!ro && (
        <div>
          <button className="btn primary" onClick={() => act(() => api.put(`/api/projects/${project.project.key}`, { settings: { label_options: labels } }))}>
            {t('保存标签')}
          </button>
        </div>
      )}
      <SoftwareUnits />
    </div>
  )
}

function SoftwareUnits() {
  const project = useProjectCtx()
  const act = useAction()
  const configured = project.project.settings.software_units ?? []
  const [paths, setPaths] = useState<string[]>(configured.length ? configured : project.software_units.map((u) => u.path))
  const units = useQuery({
    queryKey: ['org-tree-flat'],
    queryFn: () => api.get<{ units: OrgUnit[] }>('/api/org/tree'),
    staleTime: 300_000,
  })
  const ro = !project.is_admin
  const byPath = new Map((units.data?.units ?? []).map((u) => [u.path, u]))
  const save = (next: string[]) => act(() => api.put(`/api/projects/${project.project.key}`, { settings: { software_units: next } }))
  return (
    <div className="col" style={{ gap: 8, borderTop: '1px solid var(--border)', paddingTop: 14, marginTop: 4 }}>
      <h3>{t('软件部门')}</h3>
      <div className="small muted">
        {t('「是否涉及软件」为「是」时，软件负责人只能从这些组织（含下级）中选择。未配置时自动识别名称以 Software / 软件 开头的组织。')}
      </div>
      <div className="row wrap" style={{ gap: 6 }}>
        {paths.map((p) => (
          <span key={p} className="tag" style={{ height: 26 }} title={p}>
            {byPath.get(p)?.label ?? p.split(' / ').pop()}
            {!ro && <button onClick={() => setPaths(paths.filter((x) => x !== p))}>×</button>}
          </span>
        ))}
        {paths.length === 0 && <span className="small faint">{t('未找到软件部门，所有人员均可选')}</span>}
        {!ro && (
          <Select
            className="input sm"
            style={{ width: 220 }}
            value=""
            onChange={(e) => e.target.value && !paths.includes(e.target.value) && setPaths([...paths, e.target.value])}
          >
            <option value="">{t('+ 添加组织')}</option>
            {(units.data?.units ?? []).map((u) => (
              <option key={u.id} value={u.path} disabled={paths.includes(u.path)}>
                {'\u00a0\u00a0'.repeat(Math.max(0, u.level - 1))}
                {u.label}
              </option>
            ))}
          </Select>
        )}
      </div>
      <div className="tiny faint">{configured.length ? t('当前为手动配置') : t('当前为自动识别')}</div>
      {!ro && (
        <div className="row" style={{ gap: 8 }}>
          <button className="btn primary" onClick={() => save(paths)}>
            {t('保存')}
          </button>
          {configured.length > 0 && (
            <button
              className="btn ghost"
              onClick={async () => {
                if (await save([])) setPaths([])
              }}
            >
              {t('恢复自动识别')}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function Boards() {
  const project = useProjectCtx()
  const [params] = useSearchParams()
  const [boardId, setBoardId] = useState<number>(Number(params.get('board')) || project.boards[0]?.id)
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['boards-config', project.project.key],
    queryFn: () => api.get<{ boards: Board[] }>(`/api/projects/${project.project.key}/boards`),
  })
  const board = data?.boards.find((b) => b.id === boardId)
  return (
    <div className="col" style={{ gap: 14 }}>
      <div className="row">
        <Select className="select" style={{ maxWidth: 320 }} value={boardId} onChange={(e) => setBoardId(Number(e.target.value))}>
          {(data?.boards ?? []).map((b) => (
            <option key={b.id} value={b.id}>
              {b.kind === 'scrum' ? t('冲刺看板') : t('看板')} · {b.name}
            </option>
          ))}
        </Select>
      </div>
      {error ? (
        <div className="callout danger row" role="alert">
          <span>{(error as Error).message}</span>
          <span className="spacer" />
          <button type="button" className="btn sm" onClick={() => refetch()}>{t('重试')}</button>
        </div>
      ) : board ? (
        <BoardEditor key={board.id} board={board} project={project} />
      ) : isLoading ? (
        <Spinner center />
      ) : (
        <div className="empty">{t('没有可配置的看板')}</div>
      )}
    </div>
  )
}

function BoardEditor({ board, project }: { board: Board; project: ProjectDetail }) {
  const client = useQueryClient()
  const act = useAction()
  const [name, setName] = useState(board.name)
  const [columns, setColumns] = useState<BoardColumn[]>(board.columns)
  const [swimlane, setSwimlane] = useState(board.swimlane)
  const [filters, setFilters] = useState(board.quick_filters)
  const [filterJql, setFilterJql] = useState(board.filter_jql)
  const ro = !project.is_admin
  const mapped = new Set(columns.flatMap((c) => c.status_ids))
  const unmapped = project.statuses.filter((s) => !mapped.has(s.id))
  const update = (i: number, changes: Partial<BoardColumn>) => setColumns(columns.map((c, j) => (j === i ? { ...c, ...changes } : c)))
  const save = () =>
    act(async () => {
      await api.put(`/api/boards/${board.id}`, { name, columns, swimlane, quick_filters: filters.filter((f) => f.name && f.jql), filter_jql: filterJql })
      client.invalidateQueries({ queryKey: ['boards-config'] })
    })
  return (
    <div className="col" style={{ gap: 14 }}>
      <div className="card card-pad grid grid-2" style={{ gap: 12 }}>
        <div className="field">
          <label>{t('看板名称')}</label>
          <input className="input" disabled={ro} value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field">
          <label>
            {t('默认泳道')}
            <InfoTip topic="swimlane" />
          </label>
          <Select className="select" disabled={ro} value={swimlane} onChange={(e) => setSwimlane(e.target.value as Board['swimlane'])}>
            <option value="none">{t('无')}</option>
            <option value="assignee">{t('负责人')}</option>
            <option value="group">{t('机台组')}</option>
            <option value="epic">{t('史诗')}</option>
            <option value="priority">{t('优先级')}</option>
          </Select>
        </div>
        <div className="field" style={{ gridColumn: '1 / -1' }}>
          <label>
            {t('看板筛选（JQL，可选，例如只显示某些类型或机台组）')}
            <InfoTip topic="jql" />
          </label>
          <input className="input mono" disabled={ro} value={filterJql} onChange={(e) => setFilterJql(e.target.value)} placeholder={t('type = 需求 AND group in ("CMB-1", "CMB-2")')} />
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h3>{t('列')}</h3>
          <span className="small muted">{t('每列可映射多个状态；在制品上限（WIP）指该列最多容纳的问题数，超出时列会标红')}</span>
          <span className="spacer" />
          {!ro && (
            <button className="btn sm" onClick={() => setColumns([...columns, { name: t('新列'), status_ids: [], wip: null }])}>
              {t('＋ 添加列')}
            </button>
          )}
        </div>
        <div className="card-pad row" style={{ alignItems: 'flex-start', overflowX: 'auto', overflowY: 'hidden', gap: 10 }}>
          {columns.map((c, i) => (
            <div key={i} className="column" style={{ width: 220, padding: 10, gap: 8, maxHeight: 'none' }}>
              <input className="input sm" disabled={ro} value={c.name} onChange={(e) => update(i, { name: e.target.value })} />
              <div className="row small">
                <span className="row" style={{ gap: 0 }}>
                  {t('在制品上限')}
                  <InfoTip topic="wip" />
                </span>
                <input className="input sm w-num" type="number" min={0} disabled={ro} value={c.wip ?? ''} onChange={(e) => update(i, { wip: e.target.value ? Number(e.target.value) : null })} />
              </div>
              <div className="row" style={{ gap: 4, flexWrap: 'wrap' }}>
                {c.status_ids.map((sid) => {
                  const s = project.statuses.find((x) => x.id === sid)
                  return (
                    <span key={sid} className="row" style={{ gap: 0 }}>
                      <StatusPill status={s} />
                      {!ro && (
                        <button className="btn ghost sm icon" onClick={() => update(i, { status_ids: c.status_ids.filter((x) => x !== sid) })}>
                          ×
                        </button>
                      )}
                    </span>
                  )
                })}
              </div>
              {!ro && unmapped.length > 0 && (
                <Select className="select sm" value="" onChange={(e) => e.target.value && update(i, { status_ids: [...c.status_ids, Number(e.target.value)] })}>
                  <option value="">{t('+ 映射状态')}</option>
                  {unmapped.map((s) => (
                    <option key={s.id} value={s.id}>
                      {t(s.name)}
                    </option>
                  ))}
                </Select>
              )}
              {!ro && (
                <div className="row">
                  <button className="btn ghost sm icon" disabled={i === 0} onClick={() => setColumns([...columns.slice(0, i - 1), c, columns[i - 1], ...columns.slice(i + 1)])}>
                    ←
                  </button>
                  <button className="btn ghost sm icon" disabled={i === columns.length - 1} onClick={() => setColumns([...columns.slice(0, i), columns[i + 1], c, ...columns.slice(i + 2)])}>
                    →
                  </button>
                  <span className="spacer" />
                  <button className="btn ghost sm danger" onClick={() => setColumns(columns.filter((_, j) => j !== i))}>
                    {t('删除')}
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
        {unmapped.length > 0 && (
          <div className="card-pad small warn-text" style={{ paddingTop: 0 }}>
            {t('未映射的状态（处于这些状态的问题不会显示在看板上）：{names}', { names: unmapped.map((s) => t(s.name)).join(t('、')) })}
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-header">
          <h3>
            {t('快速筛选')}
            <InfoTip topic="quick_filters" />
          </h3>
          <span className="spacer" />
          {!ro && (
            <button className="btn sm" onClick={() => setFilters([...filters, { name: '', jql: '' }])}>
              {t('＋ 添加')}
            </button>
          )}
        </div>
        <div className="card-pad col" style={{ gap: 8 }}>
          {filters.map((f, i) => (
            <div key={i} className="row">
              <input className="input sm w-short" disabled={ro} placeholder={t('名称')} value={f.name} onChange={(e) => setFilters(filters.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
              <input className="input sm mono" disabled={ro} placeholder="JQL" value={f.jql} onChange={(e) => setFilters(filters.map((x, j) => (j === i ? { ...x, jql: e.target.value } : x)))} />
              {!ro && (
                <button className="btn ghost sm icon" onClick={() => setFilters(filters.filter((_, j) => j !== i))}>
                  ×
                </button>
              )}
            </div>
          ))}
          {filters.length === 0 && <div className="small faint">{t('暂无快速筛选')}</div>}
        </div>
      </div>
      {!ro && (
        <div>
          <button className="btn primary" onClick={save}>
            {t('保存看板配置')}
          </button>
        </div>
      )}
    </div>
  )
}

interface Preview {
  token: string
  filename?: string
  sheets?: string[]
  sheet: string
  header_row?: number
  columns: { index: number; header: string; guess: string | null }[]
  mapping: Record<number, string>
  rows: { row: number; title: string; project_name: string; machine_count: number; update_scope: string[]; status_id: number | null; test_passed: boolean | null }[]
  total: number
  warnings: Record<string, string[]>
}

const IMPORT_FIELD_LABELS: Record<string, string> = {
  seq: '序号（忽略）',
  key: '编号（忽略）',
  reporter: '需求提出人',
  project_name: '项目名称',
  machine_group: '机台组',
  machine: '机台',
  title: '标题',
  description: '需求说明',
  update_scope: '影响范围',
  involves_software: '是否涉及软件',
  software_owner: '软件负责人',
  software_ready: '软件是否准备好',
  software_note: '软件交付说明',
  software_jira_key: '软件 Jira 单号',
  verify_method: '验证方法',
  verify_criteria: '验证标准',
  status: '当前状态',
  assignee: '负责人',
  update_date: '更新日期',
  tester: '测试人',
  test_passed: '是否通过',
  remark: '备注',
  priority: '优先级',
  due_date: '截止日期',
}

const WARNING_LABELS: Record<string, string> = {
  未匹配的人员: '未匹配的人员（将留空，报告人默认为导入者）',
  未匹配的状态: '未匹配的状态（使用默认状态）',
  未匹配的机台: '未匹配的机台（勾选下方选项可自动创建）',
  未匹配的机台组: '未匹配的机台组（勾选下方选项可自动创建）',
}

function ImportSection() {
  const project = useProjectCtx()
  const importFields = {
    ...IMPORT_FIELD_LABELS,
    ...Object.fromEntries(project.profile.custom_fields.map((field) => [`custom.${field.key}`, field.label])),
  }
  const key = project.project.key
  const { toast } = useApp()
  const client = useQueryClient()
  const [preview, setPreview] = useState<Preview | null>(null)
  const [busy, setBusy] = useState(false)
  const [createMachines, setCreateMachines] = useState(true)
  const history = useQuery({
    queryKey: ['imports', key],
    queryFn: () => api.get<{ imports: { id: number; filename: string; count: number; undone: boolean; user: UserBrief | null; created_at: string }[] }>(`/api/projects/${key}/imports`),
  })
  if (!project.is_admin) return <Empty icon="🔒" title={t('需要项目管理员权限')} />

  const upload = async (file: File) => {
    setBusy(true)
    try {
      const form = new FormData()
      form.append('file', file)
      setPreview(await api.upload<Preview>(`/api/projects/${key}/import/preview`, form))
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const remap = async (mapping: Record<number, string>, sheet?: string) => {
    if (!preview) return
    setBusy(true)
    try {
      const res = await api.post<Omit<Preview, 'token'>>(`/api/projects/${key}/import/remap`, { token: preview.token, sheet: sheet ?? preview.sheet, mapping })
      setPreview({ ...preview, ...res })
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const commit = async () => {
    if (!preview) return
    setBusy(true)
    try {
      const res = await api.post<{ created: unknown[] }>(`/api/projects/${key}/import/commit`, {
        token: preview.token,
        sheet: preview.sheet,
        mapping: preview.mapping,
        create_missing_machines: createMachines,
      })
      toast(t('已导入 {n} 条', { n: res.created.length }), 'success')
      setPreview(null)
      for (const qk of ['board', 'backlog', 'list', 'search', 'project', 'project-issues', 'dashboard'] as const) {
        client.invalidateQueries({ queryKey: [qk] })
      }
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const statusName = (id: number | null) => t(project.statuses.find((s) => s.id === id)?.name ?? '默认')

  return (
    <div className="col" style={{ gap: 16 }}>
      {!preview ? (
        <div className="card card-pad col" style={{ gap: 10 }}>
          <h3>{t('从 Excel 导入需求')}</h3>
          <div className="small muted">
            {t('支持现有的需求跟踪表：自动识别表头行（需求提出人 / 项目名称 / 机台 / 需求说明 / 影响范围 / … / 备注），合并的机台组单元格会自动展开，两列「机台」分别识别为机台组和机台。')}
          </div>
          <label className="dropzone">
            {busy ? <Spinner /> : t('点击选择 .xlsx 文件')}
            <input type="file" accept=".xlsx,.xlsm" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          </label>
        </div>
      ) : (
        <div className="card">
          <div className="card-header">
            <h3>{t('预览：{name}', { name: preview.filename ?? '' })}</h3>
            {preview.sheets && preview.sheets.length > 1 && (
              <Select className="select sm" style={{ width: 'auto' }} value={preview.sheet} onChange={(e) => remap({}, e.target.value)}>
                {preview.sheets.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </Select>
            )}
            <span className="small muted">{t('共 {n} 行数据', { n: preview.total })}</span>
            <span className="spacer" />
            {busy && <Spinner />}
          </div>
          <div className="card-pad col" style={{ gap: 14 }}>
            <div>
              <b className="small">{t('列映射')}</b>
              <div className="grid grid-4" style={{ gap: 8, marginTop: 8 }}>
                {preview.columns.map((c) => (
                  <div key={c.index} className="field">
                    <label className="ellipsis" title={c.header}>
                      {c.header}
                    </label>
                    <Select
                      className="select sm"
                      value={preview.mapping[c.index] ?? ''}
                      onChange={(e) => {
                        const mapping = { ...preview.mapping }
                        if (e.target.value) mapping[c.index] = e.target.value
                        else delete mapping[c.index]
                        remap(mapping)
                      }}
                    >
                      <option value="">{t('（不导入）')}</option>
                      {Object.entries(importFields).map(([f, label]) => (
                        <option key={f} value={f}>
                          {t(label)}
                        </option>
                      ))}
                    </Select>
                  </div>
                ))}
              </div>
            </div>
            {Object.entries(preview.warnings).some(([, v]) => v.length) && (
              <div className="triage-box small col" style={{ gap: 4 }}>
                {Object.entries(preview.warnings)
                  .filter(([, v]) => v.length)
                  .map(([k, v]) => (
                    <div key={k}>
                      <span className="warn-text bold">
                        {WARNING_LABELS[k] ? t(WARNING_LABELS[k]) : tServer(k)}
                        {t('：')}
                      </span>
                      {v.join(t('、'))}
                    </div>
                  ))}
              </div>
            )}
            <div style={{ maxHeight: 340, overflow: 'auto', border: '1px solid var(--border)', borderRadius: 10 }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('行')}</th>
                    <th>{t('标题')}</th>
                    <th>{t('项目名称')}</th>
                    <th>{t('机台数')}</th>
                    <th>{t('影响范围')}</th>
                    <th>{t('状态')}</th>
                    <th>{t('测试结果')}</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.rows.map((r) => (
                    <tr key={r.row}>
                      <td className="faint">{r.row}</td>
                      <td style={{ maxWidth: 360 }}>
                        <div className="ellipsis">{r.title}</div>
                      </td>
                      <td>{r.project_name}</td>
                      <td>{r.machine_count}</td>
                      <td className="small">{r.update_scope.map((s) => t(s)).join(t('、'))}</td>
                      <td className="small">{statusName(r.status_id)}</td>
                      <td className="small">{r.test_passed === null ? '' : r.test_passed ? t('通过') : t('不通过')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {preview.total > preview.rows.length && <div className="tiny faint">{t('仅显示前 {n} 行', { n: preview.rows.length })}</div>}
            <div className="row">
              <label className="row small">
                <input type="checkbox" checked={createMachines} onChange={(e) => setCreateMachines(e.target.checked)} /> {t('自动创建不存在的机台组 / 机台')}
              </label>
              <span className="spacer" />
              <button className="btn" onClick={() => setPreview(null)}>
                {t('取消')}
              </button>
              <button className="btn primary" disabled={busy} onClick={commit}>
                {t('导入 {n} 行', { n: preview.total })}
              </button>
            </div>
          </div>
        </div>
      )}
      <div className="card">
        <div className="card-header">
          <h3>{t('导入记录')}</h3>
        </div>
        <table className="table">
          <tbody>
            {history.data?.imports.map((b) => (
              <tr key={b.id}>
                <td>#{b.id}</td>
                <td>
                  <span className="row">
                    <Avatar user={b.user} size={20} /> {b.user?.display_name}
                  </span>
                </td>
                <td>{t('{n} 条', { n: b.count })}</td>
                <td className="small muted">{fmtDateTime(b.created_at)}</td>
                <td style={{ textAlign: 'right' }}>
                  {b.undone ? (
                    <span className="faint small">{t('已撤销')}</span>
                  ) : (
                    <ConfirmButton
                      className="btn sm danger"
                      message={t('撤销批次 #{id}？将删除本次导入的 {n} 个问题。', { id: b.id, n: b.count })}
                      onConfirm={async () => {
                        try {
                          const res = await api.post<{ deleted: number }>(`/api/imports/${b.id}/undo`)
                          toast(t('已删除 {n} 个问题', { n: res.deleted }), 'success')
                          for (const qk of ['board', 'backlog', 'list', 'search', 'project', 'project-issues', 'dashboard'] as const) {
                            client.invalidateQueries({ queryKey: [qk] })
                          }
                        } catch (err) {
                          toast((err as Error).message, 'error')
                        }
                      }}
                    >
                      {t('撤销导入')}
                    </ConfirmButton>
                  )}
                </td>
              </tr>
            ))}
            {history.data?.imports.length === 0 && (
              <tr>
                <td className="faint">{t('暂无导入记录')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
