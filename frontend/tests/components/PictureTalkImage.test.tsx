import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PictureTalkImage } from '@/components/conversation/PictureTalkImage'

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn() }))

vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('@/lib/browser-images', () => ({
  browserImagesSupported: () => false,
  drawImage: vi.fn(),
}))
vi.mock('@/store/auth', () => ({
  useAuthStore: (select: (s: unknown) => unknown) =>
    select({ user: { images_enabled: true, image_model_base_url: '' } }),
}))

const pending = () => new Response('{"status":"pending"}', { status: 202 })
const png = () => new Response(new Blob(['png']), { status: 200 })
const box = (container: HTMLElement) =>
  container.querySelector('[data-picture-talk-image]')
const state = (container: HTMLElement) =>
  box(container)?.getAttribute('data-image-state')

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('PictureTalkImage server polling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mocks.apiFetch.mockReset()
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
  })
  afterEach(() => vi.useRealTimers())

  it('polls every 3 s on 202 and shows the image when it is ready', async () => {
    mocks.apiFetch
      .mockResolvedValueOnce(pending())
      .mockResolvedValueOnce(pending())
      .mockResolvedValueOnce(png())
    const { container } = render(
      <PictureTalkImage prompt="p" sceneId={1} words={['table']} />
    )
    await tick(0)
    expect(state(container)).toBe('server-loading')
    expect(screen.getByText('table')).toBeTruthy()
    await tick(2999)
    expect(mocks.apiFetch).toHaveBeenCalledTimes(1)
    await tick(1)
    expect(mocks.apiFetch).toHaveBeenCalledTimes(2)
    await tick(3000)
    expect(mocks.apiFetch).toHaveBeenCalledTimes(3)
    expect(state(container)).toBe('server')
    expect(container.querySelector('img')).not.toBeNull()
  })

  it('gives up with the words after 4 minutes of pending', async () => {
    mocks.apiFetch.mockImplementation(() => Promise.resolve(pending()))
    const { container } = render(
      <PictureTalkImage prompt="p" sceneId={1} words={['table']} />
    )
    await tick(4 * 60 * 1000 + 3000)
    expect(state(container)).toBe('failed')
    const calls = mocks.apiFetch.mock.calls.length
    await tick(30_000)
    expect(mocks.apiFetch).toHaveBeenCalledTimes(calls)
  })

  it('stops polling on unmount and restarts on a scene change', async () => {
    mocks.apiFetch.mockImplementation(() => Promise.resolve(pending()))
    const { rerender, unmount } = render(
      <PictureTalkImage prompt="a" sceneId={1} words={['table']} />
    )
    await tick(0)
    rerender(<PictureTalkImage prompt="b" sceneId={2} words={['table']} />)
    await tick(0)
    const lastBody = mocks.apiFetch.mock.calls.at(-1)?.[1].body
    expect(JSON.parse(lastBody)).toEqual({ scene_id: 2 })
    unmount()
    const calls = mocks.apiFetch.mock.calls.length
    await tick(10_000)
    expect(mocks.apiFetch).toHaveBeenCalledTimes(calls)
  })

  it('retries a 503 once, then shows the words', async () => {
    mocks.apiFetch.mockImplementation(() =>
      Promise.resolve(new Response('{}', { status: 503 }))
    )
    const { container } = render(
      <PictureTalkImage prompt="p" sceneId={1} words={['table']} />
    )
    await tick(0)
    expect(state(container)).toBe('server-loading')
    await tick(3000)
    expect(mocks.apiFetch).toHaveBeenCalledTimes(2)
    expect(state(container)).toBe('failed')
  })

  it('recovers when the 503 retry succeeds', async () => {
    mocks.apiFetch
      .mockResolvedValueOnce(new Response('{}', { status: 503 }))
      .mockResolvedValueOnce(png())
    const { container } = render(
      <PictureTalkImage prompt="p" sceneId={1} words={['table']} />
    )
    await tick(3000)
    expect(state(container)).toBe('server')
  })

  it('backs off on 429 and keeps polling until the image arrives', async () => {
    const limited = () => new Response('{}', { status: 429 })
    mocks.apiFetch
      .mockResolvedValueOnce(limited())
      .mockResolvedValueOnce(limited())
      .mockResolvedValueOnce(png())
    const { container } = render(
      <PictureTalkImage prompt="p" sceneId={1} words={['table']} />
    )
    await tick(0)
    expect(state(container)).toBe('server-loading')
    await tick(10_000)
    expect(state(container)).toBe('server-loading')
    await tick(10_000)
    expect(mocks.apiFetch).toHaveBeenCalledTimes(3)
    expect(state(container)).toBe('server')
  })
})
