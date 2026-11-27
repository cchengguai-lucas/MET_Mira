import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { CompletionSpark } from '../components/CompletionCurve'
import { DeleteProjectModal } from '../components/DeleteProjectModal'
import { UserPicker } from '../components/Pickers'
import { ConfirmButton, Empty, FieldIcon, Modal, Segmented, Spinner, UserChip, Select } from '../components/ui'
import { useApp } from '../context'
import { t } from '../i18n'
import { fmtDateTime, profileName } from '../labels'
import { EN } from '../locales/en'
import { projects as projectsEn } from '../locales/en/projects'
import { usePinnedProjects } from '../pinnedProjects'
import { ProjectBadge } from '../projectIcons'
import { useArchivedProjects, useProjects } from '../queries'
import type { Project, ProjectProfile, ProjectVisibility, ProjectWorkMode, UserBrief } from '../types'
import { projectWorkMode } from '../types'

Object.assign(EN, projectsEn)

const COLORS = ['#0a84ff', '#5e5ce6', '#bf5af2', '#ff375f', '#ff9f0a', '#30d158', '#64d2ff', '#ac8e68']

export function ProjectsPage() {
  const { user } = useApp()
  const projects = useProjects()
  const archived = useArchivedProjects()
  const { isPinned, toggle, sortPinnedFirst } = usePinnedProjects()
  const [params, setParams] = useSearchParams()
  const view = params.get('view') === 'archived' ? 'archived' : 'active'
  const [creating, setCreating] = useState(false)
  const [q, setQ] = useState('')
  const [pinBusy, setPinBusy] = useState<string | null>(null)
  const navigate = useNavigate()
  const matches = (p: Project) => !q || p.name.includes(q) || p.key.toLowerCase().includes(q.toLowerCase())
  const list = useMemo(() => {
    const filtered = (projects.data ?? []).filter(
      (p) => !q || p.name.includes(q) || p.key.toLowerCase().includes(q.toLowerCase()),
    )
    return sortPinnedFirst(filtered)
  }, [projects.data, q, sortPinnedFirst])
  const deliveryList = list.filter((p) => projectWorkMode(p) === 'delivery').slice(0, 24)
  const curves = useQueries({
    queries: deliveryList.map((p) => ({
      queryKey: ['completion-curve', p.key, 'spark'],
      queryFn: () => api.get<{ spark?: never; points: { date: string; actual_pct: number; plan_pct: number }[]; actual_pct: number; plan_pct: number }>(
        `/api/projects/${p.key}/reports/completion-curve?days=60`,
      ),
      staleTime: 60_000,
      enabled: projectWorkMode(p) === 'delivery',
    })),
  })
  const curveByKey = new Map(deliveryList.map((p, i) => [p.key, curves[i]?.data]))
  const setView = (next: 'active' | 'archived') => setParams(next === 'archived' ? { view: next } : {})
  return (
    <div className="page page-wide">
      <div className="page-header">
        <div>
          <h1>{t('所有项目')}</h1>
          <p className="muted">{t('{n} 个项目', { n: (view === 'archived' ? archived.data : projects.data)?.length ?? 0 })}</p>
        </div>
        <span className="spacer" />
        <Segmented value={view} onChange={setView} items={[
          { value: 'active', label: t('进行中') },
          { value: 'archived', label: <>{t('已归档')}{archived.data?.length ? <span className="count-badge">{archived.data.length}</span> : null}</> },
        ]} />
        <input className="input" style={{ width: 220 }} placeholder={t('筛选项目')} value={q} onChange={(e) => setQ(e.target.value)} />
        {(user.is_admin || user.can_create_projects) && (
          <button className="btn primary" onClick={() => setCreating(true)}>
            {t('＋ 新建项目')}
          </button>
        )}
      </div>
      {view === 'archived' ? <ArchivedProjects projects={(archived.data ?? []).filter(matches)} loading={archived.isLoading} /> : <>
      {projects.isLoading && <Spinner center />}
      {list.length === 0 && !projects.isLoading && <Empty title={t('没有项目')}>{user.is_admin || user.can_create_projects ? t('点击右上角新建项目') : t('请联系管理员创建项目')}</Empty>}
      <div className="grid grid-3">
        {list.map((p) => {
          const pinned = isPinned(p.key)
          return (
          <div key={p.id} className={`card project-card${pinned ? ' pinned' : ''}`} onClick={() => navigate(`/projects/${p.key}/board`)}>
            <div
              className={`project-card-cover${p.cover_url ? '' : ' fallback'}`}
              style={p.cover_url
                ? { backgroundImage: `url(${p.cover_url})` }
                : { background: `linear-gradient(135deg, ${p.color}, color-mix(in srgb, ${p.color} 40%, #1d1d1f))` }}
            >
              <button
                type="button"
                className={`project-pin-btn${pinned ? ' active' : ''}`}
                title={pinned ? t('取消置顶') : t('置顶')}
                aria-label={pinned ? t('取消置顶') : t('置顶')}
                aria-pressed={pinned}
                disabled={pinBusy === p.key}
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  setPinBusy(p.key)
                  void toggle(p.key).finally(() => setPinBusy((cur) => (cur === p.key ? null : cur)))
                }}
              >
                <FieldIcon name="pin" size={14} />
              </button>
              {curveByKey.get(p.key)?.points && (
                <div className="project-card-cover-overlay">
                  <CompletionSpark
                    points={curveByKey.get(p.key)!.points.filter((_, i, arr) => i % Math.max(1, Math.floor(arr.length / 24)) === 0 || i === arr.length - 1)}
                  />
                  <span className="tiny">
                    {t('完成 {n}%', { n: Math.round(curveByKey.get(p.key)!.actual_pct) })}
                  </span>
                </div>
              )}
            </div>
            <div className="project-card-body">
              <div className="row">
                <ProjectBadge icon={p.icon} color={p.color} label={p.key.slice(0, 3)} size={40} />
                <div style={{ minWidth: 0 }}>
                  <div className="bold ellipsis" style={{ fontSize: 16 }}>
                    {p.name}
                  </div>
                  <div className="tiny faint">{p.key}</div>
                </div>
                <span className="spacer" />
                {pinned && <span className="tag project-pin-tag">{t('置顶')}</span>}
                {projectWorkMode(p) === 'ongoing' && <span className="tag">{t('持续项目')}</span>}
                {p.is_member && <span className="tag">{t('成员')}</span>}
              </div>
              <div className="project-card-desc" title={p.description || undefined}>
                {p.description || t('暂无描述')}
              </div>
              <div className="row small">
                <UserChip user={p.lead} size={20} />
                <span className="spacer" />
                <span className="muted">{t('{open} 个未完成 / 共 {total} 个', { open: p.open_count, total: p.total_count })}</span>
              </div>
            </div>
          </div>
          )
        })}
      </div>
      </>}
      {creating && <CreateProjectModal onClose={() => setCreating(false)} />}
    </div>
  )
}

function ArchivedProjects({ projects, loading }: { projects: Project[]; loading: boolean }) {
  const { user, toast } = useApp()
  const client = useQueryClient()
  const navigate = useNavigate()
  const [deleting, setDeleting] = useState<Project | null>(null)
  const restore = async (project: Project) => {
    try {
      await api.put(`/api/projects/${project.key}`, { archived: false })
      await client.invalidateQueries({ queryKey: ['projects'] })
      client.invalidateQueries({ queryKey: ['project', project.key] })
      toast(t('已恢复项目 {key}', { key: project.key }), 'success')
    } catch (err) {
      toast((err as Error).message, 'error')
    }
  }
  if (loading) return <Spinner center />
  if (!projects.length) return <Empty title={t('没有已归档的项目')}>{t('在项目设置中归档的项目会出现在这里，可随时恢复。')}</Empty>
  return (
    <>
      <div className="card archived-list">
        {projects.map((p) => (
          <div key={p.id} className="archived-row">
            <ProjectBadge icon={p.icon} color={p.color} label={p.key.slice(0, 3)} size={34} className="project-logo archived" />
            <div className="col" style={{ gap: 2, minWidth: 0, flex: 1 }}>
              <div className="row" style={{ gap: 8 }}>
                <a className="bold ellipsis" onClick={() => navigate(`/projects/${p.key}/settings`)}>{p.name}</a>
                <span className="tiny faint mono">{p.key}</span>
              </div>
              <div className="tiny muted">
                {t('{total} 个事项', { total: p.total_count ?? 0 })}
                {p.settings.archived_at && ` · ${t('{who} 于 {when} 归档', {
                  who: p.settings.archived_by ?? '—', when: fmtDateTime(p.settings.archived_at),
                })}`}
              </div>
            </div>
            <UserChip user={p.lead} size={20} />
            {p.can_admin && <ConfirmButton className="btn sm" message={t('恢复项目 {key}？恢复后重新出现在项目列表中。', { key: p.key })} onConfirm={() => restore(p)}>{t('恢复')}</ConfirmButton>}
            {user.is_admin && <button className="btn danger sm" onClick={() => setDeleting(p)}>{t('永久删除')}</button>}
          </div>
        ))}
      </div>
      {deleting && <DeleteProjectModal project={deleting} onClose={() => setDeleting(null)} />}
    </>
  )
}

function CreateProjectModal({ onClose }: { onClose: () => void }) {
  const { user, toast } = useApp()
  const client = useQueryClient()
  const navigate = useNavigate()
  const [key, setKey] = useState('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [lead, setLead] = useState<UserBrief | null>(user)
  const [color, setColor] = useState(COLORS[0])
  const [profile, setProfile] = useState('')
  const [visibility, setVisibility] = useState<ProjectVisibility>('public')
  const [workMode, setWorkMode] = useState<ProjectWorkMode>('delivery')
  const [planStartDate, setPlanStartDate] = useState('')
  const [targetDate, setTargetDate] = useState('')
  const profiles = useQuery({
    queryKey: ['project-profile-catalog'],
    queryFn: () => api.get<{ profiles: ProjectProfile[] }>('/api/projects/profile-catalog'),
    staleTime: Infinity,
  })
  const recommendations = useQuery({
    queryKey: ['project-profile-recommendations', lead?.id],
    queryFn: () => api.get<{ profiles: ProjectProfile[] }>(
      `/api/projects/profile-recommendations${lead ? `?lead_id=${lead.id}` : ''}`,
    ),
  })
  const [busy, setBusy] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const keyValid = /^[A-Za-z][A-Za-z0-9]{1,9}$/.test(key)
  const nameMissing = !name.trim()
  const profileMissing = !profile
  const keyError = !key
    ? (submitted ? t('请填写项目编号') : '')
    : keyValid
      ? ''
      : t('项目编号须为 2–10 位字母或数字，且以字母开头')
  const nameError = submitted && nameMissing ? t('请填写项目名称') : ''
  const profileError = submitted && profileMissing ? t('请选择项目模板') : ''
  const canSubmit = keyValid && !nameMissing && !profileMissing
  const submit = async () => {
    setSubmitted(true)
    if (!canSubmit) {
      toast(
        (!key ? t('请填写项目编号') : !keyValid ? t('项目编号须为 2–10 位字母或数字，且以字母开头') : '')
          || (nameMissing ? t('请填写项目名称') : '')
          || (profileMissing ? t('请选择项目模板') : '')
          || t('请检查标红的输入项'),
        'error',
      )
      return
    }
    setBusy(true)
    try {
      const delivery = workMode === 'delivery'
      const datesMissing = delivery && (!planStartDate || !targetDate)
      await api.post('/api/projects', {
        key: key.toUpperCase(),
        name,
        description,
        lead_id: lead?.id,
        color,
        with_machines: false,
        profile,
        visibility,
        work_mode: workMode,
        plan_start_date: delivery ? (planStartDate || null) : null,
        target_date: delivery ? (targetDate || null) : null,
      })
      await client.invalidateQueries({ queryKey: ['projects'] })
      toast(
        datesMissing
          ? t('已创建项目。请尽快在设置中补齐计划开始日期和目标日期，以便后续发布排期基线。')
          : t('项目已创建'),
        'success',
      )
      navigate(`/projects/${key.toUpperCase()}/settings`)
      onClose()
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal
      title={t('新建项目')}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            {t('取消')}
          </button>
          <button className="btn primary" disabled={busy} onClick={submit}>
            {t('创建')}
          </button>
        </>
      }
    >
      <div className="col" style={{ gap: 12 }}>
        <div className="grid grid-2" style={{ gap: 12 }}>
          <div className={`field${nameError ? ' is-invalid' : ''}`}>
            <label>{t('项目名称')}</label>
            <input
              className="input"
              autoFocus
              value={name}
              aria-invalid={!!nameError}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('例如：电气组需求')}
            />
            {nameError ? <div className="field-error" role="alert">{nameError}</div> : null}
          </div>
          <div className={`field${keyError ? ' is-invalid' : ''}`}>
            <label>{t('项目编号（单号前缀，2–10 位字母或数字）')}</label>
            <input
              className="input mono"
              value={key}
              aria-invalid={!!keyError}
              onChange={(e) => setKey(e.target.value.toUpperCase())}
              placeholder="ELEC"
            />
            {keyError ? (
              <div className="field-error" role="alert">{keyError}</div>
            ) : (
              <div className="tiny faint" style={{ marginTop: 4 }}>
                {t('用作问题单号前缀，例如 {key}-12', { key: key || 'ELEC' })}
              </div>
            )}
          </div>
        </div>
        <div className="field">
          <label>{t('描述')}</label>
          <textarea className="textarea" value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div className="field">
          <label>{t('项目类型')} <span className="danger">*</span></label>
          <Segmented
            value={workMode}
            onChange={setWorkMode}
            items={[
              { value: 'delivery', label: t('交付项目') },
              { value: 'ongoing', label: t('持续项目') },
            ]}
          />
          <div className="tiny faint" style={{ marginTop: 4 }}>
            {workMode === 'delivery'
              ? t('有明确目标日期，可发布排期基线并跟踪延期风险。')
              : t('长期事务、备忘跟进或杂项清单；保留完整流转，不发布排期基线。')}
          </div>
        </div>
        {workMode === 'delivery' && (
          <div className="grid grid-2" style={{ gap: 12 }}>
            <div className="field">
              <label>{t('计划开始日期')}</label>
              <input
                className="input"
                type="date"
                value={planStartDate}
                onChange={(e) => setPlanStartDate(e.target.value)}
              />
            </div>
            <div className="field">
              <label>{t('项目目标日期')}</label>
              <input
                className="input"
                type="date"
                value={targetDate}
                min={planStartDate || undefined}
                onChange={(e) => setTargetDate(e.target.value)}
              />
            </div>
            <div className="tiny faint" style={{ gridColumn: '1 / -1' }}>
              {t('选填。完整排期基线在创建后、有单据时再发布。跳过会提醒你稍后补充。')}
            </div>
          </div>
        )}
        <div className={`field${profileError ? ' is-invalid' : ''}`}>
          <label>{t('项目模板')} <span className="danger">*</span></label>
          <Select
            className="select"
            value={profile}
            aria-invalid={!!profileError}
            onChange={(e) => setProfile(e.target.value)}
          >
            <option value="">{t('请选择项目模板')}</option>
            {(profiles.data?.profiles ?? []).map((item) => (
              <option key={item.key} value={item.key}>{profileName(item)}</option>
            ))}
          </Select>
          {profileError ? (
            <div className="field-error" role="alert">{profileError}</div>
          ) : (
            <div className="tiny faint" style={{ marginTop: 4 }}>
              {t('必选。模板决定表单分区、字段和工作流。')}
              {recommendations.data?.profiles[0] && (
                <> {t('由 {department} 推荐：{template}', {
                  department: recommendations.data.profiles[0].recommended_by?.path
                    || recommendations.data.profiles[0].recommended_by?.name
                    || t('负责人所在部门'),
                  template: profileName(recommendations.data.profiles[0]),
                })}</>
              )}
            </div>
          )}
        </div>
        <div className="field">
          <label>{t('可见范围')}</label>
          <Select className="select" value={visibility} onChange={(e) => setVisibility(e.target.value as ProjectVisibility)}>
            <option value="public">{t('公开：所有登录用户可查看和提需求')}</option>
            <option value="members">{t('仅成员：只有项目成员和系统管理员可查看')}</option>
          </Select>
          <div className="tiny faint" style={{ marginTop: 4 }}>
            {t('仅成员模式下，非成员仍可查看自己报告、负责、测试或担任软件负责人的问题。')}
          </div>
        </div>
        <div className="grid grid-2" style={{ gap: 12 }}>
          <div className="field">
            <label>{t('项目负责人')}</label>
            <UserPicker value={lead} onChange={setLead} allowClear={false} />
          </div>
          <div className="field">
            <label>{t('颜色')}</label>
            <div className="row">
              {COLORS.map((c) => (
                <button key={c} className="color-swatch" style={{ background: c, outline: c === color ? '2px solid var(--text)' : 'none' }} onClick={() => setColor(c)} />
              ))}
            </div>
          </div>
        </div>
        <div className="tiny faint">{t('将自动创建按类型区分的工作流（需求包含方案讨论和测试，缺陷和故事包含测试，任务、子任务和史诗只有待办 → 进行中 → 已完成），以及看板和冲刺看板。')}</div>
      </div>
    </Modal>
  )
}
