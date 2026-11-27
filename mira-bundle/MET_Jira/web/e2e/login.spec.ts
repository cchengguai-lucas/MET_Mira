import { expect, test } from '@playwright/test'

test('Mira login theme mounts, adapts to reduced motion, and cleans up after sign-in', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/login')

  await expect(page.locator('.mira-login-hero h1')).toHaveText('MIRA')
  await expect(page.getByRole('checkbox', { name: /7 天免密登录|Stay signed in for 7 days/ })).toBeVisible()
  await expect(page.locator('#mira-login-bg canvas')).toBeVisible()
  await expect(page.getByText('MET.Ai', { exact: true })).toBeVisible()

  await page.locator('input[autocomplete="username"]').fill('admin')
  await page.locator('input[autocomplete="current-password"]').fill('admin-pass')
  await page.getByRole('checkbox', { name: /7 天免密登录|Stay signed in for 7 days/ }).check()
  await page.getByRole('button', { name: /登录|Sign in/ }).click()

  await expect(page.locator('.page-header').first()).toBeVisible()
  await expect(page.locator('#mira-login-bg')).toHaveCount(0)
})

test('Mira login theme remains usable on a narrow viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 760 })
  await page.goto('/login')

  await expect(page.locator('.mira-login-panel')).toBeVisible()
  await expect(page.locator('input[autocomplete="username"]')).toBeVisible()
  await expect(page.getByRole('checkbox', { name: /7 天免密登录|Stay signed in for 7 days/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /登录|Sign in/ })).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('Mira login glass becomes opaque when reduced transparency is requested', async ({ page }) => {
  const session = await page.context().newCDPSession(page)
  await session.send('Emulation.setEmulatedMedia', {
    features: [{ name: 'prefers-reduced-transparency', value: 'reduce' }],
  })
  await page.goto('/login')

  await expect(page.locator('.mira-login-panel')).toHaveCSS('backdrop-filter', 'none')
  await expect(page.locator('.mira-login-panel')).toHaveCSS('background-color', 'rgb(31, 35, 43)')
})
