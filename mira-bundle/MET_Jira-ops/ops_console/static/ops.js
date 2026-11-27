'use strict'

const REFRESH_MS = 15000
const COLORS = ['#0a84ff', '#ff9f0a', '#30d158', '#bf5af2', '#ff453a', '#64d2ff', '#ffd60a', '#ac8e68', '#5e5ce6', '#ff375f']
const ENV_COLOR = { production: '#0a84ff', staging: '#ff9f0a' }
const ACTION_LABELS = {
  created: 'Created', updated: 'Field edit', transition: 'Transition', comment: 'Comment', commented: 'Comment',
  assigned: 'Assigned', attachment: 'Attachment', link: 'Link', linked: 'Link', deleted: 'Deleted', rank: 'Rank',
  sprint: 'Sprint', moved: 'Moved', imported: 'Imported', watch: 'Watch',
}
const AI_KIND_LABELS = { chat: 'Chat', draft: 'AI draft', split: 'Split', triage: 'Triage', summary: 'Summary', report: 'Weekly report', probe: 'Probe' }
const EVENT_LABELS = { login: 'Login', logout: 'Logout', export_excel: 'Excel export' }
const ACTION_STATUS = { pending: 'Pending', done: 'Done', rejected: 'Rejected', failed: 'Failed' }

const state = {
  tab: localStorage.getItem('ops-tab') || 'overview',
  env: localStorage.getItem('ops-env') || 'production',
  overviewHours: 6, usageDays: 30, presenceHours: 6, businessDays: 30, aiDays: 30, logsHours: 6,
}
const charts = {}

// ---------- helpers ----------
const $ = (sel) => document.querySelector(sel)
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
const num = (n) => (n == null ? '—' : Number(n).toLocaleString('en-US'))
const pct = (r, digits = 1) => (r == null ? '—' : `${(r * 100).toFixed(digits)}%`)
function bytes(n) {
  if (n == null) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = Number(n), i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(i ? 1 : 0)} ${units[i]}`
}
function duration(sec) {
  if (sec == null) return '—'
  sec = Math.round(sec)
  if (sec < 60) return `${sec} s`
  if (sec < 3600) return `${Math.round(sec / 60)} min`
  if (sec < 86400) return `${(sec / 3600).toFixed(1)} h`
  return `${(sec / 86400).toFixed(1)} d`
}
function ago(sec) { return sec < 60 ? `${sec}s ago` : `${Math.round(sec / 60)} min ago` }
function fmtTime(value) {
  if (!value) return '—'
  const d = typeof value === 'number' ? new Date(value) : new Date(value)
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
}
function avatar(label, id) {
  const text = String(label || '?').slice(-2)
  return `<span class="avatar" style="background:${COLORS[(id || 0) % COLORS.length]}">${esc(text)}</span>`
}
function who(label, id, extra = '') {
  return `<span class="who">${avatar(label, id)}<span>${esc(label)}${extra}</span></span>`
}
function envLabel(key) { return key === 'staging' ? 'Staging' : 'Production' }
function envPill(key) { return `<span class="pill ${key === 'staging' ? 'staging' : 'prod'}">${envLabel(key)}</span>` }
function errorBox(data) { return data && data.error ? `<div class="error-box">Data unavailable: ${esc(data.error)}</div>` : '' }
function table(headers, rows, empty = 'No data') {
  if (!rows.length) return `<div class="empty">${empty}</div>`
  const head = headers.map((h) => `<th class="${h.num ? 'num' : ''}">${esc(h.label)}</th>`).join('')
  return `<table><thead><tr>${head}</tr></thead><tbody>${rows.map((r) => `<tr>${r.join('')}</tr>`).join('')}</tbody></table>`
}
const td = (html, cls = '') => `<td class="${cls}">${html}</td>`
function barTd(value, max, label) {
  const w = max ? Math.max(2, (value / max) * 100) : 0
  return `<td class="bar-cell"><div class="bar" style="width:${w}%"></div><span>${label ?? num(value)}</span></td>`
}

async function api(path) {
  const res = await fetch(path, { credentials: 'same-origin' })
  if (res.status === 401) { location.href = '/login'; throw new Error('login') }
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

// ---------- charts ----------
function themeVars() {
  const css = getComputedStyle(document.documentElement)
  return { text: css.getPropertyValue('--text-2').trim(), grid: css.getPropertyValue('--border').trim() }
}
function chart(id, option) {
  const el = document.getElementById(id)
  if (!el || !window.echarts) return
  let c = charts[id]
  if (!c || c.isDisposed()) { c = echarts.init(el, null, { renderer: 'canvas', locale: 'EN' }); charts[id] = c }
  const t = themeVars()
  const axis = { axisLine: { lineStyle: { color: t.grid } }, axisLabel: { color: t.text }, splitLine: { lineStyle: { color: t.grid } } }
  const base = {
    color: COLORS,
    textStyle: { fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, PingFang SC, Microsoft YaHei, sans-serif' },
    grid: { left: 44, right: 16, top: 30, bottom: 30 },
    tooltip: { trigger: 'axis' },
    legend: { top: 0, right: 0, textStyle: { color: t.text }, itemWidth: 10, itemHeight: 10 },
  }
  const merged = { ...base, ...option }
  for (const key of ['xAxis', 'yAxis']) {
    if (!merged[key]) continue
    merged[key] = (Array.isArray(merged[key]) ? merged[key] : [merged[key]]).map((a) => ({ ...axis, ...a }))
  }
  // Time series: any sample counts (0 online is data). Category/pie: all-zero means empty.
  const hasData = (merged.series || []).some((s) => (s.data || []).some((v) => {
    if (Array.isArray(v)) return v[1] != null
    const y = v && typeof v === 'object' ? v.value : v
    return y != null && y !== 0
  }))
  merged.graphic = hasData ? [] : [{ type: 'text', left: 'center', top: 'middle', style: { text: 'No data', fill: t.text, fontSize: 13 } }]
  c.setOption(merged, true)
}
window.addEventListener('resize', () => Object.values(charts).forEach((c) => c.resize()))

const timeAxis = { type: 'time' }
const valueAxis = (extra = {}) => ({ type: 'value', minInterval: 1, ...extra })
// Isolated points between gaps are invisible without symbols, so show them when data is sparse.
const sparse = (data) => data.filter((v) => (Array.isArray(v) ? v[1] : v) != null).length < 60
const line = (name, data, color, extra = {}) => ({ name, type: 'line', data, showSymbol: sparse(data), symbolSize: 5, smooth: 0.2, connectNulls: false, lineStyle: { width: 2, color }, itemStyle: { color }, ...extra })
const area = (name, data, color) => line(name, data, color, { areaStyle: { opacity: 0.12 } })
const bars = (name, data, color, stack) => ({ name, type: 'bar', data, stack, itemStyle: { color, borderRadius: stack ? 0 : [4, 4, 0, 0] }, barMaxWidth: 26 })

// ---------- overview ----------
function serviceCard(s) {
  const up = s.state === 'active'
  const health = s.http_ok === null ? up : s.http_ok
  const cls = up && health ? 'ok' : up ? 'warn' : 'bad'
  const meta = [
    `State ${esc(s.state)}${s.sub_state && s.sub_state !== 'running' ? ` (${esc(s.sub_state)})` : ''}`,
    s.uptime_seconds != null ? `Up ${duration(s.uptime_seconds)}` : '',
    s.memory_bytes != null ? `Memory ${bytes(s.memory_bytes)}` : '',
    s.latency_ms != null ? `Health check ${s.latency_ms} ms` : s.http_ok === false ? 'Health check failed' : '',
    s.restarts ? `Auto-restarted ${s.restarts}×` : '',
  ].filter(Boolean)
  const link = s.url ? `<a class="small" href="${esc(s.url)}" target="_blank" rel="noopener">Open ↗</a>` : ''
  return `<div class="card service"><div class="title"><span class="dot ${cls}"></span>${esc(s.label)}<span class="spacer"></span>${link}</div><div class="meta">${meta.map((m) => `<span>${m}</span>`).join('')}</div></div>`
}

function kpi(label, value, sub = '', cls = '') {
  return `<div class="card kpi ${cls}"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`
}

async function loadOverview() {
  const hours = state.overviewHours
  const [data, pres] = await Promise.all([api(`/api/overview?hours=${hours}`), api(`/api/presence?hours=${hours}`)])
  $('#services').innerHTML = data.services.map(serviceCard).join('')
  const prod = data.envs.production
  const stg = data.envs.staging
  const c = prod.counts || {}
  const req = prod.requests || {}
  const llm = data.llm || {}
  const cards = [
    kpi('Online (production)', num(prod.online.length), `Staging online ${stg.online.length}`),
    kpi('Logins today', num(c.logins_today), `7-day active ${num(c.active_7d)} / enabled accounts ${num(c.users_enabled)}`),
    kpi('Open issues', num(c.issues_open), `${num(c.issues_total)} total · ${num(c.projects)} projects`),
    kpi('Overdue', num(c.issues_overdue), 'Open and past the due date', c.issues_overdue ? 'warn' : ''),
    kpi('Created / resolved today', `${num(c.created_today)} / ${num(c.resolved_today)}`, `7-day comments ${num(c.comments_7d)}`),
    kpi(`Requests (${hours}h)`, num(req.requests), `5xx error rate ${pct(req.error_rate, 2)} · ${num(req.clients)} client IPs`, req.error_rate > 0.01 ? 'bad' : ''),
    kpi('AI calls (24h)', num(c.ai_calls_24h), `Pending actions ${num(c.ai_pending)}`),
    kpi('LLM uptime', pct(llm.ratio), llm.avg_ms != null ? `Avg ${llm.avg_ms} ms · 24 h` : 'No probe data yet', llm.ratio != null && llm.ratio < 0.95 ? 'bad' : ''),
    kpi('Production uptime', pct(prod.uptime_24h.ratio, 2), `7-day ${pct(prod.uptime_7d.ratio, 2)}`, prod.uptime_24h.ratio != null && prod.uptime_24h.ratio < 0.99 ? 'bad' : ''),
    kpi('Database', bytes(c.db_bytes), `Connections ${num(c.db_connections)}`),
    kpi('AD org chart', `${num(c.ad_users)} people`, c.org_synced_at ? `${num(c.org_units)} departments · synced ${fmtTime(c.org_synced_at)}` : 'Not synced yet', c.org_sync_status === 'failed' ? 'bad' : ''),
  ]
  $('#kpis').innerHTML = (c.error ? errorBox(c) : '') + cards.join('')

  const online = [
    ...prod.online.map((s) => ({ ...s, env: 'production' })),
    ...stg.online.map((s) => ({ ...s, env: 'staging' })),
  ]
  $('#online-caption').textContent = `${online.length} ${online.length === 1 ? 'person' : 'people'} · active in the last 2 min`
  $('#online-now').innerHTML = online.length
    ? online.map((s) => `<div class="online-item">${avatar(s.label, s.user_id)}<div style="flex:1"><div>${esc(s.label)} <span class="faint small">${esc(s.username)}</span></div><div class="small muted">${esc(s.page_label)}${s.team ? ` · <span class="faint">${esc(s.team)}</span>` : ''}</div></div>${envPill(s.env)}<span class="small faint">${ago(s.age)}</span></div>`).join('')
    : '<div class="empty">Nobody online right now</div>'

  presenceChart('chart-online-24h', pres)
  $('#requests-overview-caption').textContent = `From the uvicorn access log · ${hours} h · ${hours <= 6 ? 'per 5 min' : 'per hour'}`
  requestsChart('chart-requests-overview', req)
}

const TT_MUTED = 'color:#8e8e93'
function presenceTooltip(entries) {
  return (params) => {
    if (!params.length) return ''
    const blocks = params.map((p) => {
      const [, peak, ids = []] = p.value
      const people = entries[p.seriesIndex]?.[1].people || {}
      const groups = {}
      for (const id of ids) {
        const u = people[id] || { name: `#${id}`, team: 'Unassigned' }
        ;(groups[u.team] ||= []).push(u.name)
      }
      const teams = Object.entries(groups).sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0], 'en'))
      const shown = teams.slice(0, 8).map(([team, names]) =>
        `<div style="margin:2px 0 0 14px"><span style="${TT_MUTED}">${esc(team)}</span>&ensp;${esc(names.join(', '))}</div>`).join('')
      const more = teams.length > 8 ? `<div style="margin-left:14px;${TT_MUTED}">+${teams.length - 8} more departments</div>` : ''
      const detail = ids.length ? shown + more
        : peak ? `<div style="margin-left:14px;${TT_MUTED}">No per-person detail (older data)</div>` : ''
      const seen = ids.length > (peak || 0) ? `<span style="${TT_MUTED}">&ensp;${ids.length} seen in interval</span>` : ''
      return `<div style="margin-top:6px">${p.marker}${esc(p.seriesName)}&ensp;<b>${peak ?? '—'}</b>${seen}${detail}</div>`
    })
    return `<div style="${TT_MUTED}">${esc(fmtTime(params[0].value[0]))}</div>${blocks.join('')}`
  }
}

function presenceChart(id, pres) {
  const entries = Object.entries(pres)
  chart(id, {
    xAxis: timeAxis, yAxis: valueAxis(),
    tooltip: { trigger: 'axis', confine: true, formatter: presenceTooltip(entries), extraCssText: 'max-width:420px;white-space:normal' },
    series: entries.map(([key, v]) => area(envLabel(key), v.history, ENV_COLOR[key])),
  })
}

function requestsChart(id, req) {
  const s = req.series || []
  chart(id, {
    xAxis: timeAxis, yAxis: valueAxis(),
    series: [
      bars('Success', s.map((r) => [r[0], r[1] - r[2] - r[3]]), '#0a84ff', 'req'),
      bars('4xx', s.map((r) => [r[0], r[2]]), '#ff9f0a', 'req'),
      bars('5xx', s.map((r) => [r[0], r[3]]), '#ff453a', 'req'),
    ],
  })
}

// ---------- usage ----------
async function loadUsage() {
  const [usage, pres] = await Promise.all([
    api(`/api/usage?env=${state.env}&days=${state.usageDays}`),
    api(`/api/presence?hours=${state.presenceHours}`),
  ])
  presenceChart('chart-presence', pres)
  chart('chart-daily-usage', {
    xAxis: { type: 'category', data: usage.daily.map((d) => d.day.slice(5)) },
    yAxis: [valueAxis({ name: 'Users' }), { type: 'value', name: 'Hours', splitLine: { show: false } }],
    grid: { left: 44, right: 44, top: 36, bottom: 30 },
    series: [
      bars('Users', usage.daily.map((d) => d.users), '#0a84ff'),
      line('Time (hours)', usage.daily.map((d) => +(d.seconds / 3600).toFixed(2)), '#30d158', { yAxisIndex: 1 }),
    ],
  })
  chart('chart-pages', {
    tooltip: { trigger: 'item', formatter: (p) => `${p.name}<br>${duration(p.value)} · ${p.percent}%` },
    legend: { show: false },
    series: [{
      type: 'pie', radius: ['45%', '72%'], itemStyle: { borderRadius: 6, borderColor: 'transparent', borderWidth: 2 },
      label: { color: themeVars().text, formatter: '{b} {d}%' },
      data: usage.pages.map((p) => ({ name: p.label, value: p.seconds })),
    }],
  })
  const lb = usage.leaderboard
  const max = Math.max(1, ...lb.map((r) => r.seconds))
  $('#leaderboard').innerHTML = table(
    [{ label: 'Member' }, { label: 'Department' }, { label: 'Time online' }, { label: 'Active days', num: true }],
    lb.map((r) => [td(who(r.label, r.user_id)), td(esc(r.team || '—'), 'small muted'), barTd(r.seconds, max, duration(r.seconds)), td(r.active_days, 'num')]),
    'No usage in the selected range',
  )
  const depts = usage.departments || []
  const maxD = Math.max(1, ...depts.map((d) => d.seconds))
  $('#dept-usage').innerHTML = table(
    [{ label: 'Department' }, { label: 'Users', num: true }, { label: 'Headcount', num: true }, { label: 'Coverage' }, { label: 'Total time' }, { label: 'Per user', num: true }],
    depts.map((d) => [
      td(`<b>${esc(d.dept)}</b>`), td(d.users, 'num'), td(d.headcount || '—', 'num'),
      d.coverage != null ? barTd(d.users, d.headcount, pct(d.coverage, 0)) : td('—', 'muted'),
      barTd(d.seconds, maxD, duration(d.seconds)), td(duration(d.seconds / Math.max(1, d.users)), 'num'),
    ]),
    'No usage in the selected range',
  )
  const days = Object.keys(usage.events.per_day)
  const actions = [...new Set(days.flatMap((d) => Object.keys(usage.events.per_day[d])))]
  chart('chart-events', {
    xAxis: { type: 'category', data: days.map((d) => d.slice(5)) }, yAxis: valueAxis(),
    series: actions.map((a, i) => bars(EVENT_LABELS[a] || a, days.map((d) => usage.events.per_day[d][a] || 0), COLORS[i], 'ev')),
  })
  $('#recent-events').innerHTML = table(
    [{ label: 'Time' }, { label: 'Member' }, { label: 'Event' }, { label: 'Detail' }],
    usage.events.recent.slice(0, 10).map((e) => [td(fmtTime(e.ts * 1000), 'small muted'), td(esc(e.label)), td(EVENT_LABELS[e.action] || esc(e.action)), td(esc(e.kind || e.page || ''), 'small muted')]),
    'No events',
  )
}

// ---------- business ----------
async function loadBusiness() {
  const data = await api(`/api/business?env=${state.env}&days=${state.businessDays}`)
  if (data.error) { $('#projects-table').innerHTML = errorBox(data); return }
  const labels = data.days.map((d) => d.slice(5))
  chart('chart-created', {
    xAxis: { type: 'category', data: labels }, yAxis: valueAxis(),
    series: [bars('Created', data.created, '#0a84ff'), bars('Resolved', data.resolved, '#30d158')],
  })
  chart('chart-activity', {
    xAxis: { type: 'category', data: labels }, yAxis: valueAxis(),
    series: [area('Changes', data.activity, '#bf5af2'), area('Comments', data.comments, '#ff9f0a')],
  })
  chart('chart-actions', {
    grid: { left: 90, right: 24, top: 10, bottom: 24 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: valueAxis(), yAxis: { type: 'category', inverse: true, data: data.by_action.map((a) => ACTION_LABELS[a.action] || a.action) },
    series: [bars('Count', data.by_action.map((a) => a.n), '#5e5ce6')],
  })
  $('#projects-table').innerHTML = table(
    [{ label: 'Project' }, { label: 'Total', num: true }, { label: 'To do', num: true }, { label: 'In progress', num: true }, { label: 'Done', num: true }, { label: 'Overdue', num: true }, { label: 'Done %' }, { label: 'Last update' }],
    data.projects.map((p) => [
      td(`<b>${esc(p.key)}</b> ${esc(p.name)}`), td(p.total, 'num'), td(p.todo, 'num'), td(p.doing, 'num'), td(p.done, 'num'),
      td(p.overdue ? `<span class="pill bad">${p.overdue}</span>` : 0, 'num'),
      barTd(p.done, p.total, p.total ? pct(p.done / p.total, 0) : '—'), td(fmtTime(p.last_update), 'small muted'),
    ]),
  )
  const maxA = Math.max(1, ...data.actors.map((a) => a.n))
  $('#actors').innerHTML = table(
    [{ label: 'Member' }, { label: 'Actions' }],
    data.actors.map((a, i) => [td(who(a.name, i, ` <span class="faint small">${esc(a.username)}</span>`)), barTd(a.n, maxA)]),
  )
  const rd = data.requests_by_dept || []
  const maxR = Math.max(1, ...rd.map((d) => d.total))
  $('#requests-dept').innerHTML = table(
    [{ label: 'Department' }, { label: 'Issues' }, { label: 'Open', num: true }, { label: `New (${state.businessDays} d)`, num: true }, { label: 'Reporters', num: true }],
    rd.map((d) => [td(`<b>${esc(d.dept)}</b>`), barTd(d.total, maxR, num(d.total)), td(d.open, 'num'), td(d.recent, 'num'), td(d.people, 'num')]),
    'No issues',
  )
  const hc = data.headcount || []
  chart('chart-headcount', {
    grid: { left: 130, right: 30, top: 10, bottom: 24 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: valueAxis(), yAxis: { type: 'category', inverse: true, data: hc.map((d) => d.dept) },
    series: [bars('Active AD accounts', hc.map((d) => d.n), '#0a84ff')],
  })
  $('#sync-runs').innerHTML = table(
    [{ label: 'Started' }, { label: 'Trigger' }, { label: 'Result' }, { label: 'Duration' }, { label: 'Notes' }],
    data.sync_runs.map((r) => {
      const secs = r.finished_at ? (new Date(r.finished_at) - new Date(r.started_at)) / 1000 : null
      const ok = r.status === 'success'
      const stats = r.stats ? Object.entries(r.stats).filter(([, v]) => v).map(([k, v]) => `${k} ${v}`).join(' · ') : ''
      return [td(fmtTime(r.started_at), 'small'), td(esc(r.trigger)), td(`<span class="pill ${ok ? 'ok' : r.status === 'running' ? '' : 'bad'}">${esc(r.status)}</span>`), td(secs != null ? duration(secs) : '—', 'small'), td(esc(r.error || stats), 'small muted')]
    }),
    'AD sync has never run (set LDAP_APP_PASSWORD in configs/ldap.env)',
  )
  $('#imports').innerHTML = table(
    [{ label: 'Time' }, { label: 'File' }, { label: 'Rows', num: true }, { label: 'By' }],
    data.imports.map((b) => [td(fmtTime(b.created_at), 'small'), td(`${esc(b.filename)} ${b.undone ? '<span class="pill">Undone</span>' : ''}`), td(b.count, 'num'), td(esc(b.user || '—'))]),
    'No imports',
  )
}

// ---------- AI ----------
async function loadAi() {
  const data = await api(`/api/ai?env=${state.env}&days=${state.aiDays}`)
  if (data.error) { $('#ai-kpis').innerHTML = errorBox(data); return }
  const total = data.kinds.reduce((s, k) => s + k.n, 0)
  const okWeighted = total ? data.kinds.reduce((s, k) => s + k.ok_rate * k.n, 0) / total : null
  const confirmed = data.actions.filter((a) => a.status === 'done').reduce((s, a) => s + a.n, 0)
  const decided = data.actions.filter((a) => a.status !== 'pending').reduce((s, a) => s + a.n, 0)
  const llm = data.llm || {}
  $('#ai-kpis').innerHTML = [
    kpi('Calls', num(total), `${state.aiDays} days`),
    kpi('Success rate', pct(okWeighted), '', okWeighted != null && okWeighted < 0.95 ? 'bad' : ''),
    kpi('Write acceptance', decided ? pct(confirmed / decided) : '—', `${decided} decided`),
    kpi('LLM uptime (24h)', pct(llm.uptime_24h?.ratio), `7-day ${pct(llm.uptime_7d?.ratio)}`),
    kpi('Avg API latency', llm.uptime_24h?.avg_ms != null ? `${llm.uptime_24h.avg_ms} ms` : '—', '/v1/models probe'),
  ].join('')
  const labels = data.days.map((d) => d.slice(5))
  chart('chart-ai-calls', {
    xAxis: { type: 'category', data: labels }, yAxis: valueAxis(),
    series: Object.entries(data.series).map(([kind, values], i) => bars(AI_KIND_LABELS[kind] || kind, values, COLORS[i], 'ai')),
  })
  chart('chart-llm', {
    xAxis: timeAxis, yAxis: valueAxis({ name: 'ms' }),
    series: [line('Latency', llm.series || [], '#30d158')],
  })
  $('#ai-kinds').innerHTML = table(
    [{ label: 'Feature' }, { label: 'Calls', num: true }, { label: 'Success', num: true }, { label: 'Avg', num: true }, { label: 'P95', num: true }],
    data.kinds.map((k) => [td(AI_KIND_LABELS[k.kind] || esc(k.kind)), td(k.n, 'num'), td(pct(k.ok_rate), 'num'), td(`${num(k.avg_ms)} ms`, 'num'), td(`${num(k.p95_ms)} ms`, 'num')]),
  )
  $('#ai-actions').innerHTML = table(
    [{ label: 'Tool' }, { label: 'Status' }, { label: 'Count', num: true }],
    data.actions.map((a) => [td(`<span class="mono">${esc(a.tool)}</span>`), td(`<span class="pill ${a.status === 'done' ? 'ok' : a.status === 'rejected' || a.status === 'failed' ? 'bad' : ''}">${ACTION_STATUS[a.status] || esc(a.status)}</span>`), td(a.n, 'num')]),
  )
  const maxU = Math.max(1, ...data.users.map((u) => u.n))
  $('#ai-users').innerHTML = table([{ label: 'Member' }, { label: 'Calls' }], data.users.map((u, i) => [td(who(u.name, i)), barTd(u.n, maxU)]))
  $('#ai-failures').innerHTML = data.failures.length
    ? data.failures.map((f) => `<div class="log-item"><div class="small muted">${fmtTime(f.created_at)} · ${AI_KIND_LABELS[f.kind] || esc(f.kind)} · ${esc(f.user || '')} · ${num(f.latency_ms)} ms</div><pre class="log mono">${esc(f.result)}</pre></div>`).join('')
    : '<div class="empty">No failures</div>'
}

// ---------- logs ----------
async function loadLogs() {
  const data = await api(`/api/logs?env=${state.env}&hours=${state.logsHours}`)
  const st = data.status || {}
  $('#logs-kpis').innerHTML = [
    kpi('Requests', num(data.requests), `${state.logsHours} h · ${envLabel(state.env)}`),
    kpi('Client IPs', num(data.clients)),
    kpi('4xx', num(st['4xx'] || 0), 'Mostly signed-out or validation errors'),
    kpi('5xx', num(st['5xx'] || 0), `Error rate ${pct(data.error_rate, 2)}`, st['5xx'] ? 'bad' : ''),
    kpi('Error logs', num(data.errors.length), `WARNING ${num(data.warnings)}`, data.errors.length ? 'warn' : ''),
    kpi('Service starts', num(data.restarts.length), data.restarts.length ? `Last ${fmtTime(data.restarts[data.restarts.length - 1])}` : 'No restarts in range'),
  ].join('')
  requestsChart('chart-requests', data)
  const maxE = Math.max(1, ...data.top_endpoints.map((e) => e[1]))
  $('#top-endpoints').innerHTML = table([{ label: 'Endpoint' }, { label: 'Count' }], data.top_endpoints.map(([path, n]) => [td(`<span class="mono">${esc(path)}</span>`), barTd(n, maxE)]))
  $('#error-endpoints').innerHTML = table([{ label: 'Endpoint' }, { label: '5xx count', num: true }], data.error_endpoints.map(([path, n]) => [td(`<span class="mono">${esc(path)}</span>`), td(n, 'num')]), 'No 5xx errors 🎉')
  $('#restarts').textContent = data.restarts.length ? `Started ${data.restarts.length}× in range` : ''
  $('#error-lines').innerHTML = data.errors.length
    ? data.errors.map((e) => `<div class="log-item"><div class="small muted">${fmtTime(e.ts * 1000)}</div><pre class="log mono">${esc(e.text)}</pre></div>`).join('')
    : '<div class="empty">No error logs in range</div>'
}

// ---------- system ----------
function meter(used, total) {
  const r = total ? used / total : 0
  return `<div class="meter ${r > 0.9 ? 'bad' : r > 0.75 ? 'warn' : ''}"><div style="width:${(r * 100).toFixed(1)}%"></div></div>`
}

async function loadSystem() {
  const data = await api('/api/system')
  const h = data.host
  const memUsed = h.mem_total - h.mem_available
  $('#host').innerHTML = [
    `<div class="stat-row"><div class="line"><span>CPU load (1 / 5 / 15 min)</span><b>${h.load.join(' / ')}</b></div><div class="small muted">${h.cpu_count} cores · host up ${duration(h.uptime_seconds)}</div>${meter(h.load[0], h.cpu_count)}</div>`,
    `<div class="stat-row"><div class="line"><span>Memory</span><b>${bytes(memUsed)} / ${bytes(h.mem_total)}</b></div>${meter(memUsed, h.mem_total)}</div>`,
    ...h.disks.map((d) => `<div class="stat-row"><div class="line"><span>${esc(d.label)}</span><b>${bytes(d.used)} / ${bytes(d.total)}</b></div><div class="small muted">${bytes(d.free)} free</div>${meter(d.used, d.total)}</div>`),
  ].join('')
  $('#storage').innerHTML = h.storage.map((s) => `<div class="stat-row"><div class="line"><span>${esc(s.label)}</span><b>${bytes(s.bytes)}</b></div><div class="small faint mono">${esc(s.path)}</div></div>`).join('')
  const l = data.ldap || {}
  const row = (k, v) => `<div class="stat-row"><div class="line"><span>${k}</span><b>${v}</b></div></div>`
  $('#ldap').innerHTML = [
    row('Status', l.enabled ? `<span class="pill ok">Enabled</span>` : `<span class="pill bad">Not configured</span>`),
    row('Server', `<span class="mono">${esc(l.server || '—')}</span>`),
    row('Search base', `<span class="mono">${esc(l.search_base || '—')}</span>`),
    row('Service account', `${esc(l.service_account || '—')} ${l.service_password ? '<span class="pill ok">Password set</span>' : '<span class="pill bad">Password missing</span>'}`),
    `<div class="stat-row"><div class="small muted">Config sources (later overrides earlier)</div>${(l.sources || []).map((s) => `<div class="small faint mono">${esc(s)}</div>`).join('') || '<div class="small faint">None</div>'}</div>`,
  ].join('')
  const names = { 'web:production': 'Production web', 'web:staging': 'Staging web', postgres: 'PostgreSQL', llm: 'LLM 65:8001' }
  chart('chart-probes', {
    xAxis: timeAxis, yAxis: valueAxis({ name: 'ms' }),
    series: Object.entries(data.probes).map(([k, v], i) => line(names[k] || k, v, COLORS[i])),
  })
  $('#db-tables').innerHTML = Object.entries(data.databases).map(([key, db]) => {
    if (db.error) return `<div class="card"><div class="card-head"><h3>${envLabel(key)} database</h3></div>${errorBox(db)}</div>`
    const max = Math.max(1, ...db.tables.map((t) => t.bytes))
    return `<div class="card"><div class="card-head"><h3>${envLabel(key)} database</h3><span class="muted small">Migration ${esc(db.migration || '—')}</span></div>${table(
      [{ label: 'Table' }, { label: 'Size' }, { label: 'Rows (est.)', num: true }],
      db.tables.map((t) => [td(`<span class="mono">${esc(t.name)}</span>`), barTd(t.bytes, max, bytes(t.bytes)), td(num(t.rows), 'num')]),
    )}</div>`
  }).join('')
}

// ---------- shell ----------
const loaders = { overview: loadOverview, usage: loadUsage, business: loadBusiness, ai: loadAi, logs: loadLogs, system: loadSystem }
let inflight = false

async function refresh() {
  if (inflight || document.hidden) return
  inflight = true
  const dot = $('#refresh-state')
  dot.className = 'refresh loading'
  try {
    await loaders[state.tab]()
    dot.className = 'refresh'
    dot.title = `Last refreshed ${new Date().toLocaleTimeString('en-GB', { hour12: false })}`
  } catch (err) {
    console.error(err)
    dot.className = 'refresh error'
    dot.title = `Refresh failed: ${err.message}`
  } finally {
    inflight = false
  }
}

function activate(tab) {
  state.tab = loaders[tab] ? tab : 'overview'
  localStorage.setItem('ops-tab', state.tab)
  document.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === state.tab))
  document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('active', p.dataset.panel === state.tab))
  $('#env-switch').style.visibility = ['overview', 'system'].includes(state.tab) ? 'hidden' : 'visible'
  refresh().then(() => Object.values(charts).forEach((c) => c.resize()))
}

function segmented(id, key, attr) {
  const root = document.getElementById(id)
  root.addEventListener('click', (e) => {
    const btn = e.target.closest('button')
    if (!btn) return
    root.querySelectorAll('button').forEach((b) => b.classList.toggle('active', b === btn))
    state[key] = attr === 'env' ? btn.dataset.env : Number(btn.dataset[attr])
    if (attr === 'env') localStorage.setItem('ops-env', state.env)
    refresh()
  })
}

document.getElementById('tabs').addEventListener('click', (e) => {
  const btn = e.target.closest('button')
  if (btn) activate(btn.dataset.tab)
})
document.querySelectorAll('#env-switch button').forEach((b) => b.classList.toggle('active', b.dataset.env === state.env))
segmented('env-switch', 'env', 'env')
segmented('usage-days', 'usageDays', 'days')
segmented('overview-hours', 'overviewHours', 'hours')
segmented('presence-hours', 'presenceHours', 'hours')
segmented('business-days', 'businessDays', 'days')
segmented('ai-days', 'aiDays', 'days')
segmented('logs-hours', 'logsHours', 'hours')
$('#theme-toggle').addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'
  document.documentElement.dataset.theme = next
  localStorage.setItem('ops-theme', next)
  Object.values(charts).forEach((c) => c.dispose())
  refresh()
})
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh() })
setInterval(refresh, REFRESH_MS)
activate(state.tab)
