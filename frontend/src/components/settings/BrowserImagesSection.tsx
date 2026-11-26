'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { apiFetch } from '@/lib/api'
import { browserImagesSupported } from '@/lib/browser-images'
import { mapUser } from '@/lib/mappers'
import { useAuthStore } from '@/store/auth'

/** Opt-in switch for browser-drawn images; hidden without WebGPU or a configured model. */
export function BrowserImagesSection() {
  const t = useTranslations('browserImages')
  const tSettings = useTranslations('settings')
  const user = useAuthStore((s) => s.user)
  const setUser = useAuthStore((s) => s.setUser)
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState(false)

  if (!browserImagesSupported() || !user?.image_model_configured) return null

  const enabled = !!user.images_enabled

  async function toggle() {
    const { sessionVersion } = useAuthStore.getState()
    setSaving(true)
    setFailed(false)
    try {
      const res = await apiFetch('/api/auth/me', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ images_enabled: !enabled }),
      })
      if (!res.ok) throw new Error('save')
      const updated = await res.json()
      if (useAuthStore.getState().sessionVersion !== sessionVersion) return
      setUser(mapUser(updated, user))
    } catch {
      setFailed(true)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="border-fl-border bg-fl-surface border p-6">
      <div className="border-fl-border mb-4 flex items-center gap-2 border-b pb-4">
        <span className="text-fl-label text-fl-muted-2">●</span>
        <span className="text-fl-label text-fl-muted-2 font-mono tracking-widest uppercase">
          {t('title')}
        </span>
      </div>
      <p className="text-fl-hint text-fl-muted-2 mb-4 font-mono">
        {t('description')}
      </p>
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        onClick={toggle}
        disabled={saving}
        className={`w-full border py-3 font-mono text-xs tracking-widest uppercase transition-colors disabled:opacity-40 ${
          enabled
            ? 'border-fl-accent bg-fl-accent text-fl-accent-fg'
            : 'border-fl-border text-fl-muted-2 hover:border-fl-border-2 hover:text-fl-fg'
        }`}
      >
        {t('toggle')}
      </button>
      {failed && (
        <div className="border-fl-error/40 text-fl-error mt-4 border px-4 py-3 font-mono text-xs">
          ✕ {tSettings('saveFailed')}
        </div>
      )}
    </div>
  )
}
