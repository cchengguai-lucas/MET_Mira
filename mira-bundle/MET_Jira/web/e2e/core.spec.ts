import { expect, test, type Locator, type Page } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('input[autocomplete="username"]').fill('admin')
  await page.locator('input[type="password"]').fill('admin-pass')
  await page.getByRole('button', { name: /登录|Sign in/ }).click()
  await expect(page.locator('.page-header').first()).toBeVisible()
})

/** Custom Select (button + popover), not a native <select>. */
async function pickSelect(page: Page, wrap: Locator, option: string | RegExp) {
  await wrap.locator('.select-trigger').click()
  await page.locator('.select-pop .menu-item').filter({ hasText: option }).first().click()
}

test('health, profile and mobile create path', async ({ page }) => {
  const health = await page.request.get('/api/health')
  expect(health.status()).toBe(200)
  expect((await health.json()).checks.database).toBe('ok')
  const project = await page.request.get('/api/projects/ELEC')
  expect((await project.json()).profile.key).toBe('electrical')

  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: /创建/ }).first().click()
  await expect(page.locator('.modal')).toBeVisible()
  await expect(page.locator('.modal')).toHaveCSS('min-height', '844px')
  await expect(page.locator('.modal .field').filter({ hasText: '截止日期' }).getByText('必填')).toBeVisible()
})

test('template publish, production master field and migration preview', async ({ page }) => {
  const catalog = await (await page.request.get('/api/projects/profile-catalog')).json()
  const electrical = catalog.profiles.find((row: { key: string }) => row.key === 'electrical')
  const production = catalog.profiles.find((row: { key: string }) => row.key === 'production')
  const key = `e2e${Date.now().toString().slice(-6)}`
  const createdResponse = await page.request.post('/api/admin/templates', {
    data: { key, label: 'E2E 模板', source_version_id: electrical.version_id },
  })
  expect(createdResponse.ok()).toBeTruthy()
  const created = await createdResponse.json()
  const draft = created.versions.find((row: { status: string }) => row.status === 'draft')
  const definition = draft.definition
  definition.labels.en = 'E2E Template'
  expect((await page.request.put(
    `/api/admin/templates/${created.id}/versions/${draft.id}`,
    { data: { revision: draft.revision, definition } },
  )).ok()).toBeTruthy()
  expect((await page.request.post(
    `/api/admin/templates/${created.id}/versions/${draft.id}/publish`,
  )).ok()).toBeTruthy()

  await page.goto('/admin')
  await page.getByRole('tab', { name: '部门模板' }).click()
  const card = page.locator('.card').filter({ hasText: key })
  await expect(card.getByText('E2E 模板')).toBeVisible()

  await card.getByRole('button', { name: '编辑', exact: true }).click()
  const editor = page.locator('.modal.full')
  await expect(editor.getByText('v2 草稿', { exact: true })).toBeVisible()
  await editor.locator('.tpl-nav-item').filter({ hasText: '角色' }).click()
  const requester = editor.locator('.tpl-table tbody tr').filter({ has: page.locator('code', { hasText: 'requester' }) })
  await requester.getByRole('textbox', { name: '名称', exact: true }).fill('需求方')
  await expect(editor.locator('.save-status')).toContainText('已保存', { timeout: 5000 })
  await editor.locator('.tpl-nav-item').filter({ hasText: '表单' }).click()
  await expect(editor.locator('.tpl-section').first()).toBeVisible()
  // Role rename persisted (roles tab); form tab should still render sections.
  await editor.locator('.tpl-nav-item').filter({ hasText: '角色' }).click()
  await expect(requester.getByRole('textbox', { name: '名称', exact: true })).toHaveValue('需求方')
  await editor.locator('.tpl-nav-item').filter({ hasText: '表单' }).click()
  await expect(page.getByText('模板草稿已被其他管理员更新')).toHaveCount(0)
  await editor.locator('.tpl-nav-item').filter({ hasText: '检查与发布' }).click()
  await expect(editor.getByText('检查通过，可以发布 v2')).toBeVisible()
  await editor.getByRole('button', { name: '关闭' }).click()
  await expect(card.getByText('有未发布的 v2 草稿')).toBeVisible()

  const suffix = Date.now().toString().slice(-6)
  const projectKey = `P${suffix}`
  const asset = await (await page.request.post('/api/assets', {
    data: { kind: 'line', code: `L-${suffix}`, name: `E2E 产线 ${suffix}`, attributes: {} },
  })).json()
  expect((await page.request.post('/api/projects', {
    data: { key: projectKey, name: `E2E 生产 ${suffix}`, profile: 'production' },
  })).ok()).toBeTruthy()
  await page.goto('/projects')
  await page.getByRole('button', { name: /创建/ }).first().click()
  const modal = page.locator('.modal')
  await pickSelect(page, modal.locator('.select-wrap').first(), new RegExp(projectKey))
  await expect(modal.getByText('工单号')).toBeVisible()
  await expect(modal.getByText(/电气组字段/)).toHaveCount(0)
  await expect(modal.locator('.field').filter({ hasText: '影响范围' })).toHaveCount(0)
  const lineField = modal.locator('.field').filter({ hasText: '产线' })
  await pickSelect(page, lineField.locator('.select-wrap'), asset.name)
  await modal.locator('.field').filter({ hasText: '标题' }).locator('input').fill('生产模板问题')
  await modal.locator('.field').filter({ hasText: '工单号' }).locator('input').fill('WO-E2E')
  await modal.locator('.field').filter({ hasText: '截止日期' }).locator('input[type="date"]').fill('2099-01-01')
  await modal.getByRole('button', { name: '创建', exact: true }).click()
  await expect(page.getByText('WO-E2E')).toBeVisible()

  await page.goto('/projects/ELEC/settings')
  await page.getByRole('button', { name: '更换模板…' }).click()
  const wizard = page.locator('.modal')
  await pickSelect(page, wizard.locator('.select-wrap').first(), /生产|production/i)
  await wizard.getByRole('button', { name: '预览迁移' }).click()
  await expect(wizard.getByText('状态映射')).toBeVisible()
})

test('create form stages screenshots in description and verify fields as attachments', async ({ page }) => {
  const png = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=',
    'base64',
  )
  await page.goto('/projects')
  await page.getByRole('button', { name: /创建/ }).first().click()
  const modal = page.locator('.modal')
  await pickSelect(page, modal.locator('.select-wrap').first(), /ELEC|电气/)
  const title = `贴图需求 ${Date.now().toString().slice(-6)}`
  await modal.locator('.field').filter({ hasText: '标题' }).locator('input').fill(title)
  await modal.locator('.field').filter({ hasText: '截止日期' }).locator('input[type="date"]').fill('2099-01-01')
  await modal.locator('.field').filter({ hasText: '影响范围' }).getByRole('button').first().click()
  const description = modal.locator('.field').filter({ hasText: '需求说明' }).first()
  await description.locator('input[type="file"]').setInputFiles({ name: 'desc.png', mimeType: 'image/png', buffer: png })
  await expect(description.locator('.staged-shot')).toHaveCount(1)
  await modal.getByRole('button', { name: /电气组字段/ }).click()
  const verify = modal.locator('.field').filter({ hasText: '验证方法' }).last()
  await verify.locator('input[type="file"]').setInputFiles({ name: 'verify.png', mimeType: 'image/png', buffer: png })
  await expect(verify.locator('.staged-shot')).toHaveCount(1)
  await expect(verify.locator('textarea')).not.toHaveValue(/staged:/)
  await modal.getByRole('button', { name: '创建', exact: true }).click()
  await expect(page.locator('img[src*="/api/attachments/"]').first()).toBeVisible()

  const created = await (await page.request.get(
    `/api/search?jql=${encodeURIComponent(`project = ELEC AND text ~ "${title}"`)}`,
  )).json()
  const issue = await (await page.request.get(`/api/issues/${created.issues[0].key}`)).json()
  expect(issue.attachments).toHaveLength(2)
  expect(issue.description).toContain(`${issue.attachments[0].url}?inline=true`)
  expect(issue.verify_method).toContain(`${issue.attachments[1].url}?inline=true`)
})

test('master data tab: bulk add, search and disable', async ({ page }) => {
  const suffix = Date.now().toString().slice(-5)
  await page.goto('/admin')
  await page.getByRole('tab', { name: '主数据', exact: true }).click()
  await expect(page.getByText('维护清单')).toBeVisible()
  await expect(page.locator('.md-usage .tag').filter({ hasText: '材料' })).toBeVisible()
  await page.getByRole('button', { name: '批量导入' }).first().click()
  const modal = page.locator('.modal')
  await modal.locator('textarea').fill(`M${suffix}A\t铝合金 ${suffix}\nM${suffix}B, 尼龙 ${suffix}\n缺名称`)
  await expect(modal.getByText('识别到 2 条')).toBeVisible()
  await modal.getByRole('button', { name: '添加 2 条' }).click()
  await expect(modal).toBeHidden()
  await page.getByPlaceholder('搜索编码或名称').fill(`M${suffix}`)
  const rows = page.locator('.md-table tbody tr')
  await expect(rows).toHaveCount(2)
  await page.screenshot({ path: 'test-results/master-data.png', fullPage: true })
  page.once('dialog', (dialog) => dialog.accept())
  await rows.filter({ hasText: `M${suffix}A` }).getByRole('button', { name: '停用' }).click()
  await expect(rows).toHaveCount(1)
})

test('project settings navigation is usable at 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/projects/ELEC/settings')
  const nav = page.locator('.settings-nav')
  await expect(nav).toBeVisible()
  await expect(nav).toHaveCSS('overflow-x', 'auto')
  await nav.getByRole('button', { name: '状态与工作流', exact: true }).click()
  await page.getByRole('button', { name: '矩阵', exact: true }).click()
  await expect(page.locator('.transition-matrix')).toBeVisible()
})

test('A opens the AI assistant and shortcuts can be rebound on the virtual keyboard', async ({ page }) => {
  await page.request.put('/api/auth/me/preferences', { data: { shortcuts: {} } })
  await page.goto('/')
  await expect(page.locator('.page-header').first()).toBeVisible()
  await page.locator('body').click({ position: { x: 5, y: 300 } })
  await page.keyboard.press('a')
  await expect(page.locator('.ai-panel')).toBeVisible()
  await page.reload()
  await expect(page.locator('.page-header').first()).toBeVisible()

  await page.keyboard.press('Shift+Slash')
  const editor = page.locator('.modal.full')
  await expect(editor.locator('.kb-board')).toBeVisible()
  await expect(editor.locator('.kb-key.bound', { hasText: 'AI' })).toHaveCount(1)

  await editor.locator('.kb-action', { hasText: '打开 / 收起 AI 助手' }).click()
  await expect(editor.locator('.kb-hint.picking')).toBeVisible()
  await page.keyboard.press('j')
  await expect(editor.locator('.kb-notice.ok')).toContainText('J')

  await editor.locator('.kb-action', { hasText: '我的工作' }).click()
  await editor.locator('[data-code="KeyC"]').click()
  await expect(editor.locator('.kb-hint.warn')).toContainText('创建问题')
  await editor.getByRole('button', { name: '保留原来的' }).click()
  await editor.locator('[data-code="Escape"]').click()
  await expect(editor.locator('.kb-notice.error')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(editor.locator('.kb-hint.picking')).toHaveCount(0)
  await page.screenshot({ path: 'test-results/shortcut-editor.png' })

  await editor.getByRole('button', { name: '保存' }).click()
  await expect(editor).toBeHidden()
  await page.keyboard.press('a')
  await expect(page.locator('.ai-panel')).toHaveCount(0)
  await page.keyboard.press('j')
  await expect(page.locator('.ai-panel')).toBeVisible()
  await page.request.put('/api/auth/me/preferences', { data: { shortcuts: {} } })
})

test('cancel discards staged avatar changes and Esc reverts an inline title', async ({ page }) => {
  await page.request.put('/api/auth/me/avatar-color', { data: { color: null } })
  const me = async () => (await (await page.request.get('/api/auth/me')).json()).user
  await page.goto('/')
  const openAvatar = async () => {
    await page.locator('.user-trigger').click()
    await page.getByTitle('更换头像').click()
    await expect(page.locator('.modal')).toBeVisible()
  }
  await openAvatar()
  const modal = page.locator('.modal')
  await modal.locator('.swatch:not(.auto)').first().click()
  await expect(modal.getByText('保存后生效')).toBeVisible()
  await modal.getByRole('button', { name: '取消', exact: true }).click()
  await expect(modal).toBeHidden()
  expect((await me()).avatar_color).toBeNull()

  await openAvatar()
  await expect(modal.getByRole('button', { name: '保存', exact: true })).toBeDisabled()
  const swatch = modal.locator('.swatch:not(.auto)').nth(1)
  const color = await swatch.getAttribute('title')
  await swatch.click()
  await modal.getByRole('button', { name: '保存', exact: true }).click()
  await expect(modal).toBeHidden()
  expect((await me()).avatar_color).toBe(color)
  await page.request.put('/api/auth/me/avatar-color', { data: { color: null } })

  const issue = await (await page.request.post('/api/issues', {
    data: { project: 'ELEC', title: '标题保持不变', due_date: '2099-01-01' },
  })).json()
  await page.goto(`/issues/${issue.key}`)
  const title = page.locator('.issue-title-input')
  await title.fill('不应保存的标题')
  await title.press('Escape')
  await expect(title).toHaveValue('标题保持不变')
  await page.waitForTimeout(300)
  expect((await (await page.request.get(`/api/issues/${issue.key}`)).json()).title).toBe('标题保持不变')
})

test('AI panel closes on outside click, its shortcut, other shortcuts and Esc', async ({ page }) => {
  await page.request.put('/api/auth/me/preferences', { data: { shortcuts: {} } })
  await page.goto('/')
  const panel = page.locator('.ai-panel')
  const body = page.locator('.page-header').first()
  await body.click()

  await page.keyboard.press('a')
  await expect(panel).toBeVisible()
  await page.keyboard.press('a')
  await expect(panel).toHaveClass(/closed/)
  await expect(panel).toBeHidden()

  await page.locator('.topbar [data-ai-toggle]').click()
  await expect(panel).toBeVisible()
  await panel.locator('.ai-messages').click()
  await expect(panel).toBeVisible()
  await body.click()
  await expect(panel).toBeHidden()

  await page.keyboard.press('a')
  await expect(panel).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(panel).toBeHidden()

  await page.keyboard.press('a')
  await expect(panel).toBeVisible()
  await page.keyboard.press('c')
  await expect(page.locator('.modal')).toBeVisible()
  await expect(panel).toBeHidden()
})

test('dialogs and drawers animate out and never block the page while leaving', async ({ page }) => {
  await page.goto('/')
  await page.locator('.topbar .btn.primary').click()
  await expect(page.locator('.overlay:not(.leaving) .modal')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.locator('.overlay.leaving')).toHaveCount(1)
  await expect(page.locator('.overlay.leaving')).toHaveCSS('pointer-events', 'none')
  await page.locator('.topbar .btn.primary').click({ timeout: 1000 })
  await expect(page.locator('.overlay:not(.leaving) .modal')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.locator('.overlay')).toHaveCount(0, { timeout: 1000 })

  const issue = await (await page.request.post('/api/issues', {
    data: { project: 'ELEC', title: '抽屉动画', due_date: '2099-01-01' },
  })).json()
  await page.addInitScript(() => localStorage.setItem('metjira.issue_chrome', 'drawer-right'))
  await page.goto(`/projects/ELEC/board?issue=${issue.key}`)
  await expect(page.locator('.drawer:not(.leaving)')).toBeVisible()
  await page.locator('.drawer-overlay').click({ position: { x: 5, y: 300 } })
  await expect(page.locator('.drawer.leaving')).toHaveCount(1)
  await expect(page.locator('.drawer')).toHaveCount(0, { timeout: 1000 })
})

test('description shows once: task uses the description card, requirement uses its section', async ({ page }) => {
  const create = async (type: string) => (await (await page.request.post('/api/issues', {
    data: { project: 'ELEC', type, title: `描述去重 ${type}`, due_date: '2099-01-01' },
  })).json()).key as string
  const task = await create('task')
  await page.goto(`/issues/${task}`)
  await expect(page.locator('h3', { hasText: /^描述$/ })).toHaveCount(1)
  await expect(page.locator('.section-card')).toHaveCount(0)

  const requirement = await create('requirement')
  await page.goto(`/issues/${requirement}`)
  await expect(page.locator('.section-card').first()).toBeVisible()
  await expect(page.locator('h3', { hasText: /^描述$/ })).toHaveCount(0)
  await expect(page.locator('.section-card .label', { hasText: '需求说明' })).toHaveCount(1)
})

test('comment @ opens a narrowing people picker and quick-mention chips insert mentions', async ({ page }) => {
  const username = `mention${Date.now().toString().slice(-6)}`
  const user = await (await page.request.post('/api/admin/users', {
    data: { username, display_name: '提及测试', password: 'secret-123' },
  })).json()
  expect((await page.request.post('/api/projects/ELEC/members', {
    data: { user_id: user.id, role: 'electrical' },
  })).ok()).toBeTruthy()
  const issue = await (await page.request.post('/api/issues', {
    data: { project: 'ELEC', type: 'task', title: '提及人员', assignee_id: user.id, due_date: '2099-01-01' },
  })).json()
  await page.goto(`/issues/${issue.key}`)

  const box = page.locator('.mention-field textarea').first()
  await box.click()
  await box.pressSequentially('请 @')
  const menu = page.locator('.mention-menu')
  await expect(menu.locator('.menu-item').first()).toContainText('提及测试')
  await expect(menu.locator('.menu-item').first()).toContainText('负责人')
  await box.pressSequentially(username)
  await expect(menu.locator('.menu-item')).toHaveCount(1)
  await box.press('Enter')
  await expect(box).toHaveValue(`请 @${username} `)
  await expect(menu).toHaveCount(0)
  await expect(page.locator('.mention-chip', { hasText: '提及测试' })).toHaveCount(0)

  await box.fill('')
  await page.locator('.mention-chip', { hasText: '提及测试' }).click()
  await expect(box).toHaveValue(`@${username} `)
})

test('project tabs follow Jira naming and open summary and timeline', async ({ page }) => {
  const epic = await (await page.request.post('/api/issues', {
    data: { project: 'ELEC', type: 'epic', title: '时间线史诗', due_date: '2099-01-01' },
  })).json()
  await page.addInitScript(() => localStorage.setItem('metjira.issue_chrome', 'drawer-right'))
  await page.goto('/projects/ELEC/board')
  const tabs = page.locator('.tabs .tab')
  await expect(tabs).toHaveText(['摘要', '看板', '待办事项', '列表视图', '时间线', '报表', '设置'])

  await tabs.filter({ hasText: '摘要' }).click()
  await expect(page).toHaveURL(/\/projects\/ELEC\/summary$/)
  await expect(page.getByRole('heading', { name: '状态概览' })).toBeVisible()
  await expect(page.locator('.summary-epic', { hasText: epic.key })).toBeVisible()

  await tabs.filter({ hasText: '时间线' }).click()
  await expect(page).toHaveURL(/\/projects\/ELEC\/timeline$/)
  const row = page.locator('.tl-epic', { hasText: epic.key })
  await expect(row.locator('.tl-bar')).toBeVisible()
  await page.locator('.tl-toolbar').getByRole('button', { name: '季度' }).click()
  await row.locator('.tl-title').click()
  await expect(page.locator('.drawer:not(.leaving)')).toBeVisible()
})
