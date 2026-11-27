import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  play: vi.fn(),
  pause: vi.fn(),
}))

vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('@/lib/tts-voice', () => ({
  resolveConfiguredTtsVoice: vi.fn().mockResolvedValue('female'),
}))

import { flagMistake, speakLine, translateLine } from '@/lib/tutor-tools'

function ok(body: unknown) {
  return { ok: true, json: async () => body }
}

beforeEach(() => {
  mocks.apiFetch.mockReset()
  mocks.play.mockReset().mockResolvedValue(undefined)
  mocks.pause.mockReset()
  vi.stubGlobal(
    'Audio',
    class {
      onended: (() => void) | null = null
      play = mocks.play
      pause = mocks.pause
    }
  )
  URL.createObjectURL = vi.fn(() => 'blob:x')
  URL.revokeObjectURL = vi.fn()
})
afterEach(() => vi.unstubAllGlobals())

describe('tutor-tools', () => {
  it('posts the line and plan to the translate endpoint', async () => {
    mocks.apiFetch.mockResolvedValue(ok({ translation: 'Hola' }))

    await expect(translateLine('Salut', 7)).resolves.toBe('Hola')

    const [path, init] = mocks.apiFetch.mock.calls[0]
    expect(path).toBe('/api/tutor/translate')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ text: 'Salut', study_plan_id: 7 })
  })

  it('rejects when the translate request fails', async () => {
    mocks.apiFetch.mockResolvedValue({ ok: false, status: 503 })
    await expect(translateLine('Salut', 7)).rejects.toThrow('503')
  })

  it('posts the line to the flag-mistake endpoint and returns whether it was logged', async () => {
    mocks.apiFetch.mockResolvedValue(ok({ logged: false, corrected: '' }))

    await expect(flagMistake('Je suis allé', 7)).resolves.toBe(false)

    expect(mocks.apiFetch.mock.calls[0][0]).toBe('/api/tutor/flag-mistake')
  })

  it('speaks a line through /api/tts and stops the previous replay', async () => {
    mocks.apiFetch.mockResolvedValue({
      ok: true,
      blob: async () => new Blob(['a']),
    })

    await speakLine('Salut', 7)
    await speakLine('Bonjour', 7)

    const [path, init] = mocks.apiFetch.mock.calls[0]
    expect(path).toBe('/api/tts')
    expect(JSON.parse(init.body)).toEqual({
      text: 'Salut',
      voice: 'female',
      study_plan_id: 7,
    })
    expect(mocks.play).toHaveBeenCalledTimes(2)
    expect(mocks.pause).toHaveBeenCalledTimes(1)
  })

  it('rejects when the TTS request fails', async () => {
    mocks.apiFetch.mockResolvedValue({ ok: false, status: 500 })
    await expect(speakLine('Salut', 7)).rejects.toThrow('500')
  })
})
