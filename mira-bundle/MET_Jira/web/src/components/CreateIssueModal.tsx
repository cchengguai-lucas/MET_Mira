import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { useApp } from '../context'
import { pick, t } from '../i18n'
import { customFieldLabel, ISSUE_TYPES, PRIORITIES, priorityName, typeName } from '../labels'
import { ProjectBadge } from '../projectIcons'
import { useInvalidateIssues, useProject, useProjects } from '../queries'
import type { IssueType, Priority, ProjectDetail, UserBrief, UserFull } from '../types'
import { AssetPicker, ChipSelect, LabelPicker, MachinePicker, TriState, UserPicker } from './Pickers'
import {
  StagedAttachmentsField,
  StagedMarkdownField,
  mergeCreateTokens,
  referencedTokens,
  type StagedFiles,
} from './StagedMarkdownField'
import { DateField } from './DateField'
import { Avatar, FieldIcon, Fold, InfoTip, Modal, PriorityIcon, Spinner, TypeIcon, useDebounced, Select } from './ui'

interface DraftChild {
  type: string
  title: string
  description: string
  priority: Priority
}

interface Draft {
  type: IssueType
  title: string
  description: string
  priority: Priority
  machine_ids: number[]
  unresolved_machines: string[]
  update_scope: string[]
  project_name: string
  involves_software: boolean | null
  verify_method: string
  verify_criteria: string
  labels: string[]
  custom_fields?: Record<string, unknown>
  children: DraftChild[]
  assignee_username?: string
  assignee_reason?: string
}

interface TriageResult {
  duplicates: { key: string; title: string; status: string | null; relation: string; reason: string; score: number }[]
  assignee: { username: string; name: string; org: string | null; reason?: string; open_load: number } | null
  priority: Priority | null
  priority_reason: string | null
  source: 'llm' | 'heuristic'
}

/** Builtins shown on the create form (not behind optional team fields) that gates may require later. */
const CREATE_PROMPT_BUILTINS = new Set([
  'update_scope', 'project_name', 'machine_ids', 'assignee_id',
])

/** Electrical-section builtins folded behind the optional expander on create. */
const ELECTRICAL_OPTIONAL_FIELDS = [
  'involves_software', 'software_owner_id', 'verify_method', 'verify_criteria',
] as const

/** Whether the project's template sections include this field (not general-only). */
function profileHasField(project: ProjectDetail | undefined, fieldKey: string): boolean {
  return !!project?.profile.sections.some((section) => section.fields.includes(fieldKey))
}

/** Match backend ``Profile.field_applies_to_type``: skip gates for fields the type cannot edit. */
function fieldAppliesToType(project: ProjectDetail, fieldKey: string, issueType: IssueType): boolean {
  const owners = project.profile.sections.filter((section) => section.fields.includes(fieldKey))
  if (!owners.length) return true
  return owners.some((section) => !section.issue_types?.length || section.issue_types.includes(issueType))
}

function gateFieldKeys(project: ProjectDetail | undefined, issueType: IssueType): Set<string> {
  const out = new Set<string>()
  if (!project) return out
  for (const gate of Object.values(project.project.settings.transition_gates ?? {})) {
    for (const raw of gate.required_fields ?? []) {
      const key = raw.replace(/^custom_fields\./, '')
      if (!fieldAppliesToType(project, key, issueType)) continue
      out.add(key)
    }
  }
  return out
}

/** Template-required builtins + project gate fields we can fill at create. */
function createRequiredBuiltins(project: ProjectDetail | undefined, issueType: IssueType): Set<string> {
  const out = new Set<string>()
  if (!project) return out
  for (const field of project.profile.definition?.fields ?? []) {
    if (field.kind === 'builtin' && field.required && CREATE_PROMPT_BUILTINS.has(field.key)
      && fieldAppliesToType(project, field.key, issueType)) {
      out.add(field.key)
    }
  }
  for (const key of gateFieldKeys(project, issueType)) {
    if (CREATE_PROMPT_BUILTINS.has(key)) out.add(key)
  }
  return out
}

function gateRequiresCustom(
  project: ProjectDetail | undefined, fieldKey: string, issueType: IssueType,
): boolean {
  return gateFieldKeys(project, issueType).has(fieldKey)
}

function CreateReq() {
  return <span className="create-req">{t('必填')}</span>
}

function createFieldClass(opts: { required?: boolean; missing?: boolean }): string {
  return [
    'field',
    'create-field',
    opts.required ? 'is-required' : '',
    opts.missing ? 'is-missing' : '',
  ].filter(Boolean).join(' ')
}

export function CreateIssueModal() {
  const { createState, closeCreate, toast, openIssue, user } = useApp()
  const defaults = createState ?? {}
  const projects = useProjects()
  const [projectKey, setProjectKey] = useState(defaults.project ?? '')
  const project = useProject(projectKey || undefined)
  const invalidate = useInvalidateIssues()

  const [type, setType] = useState<IssueType>((defaults.type as IssueType) ?? 'requirement')
  const [title, setTitle] = useState(defaults.title ?? '')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState<Priority>('medium')
  const [assignee, setAssignee] = useState<UserBrief | null>(null)
  const [tester, setTester] = useState<UserBrief | null>(null)
  const [machineIds, setMachineIds] = useState<number[]>([])
  const [scope, setScope] = useState<string[]>([])
  const [projectName, setProjectName] = useState('')
  const [software, setSoftware] = useState<boolean | null>(null)
  const [softwareOwner, setSoftwareOwner] = useState<UserBrief | null>(null)
  const [verifyMethod, setVerifyMethod] = useState('')
  const [verifyCriteria, setVerifyCriteria] = useState('')
  const [labels, setLabels] = useState<string[]>([])
  const [dueDate, setDueDate] = useState('')
  const [epicId, setEpicId] = useState<number | null>(defaults.epic_id ?? null)
  const [more, setMore] = useState(false)
  const [customFields, setCustomFields] = useState<Record<string, unknown>>({})
  const [customUsers, setCustomUsers] = useState<Record<string, UserBrief | null>>({})
  const [staged, setStaged] = useState<StagedFiles>({})
  const [extraFiles, setExtraFiles] = useState<StagedFiles>({})
  const [another, setAnother] = useState(false)
  const [busy, setBusy] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const formRef = useRef<HTMLDivElement>(null)

  const [aiText, setAiText] = useState(defaults.aiText ?? '')
  const [aiBusy, setAiBusy] = useState(false)
  const [split, setSplit] = useState(false)
  const [drafts, setDrafts] = useState<Draft[] | null>(null)
  const [aiHint, setAiHint] = useState<string | null>(null)
  const [confirmClose, setConfirmClose] = useState(false)

  const dirty = useMemo(() => {
    const emptyCustom = Object.values(customFields).every((value) => (
      value === null || value === undefined || value === ''
      || (Array.isArray(value) && value.length === 0)
    ))
    return title.trim() !== (defaults.title ?? '').trim()
      || description.trim() !== ''
      || priority !== 'medium'
      || assignee !== null
      || tester !== null
      || machineIds.length > 0
      || scope.length > 0
      || projectName.trim() !== ''
      || software !== null
      || softwareOwner !== null
      || verifyMethod.trim() !== ''
      || verifyCriteria.trim() !== ''
      || labels.length > 0
      || dueDate !== ''
      || epicId !== (defaults.epic_id ?? null)
      || type !== ((defaults.type as IssueType) ?? 'requirement')
      || !emptyCustom
      || Object.keys(staged).length > 0
      || Object.keys(extraFiles).length > 0
      || aiText.trim() !== (defaults.aiText ?? '').trim()
      || drafts !== null
  }, [
    title, description, priority, assignee, tester, machineIds, scope, projectName,
    software, softwareOwner, verifyMethod, verifyCriteria, labels, dueDate, epicId,
    type, customFields, staged, extraFiles, aiText, drafts, defaults.title, defaults.aiText,
    defaults.epic_id, defaults.type,
  ])

  const requestClose = () => {
    if (busy || aiBusy) return
    if (confirmClose) {
      setConfirmClose(false)
      return
    }
    if (dirty) {
      setConfirmClose(true)
      return
    }
    closeCreate()
  }

  useEffect(() => {
    if (!projectKey && projects.data?.length) {
      const mine = projects.data.find((p) => p.is_member) ?? projects.data[0]
      setProjectKey(mine.key)
    }
  }, [projects.data, projectKey])

  useEffect(() => {
    setSoftwareOwner(null)
    setSoftware(null)
    setVerifyMethod('')
    setVerifyCriteria('')
    setScope([])
    setMore(false)
    setCustomFields({})
    setCustomUsers({})
  }, [projectKey])

  const dueRequired = !defaults.parent_id
  const titleMissing = !title.trim()
  const dueMissing = dueRequired && !dueDate
  const requiredBuiltins = createRequiredBuiltins(project.data, type)
  const showProjectName = type === 'requirement' && profileHasField(project.data, 'project_name')
  const showMachines = profileHasField(project.data, 'machine_ids')
  const showScope = type === 'requirement' && profileHasField(project.data, 'update_scope')
  const showElectricalOptional = type === 'requirement'
    && ELECTRICAL_OPTIONAL_FIELDS.some((key) => profileHasField(project.data, key))
  /** Impact scope is required for requirements only when the template includes it. */
  const scopeRequired = showScope
  const scopeMissing = scopeRequired && scope.length === 0
  const machinesRequired = showMachines && requiredBuiltins.has('machine_ids')
  const machinesMissing = machinesRequired && machineIds.length === 0
  const projectNameRequired = showProjectName && requiredBuiltins.has('project_name')
  const projectNameMissing = projectNameRequired && !projectName.trim()
  const assigneeRequired = requiredBuiltins.has('assignee_id')
  const assigneeMissing = assigneeRequired && !assignee
  const showTester = profileHasField(project.data, 'tester_id')
  const testerDefaulted = useRef<string | null>(null)
  // Self-test default: creator is also the tester on first open of a project with testers.
  useEffect(() => {
    if (!showTester || !user) return
    if (testerDefaulted.current === projectKey) return
    testerDefaulted.current = projectKey
    setTester({
      id: user.id,
      username: user.username,
      display_name: user.display_name,
      avatar_url: user.avatar_url ?? null,
      avatar_color: user.avatar_color ?? null,
      title: user.title,
      email: user.email,
    })
  }, [showTester, projectKey, user])
  const requiredCustomMissing = (project.data?.profile.custom_fields ?? []).some((field) => {
    if (!field.required && !gateRequiresCustom(project.data, field.key, type)) return false
    const section = project.data?.profile.sections.find((item) => item.key === field.section)
    if (section?.issue_types?.length && !section.issue_types.includes(type)) return false
    const value = customFields[field.key]
    return value === null || value === undefined || value === ''
      || (Array.isArray(value) && value.length === 0)
  })
  const gateCreateMissing = scopeMissing || machinesMissing || projectNameMissing || assigneeMissing
  const createBlocked = titleMissing || !projectKey || dueMissing
    || requiredCustomMissing || gateCreateMissing
  /** Smart triage after required fields are filled (manual create), not only after AI draft. */
  const triageReady = !createBlocked

  const triageQuery = useDebounced({ title, description, machineIds, scope }, 700)
  const triage = useQuery({
    queryKey: ['triage', projectKey, triageQuery],
    queryFn: () =>
      api.post<TriageResult>('/api/agent/triage', {
        project: projectKey,
        title: triageQuery.title,
        description: triageQuery.description,
        machine_ids: triageQuery.machineIds,
        update_scope: triageQuery.scope,
      }),
    enabled: triageReady,
    staleTime: 60_000,
  })

  const applyDraft = async (d: Draft) => {
    setType(d.type === 'epic' ? 'epic' : d.type)
    setTitle(d.title)
    setDescription(d.description)
    setPriority(d.priority)
    setMachineIds(d.machine_ids)
    setScope(d.update_scope)
    setProjectName(d.project_name)
    setSoftware(d.involves_software)
    setVerifyMethod(d.verify_method)
    setVerifyCriteria(d.verify_criteria)
    setLabels(d.labels)
    setCustomFields(d.custom_fields ?? {})
    if (d.verify_method || d.verify_criteria || d.involves_software !== null) setMore(true)
    if (d.assignee_username) {
      try {
        const res = await api.get<{ user: UserFull }>(`/api/users/${d.assignee_username}`)
        setAssignee(res.user)
      } catch {
        /* suggested user not visible */
      }
    }
    const hints = []
    if (d.unresolved_machines.length) hints.push(t('未识别的机台：{names}', { names: d.unresolved_machines.join(t('、')) }))
    if (d.assignee_reason) hints.push(t('推荐负责人：{reason}', { reason: d.assignee_reason }))
    setAiHint(hints.join(t('；')) || null)
  }

  const runDraft = async () => {
    if (!projectKey || aiText.trim().length < 2) return
    setAiBusy(true)
    setDrafts(null)
    try {
      const res = await api.post<{ drafts: Draft[] }>('/api/agent/draft', { project: projectKey, text: aiText, split })
      if (split) {
        // Split must open the multi-draft review UI — never silently re-fill the single form.
        if (!res.drafts.some((d) => d.children.length > 0)) {
          toast(t('未能拆分为史诗和子任务，请补充实施步骤后再试'), 'error')
          return
        }
        setDrafts(res.drafts)
        toast(t('AI 已拆成史诗和子任务，请确认后创建'), 'success')
        return
      }
      if (res.drafts.length === 1 && res.drafts[0].children.length === 0) {
        await applyDraft(res.drafts[0])
        toast(t('AI 已填好草稿，请检查后创建'), 'success')
      } else if (res.drafts.length === 0) {
        toast(t('AI 没能生成草稿，请补充描述'), 'error')
      } else {
        setDrafts(res.drafts)
      }
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setAiBusy(false)
    }
  }

  const commitDrafts = async () => {
    if (!drafts) return
    const cleaned = drafts
      .map((d) => ({ ...d, title: d.title.trim(), children: d.children.filter((c) => c.title.trim()).map((c) => ({ ...c, title: c.title.trim() })) }))
      .filter((d) => d.title)
    if (!cleaned.length) {
      toast(t('请至少保留一条有标题的草稿'), 'error')
      return
    }
    setBusy(true)
    try {
      const res = await api.post<{ created: { key: string; title: string }[] }>('/api/agent/draft/commit', { project: projectKey, drafts: cleaned })
      toast(t('已创建 {n} 个问题', { n: res.created.length }), 'success')
      invalidate()
      closeCreate()
      if (res.created[0]) openIssue(res.created[0].key)
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const createBlockReason = titleMissing ? t('请填写标题')
    : dueMissing ? t('请填写截止日期')
    : assigneeMissing ? t('请选择负责人')
    : scopeMissing ? t('请选择影响范围')
    : projectNameMissing ? t('请填写项目名称')
    : machinesMissing ? t('请选择机台')
    : requiredCustomMissing ? t('请填写模板必填字段')
    : undefined
  const submit = async () => {
    setSubmitted(true)
    if (createBlocked) {
      requestAnimationFrame(() => {
        formRef.current
          ?.querySelector<HTMLElement>('.create-field.is-missing input, .create-field.is-missing button, .create-field.is-missing textarea')
          ?.focus()
      })
      return
    }
    setBusy(true)
    try {
      const body: Record<string, unknown> = {
        project: projectKey,
        type,
        title: title.trim(),
        description,
        priority,
        assignee_id: assignee?.id ?? null,
        tester_id: tester?.id ?? null,
        machine_ids: machineIds,
        labels,
        due_date: dueDate || null,
        epic_id: epicId,
        parent_id: defaults.parent_id ?? null,
        sprint_id: defaults.sprint_id ?? null,
        custom_fields: customFields,
      }
      if (type === 'requirement') {
        if (showProjectName) body.project_name = projectName
        if (showScope) body.update_scope = scope
        if (showElectricalOptional) {
          Object.assign(body, {
            involves_software: software,
            software_owner_id: software === true ? (softwareOwner?.id ?? null) : null,
            verify_method: verifyMethod,
            verify_criteria: verifyCriteria,
          })
        }
      }
      if (!showMachines) body.machine_ids = []
      const markdownTokens = referencedTokens(
        [body.description, body.verify_method, body.verify_criteria, ...Object.values(customFields)],
        staged,
      )
      const tokens = mergeCreateTokens(markdownTokens, extraFiles)
      const stagedAll: StagedFiles = { ...staged, ...extraFiles }
      let issue: { key: string }
      if (tokens.length) {
        const form = new FormData()
        form.append('payload', JSON.stringify(body))
        form.append('tokens', JSON.stringify(tokens))
        tokens.forEach((token) => form.append('files', stagedAll[token]))
        issue = await api.upload<{ key: string }>('/api/issues/with-attachments', form)
      } else {
        issue = await api.post<{ key: string }>('/api/issues', body)
      }
      toast(t('{key} 已创建', { key: issue.key }), 'success')
      invalidate()
      if (another) {
        setSubmitted(false)
        setTitle('')
        setDescription('')
        setVerifyMethod('')
        setVerifyCriteria('')
        setLabels([])
        setStaged({})
        setExtraFiles({})
        setAiHint(null)
      } else {
        closeCreate()
        openIssue(issue.key)
      }
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const p = project.data
  const tri = triage.data
  const pickSuggested = async () => {
    if (!tri?.assignee) return
    try {
      setAssignee((await api.get<{ user: UserFull }>(`/api/users/${tri.assignee.username}`)).user)
    } catch (err) {
      toast((err as Error).message, 'error')
    }
  }

  return (
    <Modal
      title={t('创建问题')}
      size="wide"
      onClose={requestClose}
      layer={confirmClose ? (
        <div
          className="create-discard"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="create-discard-title"
          onMouseDown={(e) => e.target === e.currentTarget && setConfirmClose(false)}
        >
          <div className="create-discard-card">
            <h4 id="create-discard-title">{t('放弃编辑？')}</h4>
            <p>{t('已填写的内容将不会保存')}</p>
            <div className="create-discard-actions">
              <button type="button" className="btn ghost" onClick={closeCreate}>{t('放弃')}</button>
              <button type="button" className="btn primary" autoFocus onClick={() => setConfirmClose(false)}>
                {t('继续编辑')}
              </button>
            </div>
          </div>
        </div>
      ) : null}
      footer={
        drafts ? (
          <>
            <button className="btn" onClick={() => setDrafts(null)}>
              {t('返回表单')}
            </button>
            <button className="btn primary" disabled={busy} onClick={commitDrafts}>
              {busy
                ? t('创建中…')
                : t('创建 {n} 个问题', {
                    n: drafts.reduce(
                      (n, d) => n + (d.title.trim() ? 1 : 0) + d.children.filter((c) => c.title.trim()).length,
                      0,
                    ),
                  })}
            </button>
          </>
        ) : (
          <>
            <label className="row small" style={{ marginRight: 'auto' }}>
              <input type="checkbox" checked={another} onChange={(e) => setAnother(e.target.checked)} /> {t('继续创建下一个')}
            </label>
            <button className="btn" onClick={requestClose}>
              <FieldIcon name="cancel" size={13} /> {t('取消')}
            </button>
            {submitted && createBlocked && (
              <span id="create-validation-summary" className="small err-text" role="alert">
                {createBlockReason}
              </span>
            )}
            <button
              className="btn primary"
              disabled={busy}
              title={createBlockReason}
              aria-describedby={submitted && createBlocked ? 'create-validation-summary' : undefined}
              onClick={submit}
            >
              {busy ? t('创建中…') : <><FieldIcon name="create" size={13} /> {t('创建')}</>}
            </button>
          </>
        )
      }
    >
      <div className="ai-box" style={{ marginBottom: 16 }}>
        <div className="row" style={{ marginBottom: 8 }}>
          <span className="ai-orb" style={{ width: 18, height: 18 }} />
          <b>{t('用自然语言描述，AI 帮你填写')}</b>
          <span className="spacer" />
          <label className="row small">
            <input type="checkbox" checked={split} onChange={(e) => setSplit(e.target.checked)} /> {t('拆分为史诗和子任务')}
          </label>
        </div>
        <div className="row" style={{ alignItems: 'stretch' }}>
          <textarea
            className="textarea"
            style={{ minHeight: 54 }}
            placeholder={t('例如：CMB-2 的上料机械手在高速模式下偶发定位偏移，需要电气组排查伺服参数，下周三前给出方案')}
            value={aiText}
            onChange={(e) => setAiText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && (e.metaKey || e.ctrlKey) && runDraft()}
          />
          <button
            className={`btn ai ai-draft-btn${aiBusy ? ' busy' : ''}`}
            style={{ height: 'auto' }}
            disabled={aiBusy || aiText.trim().length < 2 || !projectKey}
            onClick={runDraft}
          >
            <span className="ai-draft-label">{t('✦ 生成')}</span>
            {aiBusy && <Spinner />}
          </button>
        </div>
        {aiHint && <div className="tiny muted" style={{ marginTop: 6 }}>{aiHint}</div>}
      </div>

      {drafts ? (
        <div className="col" style={{ gap: 10 }}>
          <div className="small muted">{t('AI 已生成以下草稿，可修改标题或移除不需要的条目：')}</div>
          {drafts.map((d, i) => (
            <div key={i} className="card card-pad">
              <div className="row">
                <TypeIcon type={d.type} />
                <input
                  className="input sm"
                  value={d.title}
                  onChange={(e) => setDrafts(drafts.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))}
                  placeholder={t('标题')}
                />
                <PriorityIcon priority={d.priority} />
                <button className="btn ghost sm icon" onClick={() => setDrafts(drafts.filter((_, j) => j !== i))} title={t('移除')}>
                  ✕
                </button>
              </div>
              {d.description && <div className="small muted" style={{ marginTop: 6, whiteSpace: 'pre-wrap', maxHeight: 90, overflow: 'auto' }}>{d.description}</div>}
              <div style={{ marginTop: 8, paddingLeft: 22 }} className="col" >
                {d.children.map((c, k) => (
                  <div key={k} className="row small">
                    ↳
                    <input
                      className="input sm"
                      value={c.title}
                      placeholder={t('子任务标题')}
                      onChange={(e) =>
                        setDrafts(
                          drafts.map((x, j) =>
                            j === i ? { ...x, children: x.children.map((cc, kk) => (kk === k ? { ...cc, title: e.target.value } : cc)) } : x,
                          ),
                        )
                      }
                    />
                    <button
                      className="btn ghost sm icon"
                      title={t('移除')}
                      onClick={() => setDrafts(drafts.map((x, j) => (j === i ? { ...x, children: x.children.filter((_, kk) => kk !== k) } : x)))}
                    >
                      ✕
                    </button>
                  </div>
                ))}
                <button
                  className="btn ghost sm"
                  style={{ alignSelf: 'flex-start' }}
                  onClick={() =>
                    setDrafts(
                      drafts.map((x, j) =>
                        j === i
                          ? {
                              ...x,
                              children: [...x.children, { type: 'task', title: '', description: '', priority: x.priority }],
                            }
                          : x,
                      ),
                    )
                  }
                >
                  {t('＋ 添加子项')}
                </button>
              </div>
            </div>
          ))}
          <button
            className="btn ghost sm"
            style={{ alignSelf: 'flex-start' }}
            onClick={() =>
              setDrafts([
                ...drafts,
                {
                  type: 'task',
                  title: '',
                  description: '',
                  priority: 'medium',
                  machine_ids: [],
                  unresolved_machines: [],
                  update_scope: [],
                  project_name: '',
                  involves_software: null,
                  verify_method: '',
                  verify_criteria: '',
                  labels: [],
                  children: [],
                },
              ])
            }
          >
            {t('＋ 添加条目')}
          </button>
        </div>
      ) : (
        <div className="create-grid" ref={formRef}>
          <div className="col" style={{ gap: 12 }}>
            <div className="grid grid-2" style={{ gap: 12 }}>
              <div className="field create-field">
                <label><FieldIcon name="project" />{pick({ zh: '项目', en: 'Project' })}</label>
                <Select
                  className="select"
                  value={projectKey}
                  onChange={(e) => setProjectKey(e.target.value)}
                  options={(projects.data ?? []).map((proj) => ({
                    value: proj.key,
                    label: (
                      <>
                        <ProjectBadge icon={proj.icon} color={proj.color} label={proj.key.slice(0, 2)} size={16} className="project-dot" />
                        <span className="ellipsis">{proj.key} · {proj.name}</span>
                      </>
                    ),
                  }))}
                />
              </div>
              <div className="field create-field">
                <label><FieldIcon name="type" />{t('类型')}</label>
                <Select
                  className="select"
                  value={type}
                  onChange={(e) => setType(e.target.value as IssueType)}
                  disabled={!!defaults.parent_id}
                  options={ISSUE_TYPES.filter((tp) => (defaults.parent_id ? tp === 'subtask' : tp !== 'subtask')).map((tp) => ({
                    value: tp,
                    label: <><TypeIcon type={tp} size={16} />{typeName(tp)}</>,
                  }))}
                />
              </div>
            </div>
            <div className={createFieldClass({ required: true, missing: submitted && titleMissing })}>
              <label>
                <FieldIcon name="title" />{t('标题')}
                <CreateReq />
              </label>
              <input
                className="input"
                autoFocus
                required
                aria-invalid={submitted && titleMissing}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={t('一句话说明需求或问题')}
              />
            </div>
            <div className="field create-field">
              <label><FieldIcon name="description" />{type === 'requirement' ? t('需求说明') : t('描述')}</label>
              <StagedMarkdownField value={description} onChange={setDescription} staged={staged} onStage={setStaged}
                minHeight={110} placeholder={t('添加描述，或输入 / 插入格式与操作')} />
            </div>
            {type === 'requirement' && (showProjectName || showMachines) && (
              <div className="grid grid-2" style={{ gap: 12 }}>
                {showProjectName && (
                  <div className={createFieldClass({
                    required: projectNameRequired,
                    missing: submitted && projectNameMissing,
                  })}>
                    <label>
                      <FieldIcon name="project" />{t('项目名称')}
                      {projectNameRequired && <CreateReq />}
                    </label>
                    <input className="input" value={projectName}
                      onChange={(e) => setProjectName(e.target.value)}
                      placeholder={projectNameRequired ? t('所属业务项目') : t('所属业务项目（可选）')} />
                  </div>
                )}
                {showMachines && (
                  <div className={createFieldClass({
                    required: machinesRequired,
                    missing: submitted && machinesMissing,
                  })}>
                    <label>
                      <FieldIcon name="machines" />{t('机台')}
                      {machinesRequired && <CreateReq />}
                    </label>
                    <MachinePicker variant="field" groups={p?.machine_groups ?? []} value={machineIds} onChange={setMachineIds} />
                  </div>
                )}
              </div>
            )}
            {showScope && (
              <div className={createFieldClass({
                required: scopeRequired,
                missing: submitted && scopeMissing,
              })}>
                <label>
                  <FieldIcon name="scope" />{t('影响范围')}
                  <CreateReq />
                </label>
                <ChipSelect options={p?.project.settings.update_scope_options ?? []} value={scope} onChange={setScope} />
              </div>
            )}
            {showElectricalOptional && (
              <>
                <button className="btn ghost sm" style={{ alignSelf: 'flex-start' }} onClick={() => setMore((m) => !m)}>
                  {more ? '▾' : '▸'} {t('电气组字段（可选，通常由电气组评估后填写）')}
                </button>
                <Fold open={more} gap={12}>
                  <div className="col" style={{ gap: 12 }}>
                    {profileHasField(p, 'involves_software') && (
                      <div className="field">
                        <label>{t('是否涉及软件')}</label>
                        <TriState value={software} onChange={setSoftware} />
                      </div>
                    )}
                    {profileHasField(p, 'software_owner_id') && (
                      <Fold open={software === true} gap={12}>
                        <div className="field">
                          <label>{t('软件负责人')}</label>
                          <UserPicker value={softwareOwner} softwareOf={projectKey || undefined} onChange={setSoftwareOwner} />
                        </div>
                      </Fold>
                    )}
                    {(profileHasField(p, 'verify_method') || profileHasField(p, 'verify_criteria')) && (
                      <div className="grid grid-2" style={{ gap: 12 }}>
                        {profileHasField(p, 'verify_method') && (
                          <div className="field">
                            <label>{t('验证方法')}</label>
                            <StagedMarkdownField value={verifyMethod} onChange={setVerifyMethod} staged={staged} onStage={setStaged} />
                          </div>
                        )}
                        {profileHasField(p, 'verify_criteria') && (
                          <div className="field">
                            <label>{t('验证标准')}</label>
                            <StagedMarkdownField value={verifyCriteria} onChange={setVerifyCriteria} staged={staged} onStage={setStaged} />
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </Fold>
              </>
            )}
            {type !== 'requirement' && showMachines && (
              <div className={createFieldClass({
                required: machinesRequired,
                missing: submitted && machinesMissing,
              })}>
                <label>
                  <FieldIcon name="machines" />{t('机台')}
                  {machinesRequired && <CreateReq />}
                </label>
                <MachinePicker variant="field" groups={p?.machine_groups ?? []} value={machineIds} onChange={setMachineIds} />
              </div>
            )}
            {(p?.profile.custom_fields.length ?? 0) > 0 && (
              <div className="col profile-fields" style={{ gap: 12 }}>
                {p!.profile.custom_fields.filter((field) => {
                  const section = p!.profile.sections.find((item) => item.key === field.section)
                  return !section?.issue_types?.length || section.issue_types.includes(type)
                }).map((field) => {
                  const required = field.required || gateRequiresCustom(p, field.key, type)
                  const value = customFields[field.key]
                  const missing = required && (value === null || value === undefined || value === ''
                    || (Array.isArray(value) && value.length === 0))
                  return (
                  <div
                    className={createFieldClass({ required, missing: submitted && missing })}
                    key={field.key}
                  >
                    <label>
                      {customFieldLabel(field)}
                      {required && <CreateReq />}
                    </label>
                    {field.kind === 'markdown' ? (
                      <StagedMarkdownField value={String(customFields[field.key] ?? '')} staged={staged} onStage={setStaged}
                        onChange={(value) => setCustomFields((current) => ({ ...current, [field.key]: value }))} />
                    ) : field.kind === 'textarea' ? (
                      <textarea className="textarea" value={String(customFields[field.key] ?? '')}
                        onChange={(e) => setCustomFields({ ...customFields, [field.key]: e.target.value })} />
                    ) : field.kind === 'select' ? (
                      <Select className="select" value={String(customFields[field.key] ?? '')}
                        onChange={(e) => setCustomFields({ ...customFields, [field.key]: e.target.value })}>
                        <option value="">—</option>
                        {field.options.map((option) => <option key={option}>{option}</option>)}
                      </Select>
                    ) : field.kind === 'boolean' ? (
                      <TriState value={customFields[field.key] as boolean | null | undefined ?? null}
                        onChange={(value) => setCustomFields({ ...customFields, [field.key]: value })} />
                    ) : field.kind === 'master_ref' && field.master_kind ? (
                      <AssetPicker kind={field.master_kind} multiple={field.multiple}
                        value={(customFields[field.key] as number | number[] | null) ?? (field.multiple ? [] : null)}
                        onChange={(value) => setCustomFields({ ...customFields, [field.key]: value })} />
                    ) : field.kind === 'user' ? (
                      <UserPicker value={customUsers[field.key] ?? null} project={projectKey || undefined} field="member" onChange={(value) => {
                        setCustomUsers({ ...customUsers, [field.key]: value })
                        setCustomFields({ ...customFields, [field.key]: value?.id ?? null })
                      }} />
                    ) : field.kind === 'machine' ? (
                      <MachinePicker variant="field" groups={p?.machine_groups ?? []}
                        value={(customFields[field.key] as number[] | undefined) ?? []}
                        onChange={(value) => setCustomFields({ ...customFields, [field.key]: value })} />
                    ) : field.kind === 'date' ? (
                      <DateField
                        value={String(customFields[field.key] ?? '')}
                        onChange={(value) => setCustomFields({ ...customFields, [field.key]: value })}
                      />
                    ) : (
                      <input
                        className={`input${field.kind === 'number' ? ' w-num' : ''}`}
                        type={field.kind === 'number' ? 'number' : 'text'}
                        value={String(customFields[field.key] ?? '')}
                        onChange={(e) => setCustomFields({ ...customFields, [field.key]: field.kind === 'number' && e.target.value ? Number(e.target.value) : e.target.value })}
                      />
                    )}
                  </div>
                  )
                })}
              </div>
            )}
            <div className="field create-field">
              <label><FieldIcon name="attachment" />{t('附件')}</label>
              <StagedAttachmentsField files={extraFiles} onChange={setExtraFiles} />
            </div>
          </div>

          <div className="col" style={{ gap: 12 }}>
            <div className={createFieldClass({
              required: assigneeRequired, missing: submitted && assigneeMissing,
            })}>
              <label>
                <FieldIcon name="assignee" />{t('负责人')}
                {assigneeRequired && <CreateReq />}
              </label>
              <UserPicker value={assignee} project={projectKey || undefined} field="assignee_id" onChange={setAssignee} />
            </div>
            {showTester && (
              <div className="field">
                <label><FieldIcon name="tester" />{t('测试人')}</label>
                <UserPicker value={tester} project={projectKey || undefined} field="tester_id" onChange={setTester} />
              </div>
            )}
            <div className="field">
              <label><FieldIcon name="priority" />{t('优先级')}</label>
              <Select
                className="select"
                value={priority}
                onChange={(e) => setPriority(e.target.value as Priority)}
                options={PRIORITIES.map((pr) => ({
                  value: pr,
                  label: <><PriorityIcon priority={pr} />{priorityName(pr)}</>,
                }))}
              />
            </div>
            {type !== 'epic' && (
              <div className="field">
                <label>
                  <FieldIcon name="epic" />
                  {t('史诗')}
                  <InfoTip topic="epic" />
                </label>
                <Select
                  className="select"
                  value={epicId ?? ''}
                  onChange={(e) => setEpicId(e.target.value ? Number(e.target.value) : null)}
                  options={[
                    { value: '', label: t('无') },
                    ...(p?.epics ?? []).map((e) => ({
                      value: String(e.id),
                      label: <><TypeIcon type="epic" size={14} /><span className="ellipsis">{e.key} {e.title}</span></>,
                    })),
                  ]}
                />
              </div>
            )}
            <div className="field">
              <label>
                <FieldIcon name="labels" />
                {t('标签')}
                <InfoTip topic="labels" />
              </label>
              <LabelPicker
                options={p?.labels ?? p?.project.settings.label_options ?? []}
                value={labels}
                onChange={setLabels}
                commitDelay={0}
              />
            </div>
            <div className={createFieldClass({ required: dueRequired, missing: submitted && dueMissing })}>
              <label>
                <FieldIcon name="due" />{t('截止日期')}
                {dueRequired && <CreateReq />}
              </label>
              <DateField required={dueRequired} value={dueDate} onChange={setDueDate} />
              {!dueRequired && !dueDate && <span className="tiny faint">{t('留空则沿用父任务的截止日期、冲刺和机台')}</span>}
            </div>
            <div className="tiny faint row" style={{ gap: 6 }}>
              <FieldIcon name="reporter" size={12} />
              <Avatar user={user} size={16} />
              {t('报告人：{name}', { name: user.display_name })}
            </div>

            {triageReady && (
              <div className="triage-box">
                <div className="row small bold" style={{ marginBottom: 6 }}>
                  ✦ {t('智能分派')} {triage.isFetching && <Spinner />}
                  <span className="spacer" />
                  {tri && <span className="tiny faint">{tri.source === 'llm' ? 'AI' : t('规则')}</span>}
                </div>
                {tri?.duplicates.length ? (
                  <div className="col" style={{ gap: 4 }}>
                    <div className="tiny muted">{t('可能重复 / 相似：')}</div>
                    {tri.duplicates.slice(0, 4).map((d) => (
                      <a key={d.key} className="small ellipsis" style={{ cursor: 'pointer', color: d.relation === 'duplicate' ? 'var(--danger)' : undefined }} onClick={() => window.open(`/issues/${d.key}`, '_blank')} title={d.reason}>
                        {d.relation === 'duplicate' ? '⚠ ' : ''}
                        {d.key} {d.title} <span className="faint">· {d.status && t(d.status)}</span>
                      </a>
                    ))}
                  </div>
                ) : (
                  tri && <div className="tiny muted">{t('未发现相似问题')}</div>
                )}
                {tri?.assignee && assignee?.username !== tri.assignee.username && (
                  <div className="row small" style={{ marginTop: 8 }}>
                    <span className="ellipsis" style={{ flex: 1 }} title={tri.assignee.reason}>
                      {t('推荐负责人：')}
                      <b>{tri.assignee.name}</b>
                    </span>
                    <button className="btn sm" onClick={pickSuggested}>
                      {t('采用')}
                    </button>
                  </div>
                )}
                {tri?.priority && tri.priority !== priority && (
                  <div className="row small" style={{ marginTop: 6 }}>
                    <span className="ellipsis" style={{ flex: 1 }} title={tri.priority_reason ?? ''}>
                      {t('建议优先级：')}
                      <PriorityIcon priority={tri.priority} /> {priorityName(tri.priority)}
                    </span>
                    <button className="btn sm" onClick={() => setPriority(tri.priority!)}>
                      {t('采用')}
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </Modal>
  )
}
