export type IssueType = 'requirement' | 'bug' | 'task' | 'story' | 'epic' | 'subtask'
export type Priority = 'highest' | 'high' | 'medium' | 'low' | 'lowest'
/** Profile section keys; built-in templates use requester/electrical/…, AI uses basic/links/… */
export type Section = string

export interface UserBrief {
  id: number
  username: string
  display_name: string
  title: string | null
  email: string | null
  avatar_url?: string | null
  avatar_color?: string | null
  org_unit_id?: number | null
  org_path?: string | null
}

export interface UserFull extends UserBrief {
  department: string | null
  phone: string | null
  enabled: boolean
  is_admin: boolean
  can_create_projects: boolean
  auth_source: string
  org_unit_id: number | null
  org_path: string | null
  manager: UserBrief | null
  last_login_at: string | null
  synced_at: string | null
}

export interface Status {
  id: number
  name: string
  category: 'todo' | 'doing' | 'done'
  color: string
  sort_order: number
}

export interface MachineRef {
  id: number
  name: string
  group_id: number
  group: string | null
  active: boolean
}

export interface MachineGroup {
  id: number
  name: string
  sort_order: number
  machines: MachineRef[]
}

export interface IssueRef {
  id: number
  key: string
  title: string
  type: IssueType
}

export interface Issue {
  id: number
  lock_version: number
  key: string
  number: number
  project_id: number
  project_key: string | null
  type: IssueType
  title: string
  status: Status
  priority: Priority
  assignee: UserBrief | null
  reporter: UserBrief | null
  tester: UserBrief | null
  labels: string[]
  due_date: string | null
  story_points: number | null
  rank: number
  sprint_id: number | null
  epic: IssueRef | null
  parent: IssueRef | null
  machines: MachineRef[]
  project_name: string
  description?: string
  update_scope: string[]
  involves_software: boolean | null
  software_owner: UserBrief | null
  software_ready: boolean | null
  software_note: string
  software_jira_key: string
  software_test_wait?: string | null
  software_jira_snapshot: {
    assignee?: string | null
    tester?: string | null
    status?: string | null
    due_date?: string | null
    fetched_at?: string | null
    error?: string | null
  } | null
  software_jira_people?: {
    assignee: UserBrief | null
    tester: UserBrief | null
  }
  scheme_note: string
  electrical_scheme_ok: boolean | null
  electrical_scheme_by: UserBrief | null
  process_scheme_ok: boolean | null
  process_scheme_by: UserBrief | null
  software_scheme_ok: boolean | null
  software_scheme_by: UserBrief | null
  scheme_locked: boolean
  scheme_confirmed_note: string
  verify_method: string
  verify_criteria: string
  update_date: string | null
  test_passed: boolean | null
  remark: string
  custom_fields: Record<string, unknown>
  /** Resolved briefs for ``user`` custom fields (list/search payloads). */
  custom_field_users?: Record<string, UserBrief>
  created_at: string
  updated_at: string
  resolved_at: string | null
  sla_due_at: string | null
  sla_state: 'ok' | 'warning' | 'breached' | 'completed' | 'paused' | 'none'
  sla_started_at: string | null
  /** Sub-task progress on parents (board, backlog and list responses). */
  subtasks?: SubtaskProgress
}

export interface SubtaskProgress {
  done: number
  total: number
}

export interface Attachment {
  id: number
  filename: string
  size: number
  content_type: string
  uploader: UserBrief | null
  created_at: string
  url: string
}

export interface IssueLinkOut {
  id: number
  link_type: string
  label: string
  issue: IssueRef & { status: Status }
}

export interface IssueDetail extends Issue {
  description: string
  custom_field_users: Record<string, UserBrief>
  children: Issue[]
  links: IssueLinkOut[]
  watchers: UserBrief[]
  watching: boolean
  attachments: Attachment[]
  comment_count: number
  editable_sections: Section[]
  transitions: Status[]
  test_result_editable: boolean
  can_delete: boolean
  approval_requests: ApprovalRequest[]
  parent_progress: SubtaskProgress | null
  parent_info: (IssueRef & { status: Status; assignee: UserBrief | null }) | null
  link_result?: 'linked' | 'already_subtask' | 'subtask_added'
}

export interface ApprovalRequest {
  id: number
  lock_version: number
  issue_id: number
  target_status_id: number
  status: 'pending' | 'approved' | 'rejected' | 'cancelled'
  requirements: { role: string; count: number }[]
  decisions: { user_id: number; name: string; roles: string[]; decision: string; comment: string; at: string }[]
  comment: string
  requested_by_id: number | null
  created_at: string
  finished_at: string | null
}

export interface Comment {
  id: number
  issue_id: number
  author: UserBrief | null
  body: string
  created_at: string
  updated_at: string | null
}

export interface Activity {
  id: number
  actor: UserBrief | null
  action: string
  field: string | null
  old_value: string | null
  new_value: string | null
  meta: Record<string, unknown>
  created_at: string
  issue?: { key: string; title: string } | null
}

export interface Sprint {
  id: number
  lock_version: number
  project_id: number
  name: string
  goal: string
  state: 'future' | 'active' | 'closed'
  start_date: string | null
  end_date: string | null
  started_at: string | null
  completed_at: string | null
  stats: Record<string, unknown>
}

export type ProjectVisibility = 'public' | 'members'
export type ProjectWorkMode = 'delivery' | 'ongoing'

export function projectWorkMode(
  project: { settings?: { work_mode?: string } | null },
): ProjectWorkMode {
  return project.settings?.work_mode === 'ongoing' ? 'ongoing' : 'delivery'
}

export interface ProjectSettings {
  visibility?: ProjectVisibility
  /** delivery = finishable goal; ongoing = continuous / memo-style work. */
  work_mode?: ProjectWorkMode
  update_scope_options?: string[]
  /** Shared label catalog for the project; grown when issues use new labels. */
  label_options?: string[]
  transitions?: Record<string, Record<string, string[]>>
  /** Freeform metadata per status name (Jira-like status properties; not enforced at runtime). */
  status_properties?: Record<string, Record<string, string>>
  test_status?: string
  pass_status?: string
  fail_return_status?: string
  /** Status that triggers ready-for-test mail (default 开发完成). */
  dev_complete_status?: string
  transition_gates?: Record<string, {
    required_fields: string[]
    approvals: { role: string; count: number }[]
  }>
  sla_hours?: Record<Priority, number>
  sla_warning_hours?: number
  /** `business` counts only working hours on working days. */
  sla_calendar?: 'natural' | 'business'
  sla_business_hours?: { start: string; end: string; workdays: number[] }
  sla_holidays?: string[]
  sla_extra_workdays?: string[]
  /** Statuses in which the SLA clock stops. */
  sla_paused_statuses?: string[]
  archived_at?: string | null
  archived_by?: string | null
  /** Org unit paths whose members can be 软件负责人; empty means auto-detect "Software" units. */
  software_units?: string[]
}

export interface Project {
  id: number
  key: string
  name: string
  description: string
  color: string
  /** Built-in cartoon badge id; null/undefined falls back to key initials. */
  icon?: string | null
  /** Landscape cover served from `/api/projects/{key}/cover?v=…`. */
  cover_url?: string | null
  /** Built-in cover preset id when the current cover came from a default, else null. */
  cover_preset?: string | null
  lead: UserBrief | null
  plan_start_date?: string | null
  target_date?: string | null
  settings: ProjectSettings
  archived: boolean
  created_at: string
  open_count?: number
  total_count?: number
  is_member?: boolean
  /** The current user may change this project's settings (system or project admin). */
  can_admin?: boolean
}

export interface ProfileSection {
  key: string
  label: string
  labels?: Record<string, string>
  fields: string[]
  /** Project role that may fill this section on any requirement. */
  role: string | null
  /** Issue person fields (`reporter_id`…) whose user may fill this section. */
  issue_users: string[]
  issue_types?: IssueType[]
}

export interface TemplateField {
  key: string
  label: string
  labels?: Record<string, string>
  kind: 'builtin' | 'text' | 'textarea' | 'markdown' | 'date' | 'boolean' | 'number' | 'select' | 'master_ref' | 'user' | 'machine'
  required: boolean
  options: string[]
  section: string
  storage?: 'column' | 'extra' | 'master_ref'
  master_kind?: 'equipment' | 'line' | 'material' | 'service' | null
  multiple?: boolean
  aliases?: string[]
}

export interface ProjectProfile {
  key: string
  label: string
  sections: ProfileSection[]
  general_fields: string[]
  roles: string[]
  custom_fields: TemplateField[]
  default_statuses: string[]
  labels?: Record<string, string>
  version_id?: number | null
  template_id?: number
  version?: number
  description?: string
  recommended_by?: { id: number; name: string; path: string }
  definition?: TemplateDefinition | null
}

export interface TemplateDefinition {
  schema_version: 1
  key: string
  labels: Record<string, string>
  roles: { key: string; labels: Record<string, string> }[]
  sections: {
    key: string
    labels: Record<string, string>
    fields: string[]
    role: string | null
    issue_users: string[]
    issue_types: IssueType[]
  }[]
  fields: (Omit<TemplateField, 'label'> & { labels: Record<string, string>; report?: boolean; excel?: boolean })[]
  general_fields: string[]
  statuses: { name: string; category: 'todo' | 'doing' | 'done'; color: string }[]
  workflows: Record<IssueType, Record<string, string[]>>
  /** Freeform metadata per status name (optional; not enforced at runtime). */
  status_properties?: Record<string, Record<string, string>>
  defaults: {
    update_scope_options?: string[]
    label_options?: string[]
    test_status?: string | null
    pass_status?: string | null
    fail_return_status?: string | null
    sla_hours?: Record<string, number>
    sla_warning_hours?: number
    [key: string]: unknown
  }
  default_gates: Record<string, {
    required_fields: string[]
    approvals: { role: string; count: number }[]
  }>
  list_layout: { columns: { field: string; width: number; grow?: number; section?: string }[] }
}

export interface DepartmentTemplateVersion {
  id: number
  template_id: number
  version: number
  status: 'draft' | 'published'
  revision: number
  definition: TemplateDefinition | null
  validation_errors?: string[]
  created_at: string
  published_at: string | null
}

export interface DepartmentTemplate {
  id: number
  key: string
  label: string
  description: string
  enabled: boolean
  is_system: boolean
  current_version_id: number | null
  current_version: DepartmentTemplateVersion | null
  versions?: DepartmentTemplateVersion[]
  audits?: DepartmentTemplateAudit[]
  draft_version?: number | null
  project_count?: number
}

export interface DepartmentTemplateAudit {
  id: number
  action: 'create' | 'copy' | 'draft' | 'publish' | 'enable' | 'disable' | 'update' | 'discard'
  version_id: number | null
  actor_id: number | null
  actor_name: string | null
  detail: Record<string, unknown>
  created_at: string
}

export interface ProjectDetail {
  project: Project
  my_roles: string[]
  is_admin: boolean
  statuses: Status[]
  /** Effective transition rules per issue type (project override or built-in default). */
  workflows: Record<IssueType, Record<string, string[]>>
  /** Types whose project rules differ from the built-in flow. */
  customized_workflows: IssueType[]
  machine_groups: MachineGroup[]
  members: { id: number; role: string; user: UserBrief }[]
  boards: { id: number; name: string; kind: 'kanban' | 'scrum' }[]
  sprints: Sprint[]
  epics: { id: number; key: string; title: string }[]
  roles: string[]
  /** Form sections, their fields and who may fill them (backend services/profiles.py). */
  profile: ProjectProfile
  /** Resolved software department (configured or auto-detected). */
  software_units: { id: number; path: string; label: string }[]
  /** Shared label catalog (settings ∪ labels used on issues). */
  labels: string[]
}

export interface BoardColumn {
  name: string
  status_ids: number[]
  wip: number | null
}

export interface Board {
  id: number
  project_id: number
  name: string
  kind: 'kanban' | 'scrum'
  columns: BoardColumn[]
  swimlane: 'none' | 'assignee' | 'group' | 'epic' | 'priority'
  quick_filters: { name: string; jql: string }[]
  filter_jql: string
}

export interface OrgUnit {
  id: number
  dn: string
  name: string
  display_name: string | null
  label: string
  parent_id: number | null
  path: string
  level: number
  sort_order: number
  hidden: boolean
  removed: boolean
  member_count: number
  total_count?: number
}

export interface Notification {
  id: number
  kind: string
  message: string
  read: boolean
  actor: UserBrief | null
  issue_key: string | null
  created_at: string
}

export interface AgentAction {
  id: number
  tool: string
  args: Record<string, unknown>
  summary: string
  status: 'pending' | 'done' | 'rejected' | 'failed'
  result?: Record<string, unknown>
}

export type FilterScope = 'private' | 'everyone' | 'project'

export interface SavedFilter {
  id: number
  name: string
  jql: string
  description: string
  scope: FilterScope
  project: { key: string; name: string } | null
  source: { id: number; name: string; owner: UserBrief | null } | null
  copies: number
  mine: boolean
  owner: UserBrief | null
  created_at: string
  updated_at: string
}

export interface SharedFilter extends SavedFilter {
  saved: boolean
}
