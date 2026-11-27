import { t } from '../i18n'
import { mdImageSrc, parseMdImages } from './markdownImages'

/** Thumbnail strip for markdown images hidden from a textarea draft. */
export function MarkdownImageThumbs({
  text,
  onRemove,
  stagedUrls,
}: {
  text: string
  onRemove: (raw: string) => void
  /** Object URLs for ``staged:token`` markers (create form). */
  stagedUrls?: Record<string, string>
}) {
  const images = parseMdImages(text)
  if (!images.length) return null
  return (
    <div className="staged-shots">
      {images.map((image, index) => {
        const staged = image.url.startsWith('staged:') ? stagedUrls?.[image.url.slice('staged:'.length)] : undefined
        const src = staged ?? mdImageSrc(image.url)
        return (
          <figure key={`${image.url}-${index}`} className="staged-shot" title={image.alt || t('截图')}>
            {src ? <img src={src} alt={image.alt} /> : <span className="staged-shot-pending" aria-hidden>…</span>}
            <button
              type="button"
              className="staged-shot-remove"
              aria-label={t('移除截图')}
              title={t('移除截图')}
              onClick={() => onRemove(image.raw)}
            >
              ×
            </button>
          </figure>
        )
      })}
    </div>
  )
}
