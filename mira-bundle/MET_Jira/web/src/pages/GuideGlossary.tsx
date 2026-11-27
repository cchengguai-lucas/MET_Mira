import { useEffect, useRef, useState, type RefObject } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { TypeIcon } from '../components/ui'
import { pick } from '../i18n'
import { typeName } from '../labels'
import type { IssueType } from '../types'

type L = { zh: string; en: string }

type Entry = {
  id: string
  term: L
  meaning: L
  read?: L
  use?: L
  /** Still under /guide/{media}.webp */
  media?: string
  mediaAlt?: L
}

const MEDIA = '/guide'

const NAV: { id: string; label: L }[] = [
  { id: 'demos', label: { zh: '界面示例', en: 'Demos' } },
  { id: 'model', label: { zh: '规划模型', en: 'Planning model' } },
  { id: 'terms', label: { zh: '专有名词', en: 'Terms' } },
  { id: 'charts', label: { zh: '图表与指标', en: 'Charts' } },
]

const DEMOS: { name: string; caption: L }[] = [
  { name: 'login', caption: { zh: 'Mira 登录：动态封面与玻璃面板', en: 'Mira sign-in: animated cover and glass panel' } },
  { name: 'home', caption: { zh: '我的工作：关注条、进展图与队列', en: 'My Work: focus strip, charts and queue' } },
  { name: 'issue', caption: { zh: '问题详情：流程、分区与协作', en: 'Issue details: flow, sections and collaboration' } },
  { name: 'board-still', caption: { zh: '看板：列、泳道与卡片', en: 'Board: columns, swimlanes and cards' } },
  { name: 'backlog', caption: { zh: '待办：史诗与冲刺规划', en: 'Backlog: epic and sprint planning' } },
  { name: 'timeline', caption: { zh: '时间线：史诗、冲刺与截止日期', en: 'Timeline: epics, sprints and due dates' } },
  { name: 'list-still', caption: { zh: '列表：分区列、行内与批量编辑', en: 'List: section columns, inline and bulk edit' } },
  { name: 'filters-still', caption: { zh: '筛选：查询与可配置字段', en: 'Filters: queries and configurable columns' } },
  { name: 'summary', caption: { zh: '项目摘要：四卡与状态环', en: 'Summary: count cards and status ring' } },
  { name: 'reports', caption: { zh: '报表：健康分与吞吐', en: 'Reports: health and throughput' } },
  { name: 'settings-workflow', caption: { zh: '项目设置：可视化工作流、门禁与 SLA', en: 'Settings: visual workflow, gates and SLA' } },
  { name: 'user-settings', caption: { zh: '个人设置：通知、主题与偏好', en: 'Preferences: notifications, theme and behavior' } },
  { name: 'admin', caption: { zh: '管理后台：组织、模板与平台状态', en: 'Admin: org, templates and platform health' } },
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

function Still({ name, alt }: { name: string; alt: L }) {
  return (
    <div className="g-frame">
      <img src={`${MEDIA}/${name}.webp`} alt={pick(alt)} loading="lazy" />
    </div>
  )
}

const TERMS: Entry[] = [
  {
    id: 'project',
    term: { zh: '项目', en: 'Project' },
    meaning: { zh: '一组相关问题的工作空间，有独立的成员、状态、工作流和看板。', en: 'A workspace for related issues, with its own members, statuses, workflows and board.' },
    use: { zh: '在「项目」页进入看板、列表、摘要或设置。', en: 'Open board, list, summary or settings from Projects.' },
    media: 'board-still',
    mediaAlt: { zh: '项目看板', en: 'Project board' },
  },
  {
    id: 'template',
    term: { zh: '部门模板', en: 'Department template' },
    meaning: { zh: '版本化定义项目的默认角色、表单分区、状态和字段（电气 / 机械 / 生产，也可由管理员发布新版本）。项目内可调整状态、转换、门禁与看板；角色/分区结构需通过模板迁移变更。', en: 'A versioned definition of default roles, sections, statuses and fields (Electrical / Mechanical / Production, with admin-published versions). A project can tune statuses, transitions, gates and boards; changing role/section schema requires template migration.' },
    use: { zh: '新建项目时选择；更换模板在项目设置「基本信息」，按向导映射状态、字段与角色。', en: 'Pick when creating a project; migrate under Settings → Basics and map statuses, fields and roles.' },
    media: 'settings-workflow',
    mediaAlt: { zh: '项目设置', en: 'Project settings' },
  },
  {
    id: 'section',
    term: { zh: '分区', en: 'Section' },
    meaning: { zh: '需求表单按角色划分的字段组（如需求方 / 电气组 / 测试方）。只有对应角色或相关人能编辑。', en: 'Role-owned field groups on a requirement (e.g. requester / electrical / tester). Only that role or related people can edit.' },
    use: { zh: '灰掉的字段表示不属于你的分区。', en: 'Grey fields mean that section is not yours.' },
    media: 'issue',
    mediaAlt: { zh: '事项详情分区', en: 'Issue detail sections' },
  },
  {
    id: 'role',
    term: { zh: '项目角色', en: 'Project role' },
    meaning: { zh: '成员在项目中的身份（管理员、需求方、执行组、测试/质量、普通成员等），影响可编辑分区与门禁会签。', en: 'A member’s identity in the project (admin, requester, delivery team, tester/quality, member…), controlling editable sections and gate approvals.' },
  },
  {
    id: 'gate',
    term: { zh: '门禁', en: 'Transition gate' },
    meaning: { zh: '进入某状态前必须满足的条件：必填字段和/或指定角色的会签人数。', en: 'Conditions before entering a status: required fields and/or countersignatures from a role.' },
    use: { zh: '拖不动或流程图下一步灰掉时，先补字段或找人会签。', en: 'If you cannot drag or advance, fill required fields or get approvals.' },
  },
  {
    id: 'countersign',
    term: { zh: '会签', en: 'Countersign' },
    meaning: { zh: '门禁中要求某角色若干人确认后才能进入目标状态。', en: 'A gate that needs a set number of people in a role to approve before the status change.' },
  },
  {
    id: 'sla',
    term: { zh: 'SLA', en: 'SLA' },
    meaning: {
      zh: '服务时限：从创建或重新打开起，按优先级在约定时长内完成。可按自然日或工作日历计算，某些状态可暂停计时。',
      en: 'Service deadline from create/reopen by priority. Uses calendar or business hours; some statuses can pause the clock.',
    },
    read: { zh: '正常 / SLA 即将超时 / SLA 已超时 / 已完成 / 已暂停。与「截止日期」无关。', en: 'OK / SLA due soon / SLA breached / completed / paused — independent of Due date.' },
    use: { zh: '在项目设置「状态与工作流」配置时限与日历；事项详情「处理时限」旁有说明。', en: 'Configure under Settings → Status & workflow; see the tip next to Handle-by on the issue.' },
  },
  {
    id: 'epic',
    term: { zh: '史诗', en: 'Epic' },
    meaning: {
      zh: '主题容器：用「史诗」字段把需求/任务/缺陷归到同一目标下跟踪进度。史诗本身是一等问题，但不出现在看板与冲刺工作量里。',
      en: 'A theme container: link requirements/tasks/bugs via the Epic field. Epics are first-class issues but are excluded from board and sprint workload.',
    },
    read: {
      zh: '进度 = 归属该史诗的工作完成比例（按条数）。完成子工作不会自动完成史诗。',
      en: 'Progress = share of linked work that is done (by count). Finishing children does not auto-complete the epic.',
    },
    use: {
      zh: '详情「史诗下的工作」、摘要进度、时间线分组、待办左侧史诗面板（筛选/拖入归属）。详见上方「规划模型」。',
      en: 'Issues in epic on the detail page, Summary progress, Timeline groups, and the Backlog epic panel. See Planning model above.',
    },
    media: 'backlog',
    mediaAlt: { zh: '待办与史诗面板', en: 'Backlog and epic panel' },
  },
  {
    id: 'parent',
    term: { zh: '父任务 / 子任务', en: 'Parent / sub-task' },
    meaning: {
      zh: '执行层级：只有子任务能有父任务，且只有一层。设置父任务会把类型改为子任务；移除父任务变回任务。史诗不能当父任务。',
      en: 'Execution hierarchy: only sub-tasks have a parent, one level deep. Setting a parent forces type=sub-task; clearing it restores a task. Epics cannot be parents.',
    },
    read: {
      zh: '父任务关闭前可要求先完成子任务；子任务进入进行中时，待办父任务可自动跟进。',
      en: 'Closing a parent can require finishing sub-tasks first; starting a sub-task may pull a todo parent into progress.',
    },
    use: {
      zh: '在详情「子任务清单」快速创建或完整表单；不要用史诗代替父子关系。',
      en: 'Use the Sub-tasks list (quick create or full form). Do not use epics as parents.',
    },
    media: 'issue',
    mediaAlt: { zh: '事项详情与子任务', en: 'Issue detail and sub-tasks' },
  },
  {
    id: 'issue-types',
    term: { zh: '需求 / 任务 / 缺陷 / 故事 / 子任务', en: 'Requirement / task / bug / story / sub-task' },
    meaning: {
      zh: '可执行的工作项类型。可挂史诗，也可再拆子任务。默认列表通常排除史诗，看板也不展示史诗卡片。',
      en: 'Executable work types. They can link to an epic and may have sub-tasks. Default lists exclude epics; boards hide epic cards.',
    },
    use: {
      zh: '日常拖拽、冲刺承诺、健康分与吞吐都只统计这类工作（史诗不计工作量）。',
      en: 'Day-to-day drag, sprint commitment, health and throughput count this work only (epics are not workload).',
    },
  },
  {
    id: 'sprint',
    term: { zh: '冲刺', en: 'Sprint' },
    meaning: {
      zh: '固定时长（通常 1–2 周）的迭代承诺。问题属于至多一个冲刺；未加入的留在待办。史诗本身不进冲刺列表。',
      en: 'A fixed-length commitment window (often 1–2 weeks). An issue is in at most one sprint; otherwise it stays in the backlog. Epics themselves are not sprint work.',
    },
    read: {
      zh: '进行中冲刺通常全项目唯一；完成冲刺时未完成项可移回待办或下一冲刺。',
      en: 'Usually one active sprint per project; incomplete work can move back to backlog or the next sprint when you complete it.',
    },
    use: {
      zh: '在待办页拖入/拖出冲刺；看板只显示当前冲刺（或配置）内的工作。',
      en: 'Drag into/out of sprints on Backlog; the board shows work in the active (or configured) sprint.',
    },
    media: 'backlog',
    mediaAlt: { zh: '待办与冲刺', en: 'Backlog and sprints' },
  },
  {
    id: 'backlog',
    term: { zh: '待办事项', en: 'Backlog' },
    meaning: {
      zh: '尚未排入冲刺的问题池。可按史诗筛选，并把问题拖到史诗上改归属，再按优先级拖入冲刺。',
      en: 'Issues not yet in a sprint. Filter by epic, drop issues onto an epic to set ownership, then drag into a sprint by priority.',
    },
  },
  {
    id: 'swimlane',
    term: { zh: '泳道', en: 'Swimlane' },
    meaning: { zh: '看板按行分组（负责人、机台组、史诗或优先级），便于分看各组进度。', en: 'Horizontal board rows by assignee, machine group, epic or priority.' },
  },
  {
    id: 'wip',
    term: { zh: '在制品上限 (WIP)', en: 'WIP limit' },
    meaning: { zh: '某列同时最多容纳的问题数。达到上限后会拒绝继续拖入；历史数据已超量时列会标红，提醒先完成再开新工。', en: 'Max cards in a column. Once reached, another drop is rejected; an already-over-limit column turns red so the team finishes before starting more.' },
  },
  {
    id: 'jql',
    term: { zh: 'JQL', en: 'JQL' },
    meaning: { zh: '类似 SQL 的查询，如 type = 需求 AND 负责人 = 我。看板与筛选都可用。', en: 'A SQL-like query language used on boards and filters.' },
    use: { zh: '在筛选页用自然语言或直接写条件；也可保存为筛选器。', en: 'Build filters in plain language or JQL on the Filters page, then save them.' },
    media: 'filters-still',
    mediaAlt: { zh: '筛选页', en: 'Filters page' },
  },
  {
    id: 'visibility',
    term: { zh: '可见性', en: 'Visibility' },
    meaning: { zh: '公开：登录用户可见可提需求；仅成员：只有成员能看到项目（相关人仍能看到自己的问题）。', en: 'Public: any signed-in user can view and raise requests. Members-only: only members see the project (involved people still see their issues).' },
  },
  {
    id: 'master',
    term: { zh: '主数据', en: 'Master data' },
    meaning: { zh: '跨项目复用的编码数据（如材料、产线），在字段里以编码/名称引用。', en: 'Shared coded records (materials, lines) referenced from issue fields.' },
  },
  {
    id: 'status-category',
    term: { zh: '状态类别', en: 'Status category' },
    meaning: { zh: 'todo / doing / done 三类，驱动报表、周期时间与「未完成」统计，与具体状态名无关。', en: 'todo / doing / done — drives reports, cycle time and open counts, independent of the status label.' },
  },
  {
    id: 'test-autoflow',
    term: { zh: '测试结果自动流转', en: 'Test-result auto-flow' },
    meaning: {
      zh: '当项目配置了测试中状态时：记录通过 → 配置的通过状态（电气默认「测试通过」，不是最终完成）；不通过 → 失败态并通知负责人。机械/生产通常靠质检字段与门禁。',
      en: 'When a test status is configured: Passed → the configured pass status (Electrical defaults to Test Passed, not final Done); Failed → fail status and notify the assignee. Mechanical/Production usually use quality fields and gates.',
    },
  },
]

const CHARTS: Entry[] = [
  {
    id: 'mw-donut',
    term: { zh: '我的工作 · 状态概览', en: 'My Work · Status overview' },
    meaning: { zh: '你负责的全部事项（含已完成）按状态名称合并后的分布。', en: 'All issues assigned to you (including done), merged by status name.' },
    read: { zh: '环心为总数；悬停看某一状态数量。', en: 'Center shows total; hover a segment for that status count.' },
    use: { zh: '点击图例过滤「分配给我」队列（仅未完成列表有匹配时生效）。', en: 'Click a legend row to filter the Assigned queue when open issues match.' },
    media: 'home',
    mediaAlt: { zh: '我的工作进展图', en: 'My Work progress charts' },
  },
  {
    id: 'mw-priority',
    term: { zh: '我的工作 · 优先级分布', en: 'My Work · Priority breakdown' },
    meaning: { zh: '你负责事项的五档优先级数量。', en: 'Counts of your assigned issues by priority.' },
    use: { zh: '点击柱过滤「分配给我」。', en: 'Click a column to filter Assigned.' },
  },
  {
    id: 'mw-week',
    term: { zh: '我的工作 · 近 7 日计数', en: 'My Work · Last 7 days' },
    meaning: { zh: '完成 / 更新 / 创建 / 即将到期（及逾期副文案）。', en: 'Completed / updated / created / due soon (plus overdue note).' },
    use: { zh: '用来判断本周节奏；有逾期时可点到期卡跳到逾期列表。', en: 'Judge weekly pace; overdue note can jump to the overdue list.' },
  },
  {
    id: 'summary-counts',
    term: { zh: '项目摘要 · 四卡', en: 'Summary · Count cards' },
    meaning: { zh: '过去 7 天完成、更新、创建，以及未来 7 天到期（可带逾期提示）。', en: 'Completed, updated and created in 7 days, plus due in the next 7 days.' },
    media: 'summary',
    mediaAlt: { zh: '项目摘要', en: 'Project summary' },
  },
  {
    id: 'summary-donut',
    term: { zh: '项目摘要 · 状态概览', en: 'Summary · Status overview' },
    meaning: { zh: '本项目全部问题按状态的分布（含 0 数量状态）。', en: 'All project issues by status (including zero counts).' },
  },
  {
    id: 'health',
    term: { zh: '健康分', en: 'Health score' },
    meaning: {
      zh: '从 100 分扣罚：逾期、阻塞、长期未更新、未分配、缺截止日期、创建多于完成等。≥80 健康，≥60 需关注，否则有风险。',
      en: 'Starts at 100 and subtracts penalties (overdue, blocked, stale, unassigned, missing due dates, created ≫ completed…). ≥80 good, ≥60 attention, else risk.',
    },
    use: { zh: '我的工作右轨与报表「健康」页；点分数可进报表。', en: 'Shown on My Work and Reports → Health; click through to dig in.' },
    media: 'reports',
    mediaAlt: { zh: '报表健康页', en: 'Reports health page' },
  },
  {
    id: 'cfd',
    term: { zh: '累积流程图 (CFD)', en: 'Cumulative flow' },
    meaning: { zh: '每天各个具体状态的问题数量堆叠。某条状态色带持续变宽，说明该环节正在积压。', en: 'Daily stacked counts for each concrete status. A widening status band means work is piling up at that step.' },
  },
  {
    id: 'cycle-lead',
    term: { zh: '周期时间 / 前置时间', en: 'Cycle / lead time' },
    meaning: {
      zh: '周期：首次进入进行中 → 完成；前置：创建 → 完成。常用中位数与 P85 看分布。',
      en: 'Cycle: first in-progress → done. Lead: created → done. Median and P85 show the spread.',
    },
  },
  {
    id: 'aging',
    term: { zh: '老化 / 超龄在制品', en: 'Aging WIP' },
    meaning: { zh: '在当前状态停留过久的问题（如 ≥7 天提醒、≥14 天危险）。', en: 'Issues sitting too long in the current status (e.g. warn ≥7d, danger ≥14d).' },
    use: { zh: '优先处理超龄项，防止隐性阻塞。', en: 'Clear aged items first to avoid silent bottlenecks.' },
  },
  {
    id: 'load',
    term: { zh: '负载', en: 'Workload' },
    meaning: { zh: '未完成问题按负责人或部门的分布，用于看忙闲，不是绩效考核。', en: 'Open work by assignee or org unit — capacity, not a performance score.' },
  },
  {
    id: 'quality',
    term: { zh: '质量报表', en: 'Quality report' },
    meaning: { zh: '测试通过率、重开、测试队列等；队列依赖项目的测试中状态配置。', en: 'Pass rate, reopen and test queue — queue uses the project’s test status setting.' },
  },
  {
    id: 'burndown',
    term: { zh: '燃尽 / 燃起', en: 'Burndown / burnup' },
    meaning: {
      zh: '燃尽：剩余工作是否按理想线下降。燃起：范围与完成量随时间变化，范围线上扬表示中途加活。',
      en: 'Burndown: remaining work vs ideal. Burnup: scope vs completed; a rising scope line means work was added.',
    },
  },
  {
    id: 'forecast',
    term: { zh: '完成预测', en: 'Forecast' },
    meaning: { zh: '按近期吞吐估算完成概率（如 P50–P95），帮助判断截止日期是否现实。', en: 'Finish probabilities (e.g. P50–P95) from recent throughput to sanity-check deadlines.' },
  },
  {
    id: 'story-points',
    term: { zh: '故事点', en: 'Story points' },
    meaning: { zh: '相对工作量估算（常用 1、2、3、5、8…），用于冲刺速度，不等于工时。', en: 'Relative effort (often Fibonacci). Used for velocity — not hours.' },
  },
]

const SPRINT_STEPS: { id: string; title: L; body: L }[] = [
  {
    id: 'backlog',
    title: { zh: '1 · 待办', en: '1 · Backlog' },
    body: { zh: '需求/任务先进入待办池，可按史诗归类、排优先级。史诗本身不在此当「工作卡」。', en: 'Work lands in the backlog first — group by epic and prioritize. Epics are not backlog work cards.' },
  },
  {
    id: 'plan',
    title: { zh: '2 · 排进冲刺', en: '2 · Plan sprint' },
    body: { zh: '把本迭代要做的工作拖入冲刺；一条问题同时只属于一个冲刺。', en: 'Drag the work for this iteration into a sprint. An issue belongs to at most one sprint.' },
  },
  {
    id: 'board',
    title: { zh: '3 · 看板上流转', en: '3 · Board flow' },
    body: { zh: '按状态列推进；可按史诗泳道查看。父任务与子任务有完成联动，史诗进度单独汇总。', en: 'Move across status columns; optional epic swimlanes. Parent/sub-task completion links; epic progress rolls up separately.' },
  },
  {
    id: 'done',
    title: { zh: '4 · 完成冲刺', en: '4 · Complete sprint' },
    body: { zh: '已完成留在冲刺记录里；未完成可退回待办或带入下一冲刺。', en: 'Done work stays with the sprint record; incomplete items return to backlog or move to the next sprint.' },
  },
]

const RULES: { ok: boolean; text: L }[] = [
  { ok: true, text: { zh: '史诗 ↔ 工作：用「史诗」字段关联（epic_id）', en: 'Epic ↔ work: link with the Epic field (epic_id)' } },
  { ok: true, text: { zh: '父任务 ↔ 子任务：用「父任务」字段（parent_id），仅一层', en: 'Parent ↔ sub-task: Parent field (parent_id), one level only' } },
  { ok: true, text: { zh: '工作可同时：挂在某史诗下 + 拥有自己的子任务', en: 'Work may be in an epic and also have its own sub-tasks' } },
  { ok: true, text: { zh: '冲刺只装可执行工作；史诗不进看板/冲刺工作量', en: 'Sprints hold executable work; epics stay off board/sprint workload' } },
  { ok: false, text: { zh: '禁止：把史诗设为父任务', en: 'Forbidden: use an epic as a parent' } },
  { ok: false, text: { zh: '禁止：史诗再挂到另一史诗（不能嵌套）', en: 'Forbidden: nest an epic under another epic' } },
  { ok: false, text: { zh: '禁止：子任务下再建子任务', en: 'Forbidden: nest sub-tasks under sub-tasks' } },
  { ok: false, text: { zh: '禁止：已有子项的问题改成子任务', en: 'Forbidden: turn an issue that already has children into a sub-task' } },
]

function HierarchyDiagram() {
  const work: { type: IssueType; label: L }[] = [
    { type: 'requirement', label: { zh: '需求', en: 'Requirement' } },
    { type: 'task', label: { zh: '任务', en: 'Task' } },
    { type: 'bug', label: { zh: '缺陷', en: 'Bug' } },
    { type: 'story', label: { zh: '故事', en: 'Story' } },
  ]
  return (
    <div className="g-model-hier reveal">
      <div className="g-model-rail">
        <div className="g-model-rail-label">{pick({ zh: '主题轨 · 史诗字段', en: 'Theme rail · Epic field' })}</div>
        <div className="g-model-node epic">
          <TypeIcon type="epic" size={20} />
          <div>
            <b>{pick({ zh: '史诗', en: 'Epic' })}</b>
            <span>{pick({ zh: '容器 · 不计工作量', en: 'Container · not workload' })}</span>
          </div>
        </div>
        <div className="g-model-links" aria-hidden>
          <span className="g-model-link-line" />
          <span className="g-model-link-cap">{pick({ zh: 'epic 关联', en: 'epic link' })}</span>
          <span className="g-model-link-line" />
        </div>
        <div className="g-model-work-row">
          {work.map((w) => (
            <div key={w.type} className="g-model-node work">
              <TypeIcon type={w.type} size={18} />
              <b>{pick(w.label)}</b>
            </div>
          ))}
        </div>
      </div>
      <div className="g-model-vs">{pick({ zh: '两套关系，勿混用', en: 'Two relations — do not mix' })}</div>
      <div className="g-model-rail">
        <div className="g-model-rail-label">{pick({ zh: '执行轨 · 父任务字段', en: 'Execution rail · Parent field' })}</div>
        <div className="g-model-node work">
          <TypeIcon type="task" size={20} />
          <div>
            <b>{pick({ zh: '父任务', en: 'Parent' })}</b>
            <span>{pick({ zh: '需求 / 任务 / …', en: 'Requirement / task / …' })}</span>
          </div>
        </div>
        <div className="g-model-links" aria-hidden>
          <span className="g-model-link-line" />
          <span className="g-model-link-cap">{pick({ zh: 'parent 一层', en: 'parent · 1 level' })}</span>
          <span className="g-model-link-line" />
        </div>
        <div className="g-model-node sub">
          <TypeIcon type="subtask" size={18} />
          <div>
            <b>{typeName('subtask')}</b>
            <span>{pick({ zh: '勾选完成 · 可随父任务换冲刺', en: 'Checkbox done · follows parent sprint' })}</span>
          </div>
        </div>
      </div>
    </div>
  )
}

function SprintFlowDiagram() {
  const [step, setStep] = useState(0)
  const [playing, setPlaying] = useState(false)

  useEffect(() => {
    if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) setPlaying(true)
  }, [])

  useEffect(() => {
    if (!playing) return
    const timer = window.setInterval(() => setStep((s) => (s + 1) % SPRINT_STEPS.length), 2800)
    return () => window.clearInterval(timer)
  }, [playing])

  const current = SPRINT_STEPS[step]
  return (
    <div className="g-model-sprint reveal">
      <div className="g-model-sprint-head">
        <h3>{pick({ zh: '冲刺生命周期', en: 'Sprint lifecycle' })}</h3>
        <button type="button" className="btn ghost sm" onClick={() => setPlaying((p) => !p)}>
          {playing ? pick({ zh: '暂停', en: 'Pause' }) : pick({ zh: '自动播放', en: 'Play' })}
        </button>
      </div>
      <div className="g-model-sprint-track" role="list">
        {SPRINT_STEPS.map((s, i) => (
          <button
            key={s.id}
            type="button"
            role="listitem"
            className={`g-model-sprint-step${i === step ? ' active' : ''}${i < step ? ' done' : ''}`}
            onClick={() => {
              setStep(i)
              setPlaying(false)
            }}
          >
            <span className="g-model-sprint-dot" />
            <span className="g-model-sprint-label">{pick(s.title)}</span>
          </button>
        ))}
        <div className="g-model-sprint-progress" style={{ width: `${((step + 1) / SPRINT_STEPS.length) * 100}%` }} />
      </div>
      <div className="g-model-sprint-body" key={current.id}>
        <p>{pick(current.body)}</p>
      </div>
      <div className="g-model-sprint-viz" aria-hidden>
        <div className={`g-model-lane${step === 0 ? ' lit' : ''}`}>
          <span>{pick({ zh: '待办池', en: 'Backlog' })}</span>
          <div className="g-model-cards">
            <i className={step >= 0 ? 'on' : ''} />
            <i className={step >= 0 ? 'on' : ''} />
            <i />
          </div>
        </div>
        <span className={`g-model-arrow${step >= 1 ? ' lit' : ''}`}>→</span>
        <div className={`g-model-lane${step === 1 || step === 2 ? ' lit' : ''}`}>
          <span>{pick({ zh: '冲刺', en: 'Sprint' })}</span>
          <div className="g-model-cards">
            <i className={step >= 1 ? 'on' : ''} />
            <i className={step >= 1 ? 'on sprint' : ''} />
          </div>
        </div>
        <span className={`g-model-arrow${step >= 2 ? ' lit' : ''}`}>→</span>
        <div className={`g-model-lane${step === 2 ? ' lit' : ''}`}>
          <span>{pick({ zh: '看板', en: 'Board' })}</span>
          <div className="g-model-cols">
            <b className={step >= 2 ? 'on' : ''}>{pick({ zh: '待办', en: 'To do' })}</b>
            <b className={step >= 2 ? 'on mid' : ''}>{pick({ zh: '进行中', en: 'Doing' })}</b>
            <b className={step >= 3 ? 'on done' : ''}>{pick({ zh: '完成', en: 'Done' })}</b>
          </div>
        </div>
        <span className={`g-model-arrow${step >= 3 ? ' lit' : ''}`}>→</span>
        <div className={`g-model-lane${step === 3 ? ' lit' : ''}`}>
          <span>{pick({ zh: '收尾', en: 'Wrap-up' })}</span>
          <div className="g-model-cards">
            <i className={step >= 3 ? 'on done' : ''} />
            <i className={step >= 3 ? 'on back' : ''} title={pick({ zh: '未完成回待办', en: 'Incomplete → backlog' })} />
          </div>
        </div>
      </div>
    </div>
  )
}

function RulesPanel() {
  return (
    <div className="g-model-rules reveal">
      <h3>{pick({ zh: '关系与制约一览', en: 'Relations & constraints' })}</h3>
      <ul>
        {RULES.map((r) => (
          <li key={pick(r.text)} className={r.ok ? 'ok' : 'no'}>
            <span aria-hidden>{r.ok ? '✓' : '✕'}</span>
            {pick(r.text)}
          </li>
        ))}
      </ul>
      <p className="g-model-note">
        {pick({
          zh: '记忆口诀：史诗管主题，父子管拆解，冲刺管时间盒。三者正交，不要用其中一套代替另一套。',
          en: 'Mnemonic: epics = theme, parent/sub-task = breakdown, sprint = time-box. Orthogonal — never substitute one for another.',
        })}
      </p>
    </div>
  )
}

function CombinedExample() {
  return (
    <div className="g-model-example reveal">
      <h3>{pick({ zh: '一张图看懂组合', en: 'How they combine' })}</h3>
      <div className="g-model-example-tree">
        <div className="g-model-ex-epic">
          <TypeIcon type="epic" size={18} />
          <div>
            <b>{pick({ zh: '史诗：推理服务上线 CMB-2', en: 'Epic: Inference on CMB-2' })}</b>
            <span>{pick({ zh: '摘要/时间线看整体进度', en: 'Track overall progress on Summary / Timeline' })}</span>
          </div>
        </div>
        <div className="g-model-ex-children">
          <div className="g-model-ex-card">
            <div className="g-model-ex-head">
              <TypeIcon type="requirement" size={16} />
              <b>{pick({ zh: '需求：环境与部署', en: 'Req: env & deploy' })}</b>
              <span className="g-model-ex-tag">{pick({ zh: '冲刺 S12', en: 'Sprint S12' })}</span>
            </div>
            <div className="g-model-ex-subs">
              <span><TypeIcon type="subtask" size={14} /> {pick({ zh: '子任务：装依赖', en: 'Sub-task: install deps' })}</span>
              <span><TypeIcon type="subtask" size={14} /> {pick({ zh: '子任务：冒烟验证', en: 'Sub-task: smoke test' })}</span>
            </div>
          </div>
          <div className="g-model-ex-card">
            <div className="g-model-ex-head">
              <TypeIcon type="task" size={16} />
              <b>{pick({ zh: '任务：监控告警', en: 'Task: monitoring' })}</b>
              <span className="g-model-ex-tag muted">{pick({ zh: '仍在待办', en: 'Still backlog' })}</span>
            </div>
            <p>{pick({ zh: '已挂史诗，但尚未进冲刺 → 只在待办出现。', en: 'Linked to the epic, not in a sprint yet → backlog only.' })}</p>
          </div>
        </div>
      </div>
      <div className="g-model-shots">
        <figure>
          <Still name="backlog" alt={{ zh: '待办与冲刺', en: 'Backlog and sprint' }} />
          <figcaption>{pick({ zh: '待办：排冲刺 + 史诗面板', en: 'Backlog: sprint plan + epic panel' })}</figcaption>
        </figure>
        <figure>
          <Still name="board-still" alt={{ zh: '看板', en: 'Board' }} />
          <figcaption>{pick({ zh: '看板：冲刺内工作流转', en: 'Board: flow work inside the sprint' })}</figcaption>
        </figure>
      </div>
    </div>
  )
}

function PlanningModelSection() {
  return (
    <section id="model" className="g-section g-model-section">
      <div className="g-inner">
        <header className="g-head reveal">
          <p className="g-eyebrow">{pick({ zh: '规划模型', en: 'Planning model' })}</p>
          <h2 className="g-title">
            {pick({ zh: '史诗、任务、冲刺如何咬合。', en: 'How epics, tasks and sprints fit.' })}
          </h2>
          <p className="g-body">
            {pick({
              zh: 'Mira 沿用行业常见的双轨模型：史诗管主题归类，父子管执行拆解，冲刺管时间盒。下面用动图与对照表一次讲清。',
              en: 'Mira follows the usual dual model: epics for themes, parent/sub-task for breakdown, sprints for time-boxes. Diagrams and rules below.',
            })}
          </p>
        </header>
        <HierarchyDiagram />
        <SprintFlowDiagram />
        <div className="g-model-split">
          <RulesPanel />
          <CombinedExample />
        </div>
      </div>
    </section>
  )
}

function EntryCard({ entry, i }: { entry: Entry; i: number }) {
  const hasMedia = Boolean(entry.media)
  return (
    <article
      id={entry.id}
      className={`g-gloss-card reveal${hasMedia ? ' has-media' : ''}`}
      style={{ transitionDelay: `${(i % 4) * 40}ms` }}
    >
      <div className="g-gloss-copy">
        <h3>{pick(entry.term)}</h3>
        <p className="g-gloss-meaning">{pick(entry.meaning)}</p>
        {entry.read && (
          <p className="g-gloss-meta">
            <span>{pick({ zh: '怎么读', en: 'How to read' })}</span>
            {pick(entry.read)}
          </p>
        )}
        {entry.use && (
          <p className="g-gloss-meta">
            <span>{pick({ zh: '怎么用', en: 'How to use' })}</span>
            {pick(entry.use)}
          </p>
        )}
      </div>
      {entry.media && (
        <figure className="g-gloss-shot">
          <Still name={entry.media} alt={entry.mediaAlt ?? entry.term} />
          {entry.mediaAlt && <figcaption>{pick(entry.mediaAlt)}</figcaption>}
        </figure>
      )}
    </article>
  )
}

export function GuideGlossaryPage() {
  const root = useRef<HTMLDivElement>(null)
  const location = useLocation()
  const [active, setActive] = useState('demos')
  useReveal(root)

  useEffect(() => {
    const id = location.hash.slice(1)
    if (id) requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' }))
  }, [location.hash])

  useEffect(() => {
    const sections = ['demos', 'model', 'terms', 'charts'].map((id) => document.getElementById(id)).filter((el): el is HTMLElement => !!el)
    const observer = new IntersectionObserver(
      (entries) => {
        const hit = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0]
        if (hit) setActive(hit.target.id)
      },
      { rootMargin: '-40% 0px -50% 0px' },
    )
    sections.forEach((s) => observer.observe(s))
    return () => observer.disconnect()
  }, [])

  return (
    <div className="guide g-glossary" ref={root}>
      <nav className="g-localnav">
        <div className="g-localnav-inner">
          <Link className="g-localnav-title" to="/guide">
            {pick({ zh: '← 使用说明', en: '← Guide' })}
          </Link>
          <div className="g-localnav-links">
            {NAV.map((n) => (
              <a key={n.id} href={`#${n.id}`} className={active === n.id ? 'active' : ''}>
                {pick(n.label)}
              </a>
            ))}
          </div>
          <Link to="/" className="g-pill">
            {pick({ zh: '开始使用', en: 'Open app' })}
          </Link>
        </div>
      </nav>

      <section className="g-hero g-gloss-hero">
        <div className="g-inner">
          <p className="g-eyebrow reveal">{pick({ zh: '名词与图表', en: 'Glossary' })}</p>
          <h1 className="g-hero-title reveal">
            {pick({ zh: '把屏幕上的词和数字讲清楚。', en: 'Every label and number, explained.' })}
          </h1>
          <p className="g-hero-sub reveal">
            {pick({
              zh: '界面示例 + 规划模型（史诗 / 任务 / 冲刺）+ 专有名词 + 报表指标。可从使用说明跳转对照真实画面。',
              en: 'UI demos, the planning model (epic / task / sprint), terms and chart metrics — linked from the guide.',
            })}
          </p>
          <div className="g-ctas reveal" style={{ marginTop: 18 }}>
            <a href="#model" className="g-btn primary">
              {pick({ zh: '看规划模型', en: 'Planning model' })}
            </a>
            <a href="#terms" className="g-link">
              {pick({ zh: '专有名词', en: 'Terms' })} ›
            </a>
          </div>
        </div>
      </section>

      <section id="demos" className="g-section tint">
        <div className="g-inner">
          <header className="g-head reveal">
            <p className="g-eyebrow">{pick({ zh: '界面示例', en: 'UI demos' })}</p>
            <h2 className="g-title">{pick({ zh: '按本次发布重新生成。', en: 'Regenerated for this release.' })}</h2>
            <p className="g-body">
              {pick({
                zh: '下列静帧由演示脚本从 staging 统一生成，使用演示账号业务数据；页面与交互仍以实际项目配置和权限为准。',
                en: 'These stills are generated together from staging with demo workload. Actual pages and actions still follow your project configuration and permissions.',
              })}
            </p>
          </header>
          <div className="g-demo-strip">
            {DEMOS.map((d, i) => (
              <figure key={d.name} className="reveal" style={{ transitionDelay: `${(i % 3) * 50}ms` }}>
                <Still name={d.name} alt={d.caption} />
                <figcaption>{pick(d.caption)}</figcaption>
              </figure>
            ))}
          </div>
        </div>
      </section>

      <PlanningModelSection />

      <section id="terms" className="g-section">
        <div className="g-inner">
          <header className="g-head reveal">
            <p className="g-eyebrow">{pick({ zh: '专有名词', en: 'Terms' })}</p>
            <h2 className="g-title">{pick({ zh: '协作里常听到的词。', en: 'Words you will keep hearing.' })}</h2>
          </header>
          <div className="g-gloss-grid">
            {TERMS.map((e, i) => (
              <EntryCard key={e.id} entry={e} i={i} />
            ))}
          </div>
        </div>
      </section>

      <section id="charts" className="g-section tint">
        <div className="g-inner">
          <header className="g-head reveal">
            <p className="g-eyebrow">{pick({ zh: '图表与指标', en: 'Charts & metrics' })}</p>
            <h2 className="g-title">{pick({ zh: '数字在说什么。', en: 'What the numbers say.' })}</h2>
            <p className="g-body">
              {pick({
                zh: '覆盖我的工作进展、项目摘要与报表页。含义 · 怎么读 · 怎么用，并配界面截图。',
                en: 'My Work, Summary and Reports — meaning, how to read, how to use, with UI stills.',
              })}
            </p>
          </header>
          <div className="g-gloss-grid">
            {CHARTS.map((e, i) => (
              <EntryCard key={e.id} entry={e} i={i} />
            ))}
          </div>
        </div>
      </section>

      <section className="g-section g-end">
        <div className="g-inner">
          <h2 className="g-title reveal">{pick({ zh: '回到使用说明', en: 'Back to the guide' })}</h2>
          <div className="g-ctas reveal">
            <Link to="/guide" className="g-btn primary">
              {pick({ zh: '打开使用说明', en: 'Open the guide' })}
            </Link>
            <Link to="/guide#settings" className="g-link">
              {pick({ zh: '项目设置讲解', en: 'Project settings' })} ›
            </Link>
          </div>
        </div>
      </section>
    </div>
  )
}
