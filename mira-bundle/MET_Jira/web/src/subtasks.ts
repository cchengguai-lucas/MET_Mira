import { ApiError } from './api'
import { t } from './i18n'
import type { IssueDetail, ProjectDetail, Status } from './types'
import { isClosed, isCompleted, REJECTED, typeRules } from './workflow'

/**
 * Run a status change; if the backend refuses because sub-tasks are still open, ask whether to
 * finish them too and retry. Resolves to null when the user declines.
 */
export async function withSubtaskClose<T>(run: (closeSubtasks: boolean) => Promise<T>): Promise<T | null> {
  try {
    return await run(false)
  } catch (err) {
    if (!(err instanceof ApiError) || err.code !== 'open_subtasks') throw err
    if (!window.confirm(`${err.message}\n\n${t('是否将这些子任务一并标记为完成？')}`)) return null
    return run(true)
  }
}

/** The status a sub-task checkbox moves to: done when open, reopened (or back to to-do) when done. */
export function checkboxTarget(project: ProjectDetail, current: Status): Status | undefined {
  const rules = typeRules(project, 'subtask')
  const names = rules?.[current.name] ?? []
  const targets = project.statuses.filter((s) => names.includes(s.name))
  if (isClosed(current)) return targets.find((s) => !isClosed(s))
  return targets.find((s) => isCompleted(s)) ?? targets.find((s) => isClosed(s) && s.name !== REJECTED)
}

/** Tell the user when finishing a sub-task completed the last one of its parent. */
export function lastSubtaskMessage(result: IssueDetail | null): string | null {
  const progress = result?.parent_progress
  if (!result?.parent || !progress || !isCompleted(result.status)) return null
  return progress.done === progress.total ? t('{key} 的子任务已全部完成，可以继续推进父任务了', { key: result.parent.key }) : null
}
