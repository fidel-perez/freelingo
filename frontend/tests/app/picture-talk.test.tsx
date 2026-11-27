import { Suspense } from 'react'
import { lazy } from 'react'
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { useMicVAD } from '@ricky0123/vad-react'

const mocks = vi.hoisted(() => ({
  query: 'picture=7',
  push: vi.fn(),
  apiFetch: vi.fn(),
  getUserMedia: vi.fn(),
  vadOptions: {} as Parameters<typeof useMicVAD>[0],
  vadCalls: 0,
}))

vi.mock('@ricky0123/vad-react', () => ({
  useMicVAD: (options: Parameters<typeof useMicVAD>[0]) => {
    mocks.vadOptions = options
    mocks.vadCalls++
    return {
      loading: false,
      errored: false,
      start: vi.fn().mockResolvedValue(undefined),
      pause: vi.fn().mockResolvedValue(undefined),
    }
  },
}))
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mocks.push }),
  useSearchParams: () => new URLSearchParams(mocks.query),
}))
vi.mock('next/dynamic', () => ({
  default: (loader: Parameters<typeof lazy>[0]) => lazy(loader),
}))
vi.mock('next-intl', () => ({
  useLocale: () => 'en',
  useTranslations: () =>
    Object.assign((key: string) => key, { raw: () => [], rich: () => '' }),
}))
vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('@/components/billing/MaintenanceBanner', () => ({
  MaintenanceGate: ({ children }: { children: React.ReactNode }) => children,
}))
vi.mock('@/components/billing/FreemiumQuotaBanner', () => ({
  FreemiumQuotaBanner: () => null,
}))
vi.mock('@/components/plan/NoPlanBanner', () => ({ default: () => null }))
vi.mock('@/components/ui/page-loading', () => ({
  PageLoading: () => <div>loading</div>,
}))

import ConversationPage from '@/app/(app)/conversation/page'
import { SituationsCatalog } from '@/components/situations/SituationsCatalog'
import { float32ToWav } from '@/lib/audio'
import { mapUser } from '@/lib/mappers'
import { useAuthStore } from '@/store/auth'
import { useConfigStore } from '@/store/config'
import { useFreemiumStore } from '@/store/freemium'
import { useLanguageStore } from '@/store/language'
import { getLanguageByCode } from '@/lib/target-languages'

class MockWebSocket {
  static OPEN = 1
  static instances: MockWebSocket[] = []
  readyState = 1
  binaryType = ''
  onopen: (() => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: ((event: { code: number; reason: string }) => void) | null = null
  send = vi.fn()
  close = vi.fn(() => {
    this.readyState = 3
  })

  constructor() {
    MockWebSocket.instances.push(this)
  }
}

class FakeWorker {
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  terminate = vi.fn()
  postMessage(job: { prompt: string }) {
    queueMicrotask(() =>
      this.onmessage?.({
        data:
          job.prompt === 'fail'
            ? { ok: false }
            : { ok: true, blob: new Blob(['png'], { type: 'image/png' }) },
      } as MessageEvent)
    )
  }
}

class FakeAudio {
  static instances: HTMLAudioElement[] = []
  constructor(src: string) {
    const element = document.createElement('audio')
    element.src = src
    element.play = vi.fn().mockResolvedValue(undefined)
    element.pause = vi.fn()
    FakeAudio.instances.push(element)
    return element
  }
}

const plan = {
  id: 7,
  cefr_level: 'A2',
  progress_day: 1,
  total_days: 30,
  completion_pct: 0,
}
const scene = {
  id: 41,
  study_plan_id: 7,
  image_prompt: 'a red bicycle by a river',
  words: ['a', 'b'],
}

function setUser(extra: Record<string, unknown>) {
  useAuthStore.setState({
    sessionVersion: 1,
    accessToken: 'token',
    user: mapUser({
      id: 1,
      username: 'learner',
      role: 'user',
      subscription_status: 'active',
      ...extra,
    }),
  })
}

function stubBrowser(gpu: boolean) {
  vi.stubGlobal('navigator', {
    mediaDevices: { getUserMedia: mocks.getUserMedia },
    ...(gpu ? { gpu: {} } : {}),
  })
}

function renderPage() {
  return render(
    <Suspense fallback={<div>loading</div>}>
      <ConversationPage />
    </Suspense>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.vadCalls = 0
  mocks.query = 'picture=7'
  MockWebSocket.instances = []
  FakeAudio.instances = []
  mocks.getUserMedia.mockResolvedValue({
    getTracks: () => [{ stop: vi.fn() }],
  })
  mocks.apiFetch.mockImplementation(async (url: string) =>
    url === '/api/picture-talk/scenes'
      ? { ok: true, json: async () => scene }
      : { ok: true, json: async () => null }
  )
  useConfigStore.setState(
    { ...useConfigStore.getInitialState(), loaded: true },
    true
  )
  useFreemiumStore.setState(useFreemiumStore.getInitialState(), true)
  useLanguageStore.setState({
    activeLanguage: getLanguageByCode('fr-FR'),
    userLanguages: [
      {
        target_language: 'fr-FR',
        is_active: true,
        plan,
        progress: null,
      },
    ],
    isSwitching: false,
    needsRefresh: false,
    invalidationVersion: 0,
    fetchLanguages: vi.fn().mockResolvedValue(true),
  })
  setUser({
    images_enabled: true,
    image_model_base_url: 'https://weights.example/',
    image_model_configured: true,
    native_speech_rate: 2.2,
  })
  stubBrowser(true)
  vi.stubGlobal('WebSocket', MockWebSocket)
  vi.stubGlobal('Worker', FakeWorker)
  vi.stubGlobal('Audio', FakeAudio)
  vi.stubGlobal(
    'AudioContext',
    class {
      state = 'running'
      close = vi.fn().mockResolvedValue(undefined)
    }
  )
  vi.stubGlobal('caches', {
    open: async () => ({
      match: async () => undefined,
      put: async () => undefined,
    }),
  })
  URL.createObjectURL = vi.fn(() => 'blob:fake')
  URL.revokeObjectURL = vi.fn()
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('picture talk page', () => {
  it('picture_talk_page_has_no_text_input', async () => {
    const { container } = renderPage()
    await screen.findByRole('button', { name: 'start' })
    await waitFor(() =>
      expect(
        container.querySelector('[data-picture-talk-image]')
      ).toHaveAttribute('data-image-state', 'done')
    )
    expect(container.querySelector('input')).toBeNull()
    expect(container.querySelector('textarea')).toBeNull()
    expect(container.querySelector('[contenteditable="true"]')).toBeNull()
    expect(mocks.apiFetch).toHaveBeenCalledWith(
      '/api/picture-talk/scenes',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ study_plan_id: 7 }),
      })
    )
  })

  it('fetches the Mac-drawn image without WebGPU, showing the words while it loads', async () => {
    stubBrowser(false)
    let release: (r: unknown) => void = () => {}
    const image = new Promise((resolve) => {
      release = resolve
    })
    mocks.apiFetch.mockImplementation(async (url: string) =>
      url === '/api/picture-talk/scenes'
        ? { ok: true, json: async () => scene }
        : image
    )
    const { container } = renderPage()
    await screen.findByRole('button', { name: 'start' })
    expect(screen.queryByText('unavailable')).toBeNull()
    const slot = container.querySelector('[data-picture-talk-image]')
    await waitFor(() =>
      expect(slot).toHaveAttribute('data-image-state', 'server-loading')
    )
    expect(slot).toHaveTextContent('ab')
    expect(mocks.apiFetch).toHaveBeenCalledWith(
      '/api/picture-talk/image',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ prompt: scene.image_prompt }),
      })
    )
    release({ ok: true, blob: async () => new Blob(['png']) })
    await waitFor(() =>
      expect(slot).toHaveAttribute('data-image-state', 'server')
    )
    expect(slot!.querySelector('img')).not.toBeNull()
  })

  it('shows the words when the Mac image fails', async () => {
    stubBrowser(false)
    mocks.apiFetch.mockImplementation(async (url: string) =>
      url === '/api/picture-talk/scenes'
        ? { ok: true, json: async () => scene }
        : { ok: false, status: 404 }
    )
    const { container } = renderPage()
    await screen.findByRole('button', { name: 'start' })
    const slot = container.querySelector('[data-picture-talk-image]')
    await waitFor(() =>
      expect(slot).toHaveAttribute('data-image-state', 'failed')
    )
    expect(slot).toHaveTextContent('ab')
  })

  it('draws nothing and shows the words when images are off', async () => {
    const worker = vi.fn()
    vi.stubGlobal('Worker', worker)
    setUser({
      images_enabled: false,
      image_model_base_url: 'https://weights.example/',
    })
    const { container } = renderPage()
    await screen.findByRole('button', { name: 'start' })
    const slot = container.querySelector('[data-picture-talk-image]')
    expect(slot).toHaveAttribute('data-image-state', 'unsupported')
    expect(slot).toHaveTextContent('ab')
    expect(worker).not.toHaveBeenCalled()
    expect(mocks.apiFetch).not.toHaveBeenCalledWith(
      '/api/picture-talk/image',
      expect.anything()
    )
  })

  it('uses the Mac image, not an empty box, with WebGPU and no weights URL', async () => {
    setUser({ images_enabled: true })
    mocks.apiFetch.mockImplementation(async (url: string) =>
      url === '/api/picture-talk/scenes'
        ? { ok: true, json: async () => scene }
        : { ok: true, blob: async () => new Blob(['png']) }
    )
    const { container } = renderPage()
    await screen.findByRole('button', { name: 'start' })
    await waitFor(() =>
      expect(
        container.querySelector('[data-picture-talk-image]')
      ).toHaveAttribute('data-image-state', 'server')
    )
  })

  it('shows the words when the draw fails, one per word even when repeated', async () => {
    mocks.apiFetch.mockImplementation(async (url: string) =>
      url === '/api/picture-talk/scenes'
        ? {
            ok: true,
            json: async () => ({
              ...scene,
              image_prompt: 'fail',
              words: ['a', 'a'],
            }),
          }
        : { ok: true, json: async () => null }
    )
    const { container } = renderPage()
    await screen.findByRole('button', { name: 'start' })
    await waitFor(() =>
      expect(
        container.querySelector('[data-picture-talk-image]')
      ).toHaveAttribute('data-image-state', 'failed')
    )
    const slot = container.querySelector('[data-picture-talk-image]')!
    expect(slot.querySelectorAll('span')).toHaveLength(2)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('plays the native line at the saved rate with the pitch kept', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'start' }))
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    const ws = MockWebSocket.instances[0]
    act(() => ws.onopen?.())
    expect(JSON.parse(ws.send.mock.calls[0][0])).toMatchObject({
      type: 'auth',
      scene_id: 41,
    })

    const wav = float32ToWav(new Float32Array(4410).fill(0.2), 22050)
    act(() => {
      ws.onmessage?.({
        data: JSON.stringify({ type: 'audio_voice', voice: 'study' }),
      })
      ws.onmessage?.({ data: wav.slice(0) })
    })
    await waitFor(() => expect(FakeAudio.instances).toHaveLength(1))
    expect(FakeAudio.instances[0].playbackRate).toBe(1)
    act(() => {
      FakeAudio.instances[0].onended?.(new Event('ended'))
    })

    act(() => {
      ws.onmessage?.({
        data: JSON.stringify({ type: 'audio_voice', voice: 'native' }),
      })
      ws.onmessage?.({ data: wav.slice(0) })
    })
    await waitFor(() => expect(FakeAudio.instances).toHaveLength(2))
    const native = FakeAudio.instances[1]
    expect(native.preservesPitch).toBe(true)
    expect(native.playbackRate).toBe(2.2)
    expect(native.getAttribute('data-native-audio')).toBe('loading')
  })

  it('picture_talk_page_opens_no_microphone', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'start' }))
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    const ws = MockWebSocket.instances[0]
    act(() => ws.onopen?.())

    expect(mocks.getUserMedia).not.toHaveBeenCalled()
    expect(mocks.vadCalls).toBe(0)
    const sent = ws.send.mock.calls.map((call) => call[0])
    expect(sent.filter((frame) => typeof frame !== 'string')).toEqual([])
  })

  it('sends a button event for each press and next_scene with a fresh scene id', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'start' }))
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    const ws = MockWebSocket.instances[0]
    act(() => ws.onopen?.())
    ws.send.mockClear()

    fireEvent.click(await screen.findByRole('button', { name: 'tookTooLong' }))
    expect(ws.send).toHaveBeenLastCalledWith(
      JSON.stringify({ type: 'button_event', event: 'took_too_long' })
    )
    fireEvent.click(screen.getByRole('button', { name: 'answered' }))
    expect(ws.send).toHaveBeenLastCalledWith(
      JSON.stringify({ type: 'button_event', event: 'answered' })
    )

    mocks.apiFetch.mockImplementation(async (url: string) =>
      url === '/api/picture-talk/scenes'
        ? { ok: true, json: async () => ({ ...scene, id: 42 }) }
        : { ok: true, json: async () => null }
    )
    fireEvent.click(screen.getByRole('button', { name: 'nextPicture' }))
    await waitFor(() =>
      expect(ws.send).toHaveBeenCalledWith(
        JSON.stringify({ type: 'next_scene', scene_id: 42 })
      )
    )
  })
})

describe('picture talk entry', () => {
  async function renderCatalog() {
    mocks.apiFetch.mockImplementation(
      async () =>
        new Response(
          JSON.stringify({
            study_plan_id: 7,
            target_language: 'fr-FR',
            cefr_level: 'A2',
            situations: [{ id: 'hotel_problem', category: 'travel' }],
          })
        )
    )
    render(<SituationsCatalog />)
    await screen.findByRole('button', { name: /hotel_problem/ })
  }

  it('is hidden when images are off', async () => {
    setUser({
      images_enabled: false,
      image_model_base_url: 'https://weights.example/',
    })
    await renderCatalog()
    expect(screen.queryByText('entryStart')).toBeNull()
  })

  it('shows without WebGPU and hides without a weights URL', async () => {
    stubBrowser(false)
    await renderCatalog()
    expect(screen.queryByText('entryStart')).not.toBeNull()
    cleanup()
    stubBrowser(true)
    setUser({ images_enabled: true })
    await renderCatalog()
    expect(screen.queryByText('entryStart')).toBeNull()
  })

  it('opens the picture route when images can draw', async () => {
    await renderCatalog()
    fireEvent.click(screen.getByText('entryStart'))
    expect(mocks.push).toHaveBeenCalledWith('/conversation?picture=7')
  })
})
