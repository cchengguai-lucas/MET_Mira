# Mira 参赛全流程 & 现场 Todo List

---

## 阶段一：赛前准备（出发前在自己电脑上完成）

- [ ] **下载并解压打包文件**：将 `mira-competition-20261125.zip` 下载到本地电脑并解压。
- [ ] **完成本地基线部署**：按 `04-本地部署步骤.md` 启动 PostgreSQL 16 + Mira 后端 + 前端，确认 `http://127.0.0.1:8800/api/health` 返回 200 且 `admin / MetJira123!` 能正常登录。
- [ ] **建立清晰的 Git 基线**：在本地 `MET_Jira` 目录执行 `git init && git add . && git commit -m "chore: pre-competition baseline"`，确保后续赛事期间所有 Qoder 改动都留在独立分支（如 `feat/competition-delivery-loop`），方便评委核验提交差异。
- [ ] **准备 LLM 可用方案（主用 + 备用）**：
  - [ ] 主方案：准备好可用的 OpenAI 兼容 API Key（DeepSeek / 通义千问 / 赛事方模型）或验证好接口连通性。
  - [ ] 离线兜底方案：本地安装 Ollama 并预先拉取一个 7B/14B 支持工具调用的模型（如 `qwen2.5:14b` 或 `qwen2.5:7b`），防止比赛现场断网/限流翻车。
- [ ] **验证基础测试全绿**：在本地电脑提前跑通 `pytest -q`、`ruff check backend tests`、`cd web && npm run typecheck && npm run build`，避免现场才发现 Python/Node/Playwright 缺依赖。
- [ ] **提前装好 Playwright 浏览器**：在 `web/` 下执行 `npx playwright install chromium`，避免现场大文件下载卡住。
- [ ] **合规自查**：确认包内无任何公司真实数据、真实员工名录、内网 IP 与敏感密钥。

---

## 阶段二：现场开发时段（使用 Qoder 高效推进）

> 原则：**先跑通最小端到端闭环，再逐步打磨亮点；每完成一个里程碑立即 `git commit` + 跑测试。**

### Milestone 1：打通环境与 LLM 适配（前 15% 时间）
- [ ] 拉出赛事开发分支：`git checkout -b feat/competition-delivery-loop`。
- [ ] 将 `02-技术清单.md` 提供给 Qoder，让 Qoder 先快速建立仓库上下文。
- [ ] 若使用公网 API Key，让 Qoder 完成 `METJIRA_LLM_API_KEY` 支持并验证右侧 AI 助手与 AI 起草正常返回。

### Milestone 2：落地任务 2 — 脱敏沙箱与 Mock 软件 Jira Relay（20%—40% 时间）
- [ ] 让 Qoder 实现 `scripts/demo/mock_software_jira.py`（实现 `/api/jira/relay?key=` + 控制台/接口一键切换软件状态为 `待上机验证` 或修改截止日期）。
- [ ] 让 Qoder 实现 `scripts/demo/seed_competition_sandbox.py`（支持 `--reset` 一键重置，自动创建模拟账号、项目、阻塞链、已发布基线、待会签需求与待联调需求）。
- [ ] 配置 `.env` 中的 `METJIRA_SOFTWARE_JIRA_URL=http://127.0.0.1:18787` 与共享 Token，联调确认 Mira 能实时拉取 Mock Jira 快照并触发自动转测。
- [ ] `git commit` 保存 Milestone 2。

### Milestone 3：落地任务 1 — 多阶段 Agent 协同交接工作流（40%—70% 时间）
- [ ] 让 Qoder 设计并实现多阶段交付交接编排服务与 API（串联：需求起草查重 → 三方方案门禁/变更检测 → 软件就绪自动交接 → 排期级联改期建议）。
- [ ] 让 Qoder 实现前端交接追踪视图 / 确认卡片增强（清晰展示每个阶段的责任角色、门禁状态、下一步动作与审计记录）。
- [ ] 让 Qoder 编写后端专项 `pytest` 用例，覆盖：
  - [ ] 正常交接闭环
  - [ ] 未完成三方会签被门禁拦截 + 方案实质修改后签核重置
  - [ ] 越权用户尝试越级操作被拒绝
  - [ ] 排期改期建议在并发修改下的 `stale` token 保护
- [ ] 运行 `ruff check` 与 `pytest -q` 全绿，`git commit` 保存 Milestone 3。

### Milestone 4：落地任务 3 — 效能观测指标与 E2E 验收（70%—90% 时间）
- [ ] 让 Qoder 补齐交付闭环指标统计（交接耗时、AI 确认采纳率、门禁拦截次数、风险处置率）及前端展示。
- [ ] 让 Qoder 编写并跑通全链路 Playwright E2E 脚本（`web/e2e/competition-delivery-loop.spec.ts`）。
- [ ] 运行全量质量检查：`ruff check` + `pytest -q` + `npm run typecheck` + `npm test` + `npm run build` + Playwright E2E。
- [ ] `git commit` 保存最终验收版本，并整理一份简短的 `git log --stat` 变更清单供评委查验。

---

## 阶段三：路演前 30 分钟彩排与防翻车检查（最后 10% 时间）

- [ ] **一键重置彩排**：完整执行一次沙箱重置脚本，从零走一遍 4 步演示流程，掐表控制在 3—5 分钟内。
- [ ] **录制兜底演示视频**：用录屏软件录一段 3 分钟无剪辑全链路实操视频（万一现场网络/大屏/模型抽风，秒切视频讲解不冷场）。
- [ ] **准备多角色浏览器窗口**：提前用普通窗口 + 无痕窗口（或不同浏览器 Profile）分别登录“PM/管理员”、“电气/研发”、“测试”账号，现场演示三方会签与交接无需频繁退出重登。
- [ ] **准备好验证命令终端**：提前开好一个终端窗口，备好测试命令和 `git diff --stat main..HEAD`，评委追问工程真实性时 5 秒出结果。

---

## 阶段四：现场演示标准脚本（4 步讲透 DELIVER 闭环）

1. **开场（30s）**：一句话点明痛点（软硬件研发 Excel+群+孤立 Jira 割裂）与定位（Mira AI 全流程交付与排期风控平台），亮出 Git 基线与赛事期间 Qoder 交付清单。
2. **演示闭环 1 — 智能建单与分诊（45s）**：在对话面板输入一段非结构化机电需求 → Agent 自动查重、拆出 Epic+子任务 → 弹出待确认卡片 → 点击确认后入库并审计留痕。
3. **演示闭环 2 — 方案门禁与实质变更重签（45s）**：尝试把需求直接拖入“研发中”被硬门禁拦截 → 完成三方会签放行 → 修改核心方案参数 → LLM 识别实质变更并自动重置签核。
4. **演示闭环 3 — 软硬件跨系统自动交接（45s）**：在 Mock 软件 Jira 一键推进软件至“待上机验证” → Mira 刷新快照后自动解锁测试门禁、流转至测试并生成 Outbox 通知。
5. **演示闭环 4 — 不可变基线与级联风控 + 观测收尾（60s）**：将上游任务推迟 5 天 → 时间线与风控引擎立即算出下游级联延期与目标日影响 → 生成待 PM 确认的改期建议并安全落地 → 切到观测面板展示 AI 采纳率、门禁拦截统计与 E2E 全绿测试报告。
