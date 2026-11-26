import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ElementType, HTMLAttributes, ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
}))

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: '1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/lesson/1',
  useSearchParams: () => new URLSearchParams(),
}))
vi.mock('next/link', () => ({
  default: ({ children, href }: { children: ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))
vi.mock('next-intl', () => ({
  useTranslations: () =>
    Object.assign((key: string) => key, { rich: (key: string) => key }),
  useLocale: () => 'en',
}))
vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('@/data/grammar', () => ({
  getGrammarTopics: () => Promise.resolve([]),
}))
vi.mock('@/components/billing/PaywallBanner', () => ({
  PaywallBanner: () => null,
}))
vi.mock('@/components/billing/FreemiumQuotaBanner', () => ({
  FreemiumQuotaBanner: () => null,
}))
vi.mock('@/components/ui/AudioPlayer', () => ({ AudioPlayer: () => null }))
vi.mock('@/components/ui/VoiceRecorder', () => ({
  VoiceRecorder: ({
    onTranscription,
    onAudio,
  }: {
    onTranscription: (text: string) => void
    onAudio?: (audio: Blob) => void
  }) => (
    <button
      onClick={() => {
        onAudio?.(new Blob(['wav'], { type: 'audio/wav' }))
        onTranscription('hola')
      }}
    >
      record
    </button>
  ),
}))
vi.mock('@/components/ui/confirm-dialog', () => ({
  ConfirmDialog: () => null,
}))
vi.mock('@/components/ui/WordTooltip', () => ({
  WordTooltip: () => null,
  useWordSave: () => ({
    selectedWord: null,
    tooltipPos: null,
    saveState: 'idle',
    handleTextSelection: vi.fn(),
    handleSaveWord: vi.fn(),
    dismissTooltip: vi.fn(),
  }),
}))
vi.mock('@/components/ui/page-loading', () => ({
  PageLoading: ({ label }: { label: string }) => <p role="status">{label}</p>,
}))
vi.mock('@/components/reviews/ReviewPrompt', () => ({
  ReviewPrompt: () => null,
  getReviewPromptDismissal: () => ({ count: 0, lastDismissedAt: null }),
}))
vi.mock('@/components/TargetLanguageText', () => ({
  TargetLanguageText: ({
    children,
    as: Component = 'div',
    languageCode,
    reading,
    translation,
    ...props
  }: {
    children: ReactNode
    as?: ElementType
    languageCode?: string | null
    reading?: string | null
    translation?: string | null
  } & HTMLAttributes<HTMLElement>) => {
    void languageCode
    void reading
    void translation
    return <Component {...props}>{children}</Component>
  },
}))

import LessonPage from '@/app/(app)/lesson/[id]/page'
import { getLanguageByCode } from '@/lib/target-languages'
import { useAuthStore } from '@/store/auth'
import { useConfigStore } from '@/store/config'
import { useFreemiumStore } from '@/store/freemium'
import { useLanguageStore } from '@/store/language'
import { useProgressStore } from '@/store/progress'


const WORDS = [
  { word: 'hola', score: 0.4, weak_phonemes: ['o', 'l'] },
  { word: 'amigo', score: 0.95, weak_phonemes: [] },
]

const detail = {
  lesson: {
    id: 1,
    study_plan_id: 1,
    title: 'Saludos',
    lesson_type: 'speaking',
    cefr_level: 'A1',
    content: {},
    is_completed: false,
  },
  exercises: [
    {
      id: 10,
      exercise_type: 'pronunciation',
      question: 'Say hello.',
      options: null,
      correct_answer: 'hola',
      explanation: null,
      native_explanation: null,
      user_answer: null,
      score: null,
      feedback: null,
      corrections: null,
      native_hint: null,
    },
  ],
}

function jsonResponse(data: unknown) {
  return new Response(JSON.stringify(data), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('LessonPage pronunciation audio', () => {
  beforeEach(() => {
    mocks.apiFetch.mockReset()
    useAuthStore.setState(useAuthStore.getInitialState(), true)
    useConfigStore.setState(useConfigStore.getInitialState(), true)
    useFreemiumStore.setState(useFreemiumStore.getInitialState(), true)
    useLanguageStore.setState(
      {
        ...useLanguageStore.getInitialState(),
        activeLanguage: getLanguageByCode('es-ES') ?? null,
      },
      true
    )
    useProgressStore.setState(useProgressStore.getInitialState(), true)
    useAuthStore.getState().startSession('test-session')
    useAuthStore.getState().setUser({
      id: 1,
      username: 'learner',
      displayName: 'Learner',
      native_language: 'en',
      role: 'user',
      subscription_status: 'active',
      conversation_max_duration: 300,
      conversation_inactivity_timeout: 30,
    })
    mocks.apiFetch.mockImplementation((url: string) => {
      if (url === '/api/lessons/1') return Promise.resolve(jsonResponse(detail))
      if (url === '/api/lessons/exercises/10/answer') {
        return Promise.resolve(
          jsonResponse({
            id: 10,
            score: 0.7,
            feedback: 'Close.',
            correct_answer: 'hola',
            words: WORDS,
          })
        )
      }
      return Promise.resolve(new Response(null, { status: 404 }))
    })
  })

  it('uploads the recording and shows the weak phonemes', async () => {
    await act(async () => {
      render(<LessonPage />)
    })
    fireEvent.click(await screen.findByRole('button', { name: 'record' }))

    await waitFor(() =>
      expect(mocks.apiFetch).toHaveBeenCalledWith(
        '/api/lessons/exercises/10/answer',
        expect.objectContaining({ method: 'POST', body: expect.any(FormData) })
      )
    )
    const call = mocks.apiFetch.mock.calls.find(
      ([url]) => url === '/api/lessons/exercises/10/answer'
    )
    const form = call?.[1].body as FormData
    expect(form.get('answer')).toBe('hola')
    expect((form.get('audio') as File).name).toBe('recording.wav')

    expect(
      await screen.findByText('pronunciationWeakPhonemes')
    ).toBeInTheDocument()
    expect(screen.getByText('o l')).toBeInTheDocument()
    expect(screen.queryByText('amigo')).toBeNull()
  })
})
