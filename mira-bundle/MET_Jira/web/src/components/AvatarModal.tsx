import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { api } from '../api'
import { useApp } from '../context'
import { t } from '../i18n'
import { AVATAR_COLORS } from '../labels'
import type { UserFull } from '../types'
import { Avatar, Modal } from './ui'

const VIEW = 240
const OUTPUT = 512
const MAX_ZOOM = 4

interface Source {
  url: string
  image: HTMLImageElement
}

/** Upload, crop and remove the current user's avatar, or pick a color for the initials avatar. */
export function AvatarModal({ onClose }: { onClose: () => void }) {
  const { user, toast } = useApp()
  const client = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null)
  const [source, setSource] = useState<Source | null>(null)
  const [zoom, setZoom] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const [saving, setSaving] = useState(false)
  // Choices stay local until 保存; undefined means the color is unchanged.
  const [color, setColor] = useState<string | null | undefined>(undefined)
  const [removeImage, setRemoveImage] = useState(false)

  useEffect(() => () => { if (source) URL.revokeObjectURL(source.url) }, [source])

  const base = source ? VIEW / Math.min(source.image.naturalWidth, source.image.naturalHeight) : 1
  const scale = base * zoom
  const width = source ? source.image.naturalWidth * scale : VIEW
  const height = source ? source.image.naturalHeight * scale : VIEW

  const clamp = (x: number, y: number, w = width, h = height) => ({
    x: Math.max(-(w - VIEW) / 2, Math.min((w - VIEW) / 2, x)),
    y: Math.max(-(h - VIEW) / 2, Math.min((h - VIEW) / 2, y)),
  })

  const applyZoom = (next: number) => {
    if (!source) return
    const z = Math.max(1, Math.min(MAX_ZOOM, next))
    const s = base * z
    const ratio = z / zoom
    setZoom(z)
    setOffset(clamp(offset.x * ratio, offset.y * ratio, source.image.naturalWidth * s, source.image.naturalHeight * s))
  }

  const pick = (file: File | undefined) => {
    if (!file) return
    if (!file.type.startsWith('image/')) {
      toast(t('请选择图片文件'), 'error')
      return
    }
    if (file.size > 5 * 1024 * 1024) {
      toast(t('图片不能超过 5 MB'), 'error')
      return
    }
    const url = URL.createObjectURL(file)
    const image = new Image()
    image.onload = () => {
      setSource({ url, image })
      setZoom(1)
      setOffset({ x: 0, y: 0 })
    }
    image.onerror = () => {
      URL.revokeObjectURL(url)
      toast(t('无法读取该图片'), 'error')
    }
    image.src = url
  }

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!source) return
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { x: e.clientX, y: e.clientY, ox: offset.x, oy: offset.y }
  }
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current
    if (d) setOffset(clamp(d.ox + e.clientX - d.x, d.oy + e.clientY - d.y))
  }

  const updated = (next: UserFull) => {
    client.setQueryData(['me'], (old: { user: UserFull } | undefined) => (old ? { ...old, user: next } : old))
    client.invalidateQueries({ queryKey: ['me'] })
    client.invalidateQueries({ queryKey: ['projects'] })
    client.invalidateQueries({ queryKey: ['org-tree'] })
  }

  const crop = () =>
    new Promise<Blob>((resolve, reject) => {
      const fail = () => reject(new Error(t('裁剪失败，请换一张图片')))
      if (!source) return fail()
      const canvas = document.createElement('canvas')
      canvas.width = OUTPUT
      canvas.height = OUTPUT
      const ctx = canvas.getContext('2d')
      if (!ctx) return fail()
      const left = (VIEW - width) / 2 + offset.x
      const top = (VIEW - height) / 2 + offset.y
      ctx.imageSmoothingQuality = 'high'
      ctx.drawImage(source.image, -left / scale, -top / scale, VIEW / scale, VIEW / scale, 0, 0, OUTPUT, OUTPUT)
      canvas.toBlob((blob) => (blob ? resolve(blob) : fail()), 'image/png')
    })

  const currentColor = user.avatar_color ?? null
  const shownColor = color === undefined ? currentColor : color
  const colorChanged = color !== undefined && color !== currentColor
  const willRemove = removeImage && !source && !!user.avatar_url
  const dirty = !!source || willRemove || colorChanged

  const save = async () => {
    if (!dirty) return onClose()
    setSaving(true)
    try {
      let next: UserFull | null = null
      if (source) {
        const form = new FormData()
        form.append('file', await crop(), 'avatar.png')
        next = (await api.upload<{ user: UserFull }>('/api/auth/me/avatar', form)).user
      } else if (willRemove) {
        next = (await api.del<{ user: UserFull }>('/api/auth/me/avatar')).user
      }
      if (colorChanged) next = (await api.put<{ user: UserFull }>('/api/auth/me/avatar-color', { color })).user
      if (next) updated(next)
      toast(t('头像已更新'), 'success')
      onClose()
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setSaving(false)
    }
  }

  const shownUrl = source || willRemove ? null : user.avatar_url
  const preview = { ...user, avatar_url: shownUrl, avatar_color: shownColor }

  return (
    <Modal
      title={t('更换头像')}
      size="narrow"
      onClose={onClose}
      footer={
        <>
          {user.avatar_url && !source && (
            willRemove ? (
              <button className="btn" disabled={saving} onClick={() => setRemoveImage(false)}>
                {t('保留图片')}
              </button>
            ) : (
              <button className="btn danger" disabled={saving} onClick={() => setRemoveImage(true)}>
                {t('移除图片')}
              </button>
            )
          )}
          <span className="spacer" />
          {dirty && <span className="tiny faint">{t('保存后生效')}</span>}
          <button className="btn" onClick={onClose}>
            {t('取消')}
          </button>
          <button className="btn primary" onClick={save} disabled={!dirty || saving}>
            {t('保存')}
          </button>
        </>
      }
    >
      <div className="avatar-editor">
        <div
          className={`avatar-crop ${source ? 'has-image' : ''}`}
          style={{ width: VIEW, height: VIEW }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={() => (drag.current = null)}
          onPointerCancel={() => (drag.current = null)}
          onWheel={(e) => source && applyZoom(zoom * (e.deltaY < 0 ? 1.08 : 1 / 1.08))}
          onClick={() => !source && fileRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault()
            pick(e.dataTransfer.files[0])
          }}
        >
          {source ? (
            <img
              src={source.url}
              alt=""
              draggable={false}
              style={{ width, height, transform: `translate(${(VIEW - width) / 2 + offset.x}px, ${(VIEW - height) / 2 + offset.y}px)` }}
            />
          ) : (
            <Avatar user={preview} size={VIEW} />
          )}
        </div>
        {source ? (
          <label className="row small muted" style={{ width: VIEW }}>
            {t('缩放')}
            <input type="range" min={1} max={MAX_ZOOM} step={0.01} value={zoom} onChange={(e) => applyZoom(Number(e.target.value))} style={{ flex: 1 }} />
          </label>
        ) : (
          <div className="small muted">{t('点击或拖入图片，支持 PNG、JPG、WebP、GIF，最大 5 MB')}</div>
        )}
        <div className="row">
          <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden onChange={(e) => { pick(e.target.files?.[0]); e.target.value = '' }} />
          <button className="btn sm" onClick={() => fileRef.current?.click()}>
            {source ? t('换一张') : user.avatar_url ? t('上传新图片') : t('上传图片')}
          </button>
          {source && (
            <button className="btn sm ghost" onClick={() => setSource(null)}>
              {t('取消选择')}
            </button>
          )}
        </div>
        {source && <div className="tiny faint">{t('拖动图片调整位置，滚轮或滑块缩放')}</div>}

        <div className="divider" style={{ alignSelf: 'stretch' }} />
        <div className="col" style={{ alignSelf: 'stretch', gap: 6 }}>
          <div className="small muted">{shownUrl ? t('文字头像颜色（移除图片后显示）') : t('文字头像颜色')}</div>
          <div className="row wrap">
            <button
              className={`swatch auto ${!shownColor ? 'active' : ''}`}
              title={t('自动')}
              disabled={saving}
              onClick={() => setColor(null)}
            >
              {t('自动')}
            </button>
            {AVATAR_COLORS.map((c) => (
              <button
                key={c}
                className={`swatch ${shownColor === c ? 'active' : ''}`}
                style={{ background: c }}
                title={c}
                aria-pressed={shownColor === c}
                disabled={saving}
                onClick={() => setColor(c)}
              />
            ))}
          </div>
        </div>
      </div>
    </Modal>
  )
}
