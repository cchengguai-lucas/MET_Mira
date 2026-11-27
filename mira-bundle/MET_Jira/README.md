# MET Jira

MET 内网 Issue 跟踪与协作平台：AD 登录、组织架构同步、需求三段式流程（需求提出人 / 电气组 / 测试人员）、看板、Backlog 与 Sprint、Excel 风格列表、JQL 搜索、报表，以及接入 65 服务器 LLM 的智能助手。

- 后端：FastAPI + SQLAlchemy 2 + PostgreSQL 16（`pg_trgm`），APScheduler 定时任务
- 前端：React 19 + Vite + TanStack Query + dnd-kit，构建产物由后端直接托管
- 访问地址：<http://10.113.17.63:8800>

## 环境与发布

| 环境 | 文件夹 | 分支 | 地址 | 服务 | 数据 |
| --- | --- | --- | --- | --- | --- |
| 生产 | `MET_Jira` | `production` | <http://10.113.17.63:8800> | `met-jira.service` | 库 `met_jira`，`MET_Jira/data` |
| 测试 | `MET_Jira-staging` | `staging` | <http://10.113.17.63:8801> | `met-jira-staging.service` | 库 `met_jira_staging`，`~/.met_jira_staging` |
| 运维 | `MET_Jira-ops` | `main` | <http://10.113.17.63:8803> | `met-jira-ops.service` | `~/.met_jira_ops` |

用 `/data/home/lucas/projects/MET_Jira.code-workspace` 同时打开三个文件夹。

- 日常改动只做在 `MET_Jira-staging`：提交到 `staging` 分支后运行 `scripts/ops/deploy-staging.sh` 构建前端并按顺序滚动 staging 服务。Uvicorn 使用两个 worker，不启用自动 reload。
- 测试确认后才发布：运行 `scripts/ops/release.sh`，脚本会在 staging 上跑 pytest / ruff / typecheck，以 `--no-ff` 把 `staging` 合并进 `production`，重新构建前端并重启生产服务。
- 不要直接修改生产文件夹，也不要在两个文件夹之间手工复制文件。
- 测试环境使用独立的数据库、数据目录、会话 Cookie 和密钥，且不运行定时任务（AD 同步、AI 周报），登录测试环境不会影响生产会话。

## 目录

```
backend/            FastAPI 应用（routers / services / agent / alembic 迁移）
web/                React 前端（npm run build -> web/dist）
configs/            ldap.env（AD 配置，不入库）
scripts/ops/        pg-local.sh（用户级 PostgreSQL）、install-systemd.sh、systemd 单元模板
tests/backend/      pytest
data/               附件与导入文件（METJIRA_DATA_DIR）
```

## 部署（用户级 systemd，无需 sudo）

```bash
python3 -m pip install --user -e '.[dev]'
(cd web && npm ci)

scripts/ops/pg-local.sh init          # 初始化 ~/.met_jira/pgdata，监听 127.0.0.1:5433
scripts/ops/install-systemd.sh        # 生产：生成 .env、构建前端、安装并启动服务
loginctl enable-linger "$USER"        # 未登录时保持服务运行（如尚未开启）

# 测试环境（在 staging worktree 中执行；--clone-prod 首次建库时复制生产数据）
git worktree add ../MET_Jira-staging staging
cd ../MET_Jira-staging && (cd web && npm ci) && scripts/ops/install-systemd.sh staging --clone-prod
```

`install-systemd.sh` 会安装以下用户服务：

| 服务 | 说明 |
| --- | --- |
| `met-jira-postgres.service` | 运行 `pg-local.sh run` |
| `met-jira.service` | 生产，`uvicorn backend.main:app --port 8800`，依赖数据库服务 |
| `met-jira-staging-nginx.service` | 测试入口 `0.0.0.0:8801`，静态缓存、gzip、SSE 反代 |
| `met-jira-staging.service` | 两个 Uvicorn worker，仅监听 `127.0.0.1:18801`，无 reload |
| `met-jira-staging-pgbouncer.service` | `127.0.0.1:6433` transaction pooling 到 PostgreSQL `127.0.0.1:5433` |
| `met-jira-scheduler.service` / `met-jira-staging-scheduler.service` | 前台定时任务；测试环境默认禁用 |
| `met-jira-outbox.service` / `met-jira-staging-outbox.service` | 事务 outbox 投递 worker；测试环境可独立运行 |

Web 进程不运行定时任务或外部投递；scheduler 以 PostgreSQL session advisory lock
选主，并为每个 job 获取独立锁；outbox worker 使用租约、指数退避和 dead-letter
保证至少一次投递。启动时自动执行 Alembic 迁移并创建本地管理员。常用命令：

```bash
systemctl --user status met-jira.service
systemctl --user restart met-jira.service      # 更新代码或 web/dist 后
journalctl --user -u met-jira.service -f
```

### Staging m2 边缘拓扑

staging 固定为 `Nginx :8801 -> Uvicorn 127.0.0.1:18801 -> PgBouncer
127.0.0.1:6433 -> PostgreSQL 127.0.0.1:5433`。全部是用户级 systemd 服务，
无需 sudo。主机没有 PgBouncer 时，安装脚本从已配置的 apt 仓库下载已签名 deb，
解包到 `~/.local/lib/met-jira-pgbouncer`，不改系统包。

```bash
scripts/ops/install-edge-staging.sh  # 安装依赖、生成配置和 unit，不切流量
scripts/ops/preflight-staging.sh     # DB、权限、模板和 nginx -t
scripts/ops/deploy-staging.sh        # bootstrap -> PgBouncer -> API -> outbox -> Nginx
scripts/ops/status-staging.sh        # 服务、端口、health、metrics 404
scripts/ops/rollback-staging.sh      # 恢复 Uvicorn 直连 :8801 / PostgreSQL :5433
```

`.env` 始终保留直连 PostgreSQL 的 URL，供 bootstrap 和回滚使用。生成器从该 URL
读取账号，优先从 PostgreSQL 取得 SCRAM verifier，并只在
`$XDG_RUNTIME_DIR/met-jira-staging-edge` 写入权限 0600 的 `userlist.txt` 与
`app.env`；仓库模板不含密码。SQLAlchemy/psycopg 在池化路径禁用 server-side
prepared statements。PgBouncer 使用 transaction pooling，默认池 8、reserve 2、
最多 100 个客户端，等待超时 5 秒。

Nginx 不代理 `/metrics`（固定 404）；主机监控应直接抓
`127.0.0.1:18801/metrics`。`/api/agent/` 禁用 buffering，读写超时 180 秒，
上传上限 50 MB。只信任 loopback 代理，且 Nginx 覆盖客户端伪造的 forwarded
headers。发布脚本只会把这些模板带入 production worktree，不会自动为 production
启用 PgBouncer 或 Nginx 拓扑。

也可以用 `docker compose up -d` 启动 `compose.yaml` 中的 PostgreSQL（同样映射到 5433）。

### 备份与恢复演练

```bash
scripts/ops/install-systemd.sh backup        # 安装并启用备份 / 恢复演练定时器
scripts/ops/backup.sh backup                 # 立即备份
scripts/ops/backup.sh list                   # 列出备份
scripts/ops/backup.sh restore-check [stamp]  # 恢复到临时库 <db>_restore_check 并核对，默认最新一份
```

| 定时器 | 时间 | 内容 |
| --- | --- | --- |
| `met-jira-backup.timer` | 每天 02:30 | `pg_dump -Fc` 生产库 + 打包附件目录 + `manifest.json`（迁移版本、行数、sha256） |
| `met-jira-restore-check.timer` | 每周日 04:00 | 校验 sha256，恢复到临时库对比行数，确认附件文件齐全，结束后删除临时库 |

备份位于 `~/.met_jira_backups`：`daily/` 保留 14 份，每周日（或 `weekly/` 为空时）以硬链接存入 `weekly/`，保留 8 份。最近结果写在 `last-backup.json` / `last-restore-check.json`，生产 `/api/health` 会返回 `backup_age_hours`。可用环境变量覆盖：`METJIRA_BACKUP_DIR`、`METJIRA_BACKUP_DB`（默认 `met_jira`）、`METJIRA_BACKUP_DATA_DIR`（默认生产 worktree 的 `data/`）、`METJIRA_BACKUP_KEEP_DAILY`、`METJIRA_BACKUP_KEEP_WEEKLY`。

整库恢复（会覆盖生产数据，先停服务）：

```bash
systemctl --user stop met-jira.service
pg_restore --clean --if-exists --no-owner -h 127.0.0.1 -p 5433 -U met_jira -d met_jira ~/.met_jira_backups/daily/<stamp>/db.dump
tar -xzf ~/.met_jira_backups/daily/<stamp>/data.tar.gz -C <生产 worktree>
systemctl --user start met-jira.service
```

### 健康检查

`GET /api/health` 检查数据库连接（并返回 Alembic 版本）与数据目录可写，任一失败返回 503，Ops 控制台据此判断服务状态。

### 用户未完成指派（外部可读）

`GET /api/users/{username}/assigned-open` 返回指派给该用户、状态分类不为 `done` 的问题列表。

- **鉴权**：会话 Cookie；或配置了 `METJIRA_EXTERNAL_API_TOKEN` 时用 `Authorization: Bearer <token>` / `X-Api-Key: <token>`（机器调用，无需登录）。
- **字段**：完整 issue 摘要（与列表用的 `issue_brief` 一致：描述、负责人/报告人/测试、机台、Epic/父项、标签、自定义字段、SLA 等），另含 Mira 项目显示名 `project_title`、指派给当前负责人的时间 `assigned_at`（活动日志；创建时即指派则用 `created_at`）与可点击的绝对 `url`（优先 `METJIRA_PUBLIC_URL`）。
- **分页**：`limit`（1–1000，默认 100）、`offset`。
- **可见性**：Cookie 调用按调用者可见范围过滤私有项目；外部令牌调用可读取全部指派（供门户 / 机器人集成）。

Prometheus 可从主机本地抓取 `GET /metrics`。该端点仅接受 `127.0.0.1` / `::1`
来源，指标标签只使用 HTTP 方法、路由模板、状态码等有界值；Nginx 配置不得代理
`/metrics`，如需远端采集应由主机上的 Prometheus 抓取后再转发。

## 配置

### `.env`

首次运行 `install-systemd.sh` 时由 `.env.example` 生成，并写入随机 `METJIRA_SECRET`（权限 600）。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `METJIRA_DATABASE_URL` | `postgresql+psycopg://met_jira:met_jira_dev@127.0.0.1:5433/met_jira` | 数据库连接 |
| `METJIRA_DB_POOL_SIZE` / `METJIRA_DB_MAX_OVERFLOW` | `5` / `5` | PostgreSQL 常驻连接与突发连接上限 |
| `METJIRA_DB_POOL_TIMEOUT` / `METJIRA_DB_POOL_RECYCLE` | `5` / `300` | 连接池等待与回收秒数 |
| `METJIRA_DB_POOL_USE_LIFO` | `true` | 优先复用最近连接，兼容直连及未来 PgBouncer 部署 |
| `METJIRA_DB_CONNECT_TIMEOUT` / `METJIRA_DB_APPLICATION_NAME` | `5` / `met-jira-<env>` | PostgreSQL 连接超时与 `application_name` |
| `METJIRA_DB_PREPARE_THRESHOLD` | `-1` | `-1` 禁用 psycopg server-side prepared statements，兼容 transaction pooling |
| `METJIRA_PROXY_TRUSTED_IPS` | `127.0.0.1` | Uvicorn 接受 forwarded headers 的代理白名单 |
| `METJIRA_WEB_HOST` / `METJIRA_WEB_PORT` / `METJIRA_WEB_WORKERS` | `127.0.0.1` / `18801` / `2` | Uvicorn 监听与 worker 数；staging unit 显式固定这些值 |
| `METJIRA_SECRET` | 随机 | 会话签名密钥，修改后所有用户需重新登录 |
| `METJIRA_AUTH_MODE` | `auto` | `auto`：AD 优先并保留本地账号；`ldap`：仅 AD；`local`：仅本地 |
| `METJIRA_AD_ADMINS` | 空 | 逗号分隔的 AD 用户名，登录后自动成为系统管理员 |
| `METJIRA_ADMIN_USER` / `METJIRA_ADMIN_PASSWORD` | `admin` / `MetJira123!` | 首次启动创建的本地管理员 |
| `METJIRA_SEED_SAMPLE_PROJECT` | `false` | 为 `true` 时仅首次（`data_dir/.sample_project_seeded` 标记）创建示例项目 `ELEC`；默认关闭，避免删光项目后 bootstrap 再次种回 |
| `METJIRA_SESSION_HOURS` | `12` | 登录有效期 |
| `METJIRA_COOKIE_SECURE` | `false` | 走 HTTPS 反代时设为 `true` |
| `METJIRA_COOKIE_NAME` | `metjira_session` | 会话 Cookie 名；同一主机不同端口共享 Cookie，测试环境须使用不同名称 |
| `METJIRA_DEPLOY_ENV` | `production` | `production` / `staging`，由 systemd 单元设置 |
| `METJIRA_PRESENCE_ROOT` | `~/.met_jira_ops/presence` | 在线心跳与登录 / 导出事件目录，供 Ops 控制台读取 |
| `METJIRA_MAX_UPLOAD_MB` | `50` | 附件 / 导入大小上限 |
| `METJIRA_ATTACHMENT_RECONCILE_GRACE_SECONDS` | `300` | bootstrap 修复遗留附件前的安全等待时间 |
| `METJIRA_DATA_DIR` | `./data` | 附件与导入文件目录 |
| `METJIRA_LLM_ENABLED` | `true` | 关闭后隐藏全部 AI 功能 |
| `METJIRA_LLM_BASE_URL` | `http://10.113.17.65:8001/v1` | OpenAI 兼容接口 |
| `METJIRA_LLM_MODEL` | `DeepSeek V4 Flash` | 模型名 |
| `METJIRA_LLM_TIMEOUT` | `90` | 秒 |
| `METJIRA_ORG_SYNC_HOUR` | `2` | 每日 AD 组织架构同步时刻 |
| `METJIRA_DUE_DIGEST_HOUR` | `8` | 每日到期摘要小时（分钟固定为 30） |
| `METJIRA_REPORT_WEEKDAY` | `fri` | AI 周报生成日（17:30） |
| `METJIRA_SCHEDULER` | `true` | 是否允许独立 scheduler 进程运行；staging 安装时设为 `false` |
| `METJIRA_OUTBOX_BATCH_SIZE` | `20` | worker 每次 claim 的最大事件数 |
| `METJIRA_OUTBOX_LEASE_SECONDS` | `60` | claim 租约；超时后其他 worker 可重领 |
| `METJIRA_OUTBOX_POLL_SECONDS` | `2` | 队列为空时的轮询间隔 |
| `METJIRA_LOGIN_MAX_FAILURES` | `5` | 同一账号在窗口内允许的登录失败次数，超出返回 429（同一 IP 为 4 倍） |
| `METJIRA_LOGIN_WINDOW_SECONDS` | `300` | 登录失败计数窗口与锁定时长 |
| `METJIRA_AI_REQUESTS_PER_MINUTE` | `20` | 每个用户每分钟 AI 请求上限 |
| `METJIRA_AI_MAX_CONCURRENT` | `2` | 每个用户同时进行的 AI 任务上限 |
| `METJIRA_BACKUP_DIR` | `~/.met_jira_backups` | 备份目录，生产健康检查读取其中的 `last-backup.json` |
| `METJIRA_ALERT_WEBHOOK_URL` | 空 | 可选告警适配器；SLA 或组织同步异常时 POST JSON，未配置时仍发送应用内通知 |
| `METJIRA_WECOM_PUSH_URL` | 空 | 65 服务器企业微信网关，如 `http://10.113.17.65:8012/internal/push_markdown`；SLA 提醒按邮箱推送给已绑定企业微信的个人 |
| `METJIRA_WECOM_PUSH_TOKEN` | 空 | 网关的 `TOOL_SHARED_SECRET`（Bearer 令牌） |
| `METJIRA_EXTERNAL_API_TOKEN` | 空 | 外部只读接口共享密钥（如 `assigned-open`）；空则仅会话 Cookie 可用 |
| `METJIRA_SOFTWARE_JIRA_URL` | 空 | 软件 Jira 中转根地址。保存单号、手动刷新，以及每 5 分钟的定时任务都会请求 `{URL}/api/jira/relay?key=`。定时任务只处理未完成的问题，同一单号只请求一次，每轮最多 20 个 |
| `METJIRA_SOFTWARE_JIRA_TOKEN` | 空 | 中转的 `X-Api-Key`。空则不发起请求，详情仍显示「尚未连接」 |
| `METJIRA_PUBLIC_URL` | 空 | 本站外部地址，用于企业微信消息、提醒邮件与外部接口中的问题链接 |
| `METJIRA_SMTP_HOST` / `METJIRA_SMTP_PORT` | 空 / `465` | 提醒邮件 SMTP（SSL），公司邮箱为 `smtp.qiye.aliyun.com` |
| `METJIRA_SMTP_USER` / `METJIRA_SMTP_PASSWORD` | 空 | 发信账号（AI 邮箱 `met.ai@micropolaris.com.cn`）；SLA 提醒按邮件发给负责人、报告人和项目管理员 |
| `METJIRA_MAIL_FROM_NAME` | `Mira` | 发件人显示名 |
| `METJIRA_ALERT_REDIRECT` | 空 | 仅测试环境：所有邮件和企业微信提醒改投到该地址，避免打扰真实同事 |

> 默认管理员密码 `MetJira123!` 仅用于首次登录，请登录后通过左下角用户菜单「修改密码」更改（`.env` 中的密码只在首次创建管理员时生效）。管理员也可以在「管理后台 → 用户」中重置其他本地账号的密码；AD 账号密码由域控管理。

## 配置化流程与部门模板

- 新建项目可选电气、机械或生产模板。模板定义角色、状态、表单分区、自定义字段、列表列及 Excel 导入导出列；现有电气项目行为保持不变。
- 自定义字段存入受模板 schema 校验的 `issues.extra`，未知字段会被拒绝。
- JQL 可用 `cf_<字段 key>` 查询模板字段，支持 `=`、`!=`、`~`、`IN` 和 `IS EMPTY`；报表及 AI 工具会读取同一套模板定义。
- 项目设置 → 状态与工作流可为每个目标状态配置必填字段、审批角色和会签人数。审批决定及意见永久留在问题详情；满足全部会签后自动流转。
- 同一页面可按优先级配置 SLA。后台每 5 分钟扫描，提前提醒负责人、报告人和项目管理员，超时后升级；管理接口 `/api/admin/capacity` 提供容量快照。
- 管理后台 → 共享主数据维护机台、产线和物料。项目设置可将已有机台非破坏性映射到共享目录，不删除原有 `Machine` 数据。
- 管理后台的 AD 页显示最近成功时间、陈旧状态和处理建议，并提供只读连接诊断和回滚式变更预览；定时同步失败也会持久化为同步记录。

## 自动质量检查

GitHub Actions 配置位于 `.github/workflows/ci.yml`，运行 ruff、SQLite 单元测试、PostgreSQL 专项测试（迁移到 head、并发编号锁及 `pg_trgm`）、TypeScript、i18n、构建及 Playwright 移动端 smoke test。本地 PostgreSQL 专项测试需设置独立空库：

```bash
METJIRA_TEST_POSTGRES_URL=postgresql+psycopg://.../met_jira_test python3 -m pytest -q -m postgres
```

容量基线（默认要求搜索 p95 ≤ 2 秒）：

```bash
scripts/ops/capacity-baseline.py --password '<管理员密码>' --requests 100 --concurrency 8
```

### `configs/ldap.env`（AD）

按以下顺序读取，后者覆盖前者：

1. `/data/home/lucas/projects/Eletrical-Area/drawing-review-agent/configs/ldap.env`（与 FileChecker 共用）
2. `configs/ldap.env`（本项目，参考 `configs/ldap.env.example`）

**组织架构全量同步必须配置服务账号密码 `LDAP_APP_PASSWORD`。** 现有共享配置中没有该项，因此目前只能 AD 登录（登录时同步本人信息），无法拉取完整组织架构。补充方式：

```bash
cp configs/ldap.env.example configs/ldap.env
chmod 600 configs/ldap.env
# 编辑 LDAP_APP_DN / LDAP_APP_PASSWORD
systemctl --user restart met-jira.service
```

然后在「管理后台 → AD 同步」中点击「立即同步」，或等待每日定时同步。同步内容包括 OU 树、用户、汇报关系（`manager`）以及禁用账号。

## LLM 智能助手

连接 65 服务器 8001 端口的 OpenAI 兼容接口，启动时探测是否支持原生 tool calling，不支持时退回 JSON 协议。

- 自然语言建单 / 拆分：创建 Issue 时「AI 起草」，可拆分为 Epic + 子需求
- 对话查询与操作：右侧 AI 面板（或 `Ctrl/Cmd+K` → 问 AI）。查询直接执行；**所有写操作都会生成待确认卡片，用户确认后才会执行**，且按当前用户权限校验
- 摘要与周报：Issue 详情「AI 摘要」，报表页「AI 周报」，每周定时生成
- 智能分诊：建单时提示疑似重复（`pg_trgm` 相似度）以及建议的负责人和优先级

所有 AI 调用和操作都记录在「管理后台 → AI」的审计日志中。

## 开发

```bash
# 后端（自动重载）
METJIRA_SCHEDULER=false python3 -m uvicorn backend.main:app --reload --port 8800

# 前端开发服务器：http://localhost:5180，/api 代理到 8800
cd web && npm run dev
```

数据库迁移：

```bash
python3 -m alembic revision --autogenerate -m "describe change"
python3 -m backend.ops bootstrap
```

## 测试

```bash
python3 -m pytest -q             # 使用临时 SQLite，不影响生产库
python3 -m ruff check backend tests
cd web && npm run typecheck && npm run build
```

## Excel 导入

「项目设置 → Excel 导入」支持现有需求表格式：自动识别两行表头与合并的机台组单元格，预览后可调整列映射、人员 / 状态 / 机台匹配，提交后可在导入历史中整批撤销。
