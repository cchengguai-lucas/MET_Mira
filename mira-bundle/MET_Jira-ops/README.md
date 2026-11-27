# MET Jira Ops

MET Jira 的运维与使用监控控制台，与 `MET_Jira`（生产 :8800）/ `MET_Jira-staging`（测试 :8810）并列。独立部署，不导入 MET Jira 的任何代码。运行在 `10.113.17.65`：

- 读取 `~/.met_jira_ops/presence/{production,staging}/` 下的在线心跳，以及 `events/*.jsonl` 中的登录、退出、Excel 导出事件
- 以只读角色 `met_jira_ops` 查询 `met_jira` / `met_jira_staging` 数据库（会话强制只读，且仅有 SELECT 权限；Postgres 在 `127.0.0.1:5434`）
- 通过 `systemctl --user` 和 `/api/health` 探测服务状态，从 `journalctl --user` 解析 uvicorn 访问日志与错误日志
- 自身数据（在线采样、使用时长、探测记录）存放在 `~/.met_jira_ops/ops.sqlite3`，保留 120 天

```
/home/hal/docker/mira/MET_Jira           # production :8800（分支 production）
/home/hal/docker/mira/MET_Jira-staging   # staging    :8810（分支 staging；8801 被 BOM 占用）
/home/hal/docker/mira/MET_Jira-ops       # 本控制台   :8803（分支 main）
```

## 功能

| 页面 | 内容 |
| --- | --- |
| 概览 | 生产 / 测试 / PostgreSQL / Ops 服务状态（运行时长、内存、自动重启次数、健康检查延迟）；在线人数、今日登录、7 日活跃、未完成与逾期 Issue、24 小时请求量与 5xx 错误率、AI 调用、LLM 与生产可用率、数据库大小；当前在线成员及所在页面 |
| 在线与使用 | 在线趋势（24 小时 / 7 天 / 30 天）、每日使用人数与时长、页面停留分布、使用时长排行、登录与导出事件、部门使用与覆盖率 |
| 业务 | Issue 新建与解决、协作活跃度、项目进度（待办 / 进行中 / 完成 / 逾期）、操作类型、最活跃成员、需求来源部门、部门人数、AD 同步记录、Excel 导入记录 |
| AI 助手 | 按功能的每日调用、成功率、P95 延迟、写操作采纳率、使用最多的成员、最近失败、65:8001 接口延迟与可用率 |
| 请求与错误 | 每小时请求（成功 / 4xx / 5xx）、热门接口、5xx 接口、错误日志与 Traceback、服务启动记录 |
| 系统 | CPU 负载、内存、磁盘、附件 / 数据库 / 心跳目录占用、探测延迟曲线、生产与测试数据库表大小及迁移版本 |

除「概览」和「系统」外，右上角可切换查看生产或测试环境。页面每 15 秒自动刷新。

在线的判定：MET Jira 前端在标签页可见且用户 5 分钟内有操作时，每 30 秒发送一次心跳；Ops 每 15 秒采样，2 分钟内有心跳即视为在线，并按所在页面累计使用时长。

## 登录

两种方式：

- **MET AD 域账号**（与 Interlock-Maker Ops 一致）：账号须在 `configs/ops.env` 的 `METJIRA_OPS_USERS`（逗号分隔的 sAMAccountName）中，或是 MET Jira 生产环境中已启用的系统管理员。先校验授权，再用服务账号查找用户并以用户本人凭据绑定；AD 中已禁用的账号无法登录。
- **MET Jira 本地管理员**（`auth_source = local` 且为系统管理员），用于 AD 不可用时兜底。

LDAP 配置不在 Ops 中重复保存，按以下顺序读取，后者覆盖前者，进程环境变量优先：

1. `METJIRA_OPS_LDAP_SHARED`（默认 `MET_Jira/configs/ldap.env`）
2. `MET_Jira/configs/ldap.env`（MET Jira 生产配置，含服务账号密码）
3. `MET_Jira-ops/configs/ldap.env`（可选，仅 Ops 使用）
4. `METJIRA_OPS_LDAP_ENV` 指向的文件（可选）

「系统」页显示当前生效的 LDAP 服务器、服务账号、密码是否已配置以及配置来源（不显示密码）。修改后执行 `systemctl --user restart met-jira-ops.service` 生效。

## 组织架构

MET Jira 通过服务账号把 AD 组织架构同步到 `org_units` 并为用户设置所属部门（每日定时，也可在「组织架构」页手动同步）。Ops 以路径 `MET / <一级部门> / ...` 的一级部门为单位统计：

- 在线与使用：部门使用人数、在职 AD 账号数、覆盖率、总时长与人均时长；排行与当前在线显示成员所属团队
- 业务：需求来源部门（按报告人）、各部门在职人数
- 概览：已同步的 AD 账号数、部门数和最近一次同步时间

## 安装

```bash
scripts/install.sh
```

首次运行会：

1. 生成 `configs/ops.env`（权限 600，已被 Git 忽略），包含会话密钥、只读数据库角色密码和 `METJIRA_OPS_USERS`（可在运行前通过同名环境变量指定）
2. 创建或更新 PostgreSQL 角色 `met_jira_ops`，并在 `met_jira`、`met_jira_staging` 上授予 SELECT（含今后迁移新建的表）
3. 安装并启动 `met-jira-ops.service`（端口 8803）

```bash
systemctl --user status met-jira-ops.service
journalctl --user -u met-jira-ops.service -f
```

## 测试

```bash
python3 -m pytest -q
python3 -m ruff check .
```
