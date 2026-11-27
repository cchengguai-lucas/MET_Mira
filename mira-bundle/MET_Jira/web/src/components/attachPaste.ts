import { useQueryClient } from '@tanstack/react-query'
import { useState, type ClipboardEvent, type DragEvent, type RefObject } from 'react'
import { api } from '../api'
import { useApp } from '../context'
import { t } from '../i18n'
import type { Attachment } from '../types'
import { mdImageMarkerBlock, mergeVisibleAndMarkers, stripMdImages } from './markdownImages'

const stamp = () => {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`
}

/** Clipboard images arrive as ``image.png``; give them a name that tells attachments apart. */
export function named(file: File, i: number): File {
  if (file.type.startsWith('image/') && /^image\.\w+$/i.test(file.name)) {
    const ext = file.type.split('/')[1]?.replace('jpeg', 'jpg') || 'png'
    return new File([file], `paste-${stamp()}${i ? `-${i + 1}` : ''}.${ext}`, { type: file.type })
  }
  return file
}

const markdownFor = (att: Attachment) =>
  att.content_type.startsWith('image/') ? `![${att.filename}](${att.url}?inline=true)` : `[${att.filename}](${att.url})`

/**
 * Pasting or dropping files into a Markdown textarea uploads them as attachments of ``issueKey``
 * and appends image markers to the stored draft (hidden from the visible editor). Pastes that
 * also carry text (e.g. Excel cells) are left to the browser.
 */
export function useAttachmentPaste(issueKey: string | undefined, ref: RefObject<HTMLTextAreaElement | null>, setText: (update: (prev: string) => string) => void) {
  const { toast } = useApp()
  const client = useQueryClient()
  const [uploading, setUploading] = useState(0)

  const upload = (files: File[]) => {
    if (!issueKey || files.length === 0) return
    const jobs = files.map((file, i) => ({
      file: named(file, i),
      token: `![${t('上传中…')} ${Math.random().toString(36).slice(2, 8)}]()`,
    }))
    const block = `${jobs.map((job) => job.token).join('\n')}\n`
    setText((prev) => mergeVisibleAndMarkers(stripMdImages(prev), mdImageMarkerBlock(prev) + block))
    requestAnimationFrame(() => ref.current?.focus())
    setUploading((n) => n + jobs.length)
    jobs.forEach(async ({ file, token }) => {
      try {
        const form = new FormData()
        form.append('file', file)
        const att = await api.upload<Attachment>(`/api/issues/${issueKey}/attachments`, form)
        setText((prev) => prev.replace(token, markdownFor(att)))
        client.invalidateQueries({ queryKey: ['issue', issueKey] })
      } catch (err) {
        setText((prev) => prev.replace(`${token}\n`, '').replace(token, ''))
        toast(`${file.name}: ${(err as Error).message}`, 'error')
      } finally {
        setUploading((n) => n - 1)
      }
    })
  }

  return {
    uploading,
    /** Upload picked files as hidden image markers on the draft. */
    add: (files: File[]) => upload(files),
    onPaste: (e: ClipboardEvent<HTMLTextAreaElement>) => {
      const files = Array.from(e.clipboardData.files)
      if (!issueKey || files.length === 0 || e.clipboardData.getData('text/plain').trim()) return
      e.preventDefault()
      upload(files)
    },
    onDrop: (e: DragEvent<HTMLTextAreaElement>) => {
      const files = Array.from(e.dataTransfer.files)
      if (!issueKey || files.length === 0) return
      e.preventDefault()
      upload(files)
    },
  }
}
