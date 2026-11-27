import { describe, expect, it } from 'vitest'
import { RELEASE_ID, releaseInsertIndex, releaseInstant, releaseUnseen } from './releaseNotes'

describe('release notes', () => {
  it('opens until this release has been dismissed', () => {
    expect(releaseUnseen(null)).toBe(true)
    expect(releaseUnseen('2026.10.01')).toBe(true)
    expect(releaseUnseen(RELEASE_ID)).toBe(false)
  })

  it('sorts newer notifications above the note', () => {
    const at = releaseInstant()
    const newer = new Date(at + 60_000).toISOString()
    const older = new Date(at - 60_000).toISOString()
    expect(releaseInsertIndex([])).toBe(0)
    expect(releaseInsertIndex([older])).toBe(0)
    expect(releaseInsertIndex([newer, older])).toBe(1)
    expect(releaseInsertIndex([newer, newer])).toBe(2)
  })
})
