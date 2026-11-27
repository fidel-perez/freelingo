import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

vi.mock('next-intl', () => ({
  useTranslations: () =>
    Object.assign((key: string) => key, {
      raw: () => [
        { title: 'Item one', body: 'Body one' },
        { title: 'Item two', body: 'Body two' },
      ],
    }),
}))

import ConversationHelp from '@/components/conversation/ConversationHelp'

afterEach(cleanup)

describe('ConversationHelp', () => {
  it('links to the flashcards with a touch-sized target', () => {
    render(<ConversationHelp />)
    const link = screen.getByRole('link', { name: 'flashcardsLink' })
    expect(link).toHaveAttribute('href', '/flashcards')
    expect(link.className).toContain('min-h-10')
  })

  it('opens and closes a panel that explains each feature', () => {
    render(<ConversationHelp />)
    expect(screen.queryByText('Body one')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'helpButton' }))
    expect(screen.getByRole('button', { name: 'helpButton' })).toHaveAttribute(
      'aria-expanded',
      'true'
    )
    expect(screen.getByText('Body one')).toBeInTheDocument()
    expect(screen.getByText('Body two')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'helpClose' }))
    expect(screen.queryByText('Body one')).toBeNull()
  })

  it('lists a link to every feature page of the app menu', () => {
    render(<ConversationHelp />)
    fireEvent.click(screen.getByRole('button', { name: 'helpButton' }))
    const hrefs = screen
      .getAllByRole('link')
      .map((link) => link.getAttribute('href'))
    for (const href of [
      '/plan',
      '/progress',
      '/assessment',
      '/chat',
      '/conversation',
      '/listening',
      '/reading',
      '/flashcards',
      '/games',
      '/grammar-practice',
      '/situations',
      '/grammar',
      '/vocabulary',
      '/phrasebook',
      '/settings',
    ])
      expect(hrefs).toContain(href)
  })
})
