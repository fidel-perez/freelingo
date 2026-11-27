'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { flagMistake, speakLine, translateLine } from '@/lib/tutor-tools'

const BUTTON =
  'border-fl-border text-fl-muted-1 hover:border-fl-border-2 hover:text-fl-fg inline-flex min-h-10 min-w-10 items-center justify-center border px-3 font-mono text-xs tracking-wide transition-colors disabled:opacity-40'

type Flag = 'idle' | 'busy' | 'logged' | 'right' | 'error'

interface Props {
  text: string
  studyPlanId: number
  isUser: boolean
}

/** Buttons under one transcript line: replay, translate (tutor), "I made a mistake" (learner). */
export default function LineActions({ text, studyPlanId, isUser }: Props) {
  const t = useTranslations('conversation')
  const [translation, setTranslation] = useState<string | null>(null)
  const [showTranslation, setShowTranslation] = useState(false)
  const [translating, setTranslating] = useState(false)
  const [translateFailed, setTranslateFailed] = useState(false)
  const [replayFailed, setReplayFailed] = useState(false)
  const [flag, setFlag] = useState<Flag>('idle')

  async function toggleTranslation() {
    if (showTranslation) {
      setShowTranslation(false)
      return
    }
    if (translation === null) {
      setTranslating(true)
      setTranslateFailed(false)
      try {
        setTranslation(await translateLine(text, studyPlanId))
      } catch {
        setTranslateFailed(true)
        return
      } finally {
        setTranslating(false)
      }
    }
    setShowTranslation(true)
  }

  async function replay() {
    setReplayFailed(false)
    try {
      await speakLine(text, studyPlanId)
    } catch {
      setReplayFailed(true)
    }
  }

  async function markMistake() {
    setFlag('busy')
    try {
      setFlag((await flagMistake(text, studyPlanId)) ? 'logged' : 'right')
    } catch {
      setFlag('error')
    }
  }

  const flagLabel = {
    idle: t('flagMistake'),
    busy: t('flagMistakeBusy'),
    logged: t('flagMistakeLogged'),
    right: t('flagMistakeRight'),
    error: t('flagMistakeError'),
  }[flag]

  return (
    <div className="flex flex-col gap-1" data-testid="line-actions">
      <div className="flex flex-wrap gap-2">
        <button type="button" className={BUTTON} onClick={() => void replay()}>
          {t('replay')}
        </button>
        {!isUser && (
          <button
            type="button"
            className={BUTTON}
            aria-pressed={showTranslation}
            disabled={translating}
            onClick={() => void toggleTranslation()}
          >
            {translating
              ? t('translating')
              : showTranslation
                ? t('hideTranslation')
                : t('translate')}
          </button>
        )}
        {isUser && (
          <button
            type="button"
            className={BUTTON}
            disabled={flag === 'busy' || flag === 'logged' || flag === 'right'}
            onClick={() => void markMistake()}
          >
            {flagLabel}
          </button>
        )}
      </div>
      {showTranslation && translation !== null && (
        <p className="text-fl-muted-1 max-w-[70ch] text-sm">{translation}</p>
      )}
      {(translateFailed || replayFailed) && (
        <p role="alert" className="text-fl-error text-xs">
          {translateFailed ? t('translateError') : t('replayError')}
        </p>
      )}
    </div>
  )
}
