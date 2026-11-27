import { expect, test, type Locator } from '@playwright/test'

function channels(color: string) {
  const rgb = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/)
  if (rgb) return { r: Number(rgb[1]), g: Number(rgb[2]), b: Number(rgb[3]) }
  const srgb = color.match(/color\(srgb\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)/)
  if (srgb) {
    return {
      r: Math.round(Number(srgb[1]) * 255),
      g: Math.round(Number(srgb[2]) * 255),
      b: Math.round(Number(srgb[3]) * 255),
    }
  }
  throw new Error(`unparsed color: ${color}`)
}

async function thumb(tri: Locator) {
  return channels(await tri.evaluate((el) => getComputedStyle(el, '::before').backgroundColor))
}

test('yes, no and unset use different colors', async ({ page }) => {
  await page.goto('/login')
  await page.locator('input[autocomplete="username"]').fill('admin')
  await page.locator('input[type="password"]').fill('admin-pass')
  await page.getByRole('button', { name: /登录|Sign in/ }).click()
  await expect(page.locator('.page-header').first()).toBeVisible()

  const key = `TC${Date.now().toString().slice(-6)}`
  const project = await page.request.post('/api/projects', {
    data: { key, name: `颜色 ${key}`, profile: 'electrical' },
  })
  expect(project.ok(), await project.text()).toBeTruthy()
  const created = await page.request.post('/api/issues', {
    data: { project: key, type: 'requirement', title: '三态颜色', due_date: '2099-12-31' },
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  const issue = await created.json()
  await page.goto(`/issues/${issue.key}`)

  const tri = page.locator('.label:text-is("是否涉及软件") + div .segmented.tri')
  await expect(tri).toBeVisible()
  const yes = tri.locator('button.v-true')
  const no = tri.locator('button.v-false')
  const empty = tri.locator('button.v-null')

  await expect(yes).toHaveCSS('color', 'rgb(31, 122, 53)')
  await expect(no).toHaveCSS('color', 'rgb(215, 0, 21)')
  const unset = channels(await empty.evaluate((el) => getComputedStyle(el).color))
  expect(Math.abs(unset.r - unset.g)).toBeLessThan(8)
  expect(Math.abs(unset.g - unset.b)).toBeLessThan(8)

  const neutral = await thumb(tri)
  expect(Math.abs(neutral.r - neutral.g)).toBeLessThan(8)

  await yes.click()
  await expect(tri).toHaveAttribute('data-value', 'true')
  const pass = await thumb(tri)
  expect(pass.g).toBeGreaterThan(pass.r + 8)
  expect(pass.g).toBeGreaterThan(pass.b + 8)

  await no.click()
  await expect(tri).toHaveAttribute('data-value', 'false')
  const fail = await thumb(tri)
  expect(fail.r).toBeGreaterThan(fail.g + 8)
  expect(fail.r).toBeGreaterThan(fail.b + 8)

  await empty.click()
  await expect(tri).toHaveAttribute('data-value', 'null')
  const back = await thumb(tri)
  expect(Math.abs(back.r - back.g)).toBeLessThan(8)
})

test('software section glides open and closed', async ({ page }) => {
  await page.goto('/login')
  await page.locator('input[autocomplete="username"]').fill('admin')
  await page.locator('input[type="password"]').fill('admin-pass')
  await page.getByRole('button', { name: /登录|Sign in/ }).click()
  await expect(page.locator('.page-header').first()).toBeVisible()

  const key = `SF${Date.now().toString().slice(-6)}`
  const project = await page.request.post('/api/projects', {
    data: { key, name: `展开 ${key}`, profile: 'electrical' },
  })
  expect(project.ok(), await project.text()).toBeTruthy()
  const created = await page.request.post('/api/issues', {
    data: { project: key, type: 'requirement', title: '软件组展开', due_date: '2099-12-31' },
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  const issue = await created.json()
  await page.goto(`/issues/${issue.key}`)

  const tri = page.locator('.label:text-is("是否涉及软件") + div .segmented.tri')
  const fold = page.locator('.fold.card-fold')
  await expect(tri).toBeVisible()
  await expect(fold).toHaveCount(1)
  const height = () => fold.evaluate((el) => el.getBoundingClientRect().height)
  expect(await height()).toBeLessThan(2)
  const transition = await fold.evaluate((el) => getComputedStyle(el).transitionProperty)
  expect(transition).toContain('grid-template-rows')

  await tri.locator('button.v-true').click()
  await expect(fold).toHaveClass(/open/)
  await expect(fold.getByText('软件组填写')).toBeVisible()
  await expect.poll(height).toBeGreaterThan(48)

  await tri.locator('button.v-false').click()
  await expect(fold).not.toHaveClass(/\bopen\b/)
  await expect.poll(height).toBeLessThan(2)

  await page.goto(`/projects/${key}`)
  await page.getByRole('button', { name: '创建', exact: true }).click()
  const modal = page.locator('.modal')
  await expect(modal).toBeVisible()
  await modal.getByRole('button', { name: /电气组字段/ }).click()
  const owner = modal.locator('div.fold').filter({ has: page.locator('.field', { hasText: '软件负责人' }) }).last()
  const ownerHeight = () => owner.evaluate((el) => el.getBoundingClientRect().height)
  expect(await ownerHeight()).toBeLessThan(2)
  await modal.locator('.segmented.tri button.v-true').click()
  await expect(owner).toHaveClass(/open/)
  await expect.poll(ownerHeight).toBeGreaterThan(24)
})

test('board column grows to fit long card footer without clipping avatar', async ({ page }) => {
  await page.setViewportSize({ width: 980, height: 800 })
  await page.goto('/login')
  await page.locator('input[autocomplete="username"]').fill('admin')
  await page.locator('input[type="password"]').fill('admin-pass')
  await page.getByRole('button', { name: /登录|Sign in/ }).click()
  await expect(page.locator('.page-header').first()).toBeVisible()

  const key = `PVDALPHA${Date.now().toString().slice(-2)}`
  const project = await page.request.post('/api/projects', {
    data: { key, name: `看板 ${key}`, profile: 'electrical' },
  })
  expect(project.ok(), await project.text()).toBeTruthy()
  const created = await page.request.post('/api/issues', {
    data: {
      project: key,
      type: 'requirement',
      title: 'R5机台改为G2 Cu',
      due_date: '2026-11-02',
      assignee_id: 1,
      priority: 'high',
      story_points: 8,
    },
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  const issue = await created.json()
  const sub = await page.request.post('/api/issues', {
    data: { project: key, type: 'subtask', title: '子任务', parent_id: issue.id, due_date: '2026-11-02' },
  })
  expect(sub.ok(), await sub.text()).toBeTruthy()

  await page.goto(`/projects/${key}/board`)
  const card = page.locator('.issue-card').filter({ has: page.locator('.issue-key', { hasText: issue.key }) })
  await expect(card).toBeVisible()
  const geometry = await card.evaluate((el) => {
    const cardBox = el.getBoundingClientRect()
    const colBox = el.closest('.column')!.getBoundingClientRect()
    const avatar = el.querySelector('.meta.foot .avatar') as HTMLElement
    const avatarBox = avatar.getBoundingClientRect()
    return {
      cardRight: cardBox.right,
      colRight: colBox.right,
      avatarRight: avatarBox.right,
    }
  })
  expect(geometry.avatarRight).toBeLessThanOrEqual(geometry.cardRight)
  expect(geometry.cardRight).toBeLessThanOrEqual(geometry.colRight)
})
