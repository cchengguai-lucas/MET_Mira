import { defineConfig } from '@playwright/test'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

const runId = process.env.METJIRA_E2E_RUN_ID ?? `${process.pid}-${Date.now()}`
const port = Number(process.env.METJIRA_E2E_PORT ?? 8810)
const baseURL = `http://127.0.0.1:${port}`
const dataDir = join(tmpdir(), `metjira-e2e-${runId}`)

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  workers: 1,
  use: { baseURL, trace: 'retain-on-failure' },
  webServer: {
    command: `cd .. && python3 -m uvicorn backend.main:app --port ${port}`,
    url: `${baseURL}/api/health`,
    timeout: 60_000,
    reuseExistingServer: false,
    env: {
      METJIRA_DATABASE_URL: process.env.METJIRA_E2E_DATABASE_URL ?? `sqlite:///${join(dataDir, 'metjira.db')}`,
      METJIRA_DATA_DIR: dataDir,
      METJIRA_AUTH_MODE: 'local',
      METJIRA_ADMIN_PASSWORD: 'admin-pass',
      METJIRA_SCHEDULER: 'false',
      METJIRA_LLM_ENABLED: 'false',
      METJIRA_DEPLOY_ENV: 'staging',
    },
  },
})
