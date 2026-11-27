/** Markdown image syntax ``![alt](url)`` with an optional trailing newline. */

const imagePattern = () => /!\[([^\]\n]*)\]\(([^)\n]*)\)\n?/g
const trailingImagePattern = /!\[[^\]\n]*\]\([^)\n]*\)\n?$/

export type MdImage = { alt: string; url: string; raw: string }

/** Drop image markdown so editors only show prose; markers stay in the stored value. */
export function stripMdImages(text: string): string {
  const hadTrailingImage = trailingImagePattern.test(text)
  const stripped = text.replace(imagePattern(), '')
  // mergeVisibleAndMarkers inserts exactly one separator \n before a trailing marker block;
  // strip only that single separator so user-typed trailing newlines are preserved.
  return hadTrailingImage && stripped.endsWith('\n') ? stripped.slice(0, -1) : stripped
}

/** Collect image markers (each on its own line) for re-merge after editing prose. */
export function mdImageMarkerBlock(text: string): string {
  return [...text.matchAll(imagePattern())]
    .map((match) => (match[0].endsWith('\n') ? match[0] : `${match[0]}\n`))
    .join('')
}

export function mergeVisibleAndMarkers(visible: string, markerBlock: string): string {
  if (!markerBlock) return visible
  const markers = markerBlock.replace(/^\n+/, '')
  return visible ? `${visible}\n${markers}` : markers
}

export function parseMdImages(text: string): MdImage[] {
  return [...text.matchAll(imagePattern())].map((match) => ({
    alt: match[1],
    url: match[2],
    raw: match[0],
  }))
}

export function removeMdImage(text: string, raw: string): string {
  const visible = stripMdImages(text)
  const markers = mdImageMarkerBlock(text)
  const normalized = raw.endsWith('\n') ? raw : `${raw}\n`
  const at = markers.indexOf(normalized)
  if (at >= 0) {
    const remaining = `${markers.slice(0, at)}${markers.slice(at + normalized.length)}`
    return mergeVisibleAndMarkers(visible, remaining)
  }
  const fallbackAt = text.indexOf(raw)
  if (fallbackAt < 0) return text
  return `${text.slice(0, fallbackAt)}${text.slice(fallbackAt + raw.length)}`
}

/** Resolve a preview URL for an image marker; ``null`` means still uploading / unknown. */
export function mdImageSrc(url: string): string | null {
  if (!url || url.startsWith('staged:')) return null
  if (/\/api\/attachments\/\d+/.test(url)) {
    return url.includes('inline=true') ? url : `${url}${url.includes('?') ? '&' : '?'}inline=true`
  }
  return url
}
