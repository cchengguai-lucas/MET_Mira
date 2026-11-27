/** Release announcement shown once in the notification menu. */
export const releaseNotes: Record<string, string> = {
  'Mira 的新变化': "What's New in Mira",
  用一句话修改工作流: 'Edit a workflow in one sentence',
  '项目管理员可以在「设置 → 状态与工作流」用一句话说明想怎么改，也可以要求新增状态。新状态在应用到画布时加入项目，流转要再保存才生效。页面会列出必须修改、建议修改和可选的改动，由你决定是否采用。':
    'Project admins can describe a workflow change on Settings → Status and workflow, including a new status. A new status joins the project when you apply it. Transitions take effect after you save. The page also lists required, suggested, and optional changes for you to accept.',
  标明谁挡住谁: 'Show who blocks whom',
  '添加问题关联时选择「阻塞」或「被阻塞」。保存前会看到方向，延期会按这个方向影响对方或自己的预测日期。':
    'When linking issues, choose Blocks or Is blocked by. The direction is shown before you save, and a delay moves the forecast for the other issue or for this one.',
  发布排期基线: 'Publish a schedule baseline',
  '项目可以发布一份之后不再改动的排期基线。时间线会同时显示基线、当前计划和预测。系统给出的改期建议，需要确认并应用后才会写入。':
    'A project can publish a schedule baseline that stays unchanged. The timeline shows the baseline, the current plan, and the forecast together. A suggested date change is saved only after you confirm and apply it.',
  在工作台查看风险: 'See risk from the workspace',
  '工作台按风险排列项目，并用当前排期给出一段简短判断。需要更具体的建议时，可以让 AI 助手先读取项目数据再分析。':
    'The workspace ranks projects by risk and writes a short assessment from the current schedule. For a closer look, ask the AI assistant to read the project data first.',
  '此更新还包括缺陷修复和界面优化。': 'This update also includes bug fixes and interface improvements.',
  电气需求要三方确认方案: 'Electrical requirements need a three-party scheme confirmation',
  '电气需求在「方案讨论」里填写方案说明，点「发起方案确认」后由电气、工艺和涉及软件时的软件负责人分别确认。三方都确认后才能进入「研发中」。确认后会显示签字人。改方案前要先解锁；如果方案有实质变化，需要重新确认。':
    'In scheme discussion, write the scheme and request confirmation. Electrical, process, and the software owner when software is involved each confirm. The issue can enter In Development only after that. A yes shows who signed. Unlock before editing; a material change must be confirmed again.',
  电气流程增加开发完成: 'Electrical work adds Development complete',
  '电气的需求、缺陷和故事在「研发中」之后进入「开发完成」，再进入测试。涉及软件时，软件 Jira 还不是「待上机验证」就点下一步，问题会留在「研发中」等待；软件到达该状态后自动进入测试，并通知测试人。':
    'Electrical requirements, bugs, and stories move from In Development to Development complete, then to testing. When software is involved and the software Jira is not yet Awaiting machine verification, Next keeps the issue in In Development. Once it reaches that status, the issue enters testing on its own and the tester is notified.',
  '软件 Jira 显示在单据上': 'Software Jira appears on the issue',
  '填写软件单号后，单据显示软件侧的负责人和测试人、状态和截止日期。状态到「待上机验证」时，软件是否准备好自动标为是，能对上通讯录的人会加入关注。软件截止日期晚于本问题时，会邮件请双方确认时间。':
    'After you enter a software issue key, the issue shows that issue\'s assignee, tester, status, and due date. Awaiting machine verification marks software ready and adds matched directory people as watchers. If the software due date is later than this issue, both sides get an email to confirm the dates.',
  选人只看本项目相关成员: 'People lists stay inside the project',
  '单据上的负责人从负责执行的项目成员和项目管理员里选，测试人从测试成员里选，自定义人员从本项目成员里选。软件负责人仍只从该项目的软件部门里选。':
    'On an issue, the assignee list is the project members who do the work, plus project admins. The tester list is the project testers. Custom person fields list project members. The software owner list stays inside that project\'s software department.',
  同名部门按完整路径区分: 'Show a department by its full path',
  '按组织加人时，选项显示完整路径和人数，并预览该范围内的在职人员。':
    'When you add people by department, each option shows the full path and the headcount, and previews the active people in that scope.',
  成员角色可以批量调整: 'Change member roles in bulk',
  '在成员表勾选多人后，可以一次新增或移除角色，也可以改一个人的全部角色。按住复选框滑动可以连续勾选。':
    'Select people in the member table, then add or remove roles for all of them, or edit every role for one person. Drag across the checkboxes to select a run of people.',
  '此更新还包括方案解锁图标，以及成员操作条固定在原位。':
    'This update also adds the scheme unlock icon and keeps the member action bar in place.',
  工作流错误可以一次修好: 'Workflow errors can be repaired in one step',
  '在「设置 → 状态与工作流」里，连线还指向已删除的模板状态时，「修复全部错误」会把这些状态补回来。流程没有完成状态时，会补上「已完成」并接到能走完的路径。放到画布后，再保存工作流才生效。':
    'On Settings → Status and workflow, Fix all errors restores a template status when a line still points to it. A flow with nowhere to finish gains Done and a path to it. Save the workflow after you put the draft on the canvas.',
}
