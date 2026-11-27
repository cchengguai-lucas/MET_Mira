import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { useMemo, useState } from 'react'
import { useOptionalApp } from '../context'
import { Lightbox } from './ui'

marked.setOptions({ gfm: true, breaks: true })

export function renderMarkdown(text: string): string {
  const html = marked.parse(text || '', { async: false }) as string
  const withMentions = html.replace(/(^|[\s>(])@([A-Za-z0-9._-]{2,64})/g, '$1<span class="mention">@$2</span>')
  const linked = withMentions.replace(
    /(^|[\s>(])([A-Z][A-Z0-9]{1,9}-\d+)(?=[\s<),.;:，。]|$)/g,
    '$1<a href="?issue=$2" data-issue="$2">$2</a>',
  )
  return DOMPurify.sanitize(linked, { ADD_ATTR: ['data-issue'] })
}

export function Markdown({ text, onIssueClick }: { text: string; onIssueClick?: (key: string) => void }) {
  const html = useMemo(() => renderMarkdown(text), [text])
  const app = useOptionalApp()
  const handleIssue = onIssueClick ?? app?.openIssue
  const [zoom, setZoom] = useState<{ src: string; alt: string } | null>(null)
  return (
    <>
      <div
        className="markdown"
        dangerouslySetInnerHTML={{ __html: html }}
        onClick={(e) => {
          const el = e.target as HTMLElement
          if (el instanceof HTMLImageElement && !el.closest('a')) {
            e.stopPropagation()
            setZoom({ src: el.getAttribute('src') ?? '', alt: el.alt })
            return
          }
          const target = el.closest('a[data-issue]') as HTMLAnchorElement | null
          if (target && handleIssue) {
            e.preventDefault()
            handleIssue(target.dataset.issue!)
          }
        }}
      />
      {zoom && <Lightbox src={zoom.src} alt={zoom.alt} onClose={() => setZoom(null)} />}
    </>
  )
}
