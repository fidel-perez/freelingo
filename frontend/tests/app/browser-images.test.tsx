import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
  useLocale: () => 'en',
}))

const { mockApiFetch } = vi.hoisted(() => ({ mockApiFetch: vi.fn() }))
vi.mock('@/lib/api', () => ({ apiFetch: mockApiFetch }))

import { BrowserImage } from '@/components/BrowserImage'
import { BrowserImagesSection } from '@/components/settings/BrowserImagesSection'
import { useAuthStore } from '@/store/auth'

const baseUser = {
  id: 1,
  username: 'u',
  displayName: 'U',
  role: 'user' as const,
  conversation_max_duration: 1800,
  conversation_inactivity_timeout: 180,
  images_enabled: true,
  image_model_base_url: 'https://weights.example/',
  image_model_configured: true,
}

const posted: unknown[] = []

class FakeWorker {
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  postMessage(message: unknown) {
    posted.push(message)
    queueMicrotask(() =>
      this.onmessage?.({
        data: { ok: true, blob: new Blob(['png'], { type: 'image/png' }) },
      } as MessageEvent)
    )
  }
}

function setGpu(on: boolean) {
  Object.defineProperty(navigator, 'gpu', {
    value: on ? {} : undefined,
    configurable: true,
  })
  if (!on) delete (navigator as unknown as Record<string, unknown>).gpu
}

describe('browser images', () => {
  beforeEach(() => {
    posted.length = 0
    mockApiFetch.mockReset()
    useAuthStore.setState({ user: { ...baseUser } })
    vi.stubGlobal('Worker', FakeWorker)
    vi.stubGlobal('caches', {
      open: async () => ({
        match: async () => undefined,
        put: async () => undefined,
      }),
    })
    URL.createObjectURL = vi.fn(() => 'blob:fake')
    URL.revokeObjectURL = vi.fn()
  })

  afterEach(() => {
    setGpu(false)
    vi.unstubAllGlobals()
  })

  it('hides the setting without navigator.gpu', () => {
    setGpu(false)
    render(<BrowserImagesSection />)
    expect(screen.queryByRole('switch')).toBeNull()
  })

  it('shows the setting with navigator.gpu and a configured model', () => {
    setGpu(true)
    render(<BrowserImagesSection />)
    expect(screen.getByRole('switch')).toBeDefined()
  })

  it('draws through the worker and marks the element done', async () => {
    setGpu(true)
    mockApiFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ prompt: 'a red apple' }),
    })
    const { container } = render(
      <BrowserImage promptPath="/api/lessons/1/image-prompt" />
    )

    const el = container.querySelector('[data-image-state]')
    expect(el?.getAttribute('data-image-state')).toBe('loading')
    await waitFor(() =>
      expect(
        container.querySelector('[data-image-state="done"]')
      ).not.toBeNull()
    )
    expect(posted).toEqual([
      { baseUrl: 'https://weights.example/', prompt: 'a red apple' },
    ])
  })

  it('renders nothing without navigator.gpu', () => {
    setGpu(false)
    const { container } = render(
      <BrowserImage promptPath="/api/lessons/1/image-prompt" />
    )
    expect(container.innerHTML).toBe('')
    expect(mockApiFetch).not.toHaveBeenCalled()
  })
})
