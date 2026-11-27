import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import React from 'react'

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/api', () => ({ apiFetch, requestAccessToken: vi.fn() }))
vi.mock('next-intl', () => ({
  useLocale: () => 'en',
  useTranslations: () => (key: string) => key,
}))
vi.mock('next/link', () => ({
  default: ({
    href,
    children,
    ...props
  }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}))
vi.mock('@/components/LanguageSwitcher', () => ({ default: () => null }))
vi.mock('@/components/ui/confirm-dialog', () => ({
  ConfirmDialog: () => null,
}))
vi.mock('@/components/ui/contact-form-modal', () => ({
  ContactFormModal: () => null,
}))
vi.mock('@/components/ui/loading-bar', () => ({ LoadingBar: () => null }))
vi.mock('@/components/ui/page-loading', () => ({ PageLoading: () => null }))
vi.mock('@/components/ui/page-loading-boundary', () => ({
  PageLoadingProvider: ({ children }: { children: React.ReactNode }) =>
    children,
  PageLoadingViewport: ({ children }: { children: React.ReactNode }) =>
    children,
}))

import AppLayout from '@/app/(app)/layout'
import { useAuthStore } from '@/store/auth'
import { useConfigStore } from '@/store/config'
import { useLanguageStore } from '@/store/language'
import { getLanguageByCode } from '@/lib/target-languages'

beforeEach(() => {
  useAuthStore.getState().startSession('token')
  useConfigStore.setState({ loaded: true, stripeEnabled: false })
  useLanguageStore.setState({
    activeLanguage: getLanguageByCode('fr-FR'),
    userLanguages: [
      {
        target_language: 'fr-FR',
        is_active: true,
        plan: {
          id: 7,
          cefr_level: 'A2',
          progress_day: 1,
          total_days: 30,
          completion_pct: 0,
        },
        progress: null,
      },
    ],
  })
  apiFetch.mockReset()
  apiFetch.mockImplementation(async (url: string) => ({
    ok: true,
    json: async () =>
      url === '/api/auth/me'
        ? {
            id: 1,
            username: 'learner',
            role: 'user',
            learning_goals: [],
            subscription_status: 'active',
          }
        : { unread_count: 0 },
  }))
})

afterEach(cleanup)

describe('phone navigation', () => {
  it('reaches every feature page, picture talk included', async () => {
    await act(async () => {
      render(
        <AppLayout>
          <div>content</div>
        </AppLayout>
      )
    })
    fireEvent.click(screen.getByRole('button', { name: 'openMenu' }))
    const nav = document.getElementById('mobile-navigation')!
    const hrefs = Array.from(nav.querySelectorAll('a')).map((a) =>
      a.getAttribute('href')
    )
    for (const href of [
      '/dashboard',
      '/plan',
      '/progress',
      '/assessment',
      '/chat',
      '/conversation',
      '/conversation?picture=7',
      '/listening',
      '/reading',
      '/flashcards',
      '/games',
      '/grammar-practice',
      '/situations',
      '/grammar',
      '/vocabulary',
      '/phrasebook',
      '/faq',
      '/feedback',
      '/settings',
    ])
      expect(hrefs).toContain(href)
  })

  it('gives the menu button and every link a touch-sized target', async () => {
    await act(async () => {
      render(
        <AppLayout>
          <div>content</div>
        </AppLayout>
      )
    })
    const menu = screen.getByRole('button', { name: 'openMenu' })
    expect(menu.className).toContain('min-h-11')
    expect(menu.className).toContain('min-w-11')
    fireEvent.click(menu)
    const nav = document.getElementById('mobile-navigation')!
    for (const link of nav.querySelectorAll('a'))
      expect(link.className).toContain('min-h-11')
  })
})
