'use client'

import { useEffect, useState } from 'react'
import { browserImagesSupported, drawImage } from '@/lib/browser-images'
import { useAuthStore } from '@/store/auth'

type ImageState = 'loading' | 'done' | 'failed' | 'unsupported'

/** Draws the scene in the browser; `data-image-state` follows the draw. */
export function PictureTalkImage({
  prompt,
  words = [],
}: {
  prompt: string
  words?: string[]
}) {
  const enabled = useAuthStore((s) => s.user?.images_enabled)
  const baseUrl = useAuthStore((s) => s.user?.image_model_base_url)
  const canDraw = !!enabled && !!baseUrl && browserImagesSupported()
  const [drawState, setState] = useState<ImageState>('loading')
  const [src, setSrc] = useState('')
  // No draw can start: show the words. A server-image branch goes here later.
  const state: ImageState = canDraw ? drawState : 'unsupported'

  useEffect(() => {
    if (!canDraw || !baseUrl) return
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
  }, [canDraw, baseUrl, prompt])

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
      {(state === 'unsupported' || state === 'failed') && (
        <p className="text-fl-muted-1 flex h-full flex-wrap content-center items-center justify-center gap-2 p-4 text-center font-sans text-lg">
          {words.map((word, index) => (
            <span key={index}>{word}</span>
          ))}
        </p>
      )}
    </div>
  )
}
