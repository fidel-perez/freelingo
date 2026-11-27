'use client'

import { useState, type FormEvent } from 'react'
import { useTranslations } from 'next-intl'

/** A text box that sends a typed line as the learner's turn. */
export default function TypedInput({
  onSend,
  disabled,
}: {
  onSend: (text: string) => void
  disabled: boolean
}) {
  const t = useTranslations('conversation')
  const [value, setValue] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    const text = value.trim()
    if (!text || disabled) return
    onSend(text)
    setValue('')
  }

  return (
    <form onSubmit={submit} className="flex w-full gap-2">
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        maxLength={1000}
        aria-label={t('typedLabel')}
        placeholder={t('typedPlaceholder')}
        className="border-fl-border bg-fl-surface text-fl-fg min-h-10 min-w-0 flex-1 border px-3 font-sans text-base"
      />
      <button
        type="submit"
        disabled={disabled || value.trim() === ''}
        className="border-fl-border text-fl-muted-1 hover:border-fl-border-2 hover:text-fl-fg min-h-10 min-w-10 border px-4 font-mono text-xs tracking-widest uppercase transition-colors disabled:opacity-40"
      >
        {t('typedSend')}
      </button>
    </form>
  )
}
