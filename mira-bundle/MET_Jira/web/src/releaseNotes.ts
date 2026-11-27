/**
 * Current release notes. Change `RELEASE_ID` when the notes change.
 * The notification menu opens once per browser for that id.
 * The note stays in that menu and sorts by release date, so newer notifications appear above it.
 * Describe only changes that affect how people work. Keep bug fixes and visual polish in `RELEASE_ALSO`.
 */
export const RELEASE_ID = '2026.11.09.2'
export const RELEASE_DATE = '2026-11-09'
export const RELEASE_SEEN_KEY = 'metjira.release'

export const RELEASE_ITEMS: { title: string; body: string }[] = [
  {
    title: '同名部门按完整路径区分',
    body: '按组织加人时，选项显示完整路径和人数，并预览该范围内的在职人员。',
  },
  {
    title: '成员角色可以批量调整',
    body: '在成员表勾选多人后，可以一次新增或移除角色，也可以改一个人的全部角色。按住复选框滑动可以连续勾选。',
  },
]

export const RELEASE_ALSO = '此更新还包括方案解锁图标，以及成员操作条固定在原位。'

/** Local midnight of the release date. Newer notifications sort above the note. */
export function releaseInstant(): number {
  const [year, month, day] = RELEASE_DATE.split('-').map(Number)
  return new Date(year, month - 1, day).getTime()
}

/** Where the note belongs in a newest-first list of notification timestamps. */
export function releaseInsertIndex(createdAt: string[]): number {
  const at = releaseInstant()
  const index = createdAt.findIndex((value) => Date.parse(value) <= at)
  return index === -1 ? createdAt.length : index
}

export function releaseUnseen(stored: string | null): boolean {
  return stored !== RELEASE_ID
}

export function releaseAutoOpen(): boolean {
  if (typeof navigator !== 'undefined' && navigator.webdriver) return false
  try {
    return releaseUnseen(localStorage.getItem(RELEASE_SEEN_KEY))
  } catch {
    return false
  }
}

export function markReleaseSeen(): void {
  try {
    localStorage.setItem(RELEASE_SEEN_KEY, RELEASE_ID)
  } catch {
    /* Private mode can reject storage; the menu still closes. */
  }
}
