import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

const mocks = vi.hoisted(() => ({
  translateLine: vi.fn(),
  flagMistake: vi.fn(),
  speakLine: vi.fn(),
}))

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))
vi.mock('@/lib/tutor-tools', () => mocks)

import TranscriptBubble from '@/components/conversation/TranscriptBubble'

beforeEach(() => {
  mocks.translateLine.mockReset().mockResolvedValue('Hola, ¿qué tal?')
  mocks.flagMistake.mockReset().mockResolvedValue(true)
  mocks.speakLine.mockReset().mockResolvedValue(undefined)
})
afterEach(cleanup)

describe('LineActions in a transcript bubble', () => {
  it('shows no buttons without a study plan or while streaming', () => {
    const { rerender } = render(
      <TranscriptBubble role="assistant" text="Salut" />
    )
    expect(screen.queryByTestId('line-actions')).toBeNull()
    rerender(
      <TranscriptBubble
        role="assistant"
        text="Salut"
        studyPlanId={7}
        streaming
      />
    )
    expect(screen.queryByTestId('line-actions')).toBeNull()
  })

  it('translates a tutor line, hides it, and shows it again without a second request', async () => {
    render(
      <TranscriptBubble
        role="assistant"
        text="Salut, ça va ?"
        studyPlanId={7}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'translate' }))
    expect(await screen.findByText('Hola, ¿qué tal?')).toBeInTheDocument()
    expect(mocks.translateLine).toHaveBeenCalledWith('Salut, ça va ?', 7)

    fireEvent.click(screen.getByRole('button', { name: 'hideTranslation' }))
    expect(screen.queryByText('Hola, ¿qué tal?')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'translate' }))
    expect(await screen.findByText('Hola, ¿qué tal?')).toBeInTheDocument()
    expect(mocks.translateLine).toHaveBeenCalledTimes(1)
  })

  it('reports a failed translation and allows a retry', async () => {
    mocks.translateLine.mockRejectedValueOnce(new Error('503'))
    render(<TranscriptBubble role="assistant" text="Salut" studyPlanId={7} />)

    fireEvent.click(screen.getByRole('button', { name: 'translate' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('translateError')

    fireEvent.click(screen.getByRole('button', { name: 'translate' }))
    expect(await screen.findByText('Hola, ¿qué tal?')).toBeInTheDocument()
  })

  it('replays a line on the tutor side and on the learner side', () => {
    const { rerender } = render(
      <TranscriptBubble role="assistant" text="Salut" studyPlanId={7} />
    )
    fireEvent.click(screen.getByRole('button', { name: 'replay' }))
    rerender(
      <TranscriptBubble role="user" text="Je suis allé" studyPlanId={7} />
    )
    fireEvent.click(screen.getByRole('button', { name: 'replay' }))
    expect(mocks.speakLine.mock.calls).toEqual([
      ['Salut', 7],
      ['Je suis allé', 7],
    ])
  })

  it('shows an error when the replay audio fails', async () => {
    mocks.speakLine.mockRejectedValueOnce(new Error('TTS error 500'))
    render(<TranscriptBubble role="assistant" text="Salut" studyPlanId={7} />)
    fireEvent.click(screen.getByRole('button', { name: 'replay' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('replayError')
  })

  it('offers translate only on tutor lines and the mistake button only on learner lines', () => {
    const { rerender } = render(
      <TranscriptBubble role="user" text="J ai allé" studyPlanId={7} />
    )
    expect(screen.queryByRole('button', { name: 'translate' })).toBeNull()
    expect(
      screen.getByRole('button', { name: 'flagMistake' })
    ).toBeInTheDocument()
    rerender(
      <TranscriptBubble role="assistant" text="Très bien" studyPlanId={7} />
    )
    expect(screen.queryByRole('button', { name: 'flagMistake' })).toBeNull()
  })

  it('flags a learner line once and shows the saved label', async () => {
    render(<TranscriptBubble role="user" text="J ai allé" studyPlanId={7} />)
    fireEvent.click(screen.getByRole('button', { name: 'flagMistake' }))
    const saved = await screen.findByRole('button', {
      name: 'flagMistakeLogged',
    })
    expect(saved).toBeDisabled()
    expect(mocks.flagMistake).toHaveBeenCalledWith('J ai allé', 7)
  })

  it('says so when the tutor finds no mistake, and when saving fails', async () => {
    mocks.flagMistake.mockResolvedValueOnce(false)
    const { unmount } = render(
      <TranscriptBubble role="user" text="Je suis allé" studyPlanId={7} />
    )
    fireEvent.click(screen.getByRole('button', { name: 'flagMistake' }))
    expect(
      await screen.findByRole('button', { name: 'flagMistakeRight' })
    ).toBeDisabled()
    unmount()

    mocks.flagMistake.mockRejectedValueOnce(new Error('503'))
    render(<TranscriptBubble role="user" text="J ai allé" studyPlanId={7} />)
    fireEvent.click(screen.getByRole('button', { name: 'flagMistake' }))
    expect(
      await screen.findByRole('button', { name: 'flagMistakeError' })
    ).toBeEnabled()
  })

  it('gives every button a 40 px touch target', () => {
    render(<TranscriptBubble role="user" text="J ai allé" studyPlanId={7} />)
    const buttons = screen
      .getByTestId('line-actions')
      .querySelectorAll('button')
    expect(buttons.length).toBeGreaterThan(0)
    for (const button of buttons) {
      expect(button.className).toContain('min-h-10')
      expect(button.className).toContain('min-w-10')
    }
  })
})
