import { Fragment, useEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { TypeIcon } from '../components/ui'
import { useApp } from '../context'
import { pick, t } from '../i18n'
import { typeName } from '../labels'
import { ACTION_BY_ID, ACTIONS, comboKeys, useShortcutBindings } from '../shortcuts'
import type { IssueType } from '../types'

type L = { zh: string; en: string }

const MEDIA = '/guide'

const NAV: { id: string; label: L }[] = [
  { id: 'start', label: { zh: '快速上手', en: 'Get started' } },
  { id: 'navigation', label: { zh: '登录与导航', en: 'Login & navigation' } },
  { id: 'my-work', label: { zh: '我的工作', en: 'My work' } },
  { id: 'create', label: { zh: '创建', en: 'Create' } },
  { id: 'issue', label: { zh: '问题详情', en: 'Issue details' } },
  { id: 'templates', label: { zh: '模板', en: 'Templates' } },
  { id: 'roles', label: { zh: '角色', en: 'Roles' } },
  { id: 'settings', label: { zh: '设置', en: 'Settings' } },
  { id: 'workflow', label: { zh: '工作流', en: 'Workflow' } },
  { id: 'board', label: { zh: '看板', en: 'Board' } },
  { id: 'list', label: { zh: '列表', en: 'List' } },
  { id: 'timeline', label: { zh: '时间线', en: 'Timeline' } },
  { id: 'filters', label: { zh: '筛选', en: 'Filters' } },
  { id: 'ai', label: { zh: 'AI', en: 'AI' } },
  { id: 'palette', label: { zh: '快速跳转', en: 'Quick jump' } },
  { id: 'more', label: { zh: '更多', en: 'More' } },
  { id: 'admin', label: { zh: '管理后台', en: 'Admin' } },
  { id: 'glossary-cta', label: { zh: '名词', en: 'Glossary' } },
  { id: 'shortcuts', label: { zh: '快捷键', en: 'Shortcuts' } },
  { id: 'faq', label: { zh: 'FAQ', en: 'FAQ' } },
]

function useReveal(root: RefObject<HTMLElement | null>) {
  useEffect(() => {
    const nodes = root.current?.querySelectorAll('.reveal') ?? []
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            entry.target.classList.add('in')
            observer.unobserve(entry.target)
          }
        }
      },
      { threshold: 0.12, rootMargin: '0px 0px -40px 0px' },
    )
    nodes.forEach((n) => observer.observe(n))
    return () => observer.disconnect()
  }, [root])
}

const CLIP_STILL: Record<string, string> = {
  'my-work': 'home',
  workflow: 'board-still',
  board: 'board-still',
  list: 'list-still',
  'ai-filter': 'filters-still',
  filters: 'filters-still',
  create: 'create-still',
  palette: 'palette-still',
  'ai-assistant': 'home',
}

/** Looping product demo — animated WebP (GIF-like), no video player chrome. */
function Clip({
  name,
  caption,
  sub,
  wide = true,
  feature = false,
}: {
  name: string
  caption?: L
  sub?: L
  wide?: boolean
  /** Full-width product-page treatment — larger frame, left-aligned title. */
  feature?: boolean
}) {
  const [missing, setMissing] = useState(false)
  const figClass = `g-media reveal${wide || feature ? ' wide' : ''}${feature ? ' g-feature' : ''}`
  const alt = caption ?? { zh: name, en: name }
  const captionNode = caption ? (
    <figcaption>
      {pick(caption)}
      {sub && <span>{pick(sub)}</span>}
    </figcaption>
  ) : null

  if (missing) {
    return (
      <figure className={figClass}>
        <Still name={CLIP_STILL[name] ?? 'home'} alt={alt} />
        {captionNode}
      </figure>
    )
  }

  return (
    <figure className={figClass}>
      <div className="g-frame g-demo">
        <img
          className="g-demo-anim"
          src={`${MEDIA}/${name}-demo.webp`}
          alt={pick(alt)}
          loading="lazy"
          decoding="async"
          onError={() => setMissing(true)}
        />
      </div>
      {captionNode}
    </figure>
  )
}

function Still({ name, alt, className = '' }: { name: string; alt: L; className?: string }) {
  return (
    <div className={`g-frame ${className}`}>
      <img src={`${MEDIA}/${name}.webp`} alt={pick(alt)} loading="lazy" />
    </div>
  )
}

function Section({
  id,
  eyebrow,
  title,
  body,
  tips,
  tone = '',
  children,
}: {
  id: string
  eyebrow: L
  title: L
  body: L
  tips?: L[]
  tone?: string
  children?: ReactNode
}) {
  return (
    <section id={id} className={`g-section ${tone}`}>
      <div className="g-inner">
        <header className="g-head reveal">
          <p className="g-eyebrow">{pick(eyebrow)}</p>
          <h2 className="g-title">{pick(title)}</h2>
          <p className="g-body">{pick(body)}</p>
        </header>
        {children}
        {tips && (
          <ul className="g-tips reveal">
            {tips.map((tip, i) => (
              <li key={i}>{pick(tip)}</li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}

const STATUS_COLORS: Record<string, string> = {
  待评估: '#8e8e93',
  待分派: '#8e8e93',
  方案讨论: '#af52de',
  重新打开: '#32ade6',
  研发中: '#0a84ff',
  开发完成: '#5ac8fa',
  设计中: '#0a84ff',
  图纸评审: '#af52de',
  试装: '#5e5ce6',
  终检: '#ff9f0a',
  测试中: '#ff9f0a',
  测试通过: '#34c759',
  测试未通过: '#ff375f',
  已排产: '#af52de',
  在制: '#0a84ff',
  质检: '#ff9f0a',
  已完成: '#30d158',
  完工: '#30d158',
  已驳回: '#ff453a',
}

type Flow = { type: IssueType; path: string[]; branch?: string; loop?: string[]; note: L }

const ELEC_FLOWS: Flow[] = [
  { type: 'requirement', path: ['待评估', '方案讨论', '研发中', '开发完成', '测试中', '测试通过', '已完成'], branch: '已驳回', loop: ['测试未通过', '重新打开', '研发中'], note: { zh: '完整评估 → 方案 → 研发 → 开发完成 → 测试 → 负责人确认', en: 'Evaluate → discuss → build → development complete → test → accept' } },
  { type: 'bug', path: ['待评估', '研发中', '开发完成', '测试中', '测试通过', '已完成'], branch: '已驳回', loop: ['测试未通过', '重新打开', '研发中'], note: { zh: '跳过方案讨论', en: 'Skips solution review' } },
  { type: 'story', path: ['待评估', '研发中', '开发完成', '测试中', '测试通过', '已完成'], loop: ['测试未通过', '重新打开', '研发中'], note: { zh: '用户故事也经过测试确认', en: 'Stories also pass through test acceptance' } },
  { type: 'task', path: ['待评估', '研发中', '已完成'], loop: ['已完成', '重新打开', '研发中'], note: { zh: '无单独测试环节', en: 'No dedicated test step' } },
  { type: 'subtask', path: ['待评估', '研发中', '已完成'], loop: ['已完成', '重新打开', '研发中'], note: { zh: '可从待评估直接完成', en: 'May also complete directly from evaluation' } },
  { type: 'epic', path: ['待评估', '研发中', '已完成'], branch: '已驳回', note: { zh: '承载子工作的容器', en: 'Container for child work' } },
]

const ALL_ISSUE_TYPES: IssueType[] = ['requirement', 'bug', 'story', 'task', 'subtask', 'epic']
const MECH_FLOWS: Flow[] = ALL_ISSUE_TYPES.map((type) => ({
  type,
  path: ['待评估', '设计中', '图纸评审', '试装', '终检', '已完成'],
  branch: '已驳回',
  note: { zh: '各类型默认共用完整机械流程', en: 'All types share the full mechanical flow by default' },
}))

const PROD_FLOWS: Flow[] = ALL_ISSUE_TYPES.map((type) => ({
  type,
  path: ['待分派', '已排产', '在制', '质检', '完工'],
  branch: '已驳回',
  note: { zh: '各类型默认共用完整生产流程', en: 'All types share the full production flow by default' },
}))

function StatusChip({ name }: { name: string }) {
  const color = STATUS_COLORS[name] ?? '#8e8e93'
  return (
    <span className="g-status" style={{ color, background: `${color}22` }}>
      {t(name)}
    </span>
  )
}

function FlowTable({ flows }: { flows: Flow[] }) {
  return (
    <div className="g-flows reveal">
      {flows.map((flow) => (
        <div key={flow.type} className="g-flow">
          <div className="g-flow-type">
            <TypeIcon type={flow.type} size={18} />
            <b>{typeName(flow.type)}</b>
          </div>
          <div className="g-flow-path">
            {flow.path.map((name, i) => (
              <Fragment key={name}>
                {i > 0 && <span className="g-arrow">→</span>}
                <StatusChip name={name} />
              </Fragment>
            ))}
            {flow.branch && (
              <>
                <span className="g-branch">{pick({ zh: '或', en: 'or' })}</span>
                <StatusChip name={flow.branch} />
              </>
            )}
            {flow.loop && (
              <span className="g-flow-loop">
                <span className="g-branch">{pick({ zh: '返工', en: 'Rework' })}</span>
                {flow.loop.map((name, i) => (
                  <Fragment key={name}>
                    {i > 0 && <span className="g-arrow">→</span>}
                    <StatusChip name={name} />
                  </Fragment>
                ))}
              </span>
            )}
          </div>
          <div className="g-flow-note">{pick(flow.note)}</div>
        </div>
      ))}
    </div>
  )
}

function TemplateFlows() {
  const [tab, setTab] = useState<'electrical' | 'mechanical' | 'production'>('electrical')
  const flows = tab === 'electrical' ? ELEC_FLOWS : tab === 'mechanical' ? MECH_FLOWS : PROD_FLOWS
  const tabs: { id: typeof tab; label: L }[] = [
    { id: 'electrical', label: { zh: '电气', en: 'Electrical' } },
    { id: 'mechanical', label: { zh: '机械', en: 'Mechanical' } },
    { id: 'production', label: { zh: '生产', en: 'Production' } },
  ]
  return (
    <div className="reveal">
      <div className="g-tpl-tabs">
        {tabs.map((t0) => (
          <button key={t0.id} type="button" className={`g-tpl-tab${tab === t0.id ? ' on' : ''}`} onClick={() => setTab(t0.id)}>
            {pick(t0.label)}
          </button>
        ))}
      </div>
      <FlowTable flows={flows} />
      <p className="g-flow-hint">
        {pick({
          zh: '上表列出内置模板的默认主路径；退回、重开等完整转换请以项目设置为准。电气测试通过默认进入「测试通过」，再由负责人确认完成。',
          en: 'These are the built-in main paths; check Project settings for every return and reopen edge. An Electrical pass enters Test Passed, then the assignee confirms Done.',
        })}
      </p>
    </div>
  )
}

const STEPS: { title: L; body: L }[] = [
  { title: { zh: '打开我的工作', en: 'Open My work' }, body: { zh: '首页一眼看到逾期、待测和今日队列，也可让 AI 帮你梳理优先级。', en: 'See overdue, waiting-for-test and today’s queue at a glance — or ask AI to prioritize.' } },
  { title: { zh: '提出问题', en: 'Raise an issue' }, body: { zh: '按 C 或点「创建」，一句话写清需求；也可勾选拆成史诗和子任务。', en: 'Press C or Create; describe it in a sentence, or split into an epic with sub-tasks.' } },
  { title: { zh: '按流程推进', en: 'Move it forward' }, body: { zh: '在详情流程图或看板上一步步流转，门禁与角色分区会自动生效。', en: 'Advance on the flow bar or board; gates and role sections apply automatically.' } },
  { title: { zh: '随时找到任何东西', en: 'Find anything' }, body: { zh: '自然语言筛选，或 Ctrl K 跳转；报表与名词页帮你读懂数字。', en: 'Plain-language filters or Ctrl K; reports and the glossary explain the numbers.' } },
]

const TEMPLATES: { key: string; title: L; roles: L; fields: L; flow: L; test: L }[] = [
  {
    key: 'electrical',
    title: { zh: '电气', en: 'Electrical' },
    roles: { zh: '项目管理员 · 需求方 · 电气组 · 工艺工程师 · 测试方 · 成员', en: 'Project admin · Requester · Electrical · Process engineer · Tester · Member' },
    fields: { zh: '机台、影响范围、方案确认、是否软件、验证方法/标准、测试结果', en: 'Machines, impact scope, scheme confirmation, software flag, verification, test result' },
    flow: { zh: '待评估 → 方案讨论 → 研发中 → 开发完成 → 测试中 → 测试通过 → 已完成', en: 'Evaluate → review → develop → development complete → test → accept → done' },
    test: { zh: '默认有测试中状态，可通过/不通过自动流转', en: 'Default test status with pass/fail auto-flow' },
  },
  {
    key: 'mechanical',
    title: { zh: '机械', en: 'Mechanical' },
    roles: { zh: '项目管理员 · 需求方 · 机械设计组 · 质量 · 成员', en: 'Project admin · Requester · Mechanical · Quality · Member' },
    fields: { zh: '图号、零件号、材料、公差、试装/终检结果', en: 'Drawing no., part no., material, tolerance, trial/final check' },
    flow: { zh: '待评估 → 设计中 → 图纸评审 → 试装 → 终检 → 已完成', en: 'Evaluate → design → review → trial → final → done' },
    test: { zh: '无默认测试中；质量靠字段与门禁确认', en: 'No default test status; quality via fields and gates' },
  },
  {
    key: 'production',
    title: { zh: '生产', en: 'Production' },
    roles: { zh: '项目管理员 · 提出人 · 生产执行组 · 质量 · 成员', en: 'Project admin · Requester · Production · Quality · Member' },
    fields: { zh: '工单、批次、产线、计划日期、实际数量、质量结果', en: 'Work order, batch, line, plan date, qty, quality result' },
    flow: { zh: '待分派 → 已排产 → 在制 → 质检 → 完工', en: 'Assign → scheduled → in progress → QC → finished' },
    test: { zh: '无默认测试自动流转；质检分区由质量角色填写', en: 'No test auto-flow; quality section owned by Quality' },
  },
]

const ROLE_LADDER: { title: L; body: L }[] = [
  { title: { zh: '系统管理员', en: 'System admin' }, body: { zh: '可见全部项目；永久删除已归档项目；管理组织与模板；默认可新建项目。', en: 'Sees all projects; can purge archived projects; manages org and templates; can create projects by default.' } },
  { title: { zh: '项目管理员（用户权限）', en: 'Project admin (user flag)' }, body: { zh: '可新建项目；在管理后台「用户」中勾选。与项目内角色「项目管理员」不同。', en: 'Can create projects; granted under Admin → Users. Distinct from the in-project admin role.' } },
  { title: { zh: '项目负责人 / 项目内管理员', en: 'Project lead / in-project admin' }, body: { zh: '改设置、成员、工作流与看板；可编辑所有分区。', en: 'Owns settings, members, workflows and board; edits every section.' } },
  { title: { zh: '模板角色', en: 'Template roles' }, body: { zh: '编辑对应分区；可作为门禁会签人；测试/质量记录结果。', en: 'Edit matching sections; may countersign gates; record test/quality results.' } },
  { title: { zh: '成员与相关人', en: 'Members & involved' }, body: { zh: '成员可看项目并改通用字段；仅相关人在仅成员项目中仍能看到自己的问题。', en: 'Members view and edit general fields; involved people still see their issues on members-only projects.' } },
]

const DAY_IN_LIFE: { title: L; items: L[] }[] = [
  {
    title: { zh: '需求方 / 提出人', en: 'Requester' },
    items: [
      { zh: '创建问题，写清标题、描述与机台（生产模板无强制机台）。', en: 'Create issues with a clear title, description and machines (not required on Production).' },
      { zh: '跟进自己提出的事项，在「我报告的」里查看。', en: 'Track what you reported under Reported by me.' },
      { zh: '灰字段属于执行组或质量，留给对应角色填写。', en: 'Grey fields belong to delivery or quality — leave them for those roles.' },
    ],
  },
  {
    title: { zh: '电气 / 机械 / 生产执行', en: 'Delivery teams' },
    items: [
      { zh: '在「分配给我」处理事项，按分区补齐专业字段。', en: 'Work Assigned to me and fill your section’s fields.' },
      { zh: '沿流程图推进；遇门禁先补必填或请会签。', en: 'Advance on the flow; clear gates or get countersigns first.' },
      { zh: '关注 SLA：即将超时与已超时会出现在我的工作。', en: 'Watch SLA — warning and breached items surface on My work.' },
    ],
  },
  {
    title: { zh: '测试方 / 质量', en: 'Tester / Quality' },
    items: [
      { zh: '电气：在「待我测试」处理测试中事项，记录通过/不通过。', en: 'Electrical: clear Waiting for my test; record Passed/Failed.' },
      { zh: '机械/生产：填写试装、终检或质量结果字段。', en: 'Mechanical/Production: fill trial, final-check or quality fields.' },
      { zh: '失败后负责人会收到通知并重新打开返工。', en: 'Failures notify the assignee to reopen and rework.' },
    ],
  },
]

const SETTINGS_STEPS: { title: L; body: L }[] = [
  { title: { zh: '1. 基本信息', en: '1. Basics' }, body: { zh: '名称、负责人、颜色、公开/仅成员、归档；更换部门模板（需映射状态与字段）。', en: 'Name, lead, color, visibility, archive; change department template (map statuses and fields).' } },
  { title: { zh: '2. 成员与角色', en: '2. Members & roles' }, body: { zh: '按人或组织单元添加；一人可多角色。角色含义随模板变化。', en: 'Add by person or org unit; multiple roles per user. Role meaning follows the template.' } },
  { title: { zh: '3. 状态与工作流', en: '3. Status & workflow' }, body: { zh: '按类型在示意图或矩阵中编辑转换；支持拖线、撤销/重做、校验、恢复默认，以及测试自动流转。', en: 'Edit per-type transitions in graph or matrix view, with drag-to-connect, undo/redo, linting, reset and test auto-flow.' } },
  { title: { zh: '4. 门禁与 SLA', en: '4. Gates & SLA' }, body: { zh: '配置目标状态必填字段、角色会签人数、优先级时限、暂停状态与工作日历。', en: 'Set target-state required fields, role countersigns, priority deadlines, pause statuses and business calendar.' } },
  { title: { zh: '5. 机台', en: '5. Machines' }, body: { zh: '维护机台组与机台，供电气、机械字段和泳道复用。', en: 'Maintain machine groups and machines for fields and swimlanes.' } },
  { title: { zh: '6. 字段选项', en: '6. Field options' }, body: { zh: '维护模板字段选项；电气项目还可配置影响范围与软件部门。', en: 'Maintain template field choices; Electrical projects also configure impact scope and software department.' } },
  { title: { zh: '7. 看板', en: '7. Board' }, body: { zh: '列对应状态、WIP 硬限制、五类泳道、快速筛选 JQL 与共享视图。', en: 'Map columns to statuses, enforce WIP, choose five swimlanes, define JQL quick filters and share views.' } },
  { title: { zh: '8. Excel', en: '8. Excel' }, body: { zh: '导入前预览列映射与错误；列表页可导出当前数据。', en: 'Preview column mapping and errors before import; export current data from List.' } },
]

const TILES: { name: string; title: L; body: L; to?: string }[] = [
  { name: 'home', title: { zh: '我的工作', en: 'My work' }, body: { zh: '需关注、进展图与个人队列。', en: 'Focus strip, progress charts and your queues.' }, to: '/' },
  { name: 'summary', title: { zh: '摘要', en: 'Summary' }, body: { zh: '近 7 日计数、状态环、优先级与史诗进度。', en: 'Week counts, status ring, priority and epic progress.' }, to: '/projects/ELEC/summary' },
  { name: 'backlog', title: { zh: '待办与冲刺', en: 'Backlog & sprints' }, body: { zh: '史诗归类、冲刺目标、故事点与进度偏差。', en: 'Epic planning, sprint goals, story points and schedule variance.' }, to: '/projects/ELEC/backlog' },
  { name: 'timeline', title: { zh: '时间线', en: 'Timeline' }, body: { zh: '按史诗与冲刺查看排期，拖动条尾调整截止日期。', en: 'Plan by epic and sprint; drag bar ends to change due dates.' }, to: '/projects/ELEC/timeline' },
  { name: 'reports', title: { zh: '报表', en: 'Reports' }, body: { zh: '九类报表：健康、流动、质量、冲刺、预测与 AI 周报。', en: 'Nine report areas: health, flow, quality, sprint, forecast and AI reports.' }, to: '/projects/ELEC/reports' },
  { name: 'org', title: { zh: '组织架构', en: 'Organization' }, body: { zh: '查找同事与部门负载。', en: 'Find people and org workload.' }, to: '/org' },
  { name: 'user-settings', title: { zh: '个人设置', en: 'Personal settings' }, body: { zh: '通知、主题、语言、打开方式与快捷键。', en: 'Notifications, theme, language, issue opening and shortcuts.' }, to: '/settings' },
  { name: 'settings-workflow', title: { zh: '项目设置', en: 'Project settings' }, body: { zh: '模板、成员、可视化工作流、门禁与 SLA。', en: 'Template, members, visual workflow, gates and SLA.' }, to: '/projects/ELEC/settings' },
  { name: 'notifications', title: { zh: '通知', en: 'Notifications' }, body: { zh: '站内、邮件与企业微信提醒，可按角色和事件细分。', en: 'In-app, email and WeCom alerts by event and role.' }, to: '/settings/notifications' },
]

const SHORTCUTS: { keys: string[]; action: L }[] = [
  { keys: ['Ctrl', 'K'], action: { zh: '打开快速跳转', en: 'Open quick jump' } },
  { keys: ['Ctrl', 'Enter'], action: { zh: '提交评论 / AI 生成草稿', en: 'Post comment / AI draft' } },
  { keys: ['Enter'], action: { zh: '保存单行编辑', en: 'Save a single-line edit' } },
  { keys: ['Esc'], action: { zh: '取消或关闭', en: 'Cancel or close' } },
]

const FAQ: { q: L; a: L }[] = [
  {
    q: { zh: '为什么不能拖到「已完成」？', en: 'Why can’t I drag straight to Done?' },
    a: { zh: '工作流逐步流转，管理员也一样。不允许进入的列会变暗；详情流程图显示下一步。门禁未满足时也无法进入。', en: 'Workflows move one step at a time, admins included. Disallowed columns dim; the flow bar shows next steps. Gates also block entry.' },
  },
  {
    q: { zh: '为什么有些字段是灰的？', en: 'Why are some fields grey?' },
    a: { zh: '需求按模板分区：只有对应角色或相关人（负责人、测试人等）能编辑。其他类型在能编辑时通常整单可改通用与业务字段。', en: 'Requirements are sectioned by template role. Other types are usually fully editable once you can edit the issue.' },
  },
  {
    q: { zh: '测试通过后谁来点完成？', en: 'Who marks Done after a test pass?' },
    a: { zh: '测试人点「通过」后进入「测试通过」；只有负责人（或项目管理员）才能再流转到「已完成」或「已驳回」。电气模板默认如此；机械与生产多依赖质检字段和门禁。', en: 'The tester’s Pass moves the issue to Test Passed; only the assignee (or project admin) can then move it to Done or Rejected. That is the Electrical default; Mechanical and Production rely on quality fields and gates instead.' },
  },
  {
    q: { zh: '「软件负责人」在哪个模板？', en: 'Which template has Software owner?' },
    a: { zh: '电气模板：将「是否涉及软件」设为是后出现，人选来自软件部门。', en: 'Electrical: set Involves software to Yes; candidates come from the software department.' },
  },
  {
    q: { zh: '如何更换部门模板？', en: 'How do I change the department template?' },
    a: { zh: '项目设置 → 基本信息 → 更换模板，按向导映射状态、字段与角色。进行中的审批与部分筛选可能需要复查。', en: 'Settings → Basics → change template, then map statuses, fields and roles. Review pending approvals and saved filters.' },
  },
  {
    q: { zh: '健康分和 SLA 去哪看解释？', en: 'Where are health and SLA explained?' },
    a: { zh: '打开「名词与图表」页，或看事项上的 SLA 问号提示、报表健康页。', en: 'Open the Glossary page, the SLA tip on issues, or Reports → Health.' },
  },
  {
    q: { zh: '怎么批量导入？', en: 'How do I import a spreadsheet?' },
    a: { zh: '列表视图「导入 Excel」，预览映射后导入；同一处可导出。', en: 'List view → Import Excel, preview mapping, then import. Export from the same place.' },
  },
  {
    q: { zh: '遇到问题找谁？', en: 'Who do I ask?' },
    a: { zh: 'Mira 由 MET Machine Learning Group 维护，负责人 Houcheng Fan。', en: 'Maintained by MET Machine Learning Group, owner Houcheng Fan.' },
  },
]

export function GuidePage() {
  const root = useRef<HTMLDivElement>(null)
  const location = useLocation()
  const { openCreate, openShortcuts, user } = useApp()
  const { bindings } = useShortcutBindings()
  const personal = ACTIONS.filter((a) => bindings[a.id] && (!a.adminOnly || user.is_admin)).map((a) => ({
    id: a.id,
    combo: bindings[a.id],
  }))
  const [active, setActive] = useState('')
  useReveal(root)

  useEffect(() => {
    const id = location.hash.slice(1)
    if (id) requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' }))
  }, [location.hash])

  useEffect(() => {
    const sections = NAV.map((n) => document.getElementById(n.id)).filter((el): el is HTMLElement => !!el)
    const observer = new IntersectionObserver(
      (entries) => {
        const hit = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0]
        if (hit) setActive(hit.target.id)
      },
      { rootMargin: '-45% 0px -50% 0px' },
    )
    sections.forEach((s) => observer.observe(s))
    return () => observer.disconnect()
  }, [])

  return (
    <div className="guide" ref={root}>
      <nav className="g-localnav">
        <div className="g-localnav-inner">
          <a className="g-localnav-title" href="#top">
            {pick({ zh: 'Mira 使用说明', en: 'Mira Guide' })}
          </a>
          <div className="g-localnav-links">
            {NAV.map((n) => (
              <a key={n.id} href={`#${n.id}`} className={active === n.id ? 'active' : ''}>
                {pick(n.label)}
              </a>
            ))}
          </div>
          <Link to="/guide/glossary" className="g-pill ghost">
            {pick({ zh: '名词', en: 'Glossary' })}
          </Link>
          <Link to="/" className="g-pill">
            {pick({ zh: '开始使用', en: 'Open app' })}
          </Link>
        </div>
      </nav>

      <section id="top" className="g-hero">
        <div className="g-inner">
          <p className="g-eyebrow reveal">{pick({ zh: 'Mira 使用说明', en: 'Mira User Guide' })}</p>
          <h1 className="g-hero-title reveal">
            {pick({ zh: '需求从提出到上线，', en: 'From request to release — ' })}
            <span className="g-gradient">{pick({ zh: '每一步都清清楚楚。', en: 'every step, crystal clear.' })}</span>
          </h1>
          <p className="g-hero-sub reveal">
            {pick({
              zh: '为 MET 各部门打造。按模板协作、按角色填表、用报表读懂节奏——再加一页名词与图表速查。',
              en: 'Built for every MET department. Collaborate by template, fill by role, read the pace in reports — plus a glossary for every term and chart.',
            })}
          </p>
          <div className="g-ctas reveal">
            <Link to="/" className="g-btn primary">
              {pick({ zh: '进入我的工作', en: 'Go to My work' })}
            </Link>
            <a href="#my-work" className="g-link">
              {pick({ zh: '观看演示', en: 'See the demos' })} ›
            </a>
            <Link to="/guide/glossary" className="g-link">
              {pick({ zh: '名词与图表', en: 'Glossary' })} ›
            </Link>
          </div>
          <div className="g-hero-media reveal">
            <Still name="home" alt={{ zh: '我的工作', en: 'My work' }} />
          </div>
        </div>
      </section>

      <section id="start" className="g-section tint">
        <div className="g-inner">
          <header className="g-head reveal">
            <p className="g-eyebrow">{pick({ zh: '快速上手', en: 'Get started' })}</p>
            <h2 className="g-title">{pick({ zh: '四步，就能用起来。', en: 'Up and running in four steps.' })}</h2>
          </header>
          <ol className="g-steps">
            {STEPS.map((step, i) => (
              <li key={i} className="g-step reveal" style={{ transitionDelay: `${i * 80}ms` }}>
                <span className="g-step-n">{i + 1}</span>
                <h3>{pick(step.title)}</h3>
                <p>{pick(step.body)}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <Section
        id="navigation"
        eyebrow={{ zh: '登录与全局导航', en: 'Login & global navigation' }}
        title={{ zh: '从 Mira 进入，也能随时直达。', en: 'Enter through Mira. Reach anything fast.' }}
        body={{
          zh: '登录页支持域账号与本地账号、语言切换和测试环境标识；首次域登录会自动建立用户档案，不保存域密码。进入后，顶栏可访问我的工作、项目、筛选、组织、通知、创建、AI 与个人设置。',
          en: 'Sign in with a domain or local account, switch language, and spot the staging badge. First domain sign-in creates a profile without storing the domain password. The top bar links My work, projects, filters, organization, notifications, Create, AI and preferences.',
        }}
        tips={[
          { zh: '齿轮菜单可切换主题、语言、问题打开方式，并进入通知与快捷键设置。', en: 'The gear menu controls theme, language, issue opening mode, notifications and shortcuts.' },
          { zh: '问题可在居中弹窗、左抽屉或右抽屉中打开。', en: 'Issues can open in a centered modal, left drawer or right drawer.' },
        ]}
      >
        <div className="g-two">
          <Still name="login" alt={{ zh: 'Mira 登录页', en: 'Mira sign-in' }} />
          <Still name="user-settings" alt={{ zh: '个人设置', en: 'Personal settings' }} />
        </div>
      </Section>

      <Section
        id="my-work"
        eyebrow={{ zh: '我的工作', en: 'My work' }}
        title={{ zh: '先看急事，再看队列。', en: 'Urgent first. Then your queue.' }}
        body={{
          zh: '首页汇总你负责、待测、报告与逾期的事项。上方「需关注」直达逾期与待测；中间是个人进展（近 7 日、状态环、优先级）；下方筛选条驱动唯一工作队列，右侧是项目健康与关注动态。',
          en: 'Home lists work assigned to you, waiting for your test, reported by you, and overdue. A focus strip jumps to risks; progress charts show the last 7 days, statuses and priorities; chips drive one queue; the rail shows project health and watched activity.',
        }}
        tips={[
          { zh: '点击状态环图例可过滤「分配给我」（仅未完成有匹配时）。', en: 'Click a status legend to filter Assigned when open issues match.' },
          { zh: '图表含义见「名词与图表」。', en: 'Chart meanings live in the Glossary.' },
        ]}
      >
        <Clip name="my-work" />
      </Section>

      <Section
        id="create"
        tone="tint"
        eyebrow={{ zh: '创建问题', en: 'Create' }}
        title={{ zh: '一句话，就能提出需求。', en: 'One sentence is all it takes.' }}
        body={{
          zh: '点「创建」或按 C。填标题与模板字段即可提交；也可用自然语言让 AI 填表。勾选「拆分为史诗和子任务」后点「✦ 生成」，会得到史诗+子项草稿，确认后批量创建。',
          en: 'Click Create or press C. Fill the title and template fields — or let AI draft from plain words. Tick “Split into epic + sub-tasks”, then Generate, review the draft tree, and create them in one go.',
        }}
        tips={[
          { zh: '勾选本身不会请求 AI，真正触发是点「✦ 生成」或 Ctrl+Enter。', en: 'The checkbox alone does nothing — Generate (or Ctrl+Enter) triggers AI.' },
          { zh: '机台按组多选；电气字段可留给电气组后补。', en: 'Pick machines by group; electrical fields can wait for that team.' },
        ]}
      >
        <Clip name="create" />
      </Section>

      <Section
        id="issue"
        eyebrow={{ zh: '问题详情', en: 'Issue details' }}
        title={{ zh: '上下文、协作与流转，都在一处。', en: 'Context, collaboration and flow — together.' }}
        body={{
          zh: '顶部可查看史诗与父任务路径、关注、克隆、类型转换和删除；流程轨道只点亮当前允许的下一步，并在门禁失败时定位缺失字段或会签。正文支持分区字段、截图附件、子任务、关联问题、评论、@ 提及、历史与 Slash 命令；侧栏管理 SLA、冲刺、史诗、父任务和故事点。',
          en: 'The header carries epic/parent breadcrumbs, watch, clone, type conversion and delete. The flow bar exposes only valid next steps and points to missing gate fields or approvals. The body combines role sections, pasted screenshots, sub-tasks, links, comments, @mentions, history and slash commands; the sidebar holds SLA, sprint, epic, parent and story points.',
        }}
        tips={[
          { zh: 'AI 菜单可改进描述、总结讨论、建议子任务并查找相似问题。', en: 'The AI menu can improve descriptions, summarize discussion, suggest sub-tasks and find similar issues.' },
          { zh: '测试通过不是最终完成；默认电气流程仍需负责人确认。', en: 'Test Passed is not final Done; the default Electrical flow still needs assignee confirmation.' },
        ]}
      >
        <Still name="issue" alt={{ zh: '问题详情最新界面', en: 'Latest issue details' }} />
      </Section>

      <section id="templates" className="g-section">
        <div className="g-inner">
          <header className="g-head reveal">
            <p className="g-eyebrow">{pick({ zh: '部门模板', en: 'Templates' })}</p>
            <h2 className="g-title">{pick({ zh: '同一套 Mira，三种日常。', en: 'One Mira. Three ways of working.' })}</h2>
            <p className="g-body">
              {pick({
                zh: '模板决定默认角色、分区字段与状态链。差别在「谁填什么、怎么往下走」，而不是另一套产品。',
                en: 'A template sets default roles, sections and statuses — who fills what and how work advances, not a different product.',
              })}
            </p>
          </header>
          <div className="g-tpl-grid">
            {TEMPLATES.map((tpl, i) => (
              <article key={tpl.key} className="g-tpl-card reveal" style={{ transitionDelay: `${i * 60}ms` }}>
                <h3>{pick(tpl.title)}</h3>
                <dl>
                  <div>
                    <dt>{pick({ zh: '角色', en: 'Roles' })}</dt>
                    <dd>{pick(tpl.roles)}</dd>
                  </div>
                  <div>
                    <dt>{pick({ zh: '关键字段', en: 'Key fields' })}</dt>
                    <dd>{pick(tpl.fields)}</dd>
                  </div>
                  <div>
                    <dt>{pick({ zh: '默认状态', en: 'Default flow' })}</dt>
                    <dd>{pick(tpl.flow)}</dd>
                  </div>
                  <div>
                    <dt>{pick({ zh: '测试 / 质量', en: 'Test / quality' })}</dt>
                    <dd>{pick(tpl.test)}</dd>
                  </div>
                </dl>
              </article>
            ))}
          </div>
          <p className="g-tips reveal" style={{ listStyle: 'none', padding: 0 }}>
            <Link to="/guide/glossary#template" className="g-link">
              {pick({ zh: '模板与分区名词解释', en: 'Template glossary' })} ›
            </Link>
          </p>
        </div>
      </section>

      <section id="roles" className="g-section tint">
        <div className="g-inner">
          <header className="g-head reveal">
            <p className="g-eyebrow">{pick({ zh: '角色与权限', en: 'Roles & access' })}</p>
            <h2 className="g-title">
              {pick({
                zh: '谁能看、谁能改。',
                en: 'Who can see — and who can change.',
              })}
            </h2>
          </header>
          <div className="g-ladder reveal">
            {ROLE_LADDER.map((r) => (
              <div key={pick(r.title)} className="g-ladder-row">
                <h3>{pick(r.title)}</h3>
                <p>{pick(r.body)}</p>
              </div>
            ))}
          </div>
          <div className="g-day-grid">
            {DAY_IN_LIFE.map((d, i) => (
              <div key={i} className="g-day-card reveal" style={{ transitionDelay: `${i * 60}ms` }}>
                <h3>{pick(d.title)}</h3>
                <ul>
                  {d.items.map((it, j) => (
                    <li key={j}>{pick(it)}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </section>

      <Section
        id="settings"
        eyebrow={{ zh: '项目设置', en: 'Project settings' }}
        title={{ zh: '负责人这样配项目。', en: 'How leads configure a project.' }}
        body={{
          zh: '只有项目负责人或项目管理员（及系统管理员）能改设置。按下面顺序配置最省事；SLA、门禁、泳道等词可在名词页查阅。',
          en: 'Only the lead, project admins and system admins can edit settings. Follow the order below; SLA, gates and swimlanes are defined in the glossary.',
        }}
      >
        <ol className="g-settings-list reveal">
          {SETTINGS_STEPS.map((s, i) => (
            <li key={i}>
              <h3>{pick(s.title)}</h3>
              <p>{pick(s.body)}</p>
            </li>
          ))}
        </ol>
        <Still name="settings-workflow" alt={{ zh: '项目设置', en: 'Project settings' }} />
        <p className="g-tips reveal" style={{ listStyle: 'none' }}>
          <Link to="/guide/glossary#sla" className="g-link">
            {pick({ zh: 'SLA · 门禁 · WIP 说明', en: 'SLA · gates · WIP' })} ›
          </Link>
        </p>
      </Section>

      <Section
        id="workflow"
        tone="dark"
        eyebrow={{ zh: '工作流', en: 'Workflow' }}
        title={{ zh: '按模板看默认路径。', en: 'Default paths by template.' }}
        body={{
          zh: '问题只能逐步流转。切换下方模板查看默认状态示意；实际项目以设置为准。电气可在测试结果录入后自动跳转。',
          en: 'Issues advance one step at a time. Switch templates below for default paths — live projects follow their settings. Electrical can auto-advance on test results.',
        }}
      >
        <Clip name="workflow" />
        <TemplateFlows />
      </Section>

      <Section
        id="board"
        eyebrow={{ zh: '看板与冲刺', en: 'Board and sprints' }}
        title={{ zh: '拖一下，状态就变了。', en: 'Drag it. Done.' }}
        body={{
          zh: '拖动卡片流转；不允许的列会变暗。可切换看板、按负责人筛选、隐藏子任务、复制共享视图，并按无泳道/负责人/机台组/史诗/优先级分组。WIP 达上限会拒绝继续拖入；待办页可管理史诗和冲刺。',
          en: 'Drag cards to move; disallowed columns dim. Switch boards, filter by assignee, hide sub-tasks, copy a shared view, and group by none, assignee, machine group, epic or priority. Reaching WIP rejects another drop; use Backlog for epics and sprints.',
        }}
      >
        <Clip name="board" />
      </Section>

      <Section
        id="list"
        tone="tint"
        eyebrow={{ zh: '列表视图', en: 'List view' }}
        title={{ zh: '像表格一样编辑。', en: 'Edit it like a spreadsheet.' }}
        body={{
          zh: '列表按模板分区展示字段，点击单元格编辑。可切换需求/全部类型、隐藏完成项或子任务、筛状态、选列、调列宽、排序与按机台组合并；支持批量修改状态、优先级、冲刺、负责人和测试人，也可复制共享视图并导入导出 Excel。',
          en: 'Columns follow template sections; click a cell to edit. Switch requirement/all types, hide done items or sub-tasks, filter statuses, choose and resize columns, sort, or merge machines. Bulk-edit status, priority, sprint, assignee and tester; share views and import/export Excel.',
        }}
      >
        <Clip name="list" />
      </Section>

      <Section
        id="timeline"
        eyebrow={{ zh: '时间线', en: 'Timeline' }}
        title={{ zh: '把范围放进时间里。', en: 'Put scope on a calendar.' }}
        body={{
          zh: '按史诗分组查看问题与冲刺区间，在周、月、季度尺度间切换并一键回到今天。逾期和无截止日期会单独提示；有编辑权限时可拖动条尾直接调整截止日期。',
          en: 'Group issues by epic alongside sprint ranges, switch week/month/quarter scale, and jump back to today. Overdue and undated work are called out; editors can drag a bar end to change its due date.',
        }}
      >
        <Still name="timeline" alt={{ zh: '项目时间线', en: 'Project timeline' }} />
      </Section>

      <Section
        id="filters"
        eyebrow={{ zh: '筛选', en: 'Filters' }}
        title={{ zh: '说出你想找的。', en: 'Say what you’re looking for.' }}
        body={{
          zh: '用一句话生成 JQL，或直接编辑条件并用表头漏斗继续收窄。常用条件可保存为个人或共享筛选器；结果支持分页、列调整与 Excel 导出。',
          en: 'Generate JQL from plain words, or edit it directly and narrow with header funnels. Save personal or shared filters; paginate, tune columns and export results to Excel.',
        }}
      >
        <div className="g-features">
          <Clip
            name="ai-filter"
            feature
            caption={{ zh: 'AI 自然语言筛选', en: 'AI plain-language filter' }}
            sub={{ zh: '展开 AI 筛选，一句话生成条件并应用到结果。', en: 'Expand AI Filter, describe the result, and apply the generated query.' }}
          />
          <Clip
            name="filters"
            feature
            caption={{ zh: 'JQL、共享筛选与表头漏斗', en: 'JQL, shared filters and header funnels' }}
            sub={{ zh: '保存查询，调整结果列，并按状态、负责人、机台等维度继续收窄。', en: 'Save queries, tune result columns, and narrow by status, assignee, machine and more.' }}
          />
        </div>
      </Section>

      <Section
        id="ai"
        tone="dark"
        eyebrow={{ zh: 'AI 助手', en: 'AI assistant' }}
        title={{ zh: '随时问，随时办。', en: 'Ask anything. Get it done.' }}
        body={{
          zh: '助手可搜索问题和人员、读取项目配置与统计、解释产品、流转/分派/评论并跳转页面；写操作和跳转都会先确认。创建表单提供智能填表、分派、重复项与优先级建议；详情页可美化、总结、拆子任务和关联相似项；报表还可生成、预览、下载或发送 AI 日报/周报。',
          en: 'The assistant searches issues and people, reads project configuration and metrics, explains the product, transitions/assigns/comments and opens pages; writes and navigation require confirmation. Create adds drafting, routing, duplicate and priority suggestions; issue AI improves, summarizes, splits and links similar work; Reports generates, previews, downloads and sends AI daily/weekly reports.',
        }}
      >
        <Clip name="ai-assistant" />
      </Section>

      <Section
        id="palette"
        eyebrow={{ zh: '快速跳转', en: 'Quick jump' }}
        title={{ zh: 'Ctrl K，直达。', en: 'Ctrl K. Go anywhere.' }}
        body={{
          zh: '搜问题、人员、项目、命令和名词；找不到时可把当前问题直接交给 AI。',
          en: 'Search issues, people, projects, commands and glossary terms; hand the query to AI when needed.',
        }}
      >
        <Clip name="palette" />
      </Section>

      <section id="more" className="g-section tint">
        <div className="g-inner">
          <header className="g-head reveal">
            <p className="g-eyebrow">{pick({ zh: '更多功能', en: 'And more' })}</p>
            <h2 className="g-title">{pick({ zh: '你需要的，都在这里。', en: 'Everything you need, in one place.' })}</h2>
          </header>
          <div className="g-tiles">
            {TILES.map((tile, i) => {
              const content = (
                <>
                  <div className="g-tile-text">
                    <h3>{pick(tile.title)}</h3>
                    <p>{pick(tile.body)}</p>
                  </div>
                  <Still name={tile.name} alt={tile.title} className="g-tile-media" />
                </>
              )
              return tile.to ? (
                <Link key={tile.name} to={tile.to} className="g-tile reveal" style={{ transitionDelay: `${(i % 2) * 80}ms` }}>
                  {content}
                </Link>
              ) : (
                <div key={tile.name} className="g-tile reveal" style={{ transitionDelay: `${(i % 2) * 80}ms` }}>
                  {content}
                </div>
              )
            })}
          </div>
        </div>
      </section>

      <Section
        id="admin"
        eyebrow={{ zh: '管理后台', en: 'Administration' }}
        title={{ zh: '组织、模板和平台状态，集中治理。', en: 'Govern people, templates and platform health.' }}
        body={{
          zh: '仅系统管理员可见。可执行 AD 连接诊断、同步与变更预览，管理用户和本地账号、跨项目主数据、版本化部门模板、AI 可用性与调用审计，以及提醒推送。',
          en: 'System-admin only. Diagnose AD connectivity, preview and run sync, manage users and local accounts, shared master data, versioned department templates, AI health and call audit, and reminder delivery.',
        }}
      >
        <Still name="admin" alt={{ zh: '管理后台', en: 'Administration' }} />
      </Section>

      <section id="glossary-cta" className="g-section">
        <div className="g-inner">
          <header className="g-head reveal">
            <p className="g-eyebrow">{pick({ zh: '名词与图表', en: 'Glossary' })}</p>
            <h2 className="g-title">{pick({ zh: '看不懂的词和数，都在这页。', en: 'Every term and metric, in one place.' })}</h2>
            <p className="g-body">
              {pick({
                zh: '规划模型（史诗 / 任务 / 冲刺）、专有名词、SLA、健康分、CFD、燃尽与预测：含义、怎么读、怎么用。',
                en: 'Planning model (epic / task / sprint), terms, SLA, health, CFD, burndown and forecast — meaning, how to read, how to use.',
              })}
            </p>
          </header>
          <div className="g-ctas reveal">
            <Link to="/guide/glossary" className="g-btn primary">
              {pick({ zh: '打开名词与图表', en: 'Open the glossary' })}
            </Link>
            <Link to="/guide/glossary#model" className="g-link">
              {pick({ zh: '规划模型', en: 'Planning model' })} ›
            </Link>
            <Link to="/guide/glossary#charts" className="g-link">
              {pick({ zh: '直接看图表', en: 'Jump to charts' })} ›
            </Link>
          </div>
        </div>
      </section>

      <section id="shortcuts" className="g-section tint">
        <div className="g-inner narrow">
          <header className="g-head reveal">
            <p className="g-eyebrow">{pick({ zh: '快捷键', en: 'Keyboard shortcuts' })}</p>
            <h2 className="g-title">{pick({ zh: '双手不离键盘。', en: 'Keep your hands on the keys.' })}</h2>
          </header>
          <div className="g-keys reveal">
            {personal.map(({ id, combo }) => (
              <div key={id} className="g-key-row">
                <span className="g-key-combo">
                  {comboKeys(combo).map((k, j) => (
                    <Fragment key={k}>
                      {j > 0 && <span className="g-plus">+</span>}
                      <kbd>{k}</kbd>
                    </Fragment>
                  ))}
                </span>
                <span>{t(ACTION_BY_ID[id].label)}</span>
              </div>
            ))}
            {SHORTCUTS.map((s, i) => (
              <div key={i} className="g-key-row">
                <span className="g-key-combo">
                  {s.keys.map((k, j) => (
                    <Fragment key={k}>
                      {j > 0 && <span className="g-plus">+</span>}
                      <kbd>{k}</kbd>
                    </Fragment>
                  ))}
                </span>
                <span>{pick(s.action)}</span>
              </div>
            ))}
          </div>
          <div className="g-keys-foot reveal">
            <span>{pick({ zh: '默认 C 创建、A 打开 AI、/ 快速跳转、Shift+/ 打开快捷键设置；Ctrl K 为固定跳转。自定义绑定会随账号同步。', en: 'Defaults: C Create, A AI, / quick jump, Shift+/ shortcuts; Ctrl K is fixed. Custom bindings sync with your account.' })}</span>
            <button type="button" className="btn primary" onClick={openShortcuts}>
              ⌘ {pick({ zh: '自定义快捷键', en: 'Customize shortcuts' })}
            </button>
          </div>
        </div>
      </section>

      <section id="faq" className="g-section">
        <div className="g-inner narrow">
          <header className="g-head reveal">
            <p className="g-eyebrow">{pick({ zh: '常见问题', en: 'FAQ' })}</p>
            <h2 className="g-title">{pick({ zh: '还有疑问？', en: 'Questions?' })}</h2>
          </header>
          <div className="g-faq reveal">
            {FAQ.map((item, i) => (
              <details key={i}>
                <summary>{pick(item.q)}</summary>
                <p>{pick(item.a)}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      <section className="g-section g-end tint">
        <div className="g-inner">
          <h2 className="g-title reveal">{pick({ zh: '准备好了吗？', en: 'Ready when you are.' })}</h2>
          <div className="g-ctas reveal">
            <button type="button" className="g-btn primary" onClick={() => openCreate()}>
              {pick({ zh: '创建第一个问题', en: 'Create your first issue' })}
            </button>
            <Link to="/" className="g-link">
              {pick({ zh: '进入我的工作', en: 'Go to My work' })} ›
            </Link>
          </div>
        </div>
      </section>
    </div>
  )
}
