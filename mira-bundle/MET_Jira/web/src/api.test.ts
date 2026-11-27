import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from './api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('issue optimistic concurrency', () => {
  it('injects the latest cached lock version into patch and rank writes', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        key: 'ELEC-901',
        lock_version: 7,
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        key: 'ELEC-901',
        lock_version: 8,
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        key: 'ELEC-901',
        lock_version: 9,
      }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await api.get('/api/issues/ELEC-901')
    await api.patch('/api/issues/ELEC-901', { title: 'new' })
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      title: 'new',
      expected_version: 7,
    })
    await api.post('/api/issues/ELEC-901/rank', { after_key: 'ELEC-902' })
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({
      after_key: 'ELEC-902',
      expected_version: 8,
    })
  })

  it('announces stale writes without replacing the caller draft', async () => {
    const stale = vi.fn()
    window.addEventListener('metjira:stale-write', stale)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: '内容已更新',
      code: 'stale_write',
    }), { status: 409 })))

    await expect(api.patch('/api/issues/ELEC-999', { title: 'draft' }))
      .rejects.toMatchObject({ status: 409, code: 'stale_write' } satisfies Partial<ApiError>)
    expect(stale).toHaveBeenCalledOnce()
    window.removeEventListener('metjira:stale-write', stale)
  })
})
