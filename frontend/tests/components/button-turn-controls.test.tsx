import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ButtonTurnControls } from '@/components/button-turn-controls'
import en from '../../../messages/en.json'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) =>
    (en.buttonTurn as Record<string, string>)[key],
}))

afterEach(cleanup)

describe('ButtonTurnControls', () => {
  it('sends answered and took_too_long', () => {
    const onEvent = vi.fn()
    render(<ButtonTurnControls onEvent={onEvent} />)
    fireEvent.click(screen.getByRole('button', { name: 'I answered' }))
    fireEvent.click(screen.getByRole('button', { name: 'I took too long' }))
    expect(onEvent.mock.calls).toEqual([['answered'], ['took_too_long']])
  })

  it('sends nothing while disabled', () => {
    const onEvent = vi.fn()
    render(<ButtonTurnControls onEvent={onEvent} disabled />)
    fireEvent.click(screen.getByRole('button', { name: 'I answered' }))
    expect(onEvent).not.toHaveBeenCalled()
  })

  it('button_turn_controls_open_no_microphone', () => {
    const getUserMedia = vi.fn()
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia },
    })
    render(<ButtonTurnControls onEvent={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'I answered' }))
    fireEvent.click(screen.getByRole('button', { name: 'I took too long' }))
    expect(getUserMedia).not.toHaveBeenCalled()
  })
})
