import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, api } from '../../api'
import { Modal, Spinner } from '../../components/ui'
import { useApp } from '../../context'
import { t } from '../../i18n'
import type { DepartmentTemplate, DepartmentTemplateVersion } from '../../types'
import { type Def, STEPS, STEP_META, type Step, lockedKeys, validate } from './templateModel'
import { FormStep, GatesStep, LayoutStep, OverviewStep, PublishStep, RolesStep, StatusesStep, WorkflowStep } from './TemplateSteps'

type SaveState = 'saved' | 'pending' | 'saving' | 'error' | 'conflict'

const draftUrl = (templateId: number, versionId: number) => `/api/admin/templates/${templateId}/versions/${versionId}`

/**
 * Edits the template's single draft. Every change autosaves with the draft's revision; a stale
 * revision (another admin or another window saved first) stops autosave and asks what to keep
 * instead of retrying.
 */
export function TemplateEditor({ templateId, onClose }: { templateId: number; onClose: () => void }) {
  const { toast } = useApp()
  const client = useQueryClient()
  const detail = useQuery({
    queryKey: ['admin-template', templateId],
    queryFn: () => api.get<DepartmentTemplate>(`/api/admin/templates/${templateId}`),
    refetchOnMount: 'always',
  })
  const [step, setStep] = useState<Step>('overview')
  const [def, setDef] = useState<Def | null>(null)
  const [draft, setDraft] = useState<DepartmentTemplateVersion | null>(null)
  const [state, setStateRaw] = useState<SaveState>('saved')
  const [saveError, setSaveError] = useState('')
  const [savedAt, setSavedAt] = useState<Date | null>(null)
  const [loadError, setLoadError] = useState('')
  const [tick, setTick] = useState(0)
  const stateRef = useRef<SaveState>('saved')
  const latest = useRef<Def | null>(null)
  const draftId = useRef(0)
  const revision = useRef(0)
  const editSeq = useRef(0)
  const savedSeq = useRef(0)
  const inflight = useRef<Promise<boolean> | null>(null)

  const setState = (next: SaveState) => {
    stateRef.current = next
    setStateRaw(next)
  }

  const adopt = (version: DepartmentTemplateVersion, keepEdits = false) => {
    setDraft(version)
    draftId.current = version.id
    revision.current = version.revision
    if (!keepEdits) {
      latest.current = version.definition
      setDef(version.definition)
      editSeq.current = savedSeq.current = 0
    }
  }

  /** Fetch the live draft (creating one from the published version if none exists). */
  const fetchDraft = useCallback(
    () => api.post<DepartmentTemplateVersion>(`/api/admin/templates/${templateId}/versions`, {}),
    [templateId],
  )

  useEffect(() => {
    let alive = true
    fetchDraft()
      .then((version) => {
        if (!alive) return
        adopt(version)
        setState('saved')
        detail.refetch()
      })
      .catch((error) => alive && setLoadError((error as Error).message))
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchDraft])

  const flush = useCallback(async (): Promise<boolean> => {
    for (;;) {
      if (inflight.current) await inflight.current
      if (stateRef.current === 'conflict') return false
      if (savedSeq.current === editSeq.current) return true
      const seq = editSeq.current
      setState('saving')
      const attempt = api.put<DepartmentTemplateVersion>(draftUrl(templateId, draftId.current), {
        revision: revision.current, definition: latest.current,
      }).then((next) => {
        revision.current = next.revision
        savedSeq.current = seq
        setDraft(next)
        setSavedAt(new Date())
        setState(editSeq.current === seq ? 'saved' : 'pending')
        return true
      }).catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 409) {
          setSaveError(error.message)
          setState('conflict')
        } else {
          setSaveError((error as Error).message)
          setState('error')
        }
        return false
      }).finally(() => { inflight.current = null })
      inflight.current = attempt
      if (!(await attempt)) return false
    }
  }, [templateId])

  useEffect(() => {
    if (state !== 'pending') return
    const timer = window.setTimeout(() => { void flush() }, 800)
    return () => window.clearTimeout(timer)
  }, [tick, state, flush])

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (savedSeq.current !== editSeq.current) event.preventDefault()
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [])

  const update = useCallback((fn: (current: Def) => Def) => {
    if (stateRef.current === 'conflict' || !latest.current) return
    const next = fn(latest.current)
    if (next === latest.current) return
    latest.current = next
    setDef(next)
    editSeq.current += 1
    setTick((n) => n + 1)
    if (stateRef.current !== 'saving') setState('pending')
  }, [])

  const reloadLatest = async () => {
    try {
      adopt(await fetchDraft())
      setState('saved')
      detail.refetch()
      toast(t('已载入最新草稿'), 'success')
    } catch (error) { toast((error as Error).message, 'error') }
  }

  const overwrite = async () => {
    try {
      adopt(await fetchDraft(), true)
      setState('pending')
      await flush()
    } catch (error) { toast((error as Error).message, 'error') }
  }

  const close = async () => {
    if (savedSeq.current !== editSeq.current && !(await flush())
      && !window.confirm(t('还有修改没有保存成功，确定放弃并关闭吗？'))) return
    onClose()
  }

  const issues = useMemo(() => (def ? validate(def) : []), [def])
  const template = detail.data
  const published = template?.current_version?.definition ?? null
  const locked = useMemo(() => lockedKeys(published), [published])

  if (loadError) {
    return <Modal title={t('模板编辑器')} onClose={onClose}><div className="callout danger">{loadError}</div></Modal>
  }
  if (!def || !draft || !template) {
    return <Modal title={t('模板编辑器')} onClose={onClose}><Spinner center /></Modal>
  }

  const index = STEPS.indexOf(step)
  const counts = Object.fromEntries(STEPS.map((s) => [s, issues.filter((i) => i.step === s).length])) as Record<Step, number>
  const publish = async () => {
    if (!(await flush())) return
    if (!window.confirm(t('发布 v{n}？发布后新建项目将使用此版本；已有项目保持原版本，需要时在项目设置中迁移。', { n: draft.version }))) return
    try {
      await api.post(`${draftUrl(templateId, draft.id)}/publish`)
      toast(t('已发布 v{n}', { n: draft.version }), 'success')
      await client.invalidateQueries({ queryKey: ['project-profile-catalog'] })
      onClose()
    } catch (error) { toast((error as Error).message, 'error') }
  }
  const discard = async () => {
    if (!window.confirm(t('放弃 v{n} 草稿的全部修改？之后可以从当前发布版本重新开始编辑。', { n: draft.version }))) return
    try {
      await api.del(draftUrl(templateId, draft.id))
      toast(t('草稿已放弃'), 'success')
      onClose()
    } catch (error) { toast((error as Error).message, 'error') }
  }

  const props = { def, update, locked, readOnly: state === 'conflict' }
  return (
    <Modal
      size="full"
      onClose={close}
      title={(
        <span className="row" style={{ gap: 8 }}>
          {def.labels.zh || template.label}
          <span className="badge accent">{t('v{n} 草稿', { n: draft.version })}</span>
          {template.current_version && <span className="small muted">{t('当前发布 v{n}', { n: template.current_version.version })}</span>}
        </span>
      )}
      footer={(
        <>
          <SaveStatus state={state} savedAt={savedAt} error={saveError} onRetry={() => { setState('pending') }} />
          <span className="spacer" />
          {index > 0 && <button className="btn" onClick={() => setStep(STEPS[index - 1])}>{t('上一步')}</button>}
          {step !== 'publish'
            ? <button className="btn primary" onClick={() => setStep(STEPS[index + 1])}>{t('下一步：{step}', { step: t(STEP_META[STEPS[index + 1]].label) })}</button>
            : <button className="btn primary" disabled={issues.length > 0 || state === 'conflict'} onClick={publish}>{t('发布 v{n}', { n: draft.version })}</button>}
        </>
      )}
    >
      <div className="tpl-editor">
        <nav className="tpl-nav" aria-label={t('编辑步骤')}>
          {STEPS.map((s, i) => (
            <button key={s} type="button" className={`tpl-nav-item ${s === step ? 'active' : ''}`} onClick={() => setStep(s)}>
              <span className="tpl-nav-index">{i + 1}</span>
              <span className="tpl-nav-text">
                <b>{t(STEP_META[s].label)}</b>
                <span className="tiny muted">{t(STEP_META[s].hint)}</span>
              </span>
              {s === 'publish'
                ? issues.length > 0 && <span className="tpl-nav-badge">{issues.length}</span>
                : counts[s] > 0 && <span className="tpl-nav-badge">{counts[s]}</span>}
            </button>
          ))}
        </nav>
        <div className="tpl-main">
          {state === 'conflict' && (
            <div className="callout danger tpl-conflict">
              <b>{t('这份草稿已在别处被修改，自动保存已暂停')}</b>
              <span className="small">{t('可能是另一位管理员或你在另一个窗口中保存过。请选择保留哪一份：')}</span>
              <div className="row wrap" style={{ gap: 8 }}>
                <button className="btn sm" onClick={reloadLatest}>{t('载入最新草稿（放弃本窗口的修改）')}</button>
                <button className="btn sm danger" onClick={overwrite}>{t('用本窗口的内容覆盖')}</button>
              </div>
            </div>
          )}
          <header className="tpl-step-head">
            <h3>{t(STEP_META[step].label)}</h3>
            <p className="small muted">{t(STEP_META[step].hint)}</p>
          </header>
          {step === 'overview' && <OverviewStep {...props} template={template} draftVersion={draft.version}
            onSavedMeta={() => detail.refetch()} goto={setStep} issues={issues} />}
          {step === 'roles' && <RolesStep {...props} />}
          {step === 'form' && <FormStep {...props} />}
          {step === 'statuses' && <StatusesStep {...props} />}
          {step === 'workflow' && <WorkflowStep {...props} />}
          {step === 'gates' && <GatesStep {...props} />}
          {step === 'layout' && <LayoutStep {...props} />}
          {step === 'publish' && <PublishStep {...props} template={template} draft={draft} issues={issues}
            goto={setStep} onPublish={publish} onDiscard={template.current_version ? discard : undefined} />}
        </div>
      </div>
    </Modal>
  )
}

function SaveStatus({ state, savedAt, error, onRetry }: { state: SaveState; savedAt: Date | null; error: string; onRetry: () => void }) {
  if (state === 'saving') return <span className="save-status"><Spinner /> {t('正在保存…')}</span>
  if (state === 'pending') return <span className="save-status muted">{t('有修改，即将自动保存')}</span>
  if (state === 'conflict') return <span className="save-status err-text">{t('自动保存已暂停：草稿已在别处被修改')}</span>
  if (state === 'error') {
    return (
      <span className="save-status err-text">
        {t('保存失败：{error}', { error })}
        <button className="btn ghost sm" onClick={onRetry}>{t('重试')}</button>
      </span>
    )
  }
  return <span className="save-status ok-text">✓ {savedAt ? t('已保存 {time}', { time: savedAt.toLocaleTimeString() }) : t('草稿已是最新')}</span>
}
