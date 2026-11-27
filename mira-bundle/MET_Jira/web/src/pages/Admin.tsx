import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, qs } from '../api'
import { PasswordModal } from '../components/PasswordModal'
import { Avatar, Empty, Modal, Spinner, Tabs, useDebounced } from '../components/ui'
import { useApp } from '../context'
import { t, tServer } from '../i18n'
import { fmtDateTime } from '../labels'
import type { UserBrief, UserFull } from '../types'
import { MasterDataTab } from './admin/MasterDataTab'
import { TemplatesTab } from './admin/TemplatesTab'

/** One row in a sync diff section — OU paths are plain strings; people are objects. */
type DiffEntry =
  | string
  | {
      username?: string
      name?: string
      org?: string | null
      from?: string | null
      to?: string | null
    }

interface SyncRun {
  id: number
  trigger: string
  status: string
  started_at: string
  finished_at: string | null
  stats: Record<string, number>
  diff: Record<string, DiffEntry[]>
  error: string | null
}

interface SyncStatus {
  ldap: { enabled: boolean; label: string; host: string; port: number; search_base: string; service_account: string; can_sync: boolean }
  scheduler?: { enabled: boolean; org_sync_hour: number }
  runs: SyncRun[]
  health: { ok: boolean; stale: boolean; last_success_at: string | null; latest_status: string; action: string | null }
}

const SYNC_STATUS_LABEL: Record<string, string> = {
  success: '成功',
  failed: '失败',
  running: '进行中',
}

const DIFF_LABELS: Record<string, string> = {
  ou_added: '新增部门',
  ou_removed: '移除部门',
  users_added: '新增人员',
  users_moved: '调岗',
  users_disabled: '停用',
  users_enabled: '重新启用',
  users_skipped_local: '跳过本地账号',
  admins_promoted: '提升管理员',
  admins_demoted: '取消管理员',
}

const DIFF_ORDER = Object.keys(DIFF_LABELS)

function formatDiffEntry(kind: string, entry: DiffEntry): { primary: string; secondary?: string } {
  if (typeof entry === 'string') return { primary: entry }
  const who = [entry.name, entry.username].filter(Boolean).join(' · ') || entry.username || '—'
  if (kind === 'users_moved') {
    const route = [entry.from || t('（无部门）'), entry.to || t('（无部门）')].join(' → ')
    return { primary: who, secondary: route }
  }
  if (kind === 'users_added' && entry.org) return { primary: who, secondary: entry.org }
  return { primary: who }
}

interface SyncDiffData {
  stats: Record<string, number>
  diff: Record<string, DiffEntry[]>
}

interface SyncProbe {
  enabled: boolean
  configured: boolean
  bind_ok: boolean
  search_ok: boolean
  base_ok?: boolean
  latency_ms?: number
  error?: string | null
}

function SyncDiffBody({ data, note }: { data: SyncDiffData; note?: string }) {
  const sections = DIFF_ORDER
    .map((key) => ({ key, items: data.diff?.[key] ?? [] }))
    .filter((s) => s.items.length > 0)
  const extra = Object.entries(data.diff ?? {})
    .filter(([key, items]) => !DIFF_LABELS[key] && items.length > 0)
    .map(([key, items]) => ({ key, items }))
  const all = [...sections, ...extra]
  const counted = DIFF_ORDER.reduce((n, key) => n + (data.stats[`${key}_count`] ?? 0), 0)
  const listed = all.reduce((n, s) => n + s.items.length, 0)
  const total = Math.max(counted, listed)
  return (
    <>
      <div className="small muted" style={{ marginBottom: 12 }}>
        {t('共 {n} 条变更', { n: total })}
        {data.stats.users != null && (
          <> · {t('{users} 人 / {ous} 部门', { users: data.stats.users ?? 0, ous: data.stats.ous ?? 0 })}</>
        )}
        {listed < total && <> · {t('明细仅展示前 {n} 条', { n: listed })}</>}
        {note && <> · {note}</>}
      </div>
      {all.length === 0 ? (
        <div className="faint">{t('本次同步无结构变更')}</div>
      ) : (
        <div className="sync-diff">
          {all.map(({ key, items }) => {
            const count = data.stats[`${key}_count`] ?? items.length
            return (
              <section key={key} className="sync-diff-section">
                <header className="sync-diff-head">
                  <b>{t(DIFF_LABELS[key] ?? key)}</b>
                  <span className="tag">{count}</span>
                </header>
                <ul className="sync-diff-list">
                  {items.map((entry, i) => {
                    const row = formatDiffEntry(key, entry)
                    const reactKey =
                      typeof entry === 'string'
                        ? entry
                        : `${entry.username ?? ''}:${entry.from ?? ''}:${entry.to ?? ''}:${i}`
                    return (
                      <li key={reactKey}>
                        <span className="sync-diff-primary">{row.primary}</span>
                        {row.secondary && <span className="sync-diff-secondary mono">{row.secondary}</span>}
                      </li>
                    )
                  })}
                </ul>
              </section>
            )
          })}
        </div>
      )}
    </>
  )
}

function SyncDiffModal({
  title,
  data,
  note,
  onClose,
}: {
  title: string
  data: SyncDiffData
  note?: string
  onClose: () => void
}) {
  return (
    <Modal title={title} onClose={onClose} size="wide">
      <SyncDiffBody data={data} note={note} />
    </Modal>
  )
}

function ProbeCheck({ ok, label, detail }: { ok: boolean | null; label: string; detail?: string }) {
  const tone = ok == null ? 'faint' : ok ? 'ok-text' : 'err-text'
  const mark = ok == null ? '—' : ok ? '✓' : '✕'
  return (
    <div className="sync-probe-row">
      <span className={`sync-probe-mark ${tone}`} aria-hidden>
        {mark}
      </span>
      <div style={{ minWidth: 0 }}>
        <div className="bold" style={{ fontSize: 13 }}>
          {label}
        </div>
        {detail && <div className="tiny faint">{detail}</div>}
      </div>
    </div>
  )
}

function SyncProbeModal({ probe, onClose }: { probe: SyncProbe; onClose: () => void }) {
  const ok = probe.configured && probe.enabled && probe.bind_ok && probe.search_ok && probe.base_ok !== false
  return (
    <Modal title={t('连接诊断')} onClose={onClose} size="narrow">
      <div className="sync-probe">
        <div className={`sync-probe-summary ${ok ? 'ok' : 'bad'}`}>
          <b>{ok ? t('连接正常') : t('连接异常')}</b>
          {probe.latency_ms != null && (
            <span className="tiny faint">{t('耗时 {ms} ms', { ms: probe.latency_ms })}</span>
          )}
        </div>
        {probe.error && (
          <div className="warn-text small" style={{ marginBottom: 10 }}>
            {tServer(probe.error)}
          </div>
        )}
        <div className="sync-probe-list">
          <ProbeCheck
            ok={probe.enabled}
            label={t('LDAP 已启用')}
            detail={probe.enabled ? undefined : t('请检查 LDAP 配置或环境变量')}
          />
          <ProbeCheck
            ok={probe.configured}
            label={t('服务账号已配置')}
            detail={probe.configured ? undefined : t('缺少服务账号密码（LDAP_APP_PASSWORD）')}
          />
          <ProbeCheck
            ok={probe.configured && probe.enabled ? probe.bind_ok : null}
            label={t('服务账号绑定')}
            detail={
              probe.configured && probe.enabled && !probe.bind_ok
                ? t('无法用服务账号登录目录服务器')
                : undefined
            }
          />
          <ProbeCheck
            ok={probe.bind_ok ? probe.base_ok ?? null : null}
            label={t('Search base 可达')}
            detail={
              probe.bind_ok && probe.base_ok === false
                ? t('Search base 不存在或无权读取')
                : undefined
            }
          />
          <ProbeCheck
            ok={probe.bind_ok ? probe.search_ok : null}
            label={t('组织单元可检索')}
            detail={
              probe.bind_ok && !probe.search_ok
                ? t('无法检索 organizationalUnit')
                : undefined
            }
          />
        </div>
      </div>
    </Modal>
  )
}

export function AdminPage() {
  const { user } = useApp()
  const [tab, setTab] = useState<'sync' | 'users' | 'assets' | 'templates' | 'ai'>('sync')
  if (!user.is_admin) return <Empty icon="🔒" title={t('需要系统管理员权限')} />
  return (
    <div className="page page-wide">
      <div className="page-header">
        <div>
          <h1>{t('管理后台')}</h1>
          <p>{t('AD 组织架构同步、用户权限与 AI 助手运行状况')}</p>
        </div>
      </div>
      <Tabs
        value={tab}
        onChange={(v) => {
          setTab(v)
          // Tall panes (Users / AI) change scroll height; keep the chrome pinned.
          document.querySelector('.content')?.scrollTo({ top: 0 })
        }}
        items={[
          { value: 'sync', label: t('AD 同步') },
          { value: 'users', label: t('用户') },
          { value: 'assets', label: t('主数据') },
          { value: 'templates', label: t('部门模板') },
          { value: 'ai', label: t('AI 助手') },
        ]}
      />
      {tab === 'sync' && <SyncTab />}
      {tab === 'users' && <UsersTab />}
      {tab === 'assets' && <MasterDataTab onOpenTemplates={() => setTab('templates')} />}
      {tab === 'templates' && <TemplatesTab />}
      {tab === 'ai' && <AiTab />}
    </div>
  )
}

function SyncTab() {
  const { toast } = useApp()
  const client = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState<number | null>(null)
  const [probe, setProbe] = useState<SyncProbe | null>(null)
  const [preview, setPreview] = useState<SyncDiffData | null>(null)
  const { data, isLoading } = useQuery({ queryKey: ['admin-sync'], queryFn: () => api.get<SyncStatus>('/api/admin/sync') })
  if (isLoading || !data) return <Spinner center />
  const run = async () => {
    setBusy(true)
    try {
      const res = await api.post<SyncRun>('/api/admin/sync')
      toast(
        res.status === 'success'
          ? t('同步完成：{users} 人，{ous} 个部门', { users: res.stats.users ?? 0, ous: res.stats.ous ?? 0 })
          : t('同步失败：{error}', { error: tServer(res.error) }),
        res.status === 'success' ? 'success' : 'error',
      )
      client.invalidateQueries({ queryKey: ['admin-sync'] })
      client.invalidateQueries({ queryKey: ['org-tree'] })
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  const l = data.ldap
  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="card card-pad">
        <div className="row">
          <h3>{t('LDAP / AD 配置')}</h3>
          <span className={data.health.ok ? 'badge success' : 'badge danger'}>
            {data.health.ok ? t('主数据正常') : t('主数据需处理')}
          </span>
          <span className="spacer" />
          <button className="btn" disabled={busy} onClick={async () => {
            setBusy(true)
            try {
              setProbe(await api.get<SyncProbe>('/api/admin/sync/probe'))
            } catch (error) { toast((error as Error).message, 'error') }
            finally { setBusy(false) }
          }}>{busy ? t('诊断中…') : t('连接诊断')}</button>
          <button className="btn" disabled={busy || !l.can_sync} onClick={async () => {
            setBusy(true)
            try {
              const result = await api.post<SyncDiffData>('/api/admin/sync/preview')
              setPreview(result)
            } catch (error) { toast((error as Error).message, 'error') }
            finally { setBusy(false) }
          }}>{busy ? t('预览中…') : t('预览变更')}</button>
          <button className="btn primary" disabled={busy || !l.can_sync} onClick={run}>
            {busy ? t('同步中…') : t('立即同步')}
          </button>
        </div>
        {data.health.action && <div className="warn-text small" style={{ marginTop: 8 }}>{tServer(data.health.action)}</div>}
        <div className="grid grid-3 small" style={{ marginTop: 12, gap: 10 }}>
          <div>
            <div className="muted">{t('状态')}</div>
            {l.enabled ? <span className="ok-text">{t('已启用（{label}）', { label: l.label })}</span> : <span className="err-text">{t('未配置')}</span>}
          </div>
          <div>
            <div className="muted">{t('服务器')}</div>
            <span className="mono">
              {l.host}:{l.port}
            </span>
          </div>
          <div>
            <div className="muted">Search base</div>
            <span className="mono">{l.search_base}</span>
          </div>
          <div>
            <div className="muted">{t('服务账号')}</div>
            <span className="mono">{l.service_account || '—'}</span>
          </div>
          <div>
            <div className="muted">{t('全量同步')}</div>
            {l.can_sync ? <span className="ok-text">{t('可用')}</span> : <span className="warn-text">{t('缺少服务账号密码（LDAP_APP_PASSWORD）')}</span>}
          </div>
          <div>
            <div className="muted">{t('自动同步')}</div>
            {data.scheduler?.enabled
              ? t('每晚 {hour}:07 自动全量同步；用户登录时也会更新其个人资料和所在部门', {
                  hour: String(data.scheduler.org_sync_hour).padStart(2, '0'),
                })
              : t('本环境未启用定时全量同步；用户登录时仍会更新其个人资料和所在部门')}
          </div>
        </div>
      </div>
      <div className="card">
        <div className="card-header">
          <h3>{t('同步记录')}</h3>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>#</th>
              <th>{t('触发')}</th>
              <th>{t('状态')}</th>
              <th>{t('开始')}</th>
              <th>{t('结果')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.runs.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td>{r.trigger === 'manual' ? t('手动') : t('定时')}</td>
                <td>
                  <span className={r.status === 'success' ? 'ok-text' : r.status === 'failed' ? 'err-text' : 'warn-text'}>
                    {t(SYNC_STATUS_LABEL[r.status] ?? r.status)}
                  </span>
                </td>
                <td className="small">{fmtDateTime(r.started_at)}</td>
                <td className="small">
                  {r.error ? (
                    <span className="err-text">{tServer(r.error).slice(0, 120)}</span>
                  ) : (
                    <>
                      {t('{users} 人 / {ous} 部门', { users: r.stats.users ?? 0, ous: r.stats.ous ?? 0 })}
                      {Object.keys(DIFF_LABELS).map((k) =>
                        r.stats[`${k}_count`] ? (
                          <span key={k} className="tag" style={{ marginLeft: 6 }}>
                            {t(DIFF_LABELS[k])} {r.stats[`${k}_count`]}
                          </span>
                        ) : null,
                      )}
                    </>
                  )}
                </td>
                <td>
                  {Object.values(r.diff).some((v) => v.length) && (
                    <button className="btn ghost sm" onClick={() => setOpen(r.id)}>
                      {t('变更明细')}
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {data.runs.length === 0 && (
              <tr>
                <td colSpan={6} className="faint">
                  {t('尚未同步')}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {open !== null && (() => {
        const run = data.runs.find((r) => r.id === open)
        return run ? (
          <SyncDiffModal
            title={t('同步 #{id} 变更明细', { id: run.id })}
            data={run}
            onClose={() => setOpen(null)}
          />
        ) : null
      })()}
      {preview && (
        <SyncDiffModal
          title={t('预览变更')}
          data={preview}
          note={t('仅预览，不会写入数据库')}
          onClose={() => setPreview(null)}
        />
      )}
      {probe && <SyncProbeModal probe={probe} onClose={() => setProbe(null)} />}
    </div>
  )
}

function UsersTab() {
  const { toast } = useApp()
  const client = useQueryClient()
  const [q, setQ] = useState('')
  const [creating, setCreating] = useState(false)
  const [resetting, setResetting] = useState<UserFull | null>(null)
  const debounced = useDebounced(q, 250)
  const { data, isLoading } = useQuery({
    queryKey: ['admin-users', debounced],
    queryFn: () => api.get<{ total: number; users: UserFull[] }>(`/api/admin/users${qs({ q: debounced, limit: 200 })}`),
  })
  const update = async (u: UserFull, body: Record<string, unknown>) => {
    try {
      await api.put(`/api/admin/users/${u.id}`, body)
      client.invalidateQueries({ queryKey: ['admin-users'] })
    } catch (err) {
      toast((err as Error).message, 'error')
    }
  }
  return (
    <div className="card">
      <div className="card-header">
        <input className="input sm" style={{ maxWidth: 280 }} placeholder={t('搜索用户')} value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="small muted">{t('共 {n} 人', { n: data?.total ?? 0 })}</span>
        <span className="spacer" />
        <button className="btn sm" onClick={() => setCreating(true)}>
          {t('＋ 本地账号')}
        </button>
      </div>
      <div className="tiny muted" style={{ padding: '0 14px 10px' }}>
        {t('AD 账号的「系统管理员」由环境变量 METJIRA_AD_ADMINS 决定；登录或组织同步时会覆盖本页勾选。「项目管理员」可在此直接设置（AD/本地均可），拥有新建项目权限，同步不会覆盖。')}
      </div>
      {isLoading ? (
        <Spinner center />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>{t('用户')}</th>
              <th>{t('部门')}</th>
              <th>{t('来源')}</th>
              <th>{t('最近登录')}</th>
              <th>{t('系统管理员')}</th>
              <th title={t('可新建项目；系统管理员默认也拥有')}>{t('项目管理员')}</th>
              <th>{t('启用')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data?.users.map((u) => (
              <tr key={u.id}>
                <td>
                  <span className="row">
                    <Avatar user={u} size={26} />
                    <span>
                      <div className="bold">{u.display_name}</div>
                      <div className="tiny faint">{u.username}</div>
                    </span>
                  </span>
                </td>
                <td className="small muted">{u.org_path}</td>
                <td className="small">{u.auth_source === 'ldap' ? 'AD' : t('本地')}</td>
                <td className="small">{fmtDateTime(u.last_login_at)}</td>
                <td>
                  <input
                    type="checkbox"
                    checked={u.is_admin}
                    title={
                      u.auth_source === 'ldap'
                        ? t('AD 账号请写入 METJIRA_AD_ADMINS（逗号分隔用户名），否则下次登录/同步会还原')
                        : undefined
                    }
                    onChange={(e) => {
                      if (u.auth_source === 'ldap') {
                        toast(
                          t('AD 账号请把用户名写入服务器环境变量 METJIRA_AD_ADMINS，仅勾选会在下次登录或同步时被覆盖'),
                          'error',
                        )
                      }
                      update(u, { is_admin: e.target.checked })
                    }}
                  />
                </td>
                <td>
                  <input
                    type="checkbox"
                    checked={u.can_create_projects || u.is_admin}
                    disabled={u.is_admin}
                    title={t('可新建项目；系统管理员默认也拥有')}
                    onChange={(e) => update(u, { can_create_projects: e.target.checked })}
                  />
                </td>
                <td>
                  <input type="checkbox" checked={u.enabled} onChange={(e) => update(u, { enabled: e.target.checked })} />
                </td>
                <td>
                  {u.auth_source === 'local' && (
                    <button className="btn sm ghost" onClick={() => setResetting(u)}>
                      {t('重置密码')}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {creating && <LocalUserModal onClose={() => setCreating(false)} />}
      {resetting && <PasswordModal target={resetting} onClose={() => setResetting(null)} />}
    </div>
  )
}

function LocalUserModal({ onClose }: { onClose: () => void }) {
  const { toast } = useApp()
  const client = useQueryClient()
  const [submitted, setSubmitted] = useState(false)
  const [form, setForm] = useState({
    username: '', display_name: '', password: '', email: '', is_admin: false, can_create_projects: false,
  })
  const errors = {
    username: submitted && !form.username.trim() ? t('请填写用户名') : '',
    display_name: submitted && !form.display_name.trim() ? t('请填写显示名') : '',
    password: form.password && form.password.length < 6
      ? t('密码至少 6 位')
      : submitted && !form.password
        ? t('请填写密码')
        : '',
  }
  const canSubmit = !!form.username.trim() && !!form.display_name.trim() && form.password.length >= 6
  const submit = async () => {
    setSubmitted(true)
    if (!canSubmit) {
      toast(
        (!form.username.trim() ? t('请填写用户名') : '')
          || (!form.display_name.trim() ? t('请填写显示名') : '')
          || (!form.password ? t('请填写密码') : form.password.length < 6 ? t('密码至少 6 位') : '')
          || t('请检查标红的输入项'),
        'error',
      )
      return
    }
    try {
      await api.post('/api/admin/users', { ...form, email: form.email || null })
      client.invalidateQueries({ queryKey: ['admin-users'] })
      toast(t('已创建'), 'success')
      onClose()
    } catch (err) {
      toast((err as Error).message, 'error')
    }
  }
  return (
    <Modal
      title={t('新建本地账号')}
      size="narrow"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            {t('取消')}
          </button>
          <button className="btn primary" onClick={submit}>
            {t('创建')}
          </button>
        </>
      }
    >
      <div className="col" style={{ gap: 12 }}>
        <div className="small muted">{t('本地账号用于外部协作者或 AD 不可用时的应急登录。')}</div>
        {(['username', 'display_name', 'email', 'password'] as const).map((f) => {
          const error = f === 'email' ? '' : errors[f]
          return (
            <div key={f} className={`field${error ? ' is-invalid' : ''}`}>
              <label>{t({ username: '用户名', display_name: '显示名', email: '邮箱', password: '密码（至少 6 位）' }[f])}</label>
              <input
                className={`input${error ? ' is-invalid' : ''}`}
                type={f === 'password' ? 'password' : 'text'}
                value={form[f]}
                aria-invalid={!!error}
                onChange={(e) => setForm({ ...form, [f]: e.target.value })}
              />
              {error ? <div className="field-error" role="alert">{error}</div> : null}
            </div>
          )
        })}
        <label className="row small">
          <input type="checkbox" checked={form.is_admin} onChange={(e) => setForm({ ...form, is_admin: e.target.checked })} /> {t('系统管理员')}
        </label>
        <label className="row small">
          <input
            type="checkbox"
            checked={form.can_create_projects || form.is_admin}
            disabled={form.is_admin}
            onChange={(e) => setForm({ ...form, can_create_projects: e.target.checked })}
          /> {t('项目管理员')}
          <span className="muted">（{t('可新建项目')}）</span>
        </label>
      </div>
    </Modal>
  )
}

interface Audit {
  id: number
  user: UserBrief | null
  kind: string
  prompt: string
  tool_calls: { name: string; error?: string }[]
  result: string
  ok: boolean
  latency_ms: number
  created_at: string
}

interface AlertStatus {
  wecom_configured: boolean
  mail_configured: boolean
  mail_from: string | null
  redirect: string | null
}

function AlertsCard() {
  const { toast } = useApp()
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const status = useQuery({ queryKey: ['admin-alerts'], queryFn: () => api.get<AlertStatus>('/api/admin/alerts') })
  const s = status.data
  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    try {
      await fn()
    } catch (error) {
      toast((error as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  const sendWecom = () =>
    run(async () => {
      const result = await api.post<{ delivered: { email: string; ok: boolean; error?: string }[]; error?: string }>(
        '/api/admin/alerts/test',
        { email: email.trim() || null },
      )
      const first = result.delivered?.[0]
      if (first?.ok) toast(t('已推送到 {email}', { email: first.email }), 'success')
      else {
        const raw = first?.error ?? result.error ?? ''
        toast(
          raw === 'no WeCom delivery target' ? t('该邮箱尚未绑定企业微信') : tServer(raw) || t('推送失败'),
          'error',
        )
      }
    })
  const sendMail = () =>
    run(async () => {
      await api.post('/api/admin/alerts/test-mail', { email: email.trim() || null })
      toast(t('测试邮件已发送'), 'success')
    })
  const sendDigest = () =>
    run(async () => {
      const r = await api.post<{ users: number; issues: number; queued: number }>('/api/admin/reminders/due-digest')
      toast(t('到期汇总：{users} 人，{issues} 个问题，已排队 {queued} 封', r), 'success')
    })
  const badge = (ok?: boolean) => (ok ? <span className="ok-text">● {t('已配置')}</span> : <span className="err-text">● {t('未配置')}</span>)
  return (
    <div className="card card-pad">
      <b>{t('提醒推送')}</b>
      <div className="small muted">{t('SLA 提醒与升级会发送给负责人、报告人和项目管理员；企业微信仅推送给已绑定的用户')}</div>
      <div className="small muted">{t('邮件还会在审批待处理/有结果、被指派、进入测试、测试未通过、重新打开和被 @ 时即时发送；每天 8:30 发送到期汇总，每周五发送 AI 周报')}</div>
      <div className="row" style={{ marginTop: 12, gap: 24 }}>
        <span>
          {t('企业微信推送')} {badge(s?.wecom_configured)}
        </span>
        <span>
          {t('提醒邮件')} {badge(s?.mail_configured)}
          {s?.mail_from && <span className="small muted mono"> {s.mail_from}</span>}
        </span>
      </div>
      {s?.redirect && <div className="small warn-text" style={{ marginTop: 8 }}>{t('测试环境：所有提醒改投到 {email}', { email: s.redirect })}</div>}
      {(s?.wecom_configured || s?.mail_configured) && (
        <div className="row" style={{ marginTop: 12 }}>
          <input
            className="input"
            style={{ maxWidth: 320 }}
            placeholder={s?.redirect || t('收件邮箱（默认当前账号）')}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          {s?.wecom_configured && (
            <button className="btn sm" disabled={busy} onClick={sendWecom}>
              {t('发送测试消息')}
            </button>
          )}
          {s?.mail_configured && (
            <button className="btn sm" disabled={busy} onClick={sendMail}>
              {t('发送测试邮件')}
            </button>
          )}
          {s?.mail_configured && (
            <button className="btn sm" disabled={busy} onClick={sendDigest}>
              {t('立即发送到期汇总')}
            </button>
          )}
          {busy && <Spinner />}
        </div>
      )}
    </div>
  )
}

function AiTab() {
  const status = useQuery({
    queryKey: ['agent-status-force'],
    queryFn: () => api.get<{ enabled: boolean; reachable: boolean; tool_mode: string; model: string; base_url?: string; error?: string }>('/api/agent/status?force=true'),
  })
  const audits = useQuery({ queryKey: ['agent-audits'], queryFn: () => api.get<{ audits: Audit[] }>('/api/admin/agent/audits') })
  const s = status.data
  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="card card-pad">
        <div className="row">
          <span className="ai-orb" />
          <div>
            <b>{s?.model ?? 'LLM'}</b>
            <div className="small muted mono">{s?.base_url}</div>
          </div>
          <span className="spacer" />
          {status.isFetching ? (
            <Spinner />
          ) : s?.reachable ? (
            <span className="ok-text">● {t('在线 · {mode}', { mode: s.tool_mode === 'native' ? t('原生工具调用') : t('JSON 工具协议') })}</span>
          ) : (
            <span className="err-text">● {s?.enabled ? t('不可达：{error}', { error: tServer(s.error) }) : t('未启用')}</span>
          )}
          <button className="btn sm" onClick={() => status.refetch()}>
            {t('重新检测')}
          </button>
        </div>
      </div>
      <AlertsCard />
      <div className="card">
        <div className="card-header">
          <h3>{t('调用记录')}</h3>
          <span className="small muted">{t('所有 AI 调用（对话、草稿、分派、总结、报告）都会记录')}</span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>{t('时间')}</th>
              <th>{t('用户')}</th>
              <th>{t('类型')}</th>
              <th>{t('输入')}</th>
              <th>{t('工具')}</th>
              <th>{t('耗时')}</th>
              <th>{t('结果')}</th>
            </tr>
          </thead>
          <tbody>
            {audits.data?.audits.map((a) => (
              <tr key={a.id}>
                <td className="small">{fmtDateTime(a.created_at)}</td>
                <td className="small">{a.user?.display_name}</td>
                <td>
                  <span className="tag">{a.kind}</span>
                </td>
                <td className="small" style={{ maxWidth: 320 }}>
                  <div className="ellipsis" title={a.prompt}>
                    {a.prompt}
                  </div>
                </td>
                <td className="small">{(a.tool_calls ?? []).map((call) => call.name).join(', ')}</td>
                <td className="small">{(a.latency_ms / 1000).toFixed(1)}s</td>
                <td>{a.ok ? <span className="ok-text">✓</span> : <span className="err-text" title={tServer(a.result)}>✗</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
