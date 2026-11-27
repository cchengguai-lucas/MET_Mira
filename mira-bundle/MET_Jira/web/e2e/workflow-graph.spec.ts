import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('input[autocomplete="username"]').fill('admin')
  await page.locator('input[type="password"]').fill('admin-pass')
  await page.getByRole('button', { name: /登录|Sign in/ }).click()
  await expect(page.locator('.page-header').first()).toBeVisible()
})

test.use({ viewport: { width: 1440, height: 900 } })

test('workflow diagram follows status order and separates return arcs', async ({ page }) => {
  const key = `WG${Date.now().toString().slice(-6)}`
  const created = await page.request.post('/api/projects', {
    data: { key, name: `E2E 走线 ${key}`, profile: 'electrical' },
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  const rules = {
    待评估: ['方案讨论', '已驳回'],
    方案讨论: ['研发中', '待评估'],
    重新打开: ['研发中'],
    研发中: ['开发完成', '重新打开', '方案讨论'],
    开发完成: ['测试中', '研发中'],
    测试中: ['测试通过', '测试未通过'],
    测试通过: ['已完成', '测试未通过'],
    测试未通过: ['重新打开', '研发中'],
    已完成: ['重新打开'],
  }
  const saved = await page.request.put(`/api/projects/${key}`, {
    data: { settings: { transitions: { requirement: rules } } },
  })
  expect(saved.ok(), await saved.text()).toBeTruthy()
  const project = await (await page.request.get(`/api/projects/${key}`)).json()
  const order: string[] = project.statuses
    .slice()
    .sort((a: { sort_order: number }, b: { sort_order: number }) => a.sort_order - b.sort_order)
    .map((status: { name: string }) => status.name)
    .filter((name: string) => name in rules || Object.values(rules).some((targets) => targets.includes(name)))

  await page.goto(`/projects/${key}/settings`)
  await page.getByRole('button', { name: '状态与工作流', exact: true }).click()
  const canvas = page.locator('.wf-canvas')
  await canvas.scrollIntoViewIfNeeded()
  await expect(canvas.locator('.react-flow__node[data-id="待评估"]')).toBeVisible()
  const draft = page.waitForResponse(
    (response) => response.url().includes('/api/agent/workflow-draft'),
    { timeout: 8000 },
  ).catch(() => null)
  await draft
  await page.waitForTimeout(450)
  const canvasTop = async () => (await canvas.boundingBox())?.y ?? 0
  const settledTop = await canvasTop()
  const viewportOf = () => canvas.evaluate((root) => {
    const el = root.querySelector('.react-flow__viewport') as HTMLElement | null
    return el?.style.transform ?? ''
  })
  const viewport = await viewportOf()
  await canvas.locator('.react-flow__node[data-id="待评估"]').click()
  await page.waitForTimeout(350)
  expect(await viewportOf()).toBe(viewport)
  expect(await canvasTop()).toBeCloseTo(settledTop, 0)

  const geometry = await canvas.evaluate((root) => {
    const read = (transform: string) => {
      const match = /translate(?:3d)?\(\s*([-\d.]+)px,\s*([-\d.]+)px/.exec(transform)
      return match ? { x: Number(match[1]), y: Number(match[2]) } : null
    }
    const nodes = [...root.querySelectorAll('.react-flow__node')].map((el) => ({
      id: el.getAttribute('data-id'),
      ...read((el as HTMLElement).style.transform) ?? { x: null, y: null },
    }))
    const edges = [...root.querySelectorAll('.react-flow__edge-path')].map((el) => {
      const box = (el as SVGGraphicsElement).getBBox()
      return { x: box.x, y: box.y, w: box.width, h: box.height, d: el.getAttribute('d') ?? '' }
    })
    const bounds = root.querySelector('.react-flow__node[data-id="__bounds__"]') as HTMLElement | null
    return { nodes, edges, boundsHeight: bounds?.offsetHeight ?? 0 }
  })

  await canvas.screenshot({ path: '/tmp/wf-graph.png' })

  const byId = new Map(geometry.nodes.map((node) => [node.id, node]))
  const main = ['待评估', '方案讨论', '研发中', '开发完成', '测试中', '测试通过', '已完成']
  const branches = ['重新打开', '测试未通过', '已驳回']
  const mainY = byId.get(main[0])?.y ?? 0
  for (let i = 0; i < main.length; i += 1) {
    const node = byId.get(main[i])
    expect(node?.y).toBeCloseTo(mainY, 0)
    if (i > 0) expect(node?.x).toBeGreaterThan(byId.get(main[i - 1])?.x ?? 0)
  }
  const branchY = byId.get(branches[0])?.y ?? 0
  expect(branchY).toBeGreaterThan(mainY + 40)
  for (let i = 0; i < branches.length; i += 1) {
    const node = byId.get(branches[i])
    expect(node?.y).toBeCloseTo(branchY, 0)
    if (i > 0) expect(node?.x).toBeGreaterThan(byId.get(branches[i - 1])?.x ?? 0)
  }
  expect(order).toContain('待评估')
  const start = geometry.nodes.find((node) => node.id === '__start__')
  expect(start?.x).toBeLessThan(byId.get('待评估')?.x ?? 0)

  const bottom = Math.max(...branches.map((name) => byId.get(name)?.y ?? 0))
  expect(geometry.boundsHeight).toBeGreaterThan(80)
  expect(geometry.edges.some((edge) => edge.y + edge.h > bottom + 40)).toBe(true)
  expect(geometry.edges.some((edge) => edge.d.includes('A '))).toBe(true)
  for (let i = 0; i < geometry.edges.length; i += 1) {
    for (let j = i + 1; j < geometry.edges.length; j += 1) {
      const a = geometry.edges[i]
      const b = geometry.edges[j]
      const same = Math.abs(a.x - b.x) < 2 && Math.abs(a.y - b.y) < 2
        && Math.abs(a.w - b.w) < 2 && Math.abs(a.h - b.h) < 2
      expect(same).toBe(false)
    }
  }

  // Dragging a status slightly keeps planned lanes + hops, and Auto arrange restores the row.
  const node = canvas.locator('.react-flow__node[data-id="方案讨论"]')
  const box = await node.boundingBox()
  expect(box).toBeTruthy()
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2)
  await page.mouse.down()
  await page.mouse.move(box!.x + box!.width / 2 + 28, box!.y + box!.height / 2 + 16, { steps: 4 })
  await page.mouse.up()

  const afterDrag = await canvas.evaluate((root) => {
    const paths = [...root.querySelectorAll('.react-flow__edge-path')].map((el) => el.getAttribute('d') ?? '')
    return { arcs: paths.filter((d) => d.includes('A ')).length }
  })
  expect(afterDrag.arcs).toBeGreaterThan(0)

  await page.getByRole('button', { name: /自动排列|Auto arrange/ }).click()
  const resetY = await canvas.evaluate((root) => {
    const el = root.querySelector('.react-flow__node[data-id="方案讨论"]') as HTMLElement | null
    const match = /translate(?:3d)?\(\s*([-\d.]+)px,\s*([-\d.]+)px/.exec(el?.style.transform ?? '')
    return match ? Number(match[2]) : null
  })
  expect(resetY).toBeCloseTo(mainY, 0)
})
