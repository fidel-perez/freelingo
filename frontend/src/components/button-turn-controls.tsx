'use client'

import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'

export type ButtonTurnEvent = 'answered' | 'took_too_long'

/** Reports how the learner's spoken practice went. Opens no microphone. */
export function ButtonTurnControls({
  onEvent,
  disabled = false,
}: {
  onEvent: (event: ButtonTurnEvent) => void
  disabled?: boolean
}) {
  const t = useTranslations('buttonTurn')
  return (
    <div className="flex flex-wrap items-center justify-center gap-3">
      <Button disabled={disabled} onClick={() => onEvent('answered')}>
        {t('answered')}
      </Button>
      <Button
        variant="outline"
        disabled={disabled}
        onClick={() => onEvent('took_too_long')}
      >
        {t('tookTooLong')}
      </Button>
    </div>
  )
}
