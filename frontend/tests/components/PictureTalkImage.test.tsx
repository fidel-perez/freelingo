import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PictureTalkImage } from '@/components/conversation/PictureTalkImage'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  supported: false,
  drawImage: vi.fn(),
  baseUrl: '',
}))

vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('@/lib/browser-images', () => ({
  browserImagesSupported: () => mocks.supported,
  drawImage: mocks.drawImage,
}))
vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))
vi.mock('@/store/auth', () => ({
  useAuthStore: (select: (s: unknown) => unknown) =>
    select({ user: { images_enabled: true, image_model_base_url: mocks.baseUrl } }),
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
    mocks.drawImage.mockReset()
    mocks.supported = false
    mocks.baseUrl = ''
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

describe('PictureTalkImage browser draw failures', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mocks.apiFetch.mockReset()
    mocks.drawImage.mockReset()
    mocks.supported = true
    mocks.baseUrl = 'http://model'
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
  })
  afterEach(() => vi.useRealTimers())

  const mount = () =>
    render(<PictureTalkImage prompt="p" sceneId={7} words={['table']} />)
  const asksServer = () => {
    expect(mocks.apiFetch).toHaveBeenCalledTimes(1)
    expect(JSON.parse(mocks.apiFetch.mock.calls[0][1].body)).toEqual({
      scene_id: 7,
    })
  }

  it('keeps the browser draw first where WebGPU works', async () => {
    mocks.drawImage.mockResolvedValue(new Blob(['png']))
    const { container } = mount()
    await tick(0)
    expect(state(container)).toBe('done')
    expect(mocks.apiFetch).not.toHaveBeenCalled()
  })

  it('asks the server when there is no navigator.gpu', async () => {
    mocks.supported = false
    mocks.apiFetch.mockResolvedValue(png())
    const { container } = mount()
    await tick(0)
    expect(mocks.drawImage).not.toHaveBeenCalled()
    asksServer()
    expect(state(container)).toBe('server')
  })

  it.each([
    ['a null adapter', 'no WebGPU adapter'],
    ['a missing shader-f16', 'no shader-f16'],
    ['a worker error', 'image worker failed'],
    ['a failed generation', 'image generation failed'],
  ])('asks the server after %s', async (_name, message) => {
    mocks.drawImage.mockRejectedValue(new Error(message))
    mocks.apiFetch.mockResolvedValue(png())
    const { container } = mount()
    await tick(0)
    asksServer()
    expect(state(container)).toBe('server')
    expect(container.querySelector('img')).not.toBeNull()
  })

  it('asks the server when no image arrives within 20 s', async () => {
    mocks.drawImage.mockImplementation(() => new Promise(() => undefined))
    mocks.apiFetch.mockResolvedValue(png())
    const { container } = mount()
    await tick(19_999)
    expect(mocks.apiFetch).not.toHaveBeenCalled()
    await tick(1)
    asksServer()
    expect(state(container)).toBe('server')
  })

  it('shows a retry button when the server fails, and asks again on click', async () => {
    mocks.drawImage.mockRejectedValue(new Error('no WebGPU adapter'))
    mocks.apiFetch.mockResolvedValue(new Response('{}', { status: 500 }))
    const { container } = mount()
    await tick(0)
    expect(state(container)).toBe('failed')
    mocks.apiFetch.mockResolvedValue(png())
    await act(async () => {
      screen.getByText('imageRetry').click()
    })
    await tick(0)
    expect(mocks.apiFetch).toHaveBeenCalledTimes(2)
    expect(state(container)).toBe('server')
  })

  it('ignores a draw that finishes after the 20 s timeout', async () => {
    let finish: (blob: Blob) => void = () => undefined
    mocks.drawImage.mockImplementation(
      () => new Promise<Blob>((resolve) => (finish = resolve))
    )
    mocks.apiFetch.mockImplementation(() => Promise.resolve(pending()))
    const { container } = mount()
    await tick(20_000)
    expect(state(container)).toBe('server-loading')
    await act(async () => {
      finish(new Blob(['late']))
    })
    await tick(0)
    expect(state(container)).toBe('server-loading')
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText('table')).toBeTruthy()
  })

  it('does not ask the server for a new scene before its own draw fails', async () => {
    mocks.drawImage.mockRejectedValueOnce(new Error('image generation failed'))
    mocks.apiFetch.mockResolvedValue(png())
    const { rerender } = mount()
    await tick(0)
    asksServer()
    mocks.drawImage.mockImplementation(() => new Promise(() => undefined))
    rerender(<PictureTalkImage prompt="q" sceneId={8} words={['table']} />)
    await tick(0)
    expect(mocks.apiFetch).toHaveBeenCalledTimes(1)
  })
})
