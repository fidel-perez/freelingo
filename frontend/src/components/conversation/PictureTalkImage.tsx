'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { drawImage } from '@/lib/browser-images'
import { useAuthStore } from '@/store/auth'

type ImageState = 'loading' | 'done' | 'failed'

/** Draws the scene in the browser; `data-image-state` follows the draw. */
export function PictureTalkImage({ prompt }: { prompt: string }) {
  const t = useTranslations('pictureTalk')
  const baseUrl = useAuthStore((s) => s.user?.image_model_base_url)
  const [state, setState] = useState<ImageState>('loading')
  const [src, setSrc] = useState('')

  useEffect(() => {
    if (!baseUrl) return
    let cancelled = false
    let url = ''
    setState('loading')
    setSrc('')
    void drawImage(baseUrl, prompt)
      .then((blob) => {
        if (cancelled) return
        url = URL.createObjectURL(blob)
        setSrc(url)
        setState('done')
      })
      .catch(() => {
        if (!cancelled) setState('failed')
      })
    return () => {
      cancelled = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [baseUrl, prompt])

  return (
    <div
      data-picture-talk-image
      data-image-state={state}
      className="border-fl-border bg-fl-surface mx-auto mb-4 aspect-square w-full max-w-xs shrink-0 overflow-hidden border"
    >
      {state === 'done' && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt="" className="h-full w-full object-cover" />
      )}
      {state === 'failed' && (
        <p role="alert" className="text-fl-muted-2 p-4 font-sans text-sm">
          {t('imageFailed')}
        </p>
      )}
    </div>
  )
}
