import { useEffect, useMemo, useRef, useState, type ClipboardEvent, type DragEvent } from 'react'
import { t } from '../i18n'
import { fmtSize } from '../labels'
import { named } from './attachPaste'
import { MarkdownImageThumbs } from './MarkdownImageThumbs'
import { mdImageMarkerBlock, mergeVisibleAndMarkers, removeMdImage, stripMdImages } from './markdownImages'
import { MentionTextarea } from './MentionInput'

/** Screenshots / files picked before the issue exists, keyed by a short token. */
export type StagedFiles = Record<string, File>

const stagedPattern = () => /!\[[^\]\n]*\]\(staged:([a-z0-9]{6,32})\)\n?/g

const newToken = () => Math.random().toString(36).slice(2, 10).padEnd(8, '0')

export const stagedTokens = (text: string) => [...text.matchAll(stagedPattern())].map((m) => m[1])

/** Tokens still referenced by any of ``texts``, in first-seen order. */
export function referencedTokens(texts: unknown[], staged: StagedFiles) {
  const seen = new Set<string>()
  for (const text of texts) {
    if (typeof text !== 'string') continue
    for (const token of stagedTokens(text)) if (staged[token]) seen.add(token)
  }
  return [...seen]
}

/** Markdown-referenced tokens first, then standalone create-form attachments. */
export function mergeCreateTokens(markdownTokens: string[], extras: StagedFiles): string[] {
  const out = [...markdownTokens]
  const seen = new Set(out)
  for (const token of Object.keys(extras)) {
    if (!seen.has(token) && extras[token]) {
      seen.add(token)
      out.push(token)
    }
  }
  return out
}

function fileBadge(file: File): string {
  const ext = file.name.split('.').pop()?.toUpperCase() || ''
  if (ext && ext.length <= 5) return ext
  if (file.type.startsWith('image/')) return 'IMG'
  if (file.type === 'application/pdf') return 'PDF'
  return 'FILE'
}

/**
 * Markdown textarea for the create form: pasted, dropped or picked screenshots are held in the
 * browser and referenced by a placeholder; the create request uploads them as attachments.
 * Placeholders stay in the stored value (for submit) but are hidden from the editor; thumbs
 * below the field are the editable image UI.
 */
export function StagedMarkdownField({ value, onChange, staged, onStage, placeholder, minHeight = 80 }: {
  value: string
  onChange: (value: string) => void
  staged: StagedFiles
  onStage: (files: StagedFiles) => void
  placeholder?: string
  minHeight?: number
}) {
  const box = useRef<HTMLTextAreaElement>(null)
  const picker = useRef<HTMLInputElement>(null)
  const visible = useMemo(() => stripMdImages(value), [value])
  const markers = useMemo(() => mdImageMarkerBlock(value), [value])
  const tokens = useMemo(() => stagedTokens(value).filter((token) => staged[token]), [value, staged])
  const stagedUrls = useMemo(() => {
    const urls: Record<string, string> = {}
    for (const token of tokens) urls[token] = URL.createObjectURL(staged[token])
    return urls
  }, [tokens, staged])
  useEffect(() => () => Object.values(stagedUrls).forEach((url) => URL.revokeObjectURL(url)), [stagedUrls])

  const insert = (files: File[]) => {
    const images = files.filter((file) => file.type.startsWith('image/'))
    if (!images.length) return
    const el = box.current
    const caret = el ? el.selectionStart : visible.length
    const additions: StagedFiles = {}
    const block = images.map((file, i) => {
      const token = newToken()
      const renamed = named(file, i)
      additions[token] = renamed
      return `![${renamed.name.replace(/[[\]\n]/g, '')}](staged:${token})\n`
    }).join('')
    onStage({ ...staged, ...additions })
    onChange(mergeVisibleAndMarkers(visible, markers + block))
    requestAnimationFrame(() => {
      if (!box.current) return
      box.current.focus()
      box.current.selectionStart = box.current.selectionEnd = caret
    })
  }

  const remove = (raw: string) => {
    const nextValue = removeMdImage(value, raw)
    onChange(nextValue)
    const keep = new Set(stagedTokens(nextValue))
    const next = { ...staged }
    let changed = false
    for (const token of Object.keys(next)) {
      if (keep.has(token)) continue
      delete next[token]
      changed = true
    }
    if (changed) onStage(next)
  }

  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData.files)
    if (!files.some((file) => file.type.startsWith('image/')) || e.clipboardData.getData('text/plain').trim()) return
    e.preventDefault()
    insert(files)
  }
  const onDrop = (e: DragEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.dataTransfer.files)
    if (!files.some((file) => file.type.startsWith('image/'))) return
    e.preventDefault()
    insert(files)
  }

  return (
    <div className="col" style={{ gap: 6 }}>
      <MentionTextarea
        textareaRef={box}
        style={{ minHeight }}
        value={visible}
        onChange={(next) => onChange(mergeVisibleAndMarkers(next, markers))}
        onPaste={onPaste}
        onDrop={onDrop}
        onSlashImage={() => picker.current?.click()}
        placeholder={placeholder ?? t('添加描述，或输入 / 插入格式与操作')}
      />
      <MarkdownImageThumbs text={value} onRemove={remove} stagedUrls={stagedUrls} />
      <div className="row" style={{ gap: 8 }}>
        <input ref={picker} type="file" accept="image/*" multiple hidden onChange={(e) => {
          const files = Array.from(e.target.files ?? [])
          e.target.value = ''
          insert(files)
        }} />
        <button type="button" className="btn sm" onClick={() => picker.current?.click()}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="15" rx="2" /><circle cx="12" cy="12.5" r="3.5" /><path d="M8.5 5l1.5-2h4l1.5 2" /></svg>
          {t('添加截图')}
        </button>
        <span className="tiny faint">{t('输入 / 插入格式；可粘贴或拖入截图')}</span>
      </div>
    </div>
  )
}

/**
 * Create-form file picker for PDF / Word / Excel and other attachments that are not
 * embedded in Markdown. Files stay in the browser until create uploads them.
 */
export function StagedAttachmentsField({ files, onChange }: {
  files: StagedFiles
  onChange: (files: StagedFiles) => void
}) {
  const picker = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const entries = useMemo(() => Object.entries(files), [files])

  const add = (picked: File[]) => {
    if (!picked.length) return
    const next = { ...files }
    picked.forEach((file, i) => {
      next[newToken()] = named(file, i)
    })
    onChange(next)
  }

  const remove = (token: string) => {
    const next = { ...files }
    delete next[token]
    onChange(next)
  }

  return (
    <div className="col" style={{ gap: 8 }}>
      {entries.length > 0 && (
        <div className="col" style={{ gap: 6 }}>
          {entries.map(([token, file]) => (
            <div key={token} className="attachment">
              <div className={`thumb file-badge${file.type === 'application/pdf' ? ' pdf' : ''}`}>
                {fileBadge(file)}
              </div>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="ellipsis" style={{ fontWeight: 500 }}>{file.name}</div>
                <div className="tiny faint">{fmtSize(file.size)}</div>
              </div>
              <button type="button" className="btn ghost sm icon" onClick={() => remove(token)} title={t('移除')}>
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
      <div
        className={`dropzone ${over ? 'over' : ''}`}
        onClick={() => picker.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          add(Array.from(e.dataTransfer.files))
        }}
      >
        <div>{t('拖拽文件到此处，或点击上传')}</div>
        <div className="tiny faint" style={{ marginTop: 4 }}>
          {t('支持 PDF、Word、Excel、PPT、压缩包、文本和图片等')}
        </div>
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            const picked = Array.from(e.target.files ?? [])
            e.target.value = ''
            add(picked)
          }}
        />
      </div>
    </div>
  )
}
