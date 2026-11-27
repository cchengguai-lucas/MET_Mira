import type { Issue, IssueType, ProjectDetail, Section, Status, UserFull } from './types'

/** Mirror of backend workflow.REJECTED — done-category but never "completed". */
export const REJECTED = '已驳回'

/** Mirror of backend workflow.REOPENED — rework status after fail / done. */
export const REOPENED = '重新打开'

/** Mirror of backend workflow.TEST_PASSED — holding status after tester pass. */
export const TEST_PASSED = '测试通过'

export function isRejected(status: Pick<Status, 'name'> | null | undefined): boolean {
  return !!status && status.name === REJECTED
}

export function isClosed(status: Pick<Status, 'category'> | null | undefined): boolean {
  return !!status && status.category === 'done'
}

/** Successfully finished (excludes rejected). */
export function isCompleted(status: Pick<Status, 'category' | 'name'> | null | undefined): boolean {
  return isClosed(status) && !isRejected(status)
}

function issueUserId(issue: Issue, field: string): number | undefined {
  switch (field) {
    case 'reporter_id':
      return issue.reporter?.id
    case 'assignee_id':
      return issue.assignee?.id
    case 'tester_id':
      return issue.tester?.id
    case 'software_owner_id':
      return issue.software_owner?.id
  }
}

/** Mirror of backend permissions.editable_sections for list-view inline editing. */
export function editableSections(project: ProjectDetail, user: UserFull, issue: Issue): Set<Section> {
  const profile = project.profile.sections
  const all = ['general', ...profile.map((s) => s.key)] as Section[]
  if (project.is_admin) return new Set(all)
  const roles = new Set(project.my_roles)
  const sections = new Set<Section>()
  const involved = [issue.reporter?.id, issue.assignee?.id, issue.tester?.id, issue.software_owner?.id].includes(user.id)
  if (roles.size || involved) sections.add('general')
  if (issue.type !== 'requirement') return sections.has('general') ? new Set(all) : sections
  for (const section of profile) {
    if ((section.role && roles.has(section.role)) || section.issue_users.some((f) => issueUserId(issue, f) === user.id)) {
      sections.add(section.key as Section)
    }
  }
  if (canParticipateScheme(project, user, issue)) sections.add('scheme')
  return sections
}

export function canParticipateScheme(project: ProjectDetail, user: UserFull, issue: Issue): boolean {
  if (issue.type !== 'requirement') return false
  if (!project.profile.sections.some((section) => section.key === 'scheme')) return false
  if (project.is_admin || user.is_admin) return true
  const roles = new Set(project.my_roles)
  return roles.has('electrical') || roles.has('process')
    || issue.software_owner?.id === user.id || issue.assignee?.id === user.id
}

function schemeAdmin(project: ProjectDetail, user: UserFull): boolean {
  return project.is_admin || user.is_admin
}

export function schemeSubmitted(issue: Issue): boolean {
  return issue.scheme_locked || !!issue.scheme_confirmed_note?.trim()
}

export function schemeVotesDone(issue: Issue): boolean {
  if (!issue.scheme_note?.trim()) return false
  if (issue.electrical_scheme_ok !== true || issue.process_scheme_ok !== true) return false
  if (issue.involves_software === true) return !!issue.software_owner && issue.software_scheme_ok === true
  return true
}

export function canEditSchemeField(project: ProjectDetail, user: UserFull, issue: Issue, field: string): boolean {
  if (issue.type !== 'requirement' || issue.status.name !== '方案讨论') return false
  if (!canParticipateScheme(project, user, issue)) return false
  const admin = schemeAdmin(project, user)
  const roles = new Set(project.my_roles)
  if (field === 'scheme_note') {
    if (issue.scheme_locked) return false
    return admin || roles.has('electrical') || issue.software_owner?.id === user.id || issue.assignee?.id === user.id
  }
  if (field === 'electrical_scheme_ok' || field === 'process_scheme_ok' || field === 'software_scheme_ok') {
    if (!schemeSubmitted(issue) || (issue.scheme_locked && schemeVotesDone(issue))) return false
    if (field === 'software_scheme_ok' && issue.involves_software !== true) return false
    if (admin) return true
    if (field === 'electrical_scheme_ok') return roles.has('electrical')
    if (field === 'process_scheme_ok') return roles.has('process')
    return issue.software_owner?.id === user.id
  }
  return false
}

export function canRequestScheme(project: ProjectDetail, user: UserFull, issue: Issue): boolean {
  if (issue.status.name !== '方案讨论' || issue.scheme_locked) return false
  const admin = schemeAdmin(project, user)
  const roles = new Set(project.my_roles)
  return admin || roles.has('electrical') || issue.software_owner?.id === user.id || issue.assignee?.id === user.id
}

/** Why the request button stays disabled. Empty when it can be clicked. */
export function schemeSubmitBlock(issue: Issue): string | null {
  if (!issue.scheme_note?.trim()) return '请先填写并保存方案说明'
  if (issue.involves_software === true && !issue.software_owner) return '请先指定软件负责人'
  return null
}

export function canSubmitScheme(project: ProjectDetail, user: UserFull, issue: Issue): boolean {
  return canRequestScheme(project, user, issue) && schemeSubmitBlock(issue) === null
}

export function canUnlockScheme(project: ProjectDetail, user: UserFull, issue: Issue): boolean {
  if (issue.status.name !== '方案讨论' || !issue.scheme_locked) return false
  return canParticipateScheme(project, user, issue)
}

function rulesFor(project: ProjectDetail, type: IssueType): Record<string, string[]> | undefined {
  const rules = project.workflows?.[type]
  return rules && Object.keys(rules).length ? rules : undefined
}

/** Mirror of backend workflow.flow_statuses: the statuses an issue type uses, in project order. */
export function flowStatuses(rules: Record<string, string[]> | undefined, statuses: Status[]): Status[] {
  if (!rules || !Object.keys(rules).length) return statuses
  const names = new Set([...Object.keys(rules), ...Object.values(rules).flat()])
  return statuses.filter((s) => names.has(s.name))
}

export function typeFlowStatuses(project: ProjectDetail, type: IssueType, statuses: Status[]): Status[] {
  return flowStatuses(rulesFor(project, type), statuses)
}

function verificationNames(project: ProjectDetail): Set<string> {
  const settings = project.project?.settings ?? {}
  const fail = settings.fail_return_status
  return new Set(
    [settings.test_status ?? '测试中', settings.pass_status ?? TEST_PASSED, fail || '测试未通过', '测试未通过']
      .filter((name): name is string => !!name),
  )
}

/** Testers (and the assigned tester, and project admins) move into testing / passed / failed. */
function canEnterVerification(
  project: ProjectDetail,
  issue: Partial<Pick<Issue, 'tester'>>,
  user: Pick<UserFull, 'id' | 'is_admin'> | undefined,
): boolean {
  if (!user) return true
  if (user.is_admin || project.is_admin) return true
  if (issue.tester?.id === user.id) return true
  const roles = new Set(
    (project.profile?.sections ?? [])
      .filter((section) => section.role && section.issue_users?.includes('tester_id'))
      .map((section) => section.role as string),
  )
  return (project.my_roles ?? []).some((role) => roles.has(role))
}

/** Mirror of backend workflow.allowed_targets (applies to admins too), for drag-time feedback. Includes the current status. */
export function allowedStatusIds(
  project: ProjectDetail,
  issue: Pick<Issue, 'type' | 'status'> & Partial<Pick<Issue, 'assignee' | 'tester'>>,
  statuses: Status[],
  user?: Pick<UserFull, 'id' | 'is_admin'>,
): Set<number> {
  const rules = rulesFor(project, issue.type)
  let ids: Set<number>
  if (rules && issue.status.name in rules) {
    const names = new Set(rules[issue.status.name])
    ids = new Set(statuses.filter((s) => names.has(s.name) || s.id === issue.status.id).map((s) => s.id))
  } else {
    const position = statuses.findIndex((s) => s.id === issue.status.id)
    if (position < 0) return new Set()
    const order = new Map(statuses.map((s, i) => [s.id, i]))
    const flow = flowStatuses(rules, statuses).filter((s) => s.id !== issue.status.id)
    const before = flow.filter((s) => order.get(s.id)! < position).slice(-1)
    const after = flow.filter((s) => order.get(s.id)! > position).slice(0, 1)
    ids = new Set([issue.status.id, ...before.map((s) => s.id), ...after.map((s) => s.id)])
  }
  // Leaving 测试通过 for done/rejected is assignee (or project admin) only.
  const passName = project.project.settings.pass_status ?? TEST_PASSED
  if (user && issue.status.name === passName) {
    const canAccept = project.is_admin || user.is_admin || issue.assignee?.id === user.id
    if (!canAccept) {
      for (const status of statuses) {
        if (status.name === REJECTED || (status.category === 'done' && status.name !== REJECTED)) {
          ids.delete(status.id)
        }
      }
    }
  }
  if (user && !canEnterVerification(project, issue, user)) {
    const gated = verificationNames(project)
    for (const status of statuses) {
      if (status.id !== issue.status.id && gated.has(status.name)) ids.delete(status.id)
    }
  }
  return ids
}

/** Mirror of backend workflow.test_result_editable: while under test, or in the pass / fail status the result led to. */
export function testResultEditable(project: ProjectDetail, issue: Pick<Issue, 'type' | 'status'>, statuses: Status[]): boolean {
  const settings = project.project.settings
  const test = statuses.find((s) => s.name === (settings.test_status ?? '测试中'))
  if (!test || !typeFlowStatuses(project, issue.type, statuses).some((s) => s.id === test.id)) return false
  if (issue.status.id === test.id) return true
  const outcomes = allowedStatusIds(project, { type: issue.type, status: test }, statuses)
  const passName = settings.pass_status ?? TEST_PASSED
  const fail = new Set(['测试未通过', settings.fail_return_status])
  return (
    outcomes.has(issue.status.id)
    && (issue.status.name === passName
      || fail.has(issue.status.name)
      || (issue.status.category === 'done' && issue.status.name !== REJECTED))
  )
}

/**
 * The happy path through `rules`: the longest route from the first status to the first done status
 * that never revisits a step. Rework loops (测试未通过 → 重新打开 → 研发中) and dead ends (已驳回)
 * cannot be part of it, so they become side branches. Ties go to the route that follows project order.
 */
function happyPath(rules: Record<string, string[]>, statuses: Status[]): Status[] {
  const goal = statuses.find((s) => s.category === 'done')
  if (!goal || !statuses.length) return []
  const byName = new Map(statuses.map((s) => [s.name, s]))
  const order = new Map(statuses.map((s, i) => [s.id, i]))
  let best: Status[] = []
  const walk = (path: Status[]) => {
    const last = path[path.length - 1]
    if (last.id === goal.id) {
      if (path.length > best.length) best = [...path]
      return
    }
    const next = (rules[last.name] ?? [])
      .map((name) => byName.get(name))
      .filter((s): s is Status => !!s && !path.includes(s))
      .sort((a, b) => order.get(a.id)! - order.get(b.id)!)
    next.forEach((s) => walk([...path, s]))
  }
  walk([statuses[0]])
  return best
}

/**
 * Split statuses for the flow diagram into the main path and side branches. With the type's rules the
 * main path is its happy path; without them it is every non-done status plus the first done status.
 */
export function flowLayout(statuses: Status[], rules?: Record<string, string[]>): { main: Status[]; branches: Status[] } {
  const path = rules && Object.keys(rules).length ? happyPath(rules, statuses) : []
  if (path.length > 1) return { main: path, branches: statuses.filter((s) => !path.includes(s)) }
  const firstDone = statuses.find((s) => s.category === 'done')
  return {
    main: statuses.filter((s) => s.category !== 'done' || s === firstDone),
    branches: statuses.filter((s) => s.category === 'done' && s !== firstDone),
  }
}

export function typeRules(project: ProjectDetail, type: IssueType): Record<string, string[]> | undefined {
  return rulesFor(project, type)
}
