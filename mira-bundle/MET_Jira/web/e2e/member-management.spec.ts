import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('input[autocomplete="username"]').fill('admin')
  await page.locator('input[type="password"]').fill('admin-pass')
  await page.getByRole('button', { name: /登录|Sign in/ }).click()
  await expect(page.locator('.page-header').first()).toBeVisible()
})

async function createUser(api: APIRequestContext, username: string, displayName: string) {
  const response = await api.post('/api/admin/users', {
    data: {
      username,
      display_name: displayName,
      password: 'member-pass',
      email: `${username}@example.com`,
    },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.json()
}

async function createProjectWithMembers(page: Page) {
  const suffix = Date.now().toString().slice(-6)
  const key = `MM${suffix}`
  const created = await page.request.post('/api/projects', {
    data: { key, name: `成员管理 ${suffix}`, profile: 'electrical' },
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  const first = await createUser(page.request, `member-a-${suffix}`, '批量成员甲')
  const second = await createUser(page.request, `member-b-${suffix}`, '批量成员乙')
  for (const user of [first, second]) {
    const added = await page.request.post(`/api/projects/${key}/members`, {
      data: { user_id: user.id, role: 'member' },
    })
    expect(added.ok(), await added.text()).toBeTruthy()
  }
  return { key, first, second }
}

test('edits one member and adds a role to selected members', async ({ page }) => {
  const { key, first, second } = await createProjectWithMembers(page)
  await page.goto(`/projects/${key}/settings?section=members`)
  await expect(page.getByText('批量成员甲')).toBeVisible()

  const firstRow = page.getByRole('row').filter({ hasText: '批量成员甲' })
  await firstRow.getByRole('button', { name: '编辑权限' }).click()
  const modal = page.getByRole('dialog', { name: /编辑 批量成员甲 的权限/ })
  await modal.getByRole('combobox').click()
  await page.getByRole('option', { name: '电气组' }).click()
  await page.keyboard.press('Escape')
  await modal.getByRole('button', { name: '保存权限' }).click()
  await expect(firstRow).toContainText('电气组')

  const firstBox = await page.getByRole('checkbox', { name: '选择 批量成员甲' }).boundingBox()
  const secondBox = await page.getByRole('checkbox', { name: '选择 批量成员乙' }).boundingBox()
  if (!firstBox || !secondBox) throw new Error('member checkboxes are not visible')
  await page.mouse.move(firstBox.x + firstBox.width / 2, firstBox.y + firstBox.height / 2)
  await page.mouse.down()
  await page.mouse.move(secondBox.x + secondBox.width / 2, secondBox.y + secondBox.height / 2, { steps: 8 })
  await page.mouse.up()
  await expect(page.getByRole('checkbox', { name: '选择 批量成员甲' })).toBeChecked()
  await expect(page.getByRole('checkbox', { name: '选择 批量成员乙' })).toBeChecked()
  const selectedBar = page.getByText('已选择 2 人').locator('..')
  await expect(selectedBar).toBeVisible()
  await selectedBar.getByRole('combobox').click()
  await page.getByRole('option', { name: '测试人员' }).click()
  await page.keyboard.press('Escape')
  await selectedBar.getByRole('button', { name: '应用到所选成员' }).click()
  await expect(firstRow).toContainText('测试人员')
  await expect(page.getByRole('row').filter({ hasText: '批量成员乙' })).toContainText('测试人员')

  const project = await (await page.request.get(`/api/projects/${key}`)).json()
  for (const user of [first, second]) {
    const roles = project.members
      .filter((row: { user: { id: number } }) => row.user.id === user.id)
      .map((row: { role: string }) => row.role)
    expect(roles).toContain('tester')
  }
})

function orgUnit(
  id: number, name: string, path: string, level: number, parentId: number | null,
  memberCount: number, totalCount: number,
) {
  return {
    id, dn: `OU=${name}`, name, display_name: null, label: name, parent_id: parentId,
    path, level, sort_order: 0, hidden: false, removed: false,
    member_count: memberCount, total_count: totalCount,
  }
}

function person(id: number, username: string, displayName: string, unitId: number, path: string) {
  return {
    id, username, display_name: displayName, title: null, email: null,
    avatar_url: null, avatar_color: '#0a84ff', org_unit_id: unitId, org_path: path,
  }
}

test('same-named departments show full paths and scoped member preview', async ({ page }) => {
  const { key } = await createProjectWithMembers(page)
  const rightDirect = person(90021, 'right.direct', '右侧直属', 9002, 'Root / Right')
  const rightPower = person(92001, 'right.power', '右侧组成员', 9201, 'Root / Right / Power')
  await page.route('**/api/org/tree', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        units: [
          orgUnit(9101, 'Power', 'Root / Left / Power', 2, 9001, 4, 4),
          orgUnit(9002, 'Right', 'Root / Right', 1, null, 1, 2),
          orgUnit(9201, 'Power', 'Root / Right / Power', 2, 9002, 1, 1),
        ],
      }),
    })
  })
  await page.route('**/api/users?*org_unit_id=9002*', async (route) => {
    const descendants = !route.request().url().includes('descendants=false')
    const users = descendants ? [rightDirect, rightPower] : [rightDirect]
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ total: users.length, users }),
    })
  })
  await page.route('**/api/users?*org_unit_id=9201*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ total: 1, users: [rightPower] }),
    })
  })

  await page.goto(`/projects/${key}/settings?section=members`)
  await page.getByRole('button', { name: '按组织批量添加' }).click()
  await page.getByPlaceholder('搜索部门或完整路径').fill('Right')
  await page.getByRole('combobox').first().click()
  await expect(page.getByRole('option', { name: /Root \/ Left \/ Power/ })).toHaveCount(0)
  await page.getByRole('option', { name: /Root \/ Right \/ Power/ }).click()
  await expect(page.getByText('Root / Right / Power', { exact: true })).toBeVisible()
  await expect(page.getByText('右侧组成员')).toBeVisible()
  await expect(page.getByText('@right.power')).toBeVisible()

  await page.getByRole('combobox').first().click()
  await page.getByRole('option', { name: /Root \/ Right ·/ }).click()
  await expect(page.getByText('右侧直属')).toBeVisible()
  await expect(page.getByText('右侧组成员')).toBeVisible()
  await page.getByRole('checkbox', { name: '包含下级组织' }).uncheck()
  await expect(page.getByText('右侧直属')).toBeVisible()
  await expect(page.getByText('右侧组成员')).toHaveCount(0)
})
