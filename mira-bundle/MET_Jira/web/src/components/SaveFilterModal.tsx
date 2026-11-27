import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { useApp } from '../context'
import { t } from '../i18n'
import { useProjects } from '../queries'
import type { FilterScope, SavedFilter } from '../types'
import { Modal, Segmented, Select } from './ui'

/** Create a filter from ``jql``, or edit ``filter`` (name, description, JQL and sharing). */
export function SaveFilterModal({ jql, filter, onClose }: { jql?: string; filter?: SavedFilter; onClose: () => void }) {
  const { user, toast } = useApp()
  const client = useQueryClient()
  const projects = useProjects()
  const [name, setName] = useState(filter?.name ?? '')
  const [description, setDescription] = useState(filter?.description ?? '')
  const [query, setQuery] = useState(filter?.jql ?? jql ?? '')
  const [scope, setScope] = useState<FilterScope>(filter?.scope ?? 'private')
  const [projectKey, setProjectKey] = useState(filter?.project?.key ?? '')
  const [busy, setBusy] = useState(false)
  const choices = (projects.data ?? []).filter((p) => user.is_admin || p.is_member)
  const valid = name.trim() && query.trim() && (scope !== 'project' || projectKey)

  const submit = async () => {
    if (!valid) return
    setBusy(true)
    const body = { name: name.trim(), description, jql: query, scope, project_key: scope === 'project' ? projectKey : null }
    try {
      if (filter) await api.patch(`/api/filters/${filter.id}`, body)
      else await api.post('/api/filters', body)
      client.invalidateQueries({ queryKey: ['filters'] })
      client.invalidateQueries({ queryKey: ['shared-filters'] })
      toast(t('已保存'), 'success')
      onClose()
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={filter ? t('编辑筛选器') : t('保存为筛选器')}
      size="narrow"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            {t('取消')}
          </button>
          <button className="btn primary" disabled={!valid || busy} onClick={submit}>
            {t('保存')}
          </button>
        </>
      }
    >
      <form
        className="col"
        style={{ gap: 12 }}
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <div className="field">
          <label>{t('筛选器名称')}</label>
          <input className="input" autoFocus maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field">
          <label>{t('描述')}</label>
          <textarea className="textarea" rows={2} maxLength={2000} placeholder={t('说明这个筛选器的用途（可选）')} value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div className="field">
          <label>JQL</label>
          <textarea className="textarea mono" rows={3} value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <div className="field">
          <label>{t('共享')}</label>
          <Segmented<FilterScope>
            value={scope}
            onChange={setScope}
            items={[
              { value: 'private', label: t('仅自己') },
              { value: 'everyone', label: t('所有人') },
              { value: 'project', label: t('指定项目') },
            ]}
          />
          {scope === 'project' && (
            <Select className="select" style={{ marginTop: 8 }} value={projectKey} onChange={(e) => setProjectKey(e.target.value)}>
              <option value="">{t('选择项目')}</option>
              {choices.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.key} · {p.name}
                </option>
              ))}
            </Select>
          )}
          <div className="tiny faint" style={{ marginTop: 6 }}>
            {scope === 'private'
              ? t('只有你能看到这个筛选器。')
              : scope === 'everyone'
                ? t('所有人都能在「共享筛选器」中浏览并保存一份副本。')
                : t('只有该项目的成员能在「共享筛选器」中看到它。')}
          </div>
        </div>
      </form>
    </Modal>
  )
}
