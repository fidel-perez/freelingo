import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
  useLocale: () => 'en',
}))

const { mockApiFetch } = vi.hoisted(() => ({ mockApiFetch: vi.fn() }))
vi.mock('@/lib/api', () => ({ apiFetch: mockApiFetch }))

vi.mock('@/lib/mappers', () => ({
  mapUser: (data: Record<string, any>, current: any) => ({
    ...current,
    ...data,
  }),
}))

import { CorrectionStyleSection } from '@/components/settings/CorrectionStyleSection'
import { useAuthStore } from '@/store/auth'

const baseUser = {
  id: 1,
  username: 'testuser',
  displayName: 'Test User',
  role: 'user' as const,
  conversation_max_duration: 1800,
  conversation_inactivity_timeout: 180,
}

describe('correction style setting', () => {
  beforeEach(() => {
    mockApiFetch.mockReset()
    useAuthStore.setState({
      accessToken: 'test-token',
      user: { ...baseUser, correction_style: 'recast' },
    })
  })

  it.each([
    ['explicit', 'explicit'],
    ['recast', 'recast'],
  ] as const)('saves %s through PATCH /api/auth/me', async (label, style) => {
    // Start from the other style so the click changes the selection.
    useAuthStore.setState({
      user: {
        ...baseUser,
        correction_style: style === 'explicit' ? 'recast' : 'explicit',
      },
    })
    mockApiFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ ...baseUser, correction_style: style }),
    })
    render(<CorrectionStyleSection />)

    fireEvent.click(screen.getByText(label))
    fireEvent.click(screen.getByText('save'))

    await waitFor(() => expect(mockApiFetch).toHaveBeenCalledTimes(1))
    const [url, init] = mockApiFetch.mock.calls[0]
    expect(url).toBe('/api/auth/me')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(init.body)).toEqual({ correction_style: style })
    expect(await screen.findByText(/saved/)).toBeDefined()
    expect(useAuthStore.getState().user?.correction_style).toBe(style)
  })
})
