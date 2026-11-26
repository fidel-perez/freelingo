'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { apiFetch } from '@/lib/api'
import { mapUser } from '@/lib/mappers'
import { DEFAULT_NATIVE_SPEECH_RATE } from '@/lib/picture-talk'
import { useAuthStore } from '@/store/auth'

const RATES = Array.from({ length: 16 }, (_, i) => 1 + i / 10)

export function NativeSpeechRateSection() {
  const t = useTranslations('pictureTalk')
  const tSettings = useTranslations('settings')
  const user = useAuthStore((s) => s.user)
  const setUser = useAuthStore((s) => s.setUser)
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState(false)

  if (!user) return null
  const rate = user.native_speech_rate ?? DEFAULT_NATIVE_SPEECH_RATE

  async function save(value: number) {
    const { sessionVersion } = useAuthStore.getState()
    setSaving(true)
    setFailed(false)
    try {
      const res = await apiFetch('/api/auth/me', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ native_speech_rate: value }),
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
      <div className="border-fl-border mb-5 flex items-center gap-2 border-b pb-4">
        <span className="text-fl-label text-fl-muted-2">●</span>
        <span className="text-fl-label text-fl-muted-2 font-mono tracking-widest uppercase">
          {t('nativeSpeedLabel')}
        </span>
      </div>
      <select
        aria-label={t('nativeSpeedLabel')}
        value={rate.toFixed(1)}
        disabled={saving}
        onChange={(e) => void save(Number(e.target.value))}
        className="bg-fl-bg border-fl-border text-fl-fg focus:border-fl-border-2 w-full appearance-none border px-4 py-3 font-mono text-sm tracking-widest transition-colors focus:outline-none disabled:opacity-40"
      >
        {RATES.map((value) => (
          <option key={value} value={value.toFixed(1)}>
            {value.toFixed(1)}x
          </option>
        ))}
      </select>
      <p className="text-fl-hint text-fl-muted-3 mt-2 font-sans">
        {t('nativeSpeedNote')}
      </p>
      {failed && (
        <div className="border-fl-error/40 text-fl-error mt-4 border px-4 py-3 font-mono text-xs">
          ✕ {tSettings('saveFailed')}
        </div>
      )}
    </div>
  )
}
