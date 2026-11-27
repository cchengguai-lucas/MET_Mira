import { describe, expect, it } from 'vitest'
import {
  mdImageMarkerBlock,
  mergeVisibleAndMarkers,
  removeMdImage,
  stripMdImages,
} from './markdownImages'
import { mergeCreateTokens, stagedTokens } from './StagedMarkdownField'

describe('hidden markdown image markers', () => {
  const sample = '现象如下\n![paste-a.png](/api/attachments/3?inline=true)\n还有说明\n![paste-b.png](staged:bbbbbb22)\n'

  it('hides markers from the editor text', () => {
    expect(stripMdImages(sample)).toBe('现象如下\n还有说明')
    expect(stripMdImages(sample)).not.toMatch(/!\[/)
  })

  it('keeps markers for submit while editing visible prose', () => {
    const markers = mdImageMarkerBlock(sample)
    expect(stagedTokens(markers)).toEqual(['bbbbbb22'])
    expect(mergeVisibleAndMarkers('改后的说明', markers)).toBe(
      '改后的说明\n![paste-a.png](/api/attachments/3?inline=true)\n![paste-b.png](staged:bbbbbb22)\n',
    )
  })

  it('round-trips an empty prose field that only has screenshots', () => {
    const onlyShots = '![a.png](staged:aaaaaa11)\n'
    expect(stripMdImages(onlyShots)).toBe('')
    expect(mergeVisibleAndMarkers('', mdImageMarkerBlock(onlyShots))).toBe(onlyShots)
  })

  it('keeps typed characters on one line after screenshots are attached', () => {
    let value = '![a.png](staged:aaaaaa11)\n![b.png](staged:bbbbbb22)\n![c.png](staged:cccccc33)\n'
    for (const ch of ['t', 't', 't']) {
      value = mergeVisibleAndMarkers(stripMdImages(value) + ch, mdImageMarkerBlock(value))
    }
    expect(stripMdImages(value)).toBe('ttt')
    expect(value).toContain('staged:aaaaaa11')
    expect(value).toContain('staged:cccccc33')
  })

  it('preserves Enter newlines and blank lines while screenshots are attached', () => {
    let value = '第一行\n![a.png](staged:aaaaaa11)\n'
    value = mergeVisibleAndMarkers(`${stripMdImages(value)}\n`, mdImageMarkerBlock(value))
    expect(stripMdImages(value)).toBe('第一行\n')

    value = mergeVisibleAndMarkers(`${stripMdImages(value)}\n`, mdImageMarkerBlock(value))
    expect(stripMdImages(value)).toBe('第一行\n\n')

    value = mergeVisibleAndMarkers(`${stripMdImages(value)}第二行`, mdImageMarkerBlock(value))
    expect(stripMdImages(value)).toBe('第一行\n\n第二行')
    expect(value).toBe('第一行\n\n第二行\n![a.png](staged:aaaaaa11)\n')

    expect(removeMdImage(value, '![a.png](staged:aaaaaa11)\n')).toBe('第一行\n\n第二行')
  })

  it('merges markdown screenshot tokens with standalone create attachments', () => {
    const pdf = new File([new Uint8Array([1, 2, 3])], 'spec.pdf', { type: 'application/pdf' })
    expect(mergeCreateTokens(['aaaaaa11'], { bbbbbb22: pdf, aaaaaa11: pdf })).toEqual([
      'aaaaaa11',
      'bbbbbb22',
    ])
    expect(mergeCreateTokens([], { cccccc33: pdf })).toEqual(['cccccc33'])
  })
})
