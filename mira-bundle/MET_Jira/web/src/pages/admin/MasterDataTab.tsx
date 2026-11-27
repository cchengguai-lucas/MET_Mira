import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api, qs } from '../../api'
import { Modal, Segmented, Spinner } from '../../components/ui'
import { useApp } from '../../context'
import { t, tServer } from '../../i18n'
import { MASTER_KINDS, masterKindExample, masterKindLabel, type MasterKind } from '../../masterData'

interface Asset {
  id: number
  kind: MasterKind
  code: string
  name: string
  active: boolean
  attributes: Record<string, unknown>
}

interface Overview {
  kinds: Record<MasterKind, {
    active: number
    inactive: number
    fields: { template_id: number; template: string; field: string; label: string }[]
  }>
  usage: Record<string, { issues: number; projects: number }>
}

/** Company-wide option lists that template "master data" fields pick from. */
export function MasterDataTab({ onOpenTemplates }: { onOpenTemplates: () => void }) {
  const { toast } = useApp()
  const client = useQueryClient()
  const [kind, setKind] = useState<MasterKind>('material')
  const [query, setQuery] = useState('')
  const [showInactive, setShowInactive] = useState(false)
  const [editing, setEditing] = useState<Asset | 'new' | null>(null)
  const [bulk, setBulk] = useState(false)
  const overview = useQuery({
    queryKey: ['assets-overview'],
    queryFn: () => api.get<Overview>('/api/assets/overview'),
  })
  const assets = useQuery({
    queryKey: ['assets', kind],
    queryFn: () => api.get<{ assets: Asset[] }>(`/api/assets${qs({ kind, include_inactive: true })}`),
  })
  const refresh = () => client.invalidateQueries({
    predicate: (q) => ['assets', 'assets-overview', 'master-assets'].includes(String(q.queryKey[0])),
  })
  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return (assets.data?.assets ?? []).filter((asset) =>
      (showInactive || asset.active)
      && (!needle || asset.code.toLowerCase().includes(needle) || asset.name.toLowerCase().includes(needle)))
  }, [assets.data, query, showInactive])

  const info = overview.data?.kinds[kind]
  const inactiveCount = info?.inactive ?? 0
  const example = masterKindExample(kind)

  const toggle = async (asset: Asset) => {
    const used = overview.data?.usage[asset.id]
    if (asset.active && !window.confirm(
      used?.issues
        ? t('「{name}」已被 {n} 个问题使用。停用后新建问题不能再选择它，已填写的问题仍正常显示。确定停用？', { name: asset.name, n: used.issues })
        : t('停用后新建问题不能再选择「{name}」。确定停用？', { name: asset.name }),
    )) return
    try {
      await api.put(`/api/assets/${asset.id}`, { ...asset, active: !asset.active })
      await refresh()
      toast(asset.active ? t('已停用') : t('已启用'), 'success')
    } catch (error) {
      toast((error as Error).message, 'error')
    }
  }

  return (
    <div className="col" style={{ gap: 14 }}>
      <div className="card card-pad col md-intro">
        <div>
          <h3>{t('主数据')}</h3>
          <p className="muted small">
            {t('全公司共用的标准选项清单（物料、产线、设备、服务）。在这里统一维护后，各项目填写时从同一份清单中选择，名称和编码保持一致，报表也能按条目准确统计。')}
          </p>
        </div>
        <ol className="md-steps">
          <li><b>{t('维护清单')}</b><span>{t('在下方按类别添加条目')}</span></li>
          <li><b>{t('模板引用')}</b><span>{t('部门模板中添加「主数据」类型的字段，并选择类别')}</span></li>
          <li><b>{t('建单选择')}</b><span>{t('创建问题时从下拉框中选择，报表按条目汇总')}</span></li>
        </ol>
      </div>

      <div className="card">
        <div className="card-pad col" style={{ gap: 12 }}>
          <div className="row wrap" style={{ gap: 10 }}>
            <Segmented value={kind} onChange={(value) => { setKind(value); setQuery('') }}
              items={MASTER_KINDS.map((value) => ({
                value,
                label: <>{masterKindLabel(value)} <span className="md-count">{overview.data?.kinds[value].active ?? 0}</span></>,
              }))} />
            <span className="spacer" />
            <button className="btn" onClick={() => setBulk(true)}>{t('批量导入')}</button>
            <button className="btn primary" onClick={() => setEditing('new')}>{t('＋ 新增{kind}', { kind: masterKindLabel(kind) })}</button>
          </div>
          <div className="md-usage small">
            <span className="muted">{t('使用位置：')}</span>
            {info?.fields.length ? info.fields.map((field) => (
              <span key={`${field.template_id}-${field.field}`} className="tag">{tServer(field.template)} · {tServer(field.label)}</span>
            )) : (
              <>
                <span className="muted">{t('还没有部门模板使用「{kind}」，建单时不会出现这些选项。', { kind: masterKindLabel(kind) })}</span>
                <button className="btn ghost sm" onClick={onOpenTemplates}>{t('去部门模板添加字段 →')}</button>
              </>
            )}
          </div>
          <div className="row wrap" style={{ gap: 10 }}>
            <input className="input" style={{ maxWidth: 280 }} placeholder={t('搜索编码或名称')}
              value={query} onChange={(e) => setQuery(e.target.value)} />
            {inactiveCount > 0 && (
              <label className="row small" style={{ gap: 6 }}>
                <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
                {t('显示已停用（{n}）', { n: inactiveCount })}
              </label>
            )}
          </div>
        </div>
        {assets.isLoading ? <Spinner center /> : rows.length ? (
          <table className="table md-table">
            <thead>
              <tr>
                <th>{t('编码')}</th>
                <th>{t('名称')}</th>
                <th>{t('使用情况')}</th>
                <th>{t('状态')}</th>
                <th aria-label={t('操作')} />
              </tr>
            </thead>
            <tbody>
              {rows.map((asset) => {
                const used = overview.data?.usage[asset.id]
                return (
                  <tr key={asset.id} className={asset.active ? '' : 'md-inactive'}>
                    <td className="mono">{asset.code}</td>
                    <td>{asset.name}</td>
                    <td className="small muted">
                      {used?.issues || used?.projects
                        ? [
                            used.issues ? t('{n} 个问题', { n: used.issues }) : null,
                            used.projects ? t('{n} 个项目绑定', { n: used.projects }) : null,
                          ].filter(Boolean).join(' · ')
                        : t('未使用')}
                    </td>
                    <td>
                      <span className={`md-state ${asset.active ? 'on' : ''}`}>{asset.active ? t('启用') : t('已停用')}</span>
                    </td>
                    <td className="md-actions">
                      <button className="btn ghost sm" onClick={() => setEditing(asset)}>{t('编辑')}</button>
                      <button className="btn ghost sm" onClick={() => toggle(asset)}>{asset.active ? t('停用') : t('启用')}</button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        ) : (
          <div className="empty">
            {query.trim() ? (
              <div className="bold" style={{ color: 'var(--text)' }}>{t('没有匹配的条目')}</div>
            ) : (
              <>
                <div className="bold" style={{ color: 'var(--text)' }}>{t('还没有{kind}条目', { kind: masterKindLabel(kind) })}</div>
                <div style={{ marginTop: 6 }}>{t('例如：编码 {code}，名称 {name}', example)}</div>
                <div className="row" style={{ justifyContent: 'center', marginTop: 12, gap: 8 }}>
                  <button className="btn" onClick={() => setBulk(true)}>{t('批量导入')}</button>
                  <button className="btn primary" onClick={() => setEditing('new')}>{t('＋ 新增{kind}', { kind: masterKindLabel(kind) })}</button>
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {editing && <AssetModal kind={kind} asset={editing === 'new' ? null : editing}
        onClose={() => setEditing(null)} onSaved={refresh} />}
      {bulk && <BulkModal kind={kind} onClose={() => setBulk(false)} onSaved={refresh} />}
    </div>
  )
}

function AssetModal({ kind, asset, onClose, onSaved }: {
  kind: MasterKind
  asset: Asset | null
  onClose: () => void
  onSaved: () => Promise<unknown>
}) {
  const { toast } = useApp()
  const [code, setCode] = useState(asset?.code ?? '')
  const [name, setName] = useState(asset?.name ?? '')
  const [busy, setBusy] = useState(false)
  const example = masterKindExample(kind)
  const label = masterKindLabel(kind)
  const submit = async () => {
    setBusy(true)
    try {
      const body = { kind, code: code.trim(), name: name.trim(), active: asset?.active ?? true, attributes: asset?.attributes ?? {} }
      if (asset) await api.put(`/api/assets/${asset.id}`, body)
      else await api.post('/api/assets', body)
      await onSaved()
      toast(asset ? t('已保存') : t('已添加'), 'success')
      onClose()
    } catch (error) {
      toast((error as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title={asset ? t('编辑{kind}', { kind: label }) : t('新增{kind}', { kind: label })} size="narrow" onClose={onClose}
      footer={<>
        <button className="btn" onClick={onClose}>{t('取消')}</button>
        <button className="btn primary" disabled={busy || !code.trim() || !name.trim()} onClick={submit}>{asset ? t('保存') : t('添加')}</button>
      </>}>
      <form className="col" style={{ gap: 14 }} onSubmit={(e) => { e.preventDefault(); if (code.trim() && name.trim()) submit() }}>
        <div className="field">
          <label>{t('编码 *')}</label>
          <input className="input mono" autoFocus value={code} placeholder={example.code} onChange={(e) => setCode(e.target.value)} />
          <span className="tiny faint">{t('同类别内唯一，建议使用公司内部的标准编号')}</span>
        </div>
        <div className="field">
          <label>{t('名称 *')}</label>
          <input className="input" value={name} placeholder={example.name} onChange={(e) => setName(e.target.value)} />
          <span className="tiny faint">{t('建单下拉框中显示为「编码 · 名称」')}</span>
        </div>
        {asset && <span className="tiny faint">{t('修改编码或名称后，已引用它的问题会自动显示新内容。')}</span>}
        <button type="submit" hidden />
      </form>
    </Modal>
  )
}

const parseLines = (text: string) => text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
  const [code, ...rest] = line.split(/\t|,|，/)
  return { code: code.trim(), name: rest.join(' ').trim() }
})

function BulkModal({ kind, onClose, onSaved }: {
  kind: MasterKind
  onClose: () => void
  onSaved: () => Promise<unknown>
}) {
  const { toast } = useApp()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const items = useMemo(() => parseLines(text), [text])
  const valid = items.filter((item) => item.code && item.name)
  const invalid = items.length - valid.length
  const example = masterKindExample(kind)
  const label = masterKindLabel(kind)
  const submit = async () => {
    setBusy(true)
    try {
      const res = await api.post<{ created: number; skipped: string[] }>('/api/assets/bulk', { kind, items: valid })
      await onSaved()
      toast(res.skipped.length
        ? t('已添加 {n} 条，跳过 {m} 条（编码已存在）', { n: res.created, m: res.skipped.length })
        : t('已添加 {n} 条', { n: res.created }), 'success')
      onClose()
    } catch (error) {
      toast((error as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title={t('批量导入{kind}', { kind: label })} onClose={onClose}
      footer={<>
        <span className="small muted" style={{ marginRight: 'auto' }}>
          {t('识别到 {n} 条', { n: valid.length })}{invalid ? t('，{m} 行缺少名称将被忽略', { m: invalid }) : ''}
        </span>
        <button className="btn" onClick={onClose}>{t('取消')}</button>
        <button className="btn primary" disabled={busy || !valid.length} onClick={submit}>{t('添加 {n} 条', { n: valid.length })}</button>
      </>}>
      <div className="col" style={{ gap: 10 }}>
        <p className="small muted">{t('每行一条，先写编码再写名称，用逗号或 Tab 分隔。可以直接从 Excel 复制两列粘贴进来；编码已存在的行会自动跳过。')}</p>
        <textarea className="textarea mono" style={{ minHeight: 220 }} autoFocus value={text}
          placeholder={`${example.code}, ${example.name}`} onChange={(e) => setText(e.target.value)} />
      </div>
    </Modal>
  )
}
