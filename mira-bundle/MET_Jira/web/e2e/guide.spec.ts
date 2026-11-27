import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('input[autocomplete="username"]').fill('admin')
  await page.locator('input[type="password"]').fill('admin-pass')
  await page.getByRole('button', { name: /登录|Sign in/ }).click()
  await expect(page.locator('.page-header').first()).toBeVisible()
})

test('guide navigation targets existing sections', async ({ page }) => {
  await page.goto('/guide')
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()

  const targets = await page.locator('.g-localnav-links a').evaluateAll((links) =>
    links.map((link) => link.getAttribute('href')).filter((href): href is string => Boolean(href)),
  )
  for (const target of targets) {
    await expect(page.locator(target), `missing Guide section ${target}`).toHaveCount(1)
  }

  await page.goto('/guide/glossary')
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  const glossaryTargets = await page.locator('.g-localnav-links a').evaluateAll((links) =>
    links.map((link) => link.getAttribute('href')).filter((href): href is string => Boolean(href)),
  )
  for (const target of glossaryTargets) {
    await expect(page.locator(target), `missing Glossary section ${target}`).toHaveCount(1)
  }
})

test('all current guide demo assets are available', async ({ request }) => {
  const stills = [
    'login',
    'home',
    'issue',
    'board-still',
    'backlog',
    'timeline',
    'list-still',
    'filters-still',
    'summary',
    'reports',
    'settings-workflow',
    'user-settings',
    'admin',
    'org',
    'notifications',
    'palette-still',
    'create-still',
    'ai-filter-still',
  ]
  const demos = ['my-work', 'create', 'workflow', 'board', 'list', 'ai-filter', 'filters', 'ai-assistant', 'palette']

  for (const name of stills) {
    expect((await request.get(`/guide/${name}.webp`)).ok(), `missing ${name}.webp`).toBeTruthy()
  }
  for (const name of demos) {
    expect(
      (await request.get(`/guide/${name}-demo.webp`)).ok(),
      `missing ${name}-demo.webp`,
    ).toBeTruthy()
  }
})

test('guide demos render as looping images not video', async ({ page }) => {
  await page.goto('/guide')
  await expect(page.locator('video')).toHaveCount(0)
  const anim = page.locator('img.g-demo-anim').first()
  await expect(anim).toBeVisible()
  await expect(anim).toHaveAttribute('src', /\/guide\/.+-demo\.webp$/)
})
