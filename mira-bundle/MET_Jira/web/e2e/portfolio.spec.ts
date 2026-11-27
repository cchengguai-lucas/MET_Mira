import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('input[autocomplete="username"]').fill('admin')
  await page.locator('input[type="password"]').fill('admin-pass')
  await page.getByRole('button', { name: /登录|Sign in/ }).click()
  await expect(page.locator('.page-header').first()).toBeVisible()
})

test('admin control tower switches risk-ranked finishable goals', async ({ page }) => {
  const key = `G${Date.now().toString(36).slice(-5)}`.toUpperCase()
  const created = await page.request.post('/api/projects', {
    data: {
      key,
      name: 'Goal demo',
      description: '交付可验证的样机工装',
      plan_start_date: '2099-01-01',
      target_date: '2099-06-30',
    },
  })
  expect(created.ok()).toBeTruthy()

  await page.goto('/')
  await expect(page.locator('.portfolio-home')).toBeVisible()
  await expect(page.locator('.home-greet')).toContainText(/早上好|中午好|下午好|晚上好|Good/)
  await expect(page.locator('.control-summary-grid')).toBeVisible()
  await page.locator('.control-project-row').filter({ hasText: key }).click()
  await expect(page.locator('.control-project-detail')).toContainText('Goal demo')
  await expect(page.getByText(/项目进展图|Project progress/)).toBeVisible()
  await expect(page.getByText(/目标预测图|Target forecast/)).toBeVisible()
  await expect(page.getByText(/风险分布图|Risk distribution/)).toBeVisible()
  await expect(page.getByText(/顺序时间线缩略图|Sequence timeline preview/)).toBeVisible()
  await expect(page.getByText(/能否按时完结|finish on time/)).toBeVisible()
  await expect(page.getByText(/延误从哪里开始|slip start/)).toBeVisible()
  await expect(page.getByText(/哪些 Gate 受影响|Gates are impacted/)).toBeVisible()
  await expect(page.getByText(/需要什么决策|decision is needed/)).toBeVisible()
})
