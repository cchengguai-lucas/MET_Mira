import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { useApp } from '../context'
import { t } from '../i18n'
import { Modal, Spinner } from './ui'

interface PurgePreview {
  key: string
  archived: boolean
  counts: { issues: number; comments: number; attachments: number; boards: number; sprints: number }
}

/** Permanently deletes an archived project after the admin types its key. */
export function DeleteProjectModal({ project, onClose, onDeleted }: {
  project: { key: string; name: string }
  onClose: () => void
  onDeleted?: () => void
}) {
  const { toast } = useApp()
  const client = useQueryClient()
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const preview = useQuery({
    queryKey: ['purge-preview', project.key],
    queryFn: () => api.get<PurgePreview>(`/api/projects/${project.key}/purge-preview`),
  })
  const matches = typed.trim().toUpperCase() === project.key
  const submit = async () => {
    setBusy(true)
    try {
      await api.del(`/api/projects/${project.key}?confirm=${encodeURIComponent(typed.trim())}`)
      client.removeQueries({ queryKey: ['project', project.key] })
      await client.invalidateQueries({ queryKey: ['projects'] })
      toast(t('项目 {key} 已永久删除', { key: project.key }), 'success')
      onDeleted?.()
      onClose()
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  const counts = preview.data?.counts
  return (
    <Modal
      title={t('永久删除项目')}
      size="narrow"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>{t('取消')}</button>
          <button className="btn danger" disabled={!matches || busy || !preview.data?.archived} onClick={submit}>
            {busy ? t('删除中…') : t('永久删除')}
          </button>
        </>
      }
    >
      <div className="col" style={{ gap: 12 }}>
        <div className="purge-warning">
          <b>{t('此操作无法撤销。')}</b>
          <div>{t('将删除项目「{name}」及其全部数据：', { name: project.name })}</div>
          {preview.isLoading ? <Spinner /> : counts && (
            <ul>
              <li>{t('{n} 个事项', { n: counts.issues })}</li>
              <li>{t('{n} 条评论', { n: counts.comments })}</li>
              <li>{t('{n} 个附件（含文件）', { n: counts.attachments })}</li>
              <li>{t('{n} 个看板、{m} 个冲刺', { n: counts.boards, m: counts.sprints })}</li>
            </ul>
          )}
          <div className="tiny">{t('还会一并删除成员、状态、工作流、机台和 AI 周报；共享给该项目的筛选器将不再对任何人可见。')}</div>
        </div>
        {preview.data && !preview.data.archived && <div className="err-text small">{t('请先归档项目，再永久删除')}</div>}
        <div className="field">
          <label>{t('输入项目 Key「{key}」以确认', { key: project.key })}</label>
          <input className="input mono" autoFocus value={typed} placeholder={project.key}
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && matches && !busy && submit()} />
        </div>
      </div>
    </Modal>
  )
}
