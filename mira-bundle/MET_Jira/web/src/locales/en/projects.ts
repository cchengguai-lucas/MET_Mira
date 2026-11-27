/** Project list / create strings loaded with the projects route. */
export const projects: Record<string, string> = {
  项目类型: 'Project type',
  交付项目: 'Delivery project',
  持续项目: 'Ongoing project',
  '有明确目标日期，可发布排期基线并跟踪延期风险。':
    'Has an explicit target date, can publish a schedule baseline, and tracks delay risk.',
  '长期事务、备忘跟进或杂项清单；保留完整流转，不发布排期基线。':
    'Long-running work, memo follow-ups, or mixed lists. Full issue flow stays on; no schedule baseline.',
  '选填。完整排期基线在创建后、有单据时再发布。跳过会提醒你稍后补充。':
    'Optional. Publish the full schedule baseline after create, once issues exist. Skipping reminds you to fill dates later.',
  '已创建项目。请尽快在设置中补齐计划开始日期和目标日期，以便后续发布排期基线。':
    'Project created. Add the planned start and target dates in settings soon so you can publish a schedule baseline later.',
  置顶: 'Pin',
  取消置顶: 'Unpin',
  置顶项目: 'Pinned',
}
