'use client'

import { useState, useEffect } from 'react'
import { useTranslations } from 'next-intl'
import { apiFetch } from '@/lib/api'
import { mapUser } from '@/lib/mappers'
import { useAuthStore } from '@/store/auth'

type CorrectionStyle = 'explicit' | 'recast'

const STYLES: CorrectionStyle[] = ['recast', 'explicit']

export function CorrectionStyleSection() {
  const t = useTranslations('settings.correctionStyle')
  const tSettings = useTranslations('settings')
  const user = useAuthStore((s) => s.user)
  const setUser = useAuthStore((s) => s.setUser)

  const [style, setStyle] = useState<CorrectionStyle>('recast')
  const [message, setMessage] = useState<{
    type: 'ok' | 'err'
    text: string
  } | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (user) setStyle(user.correction_style ?? 'recast')
  }, [user])

  async function handleSave() {
    const { sessionVersion } = useAuthStore.getState()
    setSaving(true)
    setMessage(null)
    try {
      const res = await apiFetch('/api/auth/me', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ correction_style: style }),
      })
      if (!res.ok) throw new Error(tSettings('saveFailed'))
      const updated = await res.json()
      if (useAuthStore.getState().sessionVersion !== sessionVersion) return
      setUser(mapUser(updated, user))
      setMessage({ type: 'ok', text: t('saved') })
    } catch {
      setMessage({ type: 'err', text: tSettings('saveFailed') })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="border-fl-border bg-fl-surface border p-6">
      <div className="border-fl-border mb-5 flex items-center gap-2 border-b pb-4">
        <span className="text-fl-label text-fl-muted-2">●</span>
        <span className="text-fl-label text-fl-muted-2 font-mono tracking-widest uppercase">
          {t('title')}
        </span>
      </div>

      <div className="space-y-5">
        <div role="radiogroup" aria-label={t('title')} className="space-y-2">
          {STYLES.map((val) => (
            <button
              key={val}
              type="button"
              role="radio"
              aria-checked={style === val}
              onClick={() => setStyle(val)}
              className={`w-full border p-4 text-left transition-colors ${
                style === val
                  ? 'border-fl-accent bg-fl-accent text-fl-accent-fg'
                  : 'border-fl-border text-fl-muted-2 hover:border-fl-border-2 hover:text-fl-fg'
              }`}
            >
              <span className="block font-mono text-xs tracking-widest uppercase">
                {t(val)}
              </span>
              <span className="text-fl-hint mt-1 block font-mono">
                {t(`${val}Hint`)}
              </span>
            </button>
          ))}
        </div>

        {message && (
          <div
            className={`border px-4 py-3 font-mono text-xs ${message.type === 'ok' ? 'border-fl-border text-fl-muted-1' : 'border-fl-error/40 text-fl-error'}`}
          >
            {message.type === 'ok' ? '✓ ' : '✕ '}
            {message.text}
          </div>
        )}

        <button
          onClick={handleSave}
          disabled={saving}
          className="bg-fl-accent text-fl-accent-fg hover:bg-fl-accent/90 w-full py-3 font-mono text-sm font-bold tracking-widest uppercase transition-colors disabled:opacity-40"
        >
          {saving ? tSettings('saving') : t('save')}
        </button>
      </div>
    </div>
  )
}
