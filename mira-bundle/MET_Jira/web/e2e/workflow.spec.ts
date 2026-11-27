import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('input[autocomplete="username"]').fill('admin')
  await page.locator('input[type="password"]').fill('admin-pass')
  await page.getByRole('button', { name: /登录|Sign in/ }).click()
  await expect(page.locator('.page-header').first()).toBeVisible()
})

async function createProject(api: APIRequestContext, key: string) {
  const created = await api.post('/api/projects', {
    data: { key, name: `E2E 流程 ${key}`, profile: 'electrical' },
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  const gates = await api.put(`/api/projects/${key}`, {
    data: {
      settings: {
        transition_gates: {
          研发中: { required_fields: ['update_scope'], approvals: [] },
        },
      },
    },
  })
  expect(gates.ok(), await gates.text()).toBeTruthy()
  return (await api.get(`/api/projects/${key}`)).json()
}

async function createIssue(
  api: APIRequestContext,
  project: string,
  type: string,
  title: string,
) {
  const response = await api.post('/api/issues', {
    data: { project, type, title, due_date: '2099-12-31' },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.json()
}

async function clickNext(page: Page, statusName: string) {
  await page.locator('.flow-node.next').filter({ hasText: statusName }).click()
}

function nextNode(page: Page, statusName: string) {
  return page.locator('.flow-node.next').filter({ hasText: statusName })
}

test('requirement gate blocks In Development; task skips; settings save gates', async ({
  page,
}) => {
  const api = page.request
  const key = `WF${Date.now().toString().slice(-6)}`
  const project = await createProject(api, key)
  const developing = project.statuses.find((s: { name: string }) => s.name === '研发中')
  expect(developing).toBeTruthy()

  const requirement = await createIssue(api, key, 'requirement', 'E2E 需求门禁')
  await page.goto(`/issues/${requirement.key}`)
  await expect(page.locator('.flow')).toBeVisible()
  await clickNext(page, '方案讨论')
  await expect(page.locator('.flow-node.current')).toContainText('方案讨论')
  await clickNext(page, '研发中')
  await expect(page.locator('.toast.error').filter({
    hasText: /影响范围|Impact scope/,
  })).toBeVisible({ timeout: 10_000 })
  await expect(page.locator('.gate-miss, .gate-miss-value, .gate-miss-prop').first()).toBeVisible()
  const still = await (await api.get(`/api/issues/${requirement.key}`)).json()
  expect(still.status.name).toBe('方案讨论')

  const task = await createIssue(api, key, 'task', 'E2E 任务跳过门禁')
  await page.goto(`/issues/${task.key}`)
  await expect(page.locator('.flow')).toBeVisible()
  await clickNext(page, '研发中')
  await expect(page.locator('.flow-node.current')).toContainText('研发中', { timeout: 10_000 })
  await expect(nextNode(page, '已完成')).toBeVisible()
  const moved = await (await api.get(`/api/issues/${task.key}`)).json()
  expect(moved.status.name).toBe('研发中')

  await page.goto(`/projects/${key}/settings`)
  await page.getByRole('button', { name: '状态与工作流', exact: true }).click()
  await expect(page.getByRole('heading', { name: '状态门禁与审批' })).toBeVisible()
  const row = page.locator('.gate-row').filter({ hasText: '研发中' })
  const fieldInput = row.locator('input.input.sm').first()
  await fieldInput.fill('update_scope, story_points')
  await page.getByRole('button', { name: '保存门禁与审批' }).click()
  await expect(page.locator('.toast.success').filter({ hasText: '已保存' }))
    .toBeVisible({ timeout: 10_000 })
  await page.reload()
  await page.getByRole('button', { name: '状态与工作流', exact: true }).click()
  await expect(page.locator('.gate-row').filter({ hasText: '研发中' })
    .locator('input.input.sm').first()).toHaveValue(/story_points/)
  const settings = (await (await api.get(`/api/projects/${key}`)).json()).project.settings
  expect(settings.transition_gates['研发中'].required_fields).toEqual(
    expect.arrayContaining(['update_scope', 'story_points']),
  )
})

test('requirement next step is development complete; scheme sits under requester', async ({
  page,
}) => {
  const api = page.request
  const key = `WD${Date.now().toString().slice(-6)}`
  const created = await api.post('/api/projects', {
    data: { key, name: `E2E 开发完成 ${key}`, profile: 'electrical' },
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  const project = await (await api.get(`/api/projects/${key}`)).json()
  const statusId = (name: string) =>
    project.statuses.find((item: { name: string }) => item.name === name).id
  const issue = await (await api.post('/api/issues', {
    data: { project: key, type: 'requirement', title: '下一步', due_date: '2099-12-31' },
  })).json()
  expect((await api.patch(`/api/issues/${issue.key}`, {
    data: { status_id: statusId('方案讨论') },
  })).ok()).toBeTruthy()
  expect((await api.patch(`/api/issues/${issue.key}`, {
    data: { scheme_note: '方案' },
  })).ok()).toBeTruthy()
  expect((await api.patch(`/api/issues/${issue.key}`, {
    data: { scheme_action: 'submit' },
  })).ok()).toBeTruthy()
  expect((await api.patch(`/api/issues/${issue.key}`, {
    data: { electrical_scheme_ok: true, process_scheme_ok: true },
  })).ok()).toBeTruthy()
  expect((await api.patch(`/api/issues/${issue.key}`, {
    data: { status_id: statusId('研发中') },
  })).ok()).toBeTruthy()

  await page.goto(`/issues/${issue.key}`)
  await expect(nextNode(page, '开发完成')).toBeVisible()
  const heads = await page.locator('.section-head').allTextContents()
  const requesterAt = heads.findIndex((text) => text.includes('需求方填写'))
  const schemeAt = heads.findIndex((text) => text.includes('方案确认'))
  expect(requesterAt).toBeGreaterThanOrEqual(0)
  expect(schemeAt).toBe(requesterAt + 1)
  await clickNext(page, '开发完成')
  await expect(page.locator('.flow-node.current')).toContainText('开发完成', { timeout: 10_000 })

  const task = await (await api.post('/api/issues', {
    data: { project: key, type: 'task', title: '任务下一步', due_date: '2099-12-31' },
  })).json()
  expect((await api.patch(`/api/issues/${task.key}`, {
    data: { status_id: statusId('研发中') },
  })).ok()).toBeTruthy()
  await page.goto(`/issues/${task.key}`)
  await expect(nextNode(page, '已完成')).toBeVisible()
  await expect(page.locator('.section-head', { hasText: '方案确认' })).toHaveCount(0)

  const bug = await (await api.post('/api/issues', {
    data: { project: key, type: 'bug', title: '缺陷下一步', due_date: '2099-12-31' },
  })).json()
  expect((await api.patch(`/api/issues/${bug.key}`, {
    data: { status_id: statusId('研发中') },
  })).ok()).toBeTruthy()
  await page.goto(`/issues/${bug.key}`)
  await expect(nextNode(page, '开发完成')).toBeVisible()
  await expect(page.locator('.section-head', { hasText: '方案确认' })).toHaveCount(0)

  const story = await (await api.post('/api/issues', {
    data: { project: key, type: 'story', title: '故事下一步', due_date: '2099-12-31' },
  })).json()
  expect((await api.patch(`/api/issues/${story.key}`, {
    data: { status_id: statusId('研发中') },
  })).ok()).toBeTruthy()
  await page.goto(`/issues/${story.key}`)
  await expect(nextNode(page, '开发完成')).toBeVisible()

  const me = (await (await api.get('/api/auth/me')).json()).user.id
  const waiting = await (await api.post('/api/issues', {
    data: { project: key, type: 'requirement', title: '等待软件', due_date: '2099-12-31' },
  })).json()
  for (const data of [
    { status_id: statusId('方案讨论') },
    { scheme_note: '方案' },
    { scheme_action: 'submit' },
    { electrical_scheme_ok: true, process_scheme_ok: true },
    { status_id: statusId('研发中') },
    { involves_software: true, software_owner_id: me, software_jira_key: 'CVD-1' },
  ]) {
    expect((await api.patch(`/api/issues/${waiting.key}`, { data })).ok()).toBeTruthy()
  }
  await page.goto(`/issues/${waiting.key}`)
  await clickNext(page, '开发完成')
  await expect(page.locator('.toast.info').filter({ hasText: '待上机验证' })).toBeVisible()
  await expect(page.locator('.flow-node.current')).toContainText('研发中')
  await expect(page.getByText('待上机验证').last()).toBeVisible()

  await page.goto(`/projects/${key}/board`)
  await expect(page.locator('.board').getByText('开发完成', { exact: true }).first()).toBeVisible()

  const mechanical = await api.post('/api/projects', {
    data: { key: `M${key.slice(-5)}`, name: 'E2E 机械', profile: 'mechanical' },
  })
  expect(mechanical.ok(), await mechanical.text()).toBeTruthy()
  const mechKey = `M${key.slice(-5)}`
  await page.goto(`/projects/${mechKey}/settings`)
  await page.getByRole('button', { name: '状态与工作流', exact: true }).click()
  await expect(page.locator('.react-flow__node[data-id="设计中"]')).toBeVisible()
  await expect(page.locator('.react-flow__node[data-id="开发完成"]')).toHaveCount(0)
})
