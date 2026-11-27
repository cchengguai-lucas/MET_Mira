import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api } from '../../api'
import { Empty, Modal, Spinner, Select } from '../../components/ui'
import { useApp } from '../../context'
import { t, tServer } from '../../i18n'
import type { DepartmentTemplate, OrgUnit } from '../../types'
import { TemplateEditor } from './TemplateEditor'
import { KEY_RE } from './templateModel'

export function TemplatesTab() {
  const { toast } = useApp()
  const client = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['admin-templates'],
    queryFn: () => api.get<{ templates: DepartmentTemplate[] }>('/api/admin/templates'),
  })
  const [editing, setEditing] = useState<number | null>(null)
  const [creating, setCreating] = useState<DepartmentTemplate | 'new' | null>(null)
  const refresh = () => client.invalidateQueries({ queryKey: ['admin-templates'] })
  if (isLoading || !data) return <Spinner center />

  const toggle = async (row: DepartmentTemplate) => {
    const message = row.enabled
      ? t('停用「{name}」？停用后新建项目不能再选择它，也不会被推荐；已在使用的 {n} 个项目不受影响。', { name: row.label, n: row.project_count ?? 0 })
      : t('恢复「{name}」？恢复后新建项目可以再次选择它。', { name: row.label })
    if (!window.confirm(message)) return
    try {
      await api.put(`/api/admin/templates/${row.id}`, { enabled: !row.enabled })
      await refresh()
    } catch (error) { toast((error as Error).message, 'error') }
  }

  return (
    <div className="col" style={{ gap: 14 }}>
      <div className="card card-pad col" style={{ gap: 10 }}>
        <div className="row wrap">
          <div>
            <h3>{t('部门模板')}</h3>
            <p className="muted small">{t('模板决定项目的角色、表单分区与字段、状态、各类型工作流、默认门禁和列表列。')}</p>
          </div>
          <span className="spacer" />
          <button className="btn primary" onClick={() => setCreating('new')}>＋ {t('新建模板')}</button>
        </div>
        <ol className="tpl-lifecycle small">
          <li><b>{t('编辑草稿')}</b> {t('自动保存，不影响项目')}</li>
          <li><b>{t('发布版本')}</b> {t('新建项目使用最新发布版本')}</li>
          <li><b>{t('迁移项目')}</b> {t('已有项目在项目设置中手动更换')}</li>
        </ol>
      </div>
      {!data.templates.length ? <Empty title={t('暂无模板')} /> : (
        <div className="grid grid-3" style={{ gap: 12 }}>
          {data.templates.map((row) => (
            <div className={`card card-pad col tpl-card ${row.enabled ? '' : 'disabled'}`} key={row.id} style={{ gap: 8 }}>
              <div className="row" style={{ gap: 6 }}>
                <b style={{ fontSize: 15 }}>{tServer(row.label)}</b>
                {row.is_system && <span className="badge">{t('内置')}</span>}
                <span className="spacer" />
                <span className={`badge ${row.enabled ? 'success' : ''}`}>{row.enabled ? t('启用中') : t('已停用')}</span>
              </div>
              <span className="small muted">{tServer(row.description) || t('暂无说明')}</span>
              <div className="tpl-card-meta small">
                <span>{t('当前版本')}<b>{row.current_version ? `v${row.current_version.version}` : t('未发布')}</b></span>
                <span>{t('使用项目')}<b>{row.project_count ?? 0}</b></span>
                <span>{t('模板 Key')}<code>{row.key}</code></span>
              </div>
              {row.draft_version && (
                <span className="tiny warn-text">{t('有未发布的 v{n} 草稿', { n: row.draft_version })}</span>
              )}
              <div className="row wrap" style={{ gap: 6 }}>
                <button className="btn sm primary" onClick={() => setEditing(row.id)}>
                  {row.draft_version ? t('继续编辑草稿') : t('编辑')}
                </button>
                <button className="btn sm" disabled={!row.current_version} title={row.current_version ? '' : t('发布后才能复制')}
                  onClick={() => setCreating(row)}>{t('复制为新模板')}</button>
                <button className="btn sm ghost" onClick={() => toggle(row)}>{row.enabled ? t('停用') : t('恢复')}</button>
              </div>
            </div>
          ))}
        </div>
      )}
      <RecommendationEditor templates={data.templates.filter((row) => row.enabled && row.current_version)} />
      {creating && (
        <CreateTemplateModal
          templates={data.templates.filter((row) => row.current_version)}
          source={creating === 'new' ? null : creating}
          onClose={() => setCreating(null)}
          onCreated={async (id) => {
            setCreating(null)
            await refresh()
            setEditing(id)
          }}
        />
      )}
      {editing && <TemplateEditor templateId={editing} onClose={() => {
        setEditing(null)
        refresh()
      }} />}
    </div>
  )
}

function CreateTemplateModal({ templates, source, onClose, onCreated }: {
  templates: DepartmentTemplate[]
  source: DepartmentTemplate | null
  onClose: () => void
  onCreated: (id: number) => void
}) {
  const { toast } = useApp()
  const [label, setLabel] = useState(source ? t('{name}（副本）', { name: source.label }) : '')
  const [key, setKey] = useState(source ? `${source.key}_copy` : '')
  const [description, setDescription] = useState('')
  const fallback = templates.find((row) => row.key === 'electrical') ?? templates[0]
  const [sourceId, setSourceId] = useState<number | null>((source ?? fallback)?.id ?? null)
  const [busy, setBusy] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const keyFormatError = key && !KEY_RE.test(key) ? t('只能用小写字母、数字和下划线，且以字母开头') : ''
  const labelError = submitted && !label.trim() ? t('请填写模板名称') : ''
  const keyError = keyFormatError || (submitted && !key.trim() ? t('请填写模板 Key') : '')
  const canSubmit = !!label.trim() && !!key.trim() && !keyFormatError
  const base = templates.find((row) => row.id === sourceId)
  const submit = async () => {
    setSubmitted(true)
    if (!canSubmit) {
      toast(
        (!label.trim() ? t('请填写模板名称') : '')
          || (!key.trim() ? t('请填写模板 Key') : '')
          || keyFormatError
          || t('请检查标红的输入项'),
        'error',
      )
      return
    }
    setBusy(true)
    try {
      const row = await api.post<DepartmentTemplate>('/api/admin/templates', {
        key, label, description, source_version_id: base?.current_version_id,
      })
      onCreated(row.id)
    } catch (error) {
      toast((error as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title={source ? t('复制模板') : t('新建模板')} onClose={onClose} footer={(
      <>
        <button className="btn" onClick={onClose}>{t('取消')}</button>
        <button className="btn primary" disabled={busy} onClick={submit}>{t('创建并开始编辑')}</button>
      </>
    )}>
      <div className="col" style={{ gap: 12 }}>
        <label className={`field${labelError ? ' is-invalid' : ''}`}>{t('模板名称')}
          <input
            className={`input${labelError ? ' is-invalid' : ''}`}
            autoFocus
            value={label}
            aria-invalid={!!labelError}
            placeholder={t('例如：机械设计')}
            onChange={(e) => setLabel(e.target.value)}
          />
          {labelError ? <span className="field-error" role="alert">{labelError}</span> : null}
        </label>
        <label className={`field${keyError ? ' is-invalid' : ''}`}>{t('模板 Key')}
          <input
            className={`input mono${keyError ? ' is-invalid' : ''}`}
            value={key}
            placeholder="mechanical_design"
            aria-invalid={!!keyError}
            onChange={(e) => setKey(e.target.value.toLowerCase())}
          />
          {keyError
            ? <span className="field-error" role="alert">{keyError}</span>
            : <span className="tiny faint">{t('系统内部标识，创建后不可修改。')}</span>}
        </label>
        <label className="field">{t('以哪个模板为起点')}
          <Select className="select" value={sourceId ?? ''} onChange={(e) => setSourceId(Number(e.target.value) || null)}>
            {templates.map((row) => <option key={row.id} value={row.id}>{t('{name} · v{n}', { name: row.label, n: row.current_version?.version ?? 0 })}</option>)}
          </Select>
          <span className="tiny faint">{t('复制它当前发布版本的角色、表单、状态和流程，之后可以任意修改。')}</span>
        </label>
        <label className="field">{t('模板说明')}
          <textarea className="textarea" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
      </div>
    </Modal>
  )
}

function RecommendationEditor({ templates }: { templates: DepartmentTemplate[] }) {
  const { toast } = useApp()
  const units = useQuery({
    queryKey: ['org-tree-flat'],
    queryFn: () => api.get<{ units: OrgUnit[] }>('/api/org/tree'),
  })
  const [unitId, setUnitId] = useState<number | null>(null)
  const recs = useQuery({
    queryKey: ['template-recommendations', unitId],
    enabled: unitId !== null,
    queryFn: () => api.get<{ templates: (DepartmentTemplate & { direct: boolean })[] }>(
      `/api/admin/templates/recommendations/${unitId}`,
    ),
  })
  const [selected, setSelected] = useState<number[]>([])
  useEffect(() => {
    setSelected(recs.data?.templates.filter((row) => row.direct).map((row) => row.id) ?? [])
  }, [recs.data])
  const inherited = recs.data?.templates.filter((row) => !row.direct) ?? []
  return <div className="card card-pad col" style={{ gap: 10 }}>
    <h3>{t('部门模板推荐')}</h3>
    <p className="small muted">{t('新建项目时，按项目负责人所在部门（逐级向上）预选推荐的模板。只影响默认选择，不会修改现有项目。')}</p>
    <Select className="select" value={unitId ?? ''} onChange={(e) => setUnitId(e.target.value ? Number(e.target.value) : null)}>
      <option value="">{t('选择部门')}</option>
      {(units.data?.units ?? []).map((unit) => <option key={unit.id} value={unit.id}>{unit.path || unit.name}</option>)}
    </Select>
    {unitId !== null && <>
      <div className="row wrap">{templates.map((row) => <label className="tag" key={row.id}>
        <input type="checkbox" checked={selected.includes(row.id)} onChange={(e) => setSelected((current) => e.target.checked ? [...current, row.id] : current.filter((id) => id !== row.id))} />
        {row.label}
      </label>)}</div>
      {inherited.length > 0 && <span className="tiny faint">{t('从上级部门继承：{names}', { names: inherited.map((row) => row.label).join('、') })}</span>}
      <div>
        <button className="btn primary" onClick={async () => {
          try {
            await api.put(`/api/admin/templates/recommendations/${unitId}`, { template_ids: selected })
            toast(t('部门推荐已保存'), 'success')
            recs.refetch()
          } catch (error) { toast((error as Error).message, 'error') }
        }}>{t('保存推荐')}</button>
      </div>
    </>}
  </div>
}
