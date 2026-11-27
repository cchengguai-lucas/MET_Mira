import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('input[autocomplete="username"]').fill('admin')
  await page.locator('input[type="password"]').fill('admin-pass')
  await page.getByRole('button', { name: /登录|Sign in/ }).click()
  await expect(page.locator('.page-header').first()).toBeVisible()
})

test('creates a delivery project without dates and shows the reminder tip', async ({ page }) => {
  const suffix = Date.now().toString().slice(-6)
  const key = `D${suffix}`

  await page.goto('/projects')
  await page.getByRole('button', { name: /＋ 新建项目|\+ Create project/ }).click()
  await expect(page.getByText(/交付项目|Delivery project/)).toBeVisible()
  await expect(page.getByText(/计划开始日期|Planned start date/)).toBeVisible()

  const created = await page.request.post('/api/projects', {
    data: {
      key,
      name: `交付 ${suffix}`,
      profile: 'electrical',
      work_mode: 'delivery',
    },
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  await page.goto(`/projects/${key}/settings`)
  await expect(page.getByText(/请尽快补齐计划开始日期和目标日期|Add the planned start and target dates soon/)).toBeVisible()
  await expect(page.getByText('计划基线').or(page.getByText('Schedule baseline'))).toBeVisible()
})

test('ongoing projects hide schedule baseline controls', async ({ page }) => {
  const suffix = Date.now().toString().slice(-6)
  const key = `O${suffix}`
  const created = await page.request.post('/api/projects', {
    data: {
      key,
      name: `持续 ${suffix}`,
      profile: 'electrical',
      work_mode: 'ongoing',
    },
  })
  expect(created.ok(), await created.text()).toBeTruthy()

  await page.goto(`/projects/${key}/settings`)
  await expect(page.getByText(/持续项目|Ongoing project/).first()).toBeVisible()
  await expect(page.getByText('计划开始日期').or(page.getByText('Planned start date'))).toHaveCount(0)
  await expect(page.getByText('计划基线').or(page.getByText('Schedule baseline'))).toHaveCount(0)

  const baseline = await page.request.get(`/api/projects/${key}/schedule-baseline`)
  expect(baseline.ok()).toBeTruthy()
  const body = await baseline.json()
  expect(body.ready).toBeFalsy()
  expect(body.blockers.join(' ')).toMatch(/持续项目/)
})
