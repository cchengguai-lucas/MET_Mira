import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState, type CSSProperties, type FocusEvent, type KeyboardEvent, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiError, streamPost } from '../api'
import { useApp } from '../context'
import { t, tServer } from '../i18n'
import { customFieldLabel, fmtDate, fmtDateTime, fmtSize, ISSUE_TYPES, PRIORITIES, priorityName, SECTION_META, sectionName, SLA_STATE_LABELS, slaHelp, timeAgo, typeName } from '../labels'
import { useProjectLiveSync } from '../liveSync'
import { queryKeys, useInvalidateIssues, useIssue, usePatchIssue, useProject } from '../queries'
import { copyText } from '../share'
import { checkboxTarget, lastSubtaskMessage, withSubtaskClose } from '../subtasks'
import type { Activity, Comment, Issue, IssueDetail, IssueRef, ProjectDetail, Section, Status, UserBrief } from '../types'
import { Markdown } from './Markdown'
import { insertMention, MentionChips, MentionTextarea, type MentionPerson } from './MentionInput'
import { WorkflowFlow } from './WorkflowFlow'
import { canRequestScheme, canSubmitScheme, canUnlockScheme, isClosed, isCompleted, REOPENED, schemeSubmitBlock, schemeSubmitted, schemeVotesDone, typeFlowStatuses, typeRules } from '../workflow'
import { ReopenReasonModal } from './ReopenReasonModal'
import { AssetPicker, ChipSelect, LabelPicker, MachinePicker, TriState, UserPicker } from './Pickers'
import { useAttachmentPaste } from './attachPaste'
import { MarkdownImageThumbs } from './MarkdownImageThumbs'
import { mdImageMarkerBlock, mergeVisibleAndMarkers, removeMdImage, stripMdImages } from './markdownImages'
import { IssueAiMenu } from './IssueAiMenu'
import { ProjectBadge } from '../projectIcons'
import { PersonHover } from './PersonHover'
import { ActivityText, linkLabel } from './ActivityText'
import { DateField } from './DateField'
import { Avatar, ConfirmButton, Dropdown, FieldIcon, Fold, Select, InfoTip, Lightbox, PriorityIcon, Spinner, StatusPill, Tabs, TypeIcon, UserChip, useDialogFocus, useEscape } from './ui'
import { SchemeSign } from './SchemeSign'
import { leaveAnimation } from './leave'

/** Overlay for `?issue=` — centered modal by default, or left/right drawer from preference. */
export function IssueOverlay({ issueKey }: { issueKey: string }) {
  const { closeIssue, issueChrome, toast } = useApp()
  useEscape(closeIssue)
  const dialogRef = useRef<HTMLDivElement>(null)
  useDialogFocus(dialogRef)
  const attachDialog = useCallback((node: HTMLDivElement | null) => {
    dialogRef.current = node
    return leaveAnimation(node)
  }, [])
  const isModal = issueChrome === 'modal'
  const drawerSide = issueChrome === 'drawer-left' ? 'left' : 'right'
  const header = (
    <div className={isModal ? 'issue-modal-header' : 'drawer-header'}>
      <span className="spacer" />
      <button
        className="btn ghost sm"
        onClick={async () => {
          const ok = await copyText(`${window.location.origin}/issues/${issueKey}`)
          toast(ok ? t('链接已复制') : t('复制失败，请手动复制地址栏中的链接'), ok ? 'success' : 'error')
        }}
      >
        {t('复制链接')}
      </button>
      <Link to={`/issues/${issueKey}`} className="btn ghost sm" title={t('在新页面打开')}>
        {t('新页面')} ↗
      </Link>
      <button className="btn ghost icon sm" onClick={closeIssue} aria-label={t('关闭')}>
        ✕
      </button>
    </div>
  )
  if (isModal) {
    return (
      <div className="issue-modal-overlay" ref={leaveAnimation} onClick={closeIssue}>
        <div
          className="issue-modal"
          ref={attachDialog}
          onClick={(e) => e.stopPropagation()}
          role="dialog"
          aria-modal="true"
          aria-label={t('问题详情')}
          tabIndex={-1}
        >
          {header}
          <div className="issue-modal-body">
            <IssueView issueKey={issueKey} />
          </div>
        </div>
      </div>
    )
  }
  return (
    <>
      <div className="drawer-overlay" ref={leaveAnimation} onClick={closeIssue} />
      <div
        className={`drawer ${drawerSide}`}
        ref={attachDialog}
        role="dialog"
        aria-modal="true"
        aria-label={t('问题详情')}
        tabIndex={-1}
      >
        {header}
        <div className="drawer-body">
          <IssueView issueKey={issueKey} />
        </div>
      </div>
    </>
  )
}
/** @deprecated Use IssueOverlay */
export const IssueDrawer = IssueOverlay

function InlineText({
  value,
  onSave,
  disabled,
  multiline,
  placeholder,
  type = 'text',
}: {
  value: string | null | undefined
  onSave: (v: string) => void
  disabled?: boolean
  multiline?: boolean
  placeholder?: string
  type?: 'text' | 'date' | 'number'
}) {
  const emptyLabel = placeholder ?? (type === 'number' ? '—' : t('点击填写'))
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value ?? '')
  useEffect(() => setDraft(value ?? ''), [value])
  // Unmounting a focused input fires blur, which must not save after Esc or save twice after Enter.
  const closed = useRef(false)
  const commit = () => {
    if (closed.current) return
    closed.current = true
    setEditing(false)
    if ((value ?? '') !== draft) onSave(draft)
  }
  const cancel = () => {
    closed.current = true
    setDraft(value ?? '')
    setEditing(false)
  }
  if (type === 'date') {
    if (disabled) {
      return (
        <div className="inline-edit readonly" style={{ whiteSpace: 'nowrap' }}>
          {value ? <span>{value.slice(0, 10)}</span> : <span className="placeholder">—</span>}
        </div>
      )
    }
    return (
      <DateField
        size="sm"
        value={(value ?? '').slice(0, 10)}
        onChange={(next) => {
          if ((value ?? '') !== next) onSave(next)
        }}
      />
    )
  }
  if (editing && !disabled) {
    return multiline ? (
      <textarea
        className="textarea"
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Escape') cancel()
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) commit()
        }}
        style={{ minHeight: 70 }}
      />
    ) : (
      <input
        className={`input sm${type === 'number' ? ' w-num' : ''}`}
        type={type}
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit()
          if (e.key === 'Escape') cancel()
        }}
      />
    )
  }
  const startEditing = () => {
    if (disabled) return
    closed.current = false
    setEditing(true)
  }
  return (
    <div
      className={`inline-edit${type === 'number' ? ' w-num' : ''}${disabled ? ' readonly' : ''}`}
      onClick={startEditing}
      role={disabled ? undefined : 'button'}
      tabIndex={disabled ? undefined : 0}
      aria-label={disabled ? undefined : t('点击填写')}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          startEditing()
        }
      }}
      style={{ whiteSpace: multiline && value ? 'pre-wrap' : 'nowrap', maxWidth: '100%' }}
    >
      {multiline && value ? <span className="markdown" hidden /> : null}
      {value ? <span>{value}</span> : <span className="placeholder">{disabled ? '—' : emptyLabel}</span>}
    </div>
  )
}
function SectionCard({ section, editable, children, label, extra }: { section: string; editable: boolean; children: ReactNode; label?: string; extra?: ReactNode }) {
  const accent = SECTION_META[section]?.color ?? 'var(--sec-default)'
  return (
    <div className="section-card" style={{ '--section-accent': accent } as CSSProperties}>
      <div className="section-head">
        {label ?? sectionName(section)}
        {extra}
        <span className="lock">{editable ? t('可编辑') : t('🔒 只读')}</span>
      </div>
      <div className="section-body">{children}</div>
    </div>
  )
}
function gateFieldSet(fields: string[]): Set<string> {
  const set = new Set<string>()
  for (const field of fields) {
    set.add(field)
    if (field.startsWith('custom_fields.')) set.add(field.slice('custom_fields.'.length))
    else set.add(`custom_fields.${field}`)
  }
  return set
}

function gateMisses(set: Set<string>, key: string): boolean {
  return set.has(key) || set.has(`custom_fields.${key}`)
}

function GateField({
  fieldKey,
  label,
  miss,
  top,
  required,
  children,
}: {
  fieldKey: string
  label: ReactNode
  miss: boolean
  top?: boolean
  required?: boolean
  children: ReactNode
}) {
  return (
    <>
      <span className={`label${top ? ' top' : ''}${miss ? ' gate-miss' : ''}`} data-field={fieldKey}>
        {label}
        {miss || required ? <span className="gate-miss-mark" aria-hidden>*</span> : null}
      </span>
      <div className={`fv${miss ? ' gate-miss-value' : ''}`} data-field={fieldKey}>
        {children}
      </div>
    </>
  )
}
function JiraPerson({ name, user }: { name?: string | null; user?: UserBrief | null }) {
  if (user) return <UserChip user={user} size={20} />
  const label = (name || '').trim()
  if (!label) return <span className="faint">—</span>
  return (
    <span className="row user-chip" title={label}>
      <span
        className="avatar"
        style={{ width: 20, height: 20, background: 'var(--surface-3)', color: 'var(--text-3)', fontSize: 10 }}
      >
        ?
      </span>
      <span className="ellipsis">{label}</span>
    </span>
  )
}

function SoftwareSnapshot({ issue, disabled }: { issue: IssueDetail; disabled?: boolean }) {
  const client = useQueryClient()
  const { toast } = useApp()
  const [busy, setBusy] = useState(false)
  const snap = issue.software_jira_snapshot
  const people = issue.software_jira_people
  const connected = Boolean(snap?.assignee || snap?.tester || snap?.status || snap?.due_date)
  const readyStatus = (snap?.status || '').trim() === '待上机验证'
  const miraDue = (issue.due_date || '').slice(0, 10)
  const softwareDue = (snap?.due_date || '').slice(0, 10)
  const dueConflict = Boolean(miraDue && softwareDue && softwareDue > miraDue)
  const refresh = () => {
    setBusy(true)
    void api.post<IssueDetail>(`/api/issues/${issue.key}/software-jira/refresh`)
      .then((data) => client.setQueryData(queryKeys.issue(issue.key), data))
      .catch((err: Error) => toast(tServer(err.message), 'error'))
      .finally(() => setBusy(false))
  }
  const row = (label: string, control: ReactNode) => (
    <div className="row" style={{ gap: 8, minWidth: 0, minHeight: 28, alignItems: 'center', flexWrap: 'nowrap' }}>
      <span className="tiny muted" style={{ whiteSpace: 'nowrap', flexShrink: 0 }}>{t(label)}</span>
      <span style={{ minWidth: 0 }}>{control}</span>
    </div>
  )
  return (
    <>
      <span className="label top">{t('软件 Jira')}</span>
      <div style={{ display: 'grid', gap: 6 }}>
        {snap?.error ? <div className="tiny err-text">{t(snap.error)}</div> : null}
        {connected ? (
          <>
            {row('软件经办人', <JiraPerson name={snap?.assignee} user={people?.assignee} />)}
            {row('测试人', <JiraPerson name={snap?.tester} user={people?.tester} />)}
            {row('当前状态', snap?.status ? (
              <span
                className="pill"
                style={{
                  background: readyStatus ? '#34c75922' : 'var(--surface-3)',
                  color: readyStatus ? '#248a3d' : 'var(--text-2)',
                }}
              >
                <span style={{ width: 6, height: 6, borderRadius: 3, background: 'currentColor', flexShrink: 0 }} />
                <span className="ellipsis">{t(snap.status)}</span>
              </span>
            ) : <span className="faint">—</span>)}
            {row('截止日期', snap?.due_date ? (
              <span
                className="pill"
                title={dueConflict ? t('晚于问题最终日期 {date}', { date: fmtDate(issue.due_date) }) : undefined}
                style={{
                  background: dueConflict ? '#ff9f0a22' : 'var(--surface-3)',
                  color: dueConflict ? '#b06000' : 'var(--text-2)',
                }}
              >
                {fmtDate(snap.due_date)}
              </span>
            ) : <span className="faint">—</span>)}
          </>
        ) : snap?.error ? null : t('尚未连接')}
        {snap?.fetched_at ? (
          <div className="tiny faint">{t('更新于 {time}', { time: fmtDateTime(snap.fetched_at) })}</div>
        ) : null}
        <button
          type="button"
          className="btn ghost sm"
          style={{ marginTop: 2, justifySelf: 'start' }}
          disabled={disabled || busy || !issue.software_jira_key}
          onClick={refresh}
        >
          {busy ? t('拉取中…') : t('刷新软件 Jira')}
        </button>
      </div>
    </>
  )
}

function SchemeCard({
  issue, project, highlightFields,
}: {
  issue: IssueDetail
  project: ProjectDetail
  highlightFields: Set<string>
}) {
  const { user, toast, openIssue } = useApp()
  const patch = usePatchIssue(issue.key)
  const [askUnlock, setAskUnlock] = useState(false)
  const [busy, setBusy] = useState(false)
  const inWindow = issue.status.name === '方案讨论'
  const submitted = schemeSubmitted(issue)
  const done = issue.scheme_locked && schemeVotesDone(issue)
  const resubmit = submitted && !issue.scheme_locked
  const noteEditable = inWindow && !issue.scheme_locked && (
    project.is_admin || user.is_admin || project.my_roles.includes('electrical')
    || issue.software_owner?.id === user.id || issue.assignee?.id === user.id
  )
  const voteOpen = inWindow && submitted && !(issue.scheme_locked && done)
  const vote = (role: boolean) => voteOpen && (project.is_admin || user.is_admin || role)
  const run = (changes: Record<string, unknown>) => {
    setBusy(true)
    patch.mutate(changes, {
      onSuccess: () => setAskUnlock(false),
      onError: (err) => toast((err as Error).message, 'error'),
      onSettled: () => setBusy(false),
    })
  }
  const badge = !inWindow
    ? t('仅在方案讨论中填写')
    : done
      ? t('已全部确认 · 已锁定')
      : issue.scheme_locked
        ? t('待三方确认')
        : resubmit
          ? t('已解锁，请重新发起')
          : t('待发起确认')
  const submitBlock = schemeSubmitBlock(issue)
  const hint = !inWindow
    ? t('进入「方案讨论」后才能编写和确认方案。')
    : !submitted && submitBlock
      ? t(submitBlock)
      : !submitted
        ? t('方案说明保存后，点右上角「发起方案确认」，再由电气、工艺和软件分别确认。')
        : done
        ? t('三方已确认。如需修改，先解锁。改方案说明后要重新发起确认。')
        : t('方案说明已锁定。各方只能确认自己的一项。')
  const actions = (
    <span className="scheme-actions" style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
      <span className="scheme-badge" style={{
        fontSize: 11.5, fontWeight: 550, padding: '2px 8px', borderRadius: 999,
        color: 'var(--text-2)', whiteSpace: 'nowrap',
        background: 'color-mix(in srgb, var(--section-accent, var(--sec-scheme)) 16%, var(--surface))',
      }}>{badge}</span>
      {canRequestScheme(project, user, issue) && (
        <button
          className="btn primary sm"
          type="button"
          disabled={busy || !canSubmitScheme(project, user, issue)}
          title={submitBlock ? t(submitBlock) : undefined}
          onClick={() => run({ scheme_action: 'submit' })}
        >
          {busy ? (resubmit ? t('正在分析变更…') : t('正在发起…')) : t('发起方案确认')}
        </button>
      )}
      {canUnlockScheme(project, user, issue) && !askUnlock && (
        <button
          className="btn sm icon lk"
          type="button"
          aria-label={t('解锁编辑')}
          title={t('解锁编辑')}
          onClick={() => setAskUnlock(true)}
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            <path className="s" d="M8 10.2V7.2a4 4 0 0 1 8 0V10.2" />
            <rect x="6" y="11" width="12" height="9" rx="2" />
            <circle cx="12" cy="15.5" r="1" fill="currentColor" stroke="none" />
          </svg>
        </button>
      )}
      {askUnlock && (
        <>
          <button className="btn primary sm" type="button" disabled={busy} onClick={() => run({ scheme_action: 'unlock' })}>
            {t('确认解锁')}
          </button>
          <button className="btn sm" type="button" disabled={busy} onClick={() => setAskUnlock(false)}>{t('取消')}</button>
        </>
      )}
    </span>
  )
  return (
    <SectionCard section="scheme" label={sectionName('scheme', project.profile)} editable={noteEditable || voteOpen} extra={actions}>
      <GateField fieldKey="scheme_note" label={t('方案说明')} miss={gateMisses(highlightFields, 'scheme_note')} top>
        <AttachmentMarkdownEditor
          issueKey={issue.key}
          value={issue.scheme_note || ''}
          editable={noteEditable && !busy}
          onSave={(value) => run({ scheme_note: value })}
          onIssueClick={openIssue}
          polishTitle={issue.title}
          polishKind="scheme"
          placeholder={t('方案要点、步骤和验收标准；可直接粘贴或拖入截图')}
        />
      </GateField>
      <p className="scheme-hint" style={{ gridColumn: '1 / -1', margin: 0, color: 'var(--text-3)', fontSize: 12 }}>{hint}</p>
      <GateField fieldKey="electrical_scheme_ok" label={t('电气确认')} miss={gateMisses(highlightFields, 'electrical_scheme_ok')}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <TriState
            value={issue.electrical_scheme_ok}
            disabled={!vote(project.my_roles.includes('electrical')) || busy}
            onChange={(value) => run({ electrical_scheme_ok: value })}
          />
          {issue.electrical_scheme_ok === true && issue.electrical_scheme_by && <SchemeSign user={issue.electrical_scheme_by} />}
        </span>
      </GateField>
      <GateField fieldKey="process_scheme_ok" label={t('工艺确认')} miss={gateMisses(highlightFields, 'process_scheme_ok')}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <TriState
            value={issue.process_scheme_ok}
            disabled={!vote(project.my_roles.includes('process')) || busy}
            onChange={(value) => run({ process_scheme_ok: value })}
          />
          {issue.process_scheme_ok === true && issue.process_scheme_by && <SchemeSign user={issue.process_scheme_by} />}
        </span>
      </GateField>
      <Fold open={issue.involves_software === true} className="scheme-software-fold">
        <div className="scheme-software-row" style={{ display: 'grid', gridTemplateColumns: '120px minmax(0, 1fr)', gap: '8px 12px', alignItems: 'center' }}>
          <GateField fieldKey="software_scheme_ok" label={t('软件确认')} miss={gateMisses(highlightFields, 'software_scheme_ok')}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <TriState
                value={issue.software_scheme_ok}
                disabled={!vote(issue.software_owner?.id === user.id) || busy}
                onChange={(value) => run({ software_scheme_ok: value })}
              />
              {issue.software_scheme_ok === true && issue.software_scheme_by && <SchemeSign user={issue.software_scheme_by} />}
            </span>
          </GateField>
        </div>
      </Fold>
      {gateMisses(highlightFields, 'scheme_locked') && (
        <p style={{ gridColumn: '1 / -1', margin: 0, color: 'var(--text-3)', fontSize: 12 }}>{t('进入「研发中」前请先发起方案确认。')}</p>
      )}
    </SectionCard>
  )
}

function ProfileSections({
  issue,
  project,
  can,
  save,
  descriptionBlock,
  onAddMachine,
  highlightFields,
}: {
  issue: IssueDetail
  project: ProjectDetail
  can: (section: Section) => boolean
  save: (changes: Record<string, unknown>) => void
  descriptionBlock: ReactNode
  onAddMachine: (groupId: number, name: string) => Promise<void>
  highlightFields: Set<string>
}) {
  const { openIssue } = useApp()
  const fields = new Map(project.profile.custom_fields.map((field) => [field.key, field]))
  return (
    <>
      {project.profile.sections.filter((section) =>
        !section.issue_types?.length || section.issue_types.includes(issue.type),
      ).map((section) => {
        if (section.key === 'scheme') {
          return (
            <SchemeCard
              key={section.key}
              issue={issue}
              project={project}
              highlightFields={highlightFields}
            />
          )
        }
        const card = (
        <SectionCard key={section.key} section={section.key} label={sectionName(section.key, project.profile)} editable={can(section.key as Section)}>
          {section.key === 'requester' && <>
            <span className="label">{t('需求提出人')}</span>
            <UserChip user={issue.reporter} />
          </>}
          {section.fields.map((key) => {
            const field = fields.get(key)
            const disabled = !can(section.key as Section)
            const miss = gateMisses(highlightFields, key)
            if (!field) {
              const builtins: Record<string, ReactNode> = {
                title: null,
                description: descriptionBlock,
                project_name: <InlineText value={issue.project_name} disabled={disabled} onSave={(v) => save({ project_name: v })} />,
                machine_ids: <MachinePicker groups={project.machine_groups} value={issue.machines.map((m) => m.id)} disabled={disabled} onChange={(ids) => save({ machine_ids: ids })} onAddMachine={onAddMachine} />,
                update_scope: <ChipSelect options={project.project.settings.update_scope_options ?? []} value={issue.update_scope} disabled={disabled} onChange={(v) => save({ update_scope: v })} />,
                involves_software: <TriState value={issue.involves_software} disabled={disabled} onChange={(v) => save({ involves_software: v })} />,
                software_owner_id: <UserPicker value={issue.software_owner} softwareOf={issue.project_key ?? undefined} disabled={disabled || issue.involves_software !== true} onChange={(u) => save({ software_owner_id: u?.id ?? null })} />,
                software_ready: <TriState value={issue.software_ready} disabled={disabled} onChange={(v) => save({ software_ready: v })} />,
                software_note: <InlineText multiline value={issue.software_note || ''} disabled={disabled} onSave={(v) => save({ software_note: v })} />,
                software_jira_key: <InlineText value={issue.software_jira_key || ''} disabled={disabled} onSave={(v) => save({ software_jira_key: v })} />,
                verify_method: <AttachmentMarkdownEditor issueKey={issue.key} value={issue.verify_method ?? ''} editable={!disabled} onSave={(v) => save({ verify_method: v })} onIssueClick={openIssue} />,
                verify_criteria: <AttachmentMarkdownEditor issueKey={issue.key} value={issue.verify_criteria ?? ''} editable={!disabled} onSave={(v) => save({ verify_criteria: v })} onIssueClick={openIssue} />,
                update_date: <InlineText type="date" value={issue.update_date} disabled={disabled} onSave={(v) => save({ update_date: v || null })} />,
                tester_id: <UserPicker value={issue.tester} project={issue.project_key ?? undefined} field="tester_id" disabled={disabled} onChange={(u) => save({ tester_id: u?.id ?? null })} />,
                remark: <AttachmentMarkdownEditor issueKey={issue.key} value={issue.remark} editable={!disabled} onSave={(v) => save({ remark: v })} onIssueClick={openIssue} placeholder={t('测试过程、现象或结论；可直接粘贴或拖入截图')} />,
              }
              if (!(key in builtins) || key === 'test_passed') return null
              const labels: Record<string, string> = {
                description: '需求说明', project_name: '项目名称', machine_ids: '机台',
                update_scope: '影响范围', involves_software: '是否涉及软件',
                software_owner_id: '软件负责人', software_ready: '软件是否准备好',
                software_note: '软件交付说明', software_jira_key: '软件 Jira 单号',
                verify_method: '验证方法',
                verify_criteria: '验证标准', update_date: '更新日期', tester_id: '测试人',
                remark: '备注',
              }
              if (key === 'title') return null
              return (
                <GateField
                  key={key}
                  fieldKey={key}
                  miss={miss}
                  required={key === 'software_jira_key' && issue.involves_software === true}
                  top={['description', 'remark', 'verify_method', 'verify_criteria', 'software_note'].includes(key)}
                  label={t(labels[key] ?? key)}
                >
                  {builtins[key]}
                </GateField>
              )
            }
            const value = issue.custom_fields[field.key]
            const control = field.kind === 'select' ? (
              <Select className="select" disabled={disabled} value={String(value ?? '')}
                onChange={(e) => save({ custom_fields: { [field.key]: e.target.value } })}>
                <option value="">—</option>
                {field.options.map((option) => <option key={option}>{option}</option>)}
              </Select>
            ) : field.kind === 'boolean' ? (
              <TriState disabled={disabled} value={value as boolean | null ?? null}
                onChange={(next) => save({ custom_fields: { [field.key]: next } })} />
            ) : field.kind === 'user' ? (
              <UserPicker value={issue.custom_field_users[field.key] ?? null} disabled={disabled}
                project={issue.project_key ?? undefined} field="member"
                onChange={(next) => save({ custom_fields: { [field.key]: next?.id ?? null } })} />
            ) : field.kind === 'machine' ? (
              <MachinePicker groups={project.machine_groups}
                value={(value as number[] | null) ?? []} disabled={disabled}
                onChange={(next) => save({ custom_fields: { [field.key]: next } })}
                onAddMachine={onAddMachine} />
            ) : field.kind === 'master_ref' && field.master_kind ? (
              <AssetPicker kind={field.master_kind} value={value as number | number[] | null}
                multiple={field.multiple} disabled={disabled}
                onChange={(next) => save({ custom_fields: { [field.key]: next } })} />
            ) : field.kind === 'markdown' ? (
              <AttachmentMarkdownEditor
                issueKey={issue.key}
                value={String(value ?? '')}
                editable={!disabled}
                onSave={(next) => save({ custom_fields: { [field.key]: next } })}
                onIssueClick={openIssue}
                polishTitle={issue.title}
                polishKind={field.key === 'delivery_note' ? 'delivery' : 'description'}
                placeholder={
                  field.key === 'delivery_note'
                    ? t('测试方法或交付说明；可直接粘贴或拖入截图')
                    : undefined
                }
              />
            ) : (
              <InlineText multiline={field.kind === 'textarea'}
                type={field.kind === 'date' ? 'date' : field.kind === 'number' ? 'number' : 'text'}
                value={value == null ? '' : String(value)} disabled={disabled}
                onSave={(next) => save({ custom_fields: { [field.key]: field.kind === 'number' && next ? Number(next) : next } })} />
            )
            return (
              <GateField
                key={field.key}
                fieldKey={field.key}
                miss={miss}
                label={customFieldLabel(field)}
                top={field.kind === 'markdown' || field.kind === 'textarea'}
              >
                {control}
              </GateField>
            )
          })}
          {section.key === 'software' && (
            <SoftwareSnapshot issue={issue} disabled={!can('software' as Section)} />
          )}
        </SectionCard>
        )
        if (section.key !== 'software') return card
        return (
          <Fold key={section.key} open={issue.involves_software === true} className="card-fold">
            {card}
          </Fold>
        )
      })}
    </>
  )
}
export function IssueView({ issueKey }: { issueKey: string }) {
  const { data: issue, isLoading, error } = useIssue(issueKey)
  const project = useProject(issue?.project_key)
  useProjectLiveSync(issue?.project_key)
  if (isLoading) return <Spinner center />
  if (error || !issue) return <div className="empty">{t('问题不存在或无权访问：{error}', { error: (error as Error)?.message })}</div>
  return <IssueBody issue={issue} project={project.data} />
}

function IssueBody({ issue, project }: { issue: IssueDetail; project?: ProjectDetail }) {
  const { toast, openCreate, openIssue, closeIssue, gateHighlight, clearGateHighlight } = useApp()
  const patch = usePatchIssue(issue.key)
  const invalidate = useInvalidateIssues()
  const client = useQueryClient()
  const navigate = useNavigate()
  const can = (section: Section) => issue.editable_sections.includes(section)
  const isReq = issue.type === 'requirement'
  const sectionOwns = (field: string): Section | null => {
    const match = project?.profile.sections.find((section) =>
      (!section.issue_types?.length || section.issue_types.includes(issue.type))
      && section.fields.includes(field))
    return match?.key ?? null
  }
  // AI template puts description/title in `basic`; electrical keeps them under `requester`.
  const canEditDescription = (() => {
    const key = sectionOwns('description')
    if (key) return can(key)
    return can('requester') || (!isReq && can('general'))
  })()
  const canEditTitle = (() => {
    const key = sectionOwns('title')
    if (key) return can(key)
    return can('requester') || (!isReq && can('general'))
  })()
  const descriptionInSection = !!sectionOwns('description')
  const testStatus = project?.project.settings.test_status ?? '测试中'
  const flow = project ? project.statuses.filter((s) => s.id === issue.status.id || typeFlowStatuses(project, issue.type, project.statuses).includes(s)) : null
  const usesTest = !flow || flow.some((s) => s.name === testStatus)
  const [summary, setSummary] = useState<string | null>(null)
  const [summarizing, setSummarizing] = useState(false)
  const [cloning, setCloning] = useState(false)
  const [editingDesc, setEditingDesc] = useState(false)
  const [addingSub, setAddingSub] = useState(false)
  const [descUndo, setDescUndo] = useState<string | null>(null)
  const [gateFields, setGateFields] = useState<string[]>([])
  const [reopenPrompt, setReopenPrompt] = useState<{ statusId: number } | null>(null)
  const highlightFields = gateFieldSet(
    gateHighlight?.issueKey === issue.key ? [...gateFields, ...gateHighlight.fields] : gateFields,
  )

  useEffect(() => {
    if (gateHighlight?.issueKey !== issue.key || !gateHighlight.fields.length) return
    setGateFields((prev) => [...new Set([...prev, ...gateHighlight.fields])])
    clearGateHighlight()
  }, [gateHighlight, issue.key, clearGateHighlight])

  useEffect(() => {
    if (!highlightFields.size) return
    const keys = [...highlightFields]
    const timer = window.setTimeout(() => {
      for (const key of keys) {
        const el = document.querySelector<HTMLElement>(`[data-field="${CSS.escape(key)}"]`)
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' })
          break
        }
      }
    }, 80)
    return () => window.clearTimeout(timer)
  }, [highlightFields])

  const clearGateKeys = (changes: Record<string, unknown>) => {
    const touched = new Set<string>()
    for (const key of Object.keys(changes)) {
      touched.add(key)
      if (key === 'custom_fields' && changes.custom_fields && typeof changes.custom_fields === 'object') {
        for (const custom of Object.keys(changes.custom_fields as object)) {
          touched.add(custom)
          touched.add(`custom_fields.${custom}`)
        }
      }
    }
    setGateFields((prev) => prev.filter((field) => !gateMisses(touched, field)))
  }

  const save = (changes: Record<string, unknown>) =>
    patch.mutate(changes, {
      onSuccess: () => clearGateKeys(changes),
      onError: (err) => toast((err as Error).message, 'error'),
    })

  const applyStatus = async (statusId: number, reopenReason?: string) => {
    try {
      const payload: Record<string, unknown> = { status_id: statusId }
      if (reopenReason) payload.reopen_reason = reopenReason
      const result = await withSubtaskClose((close) =>
        patch.mutateAsync(close ? { ...payload, close_subtasks: true } : payload),
      )
      const message = lastSubtaskMessage(result)
      if (message) toast(message, 'success')
      setGateFields([])
      setReopenPrompt(null)
    } catch (err) {
      if (err instanceof ApiError && err.code === 'approval_required') {
        try {
          await api.post(`/api/issues/${issue.key}/transition-requests`, { status_id: statusId })
          await invalidate()
          toast(t('已提交状态审批'), 'success')
          setReopenPrompt(null)
          return
        } catch (requestError) {
          toast((requestError as Error).message, 'error')
          return
        }
      }
      if (err instanceof ApiError && err.code === 'reopen_reason') {
        setReopenPrompt({ statusId })
        toast((err as Error).message, 'error')
        return
      }
      if (err instanceof ApiError && err.code === 'software_pending') {
        toast(tServer(err.message), 'info')
        await invalidate()
        return
      }
      if (err instanceof ApiError && err.code === 'transition_gate' && err.fields?.length) {
        setGateFields(err.fields)
      }
      toast((err as Error).message, 'error')
    }
  }

  const moveStatus = async (statusId: number) => {
    const target = issue.transitions.find((s) => s.id === statusId)
      ?? project?.statuses.find((s) => s.id === statusId)
    if (target?.name === REOPENED) {
      setReopenPrompt({ statusId })
      return
    }
    await applyStatus(statusId)
  }

  const changeType = (type: Issue['type']) => {
    if (type === issue.type) return
    if (issue.parent && !window.confirm(t('改为「{type}」后将脱离父任务 {key}，确定吗？', { type: typeName(type), key: issue.parent.key }))) return
    save({ type })
  }

  const addMachine = async (groupId: number, name: string) => {
    try {
      await api.post(`/api/projects/${issue.project_key}/machines`, { group_id: groupId, name })
      await client.invalidateQueries({ queryKey: ['project', issue.project_key] })
      toast(t('已添加机台 {name}', { name }), 'success')
    } catch (err) {
      toast((err as Error).message, 'error')
    }
  }

  const runSummary = async () => {
    setSummary('')
    setSummarizing(true)
    try {
      await streamPost(`/api/agent/summarize/${issue.key}`, {}, (ev) => {
        if (ev.type === 'delta') setSummary((s) => (s ?? '') + (ev.text ?? ''))
        if (ev.type === 'error') toast(ev.message ? tServer(ev.message) : t('AI 不可用'), 'error')
      })
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setSummarizing(false)
    }
  }

  const remove = async () => {
    await api.del(`/api/issues/${issue.key}`)
    toast(t('{key} 已删除', { key: issue.key }))
    invalidate()
    closeIssue()
    if (window.location.pathname.startsWith('/issues/')) navigate(`/projects/${issue.project_key}/board`)
  }

  const toggleWatch = async () => {
    await (issue.watching ? api.del(`/api/issues/${issue.key}/watch`) : api.post(`/api/issues/${issue.key}/watch`))
    client.invalidateQueries({ queryKey: ['issue', issue.key] })
  }

  const descriptionBlock = (
    <DescriptionEditor
      issueKey={issue.key}
      title={issue.title}
      value={issue.description}
      editable={canEditDescription}
      editing={editingDesc}
      setEditing={setEditingDesc}
      onSave={(v) => {
        setDescUndo(null)
        save({ description: v })
      }}
      onIssueClick={openIssue}
      externalUndo={descUndo}
      onClearExternalUndo={() => setDescUndo(null)}
      onExternalUndo={(prev) => {
        setDescUndo(null)
        save({ description: prev })
      }}
    />
  )

  const [detailsOpen, setDetailsOpen] = useState(true)

  return (
    <div className="issue-layout">
      <div className="issue-top">
        <div className="breadcrumb">
          <Link to={`/projects/${issue.project_key}/board`} className="breadcrumb-crumb" title={issue.project_key ?? undefined}>
            <ProjectBadge
              icon={project?.project.icon}
              color={project?.project.color ?? '#8e8e93'}
              label={(issue.project_key ?? '?').slice(0, 2)}
              size={16}
              className="project-dot"
            />
            <span className="crumb-title">{project?.project.name ?? issue.project_key}</span>
          </Link>
          {issue.type !== 'epic' && (
            <>
              <span className="faint">/</span>
              {issue.epic ? (
                <button type="button" className="breadcrumb-crumb" onClick={() => openIssue(issue.epic!.key)} title={issue.epic.key}>
                  <TypeIcon type="epic" size={14} />
                  <span className="crumb-title">{issue.epic.title}</span>
                </button>
              ) : can('general') ? (
                <Dropdown
                  trigger={
                    <span className="breadcrumb-crumb breadcrumb-add-epic">
                      <TypeIcon type="epic" size={14} />
                      {t('添加史诗')}
                      <span aria-hidden>✎</span>
                    </span>
                  }
                  options={[
                    { value: 0, label: t('无') },
                    ...(project?.epics ?? []).map((e) => ({ value: e.id, label: `${e.key} ${e.title}` })),
                  ]}
                  onSelect={(id) => save({ epic_id: id || null })}
                />
              ) : (
                <span className="faint tiny">{t('无史诗')}</span>
              )}
            </>
          )}
          {issue.parent && (
            <>
              <span className="faint">/</span>
              <button type="button" className="breadcrumb-crumb" onClick={() => openIssue(issue.parent!.key)} title={issue.parent.key}>
                <TypeIcon type={issue.parent.type} size={14} />
                <span className="crumb-title">{issue.parent.title}</span>
              </button>
            </>
          )}
          <span className="faint">/</span>
          <span className="breadcrumb-crumb" style={{ cursor: 'default' }}>
            <TypeIcon type={issue.type} size={14} />
            <span className="issue-key">{issue.key}</span>
          </span>
        </div>
        <div className="row" style={{ alignItems: 'flex-start', gap: 10 }}>
          <Dropdown
            disabled={!can('general')}
            trigger={<span style={{ marginTop: 9 }}><TypeIcon type={issue.type} size={22} /></span>}
            options={ISSUE_TYPES.filter((tp) => tp !== 'subtask' || issue.type === 'subtask').map((tp) => ({ value: tp, label: <><TypeIcon type={tp} /> {typeName(tp)}</> }))}
            header={issue.type !== 'subtask' && issue.type !== 'epic' ? <div className="tiny faint" style={{ padding: '4px 10px 6px' }}>{t('要改为子任务，请在下方设置「父任务」')}</div> : undefined}
            value={issue.type}
            onSelect={changeType}
          />
          <TitleInput value={issue.title} disabled={!canEditTitle} onSave={(v) => save({ title: v })} />
        </div>
        {issue.parent_info && (
          <button type="button" className="parent-banner" onClick={() => openIssue(issue.parent_info!.key)} title={t('打开父任务')}>
            <span className="parent-banner-label">↳ {t('所属父任务')}</span>
            <TypeIcon type={issue.parent_info.type} />
            <span className="issue-key">{issue.parent_info.key}</span>
            <span className="parent-banner-title">{issue.parent_info.title}</span>
            <StatusPill status={issue.parent_info.status} />
            {issue.parent_progress && issue.parent_progress.total > 0 && (
              <span className="tiny faint">{t('子任务 {done}/{total}', { done: issue.parent_progress.done, total: issue.parent_progress.total })}</span>
            )}
            <Avatar user={issue.parent_info.assignee} size={20} />
          </button>
        )}
        <div className="row wrap" style={{ margin: '10px 0 12px' }}>
          <IssueAiMenu
            issue={issue}
            onSummary={runSummary}
            summarizing={summarizing}
            canEditDescription={canEditDescription}
            canCreateChildren={can('general') && issue.type !== 'subtask'}
            onPolished={(next, previous) => {
              setDescUndo(previous)
              save({ description: next })
            }}
          />
          <button
            className={`btn sm watch-btn${issue.watching ? ' watching' : ''}`}
            onClick={toggleWatch}
            aria-pressed={issue.watching}
          >
            {issue.watching ? t('★ 已关注') : t('☆ 关注')} · {issue.watchers.length}
          </button>
          {issue.type !== 'subtask' && (
            <button
              className="btn sm"
              onClick={() =>
                issue.type === 'epic' ? openCreate({ project: issue.project_key!, epic_id: issue.id, type: 'task' }) : setAddingSub(true)
              }
            >
              {issue.type === 'epic' ? t('＋ 添加子项') : t('＋ 子任务')}
            </button>
          )}
          {can('general') && (
            <button
              className="btn sm"
              disabled={cloning}
              onClick={async () => {
                setCloning(true)
                try {
                  const copy = await api.post<IssueDetail>(`/api/issues/${issue.key}/clone`, {})
                  toast(t('已克隆为 {key}', { key: copy.key }), 'success')
                  invalidate()
                  openIssue(copy.key)
                } catch (err) {
                  toast((err as Error).message, 'error')
                } finally {
                  setCloning(false)
                }
              }}
            >
              {cloning ? t('克隆中…') : t('克隆')}
            </button>
          )}
          <span className="spacer" />
          {issue.can_delete && (
            <ConfirmButton onConfirm={remove} message={t('确定删除 {key}？此操作不可撤销。', { key: issue.key })}>
              {t('删除')}
            </ConfirmButton>
          )}
        </div>

        <WorkflowFlow
          issueKey={issue.key}
          statuses={flow ?? [issue.status, ...issue.transitions]}
          rules={project ? typeRules(project, issue.type) : undefined}
          current={issue.status}
          transitions={issue.transitions}
          canMove={can('general')}
          busy={patch.isPending && patch.variables?.status_id !== undefined}
          onMove={moveStatus}
          testFlow={project ? {
            testStatus: project.project.settings.test_status ?? '测试中',
            passStatus: project.project.settings.pass_status ?? '测试通过',
            failStatus: project.project.settings.fail_return_status ?? '测试未通过',
          } : undefined}
        />
        {issue.software_test_wait && (
          <p style={{ margin: '10px 0 0', color: 'var(--text-2)', fontSize: 13, lineHeight: 1.5 }}>
            {tServer(issue.software_test_wait)}
          </p>
        )}
      </div>

      <div className="issue-body">
        <div className="issue-main">
          {summary !== null && (
            <div className="ai-box" style={{ marginBottom: 16 }}>
              <div className="row" style={{ marginBottom: 6 }}>
                <span className="ai-orb" style={{ width: 18, height: 18 }} />
                <b>{t('AI 总结')}</b>
                <span className="spacer" />
                <button className="btn ghost sm" onClick={() => setSummary(null)}>
                  {t('收起')}
                </button>
              </div>
              {summary ? <Markdown text={summary} onIssueClick={openIssue} /> : <Spinner />}
            </div>
          )}

          {project ? (
            <>
              {!descriptionInSection && <div className="card card-pad" style={{ marginBottom: 14 }}>
                <h3 style={{ marginBottom: 8 }}>{t('描述')}</h3>
                {descriptionBlock}
              </div>}
              <ProfileSections issue={issue} project={project} can={can} save={save}
                descriptionBlock={descriptionBlock} onAddMachine={addMachine}
                highlightFields={highlightFields} />
            </>
          ) : isReq ? (
            <>
              <SectionCard section="requester" editable={can('requester')}>
                <span className="label">{t('需求提出人')}</span>
                <UserChip user={issue.reporter} />
                <span className="label">{t('项目名称')}</span>
                <InlineText value={issue.project_name} disabled={!can('requester')} onSave={(v) => save({ project_name: v })} />
                <span className="label">{t('机台')}</span>
                <MachinePicker
                  groups={[]}
                  value={issue.machines.map((m) => m.id)}
                  disabled={!can('requester')}
                  onChange={(ids) => save({ machine_ids: ids })}
                  onAddMachine={addMachine}
                />
                <span className="label" style={{ alignSelf: 'start', paddingTop: 6 }}>
                  {t('需求说明')}
                </span>
                {descriptionBlock}
              </SectionCard>
              <SectionCard section="electrical" editable={can('electrical')}>
                <span className="label">{t('影响范围')}</span>
                <ChipSelect
                  options={[]}
                  value={issue.update_scope}
                  disabled={!can('electrical')}
                  onChange={(v) => save({ update_scope: v })}
                />
                <span className="label">{t('是否涉及软件')}</span>
                <div>
                  <TriState value={issue.involves_software} disabled={!can('electrical')} onChange={(v) => save({ involves_software: v })} />
                </div>
                {issue.involves_software === true && (
                  <>
                    <span className="label">{t('软件负责人')}</span>
                    <UserPicker
                      value={issue.software_owner}
                      softwareOf={issue.project_key ?? undefined}
                      disabled={!can('electrical')}
                      onChange={(u) => save({ software_owner_id: u?.id ?? null })}
                    />
                  </>
                )}
                <span className="label top">{t('验证方法')}</span>
                <AttachmentMarkdownEditor issueKey={issue.key} value={issue.verify_method ?? ''} editable={can('electrical')} onSave={(v) => save({ verify_method: v })} onIssueClick={openIssue} />
                <span className="label top">{t('验证标准')}</span>
                <AttachmentMarkdownEditor issueKey={issue.key} value={issue.verify_criteria ?? ''} editable={can('electrical')} onSave={(v) => save({ verify_criteria: v })} onIssueClick={openIssue} />
                <span className="label">{t('当前状态')}</span>
                <div>
                  <StatusPill status={issue.status} />
                </div>
              </SectionCard>
              <SectionCard section="tester" editable={can('tester')}>
                <span className="label">{t('更新日期')}</span>
                <InlineText type="date" value={issue.update_date} disabled={!can('tester')} onSave={(v) => save({ update_date: v || null })} />
                <span className="label">{t('测试人')}</span>
                <UserPicker value={issue.tester} project={issue.project_key ?? undefined} field="tester_id" disabled={!can('tester')} onChange={(u) => save({ tester_id: u?.id ?? null })} />
                <span className="label top">{t('备注')}</span>
                <AttachmentMarkdownEditor issueKey={issue.key} value={issue.remark} editable={can('tester')} onSave={(v) => save({ remark: v })} onIssueClick={openIssue} placeholder={t('测试过程、现象或结论；可直接粘贴或拖入截图')} />
              </SectionCard>
            </>
          ) : (
            <div className="card card-pad" style={{ marginBottom: 14 }}>
              <h3 style={{ marginBottom: 8 }}>{t('描述')}</h3>
              {descriptionBlock}
            </div>
          )}

          <ApprovalBlock issue={issue} />
          <ChildrenBlock issue={issue} project={project} adding={addingSub} setAdding={setAddingSub} onMoveParent={moveStatus} />
          <LinksBlock issue={issue} />
          <AttachmentsBlock issue={issue} />
          <ActivityBlock issue={issue} project={project} />
        </div>

        <aside className="issue-sidebar">
          <div className={`issue-details${detailsOpen ? '' : ' collapsed'}`}>
            <button
              type="button"
              className="issue-details-head"
              aria-expanded={detailsOpen}
              onClick={() => setDetailsOpen((v) => !v)}
            >
              <span className="issue-details-chevron" aria-hidden>▾</span>
              <span>{t('详细信息')}</span>
            </button>
            {detailsOpen && (
              <div className="issue-details-body">
                <div className="props">
                  <Prop label={<><FieldIcon name="assignee" />{t('负责人')}</>}>
                    <UserPicker value={issue.assignee} project={issue.project_key ?? undefined} field="assignee_id" disabled={!can('general')} onChange={(u) => save({ assignee_id: u?.id ?? null })} />
                  </Prop>
                  <Prop label={<><FieldIcon name="due" />{t('截止日期')}</>}>
                    <InlineText type="date" value={issue.due_date} disabled={!can('general')} onSave={(v) => save({ due_date: v || null })} />
                  </Prop>
                  <Prop label={<><FieldIcon name="priority" />{t('优先级')}</>}>
                    <Dropdown
                      disabled={!can('general')}
                      trigger={
                        <span className="inline-edit">
                          <PriorityIcon priority={issue.priority} /> {priorityName(issue.priority)}
                        </span>
                      }
                      options={PRIORITIES.map((p) => ({ value: p, label: <><PriorityIcon priority={p} /> {priorityName(p)}</> }))}
                      value={issue.priority}
                      onSelect={(p) => save({ priority: p })}
                    />
                  </Prop>
                  <Prop label={<><FieldIcon name="labels" />{t('标签')}<InfoTip topic="labels" /></>}>
                    <LabelPicker
                      options={project?.labels ?? project?.project.settings.label_options ?? []}
                      value={issue.labels}
                      disabled={!can('general')}
                      onChange={(labels) => save({ labels })}
                    />
                  </Prop>
                  <Prop label={<><FieldIcon name="status" />{t('状态')}</>}>
                    <StatusPill status={issue.status} />
                  </Prop>
                  <Prop label={<><FieldIcon name="reporter" />{t('报告人')}</>}>
                    <UserChip user={issue.reporter} />
                  </Prop>
                  <Prop label={<><FieldIcon name="sprint" />{t('冲刺')}<InfoTip topic="sprint" /></>}>
                    <Dropdown
                      disabled={!can('general')}
                      trigger={<span className="inline-edit">{project?.sprints.find((s) => s.id === issue.sprint_id)?.name ?? <span className="placeholder">{t('待办事项')}</span>}</span>}
                      options={[{ value: 0, label: t('待办事项（未加入冲刺）') }, ...(project?.sprints ?? []).map((s) => ({ value: s.id, label: s.name, hint: s.state === 'active' ? t('进行中') : '' }))]}
                      value={issue.sprint_id ?? 0}
                      onSelect={(id) => save({ sprint_id: id || null })}
                    />
                  </Prop>
                  {issue.type !== 'epic' && (
                    <Prop label={<><FieldIcon name="epic" />{t('史诗')}<InfoTip topic="epic" /></>}>
                      <Dropdown
                        disabled={!can('general')}
                        trigger={
                          <span className="inline-edit">
                            {issue.epic ? (
                              <><TypeIcon type="epic" size={14} /> {issue.epic.title}</>
                            ) : (
                              <span className="placeholder">{t('无')}</span>
                            )}
                          </span>
                        }
                        options={[
                          { value: 0, label: t('无') },
                          ...(project?.epics ?? []).map((e) => ({
                            value: e.id,
                            label: <><TypeIcon type="epic" size={14} /> {e.key} {e.title}</>,
                          })),
                        ]}
                        value={issue.epic?.id ?? 0}
                        onSelect={(id) => save({ epic_id: id || null })}
                      />
                    </Prop>
                  )}
                  {issue.type !== 'epic' && (issue.parent || issue.children.length === 0) && (
                    <Prop label={<><FieldIcon name="parent" />{t('父任务')}<InfoTip topic="parent" /></>}>
                      <ParentPicker issue={issue} disabled={!can('general')} onChange={(parentId) => save({ parent_id: parentId })} />
                    </Prop>
                  )}
                  <Prop label={<>{t('故事点')}<InfoTip topic="story_points" /></>}>
                    <InlineText type="number" value={issue.story_points?.toString() ?? ''} disabled={!can('general')} onSave={(v) => save({ story_points: v === '' ? null : Number(v) })} />
                  </Prop>
                  <Prop label={<><FieldIcon name="due" />{t('处理时限')}<InfoTip mark="?" text={slaHelp(project?.project.settings)} /></>}>
                    <span className={`sla-value ${issue.sla_state === 'breached' ? 'err-text' : issue.sla_state === 'warning' ? 'warn-text' : 'muted'}`}>
                      {issue.sla_due_at && issue.sla_state !== 'paused' && issue.sla_state !== 'none' && `${fmtDateTime(issue.sla_due_at)} · `}
                      {t(SLA_STATE_LABELS[issue.sla_state] ?? issue.sla_state)}
                    </span>
                  </Prop>
                  {!isReq && usesTest && (
                    <>
                      <Prop
                        fieldKey="tester_id"
                        miss={gateMisses(highlightFields, 'tester_id')}
                        label={<><FieldIcon name="tester" />{t('测试人')}</>}
                      >
                        <UserPicker value={issue.tester} project={issue.project_key ?? undefined} field="tester_id" disabled={!can('tester')} onChange={(u) => save({ tester_id: u?.id ?? null })} />
                      </Prop>
                    </>
                  )}
                  {!isReq && (
                    <Prop label={<><FieldIcon name="machines" />{t('机台')}</>}>
                      <MachinePicker groups={project?.machine_groups ?? []} value={issue.machines.map((m) => m.id)} disabled={!can('requester')} onChange={(ids) => save({ machine_ids: ids })} />
                    </Prop>
                  )}
                </div>
                <div className="props-meta tiny muted">
                  <span>{t('创建于 {time}', { time: fmtDateTime(issue.created_at) })}</span>
                  <span>{t('更新于 {time}', { time: timeAgo(issue.updated_at) })}</span>
                  {issue.resolved_at && <span>{t('解决于 {time}', { time: fmtDateTime(issue.resolved_at) })}</span>}
                  {issue.watchers.length > 0 && (
                    <span className="row" style={{ gap: 3 }}>
                      {t('关注者：')}
                      {issue.watchers.map((w) => (
                        <Avatar key={w.id} user={w} size={20} />
                      ))}
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>
        </aside>
      </div>

      {reopenPrompt && (
        <ReopenReasonModal
          issueKey={issue.key}
          onCancel={() => setReopenPrompt(null)}
          onConfirm={(reason) => applyStatus(reopenPrompt.statusId, reason)}
        />
      )}
    </div>
  )
}

function Prop({
  label,
  wide,
  fieldKey,
  miss,
  children,
}: {
  label: ReactNode
  wide?: boolean
  fieldKey?: string
  miss?: boolean
  children: ReactNode
}) {
  return (
    <div className={`prop${wide ? ' wide' : ''}${miss ? ' gate-miss-prop' : ''}`} data-field={fieldKey}>
      <span className={`label${miss ? ' gate-miss' : ''}`}>
        {label}
        {miss ? <span className="gate-miss-mark" aria-hidden>*</span> : null}
      </span>
      <div className="prop-value">{children}</div>
    </div>
  )
}

function ApprovalBlock({ issue }: { issue: IssueDetail }) {
  const pending = issue.approval_requests?.filter((row) => row.status === 'pending') ?? []
  const recent = issue.approval_requests?.filter((row) => row.status !== 'pending').slice(0, 3) ?? []
  const { data: project } = useProject(issue.project_key)
  const { toast } = useApp()
  const client = useQueryClient()
  if (!pending.length && !recent.length) return null
  const targetName = (statusId: number) =>
    project?.statuses.find((s) => s.id === statusId)?.name ?? `#${statusId}`
  const decide = async (id: number, approve: boolean) => {
    // prompt returns null when the user cancels — must not treat that as empty OK.
    const comment = window.prompt(approve ? t('审批意见（可选）') : t('请填写驳回原因'))
    if (comment === null) return
    if (!approve && !comment.trim()) return
    try {
      await api.post(`/api/approval-requests/${id}/decision`, { approve, comment })
      await client.invalidateQueries({ queryKey: ['issue', issue.key] })
      toast(approve ? t('已批准') : t('已驳回'), 'success')
    } catch (error) {
      toast((error as Error).message, 'error')
    }
  }
  return (
    <div className="card card-pad approval-block" style={{ marginBottom: 14 }}>
      <h3>{t('状态审批')}</h3>
      {[...pending, ...recent].map((row) => (
        <div className="approval-row" key={row.id}>
          <div>
            <b>{t('流转至「{status}」', { status: targetName(row.target_status_id) })}</b>
            <span className={`badge ${row.status}`}>{t(row.status)}</span>
            <div className="tiny muted">
              {row.requirements.map((requirement) => `${requirement.role} × ${requirement.count}`).join(' · ')}
            </div>
            {row.decisions.map((decision) => (
              <div className="small" key={decision.user_id}>{decision.name} · {t(decision.decision)}{decision.comment ? `：${decision.comment}` : ''}</div>
            ))}
          </div>
          {row.status === 'pending' && (
            <div className="row">
              <button className="btn sm primary" onClick={() => decide(row.id, true)}>{t('批准')}</button>
              <button className="btn sm danger" onClick={() => decide(row.id, false)}>{t('驳回')}</button>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

function TitleInput({ value, onSave, disabled }: { value: string; onSave: (v: string) => void; disabled: boolean }) {
  const [draft, setDraft] = useState(value)
  const reverting = useRef(false)
  useEffect(() => setDraft(value), [value])
  return (
    <input
      className="issue-title-input"
      value={draft}
      disabled={disabled}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        if (reverting.current) {
          reverting.current = false
          return
        }
        if (draft.trim() && draft !== value) onSave(draft.trim())
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') e.currentTarget.blur()
        if (e.key === 'Escape') {
          e.stopPropagation()
          reverting.current = true
          setDraft(value)
          e.currentTarget.blur()
        }
      }}
    />
  )
}

const POLISH_KEY = 'metjira.aiPolish'
const AUTOSAVE_DEBOUNCE_MS = 650

/** Drops a ```markdown fence the model sometimes wraps its whole answer in. */
const unfence = (text: string) => text.trim().replace(/^```(?:markdown|md)?\s*\n([\s\S]*?)\n?```$/i, '$1').trim()

function AiPolishToggle({
  checked,
  disabled,
  ready,
  title,
  onToggle,
}: {
  checked: boolean
  disabled: boolean
  ready: boolean
  title: string
  onToggle: (next: boolean) => void
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      className={`ai-polish${checked ? ' on' : ''}${ready ? '' : ' off'}`}
      title={title}
      disabled={disabled}
      onMouseDown={(e) => e.preventDefault()}
      onClick={() => {
        const next = !checked
        onToggle(next)
        localStorage.setItem(POLISH_KEY, next ? '1' : '0')
      }}
    >
      <input type="checkbox" checked={checked} readOnly tabIndex={-1} aria-hidden="true" />
      <span>✦ {t('AI 美化')}</span>
    </button>
  )
}

/** Shared debounced + blur + unmount autosave controller for inline Markdown editors. */
function useMarkdownAutoSave({
  editing,
  value,
  draft,
  busy,
  setDraft,
  onSave,
  onExit,
  shouldPolishOnFinish,
  onPolish,
  onAbort,
}: {
  editing: boolean
  value: string
  draft: string
  busy: boolean
  setDraft: (v: string) => void
  onSave: (v: string) => void
  onExit: () => void
  shouldPolishOnFinish: () => boolean
  onPolish: () => void
  onAbort: () => void
}) {
  const [saveState, setSaveState] = useState<'idle' | 'pending' | 'saved'>('idle')
  const wasEditingRef = useRef(false)
  const initialRef = useRef(value)
  const lastSavedRef = useRef(value)
  const draftRef = useRef(draft)
  const busyRef = useRef(busy)
  const editingRef = useRef(editing)
  const closingRef = useRef(false)
  const pickingFileRef = useRef(false)
  const timerRef = useRef<number | null>(null)
  const onSaveRef = useRef(onSave)

  draftRef.current = draft
  busyRef.current = busy
  editingRef.current = editing
  onSaveRef.current = onSave

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  // Sync from prop only when not actively editing, and snapshot on entering edit mode.
  useEffect(() => {
    if (editing && !wasEditingRef.current) {
      wasEditingRef.current = true
      closingRef.current = false
      initialRef.current = value
      lastSavedRef.current = value
      setDraft(value)
      setSaveState('idle')
    } else if (!editing) {
      wasEditingRef.current = false
      clearTimer()
      initialRef.current = value
      lastSavedRef.current = value
      setDraft(value)
      setSaveState('idle')
    }
  }, [editing, value, setDraft, clearTimer])

  // Debounced autosave while editing whenever draft diverges from lastSaved.
  useEffect(() => {
    if (!editing || closingRef.current) return
    if (busy) {
      clearTimer()
      return
    }
    if (draft === lastSavedRef.current) {
      clearTimer()
      return
    }
    setSaveState('pending')
    clearTimer()
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null
      if (!editingRef.current || closingRef.current || busyRef.current) return
      const latest = draftRef.current
      if (latest === lastSavedRef.current) return
      lastSavedRef.current = latest
      onSaveRef.current(latest)
      setSaveState('saved')
    }, AUTOSAVE_DEBOUNCE_MS)
    return clearTimer
  }, [editing, draft, busy, clearTimer])

  // Flush unsaved edits if the drawer closes / component unmounts mid-edit.
  useEffect(() => () => {
    clearTimer()
    if (
      editingRef.current &&
      !closingRef.current &&
      !busyRef.current &&
      draftRef.current !== lastSavedRef.current
    ) {
      lastSavedRef.current = draftRef.current
      onSaveRef.current(draftRef.current)
    }
  }, [clearTimer])

  const markSavedAndExit = useCallback((text: string) => {
    clearTimer()
    closingRef.current = true
    if (text !== lastSavedRef.current) {
      lastSavedRef.current = text
      onSaveRef.current(text)
    }
    setDraft(text)
    setSaveState('idle')
    onExit()
  }, [clearTimer, setDraft, onExit])

  const finishEdit = useCallback(() => {
    if (closingRef.current || busyRef.current) return
    clearTimer()
    const proseChanged =
      stripMdImages(draftRef.current).trim() !== stripMdImages(initialRef.current).trim()
    if (shouldPolishOnFinish() && proseChanged) {
      onPolish()
      return
    }
    markSavedAndExit(draftRef.current)
  }, [clearTimer, shouldPolishOnFinish, onPolish, markSavedAndExit])

  const cancelEdit = useCallback(() => {
    clearTimer()
    onAbort()
    closingRef.current = true
    const original = initialRef.current
    if (lastSavedRef.current !== original) {
      lastSavedRef.current = original
      onSaveRef.current(original)
    }
    setDraft(original)
    setSaveState('idle')
    onExit()
  }, [clearTimer, onAbort, setDraft, onExit])

  const onContainerBlur = useCallback((e: FocusEvent<HTMLDivElement>) => {
    if (pickingFileRef.current || busyRef.current || closingRef.current) return
    const nextTarget = e.relatedTarget as Node | null
    if (nextTarget && e.currentTarget.contains(nextTarget)) return
    finishEdit()
  }, [finishEdit])

  const onEditorKeyDown = useCallback((e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing) return
    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      cancelEdit()
    } else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      finishEdit()
    }
  }, [cancelEdit, finishEdit])

  const openFilePicker = useCallback((input: HTMLInputElement | null) => {
    if (!input) return
    pickingFileRef.current = true
    const reset = () => {
      window.setTimeout(() => {
        pickingFileRef.current = false
      }, 150)
      window.removeEventListener('focus', reset)
    }
    window.addEventListener('focus', reset, { once: true })
    input.click()
  }, [])

  const onFilePicked = useCallback(() => {
    pickingFileRef.current = false
  }, [])

  return {
    saveState,
    markSavedAndExit,
    finishEdit,
    cancelEdit,
    onContainerBlur,
    onEditorKeyDown,
    openFilePicker,
    onFilePicked,
  }
}

function DescriptionEditor({
  issueKey,
  title,
  value,
  editable,
  editing,
  setEditing,
  onSave,
  onIssueClick,
  externalUndo = null,
  onClearExternalUndo,
  onExternalUndo,
}: {
  issueKey: string
  title: string
  value: string
  editable: boolean
  editing: boolean
  setEditing: (v: boolean) => void
  onSave: (v: string) => void
  onIssueClick: (key: string) => void
  externalUndo?: string | null
  onClearExternalUndo?: () => void
  onExternalUndo?: (previous: string) => void
}) {
  const { toast } = useApp()
  const [draft, setDraft] = useState(value)
  const [polish, setPolish] = useState(() => localStorage.getItem(POLISH_KEY) === '1')
  const [polishing, setPolishing] = useState(false)
  const [undo, setUndo] = useState<string | null>(null)
  const showUndo = undo ?? externalUndo
  const abort = useRef<AbortController | null>(null)
  const box = useRef<HTMLTextAreaElement>(null)
  const descPicker = useRef<HTMLInputElement>(null)
  const paste = useAttachmentPaste(issueKey, box, setDraft)
  const ai = useQuery({
    queryKey: ['agent-status'],
    queryFn: () => api.get<{ enabled: boolean; reachable: boolean; error?: string }>('/api/agent/status'),
    staleTime: 300_000,
    enabled: editing,
  })
  const aiReady = !!ai.data?.enabled && ai.data.reachable
  const busy = paste.uploading > 0 || polishing
  const markSavedRef = useRef<(text: string) => void>(() => {})

  useEffect(() => {
    if (polishing && box.current) box.current.scrollTop = box.current.scrollHeight
  }, [draft, polishing])
  useEffect(() => () => abort.current?.abort(), [])

  const runPolish = useCallback(async () => {
    const original = draft
    const controller = new AbortController()
    abort.current = controller
    setPolishing(true)
    let out = ''
    let failure: string | null = null
    try {
      await streamPost(
        '/api/agent/polish',
        { text: original, title },
        (ev) => {
          if (ev.type === 'delta') {
            out += ev.text ?? ''
            setDraft(out)
          }
          if (ev.type === 'error') failure = ev.message ? tServer(ev.message) : t('AI 不可用')
        },
        controller.signal,
      )
      const result = unfence(out)
      if (!failure && !result) failure = t('AI 未返回内容')
      if (failure) throw new Error(failure)
      setUndo(original)
      markSavedRef.current(result)
      toast(t('已用 AI 美化并保存'), 'success')
    } catch (err) {
      setDraft(original)
      if (!controller.signal.aborted) toast(t('AI 美化失败，未保存：{error}', { error: (err as Error).message }), 'error')
    } finally {
      abort.current = null
      setPolishing(false)
    }
  }, [draft, title, toast])

  const exitEdit = useCallback(() => setEditing(false), [setEditing])
  const abortPolish = useCallback(() => abort.current?.abort(), [])
  const shouldPolishOnFinish = useCallback(
    () => polish && aiReady && !!stripMdImages(draft).trim(),
    [polish, aiReady, draft],
  )

  const auto = useMarkdownAutoSave({
    editing,
    value,
    draft,
    busy,
    setDraft,
    onSave,
    onExit: exitEdit,
    shouldPolishOnFinish,
    onPolish: () => {
      setUndo(null)
      void runPolish()
    },
    onAbort: abortPolish,
  })
  markSavedRef.current = auto.markSavedAndExit

  if (editing) {
    const visible = stripMdImages(draft)
    const markers = mdImageMarkerBlock(draft)
    const statusText = polishing
      ? t('正在整理格式，完成后自动保存')
      : paste.uploading > 0
        ? t('图片上传中…')
        : auto.saveState === 'pending'
          ? t('自动保存中…')
          : auto.saveState === 'saved'
            ? t('已自动保存')
            : t('编辑即自动保存 · 支持 Markdown，可直接粘贴或拖入图片')
    return (
      <div className="col" tabIndex={-1} style={{ outline: 'none' }} onBlur={auto.onContainerBlur}>
        <input
          ref={descPicker}
          type="file"
          accept="image/*"
          multiple
          hidden
          onChange={(e) => {
            auto.onFilePicked()
            const files = Array.from(e.target.files ?? [])
            e.target.value = ''
            if (files.length) paste.add(files)
          }}
        />
        <MentionTextarea
          textareaRef={box}
          className={`textarea${polishing ? ' polishing' : ''}`}
          style={{ minHeight: 160 }}
          autoFocus
          readOnly={polishing}
          value={visible}
          onChange={(next) => setDraft(mergeVisibleAndMarkers(next, markers))}
          onKeyDown={auto.onEditorKeyDown}
          onPaste={paste.onPaste}
          onDrop={paste.onDrop}
          onSlashImage={() => auto.openFilePicker(descPicker.current)}
          placeholder={t('添加描述，或输入 / 插入格式与操作；引用问题可直接写编号（如 ELEC-12）')}
        />
        <MarkdownImageThumbs text={draft} onRemove={(raw) => setDraft((prev) => removeMdImage(prev, raw))} />
        <div className="row wrap">
          <button
            type="button"
            className="btn sm"
            onClick={auto.cancelEdit}
          >
            {t('取消')}
          </button>
          {ai.data?.enabled && (
            <AiPolishToggle
              checked={polish}
              disabled={!aiReady || polishing}
              ready={aiReady}
              title={aiReady ? t('保存时由 AI 把描述整理为规范美观的 Markdown，内容不变') : (ai.data.error ?? t('AI 不可用'))}
              onToggle={setPolish}
            />
          )}
          {busy ? <Spinner /> : null}
          <span className="tiny faint">{statusText}</span>
        </div>
      </div>
    )
  }
  return (
    <>
      <div className={`inline-edit ${editable ? '' : 'readonly'}`} style={{ display: 'block', whiteSpace: value ? undefined : 'nowrap', maxWidth: '100%' }} onClick={(e) => editable && !(e.target as HTMLElement).closest('a') && setEditing(true)}>
        {value ? <Markdown text={value} onIssueClick={onIssueClick} /> : <span className="placeholder">{editable ? t('添加描述…') : t('无描述')}</span>}
      </div>
      {showUndo !== null && (
        <div className="ai-polished tiny">
          ✦ {t('已由 AI 美化')}
          <button
            type="button"
            onClick={() => {
              if (undo !== null) {
                onSave(undo)
                setUndo(null)
              } else if (externalUndo !== null && onExternalUndo) {
                onExternalUndo(externalUndo)
              }
              onClearExternalUndo?.()
            }}
          >
            {t('撤销')}
          </button>
        </div>
      )}
    </>
  )
}

/** Markdown field whose screenshots are pasted, dropped or picked and stored as attachments. */
function AttachmentMarkdownEditor({ issueKey, value, editable, onSave, onIssueClick, placeholder, polishTitle, polishKind = 'description' }: {
  issueKey: string
  value: string
  editable: boolean
  onSave: (v: string) => void
  onIssueClick: (key: string) => void
  placeholder?: string
  polishTitle?: string
  polishKind?: 'description' | 'scheme' | 'delivery'
}) {
  const { toast } = useApp()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const [pending, setPending] = useState<File[]>([])
  const [polish, setPolish] = useState(() => localStorage.getItem(POLISH_KEY) === '1')
  const [polishing, setPolishing] = useState(false)
  const [undo, setUndo] = useState<string | null>(null)
  const abort = useRef<AbortController | null>(null)
  const box = useRef<HTMLTextAreaElement>(null)
  const picker = useRef<HTMLInputElement>(null)
  const paste = useAttachmentPaste(issueKey, box, setDraft)
  const ai = useQuery({
    queryKey: ['agent-status'],
    queryFn: () => api.get<{ enabled: boolean; reachable: boolean; error?: string }>('/api/agent/status'),
    staleTime: 300_000,
    enabled: editing && polishTitle !== undefined,
  })
  const aiReady = !!ai.data?.enabled && ai.data.reachable
  const busy = paste.uploading > 0 || polishing
  const markSavedRef = useRef<(text: string) => void>(() => {})

  useEffect(() => () => abort.current?.abort(), [])
  useEffect(() => {
    if (editing && pending.length && box.current) {
      paste.add(pending)
      setPending([])
    }
  }, [editing, pending, paste])

  const runPolish = useCallback(async () => {
    const original = draft
    const controller = new AbortController()
    abort.current = controller
    setPolishing(true)
    let out = ''
    let failure: string | null = null
    try {
      await streamPost(
        '/api/agent/polish',
        { text: original, title: polishTitle ?? '', kind: polishKind },
        (ev) => {
          if (ev.type === 'delta') {
            out += ev.text ?? ''
            setDraft(out)
          }
          if (ev.type === 'error') failure = ev.message ? tServer(ev.message) : t('AI 不可用')
        },
        controller.signal,
      )
      const result = unfence(out)
      if (!failure && !result) failure = t('AI 未返回内容')
      if (failure) throw new Error(failure)
      setUndo(original)
      markSavedRef.current(result)
      toast(t('已用 AI 美化并保存'), 'success')
    } catch (err) {
      setDraft(original)
      if (!controller.signal.aborted) toast(t('AI 美化失败，未保存：{error}', { error: (err as Error).message }), 'error')
    } finally {
      abort.current = null
      setPolishing(false)
    }
  }, [draft, polishTitle, polishKind, toast])

  const exitEdit = useCallback(() => setEditing(false), [])
  const abortPolish = useCallback(() => abort.current?.abort(), [])
  const shouldPolishOnFinish = useCallback(
    () => polish && aiReady && polishTitle !== undefined && !!stripMdImages(draft).trim(),
    [polish, aiReady, polishTitle, draft],
  )

  const auto = useMarkdownAutoSave({
    editing,
    value,
    draft,
    busy,
    setDraft,
    onSave,
    onExit: exitEdit,
    shouldPolishOnFinish,
    onPolish: () => void runPolish(),
    onAbort: abortPolish,
  })
  markSavedRef.current = auto.markSavedAndExit

  const fileInput = (
    <input ref={picker} type="file" accept="image/*" multiple hidden onChange={(e) => {
      auto.onFilePicked()
      const files = Array.from(e.target.files ?? [])
      e.target.value = ''
      if (!files.length) return
      setPending(files)
      setEditing(true)
    }} />
  )
  const addButton = (
    <button type="button" className="btn sm" onClick={() => auto.openFilePicker(picker.current)}>
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="5" width="18" height="15" rx="2" /><circle cx="12" cy="12.5" r="3.5" /><path d="M8.5 5l1.5-2h4l1.5 2" /></svg>
      {t('添加截图')}
    </button>
  )

  if (editing) {
    const visible = stripMdImages(draft)
    const markers = mdImageMarkerBlock(draft)
    const statusText = polishing
      ? t('正在整理格式，完成后自动保存')
      : paste.uploading > 0
        ? t('图片上传中…')
        : auto.saveState === 'pending'
          ? t('自动保存中…')
          : auto.saveState === 'saved'
            ? t('已自动保存')
            : t('编辑即自动保存 · 截图会同时保存为附件')
    return (
      <div className="col" tabIndex={-1} style={{ outline: 'none' }} onBlur={auto.onContainerBlur}>
        {fileInput}
        <textarea
          ref={box}
          className={`textarea${polishing ? ' polishing' : ''}`}
          style={{ minHeight: 110 }}
          autoFocus
          readOnly={polishing}
          value={visible}
          onChange={(e) => setDraft(mergeVisibleAndMarkers(e.target.value, markers))}
          onKeyDown={auto.onEditorKeyDown}
          onPaste={paste.onPaste}
          onDrop={paste.onDrop}
          placeholder={placeholder ?? t('支持 Markdown；可直接粘贴或拖入截图')}
        />
        <MarkdownImageThumbs text={draft} onRemove={(raw) => setDraft((prev) => removeMdImage(prev, raw))} />
        <div className="row wrap">
          <button type="button" className="btn sm" onClick={auto.cancelEdit}>
            {t('取消')}
          </button>
          {addButton}
          {polishTitle !== undefined && ai.data?.enabled && (
            <AiPolishToggle
              checked={polish}
              disabled={!aiReady || polishing}
              ready={aiReady}
              title={aiReady ? t('保存时由 AI 整理为规范美观的 Markdown，内容不变') : (ai.data.error ?? t('AI 不可用'))}
              onToggle={setPolish}
            />
          )}
          {busy ? <Spinner /> : null}
          <span className="tiny faint">{statusText}</span>
        </div>
      </div>
    )
  }
  return (
    <div className="col" style={{ gap: 6, alignItems: 'flex-start' }}>
      {fileInput}
      <div className={`inline-edit ${editable ? '' : 'readonly'}`}
        style={{ display: 'block', whiteSpace: value ? undefined : 'nowrap', maxWidth: '100%' }}
        onClick={(e) => editable && !(e.target as HTMLElement).closest('a, img') && setEditing(true)}>
        {value ? <Markdown text={value} onIssueClick={onIssueClick} /> : <span className="placeholder">{editable ? t('点击填写') : '—'}</span>}
      </div>
      {editable && <div className="row">{addButton}</div>}
      {undo !== null && (
        <div className="ai-polished tiny">
          ✦ {t('已由 AI 美化')}
          <button type="button" onClick={() => { onSave(undo); setUndo(null) }}>{t('撤销')}</button>
        </div>
      )}
    </div>
  )
}

function ChildrenBlock({
  issue,
  project,
  adding,
  setAdding,
  onMoveParent,
}: {
  issue: IssueDetail
  project?: ProjectDetail
  adding: boolean
  setAdding: (v: boolean) => void
  onMoveParent: (statusId: number) => void
}) {
  const { openIssue, openCreate, toast } = useApp()
  const invalidate = useInvalidateIssues()
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState<number | 'new' | null>(null)
  const isEpic = issue.type === 'epic'
  if (issue.children.length === 0 && !adding) return null
  const done = issue.children.filter((c) => isCompleted(c.status)).length
  const total = issue.children.length
  const allDone = !isEpic && total > 0 && done === total && !isClosed(issue.status)
  const finish = allDone
    ? issue.transitions.find((s) => isCompleted(s)) ?? issue.transitions.find((s) => !isClosed(s))
    : undefined

  const toggle = async (child: Issue, target: Status) => {
    setBusy(child.id)
    try {
      const result = await api.patch<IssueDetail>(`/api/issues/${child.key}`, { status_id: target.id })
      invalidate()
      const message = lastSubtaskMessage(result)
      if (message) toast(message, 'success')
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(null)
    }
  }

  const create = async () => {
    if (!title.trim()) return
    setBusy('new')
    try {
      await api.post('/api/issues', { project: issue.project_key, parent_id: issue.id, title: title.trim() })
      setTitle('')
      invalidate()
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="card" style={{ marginBottom: 14 }}>
      <div className="card-header">
        <h3>{isEpic ? t('史诗下的工作') : t('子任务清单')}</h3>
        {total > 0 && (
          <>
            <span className="small muted">{t('{done}/{total} 已完成', { done, total })}</span>
            <div className="bar-track" style={{ width: 120 }}>
              <div className="bar-fill" style={{ width: `${(done / total) * 100}%`, background: 'var(--success)' }} />
            </div>
          </>
        )}
        <span className="spacer" />
        {!isEpic && !adding && (
          <button className="btn ghost sm" onClick={() => setAdding(true)}>
            {t('＋ 子任务')}
          </button>
        )}
      </div>
      {allDone && (
        <div className="subtask-done-banner">
          <span>✓ {t('子任务已全部完成')}</span>
          {finish && (
            <button className="btn primary sm" onClick={() => onMoveParent(finish.id)}>
              {t('流转到「{name}」', { name: t(finish.name) })}
            </button>
          )}
        </div>
      )}
      {issue.children.length === 0 && isEpic && (
        <div className="small faint" style={{ padding: '10px 16px' }}>{t('还没有归属此史诗的工作')}</div>
      )}
      {issue.children.map((child) => {
        const target = !isEpic && child.type === 'subtask' && project ? checkboxTarget(project, child.status) : undefined
        const childDone = isCompleted(child.status)
        return (
          <div key={child.id} className={`issue-row subtask-row ${childDone ? 'done' : ''}`} onClick={() => openIssue(child.key)}>
            {!isEpic && (
              <input
                type="checkbox"
                className="subtask-check"
                checked={busy === child.id ? !childDone : childDone}
                disabled={!target || busy === child.id}
                title={target ? t('标记为「{name}」', { name: t(target.name) }) : t('当前状态下不能直接勾选')}
                onClick={(e) => e.stopPropagation()}
                onChange={() => target && toggle(child, target)}
              />
            )}
            <TypeIcon type={child.type} />
            <span className="issue-key">{child.key}</span>
            <span className="title">{child.title}</span>
            {child.due_date && <span className={`tiny ${!childDone && child.due_date < new Date().toISOString().slice(0, 10) ? 'err-text' : 'faint'}`}>{fmtDate(child.due_date)}</span>}
            <PriorityIcon priority={child.priority} />
            <Avatar user={child.assignee} size={22} />
            <StatusPill status={child.status} />
          </div>
        )
      })}
      {adding && !isEpic && (
        <div className="subtask-add">
          <div className="subtask-add-row">
            <input
              className="input sm"
              autoFocus
              value={title}
              placeholder={t('输入子任务标题')}
              title={t('留空则沿用父任务的截止日期、冲刺和机台')}
              disabled={busy === 'new'}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') create()
                if (e.key === 'Escape') {
                  setTitle('')
                  setAdding(false)
                }
              }}
            />
            <button className="btn primary sm" disabled={!title.trim() || busy === 'new'} onClick={create}>
              {busy === 'new' ? t('创建中…') : t('创建')}
            </button>
            <button
              className="btn sm"
              disabled={busy === 'new'}
              title={t('打开完整创建弹窗，可填负责人、优先级、截止日期等')}
              onClick={() => openCreate({ project: issue.project_key!, parent_id: issue.id, type: 'subtask', title: title.trim() || undefined })}
            >
              {t('完整表单')}
            </button>
            <button
              className="btn ghost sm"
              disabled={busy === 'new'}
              onClick={() => {
                setTitle('')
                setAdding(false)
              }}
            >
              {t('取消')}
            </button>
          </div>
          <div className="tiny faint subtask-add-hint">{t('回车快速创建；需要负责人、优先级等请用完整表单')}</div>
        </div>
      )}
    </div>
  )
}

function ParentPicker({ issue, disabled, onChange }: { issue: IssueDetail; disabled: boolean; onChange: (parentId: number | null) => void }) {
  const [q, setQ] = useState('')
  const candidates = useQuery({
    queryKey: ['parent-candidates', issue.key, q],
    queryFn: () => api.get<{ issues: (IssueRef & { status: Status })[] }>(`/api/issues/${issue.key}/parent-candidates?q=${encodeURIComponent(q)}`),
    enabled: !disabled,
    staleTime: 30_000,
  })
  const confirmAndSet = (parentId: number) => {
    if (parentId === 0) {
      if (window.confirm(t('移除父任务后，{key} 将变为普通任务，确定吗？', { key: issue.key }))) onChange(null)
      return
    }
    if (issue.type !== 'subtask' && !window.confirm(t('设置父任务后，{key} 将变为子任务，确定吗？', { key: issue.key }))) return
    onChange(parentId)
  }
  return (
    <div className="parent-picker">
      <Dropdown
        disabled={disabled}
        trigger={
          <span className="inline-edit">
            {issue.parent ? (
              <span className="ellipsis">
                <span className="issue-key">{issue.parent.key}</span> {issue.parent.title}
              </span>
            ) : (
              <span className="placeholder">{t('无')}</span>
            )}
          </span>
        }
        header={
          <input
            className="input sm"
            style={{ margin: '2px 2px 6px', width: 'calc(100% - 4px)' }}
            autoFocus
            value={q}
            placeholder={t('搜索编号或标题')}
            onChange={(e) => setQ(e.target.value)}
            onClick={(e) => e.stopPropagation()}
          />
        }
        options={[
          ...(issue.parent ? [{ value: 0, label: t('移除父任务') }] : []),
          ...(candidates.data?.issues ?? [])
            .filter((c) => c.id !== issue.parent?.id)
            .map((c) => ({ value: c.id, label: <span className="ellipsis" style={{ maxWidth: 280 }}><TypeIcon type={c.type} /> <span className="issue-key">{c.key}</span> {c.title}</span> })),
        ]}
        onSelect={confirmAndSet}
      />
    </div>
  )
}

function LinksBlock({ issue }: { issue: IssueDetail }) {
  const { openIssue, toast } = useApp()
  const client = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [target, setTarget] = useState('')
  const [linkType, setLinkType] = useState<'relates' | 'blocks' | 'blocked_by' | 'duplicates'>('relates')
  const linkOptions = [
    { value: 'relates' as const, label: t('关联'), help: t('两项工作彼此有关，但不会形成排期先后关系。') },
    { value: 'blocks' as const, label: t('阻塞'), help: t('当前问题必须先完成；当前问题延期时，对方的预测日期会被推迟。') },
    { value: 'blocked_by' as const, label: t('被阻塞'), help: t('对方必须先完成；对方延期时，当前问题的预测日期会被推迟。') },
    { value: 'duplicates' as const, label: t('重复于'), help: t('当前问题与对方重复；通常把对方作为继续保留的原问题。') },
  ]
  const targetLabel = target.trim().toUpperCase() || t('输入的问题')
  const refresh = () => client.invalidateQueries({ queryKey: ['issue', issue.key] })
  const invalidateIssues = useInvalidateIssues()
  const add = async () => {
    const key = target.trim()
    const blockedBy = linkType === 'blocked_by'
    const sourceKey = blockedBy ? key : issue.key
    const targetKey = blockedBy ? issue.key : key
    const post = (reparent: boolean) =>
      api.post<IssueDetail>(`/api/issues/${sourceKey}/links`, {
        target_key: targetKey,
        link_type: blockedBy ? 'blocks' : linkType,
        reparent,
      })
    try {
      let result: IssueDetail
      try {
        result = await post(false)
      } catch (err) {
        if (!(err instanceof ApiError && err.code === 'subtask_has_parent') || !window.confirm(err.message)) throw err
        result = await post(true)
      }
      setTarget('')
      setAdding(false)
      if (result.link_result === 'subtask_added') {
        toast(t('{key} 是子任务，已加入本任务的子任务清单', { key: result.children.find((c) => c.key.toUpperCase() === key.toUpperCase())?.key ?? key }))
        invalidateIssues()
      } else if (result.link_result === 'already_subtask') {
        toast(t('{key} 已经是本任务的子任务', { key }))
      }
      refresh()
    } catch (err) {
      if (err instanceof ApiError && err.code === 'subtask_has_parent') return
      toast((err as Error).message, 'error')
    }
  }
  return (
    <div className="card" style={{ marginBottom: 14 }}>
      <div className="card-header">
        <h3>{t('关联问题')}</h3>
        <button className="btn ghost sm" onClick={() => setAdding((a) => !a)}>
          {t('＋ 关联')}
        </button>
      </div>
      {adding && (
        <div className="issue-link-builder">
          <div className="issue-link-types" role="radiogroup" aria-label={t('关联类型')}>
            {linkOptions.map((option) => (
              <label className={`issue-link-type${linkType === option.value ? ' selected' : ''}`} key={option.value}>
                <input
                  type="radio"
                  name={`link-type-${issue.id}`}
                  value={option.value}
                  checked={linkType === option.value}
                  onChange={() => setLinkType(option.value)}
                />
                <span>{option.label}</span>
                <InfoTip mark="?" text={option.help} />
              </label>
            ))}
          </div>
          <div className="issue-link-entry">
            <input className="input sm" placeholder={t('问题编号，如 ELEC-12')} value={target} onChange={(e) => setTarget(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && add()} />
            <button className="btn primary sm" onClick={add} disabled={!target.trim()}>
              {t('添加')}
            </button>
          </div>
          <div className="issue-link-preview">
            <span>{linkType === 'blocked_by' ? targetLabel : issue.key}</span>
            <b>
              {linkType === 'relates' ? `↔ ${t('关联')} ↔` : `— ${t(linkType === 'duplicates' ? '重复于' : '阻塞')} →`}
            </b>
            <span>{linkType === 'blocked_by' ? issue.key : targetLabel}</span>
          </div>
        </div>
      )}
      {issue.links.length === 0 && !adding && <div className="small faint" style={{ padding: '10px 16px' }}>{t('暂无关联')}</div>}
      {issue.links.map((link) => (
        <div key={link.id} className="issue-row">
          <span className="issue-link-current">{t('当前')} {issue.key}</span>
          <span className={`issue-link-direction ${link.link_type}`}>
            {link.label === '被阻塞' || link.label === '被重复'
              ? `← ${linkLabel(link.label === '被阻塞' ? '阻塞' : '重复于')}`
              : link.link_type === 'relates'
                ? `↔ ${linkLabel(link.label)}`
                : `${linkLabel(link.label)} →`}
          </span>
          <TypeIcon type={link.issue.type} />
          <a className="issue-key" onClick={() => openIssue(link.issue.key)} style={{ cursor: 'pointer' }}>
            {link.issue.key}
          </a>
          <span className="title" onClick={() => openIssue(link.issue.key)}>
            {link.issue.title}
          </span>
          <StatusPill status={link.issue.status} />
          <button
            className="btn ghost sm icon"
            onClick={async () => {
              await api.del(`/api/links/${link.id}`)
              refresh()
            }}
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}

function attachmentBadge(filename: string, contentType: string): string {
  const ext = filename.split('.').pop()?.toUpperCase() || ''
  if (ext && ext.length <= 5) return ext
  if (contentType.startsWith('image/')) return 'IMG'
  if (contentType === 'application/pdf') return 'PDF'
  return 'FILE'
}

function attachmentKind(contentType: string): 'image' | 'pdf' | 'file' {
  if (contentType.startsWith('image/')) return 'image'
  if (contentType === 'application/pdf' || contentType.startsWith('application/pdf;')) return 'pdf'
  return 'file'
}

function AttachmentsBlock({ issue }: { issue: IssueDetail }) {
  const { toast, user } = useApp()
  const client = useQueryClient()
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [zoom, setZoom] = useState<{ src: string; alt: string } | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const upload = async (files: FileList | null) => {
    if (!files?.length) return
    setBusy(true)
    try {
      for (const file of Array.from(files)) {
        const form = new FormData()
        form.append('file', file)
        await api.upload(`/api/issues/${issue.key}/attachments`, form)
      }
      client.invalidateQueries({ queryKey: ['issue', issue.key] })
      toast(t('上传完成'), 'success')
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(false)
      if (input.current) input.current.value = ''
    }
  }
  return (
    <div className="card" style={{ marginBottom: 14 }}>
      <div className="card-header">
        <h3>{t('附件')} {issue.attachments.length > 0 && <span className="count">{issue.attachments.length}</span>}</h3>
        {busy && <Spinner />}
      </div>
      <div style={{ padding: '12px 16px' }} className="col">
        <div className="grid grid-2" style={{ gap: 8 }}>
          {issue.attachments.map((att) => {
            const kind = attachmentKind(att.content_type)
            const href = kind === 'file' ? att.url : `${att.url}?inline=true`
            return (
              <div key={att.id} className="attachment">
                {kind === 'image' ? (
                  <button type="button" className="thumb zoomable" title={t('点击放大')} onClick={() => setZoom({ src: href, alt: att.filename })}>
                    <img src={href} alt={att.filename} />
                  </button>
                ) : (
                  <div className={`thumb file-badge${kind === 'pdf' ? ' pdf' : ''}`}>{attachmentBadge(att.filename, att.content_type)}</div>
                )}
                <div style={{ minWidth: 0, flex: 1 }}>
                  <a
                    href={href}
                    className="ellipsis"
                    style={{ display: 'block' }}
                    target={kind === 'file' ? undefined : '_blank'}
                    rel={kind === 'file' ? undefined : 'noreferrer'}
                    download={kind === 'file' ? att.filename : undefined}
                  >
                    {att.filename}
                  </a>
                  <div className="tiny faint">
                    {fmtSize(att.size)} · {att.uploader?.display_name} · {timeAgo(att.created_at)}
                  </div>
                </div>
                {(att.uploader?.id === user.id || user.is_admin) && (
                  <button
                    className="btn ghost sm icon"
                    onClick={async () => {
                      await api.del(`/api/attachments/${att.id}`)
                      client.invalidateQueries({ queryKey: ['issue', issue.key] })
                    }}
                  >
                    ✕
                  </button>
                )}
              </div>
            )
          })}
        </div>
        <div
          className={`dropzone ${over ? 'over' : ''}`}
          onClick={() => input.current?.click()}
          onDragOver={(e) => {
            e.preventDefault()
            setOver(true)
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault()
            setOver(false)
            upload(e.dataTransfer.files)
          }}
        >
          <div>{t('拖拽文件到此处，或点击上传')}</div>
          <div className="tiny faint" style={{ marginTop: 4 }}>{t('支持 PDF、Word、Excel、PPT、压缩包、文本和图片等')}</div>
          <input ref={input} type="file" multiple hidden onChange={(e) => upload(e.target.files)} />
        </div>
      </div>
      {zoom && <Lightbox src={zoom.src} alt={zoom.alt} onClose={() => setZoom(null)} />}
    </div>
  )
}

function ActivityBlock({ issue, project }: { issue: IssueDetail; project?: ProjectDetail }) {
  const [tab, setTab] = useState<'comments' | 'history'>('comments')
  const customLabels = Object.fromEntries(
    (project?.profile.custom_fields ?? []).map((field) => [field.key, customFieldLabel(field)]),
  )
  return (
    <div className="card card-pad">
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { value: 'comments', label: `${t('评论')} ${issue.comment_count || ''}` },
          { value: 'history', label: t('历史') },
        ]}
      />
      {tab === 'comments' ? <Comments issue={issue} /> : <History issueKey={issue.key} customLabels={customLabels} />}
    </div>
  )
}

/** People relevant to an issue, most involved first, without the current user. */
function issuePeople(issue: IssueDetail, comments: Comment[], me: number): MentionPerson[] {
  const out: MentionPerson[] = []
  const seen = new Set<number>([me])
  const add = (user: UserBrief | null | undefined, role: string) => {
    if (user && !seen.has(user.id)) {
      seen.add(user.id)
      out.push({ user, role })
    }
  }
  add(issue.assignee, t('负责人'))
  add(issue.reporter, t('报告人'))
  add(issue.tester, t('测试人员'))
  add(issue.software_owner, t('软件负责人'))
  for (const c of [...comments].reverse()) add(c.author, t('评论者'))
  for (const w of issue.watchers) add(w, t('关注者'))
  return out
}

function Comments({ issue }: { issue: IssueDetail }) {
  const issueKey = issue.key
  const { user, toast, openIssue } = useApp()
  const client = useQueryClient()
  const { data } = useQuery({
    queryKey: ['comments', issueKey],
    queryFn: () => api.get<{ comments: Comment[] }>(`/api/issues/${issueKey}/comments`),
  })
  const people = issuePeople(issue, data?.comments ?? [], user.id)
  const [editing, setEditing] = useState<number | null>(null)
  const [editText, setEditText] = useState('')
  const refresh = () => {
    client.invalidateQueries({ queryKey: ['comments', issueKey] })
    client.invalidateQueries({ queryKey: ['issue', issueKey] })
  }
  const post = useMutation({
    mutationFn: (body: string) => api.post(`/api/issues/${issueKey}/comments`, { body }),
    onSuccess: refresh,
    onError: (err) => toast((err as Error).message, 'error'),
  })
  return (
    <div>
      <MentionComposer issueKey={issueKey} people={people} onSubmit={(body) => post.mutateAsync(body)} busy={post.isPending} />
      <div style={{ marginTop: 8 }}>
        {data?.comments.length === 0 && <div className="faint small" style={{ padding: '12px 0' }}>{t('还没有评论')}</div>}
        {[...(data?.comments ?? [])].reverse().map((c) => (
          <div key={c.id} className="comment">
            {c.author ? (
              <PersonHover user={c.author}>
                <Avatar user={c.author} size={30} />
              </PersonHover>
            ) : (
              <Avatar user={null} size={30} />
            )}
            <div className="comment-body">
              <div className="row small">
                {c.author ? (
                  <PersonHover user={c.author}>
                    <b>{c.author.display_name}</b>
                  </PersonHover>
                ) : (
                  <b>{t('系统')}</b>
                )}
                <span className="faint">{timeAgo(c.created_at)}</span>
                {c.updated_at && <span className="faint tiny">{t('（已编辑）')}</span>}
                <span className="spacer" />
                {c.author?.id === user.id && editing !== c.id && (
                  <>
                    <button
                      className="btn ghost sm"
                      onClick={() => {
                        setEditing(c.id)
                        setEditText(c.body)
                      }}
                    >
                      {t('编辑')}
                    </button>
                    <ConfirmButton
                      className="btn ghost sm danger"
                      message={t('删除这条评论？')}
                      onConfirm={async () => {
                        await api.del(`/api/comments/${c.id}`)
                        refresh()
                      }}
                    >
                      {t('删除')}
                    </ConfirmButton>
                  </>
                )}
              </div>
              {editing === c.id ? (
                <div className="col" style={{ marginTop: 6 }}>
                  <MentionTextarea value={editText} onChange={setEditText} people={people} autoFocus />
                  <div className="row">
                    <button
                      className="btn primary sm"
                      onClick={async () => {
                        await api.put(`/api/comments/${c.id}`, { body: editText })
                        setEditing(null)
                        refresh()
                      }}
                    >
                      {t('保存')}
                    </button>
                    <button className="btn sm" onClick={() => setEditing(null)}>
                      {t('取消')}
                    </button>
                  </div>
                </div>
              ) : (
                <div style={{ marginTop: 4 }}>
                  <Markdown text={c.body} onIssueClick={openIssue} />
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export function MentionComposer({ issueKey, people = [], onSubmit, busy, placeholder = t('添加评论…  输入 @ 提及同事，或 / 插入格式；Ctrl+Enter 发送') }: { issueKey?: string; people?: MentionPerson[]; onSubmit: (body: string) => Promise<unknown>; busy?: boolean; placeholder?: string }) {
  const [text, setText] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)
  const commentPicker = useRef<HTMLInputElement>(null)
  const paste = useAttachmentPaste(issueKey, ref, setText)
  const quickMention = (username: string) => {
    const el = ref.current
    const next = insertMention(text, el ? el.selectionStart : text.length, username)
    setText(next.text)
    requestAnimationFrame(() => {
      if (!el) return
      el.focus()
      el.selectionStart = el.selectionEnd = next.caret
    })
  }
  const submit = async () => {
    if (!text.trim()) return
    await onSubmit(text)
    setText('')
  }
  return (
    <div>
      <input
        ref={commentPicker}
        type="file"
        accept="image/*"
        multiple
        hidden
        onChange={(e) => {
          const files = Array.from(e.target.files ?? [])
          e.target.value = ''
          if (files.length) paste.add(files)
        }}
      />
      <MentionTextarea
        textareaRef={ref}
        style={{ minHeight: 70 }}
        placeholder={placeholder}
        value={stripMdImages(text)}
        onChange={(next) => setText(mergeVisibleAndMarkers(next, mdImageMarkerBlock(text)))}
        people={people}
        onSlashImage={issueKey ? () => commentPicker.current?.click() : undefined}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && paste.uploading === 0) submit()
        }}
        onPaste={paste.onPaste}
        onDrop={paste.onDrop}
      />
      <MarkdownImageThumbs text={text} onRemove={(raw) => setText((prev) => removeMdImage(prev, raw))} />
      <MentionChips people={people} text={text} onPick={quickMention} />
      <div className="row" style={{ marginTop: 6 }}>
        {paste.uploading > 0 && (
          <>
            <Spinner />
            <span className="tiny faint">{t('图片上传中…')}</span>
          </>
        )}
        <span className="spacer" />
        <button className="btn primary sm" disabled={busy || !text.trim() || paste.uploading > 0} onClick={submit}>
          {t('发送')}
        </button>
      </div>
    </div>
  )
}

function History({ issueKey, customLabels }: { issueKey: string; customLabels?: Record<string, string> }) {
  const { data, isLoading } = useQuery({
    queryKey: ['activity', issueKey],
    queryFn: () => api.get<{ activity: Activity[] }>(`/api/issues/${issueKey}/activity`),
  })
  if (isLoading) return <Spinner center />
  return (
    <div>
      {data?.activity.map((a) => (
        <div key={a.id} className="activity-item" style={{ padding: '9px 0' }}>
          {a.actor ? (
            <PersonHover user={a.actor}>
              <Avatar user={a.actor} size={24} />
            </PersonHover>
          ) : (
            <Avatar user={null} size={24} />
          )}
          <div style={{ flex: 1 }}>
            <ActivityText activity={a} customLabels={customLabels} />
            <div className="tiny faint">{fmtDateTime(a.created_at)}</div>
          </div>
        </div>
      ))}
    </div>
  )
}
